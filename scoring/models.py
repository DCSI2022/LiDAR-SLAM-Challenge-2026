"""评分器的数据模型；这里只定义公开数据结构，不负责文件读取或指标计算。"""

from dataclasses import dataclass
from typing import Dict, Tuple

import numpy as np


@dataclass(frozen=True)
class Trajectory:
    """内存中的 TUM 轨迹，四元数固定使用 [x, y, z, w] 顺序。"""
    timestamps: np.ndarray
    positions: np.ndarray
    quaternions_xyzw: np.ndarray

    def __post_init__(self) -> None:
        # 尽早检查数组形状，避免错误一直传播到 EVO 后才以难懂的异常形式出现。
        count = len(self.timestamps)
        if self.timestamps.shape != (count,):
            raise ValueError("timestamps must have shape (N,)")
        if self.positions.shape != (count, 3):
            raise ValueError("positions must have shape (N, 3)")
        if self.quaternions_xyzw.shape != (count, 4):
            raise ValueError("quaternions must have shape (N, 4)")


@dataclass(frozen=True)
class Association:
    """一对一时间关联的索引和时间差；保留给独立关联工具与测试使用。"""
    evaluation_indices: np.ndarray
    estimate_indices: np.ndarray
    time_differences: np.ndarray

    def __post_init__(self) -> None:
        count = len(self.evaluation_indices)
        if self.evaluation_indices.shape != (count,):
            raise ValueError("evaluation_indices must have shape (N,)")
        if self.estimate_indices.shape != (count,):
            raise ValueError("estimate_indices must have shape (N,)")
        if self.time_differences.shape != (count,):
            raise ValueError("time_differences must have shape (N,)")


@dataclass(frozen=True)
class TrackConfig:
    """从冻结 JSON 读取的赛道规则。"""
    track_key: str
    title: str
    scenes: Tuple[str, str]
    max_sync_diff_sec: float
    segment_max_gap_sec: float
    tau_ate_m: float
    tau_rte_pct: float
    score_weights: Dict[str, float]
    config_sha256: str

    @classmethod
    def from_dict(cls, data: dict) -> "TrackConfig":
        """将 JSON 字段转换成强类型配置，并统一执行合法性检查。"""
        try:
            scenes = tuple(str(value) for value in data["scenes"])
            weights = {str(key): float(value) for key, value in data["score_weights"].items()}
            config = cls(
                track_key=str(data["track_key"]),
                title=str(data["title"]),
                scenes=scenes,
                max_sync_diff_sec=float(data["max_sync_diff_sec"]),
                segment_max_gap_sec=float(data["segment_max_gap_sec"]),
                tau_ate_m=float(data["tau_ate_m"]),
                tau_rte_pct=float(data["tau_rte_pct"]),
                score_weights=weights,
                config_sha256=str(data["config_sha256"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            from .errors import ConfigurationError

            raise ConfigurationError("track configuration is missing or has invalid fields") from exc
        config.validate()
        return config

    def validate(self) -> None:
        """拒绝场景、阈值、tau 或固定权重被意外修改。"""
        from .errors import ConfigurationError

        if len(self.scenes) != 2 or len(set(self.scenes)) != 2:
            raise ConfigurationError("track configuration must contain exactly two unique scenes")
        if not self.track_key or not self.title:
            raise ConfigurationError("track key and title must be non-empty")
        if self.max_sync_diff_sec <= 0.0 or self.segment_max_gap_sec <= 0.0:
            raise ConfigurationError("time thresholds must be positive")
        if self.tau_ate_m <= 0.0 or self.tau_rte_pct <= 0.0:
            raise ConfigurationError("tau values must be positive")
        if self.score_weights != {"auc": 0.40, "ate": 0.40, "rte": 0.20}:
            raise ConfigurationError("score weights must be AUC 0.40, ATE 0.40, RTE 0.20")


@dataclass(frozen=True)
class ReferenceScene:
    """一个场景的私有参考轨迹；只在 Codabench 评分环境中实例化。"""
    scene_id: str
    trajectory: Trajectory


@dataclass(frozen=True)
class SceneMetrics:
    """单场景公开指标及内部聚合所需计数。"""
    scene_id: str
    completion: float
    matched_count: int
    expected_count: int
    reliable_matched_count: int
    ate_rmse_m: float
    auc: float
    auc_expected_pairs: int
    auc_observed_pairs: int
    rte_pct: float
    rte_by_distance_pct: Dict[float, float]
    rte_pairs: int


@dataclass(frozen=True)
class ScoringResult:
    """两个场景聚合后的排行榜结果。"""
    total_score: float
    auc: float
    ate_rmse_m: float
    rte_pct: float
    rte_by_distance_pct: Dict[float, float]
    completion_pct: float
    scenes: Dict[str, SceneMetrics]
