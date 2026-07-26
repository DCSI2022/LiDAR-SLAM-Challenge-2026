"""公开提交格式检查器；不包含私有 reference，因此不计算精度。"""

import argparse
import json
from pathlib import Path

from .errors import ParticipantInputError
from .reference import load_track_config
from .submission import load_submission


def validate_public_submission(
    submission_dir: Path,
    config_path: Path,
) -> dict:
    """复用正式解析器检查目录、文件名和 TUM 数值格式。"""
    config = load_track_config(config_path)
    submissions = load_submission(submission_dir, config.scenes)
    return {scene_id: len(trajectory.timestamps) for scene_id, trajectory in submissions.items()}


def main() -> int:
    # 成功时输出每个场景的轨迹行数，方便参赛者快速确认文件没有传错。
    parser = argparse.ArgumentParser(description="Validate a SLAM competition result submission")
    parser.add_argument("--submission", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = validate_public_submission(args.submission, args.config)
    except ParticipantInputError as exc:
        parser.exit(2, f"invalid submission: {exc}\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
