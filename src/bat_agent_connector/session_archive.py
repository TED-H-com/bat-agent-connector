"""Durable conversation evidence, incremental backfill and source-grounded excerpts.

The Connector owns these records. Neither a saved message nor a derived brief is
execution evidence or permission to resume an external session.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections import Counter

from . import service
from .fleet import Fleet
from .operations import OperationError
from .summarize import summarize_message

INTERVAL = 60
READ_TIMEOUT = 30
PAGE_SIZE = 200
MAX_BYTES = 2 * 1024 * 1024
MAX_SESSION_BYTES = 256 * 1024 * 1024
MAX_TEXT = 16000
MAX_MESSAGES = 20000


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def normalize(messages):
    """Preserve code/text; absent or duplicate native IDs are omissions, never UUIDs."""
    counts = Counter(m.get("id") for m in messages if isinstance(m, dict) and isinstance(m.get("id"), str))
    rows, size, omitted = [], 0, 0
    for m in reversed(messages):
        if not isinstance(m, dict):
            omitted += 1
            continue
        content = m.get("content") if "content" in m else m.get("text")
        blocks = content if isinstance(content, list) else [content]
        # Retain only explicit public text. The live renderer's generic dict
        # fallback can stringify private reasoning or tool payloads.
        text = "\n".join(b if isinstance(b, str) else b["text"] for b in blocks
                         if isinstance(b, str) or isinstance(b, dict) and isinstance(b.get("text"), str)
                         and b.get("type") in (None, "text", "output_text", "input_text"))
        try:
            full = summarize_message({**m, "content": text}, include_tools=False, max_chars=2**31)
        except (ValueError, OverflowError, TypeError, OSError):
            omitted += 1
            continue
        if not full or full["role"] not in {"user", "assistant"}:
            continue
        mid = full.get("id")
        if (not isinstance(mid, str) or not mid or len(mid) > 512 or counts[mid] != 1
                or any(ord(c) < 32 for c in mid)):
            omitted += 1
            continue
        row = {**full, "text": full["text"][:MAX_TEXT], "clipped": len(full["text"]) > MAX_TEXT,
               "source_digest": digest(full)}
        cost = len(encode(row).encode())
        if size + cost > MAX_BYTES:
            omitted += 1
            continue
        size += cost
        rows.append({**row, "revision": digest(row)})
    rows.reverse()
    return rows, omitted


def merge_members(previous, rows, *, older=False):
    """Merge overlapping chronological source windows without dropping old members."""
    ordered = [item[0] for item in previous]
    revisions = dict(previous)
    # Place unseen runs using their known neighbours. An overlapping older page
    # may also contain a new tail; "older" only chooses the side without anchors.
    run, left = [], None
    for row in rows:
        mid = row["id"]
        if mid in revisions:
            if run:
                at = ordered.index(mid)
                ordered[at:at] = run
                run = []
            left = mid
        else:
            run.append(mid)
        revisions[mid] = row["revision"]
    if run:
        at = ordered.index(left) + 1 if left is not None else (0 if older else len(ordered))
        ordered[at:at] = run
    return [[mid, revisions[mid]] for mid in ordered]


def brief(rows, snapshot_id):
    # These labels describe exact excerpts, not inferred goals/decisions/completion.
    items = []
    for kind, role in (("latest_request", "user"), ("latest_response", "assistant")):
        row = next((m for m in reversed(rows) if m["role"] == role), None)
        if row:
            items.append({"kind": kind, "message_id": row["id"], "revision": row["revision"],
                          "text": row["text"][:800], "clipped": row["clipped"] or len(row["text"]) > 800})
    return {"kind": "extractive", "extractor_version": "session-excerpts/v1", "source_snapshot": snapshot_id,
            "execution_status": "unknown", "model": None, "items": items}


class SessionArchive:
    def __init__(self, daemon):
        self.daemon = daemon
        self.journal = daemon.journal
        self.db = self.journal.db
        with self.journal.tx():
            self.db.execute("""CREATE TABLE IF NOT EXISTS session_archive_sources (
                host TEXT NOT NULL, session_id TEXT NOT NULL, binding TEXT NOT NULL,
                checked_at REAL NOT NULL, saved_at REAL, status TEXT NOT NULL, latest TEXT, cursor TEXT NOT NULL,
                PRIMARY KEY(host,session_id,binding))""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS session_archive_messages (
                host TEXT NOT NULL, session_id TEXT NOT NULL, binding TEXT NOT NULL,
                message_id TEXT NOT NULL, revision TEXT NOT NULL, body TEXT NOT NULL,
                PRIMARY KEY(host,session_id,binding,message_id,revision))""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS session_archive_snapshots (
                host TEXT NOT NULL, session_id TEXT NOT NULL, binding TEXT NOT NULL,
                digest TEXT NOT NULL, saved_at REAL NOT NULL, document TEXT NOT NULL, brief TEXT NOT NULL,
                PRIMARY KEY(host,session_id,binding,digest))""")
            for table in ("session_archive_messages", "session_archive_snapshots"):
                for verb in ("UPDATE", "DELETE"):
                    self.db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_{verb.lower()}_immutable "
                        f"BEFORE {verb} ON {table} BEGIN SELECT RAISE(ABORT, 'Session archive is immutable'); END")

    def identity(self, host, sid):
        inventory = self.daemon.inventory
        if host not in inventory.config.hosts:
            raise OperationError("NOT_FOUND", "Saved conversation source is unavailable", 404)
        host_binding = inventory._binding(host)
        scopes = self.db.execute("SELECT binding FROM discovery_latest WHERE host=?", (host,)).fetchall()
        if not scopes or any(r[0] != host_binding for r in scopes):
            raise OperationError("NOT_FOUND", "Saved conversation source changed", 404)
        row = self.db.execute("SELECT body FROM sessions_observed WHERE host=? AND session_id=?", (host, sid)).fetchone()
        if not row:
            raise OperationError("NOT_FOUND", "Session identity has not been observed", 404)
        observed = json.loads(row[0])
        identity = {"host_binding": host_binding, "native_session_id": observed.get("provider_native_id"),
                    "creation": observed.get("registry_origin"), "agent_kind": observed.get("agent_kind")}
        if not identity["native_session_id"]:
            # Missing runtime metadata is not evidence that a carrier changed.
            # Keep its last proven identity only under the same explicit source
            # and creation binding; a newly observed native ID never takes this path.
            for saved in self.db.execute("""SELECT p.document FROM session_archive_sources s
                    JOIN session_archive_snapshots p ON p.host=s.host AND p.session_id=s.session_id
                    AND p.binding=s.binding AND p.digest=s.latest WHERE s.host=? AND s.session_id=?
                    ORDER BY s.saved_at DESC LIMIT 1""", (host, sid)):
                previous = json.loads(saved[0])["identity"]
                if all(previous.get(k) == identity[k] for k in ("host_binding", "creation", "agent_kind")):
                    identity["native_session_id"] = previous.get("native_session_id")
        return digest(identity), identity

    def source(self, key):
        return self.db.execute("SELECT * FROM session_archive_sources WHERE host=? AND session_id=? AND binding=?", key).fetchone()

    def current(self, key):
        try:
            return self.journal.owner_valid() and self.identity(*key[:2])[0] == key[2]
        except OperationError:
            return False

    def failure(self, key, status="source_unavailable"):
        if not self.current(key):
            return
        with self.journal.tx():
            self.db.execute("""INSERT INTO session_archive_sources VALUES(?,?,?,?,NULL,?,NULL,'{}')
                ON CONFLICT(host,session_id,binding) DO UPDATE SET checked_at=excluded.checked_at,status=excluded.status""",
                            (*key, time.time(), status))

    def _rows(self, key, members):
        rows = []
        for mid, revision in members:
            row = self.db.execute("SELECT body FROM session_archive_messages WHERE host=? AND session_id=? AND binding=? AND message_id=? AND revision=?",
                                  (*key, mid, revision)).fetchone()
            if not row:
                raise OperationError("ARCHIVE_INCOMPLETE", "Saved message reference is unavailable", 409)
            rows.append({**json.loads(row[0]), "revision": revision})
        return rows

    def save(self, key, batches, cursor, coverage, identity):
        """Body revisions, manifest, brief and successful cursor share one commit."""
        if not self.current(key):
            return
        source = self.source(key)
        old = self.db.execute("SELECT document FROM session_archive_snapshots WHERE host=? AND session_id=? AND binding=? AND digest=?",
                              (*key, source["latest"])).fetchone() if source and source["latest"] else None
        previous = json.loads(old[0]) if old else {"members": []}
        members = previous["members"]
        incoming = {}
        for rows, older in batches:
            members = merge_members(members, rows, older=older)
            incoming.update(((m["id"], m["revision"]), m) for m in rows)
        if len(members) > MAX_MESSAGES:
            self.failure(key, "storage_limit")
            return
        document = {"members": members, "coverage": coverage, "identity": identity}
        raw = encode(document)
        snapshot_id = digest(document)
        at = time.time()
        with self.journal.tx():
            additions = []
            for (mid, revision), m in incoming.items():
                if not self.db.execute("SELECT 1 FROM session_archive_messages WHERE host=? AND session_id=? AND binding=? AND message_id=? AND revision=?",
                                       (*key, mid, revision)).fetchone():
                    additions.append((*key, mid, revision, encode({k: v for k, v in m.items() if k != "revision"})))
            existing = self.db.execute("SELECT 1 FROM session_archive_snapshots WHERE host=? AND session_id=? AND binding=? AND digest=?",
                                       (*key, snapshot_id)).fetchone()
            size = self.db.execute("SELECT COALESCE(SUM(length(CAST(body AS BLOB))),0) FROM session_archive_messages WHERE host=? AND session_id=? AND binding=?", key).fetchone()[0]
            size += self.db.execute("SELECT COALESCE(SUM(length(CAST(document AS BLOB)) + length(CAST(brief AS BLOB))),0) FROM session_archive_snapshots WHERE host=? AND session_id=? AND binding=?", key).fetchone()[0]
            if size + sum(len(a[-1].encode()) for a in additions) + (len(raw.encode()) + 12000 if not existing else 0) > MAX_SESSION_BYTES:
                self.failure(key, "storage_limit")
                return
            self.db.executemany("INSERT INTO session_archive_messages VALUES(?,?,?,?,?,?)", additions)
            if not existing:
                # Latest role excerpts only require a bounded reverse scan of references.
                latest = []
                roles = set()
                for member in reversed(members):
                    m = self._rows(key, [member])[0]
                    if m["role"] not in roles:
                        roles.add(m["role"])
                        latest.insert(0, m)
                    if len(roles) == 2:
                        break
                self.db.execute("INSERT INTO session_archive_snapshots VALUES(?,?,?,?,?,?,?)",
                                (*key, snapshot_id, at, raw, encode(brief(latest, snapshot_id))))
            self.db.execute("""INSERT INTO session_archive_sources VALUES(?,?,?,?,?,'saved',?,?)
                ON CONFLICT(host,session_id,binding) DO UPDATE SET checked_at=excluded.checked_at,
                saved_at=excluded.saved_at,status=excluded.status,latest=excluded.latest,cursor=excluded.cursor""",
                            (*key, at, at, snapshot_id, encode(cursor)))

    async def _page(self, client, sid, offset, limit=PAGE_SIZE):
        result = await client.invoke("claude:load-archived", {"sessionId": sid, "offset": offset, "limit": limit})
        if (not isinstance(result, dict) or not isinstance(result.get("messages"), list)
                or type(result.get("hasMore")) is not bool or type(result.get("total")) is not int
                or result["total"] < 0
                or len(result["messages"]) != min(limit, max(result["total"] - offset, 0))
                or result["hasMore"] != (result["total"] > offset + len(result["messages"]))):
            raise ValueError("Unconfirmed archive page")
        return result

    async def _archive_pass(self, client, sid, cursor, evidence):
        head = await self._page(client, sid, 0)
        head_rows, head_omitted = normalize(head["messages"])
        evidence.update(batches=[(head_rows, False)], omitted=head_omitted,
                        clipped=sum(m["clipped"] for m in head_rows))
        offset, complete = 0, False
        anchor = cursor.get("anchor")
        shifted, anchor_rows = None, []
        if anchor and cursor.get("next_offset", 0) > 0:
            # Offsets count backwards from the newest row. A head-sized append
            # is covered by this head; larger appends restart the bounded scan so
            # the gap between the new head and the old anchor cannot be skipped.
            delta = head["total"] - cursor["total"]
            anchor_offset = anchor["offset"] + delta
            if 0 <= delta <= len(head["messages"]) and anchor_offset >= 0:
                check = await self._page(client, sid, anchor_offset, anchor["count"])
                if check["total"] == head["total"] and digest(check["messages"]) == anchor["digest"]:
                    offset = cursor["next_offset"] + delta
                    complete = cursor.get("complete", False)
                    shifted = {**anchor, "offset": anchor_offset}
                    anchor_rows, _ = normalize(check["messages"])
        page = None if complete else (await self._page(client, sid, offset) if offset else head)
        if page is not None and page["total"] != head["total"]:
            raise ValueError("Archive changed during collection")
        pages = [(page, True)] if page is not None and offset else []
        pages.append((head, False))
        batches, omitted, clipped = [], 0, 0
        for part, older in pages:
            rows, missing = (head_rows, head_omitted) if part is head else normalize(part["messages"])
            # Include the verified newer neighbour as an ordering anchor. After
            # a large append/reset an older page can sit between two saved runs.
            batches.append((rows + anchor_rows if older else rows, older))
            omitted += missing
            clipped += sum(m["clipped"] for m in rows)
        pending = page["hasMore"] if page is not None else False
        next_cursor = {"next_offset": offset + len(page["messages"]) if page is not None else offset,
                       "total": head["total"], "complete": not pending,
                       "anchor": {"offset": offset, "count": len(page["messages"]),
                                  "digest": digest(page["messages"])} if page is not None else shifted}
        return batches, next_cursor, pending, omitted, clipped

    async def capture(self, host, sid):
        binding, identity = self.identity(host, sid)
        key = host, sid, binding
        if not self.current(key):
            return
        source = self.source(key)
        cursor = json.loads(source["cursor"]) if source else {}
        fleet = Fleet(self.daemon.inventory.config, read_only=True, idle_timeout=0, actor="session-archive")
        fleet.confinement_journal = self.journal
        try:
            async def collect():
                client = fleet.client(host)
                # Preserve the safe-state guard. History never resumes a session.
                meta = await asyncio.wait_for(service._meta(client, sid), READ_TIMEOUT / 12)
                native = (meta or {}).get("sdkSessionId")
                if native and native != identity["native_session_id"]:
                    self.failure(key, "source_changed")
                    return
                live = []
                if service._state_safe(identity["agent_kind"], meta):
                    try:
                        state = await asyncio.wait_for(
                            client.invoke("claude:get-session-state", {"sessionId": sid}), READ_TIMEOUT / 6)
                        if isinstance(state, dict) and isinstance(state.get("messages"), list):
                            live = state["messages"]
                    except Exception:
                        live = []  # A gone live terminal does not prevent archive reads.
                confirmed = True
                evidence = {"batches": [], "omitted": 0, "clipped": 0}
                try:
                    batches, next_cursor, pending, omitted, clipped = await asyncio.wait_for(
                        self._archive_pass(client, sid, cursor, evidence), READ_TIMEOUT * 2 / 3)
                except Exception:
                    # Successful live evidence is independent of an unavailable or
                    # unsupported archive. Its failure must never move the cursor.
                    batches, next_cursor, pending = evidence["batches"], cursor, True
                    omitted, clipped = evidence["omitted"], evidence["clipped"]
                    confirmed = False
                final_meta = await asyncio.wait_for(service._meta(client, sid), READ_TIMEOUT / 12)
                if (final_meta or {}).get("sdkSessionId") != native:
                    raise ValueError("Native session changed during collection")
                live_rows, live_omitted = normalize(live[-1000:])
                if not confirmed and not live_rows and not any(rows for rows, _ in batches):
                    self.failure(key)
                    return
                batches.append((live_rows, False))
                coverage = {"scope": "observed_user_assistant_text", "history_complete": False,
                            "atomic_source_snapshot": False, "source_end_observed": confirmed and not pending,
                            "reason": "archive_unconfirmed" if not confirmed else ("backfill_pending" if pending else None),
                            "omitted_messages": omitted + live_omitted + max(0, len(live)-1000),
                            "clipped_messages": clipped + sum(m["clipped"] for m in live_rows),
                            "counts_basis": "latest_capture_pass_source_occurrences",
                            "generation_known": bool(native or identity["native_session_id"])}
                self.save(key, batches, next_cursor, coverage, identity)
            await asyncio.wait_for(collect(), READ_TIMEOUT)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.failure(key)
        finally:
            await fleet.close()

    async def collect_host(self, host):
        # Include historical identities: absent from enumeration does not mean no archive.
        # Use the last attempt across incarnations only for fairness, never for content identity.
        rows = self.db.execute("""SELECT s.session_id FROM sessions_observed s
            LEFT JOIN (SELECT host,session_id,MAX(checked_at) AS checked_at FROM session_archive_sources GROUP BY host,session_id) a
            ON a.host=s.host AND a.session_id=s.session_id WHERE s.host=?
            ORDER BY COALESCE(a.checked_at,0),s.session_id LIMIT 4""", (host,)).fetchall()
        for row in rows:
            if not self.journal.owner_valid():
                return
            await self.capture(host, row[0])

    async def loop(self):
        running = {}
        try:
            while True:
                for host in list(self.daemon.inventory.config.hosts):
                    if host not in running or running[host].done():
                        if host in running:
                            try:
                                running[host].result()
                            except Exception:
                                logging.getLogger(__name__).warning("Session archive pass failed; saved history retained")
                        running[host] = asyncio.create_task(self.collect_host(host))
                await asyncio.sleep(INTERVAL)
        finally:
            for task in running.values():
                task.cancel()
            await asyncio.gather(*running.values(), return_exceptions=True)

    def read(self, principal, host, session_id, *, snapshot_id=None, offset=0, last_n=20,
             max_chars=12000, max_message_chars=2000, **_):
        from .pm_source_lineage import require_session
        if not principal.allows("observe"):
            raise OperationError("FORBIDDEN", "Saved conversation needs observe scope", 403)
        binding, _ = self.identity(host, session_id)
        key = host, session_id, binding
        # Exact identity and current source authorization, including every derived read.
        require_session(self.db, principal, host, session_id)
        source = self.source(key)
        selected = snapshot_id or (source["latest"] if source else None)
        saved = self.db.execute("SELECT * FROM session_archive_snapshots WHERE host=? AND session_id=? AND binding=? AND digest=?",
                                (*key, selected)).fetchone()
        if not saved:
            raise OperationError("ARCHIVE_NOT_FOUND", "No saved conversation for this source and session", 404)
        doc = json.loads(saved["document"])
        messages, used = [], 0
        max_message_chars = min(max_message_chars, max_chars)
        for row in self._rows(key, list(reversed(doc["members"]))[offset:offset + last_n]):
            row = {**row, "text": row["text"][:max_message_chars],
                   "clipped": row["clipped"] or len(row["text"]) > max_message_chars}
            cost = len(row["text"]) + 80
            if messages and used + cost > max_chars:
                break
            used += cost
            messages.append(row)
        messages.reverse()
        return {"host": host, "session_id": session_id, "source": "retained", "snapshot_id": selected,
                "saved_at": saved["saved_at"], "last_checked_at": source["checked_at"], "source_status": source["status"],
                "execution_status": "unknown", "coverage": doc["coverage"], "brief": json.loads(saved["brief"]),
                "messages": messages, "offset": offset,
                "next_offset": offset + len(messages) if offset + len(messages) < len(doc["members"]) else None,
                "snapshots": [{"snapshot_id": r["digest"], "saved_at": r["saved_at"]} for r in self.db.execute(
                    "SELECT digest,saved_at FROM session_archive_snapshots WHERE host=? AND session_id=? AND binding=? "
                    "ORDER BY saved_at DESC,digest LIMIT 50", key)]}
