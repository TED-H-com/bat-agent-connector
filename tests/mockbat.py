"""A small mock of BAT's bat-remote/v2 server (TLS + WebSocket) for tests."""

from __future__ import annotations

import asyncio
import copy
import inspect
import json
import re
import ssl
from collections.abc import Callable
from typing import Any

import trustme
from websockets.asyncio.server import serve

from bat_agent_connector.client import cert_fingerprint

TOKEN = "tok-SECRET-0123456789abcdef"  # gitleaks:allow (fake test token for the mock server)


# ---------------------------------------------------- opt-in BAC handshake mock
# Independent wire oracle: deliberately does not import the Connector BAC parser.
_BAC_DEFAULT = object()
_BAC_MISSING = object()


class MockBac:
    """Handshake-only synthetic host; never dispatches a BAC call to legacy handlers."""

    MISSING = _BAC_MISSING
    CHANNELS = {
        "bac:workspace-read": "workspaceRead",
        "bac:workspace-add-terminal": "workspaceAddTerminal",
        "bac:session-locate": "sessionLocate",
        "bac:git-observe": "gitObserve",
    }

    def __init__(self):
        self.advertisement = {"protocol": "bac/1", "minor": 0}
        self.host_id = "h_0123456789abcdef0123456789abcdef"
        self.build = {"product": "bac", "revision": "unknown", "release": "dev", "dirty": None}
        self.features = dict.fromkeys(self.CHANNELS.values(), False)
        self.limits = {
            "requestBytes": 65536, "resultBytes": 524288, "registrationBytes": 16384,
            "profiles": 256, "terminals": 4096, "locationMatches": 100,
            "gitStatusBytes": 4194304, "gitIndexBytes": 16777216, "gitPaths": 10000,
            "gitTimeoutMs": 5000, "gitAttempts": 2,
        }
        self.storage_error = None
        self.storage_reason = "unspecified"
        self.drop_hello = False
        self.close_hello = False
        self.invoke_error = None
        self.hello_result = _BAC_DEFAULT
        self.hello_received = asyncio.Event()
        self.frames = []

    @staticmethod
    def error(code, **details):
        messages = {
            "INVALID_REQUEST": "Invalid BAC request",
            "PAYLOAD_TOO_LARGE": "BAC request exceeds limit",
            "RESPONSE_TOO_LARGE": "BAC result exceeds limit",
            "CONTEXT_NOT_SUPPORTED": "BAC requires a direct host connection",
            "UNSUPPORTED_VERSION": "Unsupported BAC protocol version",
            "HOST_MISMATCH": "Host identity changed",
            "STORAGE_UNAVAILABLE": "BAC identity storage is unavailable",
            "STORAGE_CORRUPT": "BAC identity storage is invalid",
            "INTERNAL_ERROR": "BAC request failed",
            "NOT_OPTED_IN": "Call bac:hello first",
            "UNSUPPORTED_FEATURE": "BAC feature is unavailable",
            "UNKNOWN_CHANNEL": "Unknown BAC channel",
        }
        return {"ok": False, "error": {"code": code, "message": messages[code],
                                       "retryable": False, "details": details}}

    def hello_value(self):
        return copy.deepcopy({"protocol": "bac/1", "minor": 0, "hostId": self.host_id,
                              "build": self.build, "features": self.features, "limits": self.limits})

    def reply(self, frame, negotiated, protocol):
        """Return (envelope, new connection state) in host validation order."""
        channel = frame.get("channel")

        def fail(code, **details):
            return self.error(code, **details), negotiated

        if isinstance(channel, str) and channel != "bac:hello" and not negotiated:
            return fail("NOT_OPTED_IN")
        if frame.get("type") != "invoke":
            return fail("INVALID_REQUEST", field="type")
        mid = frame.get("id")
        if not isinstance(mid, str) or "\0" in mid or not 1 <= len(mid.encode()) <= 128:
            return fail("INVALID_REQUEST", field="id")
        if not isinstance(channel, str) or "\0" in channel or len(channel.encode("utf-8")) > 128:
            return fail("INVALID_REQUEST", field="channel")
        if set(frame) - {"type", "id", "channel", "params", "contextId"}:
            return fail("INVALID_REQUEST", field="frame")
        params = frame.get("params")
        if not isinstance(params, dict):
            return fail("INVALID_REQUEST", field="params")
        if channel == "bac:hello":
            if set(params) - {"protocol", "minor", "expectedHostId"}:
                return fail("INVALID_REQUEST", field="params")
            if not isinstance(params.get("protocol"), str) or "\0" in params["protocol"]:
                return fail("INVALID_REQUEST", field="params.protocol")
            if type(params.get("minor")) is not int or not 0 <= params["minor"] < 2**64:
                return fail("INVALID_REQUEST", field="params.minor")
            if "expectedHostId" in params and (
                not isinstance(params["expectedHostId"], str)
                or not re.fullmatch(r"h_[0-9a-f]{32}", params["expectedHostId"])
            ):
                return fail("INVALID_REQUEST", field="params.expectedHostId")
        size = len(json.dumps(frame, ensure_ascii=False, separators=(",", ":")).encode())
        if size > 65536:
            return fail("PAYLOAD_TOO_LARGE", maxBytes=65536)
        if "contextId" in frame:
            return fail("CONTEXT_NOT_SUPPORTED")
        if channel == "bac:hello":
            if protocol != "bat-remote/v2" or params["protocol"] != "bac/1" or params["minor"] != 0:
                return fail("UNSUPPORTED_VERSION", protocol="bac/1", minor=0)
            if self.storage_error:
                details = ({"reason": self.storage_reason}
                           if self.storage_error in {"STORAGE_UNAVAILABLE", "STORAGE_CORRUPT"} else {})
                return fail(self.storage_error, **details)
            if "expectedHostId" in params and params["expectedHostId"] != self.host_id:
                return self.error("HOST_MISMATCH", hostId=self.host_id), False
            return {"ok": True, "value": self.hello_value()}, True
        feature = self.CHANNELS.get(channel)
        if feature is None:
            return fail("UNKNOWN_CHANNEL")
        if not self.features[feature]:
            return fail("UNSUPPORTED_FEATURE", feature=feature)
        # No feature implementation in this handshake-only mock.
        return fail("INTERNAL_ERROR")

    async def handle(self, ws, frame, negotiated, protocol):
        self.frames.append(copy.deepcopy(frame))
        result, negotiated = self.reply(frame, negotiated, protocol)
        mid = frame.get("id")
        if not isinstance(mid, str) or "\0" in mid or not 1 <= len(mid.encode("utf-8")) <= 128:
            mid = None
        if frame.get("channel") == "bac:hello":
            self.hello_received.set()
            if self.close_hello:
                await ws.close()
                return negotiated
            if self.drop_hello:
                return negotiated
            if self.invoke_error is not None:
                await ws.send(json.dumps({"type": "invoke-error", "id": mid,
                                          "error": self.invoke_error}))
                return negotiated
            if self.hello_result is not _BAC_DEFAULT:
                result = self.hello_result
        response = {"type": "invoke-result", "id": mid, "result": result}
        encoded = json.dumps(response, separators=(",", ":"), ensure_ascii=False)
        if len(encoded.encode("utf-8")) > 524288:
            response["result"] = self.error("RESPONSE_TOO_LARGE", maxBytes=524288)
            encoded = json.dumps(response, separators=(",", ":"), ensure_ascii=False)
        await ws.send(encoded)
        return negotiated


class MockBat:
    def __init__(self, token: str = TOKEN, protocol: str = "bat-remote/v2",
                 *, bac: bool = False) -> None:
        self.bac = MockBac() if bac else None
        self.token = token
        self.protocol = protocol
        self.frames: list[dict] = []
        self.invokes: list[dict] = []
        self.auth_frames: list[dict] = []
        self.flood_before_reply = 0
        self.conns: set = set()
        self.handlers: dict[str, Callable[[dict], Any]] = {}
        self.ws_doc = {
            "activeWorkspaceId": "ws-1",
            "workspaces": [
                {"id": "ws-1", "name": "demo-project", "folderPath": "/srv/demo"},
                {"id": "ws-2", "name": "other", "folderPath": "/srv/other"},
            ],
            "terminals": [
                {
                    "id": "sess-claude-0001",
                    "workspaceId": "ws-1",
                    "title": "Claude Agent",
                    "type": "terminal",
                    "cwd": "/srv/demo",
                    "agentPreset": "claude-code",
                    "sdkSessionId": "sdk-1",
                    "model": "m1",
                },
                {
                    "id": "sess-codex-0002",
                    "workspaceId": "ws-1",
                    "title": "Codex Agent",
                    "type": "terminal",
                    "cwd": "/srv/demo",
                    "agentPreset": "codex-agent",
                    "sdkSessionId": "sdk-2",
                },
                {
                    "id": "sess-unload-0003",
                    "workspaceId": "ws-2",
                    "title": "Claude Agent",
                    "type": "terminal",
                    "cwd": "/srv/other",
                    "agentPreset": "claude-code",
                    "sdkSessionId": "sdk-3",
                },
                {
                    "id": "shell-0004",
                    "workspaceId": "ws-2",
                    "title": "Terminal",
                    "type": "terminal",
                    "cwd": "/srv/other",
                },
            ],
        }
        self.removed_paths: set[str] = set()
        self.codex_worktrees: dict[str, dict] = {}
        self.metas: dict[str, dict | None] = {
            "sess-claude-0001": {
                "cwd": "/srv/demo",
                "model": "m1",
                "numTurns": 3,
                "isStreaming": False,
                "lastDataAt": 1_790_000_000_000,
            },
            "sess-codex-0002": {"cwd": "/srv/demo", "isStreaming": True, "lastDataAt": 1_790_000_100_000},
            "sess-unload-0003": None,
        }
        self.states: dict[str, dict] = {
            "sess-claude-0001": {
                "isStreaming": False,
                "messages": [
                    {
                        "id": f"m{i}",
                        "role": "user" if i % 2 == 0 else "assistant",
                        "content": f"live message {i}",
                        "timestamp": 1_790_000_000_000 + i * 1000,
                    }
                    for i in range(10)
                ]
                + [
                    {
                        "id": "tool1",
                        "toolName": "Bash",
                        "input": {"command": "ls"},
                        "status": "done",
                        "timestamp": 1_790_000_020_000,
                    }
                ],
                "pendingAskUser": None,
                "pendingPermission": None,
            },
            "sess-codex-0002": {
                "isStreaming": True,
                "messages": [],
                "pendingAskUser": None,
                "pendingPermission": None,
            },
        }
        self.archives: dict[str, list[dict]] = {
            "sess-claude-0001": [
                {
                    "id": f"a{i}",
                    "role": "assistant",
                    "content": [{"type": "text", "text": f"archived {i}"}],
                    "timestamp": 1_789_000_000_000 + i * 1000,
                }
                for i in range(50)
            ],
            "sess-unload-0003": [
                {"id": "u1", "role": "assistant", "content": "old", "timestamp": 1_780_000_000_000}
            ],
        }
        self.worktrees: dict[str, dict] = {}
        self.git_status: dict[str, list] = {}
        self.git_branch: dict[str, str] = {"/srv/demo": "main"}
        self.git_diff: dict[str, str] = {}
        self.perm_calls: list = []
        self.sent: dict[str, dict] = {}
        self.echo_sends = False
        self.save_hook: Callable[[], None] | None = None
        ca = trustme.CA()
        cert = ca.issue_cert("localhost", "127.0.0.1")
        self.ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cert.configure_cert(self.ssl_ctx)
        self.fingerprint = cert_fingerprint(
            ssl.PEM_cert_to_DER_cert(cert.cert_chain_pems[0].bytes().decode())
        )
        self.port = 0
        self._server = None

    # ------------------------------------------------------------------ server
    async def start(self) -> None:
        self._server = await serve(self._handle, "127.0.0.1", 0, ssl=self.ssl_ctx, compression=None)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    @property
    def url(self) -> str:
        return f"wss://127.0.0.1:{self.port}/"

    async def drop_all(self) -> None:
        for ws in list(self.conns):
            await ws.close()

    async def broadcast(self, channel: str, params: dict) -> None:
        for ws in list(self.conns):
            await ws.send(json.dumps({"type": "event", "channel": channel, "params": params}))

    async def _handle(self, ws) -> None:
        try:
            raw = await asyncio.wait_for(ws.recv(), 10)
        except Exception:
            return
        first = json.loads(raw)
        self.frames.append(first)
        if first.get("type") != "auth":
            await ws.close()
            return
        self.auth_frames.append(first)
        if first.get("token") != self.token:
            await ws.send(
                json.dumps({"type": "auth-result", "id": first.get("id"), "error": "Invalid token"})
            )
            await ws.close()
            return
        proto = self.protocol if "bat-remote/v2" in (first.get("protocols") or []) else "bat-remote/legacy-v1"
        capabilities = {}
        if (proto == "bat-remote/v2" and self.bac is not None
                and self.bac.advertisement is not _BAC_MISSING):
            capabilities["bac"] = copy.deepcopy(self.bac.advertisement)
        negotiated = False
        await ws.send(
            json.dumps(
                {
                    "type": "auth-result",
                    "id": first.get("id"),
                    "result": True,
                    "protocol": proto,
                    "compression": "none",
                    "serverVersion": "9.9.9",
                    "capabilities": capabilities,
                }
            )
        )
        self.conns.add(ws)
        try:
            async for raw in ws:
                f = json.loads(raw)
                self.frames.append(f)
                if f.get("type") == "ping":
                    await ws.send(json.dumps({"type": "pong", "id": f.get("id")}))
                    continue
                if f.get("type") != "invoke":
                    continue
                self.invokes.append(f)
                for i in range(self.flood_before_reply):
                    await ws.send(
                        json.dumps(
                            {
                                "type": "event",
                                "channel": "agent:stream",
                                "params": {"sessionId": "x", "data": {"text": str(i)}},
                            }
                        )
                    )
                channel = f.get("channel")
                if self.bac is not None and isinstance(channel, str) and channel.startswith("bac:"):
                    negotiated = await self.bac.handle(ws, f, negotiated, proto)
                    continue
                try:
                    res = self.dispatch(f["channel"], f.get("params") or {})
                    if inspect.isawaitable(res):
                        res = await res
                    await ws.send(json.dumps({"type": "invoke-result", "id": f["id"], "result": res}))
                except Exception as e:  # noqa: BLE001
                    await ws.send(json.dumps({"type": "invoke-error", "id": f["id"], "error": str(e)}))
        finally:
            self.conns.discard(ws)

    def channels(self) -> list[str]:
        return [f["channel"] for f in self.invokes]

    # ------------------------------------------------------------------ channels
    def dispatch(self, ch: str, p: dict) -> Any:
        if ch in self.handlers:
            return self.handlers[ch](p)
        sid = p.get("sessionId")
        if ch == "app:get-version":
            return "9.9.9"
        if ch == "profile:list":
            return {
                "profiles": [{"id": "default", "name": "Default", "type": "local"}],
                "activeProfileIds": ["default"],
            }
        if ch == "workspace:load":
            return json.dumps(self.ws_doc)
        if ch == "workspace:save":
            if self.save_hook:
                self.save_hook()
            self.ws_doc = json.loads(p["data"])
            return True
        if ch == "claude:get-session-meta":
            return copy.deepcopy(self.metas.get(sid))
        if ch == "claude:get-session-state":
            return copy.deepcopy(self.states.get(sid))
        if ch == "claude:load-archived":
            msgs = self.archives.get(sid, [])
            off, lim = int(p.get("offset", 0)), int(p.get("limit", 50))
            end = len(msgs) - off
            start = max(0, end - lim)
            return {"messages": msgs[start : max(0, end)], "total": len(msgs), "hasMore": start > 0}
        if ch == "claude:list-sessions":
            return [{"sdkSessionId": "sdk-1", "timestamp": 1_790_000_500_000, "messageCount": 5}]
        if ch == "claude:client-resume":
            self.metas[sid] = {"cwd": p["options"]["cwd"], "isStreaming": False,
                               **{k: v for k, v in p["options"].items() if k in
                                  ("permissionMode", "codexSandboxMode", "codexApprovalPolicy")}}
            return {"ok": True}
        if ch == "claude:send-message":
            mid = p.get("clientMessageId")
            if mid in self.sent:
                return self.sent[mid]
            r = {"ok": True, "accepted": True, "queued": bool((self.metas.get(sid) or {}).get("isStreaming"))}
            self.sent[mid] = r
            if self.echo_sends and sid in self.states:  # BAT appends the prompt as a user message
                import time as _t

                ts = max([int(_t.time() * 1000)] + [int(m.get("timestamp") or 0) + 1 for m in self.states[sid]["messages"]])
                self.states[sid]["messages"].append(
                    {"id": mid, "role": "user", "content": p.get("prompt"), "timestamp": ts}
                )
            return r
        if ch in ("claude:interrupt-turn", "claude:abort-session"):
            return True
        if ch in ("claude:resolve-ask-user", "claude:resolve-permission"):
            st = self.states.get(sid) or {}
            st["pendingAskUser"] = None
            st["pendingPermission"] = None
            return True
        if ch == "worktree:create":
            n = len(self.worktrees) + 1
            info = {
                "success": True,
                "worktreePath": f"{p['cwd']}/.bat-worktrees/0000000{n}",
                "branchName": f"bat/worktree-0000000{n}",
                "sourceBranch": p.get("baseBranch") or "main",
            }
            self.git_branch[info["worktreePath"]] = info["branchName"]
            self.worktrees[sid] = {
                "diff": "",
                "branchName": info["branchName"],
                "worktreePath": info["worktreePath"],
                "sourceBranch": p.get("baseBranch") or "main",
                "merged": False,
                "mergedKind": "unknown",
            }
            return info
        if ch == "claude:start-session":
            opts = p["options"]
            cwd = opts.get("worktreePath") if opts.get("useWorktree") else opts["cwd"]
            self.metas[sid] = {"cwd": cwd, "isStreaming": False}
            if str(opts.get("agentPreset", "")).startswith("codex"):
                self.metas[sid].update(codexSandboxMode=opts.get("codexSandboxMode", "workspace-write"),
                                       codexApprovalPolicy=opts.get("codexApprovalPolicy", "on-request"))
            else:
                self.metas[sid]["permissionMode"] = opts.get("permissionMode", "default")
            if opts.get("useWorktree"):
                self.worktrees.setdefault(sid, {"worktreePath": opts["worktreePath"],
                                            "branchName": opts["worktreeBranch"]})
            self.states[sid] = {"isStreaming": False, "messages": []}
            return {"ok": True, "sessionId": sid}
        if ch == "worktree:status":
            return copy.deepcopy(self.worktrees.get(sid))
        if ch == "claude:get-worktree-status":
            return copy.deepcopy(self.codex_worktrees.get(sid))
        if ch == "worktree:rehydrate":
            self.worktrees.setdefault(
                sid,
                {
                    "diff": "",
                    "branchName": p["branchName"],
                    "worktreePath": p["worktreePath"],
                    "sourceBranch": "main",
                    "merged": False,
                    "mergedKind": "unknown",
                },
            )
            return {"success": True}
        if ch == "worktree:merge":
            wt = self.worktrees[sid]
            wt["mergedKind"] = "ancestor"
            wt["merged"] = True
            return {"success": True, "strategy": p.get("strategy"), "branchName": wt["branchName"]}
        if ch == "worktree:remove":
            wt = self.worktrees.pop(sid, None)
            if wt:  # like BAT: without a record for the session nothing is removed, yet it reports success
                self.removed_paths.add(wt["worktreePath"])
            return {"success": True}
        if ch == "git:getRoot":
            return None if p["cwd"] in self.removed_paths else p["cwd"]
        if ch == "git:status":
            return self.git_status.get(p["cwd"], [])
        if ch == "git:branch":
            return self.git_branch.get(p["cwd"], "main")
        if ch == "git:log":
            logs = getattr(self, "git_logs", {})
            if p["cwd"] in logs:
                return logs[p["cwd"]][: p.get("count") or 50]
            return [{"hash": "abc1234", "author": "dev", "date": "2026-01-01", "message": "wip"}]
        if ch == "git:diff":
            if p.get("commitHash"):
                return getattr(self, "commit_diffs", {}).get((p["commitHash"], p.get("filePath")), "")
            return self.git_diff.get(p["cwd"], "")
        if ch == "git:diff-files":
            return getattr(self, "commit_files", {}).get(p.get("commitHash"), [])
        if ch == "claude:stop-session":
            self.metas[sid] = None
            return {"ok": True, "existed": True}
        if ch in ("claude:set-permission-mode", "claude:set-codex-sandbox-mode", "claude:set-codex-approval-policy"):
            self.perm_calls.append((ch, p))
            key = {"claude:set-permission-mode": "permissionMode", "claude:set-codex-sandbox-mode":
                   "codexSandboxMode", "claude:set-codex-approval-policy": "codexApprovalPolicy"}[ch]
            if self.metas.get(sid):
                self.metas[sid][key] = p.get("mode", p.get("policy"))
            return True
        raise RuntimeError(f"mock: unhandled channel {ch}")
