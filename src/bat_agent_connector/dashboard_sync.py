"""Dashboard bootstrap and replay continuity over the existing authoritative api_events log."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import uuid


class ResetRequired(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)

    def document(self):
        return {"error": {"code": "EVENT_CURSOR_RESET", "reason": self.reason,
                          "message": "Reload the bootstrap snapshot before replaying events; preserve local drafts.",
                          "resnapshot": True, "preserve_drafts": True}}


def install(journal):
    """Versionless metadata only; no new event authority or retention/pruning policy."""
    with journal.tx():
        journal.db.execute("""CREATE TABLE IF NOT EXISTS api_sync_metadata (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1), server_id TEXT NOT NULL,
            signing_key TEXT NOT NULL, retained_after INTEGER NOT NULL DEFAULT 0)""")
        journal.db.execute("INSERT OR IGNORE INTO api_sync_metadata(singleton,server_id,signing_key) VALUES(1,?,?)",
                           (str(uuid.uuid4()), secrets.token_hex(32)))
        journal.db.execute("""CREATE TRIGGER IF NOT EXISTS api_sync_deleted AFTER DELETE ON api_events
            BEGIN UPDATE api_sync_metadata SET retained_after=MAX(retained_after,OLD.seq) WHERE singleton=1; END""")


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _b64(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _metadata(journal):
    row = journal.db.execute("SELECT * FROM api_sync_metadata WHERE singleton=1").fetchone()
    if row is None:
        raise ResetRequired("identity_unavailable")
    return row


def identity(journal, principal):
    scope = {"actor": principal.actor, "scopes": sorted(principal.scopes), "admin": principal.admin}
    return {"server_id": _metadata(journal)["server_id"],
            "principal_id": hashlib.sha256(_canonical(scope)).hexdigest()}


def _window(journal):
    oldest = journal.db.execute("SELECT MIN(seq) FROM api_events").fetchone()[0]
    head = journal.api_head()
    return {"head_cursor": head,
            "retained_after": max(_metadata(journal)["retained_after"], (oldest - 1) if oldest else head)}


def _anchor(journal, cursor):
    row = journal.db.execute("""SELECT seq,resource_type,resource_id,kind,body,actor,task_event_id,created_at
        FROM api_events WHERE seq<=? ORDER BY seq DESC LIMIT 1""", (cursor,)).fetchone()
    return [row["seq"], hashlib.sha256(_canonical(dict(row))).hexdigest()] if row else None


def check(journal, principal, after, token=None):
    if type(after) is not int or after < 0:
        raise ValueError("after must be a nonnegative integer")
    current = identity(journal, principal)
    if token is not None:
        try:
            if not isinstance(token, str) or len(token) > 2048:
                raise ValueError()
            version, raw, signature = token.split(".")
            payload = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
            claims = json.loads(payload)
            if version != "s1" or _b64(payload) != raw or _canonical(claims) != payload:
                raise ValueError()
            if claims.get("server_id") != current["server_id"]:
                raise ResetRequired("server_changed")
            expected = _b64(hmac.digest(bytes.fromhex(_metadata(journal)["signing_key"]), payload, "sha256"))
            if not hmac.compare_digest(expected, signature):
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise ResetRequired("checkpoint_invalid") from None
        if claims.get("principal_id") != current["principal_id"]:
            raise ResetRequired("principal_changed")
        if type(claims.get("cursor")) is not int or claims["cursor"] != after:
            raise ResetRequired("checkpoint_cursor_mismatch")
    bounds = _window(journal)
    if after > bounds["head_cursor"]:
        raise ResetRequired("cursor_ahead")
    if after < bounds["retained_after"]:
        raise ResetRequired("cursor_expired")
    if token is not None and claims.get("anchor") != _anchor(journal, after):
        raise ResetRequired("history_changed")
    return bounds


def checkpoint(journal, principal, cursor=None):
    bounds = _window(journal)
    cursor = bounds["head_cursor"] if cursor is None else cursor
    current = identity(journal, principal)
    check(journal, principal, cursor)
    payload = _canonical({**current, "cursor": cursor, "anchor": _anchor(journal, cursor)})
    signature = hmac.digest(bytes.fromhex(_metadata(journal)["signing_key"]), payload, "sha256")
    return {"version": 1, **current, **bounds,
            "checkpoint": {"cursor": cursor, "token": "s1." + _b64(payload) + "." + _b64(signature)}}


def require_unfiltered(token, **filters):
    """A shared checkpoint may acknowledge only the complete public event feed."""
    if token is not None and any(value is not None for value in filters.values()):
        raise ValueError("checkpoint replay requires the unfiltered event feed; omit event filters")


def event_page(journal, principal, after=0, limit=100, *, token=None, **filters):
    require_unfiltered(token, **filters)
    # These reads share one SQLite snapshot with validation, including other-process writers/pruning.
    with journal.tx():
        check(journal, principal, after, token)
        page = journal.api_events(after, limit, **filters)
        from .pm_access import filter_events
        page = filter_events(journal.db, principal, page)
        if token is not None:
            page["sync"] = checkpoint(journal, principal, page["next_cursor"])
        return page
