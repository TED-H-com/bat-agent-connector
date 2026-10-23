"""Opt-in Linux Connector updater. Changes future launches, never running work."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import stat
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import platform_files as files
from .update_install import clean_env, digest_files, installed, package_files, run, stage
from .update_release import (
    EPOCH,
    REPOSITORY,
    EntrypointDirectoryError,
    UpdateError,
    commit,
    discover,
    require,
    version,
)

COMMANDS = ("batc", "bat-agent-connector-mcp", "batc-update")
SERVICE = """[Unit]
Description=Check and install published BAT Agent Connector updates

[Service]
Type=oneshot
ExecStart="%h/.local/bin/batc-update" run
UMask=0077
NoNewPrivileges=true
TimeoutStartSec=15min
"""
TIMER = """[Unit]
Description=Check BAT Agent Connector releases hourly

[Timer]
OnCalendar=hourly
RandomizedDelaySec=15min
Persistent=true
Unit=batc-update.service

[Install]
WantedBy=timers.target
"""


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def snapshot(path, *, links_only=False):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    require(info.st_uid == os.getuid(), "entrypoint_owner_mismatch")
    if stat.S_ISLNK(info.st_mode):
        return {"link": os.readlink(path)}
    if links_only:
        return {}  # Status only compares link targets; never read an entrypoint's contents.
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and not info.st_mode & 0o022,
            "entrypoint_not_owned")
    require(info.st_size <= 1024 * 1024, "entrypoint_size_limit")
    return {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def replace_link(path, target, expected):
    # Pin the parent directory; do not follow a substituted parent during rename.
    parent = files._posix_dir(path.parent)
    temporary = ".batc-update-" + secrets.token_hex(12)
    try:
        info = os.fstat(parent)
        if info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise EntrypointDirectoryError(path.parent)
        require(snapshot(path) == expected, "entrypoint_changed")
        os.symlink(str(target), temporary, dir_fd=parent)
        os.replace(temporary, path.name, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
    finally:
        try:
            os.unlink(temporary, dir_fd=parent)
        except FileNotFoundError:
            pass
        os.close(parent)


def write_unit(path, text):
    parent = files._posix_dir(path.parent)
    temporary = ".batc-update-" + secrets.token_hex(12)
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        with os.fdopen(descriptor, "wb") as output:
            output.write(text.encode())
            output.flush()
            os.fsync(output.fileno())
        require(snapshot(path) is None, "scheduler_unit_appeared")
        os.replace(temporary, path.name, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
    finally:
        try:
            os.unlink(temporary, dir_fd=parent)
        except FileNotFoundError:
            pass
        os.close(parent)


class Updater:
    def __init__(self, home=None, *, find_release=discover, install=stage, inspect=installed, command=run):
        require(sys.platform.startswith("linux"), "updater_requires_linux")
        self.home = Path.home() if home is None else Path(home)
        self.root = self.home / ".local/share/bat-agent-connector/update"
        self.find_release = find_release
        self.install = install
        self.inspect = inspect
        self.command = command

    @contextmanager
    def locked(self):
        files.ensure_private_directory(self.root)
        descriptor = files.open_private_file(self.root / "lease", os.O_RDWR | os.O_CREAT)
        try:
            try:
                files.lock(descriptor, blocking=False)
            except BlockingIOError as exc:
                raise UpdateError("update_already_running") from exc
            yield
        finally:
            files.unlock(descriptor)
            os.close(descriptor)

    def read(self):
        state = json.loads(files.read_private(self.root / "state.json"))
        require(state.get("format") == 1 and state.get("epoch") == EPOCH, "update_state_incompatible")
        return state

    def save(self, state):
        path = self.root / "state.json"
        encoded = (json.dumps(state, sort_keys=True, indent=2) + "\n").encode()
        try:
            current = files.read_private(path)
        except FileNotFoundError:
            current = None
        # Preserve state.json when its serialized bytes are already persisted.
        if current != encoded:
            files.atomic_write(path, encoded)

    def outcome(self, pending):
        path = self.root / "receipts" / (pending["id"] + ".json")
        expected = {"from": pending["from"], "to": pending["to"], "status": pending["status"]}
        if path.exists():
            receipt = json.loads(files.read_private(path))
            require(all(receipt.get(key) == value for key, value in expected.items()), "update_receipt_changed")
            return receipt["last"]
        last = {"status": pending["status"], "at": timestamp(), "commit": pending["to"], "receipt": pending["id"]}
        files.atomic_write(path, json.dumps({**expected, "last": last}, sort_keys=True).encode())
        return last

    def failure(self, state, exc):
        code = str(exc) if isinstance(exc, UpdateError) else "update_state_or_environment_invalid"
        receipt_id = "failure-" + secrets.token_hex(16)
        last = {"status": "failed", "code": code, "at": timestamp(), "receipt": receipt_id}
        files.atomic_write(self.root / "receipts" / (receipt_id + ".json"),
                           json.dumps({**last, "current": state["current"], "pending": state.get("pending")},
                                      sort_keys=True).encode())
        state["last"] = last
        self.save(state)

    def links(self):
        current = self.root / "current"
        result = {self.home / ".local/bin" / name: current / "bin" / name for name in COMMANDS}
        result[self.home / ".agents/skills/bat-agent-connector/SKILL.md"] = current / "skill/SKILL.md"
        return result

    def verify(self, record):
        root = Path(record["root"])
        files.ensure_private_directory(root)
        package = Path(record["proof"]["package"])
        require(package.is_relative_to(root / "tools/bat-agent-connector/lib"), "installed_package_location_changed")
        require(digest_files(package_files(package)) == record["proof"]["files"], "verified_package_changed")
        require(self.inspect(root, record["release"], home=self.home) == record["proof"], "verified_package_changed")
        require(hashlib.sha256(files.read_private(root / "skill/SKILL.md")).hexdigest()
                == record["release"]["skill_sha256"], "activated_skill_changed")

    def reconcile(self, state):
        enrollment = state.get("enrollment")
        if enrollment:
            current = state["versions"][state["current"]]
            self.verify(current)
            pointer = self.root / "current"
            observed = snapshot(pointer)
            desired = {"link": current["root"]}
            require(observed in (None, desired), "update_pointer_changed")
            if observed is None:
                replace_link(pointer, current["root"], None)
            # Validate every remaining entry before any additional replacement.
            for path, target in self.links().items():
                require(snapshot(path) in (enrollment[str(path)], {"link": str(target)}), "enrollment_target_changed")
            for path, target in self.links().items():
                if snapshot(path) != {"link": str(target)}:
                    replace_link(path, target, enrollment[str(path)])
            state.pop("enrollment")
            state["last"] = {"status": "enrolled_for_next_launch", "at": timestamp()}
            state["installed_at"] = state["last"]["at"]
            self.save(state)
        pending = state.get("pending")
        if pending:
            before = state["versions"][pending["from"]]
            after = state["versions"][pending["to"]]
            self.verify(after)
            self.check_links()
            pointer = self.root / "current"
            observed = snapshot(pointer)
            require(observed in ({"link": before["root"]}, {"link": after["root"]}), "update_pointer_changed")
            if observed == {"link": before["root"]}:
                replace_link(pointer, after["root"], observed)
            state["previous"] = pending["from"]
            state["current"] = pending["to"]
            state["last"] = self.outcome(pending)
            state["installed_at"] = state["last"]["at"]
            state.pop("pending")
            self.save(state)
        self.check_links()
        require(snapshot(self.root / "current") == {"link": state["versions"][state["current"]]["root"]},
                "update_pointer_changed")

    def check_links(self):
        for path, target in self.links().items():
            require(snapshot(path) == {"link": str(target)}, "managed_entrypoint_changed")

    def enroll(self, receipt, channel):
        require(channel in {"stable", "preview"}, "invalid_channel")
        with self.locked():
            require(not (self.root / "state.json").exists(), "already_enrolled")
            receipt = Path(receipt).absolute()
            require(receipt.name == "rollout-receipt.json", "deployment_receipt_required")
            doc = json.loads(files.read_private(receipt))
            old = doc["release"]
            require(old["repository"] == REPOSITORY + ".git", "deployment_repository_mismatch")
            sha = commit(old["commit"])
            ver = old["package_version"]
            require(version(ver) >= (0, 2, 7), "bootstrap_updater_release_required")
            require(doc["build_identity"] == {"version": ver, "commit": sha, "source": "vcs"},
                    "deployment_identity_mismatch")
            release = self.find_release(channel)
            require(release is not None and release["commit"] == sha and release["version"] == ver
                    and release["skill_sha256"] == old["canonical_sha256"], "bootstrap_release_not_on_channel")
            root = receipt.parent
            package = Path(doc["installed_proof"]["package_path"])
            require(package.is_relative_to(root / "tools/bat-agent-connector/lib"), "deployment_package_location_mismatch")
            require(package.resolve() == package.absolute(), "deployment_package_linked")
            legacy = hashlib.sha256()
            for name, value in sorted(package_files(package, python_only=True).items()):
                legacy.update(name.encode() + b"\0" + bytes.fromhex(value))
            require(legacy.hexdigest() == doc["installed_proof"]["python_source_sha256"], "deployment_source_changed")
            proof = self.inspect(root, release, home=self.home)
            require(proof["skill_sha256"] == doc["installed_proof"]["packaged_workflow_sha256"],
                    "deployment_skill_mismatch")
            originals = {}
            for path in self.links():
                original = snapshot(path)
                if path.name == "SKILL.md":
                    require(original == {"sha256": release["skill_sha256"]}, "canonical_skill_changed")
                    files.atomic_write(self.root / "pre-enrollment-SKILL.md", path.read_bytes())
                else:
                    require(original == {"link": str(root / "bin" / path.name)}
                            or (path.name == "batc-update" and original is None), "deployment_entrypoint_changed")
                    if path.name != "batc-update":
                        require(doc["entrypoints"].get(path.name) == str(path.resolve()), "deployment_entrypoint_mismatch")
                originals[str(path)] = original
            staged_skill = root / "skill/SKILL.md"
            canonical = (Path(proof["package"]) / "skill/SKILL.md").read_bytes()
            if staged_skill.exists() or staged_skill.is_symlink():
                require(files.read_private(staged_skill) == canonical, "deployment_staged_skill_changed")
            else:
                files.atomic_write(staged_skill, canonical)
            state = {"format": 1, "epoch": EPOCH, "channel": channel, "enabled": True, "current": sha,
                     "previous": None, "versions": {sha: {"root": str(root), "release": release, "proof": proof}},
                     "enrollment": originals, "created_at": timestamp()}
            self.save(state)
            self.reconcile(state)
            return self.status(state)

    def _status_snapshot(self, state=None):
        """One bounded private read, with no lease, reconciliation or writes."""
        state = self.read() if state is None else state
        current = state["versions"][state["current"]]
        matches = (snapshot(self.root / "current", links_only=True) == {"link": current["root"]}
                   and all(snapshot(path, links_only=True) == {"link": str(target)}
                           for path, target in self.links().items()))
        result = {"enabled": state["enabled"], "channel": state["channel"],
                  "installed": current["release"] if matches else None, "installation_matches_state": matches,
                  "previous": state["previous"], "last": state.get("last"),
                  "installed_at": state.get("installed_at"),
                  "release_check": (state.get("release_check")
                                    if state.get("release_check", {}).get("channel") == state["channel"] else None),
                  "pending": bool(state.get("pending") or state.get("enrollment")),
                  "activation": "next_launch", "processes_restarted": False,
                  "scheduler_configured": bool(state.get("schedule_complete"))}
        return state, current, result

    def status(self, state=None):
        return self._status_snapshot(state)[2]

    def _check_release(self, state):
        # Persist completion before installation; status/capabilities never
        # renew this finder observation.
        observation = {}
        candidate = self.find_release(state["channel"], observation=observation)
        # Preserve the shared install candidate. The selected release item's
        # display clock belongs only to this completed check's observation.
        recorded = ({**candidate, "published_at": observation["published_at"]}
                    if candidate is not None and observation.get("published_at") is not None else candidate)
        state["release_check"] = {"channel": state["channel"], "completed_at": timestamp(),
                                  "candidate": recorded}
        self.save(state)
        return candidate

    def check(self):
        with self.locked():
            state = self.read()
            candidate = self._check_release(state)
            return {"available": candidate, **self.status(state)}

    def apply(self):
        with self.locked():
            state = self.read()
            if not state["enabled"]:
                return self.status(state)
            try:
                self.reconcile(state)
                self.verify(state["versions"][state["current"]])
                candidate = self._check_release(state)
                current = state["versions"][state["current"]]["release"]
                if not candidate or version(candidate["version"]) < version(current["version"]):
                    state["last"] = {"status": "no_newer_release", "at": timestamp()}
                elif version(candidate["version"]) == version(current["version"]):
                    require(candidate["commit"] == current["commit"], "release_version_republished")
                    state["last"] = {"status": "up_to_date", "at": timestamp()}
                else:
                    root = self.root / "versions" / commit(candidate["commit"])
                    proof = self.install(root, candidate, home=self.home)
                    state["versions"][candidate["commit"]] = {"root": str(root), "release": candidate, "proof": proof}
                    state["pending"] = {"id": secrets.token_hex(16), "from": state["current"], "to": candidate["commit"],
                                        "status": "installed_for_next_launch"}
                    self.save(state)
                    self.reconcile(state)
                    return self.status(state)
                # Only release checks reach this save; install reconciliation already saved its result.
                self.save(state)
                return self.status(state)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self.failure(state, exc)
                raise

    def policy(self, enabled, channel=None):
        with self.locked():
            state = self.read()
            if enabled:
                self.reconcile(state)
            state["enabled"] = enabled
            if channel:
                require(channel in {"stable", "preview"}, "invalid_channel")
                state["channel"] = channel
            self.save(state)
            return self.status(state)

    def rollback(self):
        with self.locked():
            state = self.read()
            self.reconcile(state)
            require(state["previous"] is not None, "no_managed_rollback")
            self.verify(state["versions"][state["previous"]])
            state["enabled"] = False
            state["pending"] = {"id": secrets.token_hex(16), "from": state["current"], "to": state["previous"],
                                "status": "rolled_back_updates_disabled"}
            self.save(state)
            self.reconcile(state)
            return self.status(state)

    def rollback_preview(self, *, journal=None):
        from .config import default_journal_path
        from .rollback_compatibility import preview
        # No updater lease/reconciliation: even creating a lease is a mutation.
        # Do not resolve symlinks: the snapshot reader must still refuse them.
        path = (Path(journal) if journal is not None else default_journal_path()).absolute()
        result = preview(self.read(), journal=path, observed_at=timestamp())
        result["journal"] = {"path": str(path), "source": "explicit" if journal is not None else "default"}
        return result

    def schedule(self):
        with self.locked():
            state = self.read()
            self.reconcile(state)
            directory = self.home / ".config/systemd/user"
            # Do not chmod or take over a pre-existing scheduler directory.
            parent = files._posix_dir(directory, create=True)
            try:
                info = os.fstat(parent)
                require(info.st_uid == os.getuid() and not info.st_mode & 0o022, "scheduler_directory_not_owned")
            finally:
                os.close(parent)
            units = {"batc-update.service": SERVICE, "batc-update.timer": TIMER}
            if not state.get("schedule_intent"):
                require(all(snapshot(directory / name) is None for name in units), "scheduler_unit_not_owned")
                state["schedule_intent"] = {name: hashlib.sha256(text.encode()).hexdigest() for name, text in units.items()}
                self.save(state)
            for name, text in units.items():
                require(state["schedule_intent"][name] == hashlib.sha256(text.encode()).hexdigest(), "scheduler_contract_changed")
                path = directory / name
                observed = snapshot(path)
                require(observed in (None, {"sha256": state["schedule_intent"][name]}), "scheduler_unit_changed")
                if observed is None:
                    write_unit(path, text)
            env = clean_env(self.home)
            env["XDG_RUNTIME_DIR"] = "/run/user/" + str(os.getuid())
            self.command(["/usr/bin/systemctl", "--user", "daemon-reload"], env=env, timeout=30)
            self.command(["/usr/bin/systemctl", "--user", "enable", "--now", "batc-update.timer"], env=env, timeout=30)
            state["schedule_complete"] = True
            self.save(state)
            return self.status(state)


def _public_time(at):
    if at is not None:
        require(isinstance(at, str) and len(at) <= 40
                and datetime.fromisoformat(at.replace("Z", "+00:00")).tzinfo is not None,
                "update_state_unreadable")
    return at


def _public_release_check(check):
    if check is None:
        return None
    at = _public_time(check["completed_at"])
    require(at is not None, "update_state_unreadable")
    candidate = check["candidate"]
    if candidate is not None:
        version(candidate["version"])
        commit(candidate["commit"])
        published = candidate.get("published_at")
        # Older private state has no supported publication observation.
        if published is None:
            return None
        _public_time(published)
        candidate = {key: candidate[key] for key in ("version", "commit", "published_at")}
    return {"completed_at": at, "candidate": candidate}


def _public_status(result):
    """Explicit public fields only; private receipts and installation proofs stay local."""
    require(result["installation_matches_state"], "update_installation_mismatch")
    installed = result["installed"]
    version(installed["version"])
    sha = commit(installed["commit"])
    require(type(result["enabled"]) is bool and result["channel"] in {"stable", "preview"},
            "update_state_unreadable")
    last = result["last"]
    if last is not None:
        require(last["status"] in {"enrolled_for_next_launch", "installed_for_next_launch", "rolled_back_updates_disabled",
                                  "no_newer_release", "up_to_date", "failed"}, "update_state_unreadable")
        at = last["at"]
        require(isinstance(at, str) and len(at) <= 40 and datetime.fromisoformat(at).tzinfo is not None,
                "update_state_unreadable")
        public_last = {"status": last["status"], "at": at}
        if "code" in last:
            code = last["code"]
            require(isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,95}", code), "update_state_unreadable")
            public_last["code"] = code
        last = public_last
    return {"state": "reported", "enabled": result["enabled"], "channel": result["channel"],
            "installed": {"version": installed["version"], "commit": sha}, "pending": result["pending"],
            "last": last, "activation": "next_launch", "installed_at": _public_time(result.get("installed_at")),
            "release_check": _public_release_check(result.get("release_check"))}


def _capture_process_installation():
    """Pin launch ownership before current can move; never cache mutable update facts."""
    from .build_identity import build_identity
    try:
        observer = Updater()
        state, current, _ = observer._status_snapshot()
        sha = commit(build_identity()["commit"])
        root = Path(current["root"]).resolve()
        package = Path(__file__).resolve().parent
        if sha == state["current"] == current["release"]["commit"] and package.is_relative_to(root):
            return observer.root, sha, root, package
    except Exception:
        return None  # Capabilities report read errors, never an import/startup failure.
    return None


def connector_update_status(build):
    """Sanitized self-report; one fresh private-file read, no network or mutations."""
    base = {"activation": "next_launch"}
    if not sys.platform.startswith("linux"):
        return {**base, "state": "unsupported"}
    try:
        observer = Updater()
        state, _, result = observer._status_snapshot()
        identity = _PROCESS_INSTALLATION
        if identity is None:
            return {**base, "state": "not_managed"}
        update_root, sha, root, package = identity
        record = state["versions"].get(sha)
        if (observer.root != update_root or build.get("commit") != sha or not record
                or record["release"]["commit"] != sha or Path(record["root"]).resolve() != root
                or not package.is_relative_to(root)):
            return {**base, "state": "not_managed"}
        return _public_status(result)
    except FileNotFoundError:
        return {**base, "state": "not_enrolled"}
    except Exception as exc:
        # Never expose exception text: it can contain paths or private state bytes.
        code = str(exc) if isinstance(exc, UpdateError) and str(exc) in {
            "update_state_incompatible", "update_installation_mismatch"} else "update_state_unreadable"
        return {**base, "state": "unreadable", "code": code}


# ApiV1 imports this module at startup, alongside its loaded build snapshot.
# This is process ownership evidence, not a status cache; every API call re-reads state.
_PROCESS_INSTALLATION = _capture_process_installation()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    enable = commands.add_parser("enable", help="Enroll a deployment or resume automatic updates")
    enable.add_argument("--receipt", type=Path)
    enable.add_argument("--channel", choices=("stable", "preview"))
    for name in ("status", "check", "run", "disable", "schedule"):
        commands.add_parser(name)
    rollback = commands.add_parser("rollback")
    rollback.add_argument("--preview", action="store_true", help="Read compatibility evidence without activation")
    rollback.add_argument("--journal", type=Path, help="Journal to observe (only with --preview)")
    args = parser.parse_args(argv)
    if args.action == "rollback" and args.journal is not None and not args.preview:
        parser.error("--journal requires --preview")
    try:
        updater = Updater()
        if args.action == "enable":
            if args.receipt:
                require(args.channel is not None, "explicit_channel_required")
                result = updater.enroll(args.receipt, args.channel)
            else:
                result = updater.policy(True, args.channel)
        elif args.action == "disable":
            result = updater.policy(False)
        elif args.action == "run":
            result = updater.apply()
        elif args.action == "rollback" and args.preview:
            result = updater.rollback_preview(journal=args.journal)
        else:
            result = getattr(updater, args.action)()
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        code = str(exc) if isinstance(exc, UpdateError) else "update_state_or_environment_invalid"
        print(json.dumps({"status": "failed", **(exc.diagnostic() if isinstance(exc, EntrypointDirectoryError) else {"code": code})}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
