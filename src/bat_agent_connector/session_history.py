"""Server-owned, separately granted local history; no runtime or Work ownership."""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timezone

from .operations import OperationError
from .pm_external_history import current_grants, grant_schema
from .pm_workspace import server_id
from .session_history_config import HistoryConfig
from .session_history_decoder import decode
from .usage_adapters import UsageInvalid
from .usage_collector import now, read_page

MAX_ENTRIES = 20000
MAX_PAGE_BYTES = 512 * 1024
READABLE = {"current", "catching_up", "entry_limit"}


def schema(journal):
    with journal.tx():
        journal.db.execute("""CREATE TABLE IF NOT EXISTS session_history_sources (
            source_id TEXT PRIMARY KEY, binding TEXT NOT NULL, cursor TEXT NOT NULL,
            parser TEXT NOT NULL, status TEXT NOT NULL, checked_at TEXT, successful_at TEXT)""")
        journal.db.execute("""CREATE TABLE IF NOT EXISTS session_history_entries (
            sequence INTEGER NOT NULL, source_id TEXT NOT NULL,
            entry_id TEXT NOT NULL, content TEXT NOT NULL, content_digest TEXT NOT NULL,
            PRIMARY KEY(source_id,sequence), UNIQUE(source_id,entry_id))""")
        journal.db.execute("CREATE INDEX IF NOT EXISTS session_history_page ON session_history_entries(source_id,sequence)")
        for verb in ("UPDATE", "DELETE"):
            journal.db.execute(f"CREATE TRIGGER IF NOT EXISTS session_history_entries_{verb.lower()}_immutable "
                f"BEFORE {verb} ON session_history_entries BEGIN SELECT RAISE(ABORT,'History entries are immutable'); END")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))


class HistoryCollector:
    def __init__(self, journal, config):
        self.journal, self._config, self._generation = journal, HistoryConfig(), None
        self._lock = asyncio.Lock()
        schema(journal)
        # The callback is local to this SQLite connection, not a durable grant.
        journal.db.create_function('history_active_generation', 0, lambda: self._generation)
        self.config = config

    @property
    def config(self):
        return self._config

    @config.setter
    def config(self, value):
        generation = uuid.uuid4().hex
        with self.journal.tx():
            db = self.journal.db
            grant_schema(db)
            db.execute('DELETE FROM temp.session_history_current_grants')
            db.executemany('INSERT INTO temp.session_history_current_grants VALUES(?,?,?,?,?,?,?,?)',
                [(s.source_id, s.workspace_id, s.binding, actor, s.adapter, value.interval_s, position, generation)
                 for position, s in enumerate(value.sources) for actor in s.readers])
            db.execute('INSERT OR REPLACE INTO temp.session_history_grant_state VALUES(1,?)', (generation,))
        # Failed writes retain the previous config. An outer rollback leaves a
        # generation mismatch and refuses reads until a new activation succeeds.
        self._config, self._generation = value, generation

    async def collect_once(self):
        async with self._lock:
            generation = self._generation
            for source in self.config.sources:
                if not self.journal.owner_valid():
                    return
                db = self.journal.db
                if not source.readers or not db.execute("SELECT 1 FROM pm_workspaces WHERE workspace_id=?",
                        (source.workspace_id,)).fetchone():
                    continue
                old = db.execute("SELECT * FROM session_history_sources WHERE source_id=?", (source.source_id,)).fetchone()
                if old and old["binding"] != source.binding:
                    continue
                cursor, parser = (json.loads(old["cursor"]), json.loads(old["parser"])) if old else ({}, {})
                try:
                    started = now()
                    page = await asyncio.to_thread(read_page, source, cursor, parser, decoder=decode)
                    prepared = {}
                    for entry in page["samples"]:
                        identity = entry["entry_id"]
                        # A repeated original record may move within an append;
                        # its public content and original bytes must agree.
                        content = {k: v for k, v in entry.items() if k != "source_offset"}
                        digest = hashlib.sha256(_json(content).encode()).hexdigest()
                        saved = db.execute("SELECT content_digest FROM session_history_entries WHERE source_id=? AND entry_id=?",
                            (source.source_id, identity)).fetchone()
                        prior = saved[0] if saved else prepared.get(identity, (None, None))[1]
                        if prior is not None and prior != digest:
                            raise UsageInvalid("history_entry_conflict")
                        if prior is None:
                            prepared[identity] = (_json(entry), digest)
                    count = db.execute("SELECT COUNT(*) FROM session_history_entries WHERE source_id=?", (source.source_id,)).fetchone()[0]
                    if count + len(prepared) > MAX_ENTRIES:
                        raise UsageInvalid("entry_limit")
                    error = None
                except UsageInvalid as exc:
                    error = str(exc)
                except OSError:
                    error = "source_unavailable"
                # Source revocation during the file worker prevents every commit.
                if not self.journal.owner_valid() or generation != self._generation or source not in self.config.sources:
                    return
                with self.journal.tx():
                    db.execute("INSERT OR IGNORE INTO session_history_sources VALUES(?,?,?,?,?,?,?)",
                        (source.source_id, source.binding, "{}", "{}", "pending", None, None))
                    at = now()
                    if error:
                        db.execute("UPDATE session_history_sources SET status=?,checked_at=? WHERE source_id=?",
                            (error, at, source.source_id))
                        continue
                    for index, (identity, (content, digest)) in enumerate(prepared.items(), 1):
                        db.execute("INSERT INTO session_history_entries(sequence,source_id,entry_id,content,content_digest) VALUES(?,?,?,?,?)",
                            (count + index, source.source_id, identity, content, digest))
                    page["cursor"]["coverage_at"] = started
                    db.execute("UPDATE session_history_sources SET cursor=?,parser=?,status=?,checked_at=?,successful_at=? WHERE source_id=?",
                        (_json(page["cursor"]), _json(page["parser"]), page["status"], at, at, source.source_id))

    async def loop(self):
        while True:
            try:
                await self.collect_once()
            except Exception:
                import logging
                logging.getLogger(__name__).warning("History collection failed; original cursor retained")
            await asyncio.sleep(self.config.interval_s)


def capabilities():
    return {"version": 1, "grant": "explicit_local_file_and_readers", "read_history": True,
        "observe_live": False, "start_owned": False, "send_owned": False,
        "read_results": False, "cross_adapter_alias": False}


def _row(db, source):
    stored = db.execute("SELECT * FROM session_history_sources WHERE source_id=?", (source['source_id'],)).fetchone()
    same = stored is not None and stored["binding"] == source['binding']
    state = stored["status"] if same else "binding_changed" if stored else "pending"
    parser = json.loads(stored["parser"]) if same and state in READABLE else {}
    readable = same and state in READABLE and parser.get("identified") is True
    cursor = json.loads(stored["cursor"]) if readable else {}
    successful = stored["successful_at"] if same else None
    stale = bool(successful and (datetime.now(timezone.utc) - datetime.fromisoformat(successful)).total_seconds()
        > max(180, source['interval_s'] * 3))
    count = db.execute("SELECT COUNT(*) FROM session_history_entries WHERE source_id=?", (source['source_id'],)).fetchone()[0] if readable else None
    return {"source_id": source['source_id'], "adapter": source['adapter'], "host_id": server_id(db),
        "status": state, "stale": stale, "readable": readable, "entries": count,
        "checked_at": stored["checked_at"] if same else None, "collected_at": successful,
        "coverage": {"as_of": cursor.get("coverage_at"), "file_at_eof": state == "current",
            "history_complete": False, "omitted_records": parser.get("omitted_records", 0),
            "omitted_parts": parser.get("omitted_parts", 0), "clipped_records": parser.get("clipped_records", 0),
            "scope": "selected_file_plaintext_only"},
        "cli_version": parser.get("cli_version"), "ownership": "external_read_only",
        "work_id": None, "attempt_id": None, "execution_status": "unknown"}


def list_sources(ops, principal, workspace_id):
    with current_grants(ops.db, principal, workspace_id) as sources:
        return {"workspace_id": workspace_id, "rows": [_row(ops.db, s) for s in sources], "partial": False}


def read(ops, principal, workspace_id, source_id, *, after=0, limit=50):
    with current_grants(ops.db, principal, workspace_id) as sources:
        source = next((s for s in sources if s['source_id'] == source_id), None)
        if source is None:
            raise OperationError("NOT_FOUND", "History source is unavailable", 404)
        if type(after) is not int or not 0 <= after <= 2**53 - 1 or type(limit) is not int or not 1 <= limit <= 100:
            raise OperationError("INVALID_PARAMS", "History page requires an offset and limit of 1–100", 422)
        row = _row(ops.db, source)
        entries = ops.db.execute("SELECT sequence,content,content_digest FROM session_history_entries WHERE source_id=? AND sequence>? ORDER BY sequence LIMIT ?",
            (source_id, after, limit + 1)).fetchall() if row["readable"] else []
        selected, size = [], 0
        for saved in entries[:limit]:
            entry = {"sequence": saved["sequence"], **json.loads(saved["content"]), "content_digest": saved["content_digest"]}
            # Bound even transports that escape every non-ASCII character. A single
            # clipped record fits; pagination never loses the first omitted row.
            cost = len(json.dumps(entry, ensure_ascii=True).encode())
            if selected and size + cost > MAX_PAGE_BYTES:
                break
            selected.append(entry)
            size += cost
        more = len(entries) > len(selected)
        return {"workspace_id": workspace_id, "source": row,
            "entries": selected,
            "next_after": selected[-1]["sequence"] if more and selected else None,
            "partial": not row["readable"] or more}
