"""Select PR CI coverage from changed paths on stdin; uncertainty keeps full coverage."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path, PurePosixPath

FULL_PYTHONS = ["3.10", "3.11", "3.12", "3.13"]
PYTHON_CONFIG = {
    "pyproject.toml", "uv.lock", "uv.toml", ".python-version",
    "pytest.ini", "tox.ini", "setup.cfg", ".coveragerc",
}
NATIVE_SAFE_DOCS = {"AGENTS.md", "CHANGELOG.md", "CONTRIBUTING.md", "README.zh-TW.md", "desktop/DESIGN.md"}


def affects_python(path: str) -> bool:
    name = PurePosixPath(path).name
    return (
        name.endswith((".py", ".pyi"))
        or name in PYTHON_CONFIG
        or (name.startswith("requirements") and name.endswith(".txt"))
        # Include fixture data, shard weights/plugins and scripts that invoke Python.
        or path.startswith(("tests/", "scripts/", "desktop/scripts/", ".github/workflows/"))
    )


def native_safe(path: str) -> bool:
    # Tauri rebuilds desktop/dist from shared source; browser/Node tests run separately.
    # Do not allow all desktop/tests: build uses .mjs/.py/PS fixtures and their data.
    # The managed runtime collects dashboard assets; notices are Tauri resources.
    return (
        path.startswith(("desktop/src/", "docs/"))
        or (path.startswith("desktop/tests/") and path.endswith((".spec.ts", ".test.ts")))
        or path in NATIVE_SAFE_DOCS
    )


def select_scope(
    paths: list[str], *, event_name: str = "pull_request", diff_failed: bool = False,
) -> tuple[list[str], bool, str]:
    if event_name != "pull_request":
        return FULL_PYTHONS, True, "Non-PR event: full coverage"
    if diff_failed:
        return FULL_PYTHONS, True, "PR diff unavailable: full coverage"
    if not paths:
        return FULL_PYTHONS, True, "Empty path list: full coverage"
    # Git quotes names containing tabs/newlines. Never narrow an ambiguous path list.
    if any(path.startswith(('"', "/")) or "\\" in path
           or any(part in {"", ".", ".."} for part in path.split("/")) for path in paths):
        return FULL_PYTHONS, True, "Ambiguous path list: full coverage"
    pythons = FULL_PYTHONS if any(affects_python(path) for path in paths) else ["3.10"]
    native = not all(native_safe(path) for path in paths)
    python_reason = "Python source/tooling changed" if pythons == FULL_PYTHONS else "No Python source/tooling changed"
    native_reason = "path outside native allowlist" if native else "all paths on native allowlist"
    return pythons, native, f"{python_reason}; {native_reason}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-name", default=os.environ.get("GITHUB_EVENT_NAME", "pull_request"))
    parser.add_argument("--diff-failed", action="store_true")
    args = parser.parse_args()
    paths = [line for line in sys.stdin.read().splitlines() if line]
    pythons, native, reason = select_scope(paths, event_name=args.event_name, diff_failed=args.diff_failed)
    print(f"pythons={json.dumps(pythons)}")
    print(f"native={str(native).lower()}")
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        versions = ", ".join(pythons)
        builds = "all four native builds" if native else "native builds skipped"
        with Path(summary).open("a", encoding="utf-8") as stream:
            stream.write(f"pytest on {versions} (5 shards each); {builds}. {reason}.\n")


if __name__ == "__main__":
    main()
