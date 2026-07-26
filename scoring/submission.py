"""读取并严格校验参赛者提交目录和 TUM 轨迹。"""

from pathlib import Path

import numpy as np

from .errors import ParticipantInputError
from .models import Trajectory


def load_submission(root: Path, scene_ids: tuple) -> dict:
    """检查提交目录结构，并返回两个指定场景的轨迹。"""
    if not (root / "README.md").is_file():
        raise ParticipantInputError("submission must contain README.md")
    trajectories_dir = root / "trajectories"
    if not trajectories_dir.is_dir():
        raise ParticipantInputError("submission must contain trajectories/")

    # 明确拒绝缺失或多余 txt，防止队伍上传错场景却得到难以解释的成绩。
    expected_names = {f"{scene_id}.txt" for scene_id in scene_ids}
    actual_names = {path.name for path in trajectories_dir.glob("*.txt")}
    missing = sorted(expected_names - actual_names)
    if missing:
        raise ParticipantInputError(f"missing required trajectory: {missing[0]}")
    unexpected = sorted(actual_names - expected_names)
    if unexpected:
        raise ParticipantInputError(f"unexpected trajectory: {unexpected[0]}")

    return {
        scene_id: load_tum(trajectories_dir / f"{scene_id}.txt")
        for scene_id in scene_ids
    }


def load_tum(path: Path) -> Trajectory:
    """读取八列 TUM：timestamp tx ty tz qx qy qz qw。"""
    rows = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ParticipantInputError(f"cannot read trajectory: {path.name}") from exc

    # 允许空行和 # 注释，但每条有效数据必须恰好八列。
    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = stripped.split()
        if len(fields) != 8:
            raise ParticipantInputError(
                f"{path.name}:{line_number}: expected 8 numeric columns, got {len(fields)}"
            )
        try:
            rows.append([float(field) for field in fields])
        except ValueError as exc:
            raise ParticipantInputError(
                f"{path.name}:{line_number}: contains a non-numeric value"
            ) from exc

    if not rows:
        raise ParticipantInputError(f"{path.name}: trajectory is empty")

    # 数值检查集中在这里，公开格式校验器与正式评分器因此使用完全相同的规则。
    data = np.asarray(rows, dtype=float)
    if not np.isfinite(data).all():
        raise ParticipantInputError(f"{path.name}: all values must be finite")
    if len(data) > 1 and np.any(np.diff(data[:, 0]) <= 0.0):
        raise ParticipantInputError(f"{path.name}: timestamps must be strictly increasing")
    if np.any(np.linalg.norm(data[:, 4:8], axis=1) == 0.0):
        raise ParticipantInputError(f"{path.name}: quaternions must be non-zero")
    return Trajectory(
        timestamps=data[:, 0],
        positions=data[:, 1:4],
        quaternions_xyzw=data[:, 4:8],
    )
