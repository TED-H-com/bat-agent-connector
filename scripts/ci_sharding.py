"""Deterministic whole-file CI partitioning; no pytest or project dependencies."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from statistics import median


def collection_digest(nodeids: list[str]) -> str:
    """Hash sorted node IDs as compact ASCII JSON (including parameter IDs)."""
    payload = json.dumps(sorted(nodeids), ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def load_weights(path: Path) -> dict[str, float]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or not isinstance(data.get("files"), dict):
        raise ValueError("expected a version 1 durations table with a files mapping")
    weights = data["files"]
    for name, weight in weights.items():
        if not isinstance(weight, (int, float)) or isinstance(weight, bool) or not math.isfinite(weight) or weight < 0:
            raise ValueError(f"invalid weight for {name}")
    return weights


def assign_files(paths: list[str], weights: dict[str, float], count: int) -> dict[str, int]:
    """Longest first; equal weights use path order, equal loads use file count/index."""
    if count < 1:
        raise ValueError("shard count must be positive")
    fallback = median(weights.values()) if weights else 1.0
    loads = [0.0] * count
    sizes = [0] * count
    assignment = {}
    for path in sorted(set(paths), key=lambda path: (-weights.get(path, fallback), path)):
        index = min(range(count), key=lambda index: (loads[index], sizes[index], index))
        assignment[path] = index + 1
        loads[index] += weights.get(path, fallback)
        sizes[index] += 1
    return assignment
