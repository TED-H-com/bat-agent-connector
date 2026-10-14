"""Installed build provenance, without consulting a checkout or invoking Git."""
from __future__ import annotations

import json
import re
import sys
from importlib import metadata
from pathlib import Path

from . import __version__


def _commit(value):
    return value if isinstance(value, str) and re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value) else None


def build_identity() -> dict:
    result = {"version": __version__, "commit": None, "source": "package"}
    try:
        if getattr(sys, "frozen", False):
            result["source"] = "bundled"
            doc = json.loads(Path(__file__).with_name("build-info.json").read_text(encoding="utf-8"))
            result["commit"] = _commit(doc.get("commit"))
        else:
            distribution = metadata.distribution("bat-agent-connector")
            if Path(distribution.locate_file("bat_agent_connector")).resolve() != Path(__file__).resolve().parent:
                return result  # A different installed copy cannot identify the running checkout.
            raw = distribution.read_text("direct_url.json")
            doc = json.loads(raw) if raw else {}
            vcs = doc.get("vcs_info", {})
            if vcs.get("vcs") == "git":
                result.update(source="vcs", commit=_commit(vcs.get("commit_id")))
    except (OSError, ValueError, TypeError, AttributeError, metadata.PackageNotFoundError):
        pass  # Missing/malformed provenance is unknown, never a guessed checkout HEAD.
    return result
