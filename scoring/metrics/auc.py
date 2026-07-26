"""根据一帧平移 RPE 计算兼顾精度和轨迹覆盖率的 F1-AUC。"""

from dataclasses import dataclass
import math

import numpy as np


@dataclass(frozen=True)
class AUCResult:
    """AUC 值以及理论/实际相邻位姿对数量。"""
    auc: float
    expected_pairs: int
    observed_pairs: int


def robustness_auc_from_rpe_errors(errors: np.ndarray, expected_pairs: int) -> AUCResult:
    """在指数误差阈值上积分 F1；缺失位姿对只降低 recall。"""
    errors = np.asarray(errors, dtype=float)
    observed_pairs = len(errors)
    if expected_pairs <= 0 or observed_pairs == 0:
        return AUCResult(auc=0.0, expected_pairs=expected_pairs, observed_pairs=observed_pairs)
    if not np.isfinite(errors).all() or np.any(errors < 0.0):
        raise ValueError("RPE errors must be finite and non-negative")

    # 阈值从约 0.61 m 递减到约 0.000075 m；每个阈值计算一次 precision/recall/F1。
    area = 0.0
    for parameter in np.arange(0.05, 1.0, 0.1):
        threshold = math.exp(-10.0 * float(parameter))
        good = int(np.count_nonzero(errors <= threshold))
        precision = good / observed_pairs
        recall = min(1.0, good / expected_pairs)
        f1 = 0.0 if precision + recall == 0.0 else 2.0 * precision * recall / (precision + recall)
        left = float(parameter) - 0.05
        right = float(parameter) + 0.05
        area += f1 * (math.exp(-10.0 * left) - math.exp(-10.0 * right))
    # 除以理论最大面积，把结果归一化到 [0, 1]。
    maximum_area = 1.0 - math.exp(-10.0)
    return AUCResult(
        auc=min(1.0, max(0.0, area / maximum_area)),
        expected_pairs=expected_pairs,
        observed_pairs=observed_pairs,
    )
