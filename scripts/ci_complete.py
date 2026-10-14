"""Verify downloaded pytest manifests prove an exact partition for every Python version."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.ci_sharding import collection_digest


def verify_manifests(manifests: list[dict], versions: list[str], shard_count: int) -> list[str]:
    if not versions or len(versions) != len(set(versions)) or shard_count < 1:
        raise ValueError("expected unique Python versions and a positive shard count")
    grouped: dict[str, dict[int, dict]] = {version: {} for version in versions}
    for manifest in manifests:
        version = manifest.get("python_version")
        index = manifest.get("shard_index")
        if manifest.get("schema_version") != 1 or version not in grouped:
            raise ValueError("unexpected manifest schema or Python version")
        if type(index) is not int or not 1 <= index <= shard_count:
            raise ValueError(f"{version}: invalid shard index")
        if type(manifest.get("shard_count")) is not int or manifest["shard_count"] != shard_count:
            raise ValueError(f"{version}: mismatched shard count")
        if index in grouped[version]:
            raise ValueError(f"{version}: doubled manifest for shard {index}")
        grouped[version][index] = manifest
    summaries = []
    for version, shards in grouped.items():
        if set(shards) != set(range(1, shard_count + 1)):
            raise ValueError(f"{version}: missing shards")
        first = shards[1]
        total, digest = first.get("collection_count"), first.get("collection_sha256")
        if type(total) is not int or total < 1 or not isinstance(digest, str):
            raise ValueError(f"{version}: invalid full collection")
        union: set[str] = set()
        files: dict[str, int] = {}
        for index, manifest in shards.items():
            if manifest.get("collection_count") != total or manifest.get("collection_sha256") != digest:
                raise ValueError(f"{version}: mismatched full collection")
            selected = manifest.get("selected_nodeids")
            if not isinstance(selected, list) or not all(isinstance(nodeid, str) and nodeid for nodeid in selected):
                raise ValueError(f"{version}: invalid selected node IDs")
            if len(selected) != len(set(selected)) or union.intersection(selected):
                raise ValueError(f"{version}: doubled test")
            for nodeid in selected:
                path = nodeid.split("::", 1)[0]
                if files.setdefault(path, index) != index:
                    raise ValueError(f"{version}: test file split between shards")
            union.update(selected)
        if len(union) != total or collection_digest(list(union)) != digest:
            raise ValueError(f"{version}: union does not equal full collection")
        summaries.append(f"Python {version}: {total} tests, {len(files)} files, {shard_count} disjoint shards; SHA-256 {digest}")
    return summaries


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", type=Path)
    parser.add_argument("--python", dest="versions", nargs="+", required=True)
    parser.add_argument("--shards", type=int, required=True)
    parser.add_argument("--shards-result", choices=["success", "failure", "cancelled", "skipped"], default="success")
    args = parser.parse_args()
    try:
        manifests = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(args.artifacts.rglob("manifest.json"))]
        summaries = verify_manifests(manifests, args.versions, args.shards)
        for summary in summaries:
            print(summary)
        if args.shards_result != "success":
            raise ValueError(f"pytest matrix result: {args.shards_result}; every shard must succeed")
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        print(f"Pytest completeness failed: {exc}")
        return 1
    print("Pytest completeness passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
