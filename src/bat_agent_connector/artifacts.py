"""Immutable connector-owned artifacts and continuation dispatch evidence.

Bytes stay outside the journal. The HTTP payload adapter writes operation scratch;
OperationService verifies and publishes it, without holding a socket stream.
Design: docs/design/artifacts.md (part A).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import stat
import time
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path

from . import platform_files, resource_policy
from .operations import RERUN, ActionDef, NeedsAttention, OperationError, StepFailed, Wait

ARTIFACT_ID = re.compile(r"art_[0-9a-f]{32}")
OPERATION_ID = re.compile(r"op_[0-9a-f]{32}")
DIGEST = re.compile(r"[0-9a-f]{64}")


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def manifest_digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


@dataclass(frozen=True)
class ArtifactSettings:
    store_root: str | None = None
    max_file_bytes: int = 16 * 1024 * 1024
    max_selection_count: int = 20
    max_selection_bytes: int = 64 * 1024 * 1024
    max_store_bytes: int = 512 * 1024 * 1024
    mcp_max_file_bytes: int = 256 * 1024
    upload_window_s: int = 3600
    transfer_timeout_s: int = 300

    @classmethod
    def from_dict(cls, raw: dict):
        if not isinstance(raw, dict) or set(raw) - set(cls.__dataclass_fields__):
            raise ValueError("invalid [artifacts] settings")
        for key, value in raw.items():
            if key == "store_root":
                if not isinstance(value, str) or not Path(value).is_absolute():
                    raise ValueError("artifacts.store_root must be an absolute path")
            elif type(value) is not int or value <= 0:
                raise ValueError(f"artifacts.{key} must be a positive integer")
        return cls(**raw)

    def limits(self) -> dict:
        return {k: v for k, v in asdict(self).items() if k != "store_root"}


def safe_name(value) -> str:
    if (not isinstance(value, str) or not 1 <= len(value) <= 200 or value in {".", "..", ".git"}
            or any(c in value for c in "/\\") or any(unicodedata.category(c) == "Cc" for c in value)
            or len(value.encode()) > 240):
        raise OperationError("INVALID_PARAMS", "display_name must be a safe single file name", 422)
    return value


def _open_dir(path: Path, *, create=False) -> int:
    resource_policy.check_artifact_storage(path)
    if platform_files.WINDOWS:
        return platform_files.native.open_dir(path, create=create)
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            if create:
                with contextlib.suppress(FileExistsError):
                    os.mkdir(part, 0o700, dir_fd=fd)
                    os.fsync(fd)
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        return fd
    except BaseException:
        os.close(fd)
        raise


def _close_dir(fd):
    if platform_files.WINDOWS:
        fd.close()
    else:
        os.close(fd)


def _sync_dir(fd):
    if not platform_files.WINDOWS:
        os.fsync(fd)
    # Windows file handles use FILE_WRITE_THROUGH and flush before rename.


def _open_file(name, flags, mode=0o600, *, parent):
    if platform_files.WINDOWS:
        return platform_files.native.open_file(name, flags, mode, parent=parent)
    return os.open(name, flags | os.O_NOFOLLOW, mode, dir_fd=parent)


def _file_hash(path: Path) -> tuple[int, str]:
    parent = _open_dir(path.parent)
    try:
        fd = _open_file(path.name, os.O_RDONLY, parent=parent)
    finally:
        _close_dir(parent)
    with os.fdopen(fd, "rb") as file:
        info = os.fstat(file.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise OperationError("ARTIFACT_CONTENT_UNAVAILABLE", "artifact is not a private regular file", 409)
        digest, size = hashlib.sha256(), 0
        for chunk in iter(lambda: file.read(65536), b""):
            digest.update(chunk)
            size += len(chunk)
        after = os.fstat(file.fileno())
        if (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise OperationError("ARTIFACT_CONTENT_UNAVAILABLE", "artifact changed while read", 409)
    return size, digest.hexdigest()


def get(db, artifact_id, revision, *, principal=None) -> dict:
    if not isinstance(artifact_id, str) or not ARTIFACT_ID.fullmatch(artifact_id) or type(revision) is not int or revision < 1:
        raise OperationError("INVALID_ARTIFACT_REF", "artifact_id and positive revision are required", 422)
    if principal is not None:
        from .pm_source_lineage import require
        require(db, principal, "artifact", artifact_id)
    row = db.execute("SELECT * FROM artifact_revisions WHERE artifact_id=? AND revision=?",
                     (artifact_id, revision)).fetchone()
    if row is None:
        raise OperationError("ARTIFACT_NOT_FOUND", "no such artifact revision", 404)
    out = dict(row)
    source = db.execute("SELECT document FROM artifact_capture_sources WHERE operation_id=?",
                        (out["operation_id"],)).fetchone()
    out["source"] = json.loads(source[0]) if source else {"kind": "upload", "operation_id": out["operation_id"]}
    out["acceptances"] = [json.loads(r[0]) for r in db.execute(
        "SELECT document FROM artifact_acceptances WHERE artifact_id=? AND revision=? ORDER BY operation_id",
        (artifact_id, revision))]
    out["content_url"] = f"/api/v1/artifacts/{artifact_id}/revisions/{revision}/content"
    out["materializations"] = materializations(db, artifact_id=artifact_id, revision=revision)
    return out


def list_artifacts(db, *, limit=50, cursor=None, principal=None) -> dict:
    if type(limit) is not int or not 1 <= limit <= 200:
        raise OperationError("INVALID_PARAMS", "limit must be 1-200", 422)
    args, clause = [], ""
    if cursor:
        try:
            when, artifact_id = str(cursor).split("|", 1)
            when = float(when)
            if not ARTIFACT_ID.fullmatch(artifact_id):
                raise ValueError
        except ValueError:
            raise OperationError("INVALID_CURSOR", "use the earlier next_cursor", 422) from None
        clause = " WHERE (a.created_at,a.artifact_id)<(?,?)"
        args = [when, artifact_id]
    rows = db.execute("SELECT a.* FROM artifacts a" + clause  # noqa: S608 - fixed clauses and placeholders
                      + " ORDER BY a.created_at DESC,a.artifact_id DESC LIMIT ?", (*args, limit + 1)).fetchall()
    items = []
    for row in rows[:limit]:
        if principal is not None:
            from .pm_source_lineage import visible
            if not visible(db, principal, "artifact", row["artifact_id"]):
                continue
        item = dict(row)
        item["revision"] = get(db, row["artifact_id"], row["latest_revision"]) if row["latest_revision"] else None
        items.append(item)
    last = rows[limit - 1] if len(rows) > limit else None
    return {"artifacts": items, "next_cursor": f"{last['created_at']}|{last['artifact_id']}" if last else None}


def normalize_refs(db, values, settings: ArtifactSettings, *, roles=False) -> list[dict]:
    if not isinstance(values, list) or len(values) > settings.max_selection_count:
        raise OperationError("ARTIFACT_SELECTION_TOO_LARGE", "invalid attachment count", 422)
    refs, total, seen = [], 0, set()
    for value in values:
        keys = {"artifact_id", "revision", "digest"} | ({"role"} if roles else set())
        if not isinstance(value, dict) or set(value) != keys or not DIGEST.fullmatch(str(value.get("digest", ""))):
            raise OperationError("INVALID_ARTIFACT_REF", "use artifact_id, revision and digest (and role for work items)", 422)
        row = get(db, value["artifact_id"], value["revision"])
        if row["state"] != "ready" or row["digest"] != value["digest"]:
            raise OperationError("INVALID_ARTIFACT_REF", "revision is not ready or digest differs", 409)
        if roles and (not isinstance(value["role"], str) or value["role"] not in {"input", "result"}):
            raise OperationError("INVALID_ARTIFACT_REF", "attachment role must be input or result", 422)
        key = (value["artifact_id"], value["revision"], value.get("role", "input"))
        if key in seen:
            continue
        seen.add(key)
        total += row["size_bytes"]
        refs.append(dict(value))
    if total > settings.max_selection_bytes:
        raise OperationError("ARTIFACT_SELECTION_TOO_LARGE", "selected revisions exceed the byte limit", 422)
    return sorted(refs, key=lambda r: (r["artifact_id"], r["revision"], r.get("role", "input")))


def reference(db, owner_kind, owner_id, refs, operation_id) -> None:
    if owner_kind not in {"work_item", "checkpoint", "operation"}:
        raise ValueError("unsupported artifact reference owner")
    now = time.time()
    active = {(r["artifact_id"], r["revision"], r.get("role", "input")) for r in refs}
    for row in db.execute("SELECT * FROM artifact_references WHERE owner_kind=? AND owner_id=? AND released_at IS NULL",
                          (owner_kind, owner_id)).fetchall():
        if (row["artifact_id"], row["revision"], row["role"]) not in active:
            db.execute("""UPDATE artifact_references SET released_at=?,release_operation_id=? WHERE owner_kind=?
                AND owner_id=? AND artifact_id=? AND revision=? AND role=? AND operation_id=?""",
                       (now, operation_id, owner_kind, owner_id, row["artifact_id"], row["revision"], row["role"], row["operation_id"]))
    for ref in refs:
        if db.execute("""SELECT 1 FROM artifact_references WHERE owner_kind=? AND owner_id=? AND artifact_id=?
            AND revision=? AND role=? AND released_at IS NULL""",
                      (owner_kind, owner_id, ref["artifact_id"], ref["revision"], ref.get("role", "input"))).fetchone():
            continue
        db.execute("""INSERT OR IGNORE INTO artifact_references(owner_kind,owner_id,artifact_id,revision,digest,role,
            operation_id,created_at) VALUES(?,?,?,?,?,?,?,?)""",
                   (owner_kind, owner_id, ref["artifact_id"], ref["revision"], ref["digest"], ref.get("role", "input"), operation_id, now))


def materializations(db, *, operation_id=None, artifact_id=None, revision=None) -> list[dict]:
    clause, args = [], []
    for key, value in (("operation_id", operation_id), ("artifact_id", artifact_id), ("revision", revision)):
        if value is not None:
            clause.append(key + "=?")
            args.append(value)
    rows = db.execute("SELECT * FROM artifact_materializations" + (" WHERE " + " AND ".join(clause) if clause else "")  # noqa: S608 - fixed columns
                      + " ORDER BY materialization_id", args)
    return [{**dict(row), "evidence": json.loads(row["evidence"]) if row["evidence"] else None} for row in rows]


class ArtifactStore:
    def __init__(self, ops, settings=None):
        self.ops, self.db, self.journal = ops, ops.db, ops.journal
        self.settings = settings or ArtifactSettings()
        self.root = Path(self.settings.store_root or self.journal.path.parent / "artifacts")
        self.receivers: set[str] = set()
        self._reap_lock = asyncio.Lock()
        self._reap_task = None
        self._reaper_closed = False
        self._setup()
        # A receiver belongs to the daemon process, not a remote writer. After a restart it is gone.
        for row in self.db.execute("SELECT * FROM artifact_uploads WHERE receive_state='receiving'").fetchall():
            path = self.root / "staging" / row["operation_id"] / f"a{row['attempt']:04d}" / "content"
            try:
                size, digest = _file_hash(path)
            except FileNotFoundError:
                size, digest = 0, hashlib.sha256(b"").hexdigest()
            self.db.execute("""UPDATE artifact_uploads SET receive_state='partial',received_size=?,received_digest=?
                WHERE operation_id=?""", (size, digest, row["operation_id"]))

    def _setup(self):
        resource_policy.check_artifact_storage(self.root)
        if self.root.exists():
            platform_files.check_private(self.root, directory=True)
            if any(self.root.iterdir()) and not (self.root / ".batc-artifact-store").is_file():
                raise ValueError("not a connector artifact store")
        fd = _open_dir(self.root, create=True)
        try:
            try:
                marker = _open_file(".batc-artifact-store", os.O_WRONLY | os.O_CREAT | os.O_EXCL, parent=fd)
                with os.fdopen(marker, "w") as file:
                    file.write("batc-artifacts-v1\n")
                    file.flush()
                    os.fsync(file.fileno())
            except FileExistsError:
                marker = _open_file(".batc-artifact-store", os.O_RDONLY, parent=fd)
                with os.fdopen(marker, "r") as file:
                    if file.read(100) != "batc-artifacts-v1\n":
                        raise ValueError("not a connector artifact store") from None
            _sync_dir(fd)
        finally:
            _close_dir(fd)
        for name in ("staging", "revisions"):
            _close_dir(_open_dir(self.root / name, create=True))

    def used_bytes(self):
        ready = self.db.execute("SELECT COALESCE(SUM(size_bytes),0) FROM artifact_revisions WHERE state IN ('ready','unavailable')").fetchone()[0]
        reserved = self.db.execute("SELECT COALESCE(SUM(reserved_bytes),0) FROM artifact_uploads WHERE released_at IS NULL").fetchone()[0]
        return ready + reserved

    def validate_upload(self, target, params, pre):
        if set(params) - {"display_name", "media_type", "size_bytes", "expected_digest", "mcp"} or set(target) - {"artifact_id"}:
            raise OperationError("INVALID_PARAMS", "unknown upload parameters", 422)
        safe_name(params.get("display_name"))
        size, digest = params.get("size_bytes"), params.get("expected_digest")
        if type(size) is not int or size < 0 or not isinstance(digest, str) or not DIGEST.fullmatch(digest):
            raise OperationError("INVALID_PARAMS", "size_bytes and full SHA-256 expected_digest are required", 422)
        limit = min(self.settings.max_file_bytes, self.settings.mcp_max_file_bytes) if params.get("mcp") else self.settings.max_file_bytes
        if size > limit:
            raise OperationError("ARTIFACT_TOO_LARGE", "file exceeds the upload limit; use CLI or Dashboard for larger MCP files", 413)
        media = params.get("media_type", "application/octet-stream")
        if not isinstance(media, str) or not re.fullmatch(r"[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+", media) or len(media) > 100:
            raise OperationError("INVALID_PARAMS", "media_type must be a MIME type", 422)
        if "artifact_id" in target:
            if not isinstance(target["artifact_id"], str) or not ARTIFACT_ID.fullmatch(target["artifact_id"]):
                raise OperationError("INVALID_ARTIFACT_REF", "target.artifact_id must be an artifact ID", 422)
            row = self.db.execute("SELECT * FROM artifacts WHERE artifact_id=?", (target["artifact_id"],)).fetchone()
            if row is None:
                raise OperationError("ARTIFACT_NOT_FOUND", "no such artifact", 404)
            if type(pre.get("expected_latest_revision")) is not int or pre["expected_latest_revision"] != row["latest_revision"]:
                raise OperationError("REVISION_CONFLICT", "the ready revision changed; read it again", 409)
            if self.db.execute("""SELECT 1 FROM artifact_uploads u JOIN operations o USING(operation_id)
                WHERE u.artifact_id=? AND u.released_at IS NULL AND o.status NOT IN ('succeeded','failed','cancelled')""",
                               (target["artifact_id"],)).fetchone():
                raise OperationError("UPLOAD_IN_PROGRESS", "a revision is still reserved", 409)
        if self.used_bytes() + size > self.settings.max_store_bytes:
            raise OperationError("ARTIFACT_STORE_FULL", "artifact store is full; increase the configured quota", 409)

    def reserve(self, ctx):
        return self.reserve_revision(ctx, ctx.target, ctx.params, ctx.preconditions)

    def reserve_revision(self, ctx, target, params, pre):
        with self.journal.tx():
            row = self.db.execute("SELECT * FROM artifact_uploads WHERE operation_id=?", (ctx.operation_id,)).fetchone()
            if row:
                return dict(row)
            self.validate_upload(target, params, pre)
            artifact_id = target.get("artifact_id") or "art_" + ctx.operation_id[3:]
            now = time.time()
            self.db.execute("INSERT OR IGNORE INTO artifacts(artifact_id,created_at,actor) VALUES(?,?,?)", (artifact_id, now, ctx.actor))
            revision = self.db.execute("SELECT COALESCE(MAX(revision),0)+1 FROM artifact_revisions WHERE artifact_id=?", (artifact_id,)).fetchone()[0]
            self.db.execute("""INSERT INTO artifact_revisions(artifact_id,revision,digest,size_bytes,media_type,
                display_name,operation_id,state,created_at) VALUES(?,?,?,?,?,?,?,'receiving',?)""",
                           (artifact_id, revision, params["expected_digest"], params["size_bytes"],
                            params.get("media_type", "application/octet-stream"), params["display_name"], ctx.operation_id, now))
            self.db.execute("""INSERT INTO artifact_uploads(operation_id,artifact_id,revision,deadline,reserved_bytes)
                VALUES(?,?,?,?,?)""", (ctx.operation_id, artifact_id, revision, now + self.settings.upload_window_s, params["size_bytes"]))
        return dict(self.db.execute("SELECT * FROM artifact_uploads WHERE operation_id=?", (ctx.operation_id,)).fetchone())

    def content_path(self, artifact_id, revision) -> Path:
        get(self.db, artifact_id, revision)
        return self.root / "revisions" / artifact_id / f"r{revision:08d}" / "content"

    def read_content(self, artifact_id, revision) -> bytes:
        row = get(self.db, artifact_id, revision)
        path = self.content_path(artifact_id, revision)
        try:
            size, digest = _file_hash(path)
            fd = _open_dir(path.parent)
            try:
                raw_fd = _open_file("content", os.O_RDONLY, parent=fd)
            finally:
                _close_dir(fd)
            with os.fdopen(raw_fd, "rb") as file:
                data = file.read(row["size_bytes"] + 1)
        except (OSError, OperationError):
            raise OperationError("ARTIFACT_CONTENT_UNAVAILABLE", "artifact content is unavailable", 409) from None
        if row["state"] != "ready" or (size, digest) != (row["size_bytes"], row["digest"]) or hashlib.sha256(data).hexdigest() != digest:
            raise OperationError("ARTIFACT_CONTENT_UNAVAILABLE", "artifact content does not match its revision", 409)
        return data

    def check_receive(self, principal, operation_id, length):
        op = self.ops.get(operation_id)
        if not principal.allows("manage") or not (principal.admin or principal.actor == op["actor"]):
            raise OperationError("FORBIDDEN", "upload content needs its actor and manage scope", 403)
        if op["action"] != "artifact.upload" or op["status"] != "waiting_external" or op["cancel_requested"]:
            raise OperationError("UPLOAD_STATE", "operation is not waiting for upload content", 409)
        row = self.db.execute("SELECT * FROM artifact_uploads WHERE operation_id=?", (operation_id,)).fetchone()
        if row is None or row["released_at"] or row["deadline"] <= time.time():
            self.ops.wake(operation_id)
            raise OperationError("UPLOAD_EXPIRED", "the upload window expired", 409)
        if row["receive_state"] in {"receiving", "complete"} or operation_id in self.receivers:
            raise OperationError("UPLOAD_IN_PROGRESS", "content is being received or verified", 409)
        if type(length) is not int or length != op["params"]["size_bytes"]:
            raise OperationError("ARTIFACT_SIZE_MISMATCH", "Content-Length differs from the reserved size", 422)
        return op, dict(row)

    async def receive(self, principal, operation_id, reader, length):
        _op, row = self.check_receive(principal, operation_id, length)
        return await self.receive_reserved(operation_id, row, reader, length, principal.actor)

    async def receive_reserved(self, operation_id, row, reader, length, actor):
        """Internal receiver for an already authorized reservation; not an API entry point."""
        attempt = row["attempt"] + 1
        with self.journal.tx():
            # Partial bytes remain scratch until terminal; reserve room for this entire next attempt.
            partial = row["received_size"] if row["attempt"] else 0
            if self.used_bytes() + partial > self.settings.max_store_bytes:
                raise OperationError("ARTIFACT_STORE_FULL", "partial attempts use the remaining quota", 409)
            self.db.execute("""UPDATE artifact_uploads SET attempt=?,receive_state='receiving',
                reserved_bytes=reserved_bytes+?,received_size=0,received_digest=NULL WHERE operation_id=?""",
                            (attempt, partial, operation_id))
            self.journal.api_event("artifact", row["artifact_id"], "artifact.receiving",
                                   {"operation_id": operation_id, "attempt": attempt}, actor=actor)
        self.receivers.add(operation_id)
        digest, size, complete = hashlib.sha256(), 0, False
        directory = self.root / "staging" / operation_id / f"a{attempt:04d}"
        try:
            fd = _open_dir(directory, create=True)
            try:
                content_fd = _open_file("content", os.O_WRONLY | os.O_CREAT | os.O_EXCL, parent=fd)
            finally:
                _close_dir(fd)
            with os.fdopen(content_fd, "wb") as file:
                while size < length:
                    if self.ops.get(operation_id, steps=False)["cancel_requested"] or row["deadline"] <= time.time():
                        raise OperationError("UPLOAD_EXPIRED", "upload cancelled or expired", 409)
                    chunk = await asyncio.wait_for(reader.read(min(65536, length - size)), min(30, max(0.1, row["deadline"] - time.time())))
                    if not chunk:
                        raise OperationError("UPLOAD_INCOMPLETE", "choose or retry the file before the upload window closes", 400)
                    size += len(chunk)
                    if size > length:
                        raise OperationError("ARTIFACT_TOO_LARGE", "received more bytes than reserved", 413)
                    digest.update(chunk)
                    file.write(chunk)
                file.flush()
                os.fsync(file.fileno())
            directory_fd = _open_dir(directory)
            try:
                _sync_dir(directory_fd)
            finally:
                _close_dir(directory_fd)
            complete = True
        except (asyncio.TimeoutError, OSError) as exc:
            raise OperationError("UPLOAD_INCOMPLETE", f"upload did not complete ({type(exc).__name__})", 400) from None
        finally:
            with self.journal.tx():
                self.db.execute("""UPDATE artifact_uploads SET receive_state=?,received_size=?,received_digest=?
                    WHERE operation_id=?""", ("complete" if complete else "partial", size, digest.hexdigest(), operation_id))
            self.receivers.discard(operation_id)
            if complete:
                self.ops.wake(operation_id)
            await self.reap_best_effort(operation_id)
        return self.ops.get(operation_id)

    def publish(self, row):
        revision = get(self.db, row["artifact_id"], row["revision"])
        directory = self.content_path(row["artifact_id"], row["revision"]).parent
        dest_fd = _open_dir(directory, create=True)
        source = self.root / "staging" / row["operation_id"] / f"a{row['attempt']:04d}"
        src_fd = _open_dir(source)
        try:
            if platform_files.WINDOWS:
                platform_files.native.publish_file(src_fd, dest_fd)
            else:
                try:
                    os.link("content", "content", src_dir_fd=src_fd, dst_dir_fd=dest_fd, follow_symlinks=False)
                    os.unlink("content", dir_fd=src_fd)
                    _sync_dir(src_fd)
                    os.chmod("content", 0o400, dir_fd=dest_fd, follow_symlinks=False)
                except (FileExistsError, FileNotFoundError):
                    try:
                        src = os.stat("content", dir_fd=src_fd, follow_symlinks=False)
                        dst = os.stat("content", dir_fd=dest_fd, follow_symlinks=False)
                        if (src.st_dev, src.st_ino) == (dst.st_dev, dst.st_ino):
                            os.unlink("content", dir_fd=src_fd)
                            _sync_dir(src_fd)
                    except FileNotFoundError:
                        pass
                os.chmod("content", 0o400, dir_fd=dest_fd, follow_symlinks=False)
            if _file_hash(directory / "content") != (revision["size_bytes"], revision["digest"]):
                raise OperationError("ARTIFACT_CONTENT_UNAVAILABLE", "existing revision has different content", 409)
            document = {k: revision[k] for k in ("artifact_id", "revision", "digest", "size_bytes", "media_type", "display_name", "operation_id")}
            try:
                marker = _open_file("manifest.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400, parent=dest_fd)
                with os.fdopen(marker, "w") as file:
                    file.write(canonical(document))
                    file.flush()
                    os.fsync(file.fileno())
            except FileExistsError:
                marker = _open_file("manifest.json", os.O_RDONLY, parent=dest_fd)
                with os.fdopen(marker) as file:
                    if json.load(file) != document:
                        raise OperationError("ARTIFACT_CONTENT_UNAVAILABLE", "revision manifest differs", 409) from None
            if platform_files.WINDOWS:
                platform_files.native.seal_file(dest_fd, "manifest.json")
            _sync_dir(dest_fd)
        finally:
            _close_dir(src_fd)
            _close_dir(dest_fd)
        return document

    def published(self, row):
        path = self.content_path(row["artifact_id"], row["revision"])
        try:
            if path.exists():
                return self.publish(row)
        except FileNotFoundError:
            pass
        return None

    def _terminal_uploads(self):
        return self.db.execute("""SELECT u.operation_id,u.artifact_id,u.revision FROM artifact_uploads u JOIN operations o USING(operation_id)
            WHERE o.status IN ('succeeded','failed','cancelled') AND u.released_at IS NULL""").fetchall()

    def schedule_reap(self, operation_id=None):
        if self._reaper_closed:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # synchronous admission leaves conservative reservations for the daemon loop
        if self._reap_task is None or self._reap_task.done():
            self._reap_task = loop.create_task(self.reap_best_effort(operation_id))

    async def close_reaper(self):
        self._reaper_closed = True
        if self._reap_task is not None:
            self._reap_task.cancel()
            await asyncio.gather(self._reap_task, return_exceptions=True)

    async def reap_best_effort(self, operation_id=None):
        try:
            await self.reap_terminal()
        except Exception as exc:  # noqa: BLE001 - scratch failure must not change a committed result
            ids = [operation_id] if operation_id else []
            if not ids:
                with contextlib.suppress(Exception):
                    ids = [row["operation_id"] for row in self._terminal_uploads()]
            for identity in ids or ["unknown"]:
                logging.warning("Artifact staging reap for operation %s failed: %s", identity, type(exc).__name__)

    def _remove_staging(self, operation_id):
        if not OPERATION_ID.fullmatch(operation_id):
            raise ValueError("invalid upload scratch identity")
        directory = self.root / "staging" / operation_id
        resource_policy.check_artifact_storage(directory)
        if directory.exists():
            if platform_files.WINDOWS:
                platform_files.native.remove_tree(directory)
            else:
                shutil.rmtree(directory)  # only this terminal operation's scratch; never revisions
            parent_fd = _open_dir(directory.parent)
            try:
                _sync_dir(parent_fd)
            finally:
                _close_dir(parent_fd)

    async def reap_terminal(self):
        async with self._reap_lock:
            for row in self._terminal_uploads():
                operation_id = row["operation_id"]
                if operation_id in self.receivers:
                    continue
                try:
                    # Filesystem work may stall; journal access remains on the daemon's event loop.
                    await asyncio.to_thread(self._remove_staging, operation_id)
                    formal = self.content_path(row["artifact_id"], row["revision"])
                    with self.journal.tx():
                        if formal.exists():
                            self.db.execute("UPDATE artifact_revisions SET state='unavailable' WHERE operation_id=? AND state='receiving'",
                                            (operation_id,))
                        self.db.execute("UPDATE artifact_uploads SET reserved_bytes=0,released_at=? WHERE operation_id=?",
                                        (time.time(), operation_id))
                except Exception as exc:  # noqa: BLE001 - retain this reservation and retry on the next pass
                    logging.warning("Artifact staging reap for operation %s failed: %s", operation_id, type(exc).__name__)


def _admit_upload(ops, principal, target, params, pre):
    ops.context["artifact_store"].schedule_reap()
    ops.context["artifact_store"].validate_upload(target, params, pre)


async def _run_upload(ctx):
    store = ctx.service.context["artifact_store"]
    try:
        async def reserve():
            return store.reserve(ctx)

        async def reread(_request):
            return RERUN

        row = await ctx.step("upload.reserve", reserve, reconcile=reread)
        ctx.set_refs(artifact_id=row["artifact_id"], revision=row["revision"],
                     content_url=f"/api/v1/artifacts/uploads/{ctx.operation_id}/content")
        row = dict(store.db.execute("SELECT * FROM artifact_uploads WHERE operation_id=?", (ctx.operation_id,)).fetchone())
        existing = store.published(row) if row["attempt"] else None
        if not existing and row["receive_state"] != "complete":
            if time.time() >= row["deadline"]:
                raise StepFailed("UPLOAD_EXPIRED", "upload window expired")
            raise Wait("waiting_external", "waiting for file content", row["deadline"] - time.time())

        return await finish_revision(ctx, row, existing=existing)
    finally:
        # _execute commits its terminal transition before this callback runs.
        asyncio.get_running_loop().call_soon(store.schedule_reap, ctx.operation_id)


async def finish_revision(ctx, row, *, existing=None, event="artifact.uploaded"):
    store = ctx.service.context["artifact_store"]
    revision = get(store.db, row["artifact_id"], row["revision"])

    async def reread(_request):
        return RERUN

    async def verify():
        if existing:
            return {"digest": existing["digest"], "size_bytes": existing["size_bytes"]}
        size, digest = _file_hash(store.root / "staging" / ctx.operation_id / f"a{row['attempt']:04d}" / "content")
        return {"size_bytes": size, "digest": digest}

    evidence = await ctx.step(f"upload.verify.{row['attempt']}", verify, reconcile=reread)
    if evidence["size_bytes"] != revision["size_bytes"]:
        raise StepFailed("ARTIFACT_SIZE_MISMATCH", "uploaded bytes differ from the declared size")
    if evidence["digest"] != revision["digest"]:
        raise StepFailed("ARTIFACT_DIGEST_MISMATCH", "uploaded bytes differ from the expected SHA-256")

    async def publish():
        return store.publish(row)

    async def read_published(_request):
        result = store.published(row)
        return result if result else RERUN

    document = await ctx.step("upload.publish", publish, reconcile=read_published)
    with store.journal.tx():
        changed = store.db.execute("UPDATE artifact_revisions SET state='ready' WHERE operation_id=? AND state!='ready'",
                                   (ctx.operation_id,)).rowcount
        store.db.execute("UPDATE artifacts SET latest_revision=MAX(latest_revision,?) WHERE artifact_id=?",
                         (row["revision"], row["artifact_id"]))
        if changed:
            store.journal.api_event("artifact", row["artifact_id"], event, document, actor=ctx.actor)
    return document


ACTIONS = [ActionDef("artifact.upload", "manage", "Upload an immutable artifact revision", _run_upload, _admit_upload)]


def continuation_refs(ops, checkpoint, params):
    return normalize_refs(ops.db, params.get("artifacts", checkpoint["artifacts"]), ops.context["artifact_store"].settings)


def input_manifest(checkpoint, params, pre, refs):
    return manifest_digest({"checkpoint_id": checkpoint["checkpoint_id"], "commit": checkpoint["commit_sha"],
                            "excerpt_sha256": checkpoint["excerpt_sha256"], "artifacts": refs,
                            "instructions_sha256": hashlib.sha256(params["instructions"].encode()).hexdigest(),
                            "agent": params.get("agent", "claude"), "work_item_id": params.get("work_item_id"),
                            "expected_work_item_fingerprint": pre.get("expected_work_item_fingerprint")})


async def source_guard(ctx, checkpoint):
    from . import checkpoints, work_items

    expected = ctx.preconditions.get("expected_source_head_sha")
    confirmation = ctx.service.db.execute("""SELECT source_head_sha FROM checkpoint_source_confirmations
        WHERE parent_operation_id=? ORDER BY created_at DESC,operation_id DESC LIMIT 1""", (ctx.operation_id,)).fetchone()
    if confirmation:
        expected = confirmation["source_head_sha"]
    if expected:
        seen = await checkpoints.source_head(ctx.service, checkpoint)
        evidence = {"expected": expected, "head": seen["head"], "observed_at": time.time()}
        ctx.set_refs(source_guard=evidence)
        if seen["head"] is None:
            raise NeedsAttention("SOURCE_UNAVAILABLE", "source HEAD could not be observed; keep the selected inputs")
        if seen["head"] != expected:
            raise NeedsAttention("SOURCE_MOVED", "source advanced; explicitly confirm the original inputs to resume this operation")
    if ctx.params.get("work_item_id"):
        item = work_items._get_item(ctx.service.db, ctx.params["work_item_id"], active=True)
        if work_items.fingerprint(item) != ctx.preconditions.get("expected_work_item_fingerprint"):
            raise NeedsAttention("CONTENT_CHANGED", "work item content changed since these inputs were selected")


def _materialization_error(evidence, request):
    if not evidence.get("ok"):
        return evidence.get("code") or "ARTIFACT_CONTENT_UNAVAILABLE"
    expected_path = request["worktree"] + "/.batc-inputs/" + request["ref"]["artifact_id"] + "-r" + str(request["ref"]["revision"]) + "/" + request["name"]
    if evidence.get("path") != expected_path or evidence.get("ref") != request["ref"] or evidence.get("operation_id") != request["operation_id"]:
        return "BINDING_MISMATCH"
    if evidence.get("size_bytes") != request["size_bytes"]:
        return "ARTIFACT_SIZE_MISMATCH"
    if evidence.get("digest") != request["ref"]["digest"]:
        return "ARTIFACT_DIGEST_MISMATCH"
    return None


def _material_state(ctx, mat, state, evidence=None):
    with ctx.service.journal.tx():
        ctx.service.db.execute("UPDATE artifact_materializations SET state=?,evidence=?,updated_at=? WHERE materialization_id=?",
                               (state, canonical(evidence) if evidence else None, time.time(), mat))
        ctx.service.journal.api_event("artifact", mat, "artifact.materialization", {"operation_id": ctx.operation_id,
                                     "state": state, "evidence": evidence}, actor=ctx.actor)
    ctx.set_refs(materializations=materializations(ctx.service.db, operation_id=ctx.operation_id))


async def materialize(ctx, checkpoint, clone, worktree, branch, refs, *, published_binding=None, before_transfer=None):
    host, ops = checkpoint["host"], ctx.service
    adapter, store = ops.context["artifact_host"], ops.context["artifact_store"]
    if refs:
        ready = await adapter.probe(host)
        if not ready.get("ok"):
            raise NeedsAttention("ARTIFACT_ADAPTER_UNAVAILABLE", ready.get("message") or "artifact helper needs Python 3.9+, Git 2.31+ and no-follow/link support")
    for ref in refs:
        revision = get(ops.db, ref["artifact_id"], ref["revision"])
        relative = f".batc-inputs/{ref['artifact_id']}-r{ref['revision']}/{safe_name(revision['display_name'])}"
        resource_policy.check_artifact_destination(ops.context["fleet"].config.host(host), clone, worktree, branch, relative,
                                                   published=published_binding is not None)
        mat = "mat_" + manifest_digest([ctx.operation_id, ref["artifact_id"], ref["revision"]])[:32]
        with ops.journal.tx():
            ops.db.execute("""INSERT OR IGNORE INTO artifact_materializations(materialization_id,operation_id,
                artifact_id,revision,digest,host,managed_path,updated_at) VALUES(?,?,?,?,?,?,?,?)""",
                           (mat, ctx.operation_id, ref["artifact_id"], ref["revision"], ref["digest"], host, worktree + "/" + relative, time.time()))
        row = dict(ops.db.execute("SELECT * FROM artifact_materializations WHERE materialization_id=?", (mat,)).fetchone())
        if row["state"] == "verified":
            continue
        if row["state"] in {"pending", "blocked"}:
            row["attempt"] += 1
            ops.db.execute("UPDATE artifact_materializations SET attempt=?,state='transferring' WHERE materialization_id=?", (row["attempt"], mat))
        request = {"clone": clone, "worktree": worktree, "operation_id": ctx.operation_id, "ref": ref,
                   "name": revision["display_name"], "attempt": row["attempt"], "size_bytes": revision["size_bytes"]}
        if published_binding is not None:
            request["published_binding"] = published_binding

        async def transfer(request=request, ref=ref):
            try:
                content = store.read_content(ref["artifact_id"], ref["revision"])
            except OperationError as exc:
                return {"ok": False, "code": exc.code, "source": "store", "observed_at": time.time()}
            if before_transfer is not None:
                await before_transfer()
            evidence = await adapter.call(host, {**request, "mode": "receive"}, content)
            if evidence.get("uncertain"):
                from .operations import AmbiguousOutcome

                raise AmbiguousOutcome("artifact receiver is not proven closed")
            return evidence

        async def inspect(_saved, request=request):
            evidence = await adapter.call(host, {**request, "mode": "inspect"})
            if evidence.get("uncertain"):
                return None
            return evidence

        try:
            transferred = await ctx.step(f"artifact.{mat}.transfer.{row['attempt']}", transfer, request=request, reconcile=inspect)
            if transferred.get("uncertain"):
                from .operations import AmbiguousOutcome

                raise AmbiguousOutcome("artifact receiver is not proven closed")
            code = _materialization_error(transferred, request)
            if code:
                _material_state(ctx, mat, "blocked", {**transferred, "code": code})
                raise NeedsAttention(code, "materialization failed; resume this continuation after correcting the cause")

            async def readback(request=request):
                return await adapter.call(host, {**request, "mode": "verify"})

            async def reread(_saved):
                return RERUN

            verified = await ctx.step(f"artifact.{mat}.readback.{row['attempt']}", readback, reconcile=reread)
            code = _materialization_error(verified, request)
            if code:
                _material_state(ctx, mat, "blocked", {**verified, "code": code})
                raise NeedsAttention(code, "materialization read-back differs; no task command was sent")
            _material_state(ctx, mat, "verified", verified)
        except Exception as exc:
            from .operations import AmbiguousOutcome, Uncertain

            if isinstance(exc, (AmbiguousOutcome, Uncertain)):
                _material_state(ctx, mat, "uncertain")
            raise
    ctx.set_refs(materializations=materializations(ops.db, operation_id=ctx.operation_id))


async def dispatch_guard(ctx, checkpoint, clone, worktree, branch, refs):
    if ctx.service.db.execute("SELECT 1 FROM operation_steps WHERE operation_id=? AND name='send'", (ctx.operation_id,)).fetchone():
        return  # the existing send must reconcile, even if an agent has since changed HEAD/inputs
    await source_guard(ctx, checkpoint)
    fleet = ctx.service.context["fleet"]
    client = fleet.client(checkpoint["host"])
    root = resource_policy.norm(await client.invoke("git:getRoot", {"cwd": worktree}))
    log = await client.invoke("git:log", {"cwd": worktree, "count": 1})
    if root != worktree or not log or log[0].get("hash") != checkpoint["commit_sha"]:
        raise NeedsAttention("START_MISMATCH", "target start changed before the first command")
    await verify_materializations(ctx, checkpoint, clone, worktree, branch)


async def verify_materializations(ctx, checkpoint, clone, worktree, branch, *, published_binding=None):
    fleet = ctx.service.context["fleet"]
    adapter = ctx.service.context["artifact_host"]
    for row in materializations(ctx.service.db, operation_id=ctx.operation_id):
        revision = get(ctx.service.db, row["artifact_id"], row["revision"])
        ref = {"artifact_id": row["artifact_id"], "revision": row["revision"], "digest": row["digest"]}
        relative = f".batc-inputs/{row['artifact_id']}-r{row['revision']}/{revision['display_name']}"
        resource_policy.check_artifact_destination(fleet.config.host(checkpoint["host"]), clone, worktree, branch, relative,
                                                   published=published_binding is not None)
        request = {"clone": clone, "worktree": worktree, "operation_id": ctx.operation_id, "ref": ref,
                   "name": revision["display_name"], "attempt": row["attempt"], "size_bytes": revision["size_bytes"]}
        if published_binding is not None:
            request["published_binding"] = published_binding
        evidence = await adapter.call(checkpoint["host"], {**request, "mode": "verify"})
        code = _materialization_error(evidence, request)
        _material_state(ctx, row["materialization_id"], "blocked" if code else "verified", {**evidence, **({"code": code} if code else {})})
        if code:
            raise NeedsAttention(code, "input changed after start; keep this session and resume only when verified")
    ctx.set_refs(dispatch_verified_at=time.time())
