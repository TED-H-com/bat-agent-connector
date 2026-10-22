"""Owned local browser entry: one-use POST handoff, HttpOnly session and exact-origin CSRF.

Only a managed installation enables this adapter. Bearer API clients retain their
existing contract. Browser credentials never enter a URL, JavaScript or web storage.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import secrets
import time
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlsplit

from . import api_auth, dashboard_sync
from .operations import OperationError

SESSION_S = 12 * 3600
HANDOFF_S = 60


class BrowserSessions:
    def __init__(self, daemon, data_dir, installation_id):
        self.daemon, self.data_dir = daemon, data_dir
        self.cookie_name = "batc_" + hashlib.sha256(installation_id.encode()).hexdigest()[:20]
        with daemon.journal.tx():
            daemon.journal.db.execute("""CREATE TABLE IF NOT EXISTS browser_handoffs (
                digest TEXT PRIMARY KEY, actor TEXT NOT NULL, scopes TEXT NOT NULL,
                parent_hash TEXT NOT NULL, expires_at REAL NOT NULL, filename TEXT NOT NULL)""")

    @property
    def origin(self):
        endpoint = self.daemon._endpoint
        if not endpoint:
            raise OperationError("TASK_OWNER_UNAVAILABLE", "task owner is not listening", 503)
        return endpoint.removesuffix("/rpc")

    def _parent_valid(self, digest):
        row = self.daemon.journal.db.execute(
            "SELECT expires_at,revoked_at FROM api_principals WHERE token_hash=?", (digest,)).fetchone()
        return bool(row and row["revoked_at"] is None and
                    (row["expires_at"] is None or row["expires_at"] > time.time()))

    def create_handoff(self, principal):
        from .platform_files import atomic_write, ensure_private_directory
        if principal.admin or not principal.credential_id or not self._parent_valid(principal.credential_id):
            raise OperationError("FORBIDDEN", "browser entry requires a current personal identity", 403)
        folder = self.data_dir / "browser-handoffs"
        ensure_private_directory(folder)
        now = time.time()
        db = self.daemon.journal.db
        # Only files whose exact names were minted by this service are removed.
        for row in db.execute("SELECT filename FROM browser_handoffs WHERE expires_at<=?", (now,)).fetchall():
            (folder / row["filename"]).unlink(missing_ok=True)
        db.execute("DELETE FROM browser_handoffs WHERE expires_at<=?", (now,))
        if db.execute("SELECT COUNT(*) FROM browser_handoffs").fetchone()[0] >= 16:
            raise OperationError("TOO_MANY_HANDOFFS", "wait for the previous browser entry to expire", 429)
        ticket = secrets.token_urlsafe(32)
        filename = secrets.token_hex(16) + ".html"
        path = folder / filename
        endpoint = self.origin + "/api/v1/browser-handoff"
        # The ticket is POSTed from a private local document. The URL contains no credential.
        page = ("<!doctype html><html lang=en><meta charset=utf-8><meta name=referrer content=no-referrer>"
                "<title>Open Dashboard</title><form method=post action=\"" + html.escape(endpoint, quote=True)
                + "\"><input type=hidden name=ticket value=\"" + ticket + "\">"
                "<button>Open Dashboard / 開啟 Dashboard</button></form>"
                "<script>document.forms[0].submit()</script></html>")
        atomic_write(path, page.encode())
        try:
            db.execute("INSERT INTO browser_handoffs VALUES(?,?,?,?,?,?)",
                       (api_auth._digest(ticket), principal.actor, json.dumps(sorted(principal.scopes)),
                        principal.credential_id, now + HANDOFF_S, filename))
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return {"protocol": 1, "handoff_file": str(path)}

    async def consume(self, headers, reader, writer):
        # Only a top-level POST from our local file, never a cross-site fetch/iframe.
        if (headers.get("origin") != "null" or headers.get("sec-fetch-mode") != "navigate"
                or headers.get("sec-fetch-dest") != "document"
                or headers.get("content-type", "").split(";", 1)[0] != "application/x-www-form-urlencoded"
                or headers.get("transfer-encoding")):
            raise OperationError("BAD_HANDOFF", "browser entry requires the local handoff document", 403)
        size = headers.get("content-length", "")
        if not size.isdigit() or not 1 <= int(size) <= 512:
            raise OperationError("BAD_HANDOFF", "invalid browser entry", 400)
        import asyncio
        raw = await asyncio.wait_for(reader.readexactly(int(size)), 5)
        values = parse_qs(raw.decode("ascii"), strict_parsing=True)
        if set(values) != {"ticket"} or len(values["ticket"]) != 1:
            raise OperationError("BAD_HANDOFF", "invalid browser entry", 400)
        digest = api_auth._digest(values["ticket"][0])
        with self.daemon.journal.tx():
            db = self.daemon.journal.db
            row = db.execute("SELECT * FROM browser_handoffs WHERE digest=?", (digest,)).fetchone()
            if not row or row["expires_at"] <= time.time() or not self._parent_valid(row["parent_hash"]):
                raise OperationError("HANDOFF_EXPIRED", "open Dashboard again from the desktop menu", 401)
            db.execute("DELETE FROM browser_handoffs WHERE digest=?", (digest,))
            token = api_auth.issue(db, row["actor"], json.loads(row["scopes"]),
                                   label="Managed browser session", ttl_s=SESSION_S)
            db.execute("INSERT INTO browser_sessions VALUES(?,?,?,?)",
                       (api_auth._digest(token), secrets.token_urlsafe(32), row["parent_hash"], time.time() + SESSION_S))
        (self.data_dir / "browser-handoffs" / row["filename"]).unlink(missing_ok=True)
        writer.write(("HTTP/1.1 303 See Other\r\nLocation: /dashboard/\r\n"
                      f"Set-Cookie: {self.cookie_name}={token}; HttpOnly; SameSite=Strict; Path=/api/v1; Max-Age={SESSION_S}\r\n"
                      "Cache-Control: no-store\r\nReferrer-Policy: no-referrer\r\n"
                      "Content-Length: 0\r\nConnection: close\r\n\r\n").encode())
        await writer.drain()

    def credential(self, method, headers, *, bootstrap=False):
        cookies = SimpleCookie()
        try:
            cookies.load(headers.get("cookie", ""))
            token = cookies[self.cookie_name].value
        except (KeyError, ValueError):
            return ""
        row = self.daemon.journal.db.execute("SELECT * FROM browser_sessions WHERE token_hash=?",
                                           (api_auth._digest(token),)).fetchone()
        if not row or row["expires_at"] <= time.time() or not self._parent_valid(row["parent_hash"]):
            return ""
        origin = headers.get("origin")
        referrer = urlsplit(headers.get("referer", ""))
        referred_origin = f"{referrer.scheme}://{referrer.netloc}"
        if ((origin and origin != self.origin) or headers.get("host") != urlsplit(self.origin).netloc
                or headers.get("sec-fetch-site") != "same-origin" or referred_origin != self.origin):
            raise OperationError("BAD_ORIGIN", "browser session requires this Dashboard origin", 403)
        if not bootstrap and not hmac.compare_digest(headers.get("x-batc-csrf", ""), row["csrf"]):
            raise OperationError("CSRF_REQUIRED", "reload Dashboard to restore this browser session", 403)
        return token

    def describe(self, token):
        row = self.daemon.journal.db.execute("SELECT csrf,expires_at FROM browser_sessions WHERE token_hash=?",
                                           (api_auth._digest(token),)).fetchone()
        principal = api_auth.authenticate(self.daemon.journal.db, token, self.daemon._admin_token, browser_session=True)
        if not row or not principal:
            raise OperationError("UNAUTHORIZED", "open Dashboard from the desktop menu", 401)
        return {"csrf": row["csrf"], "expires_at": row["expires_at"],
                "actor": principal.actor, **dashboard_sync.identity(self.daemon.journal, principal)}

    def revoke(self, token):
        digest = api_auth._digest(token)
        with self.daemon.journal.tx():
            self.daemon.journal.db.execute("DELETE FROM browser_sessions WHERE token_hash=?", (digest,))
            self.daemon.journal.db.execute("UPDATE api_principals SET revoked_at=? WHERE token_hash=?",
                                           (time.time(), digest))
