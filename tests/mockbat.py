"""A small mock of BAT's bat-remote/v2 server (TLS + WebSocket) for tests."""

from __future__ import annotations

import asyncio
import copy
import hashlib
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
    """Synthetic BAC host for handshake and optional read features; never dispatches a BAC call to legacy handlers."""

    MISSING = _BAC_MISSING
    CLOSE = object()
    CHANNELS = {
        "bac:workspace-read": "workspaceRead",
        "bac:workspace-add-terminal": "workspaceAddTerminal",
        "bac:session-locate": "sessionLocate",
        "bac:git-observe": "gitObserve",
    }
    _TERMINAL_KEYS = {
        "id",
        "workspaceId",
        "title",
        "type",
        "cwd",
        "agentPreset",
        "model",
        "permissionMode",
        "agentParams",
        "worktreePath",
        "worktreeBranch",
    }
    _AGENT_PRESETS = {
        "claude-code",
        "claude-code-worktree",
        "codex-agent",
        "codex-agent-worktree",
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
        self.workspace_host = None
        self.read_error = None
        self.locate_error = None
        self.result_override = {}
        self.busy_replies = 0
        self.drop_feature_replies = False
        self.extra_locations = []
        self.before_append = None
        self.overlay = None
        self.append_error = None
        self.append_fault = None

    def enable_workspace(self, mock):
        self.workspace_host = mock
        self.features["workspaceRead"] = True
        self.features["sessionLocate"] = True

    def enable_append(self, mock):
        self.enable_workspace(mock)
        self.features["workspaceAddTerminal"] = True

    @staticmethod
    def revision(document):
        return "sha256:" + hashlib.sha256(
            json.dumps(document, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _render_document(raw):
        return copy.deepcopy({
            "workspaces": raw.get("workspaces", []),
            "activeWorkspaceId": raw.get("activeWorkspaceId"),
            "activeGroup": raw.get("activeGroup"),
            "terminals": raw.get("terminals", []),
            "activeTerminalId": raw.get("activeTerminalId"),
        })

    @staticmethod
    def _valid_id_list(items):
        return (
            isinstance(items, list)
            and all(
                isinstance(item, dict)
                and isinstance(item.get("id"), str)
                and bool(item["id"])
                for item in items
            )
            and len({item["id"] for item in items}) == len(items)
        )

    @staticmethod
    def _valid_profile_id(value):
        return (
            isinstance(value, str)
            and bool(re.fullmatch(r"[A-Za-z0-9_.\-\u4e00-\u9fff]+", value))
            and 1 <= len(value.encode("utf-8")) <= 128
        )

    @staticmethod
    def _valid_id(value):
        return isinstance(value, str) and bool(
            re.fullmatch(r"[A-Za-z0-9_.:\-]{1,128}", value)
        )

    @staticmethod
    def _valid_nonempty_str(value):
        return isinstance(value, str) and bool(value) and "\0" not in value

    @staticmethod
    def _json_equal(left, right):
        return json.dumps(
            left, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ) == json.dumps(
            right, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )

    @classmethod
    def _registrations_equal(cls, a, b):
        a_type = "terminal" if "type" not in a else a["type"]
        b_type = "terminal" if "type" not in b else b["type"]
        if not cls._json_equal(a_type, b_type):
            return False
        for key in ("id", "workspaceId", "title", "cwd", "agentPreset"):
            if not cls._json_equal(a.get(key), b.get(key)):
                return False
        for key in ("model", "permissionMode", "worktreePath"):
            if not cls._json_equal(a.get(key), b.get(key)):
                return False
        if ("worktreeBranch" in a) != ("worktreeBranch" in b):
            return False
        if "worktreeBranch" in a and not cls._json_equal(
            a["worktreeBranch"], b["worktreeBranch"]
        ):
            return False
        a_params = {} if a.get("agentParams") is None else a["agentParams"]
        b_params = {} if b.get("agentParams") is None else b["agentParams"]
        return cls._json_equal(a_params, b_params)

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
            "PROFILE_NOT_FOUND": "Local profile not found",
            "PROFILE_NOT_LOCAL": "BAC requires a local profile",
            "WORKSPACE_UNAVAILABLE": "Canonical workspace is unavailable",
            "WORKSPACE_TOO_LARGE": "Canonical workspace exceeds limit",
            "LOCATION_UNAVAILABLE": "Session locations are unavailable",
            "LOCATION_TOO_LARGE": "Session locations exceed limit",
            "WORKSPACE_NOT_FOUND": "Target workspace not found",
            "TERMINAL_EXISTS": "Terminal ID has a different registration",
            "REVISION_MISMATCH": "Workspace revision changed",
            "WRITE_FAILED": "Canonical workspace save failed; read back before retry",
        }
        return {"ok": False, "error": {"code": code, "message": messages[code],
                                       "retryable": code == "REVISION_MISMATCH", "details": details}}

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
        if self.workspace_host is None or channel == "bac:git-observe":
            # Git channel has no implementation in this mock; workspace channels require enable_workspace.
            return fail("INTERNAL_ERROR")
        if channel == "bac:workspace-add-terminal":
            if set(params) - {"profileId", "expectedRevision", "terminal"}:
                return fail("INVALID_REQUEST", field="params")
            profile_id = params.get("profileId")
            if not self._valid_profile_id(profile_id):
                return fail("INVALID_REQUEST", field="params.profileId")
            expected_rev = params.get("expectedRevision")
            if not isinstance(expected_rev, str) or not re.fullmatch(
                r"sha256:[0-9a-f]{64}", expected_rev
            ):
                return fail("INVALID_REQUEST", field="params.expectedRevision")
            if "terminal" not in params:
                return fail("INVALID_REQUEST", field="params.terminal")
            terminal = params["terminal"]
            if not isinstance(terminal, dict) or (set(terminal) - self._TERMINAL_KEYS):
                return fail("INVALID_REQUEST", field="params.terminal")
            if not self._valid_id(terminal.get("id")):
                return fail("INVALID_REQUEST", field="params.terminal.id")
            if not self._valid_id(terminal.get("workspaceId")):
                return fail("INVALID_REQUEST", field="params.terminal.workspaceId")
            if not self._valid_nonempty_str(terminal.get("title")):
                return fail("INVALID_REQUEST", field="params.terminal.title")
            if terminal.get("type") != "terminal":
                return fail("INVALID_REQUEST", field="params.terminal.type")
            if not self._valid_nonempty_str(terminal.get("cwd")):
                return fail("INVALID_REQUEST", field="params.terminal.cwd")
            preset = terminal.get("agentPreset")
            if not (isinstance(preset, str) and preset in self._AGENT_PRESETS):
                return fail("INVALID_REQUEST", field="params.terminal.agentPreset")
            if terminal.get("model") is not None and not self._valid_nonempty_str(
                terminal["model"]
            ):
                return fail("INVALID_REQUEST", field="params.terminal.model")
            if terminal.get("permissionMode") is not None and not self._valid_nonempty_str(
                terminal["permissionMode"]
            ):
                return fail("INVALID_REQUEST", field="params.terminal.permissionMode")
            agent_params = terminal.get("agentParams")
            if agent_params is not None and (
                not isinstance(agent_params, dict)
                or len(agent_params) > 32
                or not all(
                    self._valid_nonempty_str(k) and isinstance(v, str) and "\0" not in v
                    for k, v in agent_params.items()
                )
            ):
                return fail("INVALID_REQUEST", field="params.terminal.agentParams")
            if terminal.get("worktreePath") is not None and not self._valid_nonempty_str(
                terminal["worktreePath"]
            ):
                return fail("INVALID_REQUEST", field="params.terminal.worktreePath")
            if terminal.get("worktreeBranch") is not None and not self._valid_nonempty_str(
                terminal["worktreeBranch"]
            ):
                return fail("INVALID_REQUEST", field="params.terminal.worktreeBranch")
            if (
                len(
                    json.dumps(
                        terminal, ensure_ascii=False, separators=(",", ":")
                    ).encode("utf-8")
                )
                > 16384
            ):
                return fail("INVALID_REQUEST", field="params.terminal")
            if self.append_error:
                details = (
                    {"maxBytes": 520192}
                    if self.append_error == "WORKSPACE_TOO_LARGE"
                    else {}
                )
                return fail(self.append_error, **details)
            if profile_id != "default":
                return fail("PROFILE_NOT_FOUND")
            if self.before_append is not None:
                self.before_append()
            raw = self.workspace_host.ws_doc
            document = self._render_document(raw)
            rev = self.revision(document)
            if not (
                self._valid_id_list(raw.get("workspaces"))
                and self._valid_id_list(raw.get("terminals"))
            ):
                return fail("WORKSPACE_UNAVAILABLE")
            normalized = {
                "id": terminal["id"],
                "workspaceId": terminal["workspaceId"],
                "title": terminal["title"],
                "type": "terminal",
                "cwd": terminal["cwd"],
                "agentPreset": terminal["agentPreset"],
            }
            for opt_key in ("model", "permissionMode", "agentParams", "worktreePath"):
                if terminal.get(opt_key) is not None:
                    normalized[opt_key] = copy.deepcopy(terminal[opt_key])
            if "worktreeBranch" in terminal:
                normalized["worktreeBranch"] = terminal["worktreeBranch"]
            existing = next(
                (t for t in document["terminals"] if t["id"] == normalized["id"]),
                None,
            )
            if existing is not None:
                stored_copy = copy.deepcopy(existing)
                candidate_copy = copy.deepcopy(normalized)
                if self.overlay is not None:
                    self.overlay(stored_copy)
                    self.overlay(candidate_copy)
                if self._registrations_equal(stored_copy, candidate_copy):
                    return {
                        "ok": True,
                        "value": {
                            "hostId": self.host_id,
                            "profileId": "default",
                            "source": "persisted",
                            "windowId": None,
                            "terminalId": normalized["id"],
                            "appended": False,
                            "revision": rev,
                        },
                    }, negotiated
                return fail(
                    "TERMINAL_EXISTS",
                    terminalId=normalized["id"],
                    currentRevision=rev,
                )
            if expected_rev != rev:
                return fail("REVISION_MISMATCH", currentRevision=rev)
            if not any(
                w["id"] == normalized["workspaceId"] for w in document["workspaces"]
            ):
                return fail("WORKSPACE_NOT_FOUND", workspaceId=normalized["workspaceId"])
            fault = self.append_fault
            self.append_fault = None

            def apply_append():
                stored = copy.deepcopy(normalized)
                if self.overlay is not None:
                    self.overlay(stored)
                raw["terminals"] = document["terminals"] + [stored]

            if fault == "lost_request":
                return self.CLOSE, negotiated
            if fault == "lost_reply":
                apply_append()
                return self.CLOSE, negotiated
            if fault == "write_failed":
                return fail("WRITE_FAILED", outcome="unknown")
            if fault == "write_failed_applied":
                apply_append()
                return fail("WRITE_FAILED", outcome="unknown")
            if fault == "internal_error":
                return fail("INTERNAL_ERROR")
            apply_append()
            return {
                "ok": True,
                "value": {
                    "hostId": self.host_id,
                    "profileId": "default",
                    "source": "persisted",
                    "windowId": None,
                    "terminalId": normalized["id"],
                    "appended": True,
                    "revision": self.revision(self._render_document(raw)),
                },
            }, negotiated
        key = "profileId" if channel == "bac:workspace-read" else "sessionId"
        if set(params) - {key}:
            return fail("INVALID_REQUEST", field="params")
        value = params.get(key)
        if channel == "bac:workspace-read":
            valid = self._valid_profile_id(value)
        else:
            valid = self._valid_id(value)
        if not valid:
            return fail("INVALID_REQUEST", field=f"params.{key}")
        err = self.read_error if channel == "bac:workspace-read" else self.locate_error
        if err:
            details = (
                {"maxBytes": 520192}
                if err == "WORKSPACE_TOO_LARGE"
                else {"profileIds": []}
                if err == "LOCATION_UNAVAILABLE"
                else {}
            )
            return fail(err, **details)
        raw = self.workspace_host.ws_doc
        document = self._render_document(raw)
        rev = self.revision(document)
        if channel == "bac:workspace-read":
            if value != "default":
                return fail("PROFILE_NOT_FOUND")
            eligible = self._valid_id_list(raw.get("workspaces")) and self._valid_id_list(
                raw.get("terminals")
            )
            return {
                "ok": True,
                "value": {
                    "hostId": self.host_id,
                    "profileId": "default",
                    "source": "persisted",
                    "windowId": None,
                    "revision": rev,
                    "document": document,
                    "appendTargetEligible": eligible,
                },
            }, negotiated
        workspaces = document["workspaces"]
        terminals = document["terminals"]
        locations = []
        if isinstance(workspaces, list) and isinstance(terminals, list):
            ws_ids = {
                w["id"]
                for w in workspaces
                if isinstance(w, dict) and isinstance(w.get("id"), str)
            }
            for term in terminals:
                if (
                    isinstance(term, dict)
                    and term.get("id") == value
                    and isinstance(term.get("workspaceId"), str)
                    and term["workspaceId"] in ws_ids
                ):
                    locations.append({
                        "profileId": "default",
                        "workspaceId": term["workspaceId"],
                        "terminalId": value,
                        "windowId": None,
                        "source": "persisted",
                        "profileRevision": rev,
                    })
        locations.extend(copy.deepcopy(self.extra_locations))
        disposition = (
            "unregistered"
            if not locations
            else "located"
            if len(locations) == 1
            else "ambiguous"
        )
        return {
            "ok": True,
            "value": {
                "hostId": self.host_id,
                "locationRevision": self.revision([["default", "persisted", None, rev]]),
                "disposition": disposition,
                "consistency": "sequential",
                "atomic": False,
                "locations": locations,
            },
        }, negotiated

    async def handle(self, ws, frame, negotiated, protocol):
        self.frames.append(copy.deepcopy(frame))
        mid = frame.get("id")
        if not isinstance(mid, str) or "\0" in mid or not 1 <= len(mid.encode("utf-8")) <= 128:
            mid = None
        channel = frame.get("channel")
        is_feature = isinstance(channel, str) and channel in self.CHANNELS
        if is_feature and not self.drop_feature_replies and self.busy_replies > 0:
            self.busy_replies -= 1
            await ws.send(json.dumps({"type": "invoke-error", "id": mid,
                                      "error": "Remote server is busy"}))
            return negotiated
        result, negotiated = self.reply(frame, negotiated, protocol)
        if channel == "bac:hello":
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
        elif is_feature:
            if result is self.CLOSE:
                await ws.close()
                return negotiated
            if self.drop_feature_replies:
                return negotiated
            if channel in self.result_override:
                result = self.result_override[channel]
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
