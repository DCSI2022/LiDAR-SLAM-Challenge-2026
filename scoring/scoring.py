"""将 EVO 原始误差聚合成单场景指标、跨场景指标和最终总分。"""

import math
from dataclasses import dataclass

import numpy as np

from .errors import ParticipantInputError
from .metrics.auc import robustness_auc_from_rpe_errors
from .metrics.evo import evaluate_evo_scene, one_frame_pair_count
from .models import ReferenceScene, SceneMetrics, ScoringResult, TrackConfig, Trajectory


@dataclass(frozen=True)
class _SceneEvaluation:
    """内部结果：公开指标之外保留原始误差，以便跨场景重新计算 RMSE。"""
    metrics: SceneMetrics
    ape_errors_m: np.ndarray
    auc_errors_m: np.ndarray
    rte_errors_pct: np.ndarray
    rte_errors_by_distance_pct: dict


def _rmse(errors: np.ndarray) -> float:
    """统一的 RMSE 实现；空数组返回 NaN，由上层转成明确错误。"""
    if not len(errors):
        return float("nan")
    return float(np.sqrt(np.mean(np.square(errors))))


def _evaluate_scene(
    estimate: Trajectory,
    reference: ReferenceScene,
    config: TrackConfig,
) -> _SceneEvaluation:
    """完成时间关联、单场景 SE(3) 对齐，并计算该场景的三类误差。"""
    try:
        evo_result = evaluate_evo_scene(
            reference.trajectory,
            estimate,
            config.max_sync_diff_sec,
            config.segment_max_gap_sec,
        )
    except ValueError as exc:
        raise ParticipantInputError(f"{reference.scene_id}: {exc}") from exc
    # 完成度以私有 reference 为分母并公开展示；不设硬门槛，缺失覆盖由 AUC recall 扣分。
    completion = len(evo_result.matched_reference_indices) / len(reference.trajectory.timestamps)
    # 三档 RTE 都必须可计算，否则无法提供完整、可比较的排行榜字段。
    required_rte_distances = {1.0, 5.0, 10.0}
    if (
        set(evo_result.rte.pair_counts) != required_rte_distances
        or not math.isfinite(evo_result.rte.rmse_pct)
    ):
        raise ParticipantInputError(
            f"{reference.scene_id}: valid 1 m, 5 m, and 10 m RTE pairs are required"
        )
    if evo_result.rpe.pair_count == 0:
        raise ParticipantInputError(f"{reference.scene_id}: no valid AUC RPE pairs were found")
    expected_rpe_pairs = one_frame_pair_count(
        reference.trajectory.timestamps,
        config.segment_max_gap_sec,
    )
    auc_result = robustness_auc_from_rpe_errors(evo_result.rpe.errors_m, expected_rpe_pairs)
    return _SceneEvaluation(
        metrics=SceneMetrics(
            scene_id=reference.scene_id,
            completion=completion,
            matched_count=len(evo_result.matched_reference_indices),
            expected_count=len(reference.trajectory.timestamps),
            reliable_matched_count=len(evo_result.reliable_reference_indices),
            ate_rmse_m=_rmse(evo_result.ape_errors_m),
            auc=auc_result.auc,
            auc_expected_pairs=auc_result.expected_pairs,
            auc_observed_pairs=auc_result.observed_pairs,
            rte_pct=evo_result.rte.rmse_pct,
            rte_by_distance_pct=evo_result.rte.rmse_by_distance_pct,
            rte_pairs=evo_result.rte.pair_count,
        ),
        ape_errors_m=evo_result.ape_errors_m,
        auc_errors_m=evo_result.rpe.errors_m,
        rte_errors_pct=evo_result.rte.errors_pct,
        rte_errors_by_distance_pct=evo_result.rte.errors_by_distance_pct,
    )


def score_scene(
    estimate: Trajectory,
    reference: ReferenceScene,
    config: TrackConfig,
) -> SceneMetrics:
    return _evaluate_scene(estimate, reference, config).metrics


def score_submission(
    submissions: dict,
    references: dict,
    config: TrackConfig,
) -> ScoringResult:
    expected_scenes = set(config.scenes)
    if set(submissions) != expected_scenes:
        raise ParticipantInputError("submission scene set does not match the track configuration")
    if set(references) != expected_scenes:
        raise ValueError("reference scene set does not match the track configuration")

    # 两个 scene 各自执行一次 EVO 对齐和指标计算。
    evaluations = {
        scene_id: _evaluate_scene(submissions[scene_id], references[scene_id], config)
        for scene_id in config.scenes
    }
    scenes = {scene_id: evaluation.metrics for scene_id, evaluation in evaluations.items()}
    # 聚合指标合并两个 scene 的原始误差后重算，不对两个场景分数做算术平均。
    ape_errors = np.concatenate(
        [evaluations[scene_id].ape_errors_m for scene_id in config.scenes]
    )
    auc_errors = np.concatenate(
        [evaluations[scene_id].auc_errors_m for scene_id in config.scenes]
    )
    rte_errors = np.concatenate(
        [evaluations[scene_id].rte_errors_pct for scene_id in config.scenes]
    )
    expected_auc_pairs = sum(scene.auc_expected_pairs for scene in scenes.values())
    auc_result = robustness_auc_from_rpe_errors(auc_errors, expected_auc_pairs)
    ate = _rmse(ape_errors)
    rte = _rmse(rte_errors)
    # 分档 RTE 同样合并两个 scene 对应距离的原始误差后重算 RMSE。
    rte_by_distance_pct = {
        distance: _rmse(
            np.concatenate(
                [
                    evaluations[scene_id].rte_errors_by_distance_pct[distance]
                    for scene_id in config.scenes
                    if distance in evaluations[scene_id].rte_errors_by_distance_pct
                ]
            )
        )
        for distance in (1.0, 5.0, 10.0)
    }
    if not all(math.isfinite(value) for value in (ate, auc_result.auc, rte)):
        raise ValueError("reference data does not provide valid aggregate metrics")
    completion_pct = float(
        np.mean([scene.completion for scene in scenes.values()]) * 100.0
    )
    score = total_score(auc_result.auc, ate, rte, config.tau_ate_m, config.tau_rte_pct)
    return ScoringResult(
        total_score=score,
        auc=auc_result.auc,
        ate_rmse_m=ate,
        rte_pct=rte,
        rte_by_distance_pct=rte_by_distance_pct,
        completion_pct=completion_pct,
        scenes=scenes,
    )


def total_score(
    auc: float,
    ate_m: float,
    rte_pct: float,
    tau_ate_m: float,
    tau_rte_pct: float,
) -> float:
    """将 AUC、ATE、RTE 映射成 0 到 100 的综合分。"""
    values = (auc, ate_m, rte_pct, tau_ate_m, tau_rte_pct)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("score inputs must be finite")
    if not 0.0 <= auc <= 1.0:
        raise ValueError("AUC must be between zero and one")
    if ate_m < 0.0 or rte_pct < 0.0:
        raise ValueError("ATE and RTE must be non-negative")
    if tau_ate_m <= 0.0 or tau_rte_pct <= 0.0:
        raise ValueError("tau values must be positive")
    # AUC 越高越好；ATE/RTE 越低，指数得分越接近 1。
    return 100.0 * (
        0.40 * auc
        + 0.40 * math.exp(-ate_m / tau_ate_m)
        + 0.20 * math.exp(-rte_pct / tau_rte_pct)
    )
