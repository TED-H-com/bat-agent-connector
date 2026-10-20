"""Embed source attribution at build time; never ask Git at runtime."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


def _commit(value):
    return value.lower() if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{40}", value) else None


def _git(root, *args):
    git = shutil.which("git")
    if git is None:
        raise FileNotFoundError("Git unavailable")
    return subprocess.run(  # noqa: S603 -- fixed Git subcommands, no shell
        [git, "-C", str(root), *args], check=True, capture_output=True, text=True,
    ).stdout.strip()


def _build_commit(root):
    override = _commit(os.environ.get("BATC_BUILD_COMMIT"))
    if (root / "PKG-INFO").is_file():
        # An sdist belongs to its original candidate, even when extracted inside
        # another checkout. Neither that checkout nor an override can relabel it.
        try:
            doc = json.loads((root / "src/bat_agent_connector/build-info.json").read_text(encoding="utf-8"))
            original = _commit(doc.get("commit"))
        except (OSError, ValueError, TypeError, AttributeError):
            return None
        return original if override is None or override == original else None

    try:
        checkout = Path(_git(root, "rev-parse", "--show-toplevel")).resolve() == root
        if checkout:
            # A candidate SHA must never label modified tracked inputs, including
            # when supplied explicitly by the packaging environment.
            if _git(root, "status", "--porcelain", "--untracked-files=no"):
                return None
            return override or _commit(_git(root, "rev-parse", "HEAD"))
    except (OSError, subprocess.CalledProcessError):
        if (root / ".git").exists():
            return None  # A checkout whose cleanliness cannot be proved.
    return override


class BuildProvenanceHook(BuildHookInterface):
    def initialize(self, version, build_data):
        root = Path(self.root).resolve()
        # Stage outside the source tree so building cannot dirty the checkout or
        # leave provenance behind for a later, different candidate.
        self._staging = TemporaryDirectory(prefix=".batc-provenance-", dir=self.directory)
        info = Path(self._staging.name) / "build-info.json"
        info.write_text(json.dumps({"commit": _build_commit(root)}) + "\n", encoding="utf-8")
        package = "src/bat_agent_connector" if self.target_name == "sdist" else "bat_agent_connector"
        build_data["force_include"][str(info)] = f"{package}/build-info.json"
        if self.target_name == "sdist":
            build_data["force_include"][str(root / "hatch_build.py")] = "hatch_build.py"

    def finalize(self, version, build_data, artifact_path):
        self._staging.cleanup()
