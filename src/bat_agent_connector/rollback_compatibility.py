"""Read-only evidence for a previous package, never permission to activate it."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import tempfile
from contextlib import closing, contextmanager
from pathlib import Path

from . import platform_files
from .build_identity import build_identity
from .task_journal import LATEST_DATA_STEP
from .update_install import digest_files, package_files
from .update_release import EPOCH, require


def declaration(*, journal_max=LATEST_DATA_STEP, newer_journal_fence=True,
                operation_support=(), enrollment_formats=(1,), enrollment_epochs=(EPOCH,)):
    """Producer for the shipped declaration; parameters also exercise future/older contracts.

    No in-flight action/step compatibility has been certified by this declaration.
    Knowing an action's name is not evidence that its earlier effects can reconcile.
    """
    return {"format": 1, "journal_max": journal_max,
            "newer_journal_fence": newer_journal_fence, "operation_support": list(operation_support),
            "enrollment_formats": list(enrollment_formats), "enrollment_epochs": list(enrollment_epochs)}


def _valid(doc):
    def integers(values):
        return isinstance(values, list) and all(type(v) is int and 0 <= v <= 2147483647 for v in values)

    def names(values):
        return isinstance(values, list) and all(isinstance(v, str) and 0 < len(v) <= 200 for v in values)

    if not (isinstance(doc, dict) and type(doc.get("format")) is int and doc["format"] == 1
            and type(doc.get("journal_max")) is int
            and 0 <= doc["journal_max"] <= 2147483647 and type(doc.get("newer_journal_fence")) is bool
            and integers(doc.get("enrollment_formats")) and integers(doc.get("enrollment_epochs"))
            and isinstance(doc.get("operation_support"), list)):
        return False
    kinds = set()
    for item in doc["operation_support"]:
        if not (isinstance(item, dict) and names([item.get("kind")]) and item["kind"] not in kinds
                and names(item.get("statuses")) and isinstance(item.get("steps"), list)):
            return False
        kinds.add(item["kind"])
        if not all(isinstance(step, dict) and names([step.get("name")]) and names(step.get("statuses"))
                   for step in item["steps"]):
            return False
    return True


def _declaration_bytes(path):
    # Package data can be 0644. It is public code, unlike private updater state;
    # still pin the parent and refuse linked, foreign or writable-by-others data.
    parent = platform_files._posix_dir(path.parent)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
    finally:
        os.close(parent)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1
                and not info.st_mode & 0o022 and info.st_size <= 65536, "declaration_not_readable")
        return stream.read(65537)


def _previous(record):
    """Authenticate bytes against the saved proof; never import a previous module."""
    try:
        root, package = Path(record["root"]), Path(record["proof"]["package"])
        platform_files.check_private(root, directory=True)
        require(package.is_relative_to(root / "tools/bat-agent-connector/lib")
                and package.resolve() == package.absolute(), "package_location_changed")
        inventory = package_files(package)
        require(digest_files(inventory) == record["proof"]["files"], "package_proof_changed")
        require(hashlib.sha256(platform_files.read_private(root / "skill/SKILL.md")).hexdigest()
                == record["release"]["skill_sha256"], "activated_skill_changed")
        identity = {"version": record["release"]["version"], "commit": record["release"]["commit"],
                    "source": "verified_installed_package"}
    except (OSError, ValueError, KeyError, TypeError):
        return None, None, "PREVIOUS_PACKAGE_UNVERIFIED"
    if "compatibility.json" not in inventory:
        return identity, None, "PREVIOUS_BUILD_SUPPORT_UNDECLARED"
    try:
        raw = _declaration_bytes(package / "compatibility.json")
        require(hashlib.sha256(raw).hexdigest() == inventory["compatibility.json"], "declaration_changed")
        doc = json.loads(raw)
        require(_valid(doc), "declaration_invalid")
    except (OSError, ValueError, TypeError, RecursionError):
        return identity, None, "PREVIOUS_DECLARATION_UNREADABLE_OR_INVALID"
    return identity, doc, None


class JournalObservationError(ValueError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def _signatures(paths):
    result = []
    for path in paths:
        try:
            info = path.lstat()
        except FileNotFoundError:
            result.append(None)
            continue
        platform_files.check_private(path)
        result.append((info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns))
    return result


@contextmanager
def _snapshot_reader(path):
    # mode=ro alone may write WAL read-marks in the original -shm. Never open
    # SQLite on the source: copy stable DB/WAL bytes using O_RDONLY, without SHM,
    # then let SQLite build its own temporary index. A changing source is unknown.
    platform_files.check_private(path)
    require(path.resolve() == path.absolute(), "journal_linked")
    paths = [path, path.with_name(path.name + "-wal")]
    before = _signatures(paths)
    if sum(info[2] for info in before if info) > 128 * 1024 * 1024:
        raise JournalObservationError("JOURNAL_SNAPSHOT_SIZE_LIMIT")
    if path.with_name(path.name + "-journal").exists():
        raise JournalObservationError("JOURNAL_RECOVERY_REQUIRED")
    with tempfile.TemporaryDirectory(prefix="batc-rollback-preview-", dir=os.environ.get("TMPDIR")) as temporary:
        copy = Path(temporary) / path.name
        for source, info in zip(paths, before, strict=True):
            if info is None:
                continue
            fd = platform_files.open_private_file(source, os.O_RDONLY)
            with os.fdopen(fd, "rb") as incoming, (Path(temporary) / source.name).open("xb") as outgoing:
                # Bound a growing WAL too; the generation check then discards it.
                remaining = info[2]
                while remaining:
                    block = incoming.read(min(65536, remaining))
                    if not block:
                        raise JournalObservationError("JOURNAL_CHANGED_DURING_PREVIEW")
                    outgoing.write(block)
                    remaining -= len(block)
        if _signatures(paths) != before:
            raise JournalObservationError("JOURNAL_CHANGED_DURING_PREVIEW")
        with closing(sqlite3.connect(copy.as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
            yield db
        if _signatures(paths) != before:
            raise JournalObservationError("JOURNAL_CHANGED_DURING_PREVIEW")


def _journal(path):
    with _snapshot_reader(path) as db:
        db.execute("BEGIN")  # One observation, including committed WAL frames.
        recorded = db.execute("PRAGMA user_version").fetchone()[0]
        counts = [{"kind": kind, "status": status, "count": count} for kind, status, count in db.execute("""
            SELECT action,status,COUNT(*) FROM operations
            WHERE status NOT IN ('succeeded','failed','cancelled') OR operation_id IN
              (SELECT operation_id FROM operation_steps WHERE status IN ('started','uncertain'))
            GROUP BY action,status ORDER BY action,status LIMIT 1001""")]
        steps = [{"kind": kind, "name": name, "status": status, "count": count}
                 for kind, name, status, count in db.execute("""
            SELECT o.action,s.name,s.status,COUNT(*) FROM operation_steps s JOIN operations o USING(operation_id)
            WHERE o.status NOT IN ('succeeded','failed','cancelled') OR o.operation_id IN
              (SELECT operation_id FROM operation_steps WHERE status IN ('started','uncertain'))
            GROUP BY o.action,s.name,s.status ORDER BY o.action,s.name,s.status LIMIT 1001""")]
        # Older task/command work is also live data, even without an OperationService row.
        for prefix, query in (("task:", "SELECT recipe,state,COUNT(*) FROM tasks WHERE state NOT IN ('done','failed') GROUP BY recipe,state LIMIT 1001"),
                              ("command:", "SELECT kind,status,COUNT(*) FROM commands WHERE status NOT IN ('settled','rejected','cancelled') GROUP BY kind,status LIMIT 1001")):
            counts.extend({"kind": prefix + kind, "status": status, "count": count}
                          for kind, status, count in db.execute(query))
        require(len(counts) <= 1000 and len(steps) <= 1000, "journal_group_limit")
        credentials = {"api_principals": db.execute("SELECT COUNT(*) FROM api_principals").fetchone()[0],
                       "capabilities": db.execute("SELECT COUNT(*) FROM capabilities").fetchone()[0]}
    credentials["admin_token_file"] = int(os.path.lexists(path.parent / "task-admin.token"))
    return recorded, {"counts": counts, "steps": steps}, {"format": None, "records": credentials}


def preview(state, *, journal, observed_at):
    record = state.get("versions", {}).get(state.get("previous"))
    identity, support, unavailable = _previous(record) if record else (None, None, "NO_PREVIOUS_BUILD")
    observer = build_identity()
    facts = {}

    def fact(area, value, compatibility, reason):
        facts[area] = {"value": value, "compatibility": compatibility, "reason": reason,
                       "build": identity, "observed_by": observer, "observed_at": observed_at}

    fact("package", identity, "compatible" if identity else "unknown",
         "PREVIOUS_PACKAGE_DIGEST_VERIFIED" if identity else unavailable)
    try:
        recorded, in_flight, credentials = _journal(Path(journal))
        journal_reason = None
    except (OSError, ValueError, sqlite3.Error) as exc:
        recorded = in_flight = credentials = None
        journal_reason = exc.reason if isinstance(exc, JournalObservationError) else "JOURNAL_UNREADABLE"
    maximum = support["journal_max"] if support else None
    fact("journal", {"recorded_data_step": recorded, "declared_max": maximum}, "unknown",
         journal_reason if recorded is None else unavailable)
    if recorded is not None and support:
        fits = recorded <= maximum
        fact("journal", {"recorded_data_step": recorded, "declared_max": maximum},
             "compatible" if fits else "incompatible",
             "JOURNAL_DATA_STEP_SUPPORTED" if fits else "JOURNAL_DATA_STEP_UNSUPPORTED")
    fence = support["newer_journal_fence"] if support else None
    fact("newer_journal_fence", fence, "unknown" if fence is None else ("compatible" if fence else "incompatible"),
         unavailable if fence is None else ("NEWER_JOURNAL_FENCE_DECLARED" if fence else "NEWER_JOURNAL_FENCE_ABSENT"))
    fact("in_flight", in_flight, "unknown", journal_reason if in_flight is None else unavailable)
    if in_flight is not None and not in_flight["counts"] and not in_flight["steps"]:
        fact("in_flight", in_flight, "compatible", "NO_IN_FLIGHT_WORK")
    elif in_flight is not None and support:
        declared = {item["kind"]: item for item in support["operation_support"]}
        supported = all(row["status"] in declared.get(row["kind"], {}).get("statuses", [])
                        for row in in_flight["counts"])
        supported = supported and all(any(step["name"] == row["name"] and row["status"] in step["statuses"]
                                         for step in declared.get(row["kind"], {}).get("steps", []))
                                      for row in in_flight["steps"])
        fact("in_flight", in_flight, "compatible" if supported else "unknown",
             "IN_FLIGHT_SUPPORT_DECLARED" if supported else "IN_FLIGHT_SUPPORT_UNDECLARED")
    fact("credentials", credentials, "unknown", journal_reason if credentials is None else
         "CREDENTIAL_FORMAT_UNVERSIONED")
    if credentials is not None and not any(credentials["records"].values()):
        fact("credentials", credentials, "compatible", "NO_CREDENTIAL_RECORDS")
    try:
        receipts = sum((Path(item["root"]) / "rollout-receipt.json").exists()
                       for item in state["versions"].values())
        enrollment = {"state_format": state["format"], "epoch": state["epoch"],
                      "unversioned_deployment_receipts": receipts}
        if receipts:
            fact("enrollment", enrollment, "unknown", "ENROLLMENT_RECEIPT_UNVERSIONED")
        elif state.get("pending") or state.get("enrollment"):
            fact("enrollment", enrollment, "unknown", "UPDATE_SWITCH_PENDING")
        elif support:
            fits = state["format"] in support["enrollment_formats"] and state["epoch"] in support["enrollment_epochs"]
            fact("enrollment", enrollment, "compatible" if fits else "incompatible",
                 "ENROLLMENT_FORMAT_SUPPORTED" if fits else "ENROLLMENT_FORMAT_UNSUPPORTED")
        else:
            fact("enrollment", enrollment, "unknown", unavailable)
    except (OSError, ValueError, KeyError, TypeError):
        fact("enrollment", None, "unknown", "ENROLLMENT_UNREADABLE")
    values = {item["compatibility"] for item in facts.values()}
    verdict = "incompatible" if "incompatible" in values else ("compatible" if values == {"compatible"} else "unknown")
    return {"format": 1, "verdict": verdict, "facts": facts, "observed_at": observed_at,
            "activation": "unchanged", "processes_restarted": False}
