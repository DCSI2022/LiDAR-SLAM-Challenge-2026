"""EVO 适配层：时间关联、单场景 SE(3) 对齐及平移 APE/RPE/RTE。"""

from dataclasses import dataclass
import copy
from typing import Dict, List, Sequence

import numpy as np
from evo.core import filters, metrics, sync
from evo.core.trajectory import PoseTrajectory3D

from ..models import Trajectory


@dataclass(frozen=True)
class RPEResult:
    """一帧平移 RPE 原始误差，单位米。"""
    errors_m: np.ndarray

    @property
    def pair_count(self) -> int:
        return len(self.errors_m)

    @property
    def rmse_m(self) -> float:
        if not len(self.errors_m):
            return float("nan")
        return float(np.sqrt(np.mean(np.square(self.errors_m))))


@dataclass(frozen=True)
class RTEResult:
    """距离 RPE 百分比，既保存综合数组，也按 1/5/10 m 分档保存。"""
    errors_pct: np.ndarray
    pair_counts: Dict[float, int]
    errors_by_distance_pct: Dict[float, np.ndarray]

    @property
    def pair_count(self) -> int:
        return len(self.errors_pct)

    @property
    def rmse_pct(self) -> float:
        if not len(self.errors_pct):
            return float("nan")
        return float(np.sqrt(np.mean(np.square(self.errors_pct))))

    @property
    def rmse_by_distance_pct(self) -> Dict[float, float]:
        return {
            distance: float(np.sqrt(np.mean(np.square(errors))))
            for distance, errors in self.errors_by_distance_pct.items()
        }


@dataclass(frozen=True)
class EVOSceneResult:
    """一次场景 EVO 评定产生的关联索引和全部原始误差。"""
    matched_reference_indices: np.ndarray
    reliable_reference_indices: np.ndarray
    ape_errors_m: np.ndarray
    ate_rmse_m: float
    rpe: RPEResult
    rte: RTEResult


def to_evo_trajectory(trajectory: Trajectory) -> PoseTrajectory3D:
    """TUM 使用 xyzw，EVO 使用 wxyz；进入 EVO 前必须调整四元数列顺序。"""
    return PoseTrajectory3D(
        positions_xyz=trajectory.positions,
        orientations_quat_wxyz=trajectory.quaternions_xyzw[:, [3, 0, 1, 2]],
        timestamps=trajectory.timestamps,
    )


def from_evo_trajectory(trajectory: PoseTrajectory3D) -> Trajectory:
    """把 EVO 轨迹转换回评分器统一使用的 xyzw 格式。"""
    return Trajectory(
        timestamps=np.asarray(trajectory.timestamps, dtype=float),
        positions=np.asarray(trajectory.positions_xyz, dtype=float),
        quaternions_xyzw=np.asarray(
            trajectory.orientations_quat_wxyz[:, [1, 2, 3, 0]], dtype=float
        ),
    )


def _subset(trajectory: PoseTrajectory3D, ids: np.ndarray) -> PoseTrajectory3D:
    """按索引构造独立 EVO 轨迹，供连续有效配对区间计算 RPE。"""
    return PoseTrajectory3D(
        positions_xyz=trajectory.positions_xyz[ids],
        orientations_quat_wxyz=trajectory.orientations_quat_wxyz[ids],
        timestamps=trajectory.timestamps[ids],
    )


def _position_only_evo_trajectory(trajectory: Trajectory) -> PoseTrajectory3D:
    """保留位置并将姿态设为单位四元数，使相对指标只评价世界系平移。"""
    return PoseTrajectory3D(
        positions_xyz=trajectory.positions,
        orientations_quat_wxyz=np.tile(
            np.array([1.0, 0.0, 0.0, 0.0]),
            (len(trajectory.timestamps), 1),
        ),
        timestamps=trajectory.timestamps,
    )


def _source_indices(source_timestamps: np.ndarray, query_timestamps: np.ndarray) -> np.ndarray:
    """把 EVO 同步后的时间戳映射回原 reference 行号。"""
    indices = np.searchsorted(source_timestamps, query_timestamps)
    if np.any(indices >= len(source_timestamps)) or not np.allclose(
        source_timestamps[indices], query_timestamps, rtol=0.0, atol=1e-12
    ):
        raise ValueError("EVO synchronization returned unknown reference timestamps")
    return indices.astype(int)


def _contiguous_segments(
    timestamps: np.ndarray,
    max_gap_sec: float,
    source_indices: np.ndarray = None,
) -> List[np.ndarray]:
    """找出相对误差可以配对的连续区间；此函数不参与 EVO 对齐。"""
    if max_gap_sec <= 0.0:
        raise ValueError("maximum RPE segment gap must be positive")
    if not len(timestamps):
        return []
    gaps = np.diff(timestamps)
    timestamp_scale = np.maximum(np.abs(timestamps[:-1]), np.abs(timestamps[1:]))
    gap_tolerance = 2.0 * np.spacing(
        np.maximum(timestamp_scale, abs(max_gap_sec))
    )
    split_mask = gaps > max_gap_sec + gap_tolerance
    if source_indices is not None:
        if source_indices.shape != timestamps.shape:
            raise ValueError("source indices must match trajectory timestamps")
        split_mask |= np.diff(source_indices) != 1
    split_indices = np.flatnonzero(split_mask) + 1
    return [
        indices
        for indices in np.split(np.arange(len(timestamps), dtype=int), split_indices)
        if len(indices) >= 2
    ]


def one_frame_translation_rpe(
    reference: Trajectory,
    estimate: Trajectory,
    max_gap_sec: float,
    source_indices: np.ndarray = None,
) -> RPEResult:
    if len(reference.timestamps) != len(estimate.timestamps):
        raise ValueError("RPE trajectories must contain the same number of poses")

    # 平移赛道不应受参赛者 body 轴定义影响。单位姿态让 EVO 比较世界系位置增量，
    # 同时继续使用 EVO 官方 RPE 实现。
    reference_evo = _position_only_evo_trajectory(reference)
    estimate_evo = _position_only_evo_trajectory(estimate)
    errors = []
    for ids in _contiguous_segments(
        reference.timestamps, max_gap_sec, source_indices
    ):
        reference_segment = PoseTrajectory3D(
            positions_xyz=reference_evo.positions_xyz[ids],
            orientations_quat_wxyz=reference_evo.orientations_quat_wxyz[ids],
            timestamps=reference_evo.timestamps[ids],
        )
        estimate_segment = PoseTrajectory3D(
            positions_xyz=estimate_evo.positions_xyz[ids],
            orientations_quat_wxyz=estimate_evo.orientations_quat_wxyz[ids],
            timestamps=estimate_evo.timestamps[ids],
        )
        metric = metrics.RPE(
            pose_relation=metrics.PoseRelation.translation_part,
            delta=1,
            delta_unit=metrics.Unit.frames,
            rel_delta_tol=0.1,
            all_pairs=False,
        )
        metric.process_data((reference_segment, estimate_segment))
        errors.extend(np.asarray(metric.error, dtype=float).tolist())
    return RPEResult(errors_m=np.asarray(errors, dtype=float))


def one_frame_pair_count(
    timestamps: np.ndarray,
    max_gap_sec: float,
    source_indices: np.ndarray = None,
) -> int:
    """统计 reference 理论上应有的一帧相邻对，作为 AUC recall 分母。"""
    return int(
        sum(
            len(segment) - 1
            for segment in _contiguous_segments(
                timestamps, max_gap_sec, source_indices
            )
        )
    )


def distance_translation_rte(
    reference: Trajectory,
    estimate: Trajectory,
    max_gap_sec: float,
    distances_m: Sequence[float] = (1.0, 5.0, 10.0),
    source_indices: np.ndarray = None,
) -> RTEResult:
    """计算参考路程 1/5/10 m 的平移 RPE，并除以名义距离转换成百分比。"""
    if len(reference.timestamps) != len(estimate.timestamps):
        raise ValueError("RTE trajectories must contain the same number of poses")
    if any(distance <= 0.0 for distance in distances_m):
        raise ValueError("RTE distances must be positive")

    reference_evo = _position_only_evo_trajectory(reference)
    estimate_evo = _position_only_evo_trajectory(estimate)
    errors_pct = []
    pair_counts = {}
    errors_by_distance_pct = {}
    segments = _contiguous_segments(
        reference.timestamps, max_gap_sec, source_indices
    )
    for distance in distances_m:
        count = 0
        for ids in segments:
            # pairs_from_reference=True：距离配对由参考轨迹路程决定；
            # rel_delta_tol=0.1：允许实际参考距离相对名义距离偏差 10%。
            metric = metrics.RPE(
                pose_relation=metrics.PoseRelation.translation_part,
                delta=float(distance),
                delta_unit=metrics.Unit.meters,
                rel_delta_tol=0.1,
                all_pairs=True,
                pairs_from_reference=True,
            )
            try:
                metric.process_data(
                    (_subset(reference_evo, ids), _subset(estimate_evo, ids))
                )
            except filters.FilterException:
                continue
            normalized = np.asarray(metric.error, dtype=float) / float(distance) * 100.0
            errors_pct.extend(normalized.tolist())
            count += len(normalized)
        if count:
            pair_counts[float(distance)] = count
            errors_by_distance_pct[float(distance)] = np.asarray(
                errors_pct[-count:], dtype=float
            )
    return RTEResult(
        errors_pct=np.asarray(errors_pct, dtype=float),
        pair_counts=pair_counts,
        errors_by_distance_pct=errors_by_distance_pct,
    )


def evaluate_evo_scene(
    reference: Trajectory,
    estimate: Trajectory,
    max_sync_diff_sec: float,
    max_gap_sec: float,
) -> EVOSceneResult:
    """对一个完整 scene 执行一次时间关联、一次对齐和全部平移指标计算。"""
    # 第一步：整个 scene 的私有 reference 与参赛轨迹做一次 EVO 时间关联。
    try:
        reference_synced, estimate_synced = sync.associate_trajectories(
            to_evo_trajectory(reference),
            to_evo_trajectory(estimate),
            max_diff=max_sync_diff_sec,
        )
    except Exception as exc:
        raise ValueError("EVO could not synchronize trajectories") from exc
    matched_reference_indices = _source_indices(
        reference.timestamps, np.asarray(reference_synced.timestamps, dtype=float)
    )
    if len(matched_reference_indices) < 3:
        raise ValueError("fewer than three reliable positions were matched")
    reference_reliable = reference_synced
    estimate_reliable = estimate_synced
    # 第二步：对全部已匹配可靠位姿只计算一次无尺度 SE(3) 对齐。
    # correct_scale=False 表示只估计一个旋转和平移，不允许用尺度修正轨迹。
    aligned_estimate = copy.deepcopy(estimate_reliable)
    try:
        aligned_estimate.align(
            reference_reliable,
            correct_scale=False,
            correct_only_scale=False,
        )
        ape = metrics.APE(pose_relation=metrics.PoseRelation.translation_part)
        ape.process_data((reference_reliable, aligned_estimate))
    except Exception as exc:
        raise ValueError("EVO could not align reliable trajectory positions") from exc
    statistics = ape.get_all_statistics()
    reference_aligned = from_evo_trajectory(reference_reliable)
    estimate_aligned = from_evo_trajectory(aligned_estimate)
    reliable_reference_indices = matched_reference_indices
    # 第三步：ATE 使用全部匹配位姿；相对指标只使用连续、相邻的有效配对。
    return EVOSceneResult(
        matched_reference_indices=matched_reference_indices,
        reliable_reference_indices=reliable_reference_indices,
        ape_errors_m=np.asarray(ape.error, dtype=float),
        ate_rmse_m=float(statistics["rmse"]),
        rpe=one_frame_translation_rpe(
            reference_aligned,
            estimate_aligned,
            max_gap_sec=max_gap_sec,
            source_indices=reliable_reference_indices,
        ),
        rte=distance_translation_rte(
            reference_aligned,
            estimate_aligned,
            max_gap_sec=max_gap_sec,
            source_indices=reliable_reference_indices,
        ),
    )
