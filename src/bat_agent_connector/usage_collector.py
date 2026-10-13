"""Bounded host-local reads; cursor, normalized facts and dedup commit together."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import stat
from datetime import datetime, timezone

from .usage_adapters import UsageInvalid, parse

PAGE_BYTES = 2 * 1024 * 1024
LINE_BYTES = 1024 * 1024
PAGE_LINES = 2000


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def schema(journal):
    with journal.tx():
        journal.db.execute("""CREATE TABLE IF NOT EXISTS resource_usage_sources (
            source_id TEXT PRIMARY KEY, binding TEXT NOT NULL, cursor TEXT NOT NULL,
            parser TEXT NOT NULL, quota TEXT, checked_at TEXT, successful_at TEXT, status TEXT NOT NULL)""")
        journal.db.execute("""CREATE TABLE IF NOT EXISTS resource_usage_samples (
            source_id TEXT NOT NULL, sample_id TEXT NOT NULL, observed_at TEXT NOT NULL,
            value TEXT NOT NULL, PRIMARY KEY(source_id,sample_id))""")
        journal.db.execute("CREATE INDEX IF NOT EXISTS resource_usage_window ON resource_usage_samples(source_id,observed_at)")


def _hash(value):
    return hashlib.sha256(value).hexdigest()


def read_page(source, cursor, parser):
    """Worker thread has no journal access and returns allowlisted metadata only."""
    cursor, parser = dict(cursor), dict(parser)
    # Config authorizes one regular file; symlinks, pipes, devices and recursive scans are refused.
    if any(p.is_symlink() for p in (source.path, *source.path.parents)):
        raise UsageInvalid("unsafe_file")
    candidate = source.path.lstat()
    if not stat.S_ISREG(candidate.st_mode):
        raise UsageInvalid("unsafe_file")
    fd = os.open(source.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        identity = [info.st_dev, info.st_ino]
        if not stat.S_ISREG(info.st_mode) or (info.st_dev, info.st_ino) != (candidate.st_dev, candidate.st_ino):
            raise UsageInvalid("unsafe_file")
        initial_prefix_len = min(info.st_size, 256)
        initial_prefix = _hash(stream.read(initial_prefix_len))
        stream.seek(0)
        offset = cursor.get("offset", 0)
        if cursor and (cursor["identity"] != identity or info.st_size < offset):
            raise UsageInvalid("source_changed")
        prefix_len = cursor.get("prefix_len", 0)
        if prefix_len and _hash(stream.read(prefix_len)) != cursor["prefix"]:
            raise UsageInvalid("source_changed")
        stream.seek(max(0, offset - 256))
        if offset and _hash(stream.read(min(256, offset))) != cursor["anchor"]:
            raise UsageInvalid("source_changed")
        stream.seek(offset)
        values, rate = [], None
        consumed = lines = 0
        while consumed < PAGE_BYTES and lines < PAGE_LINES:
            line = stream.readline(LINE_BYTES + 1)
            if not line:
                break
            if len(line) > LINE_BYTES:
                raise UsageInvalid("line_limit")
            if not line.endswith(b"\n"):
                stream.seek(-len(line), os.SEEK_CUR)
                break
            consumed += len(line)
            lines += 1
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except (ValueError, RecursionError):
                raise UsageInvalid("invalid_json") from None
            value, incoming_rate = parse(record, source, parser)
            if value:
                values.append(value)
            if incoming_rate and (rate is None or incoming_rate["observed_at"] >= rate["observed_at"]):
                rate = incoming_rate
        end = stream.tell()
        stream.seek(0)
        prefix_len = min(end, 256)
        prefix = _hash(stream.read(prefix_len))
        stream.seek(max(0, end - 256))
        anchor = _hash(stream.read(min(256, end)))
        latest = os.fstat(stream.fileno())
        path_info = source.path.stat()
        stream.seek(0)
        if (latest.st_size < end or [path_info.st_dev, path_info.st_ino] != identity
                or _hash(stream.read(initial_prefix_len)) != initial_prefix
                or (latest.st_size == info.st_size and latest.st_mtime_ns != info.st_mtime_ns)):
            raise UsageInvalid("source_changed")
        return {"cursor": {"identity": identity, "offset": end, "prefix_len": prefix_len,
                           "prefix": prefix, "anchor": anchor}, "parser": parser, "samples": values,
                "quota": rate, "status": "catching_up" if latest.st_size > end else "current"}


class UsageCollector:
    def __init__(self, journal, config):
        self.journal, self.config = journal, config
        self._lock = asyncio.Lock()
        schema(journal)

    async def collect_once(self):
        async with self._lock:
            for source in self.config.sources:
                if not self.journal.owner_valid():
                    return
                db = self.journal.db
                if not db.execute("SELECT 1 FROM pm_workspaces WHERE workspace_id=?", (source.workspace_id,)).fetchone():
                    continue
                row = db.execute("SELECT * FROM resource_usage_sources WHERE source_id=?", (source.source_id,)).fetchone()
                if row and row["binding"] != source.binding:
                    # Changing a binding cannot transfer historical facts into another workspace.
                    continue
                cursor, parser = (json.loads(row["cursor"]), json.loads(row["parser"])) if row else ({}, {})
                try:
                    page = await asyncio.to_thread(read_page, source, cursor, parser)
                    if row and row["quota"] and page["quota"]:
                        old_rate = json.loads(row["quota"])
                        if page["quota"]["observed_at"] < old_rate["observed_at"]:
                            page["quota"] = None
                    prepared = self.prepare(source, page["samples"])
                    error = None
                except UsageInvalid as exc:
                    error = str(exc)
                except OSError:
                    error = "source_unavailable"
                if not self.journal.owner_valid() or source not in self.config.sources:
                    return
                with self.journal.tx():
                    db.execute("INSERT OR IGNORE INTO resource_usage_sources VALUES(?,?,?,?,?,?,?,?)",
                               (source.source_id, source.binding, "{}", "{}", None, None, None, "pending"))
                    at = now()
                    if error:
                        db.execute("UPDATE resource_usage_sources SET status=?,checked_at=? WHERE source_id=?",
                                   (error, at, source.source_id))
                        continue
                    for key, value in prepared.items():
                        db.execute("INSERT INTO resource_usage_samples VALUES(?,?,?,?) ON CONFLICT(source_id,sample_id) "
                                   "DO UPDATE SET value=excluded.value", (source.source_id, key, value["at"], json.dumps(value)))
                    db.execute("UPDATE resource_usage_sources SET cursor=?,parser=?,quota=COALESCE(?,quota),"
                               "checked_at=?,successful_at=?,status=? WHERE source_id=?",
                               (json.dumps(page["cursor"]), json.dumps(page["parser"]),
                                json.dumps(page["quota"]) if page["quota"] else None, at, at, page["status"], source.source_id))

    def prepare(self, source, samples):
        prepared = {}
        for incoming in samples:
            value = dict(incoming)
            key = _hash(value.pop("key").encode())
            row = self.journal.db.execute("SELECT value FROM resource_usage_samples WHERE source_id=? AND sample_id=?",
                                         (source.source_id, key)).fetchone()
            original = prepared.get(key) or (json.loads(row["value"]) if row else None)
            if original:
                if source.adapter == "codex-rollout-v1":
                    continue
                dimensions = ("actual_model", "requested_model", "effort", "cli_version")
                if any(original[k] != value[k] for k in dimensions):
                    raise UsageInvalid("conflicting_sample")
                for metric, number in original["metrics"].items():
                    incoming_number = value["metrics"][metric]
                    if number is not None and (incoming_number is None or incoming_number < number):
                        raise UsageInvalid("conflicting_sample")
                value["at"] = original["at"]
            prepared[key] = value
        return prepared

    async def loop(self):
        while True:
            try:
                await self.collect_once()
            except Exception:  # no file contents/path in diagnostics; keep the next bounded poll alive
                import logging
                logging.getLogger(__name__).warning("Usage collection failed; original cursor retained")
            await asyncio.sleep(self.config.interval_s)
