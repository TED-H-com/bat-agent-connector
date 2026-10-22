"""Principals for /api/v1: who is calling, and what they may do.

The daemon's admin token (task-admin.token) is the local operator. Every other client (the Dashboard, Hermes,
Grokbot) gets its own API token with a fixed actor name and scopes; only the token's SHA-256 is stored. The actor
on an operation always comes from the token, never from a request body.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass, field

# observe: read inventory, operations, events and policy
# operate: drive connector-managed sessions (send, answer, interrupt)
# start: start new connector-managed agent sessions (checkpoint continue); a separate grant, because a new
#   session runs an agent with the caller's instructions on a host
# manage: change connector management data (projects, work items, links; tokens are admin-only)
# approve: accept a work item as done. Separate from manage, so an agent that edits work items and claims them
#   done cannot also sign off its own claim
# merge / deploy: GitHub delivery actions
# integrate: push composed results to a PR's head branch (never merges; a merge token cannot push)
SCOPES = ("observe", "operate", "start", "manage", "approve", "merge", "deploy", "integrate", "cleanup", "cleanup_discard")
ADMIN_ACTOR = "local-admin"
_ACTOR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")


@dataclass(frozen=True)
class Principal:
    actor: str
    scopes: frozenset[str]
    admin: bool = False
    # Internal credential identity, never a bearer secret or a public actor field.
    credential_id: str | None = field(default=None, repr=False, compare=False)

    def allows(self, scope: str) -> bool:
        return self.admin or scope in self.scopes


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue(db, actor: str, scopes: list[str] | tuple[str, ...], *, label: str | None = None,
          ttl_s: float | None = None) -> str:
    """Create an API token. Returns the token once; only its hash is kept."""
    if not isinstance(actor, str) or not _ACTOR_RE.match(actor) or actor == ADMIN_ACTOR:
        raise ValueError("actor must be 1-64 letters, digits or ._:- and not the admin actor")
    wanted = sorted(set(scopes))
    if not wanted or any(s not in SCOPES for s in wanted):
        raise ValueError(f"scopes must be a non-empty subset of {', '.join(SCOPES)}")
    if ttl_s is not None and not 60 <= float(ttl_s) <= 366 * 86400:
        raise ValueError("ttl_s must be between 60 seconds and one year")
    token = "batc_" + secrets.token_urlsafe(32)
    now = time.time()
    db.execute("""INSERT INTO api_principals(token_hash,actor,scopes,label,created_at,expires_at)
        VALUES(?,?,?,?,?,?)""", (_digest(token), actor, json.dumps(wanted), (label or "")[:100] or None, now,
                                  now + float(ttl_s) if ttl_s else None))
    return token


def revoke(db, actor: str) -> int:
    """Revoke every live token of an actor; returns how many were revoked."""
    cur = db.execute("UPDATE api_principals SET revoked_at=? WHERE actor=? AND revoked_at IS NULL",
                     (time.time(), actor))
    return cur.rowcount


def list_principals(db) -> list[dict]:
    rows = db.execute("""SELECT actor,scopes,label,created_at,expires_at,revoked_at FROM api_principals
        ORDER BY created_at""").fetchall()
    return [{"actor": r["actor"], "scopes": json.loads(r["scopes"]), "label": r["label"],
             "created_at": r["created_at"], "expires_at": r["expires_at"], "revoked_at": r["revoked_at"]}
            for r in rows]


def authenticate(db, token: str, admin_token: str, *, browser_session: bool = False) -> Principal | None:
    """Authenticate an API bearer, or a session already checked by the cookie adapter."""
    if not token:
        return None
    session = db.execute("SELECT parent_hash,expires_at FROM browser_sessions WHERE token_hash=?",
                         (_digest(token),)).fetchone()
    if browser_session:
        if not session or session["expires_at"] <= time.time():
            return None
        parent = db.execute("SELECT expires_at,revoked_at FROM api_principals WHERE token_hash=?",
                            (session["parent_hash"],)).fetchone()
        if not parent or parent["revoked_at"] is not None or (parent["expires_at"] and parent["expires_at"] <= time.time()):
            return None
    elif session:
        return None
    if hmac.compare_digest(token, admin_token):
        return Principal(ADMIN_ACTOR, frozenset(SCOPES), admin=True, credential_id=_digest(token))
    row = db.execute("SELECT actor,scopes,expires_at,revoked_at FROM api_principals WHERE token_hash=?",
                     (_digest(token),)).fetchone()
    if not row or row["revoked_at"] is not None or (row["expires_at"] and row["expires_at"] <= time.time()):
        return None
    return Principal(row["actor"], frozenset(json.loads(row["scopes"])), credential_id=_digest(token))
