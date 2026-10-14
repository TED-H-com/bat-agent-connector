"""Select PR CI coverage from changed paths on stdin; uncertainty keeps full coverage."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path, PurePosixPath

FULL_PYTHONS = ["3.10", "3.11", "3.12", "3.13"]
# The build matrix has only an include list: one job per selected entry, no axes.
NATIVE_BUILDS = [
    {"os": "ubuntu-24.04", "bundle": "deb", "artifact": "desktop-Linux-unsigned"},
    {"os": "windows-latest", "bundle": "nsis", "artifact": "desktop-Windows-unsigned"},
    {"os": "macos-15", "bundle": "app,dmg", "artifact": "desktop-macOS-arm64-validation"},
    {"os": "macos-15-intel", "bundle": "app,dmg", "artifact": "desktop-macOS-x64-validation"},
]
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
    # Dashboard assets use the separate Linux level; notices are Tauri resources.
    return (
        path.startswith(("desktop/src/", "docs/"))
        or (path.startswith("desktop/tests/") and path.endswith((".spec.ts", ".test.ts")))
        or path in NATIVE_SAFE_DOCS
    )


def select_scope(
    paths: list[str], *, event_name: str = "pull_request", diff_failed: bool = False,
) -> tuple[list[str], str, str]:
    if event_name != "pull_request":
        return FULL_PYTHONS, "all", "Non-PR event: full coverage"
    if diff_failed:
        return FULL_PYTHONS, "all", "PR diff unavailable: full coverage"
    if not paths:
        return FULL_PYTHONS, "all", "Empty path list: full coverage"
    # Git quotes names containing tabs/newlines. Never narrow an ambiguous path list.
    if any(path.startswith(('"', "/")) or "\\" in path
           or any(part in {"", ".", ".."} for part in path.split("/")) for path in paths):
        return FULL_PYTHONS, "all", "Ambiguous path list: full coverage"
    pythons = FULL_PYTHONS if any(affects_python(path) for path in paths) else ["3.10"]
    if all(native_safe(path) for path in paths):
        native = "none"
    elif all(native_safe(path) or path.startswith("src/bat_agent_connector/dashboard/") for path in paths):
        native = "linux"
    else:
        native = "all"
    return pythons, native, ""


def native_builds(level: str) -> list[dict[str, str]]:
    if level == "none":
        return []
    if level == "linux":
        return [entry for entry in NATIVE_BUILDS if entry["os"] == "ubuntu-24.04"]
    if level == "all":
        return NATIVE_BUILDS
    raise ValueError(f"Unknown native level: {level}")


def summary_line(pythons: list[str], native: str, reason: str, workflow: str) -> str:
    if workflow == "ci":
        reason = reason or ("Python source/tooling changed" if pythons == FULL_PYTHONS
                            else "No Python source/tooling changed")
        return f"pytest on {', '.join(pythons)} (5 shards each). {reason}.\n"
    selection, native_reason = {
        "none": ("Native builds skipped", "all paths on native allowlist"),
        "linux": ("Linux native build only", "generated dashboard assets need packaged Linux fixtures"),
        "all": ("All four native builds", "path outside native allowlist and generated dashboard assets"),
    }[native]
    return f"{selection}. {reason or native_reason}.\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-name", default=os.environ.get("GITHUB_EVENT_NAME", "pull_request"))
    parser.add_argument("--diff-failed", action="store_true")
    parser.add_argument("--workflow", choices=["ci", "desktop"], default="ci")
    args = parser.parse_args()
    paths = [line for line in sys.stdin.read().splitlines() if line]
    pythons, native, reason = select_scope(paths, event_name=args.event_name, diff_failed=args.diff_failed)
    print(f"pythons={json.dumps(pythons)}")
    print(f"native={native}")
    print(f"builds={json.dumps(native_builds(native))}")
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary).open("a", encoding="utf-8") as stream:
            stream.write(summary_line(pythons, native, reason, args.workflow))


if __name__ == "__main__":
    main()
