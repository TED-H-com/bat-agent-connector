"""Opt-in CI plugin: load with -p tests.ci_shard_plugin. Local defaults are unchanged."""

from __future__ import annotations

import json
import platform
from pathlib import Path

import pytest

from scripts.ci_sharding import assign_files, collection_digest, load_weights


def pytest_addoption(parser):
    group = parser.getgroup("ci-sharding")
    group.addoption("--ci-shard-index", type=int, help="One-based shard index")
    group.addoption("--ci-shard-count", type=int, help="Number of separate CI runners")
    group.addoption("--ci-shard-manifest", type=Path, help="Output JSON manifest")
    group.addoption("--ci-durations", type=Path, default=Path("tests/ci_durations.json"))


def pytest_configure(config):
    index = config.getoption("ci_shard_index")
    count = config.getoption("ci_shard_count")
    manifest = config.getoption("ci_shard_manifest")
    if index is None and count is None and manifest is None:
        return
    if index is None or count is None or manifest is None or not 1 <= index <= count:
        raise pytest.UsageError("CI sharding requires index, count and manifest, with 1 <= index <= count")
    if config.getoption("numprocesses", default=None):
        raise pytest.UsageError("CI shards require separate runners, not pytest-xdist")


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
    index = config.getoption("ci_shard_index")
    if index is None:
        return
    count = config.getoption("ci_shard_count")
    nodeids = sorted(item.nodeid for item in items)
    if len(nodeids) != len(set(nodeids)):
        raise pytest.UsageError("CI collection contains duplicate node IDs")
    table = config.getoption("ci_durations")
    if not table.is_absolute():
        table = config.rootpath / table
    try:
        assignment = assign_files([nodeid.split("::", 1)[0] for nodeid in nodeids], load_weights(table), count)
    except (OSError, ValueError) as exc:
        raise pytest.UsageError(f"Cannot load CI durations: {exc}") from exc
    selected, deselected = [], []
    for item in items:
        (selected if assignment[item.nodeid.split("::", 1)[0]] == index else deselected).append(item)
    manifest = config.getoption("ci_shard_manifest")
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({
        "schema_version": 1,
        "python_version": ".".join(platform.python_version_tuple()[:2]),
        "shard_index": index,
        "shard_count": count,
        "collection_count": len(nodeids),
        "collection_sha256": collection_digest(nodeids),
        "selected_nodeids": sorted(item.nodeid for item in selected),
    }, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    items[:] = selected
    config.hook.pytest_deselected(items=deselected)
