"""Codabench 评分入口：连接平台目录、核心评分器和排行榜输出。"""

import json
from pathlib import Path
import sys

from .errors import ParticipantInputError
from .reference import load_reference_data, load_track_config
from .report import render_error_report, render_report
from .scoring import score_submission
from .submission import load_submission


def run_codabench(
    result_dir: Path,
    reference_dir: Path,
    output_dir: Path,
    public_config_path: Path,
) -> int:
    """读取参赛轨迹和私有真值，执行评分并输出 Codabench 成绩文件。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    score_path = output_dir / "scores.json"
    if score_path.exists():
        score_path.unlink()

    # reference 只在评分容器中可见；参赛者提交中只允许出现估计轨迹。
    config = load_track_config(public_config_path)
    references = load_reference_data(reference_dir, config)
    try:
        submissions = load_submission(result_dir, config.scenes)
        result = score_submission(submissions, references, config)
    except ParticipantInputError as exc:
        # 参赛输入错误不产生 scores.json，避免无效提交覆盖之前的有效榜单成绩。
        (output_dir / "detailed_results.html").write_text(
            render_error_report(str(exc)), encoding="utf-8"
        )
        print(f"Invalid submission: {exc}", file=sys.stderr)
        return 2

    # scores.json 的键与 competition.yaml 中排行榜列的 key 一一对应。
    scores = {
        "total_score": round(result.total_score, 12),
        "auc": round(result.auc, 12),
        "ate_rmse_m": round(result.ate_rmse_m, 12),
        "rte_pct": round(result.rte_pct, 12),
        "rte_1m_pct": round(result.rte_by_distance_pct[1.0], 12),
        "rte_5m_pct": round(result.rte_by_distance_pct[5.0], 12),
        "rte_10m_pct": round(result.rte_by_distance_pct[10.0], 12),
        "completion_pct": round(result.completion_pct, 12),
    }
    # 除综合指标外，逐场景和三档 RTE 也全部写入排行榜字段。
    for scene_id in config.scenes:
        scene = result.scenes[scene_id]
        scores[f"{scene_id}_auc"] = round(scene.auc, 12)
        scores[f"{scene_id}_ate_rmse_m"] = round(scene.ate_rmse_m, 12)
        scores[f"{scene_id}_rte_pct"] = round(scene.rte_pct, 12)
        for distance in (1, 5, 10):
            scores[f"{scene_id}_rte_{distance}m_pct"] = round(
                scene.rte_by_distance_pct[float(distance)], 12
            )
    score_path.write_text(
        json.dumps(scores, allow_nan=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    # HTML 详细报告便于参赛者阅读；其数值来源与 scores.json 是同一个 result。
    (output_dir / "detailed_results.html").write_text(
        render_report(result, config), encoding="utf-8"
    )
    return 0
