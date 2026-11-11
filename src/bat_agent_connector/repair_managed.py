"""Explicit recovery of an enrolled Connector using the updater transaction."""
import json
import os
import re
import secrets
import stat
from pathlib import Path

from .update_release import REPOSITORY, UpdateError, commit, require
from .updater import Updater


def verify_repair(home):
    """Bounded read-only validation; bootstrap and installed counterpart stay identical."""
    state_path = home / ".local/share/bat-agent-connector/update/state.json"
    try:
        descriptor = os.open(state_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                    and not info.st_mode & 0o077 and info.st_nlink == 1
                    and info.st_size <= 1024 * 1024, "repair_state_invalid")
            raw = stream.read(1024 * 1024 + 1)
        require(len(raw) <= 1024 * 1024, "repair_state_invalid")
        state = json.loads(raw)
        require(type(state["format"]) is int and state["format"] == 1
                and type(state["epoch"]) is int and state["epoch"] == 1
                and state["channel"] in ("stable", "preview") and type(state["enabled"]) is bool
                and isinstance(state["versions"], dict) and state["versions"], "repair_state_invalid")
        for sha, record in state["versions"].items():
            require(re.fullmatch(r"[0-9a-f]{40}", sha) and isinstance(record, dict)
                    and Path(record["root"]).is_absolute()
                    and record["release"]["commit"] == sha
                    and isinstance(record["release"]["version"], str)
                    and re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", record["release"]["version"])
                    and record["release"]["epoch"] == 1
                    and isinstance(record["release"]["tag"], str)
                    and re.fullmatch(r"v[0-9][A-Za-z0-9.+-]{0,95}", record["release"]["tag"])
                    and type(record["release"]["prerelease"]) is bool
                    and isinstance(record["release"]["skill_sha256"], str)
                    and re.fullmatch(r"[0-9a-f]{64}", record["release"]["skill_sha256"])
                    and isinstance(record["proof"], dict)
                    and Path(record["proof"]["package"]).is_relative_to(
                        Path(record["root"]) / "tools/bat-agent-connector/lib")
                    and all(isinstance(record["proof"][key], str)
                            and re.fullmatch(r"[0-9a-f]{64}", record["proof"][key])
                            for key in ("files", "python_files", "skill_sha256")), "repair_state_invalid")
        require(state["current"] in state["versions"]
                and (state["previous"] is None or state["previous"] in state["versions"]), "repair_state_invalid")
        # An interrupted transaction must be recovered by its original updater first.
        require(not state.get("pending") and not state.get("enrollment"), "repair_state_invalid")
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise UpdateError("repair_state_invalid") from None
    try:
        record = state["versions"][state["current"]]
        tool = Path(record["root"]) / "tools/bat-agent-connector"
        require(tool.resolve() == tool.absolute() and (tool / "uv-receipt.toml").is_file(),
                "repair_install_not_managed")
        package = Path(record["proof"]["package"])
        require(package.resolve() == package.absolute(), "repair_install_not_managed")
        distributions = list(package.parent.glob("bat_agent_connector-*.dist-info"))
        require(len(distributions) == 1, "repair_install_not_managed")
        metadata = distributions[0] / "direct_url.json"
        require(metadata.resolve() == metadata.absolute() and metadata.stat().st_size <= 1024 * 1024,
                "repair_install_not_managed")
        doc = json.loads(metadata.read_bytes())
        # Updater-staged installs record the private scratch clone that stage()
        # verified against the published tag, not the remote repository URL.
        scratch = re.escape((Path(record["root"]) / "scratch").as_uri())
        require(isinstance(doc.get("url"), str)
                and (doc["url"] in (REPOSITORY, REPOSITORY + ".git")
                     or re.fullmatch(scratch + r"/source-[A-Za-z0-9_]+/repository", doc["url"]))
                and doc["vcs_info"]["vcs"] == "git"
                and isinstance(doc["vcs_info"]["commit_id"], str)
                and re.fullmatch(r"[0-9a-f]{40}", doc["vcs_info"]["commit_id"])
                and doc["vcs_info"]["commit_id"] == state["current"], "repair_install_not_managed")
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise UpdateError("repair_install_not_managed") from None
    return state


def repair(release, *, home=None, updater=None, dry_run=False):
    home = Path.home() if home is None else Path(home)
    engine = Updater(home) if updater is None else updater
    state = verify_repair(home)
    if dry_run:
        return {"status": "verified_repair", "release": release, "writes": False}
    with engine.locked():
        state = verify_repair(home)
        engine.reconcile(state)
        engine.verify(state["versions"][state["current"]])
        require(release["commit"] != state["current"], "repair_release_already_current")
        root = engine.root / "versions" / commit(release["commit"])
        proof = engine.install(root, release, home=home)
        state["versions"][release["commit"]] = {"root": str(root), "release": release, "proof": proof}
        state["pending"] = {"id": secrets.token_hex(16), "from": state["current"], "to": release["commit"],
                            "status": "installed_for_next_launch"}
        engine.save(state)
        engine.reconcile(state)
        return {"status": "repaired", **engine.status(state)}
