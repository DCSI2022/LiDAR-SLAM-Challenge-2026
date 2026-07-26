"""加载冻结赛道配置和 Codabench 私有 reference。"""

import hashlib
import json
from pathlib import Path

import numpy as np

from .errors import ConfigurationError
from .errors import ParticipantInputError
from .models import ReferenceScene, TrackConfig
from .submission import load_tum


def config_hash(data: dict) -> str:
    """对除 config_sha256 外的 JSON 内容计算稳定 SHA-256。"""
    payload = {key: value for key, value in data.items() if key != "config_sha256"}
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def load_track_config(path: Path) -> TrackConfig:
    """读取配置并先核对哈希，再创建 TrackConfig。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError("cannot load track configuration") from exc
    if not isinstance(data, dict):
        raise ConfigurationError("track configuration must be a JSON object")
    expected_hash = config_hash(data)
    if data.get("config_sha256") != expected_hash:
        raise ConfigurationError("track configuration hash mismatch")
    return TrackConfig.from_dict(data)


def load_evaluation_timestamps(path: Path) -> np.ndarray:
    """读取组织方制备 reference 时使用的单列时间戳文件。"""
    try:
        values = np.loadtxt(str(path), dtype=float)
    except (OSError, ValueError) as exc:
        raise ConfigurationError("cannot load evaluation timestamps") from exc
    timestamps = np.atleast_1d(values).astype(float)
    if timestamps.ndim != 1 or len(timestamps) == 0:
        raise ConfigurationError("evaluation timestamps must be a non-empty column")
    if not np.isfinite(timestamps).all() or np.any(np.diff(timestamps) <= 0.0):
        raise ConfigurationError("evaluation timestamps must be finite and increasing")
    return timestamps


def load_reference_scenes(root: Path, expected_config: TrackConfig) -> dict:
    """按配置顺序加载每个场景的私有 TUM reference。"""
    result = {}
    for scene_id in expected_config.scenes:
        scene_root = root / scene_id
        try:
            trajectory = load_tum(scene_root / "ground_truth_reliable.txt")
        except ParticipantInputError as exc:
            raise ConfigurationError("reliable ground truth trajectory is invalid") from exc
        result[scene_id] = ReferenceScene(
            scene_id=scene_id,
            trajectory=trajectory,
        )
    return result


def load_reference_data(root: Path, expected_config: TrackConfig) -> dict:
    """确保评分程序与私有数据使用同一冻结配置后，再加载 reference。"""
    private_config = load_track_config(root / "track_config.json")
    if private_config.config_sha256 != expected_config.config_sha256:
        raise ConfigurationError("public and private track configuration hashes differ")
    return load_reference_scenes(root, expected_config)
