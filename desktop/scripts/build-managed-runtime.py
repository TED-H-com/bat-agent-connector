"""Build a platform-native, self-contained central runtime from the locked environment.

Run with uv run --locked --extra desktop-build. No Python download or environment
creation occurs on the user's computer. The gzip wrapper prevents outer macOS app
signing from changing the bytes whose SHA-256 is checked by the native launcher.
"""

from __future__ import annotations

import gzip
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "desktop" / "managed-runtime"
BUILD = ROOT / "build" / "managed-runtime"


def main() -> None:
    if importlib.metadata.version("pyinstaller") != "6.22.3":
        raise SystemExit("Use the locked desktop-build extra (PyInstaller 6.22.3)")
    executable = "batc-managed-runtime" + (".exe" if sys.platform == "win32" else "")
    BUILD.mkdir(parents=True, exist_ok=True)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    commit = os.environ.get("BATC_BUILD_COMMIT")
    if commit and not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit):
        raise SystemExit("BATC_BUILD_COMMIT must be a full commit hash")
    build_info = BUILD / "build-info.json"
    build_info.write_text(json.dumps({"commit": commit or None}) + "\n", encoding="utf-8")
    subprocess.run(  # noqa: S603 - fixed locked build tool and repository-owned entry point
        [
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
            "--noupx", "--name", "batc-managed-runtime", "--distpath", str(BUILD / "dist"),
            "--workpath", str(BUILD / "work"), "--specpath", str(BUILD),
            "--paths", str(ROOT / "src"),
            "--collect-all", "bat_agent_connector",
            # Windows has no system IANA database; policy schedules use the same zones.
            "--collect-data", "tzdata",
            "--add-data", f"{build_info}:bat_agent_connector",
            # Host helpers are transmitted as source, not imported on the central host.
            "--add-data", f"{ROOT / 'src' / 'bat_agent_connector'}:bat_agent_connector",
            str(ROOT / "desktop" / "scripts" / "managed-runtime-entry.py"),
        ],
        cwd=ROOT,
        check=True,
    )
    source = BUILD / "dist" / executable
    payload = OUTPUT / "runtime.gz"
    with source.open("rb") as reader, payload.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as writer:
            shutil.copyfileobj(reader, writer)
    architecture = {"AMD64": "x86_64", "arm64": "aarch64"}.get(platform.machine(), platform.machine())
    def digest(path: Path) -> str:
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

    document = {
        "protocol": 1,
        "runtime_version": importlib.metadata.version("bat-agent-connector"),
        "platform": {"darwin": "macos", "win32": "windows"}.get(sys.platform, sys.platform),
        "architecture": architecture,
        "executable": executable,
        "payload_sha256": digest(payload),
        "sha256": digest(source),
        "size": source.stat().st_size,
    }
    (OUTPUT / "manifest.json").write_text(json.dumps(document, indent=2) + "\n")
    print(f"Bundled central {document['runtime_version']} ({document['platform']}/{architecture})")


if __name__ == "__main__":
    main()
