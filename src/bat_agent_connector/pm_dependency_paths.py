"""Fixed dependency artifact allowlist and shared host/central bounds."""
from __future__ import annotations

import re

MAX_FILES = 128
MAX_FILE_BYTES = 512_000
MAX_TOTAL_BYTES = 2_000_000
MAX_WIRE_BYTES = 6_000_000
MAX_DEPTH = 8
MAX_TREES = 200
MAX_ENTRIES = 10_000
MAX_EDGES = 2000
SKIP_DIRS = frozenset({"node_modules", ".venv", "venv", "target", "vendor", "vendored", ".git", "dist", "build"})
NAMES = frozenset({"pyproject.toml", "uv.lock", "package.json", "package-lock.json", "Cargo.toml", "Cargo.lock",
                   "rust-toolchain", "rust-toolchain.toml", "go.mod", "go.sum", ".nvmrc", ".python-version", ".tool-versions"})


def safe_path(path):
    return (isinstance(path, str) and 0 < len(path) <= 800 and "\\" not in path
            and not any(ord(c) < 32 or ord(c) == 127 for c in path)
            and all(p not in {"", ".", ".."} for p in path.split("/")))


def allowed_path(path):
    if not safe_path(path):
        return False
    parts = path.split("/")
    if len(parts) > MAX_DEPTH + 1 or any(p in SKIP_DIRS for p in parts[:-1]):
        return False
    return (parts[-1] in NAMES or bool(re.fullmatch(r"requirements(?:[A-Za-z0-9_.-]*)\.txt", parts[-1]))
            or len(parts) == 3 and parts[:2] == [".github", "workflows"] and bool(re.fullmatch(r"[A-Za-z0-9_.-]+\.ya?ml", parts[-1])))
