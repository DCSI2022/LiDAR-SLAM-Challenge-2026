"""把评分结果渲染成 Codabench 可展示的安全 HTML。"""

import html
import math

from .models import ScoringResult, TrackConfig


def render_report(result: ScoringResult, config: TrackConfig) -> str:
    """生成综合指标、分项贡献和逐场景指标表格。"""
    # 配置标题来自组织方文件，仍使用 html.escape，避免未来修改时引入 HTML 注入。
    title = html.escape(config.title)
    ate_score = math.exp(-result.ate_rmse_m / config.tau_ate_m)
    rte_score = math.exp(-result.rte_pct / config.tau_rte_pct)
    auc_contribution = 100.0 * config.score_weights["auc"] * result.auc
    ate_contribution = 100.0 * config.score_weights["ate"] * ate_score
    rte_contribution = 100.0 * config.score_weights["rte"] * rte_score
    # 逐场景表只输出汇总指标，不输出逐时刻误差、时间关联或对齐矩阵。
    rows = []
    for scene_id in config.scenes:
        scene = result.scenes[scene_id]
        rows.append(
            "<tr>"
            f"<td>{html.escape(scene_id)}</td>"
            f"<td>{scene.matched_count} / {scene.expected_count}</td>"
            f"<td>{scene.completion * 100.0:.2f}%</td>"
            f"<td>{scene.auc:.6f}</td>"
            f"<td>{scene.ate_rmse_m:.6f}</td>"
            f"<td>{scene.rte_pct:.6f}</td>"
            f"<td>{scene.rte_by_distance_pct[1.0]:.6f}</td>"
            f"<td>{scene.rte_by_distance_pct[5.0]:.6f}</td>"
            f"<td>{scene.rte_by_distance_pct[10.0]:.6f}</td>"
            "<td>valid</td>"
            "</tr>"
        )
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        f"<title>{title} result</title></head><body>"
        f"<h1>{title} result</h1>"
        "<table><tbody>"
        f"<tr><th>Total Score</th><td>{result.total_score:.6f}</td></tr>"
        f"<tr><th>AUC</th><td>{result.auc:.6f}</td></tr>"
        f"<tr><th>ATE RMSE (m)</th><td>{result.ate_rmse_m:.6f}</td></tr>"
        f"<tr><th>RTE (%)</th><td>{result.rte_pct:.6f}</td></tr>"
        f"<tr><th>RTE@1m (%)</th><td>{result.rte_by_distance_pct[1.0]:.6f}</td></tr>"
        f"<tr><th>RTE@5m (%)</th><td>{result.rte_by_distance_pct[5.0]:.6f}</td></tr>"
        f"<tr><th>RTE@10m (%)</th><td>{result.rte_by_distance_pct[10.0]:.6f}</td></tr>"
        f"<tr><th>ATE normalized</th><td>{ate_score:.6f}</td></tr>"
        f"<tr><th>RTE normalized</th><td>{rte_score:.6f}</td></tr>"
        f"<tr><th>AUC contribution</th><td>{auc_contribution:.6f}</td></tr>"
        f"<tr><th>ATE contribution</th><td>{ate_contribution:.6f}</td></tr>"
        f"<tr><th>RTE contribution</th><td>{rte_contribution:.6f}</td></tr>"
        "</tbody></table>"
        "<h2>Scene metrics</h2><table><thead><tr>"
        "<th>Scene</th><th>Matched</th><th>Completion</th>"
        "<th>AUC</th><th>ATE RMSE (m)</th><th>RTE (%)</th>"
        "<th>RTE@1m (%)</th><th>RTE@5m (%)</th><th>RTE@10m (%)</th><th>Status</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></body></html>"
    )


def render_error_report(message: str) -> str:
    """生成无效提交报告；错误消息同样必须 HTML 转义。"""
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<title>Invalid submission</title></head><body>"
        "<h1>Invalid submission</h1>"
        f"<p>{html.escape(message)}</p>"
        "</body></html>"
    )
