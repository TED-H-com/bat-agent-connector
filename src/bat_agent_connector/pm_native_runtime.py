"""Bounded, isolated Linux Codex PM analysis; no operation or journal authority.

See docs/design/pm-native-runtime.md. The caller owns admission, durable receipts
and recovery. A broken stream never authorizes a second turn/start request.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import time
import uuid
from pathlib import Path

from .native_process import (
    MAX_LINE_BYTES,
    BoundedNativeProcess,
    NativeRuntimeError,
    _call,
    _digest,
    _json,
    _process_identity,
    _require,
    _spawn,
)
from .native_process import (
    MAX_STREAM_BYTES as MAX_STREAM_BYTES,
)
from .native_process import (
    inspect_process as inspect_process,
)

VERSION = "0.160.0"
SOURCE = "a956835d020762cb2b570053af06f643a11c0ecc"
CONTRACT = "codex-pm-linux-v1"
SYSTEM_CONFIG_ROOT = Path("/etc/codex")
MAX_OUTPUT_CHARS = 128_000
LAUNCH_TIMEOUT = 20
PREFLIGHT_TIMEOUT = 20
DISABLED_FEATURES = (
    "shell_tool", "unified_exec", "multi_agent", "multi_agent_v2", "agent_message_board",
    "hooks", "apps", "plugins", "remote_plugin", "recommended_plugins", "tool_suggest",
    "image_generation", "browser_use", "browser_use_external", "computer_use", "in_app_browser",
    "code_mode", "code_mode_host", "code_mode_prewarm", "goals", "memories",
    "skill_mcp_dependency_install", "skill_search", "deferred_executor", "send_message_to_user_async",
    "token_budget", "sleep_tool", "current_time_reminder",
)


async def _runtime_version(executable: str) -> str:
    process = await _spawn(executable, "--version", stdout=asyncio.subprocess.PIPE,
                           stderr=asyncio.subprocess.DEVNULL)
    try:
        try:
            output, _ = await asyncio.wait_for(process.communicate(), 5)
        except asyncio.TimeoutError:
            raise NativeRuntimeError("PM_NATIVE_VERSION_TIMEOUT") from None
        _require(process.returncode == 0 and output.strip() == f"codex-cli {VERSION}".encode(),
                 "PM_NATIVE_VERSION_UNSUPPORTED")
        return VERSION
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


def _host_paths() -> tuple[Path, Path]:
    home = Path.home().resolve()
    native = Path(os.environ.get("CODEX_HOME", str(home / ".codex"))).resolve()
    _require(native.is_dir() and native != Path("/"), "PM_NATIVE_HOME_UNAVAILABLE")
    return home, native


def _settings(private_home: Path) -> dict:
    return {
        "approval_policy": "never", "sandbox_mode": "read-only", "web_search": "disabled",
        "project_doc_max_bytes": 0, "project_root_markers": [],
        "agents.enabled": False, "orchestrator.mcp.enabled": False,
        "cloud.skills.enabled": False, "skills.bundled.enabled": False,
        "skills.include_instructions": False, "tools.experimental_request_user_input.enabled": False,
        "tools.update_plan.enabled": False, "features.skip_host_skill_discovery": True,
        "history.persistence": "none", "log_dir": str(private_home / "log"),
        "sqlite_home": str(private_home / "state"),
        **{f"features.{key}": False for key in DISABLED_FEATURES},
    }


def validate_config(config: dict) -> None:
    _require(isinstance(config, dict))
    for dotted, expected in _settings(Path("/unused")).items():
        if dotted in {"log_dir", "sqlite_home"}:
            continue
        value = config
        for part in dotted.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        # v0.160.0 config/read projects ToolsConfig down to web_search only.
        # These benign helpers are disabled by the pinned input contract but not observable here.
        if dotted.startswith("tools.") and value is None:
            continue
        _require(type(value) is type(expected) and value == expected)
    _require(config.get("mcp_servers") == {})
    _require(not config.get("notify") and not config.get("instructions")
             and not config.get("developer_instructions") and not config.get("model_instructions_file"))


def _check_host_config() -> None:
    # Managed policies must not be hidden or weakened to make this adapter work.
    _require(not any((SYSTEM_CONFIG_ROOT / name).exists() for name in
                     ("config.toml", "managed_config.toml", "requirements.toml")),
             "PM_NATIVE_MANAGED_CONFIG_UNSUPPORTED")


def installation_eligibility() -> dict:
    """Cheap installation hints only: never spawn, read auth bytes, or verify a model."""
    result = {"adapter": "codex-native-linux", "contract": CONTRACT, "required_version": VERSION,
              "execution_scope": "frozen_context_only", "execution_location": "connector_service",
              "eligible_for_preflight": False, "options_confirmed": False,
              "version_verification": "per_run", "auth_validity": "unverified",
              "model_execution_verified": False, "auth_file_readable": False}
    try:
        _require(sys.platform == "linux", "PM_NATIVE_PLATFORM_UNSUPPORTED")
        _require(shutil.which("codex") and shutil.which("bwrap"), "PM_NATIVE_EXECUTABLE_UNAVAILABLE")
        _check_host_config()
        _, native = _host_paths()
        auth = native / "auth.json"
        readable = auth.is_file() and not auth.is_symlink() and os.access(auth, os.R_OK)
        result["auth_file_readable"] = readable
        _require(readable, "PM_NATIVE_AUTH_UNAVAILABLE")
        return {**result, "eligible_for_preflight": True, "reason": None}
    except (NativeRuntimeError, OSError, ValueError) as error:
        return {**result, "reason": getattr(error, "code", "PM_NATIVE_INSTALLATION_UNAVAILABLE")}


def _summary_items(observations: list, summary: list) -> tuple[list, list, str | None]:
    """Pinned live summary contains only the last completed, nonempty final message."""
    starts, completed, seqs = {}, {}, []
    for method, item, seq in observations:
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id:
            return [], [], "native_item_ids_invalid"
        if method == "item/started":
            if item_id in starts or item_id in completed:
                return [], [], "native_item_lifecycle_invalid"
            starts[item_id] = item
        else:
            original = starts.get(item_id)
            if (original is None or item_id in completed or original.get("type") != item.get("type")
                    or item.get("type") == "userMessage" and original != item
                    or item.get("type") == "agentMessage" and original.get("phase") != item.get("phase")):
                return [], [], "native_item_lifecycle_invalid"
            completed[item_id] = item
        seqs.append(seq)
    if not starts or starts.keys() != completed.keys():
        return [], [], "native_items_incomplete"
    items = list(completed.values())
    messages = [item for item in items if item.get("type") == "agentMessage"
                and item.get("phase") in (None, "final_answer")
                and isinstance(item.get("text"), str) and item["text"].strip()]
    # Do not treat a summary as a complete history or accept merely matching text.
    if not messages or summary != [messages[-1]]:
        return [], [], "native_summary_mismatch"
    users = [item for item in items if item.get("type") == "userMessage"]
    if len(users) != 1 or items.index(users[0]) >= items.index(messages[-1]):
        return [], [], "native_input_mismatch"
    return items, seqs, None


def assess_turn(events: list[dict], *, run_id: str, thread_id: str, prompt_digest: str,
                client_message_id: str, actual_model: str, stream_complete: bool = True) -> dict:
    """Reduce original native notifications. ACK, exit, or assistant text alone is insufficient."""
    result = {"status": "unknown", "complete": False, "reason": "native_completion_missing",
              "content": None, "run_id": run_id, "thread_id": thread_id, "native_turn_id": None,
              "native_message_id": None, "actual_model": actual_model, "source_event_seqs": []}
    if not stream_complete:
        return {**result, "reason": "native_stream_incomplete"}
    started = None
    terminal = None
    acknowledged = None
    observations, boundary_seqs = [], []
    for index, envelope in enumerate(events, 1):
        if (not isinstance(envelope, dict) or type(envelope.get("seq")) is not int or envelope["seq"] != index
                or envelope.get("run_id") != run_id or not isinstance(envelope.get("event"), dict)):
            return {**result, "reason": "native_event_gap"}
        event = envelope["event"]
        params = event.get("params", {})
        if not isinstance(params, dict):
            return {**result, "reason": "native_event_invalid"}
        if "method" in event and "id" in event:
            return {**result, "reason": "unexpected_runtime_request"}
        if params.get("threadId", thread_id) != thread_id:
            return {**result, "reason": "native_thread_mismatch"}
        if "turnId" in params and (started is None or params["turnId"] != started):
            return {**result, "reason": "native_turn_mismatch"}
        method = event.get("method")
        if method in ("turn/started", "turn/completed", "item/started", "item/completed"):
            if params.get("threadId") != thread_id:
                return {**result, "reason": "native_thread_mismatch"}
        if "id" in event:
            response = event.get("result", {})
            if "error" in event or not isinstance(response, dict):
                return {**result, "reason": "native_error"}
            turn = response.get("turn", {})
            if (not isinstance(turn, dict) or not isinstance(turn.get("id"), str)
                    or not turn["id"] or acknowledged is not None):
                return {**result, "reason": "native_ack_invalid"}
            acknowledged = turn["id"]
            boundary_seqs.append(index)
        if method in ("item/started", "item/completed"):
            item = params.get("item", {})
            if not isinstance(item, dict) or item.get("type") not in ("userMessage", "agentMessage", "reasoning"):
                return {**result, "reason": "unexpected_runtime_tool"}
            if started is None or params.get("turnId") != started:
                return {**result, "reason": "native_turn_mismatch"}
            if terminal is not None:
                return {**result, "reason": "native_events_after_completion"}
            observations.append((method, item, index))
        if method == "turn/started":
            if started is not None or terminal is not None:
                return {**result, "reason": "multiple_native_turns"}
            turn = params.get("turn", {})
            if not isinstance(turn, dict) or not isinstance(turn.get("id"), str) or not turn["id"]:
                return {**result, "reason": "native_turn_mismatch"}
            started = turn["id"]
            boundary_seqs.append(index)
        if method == "turn/completed":
            if terminal is not None:
                return {**result, "reason": "multiple_native_completions"}
            turn = params.get("turn", {})
            if not isinstance(turn, dict):
                return {**result, "reason": "native_event_invalid"}
            terminal = (turn, index)
        if method == "error":
            return {**result, "reason": "native_error"}
    if terminal is None:
        return result
    turn, seq = terminal
    if not started or turn.get("id") != started or acknowledged != started:
        return {**result, "reason": "native_turn_mismatch"}
    result["native_turn_id"] = started
    if turn.get("status") != "completed" or turn.get("error"):
        return {**result, "status": "incomplete", "reason": "native_turn_not_completed"}
    items = turn.get("items")
    view = turn.get("itemsView", "full")
    if view not in ("full", "summary") or not isinstance(items, list):
        return {**result, "reason": "native_items_incomplete"}
    source_seqs = [seq]
    if view == "summary":
        items, item_seqs, reason = _summary_items(observations, items)
        if reason:
            return {**result, "reason": reason}
        source_seqs = sorted([*boundary_seqs, *item_seqs, seq])
    if any(not isinstance(item, dict) or item.get("type") not in
           ("userMessage", "agentMessage", "reasoning") for item in items):
        return {**result, "reason": "unexpected_runtime_tool"}
    ids = [item.get("id") for item in items]
    if any(not isinstance(item_id, str) or not item_id for item_id in ids) or len(set(ids)) != len(ids):
        return {**result, "reason": "native_item_ids_invalid"}
    users = [item for item in items if item.get("type") == "userMessage"]
    if len(users) != 1 or users[0].get("clientId") != client_message_id:
        return {**result, "reason": "native_input_mismatch"}
    content = users[0].get("content")
    if (not isinstance(content, list) or len(content) != 1 or not isinstance(content[0], dict)
            or content[0].get("type") != "text"
            or not isinstance(content[0].get("text"), str) or _digest(content[0]["text"]) != prompt_digest):
        return {**result, "reason": "native_input_mismatch"}
    messages = [item for item in items if item.get("type") == "agentMessage"
                and item.get("phase") in (None, "final_answer")
                and isinstance(item.get("text"), str) and item["text"].strip()]
    if not messages:
        return {**result, "status": "incomplete", "reason": "native_output_missing"}
    message = messages[-1]
    if not isinstance(message.get("id"), str) or not message["id"] or len(message["text"]) > MAX_OUTPUT_CHARS:
        return {**result, "reason": "native_output_invalid"}
    return {**result, "status": "completed", "complete": True, "reason": None,
            "content": message["text"], "native_message_id": message["id"], "source_event_seqs": source_seqs}


class NativePmRuntime(BoundedNativeProcess):
    """One owned process, one fresh thread, at most one turn. Use as async context manager."""

    def __init__(self, scratch_root: Path, run_id: str, *, model: str | None = None,
                 auth_file: Path | None = None, use_auth: bool = True, offline: bool = False,
                 record_lifecycle=None, executable: str | None = None):
        _require(bool(re.fullmatch(r"[A-Za-z0-9_-]{1,96}", run_id)), "PM_NATIVE_RUN_ID_INVALID")
        _require(model is None or isinstance(model, str) and bool(model.strip()) and len(model) <= 128,
                 "PM_NATIVE_MODEL_INVALID")
        self.scratch_root, self.requested_model = Path(scratch_root), model
        self.auth_file, self.use_auth, self.offline = auth_file, use_auth, offline
        self.executable = executable
        super().__init__(run_id, record_lifecycle=record_lifecycle)
        self.capability = None
        self._request_id = 0
        self._attempted = False
        self._event_sink = None
        self._stage = "not_started"
        self._stage_started = None

    def _diagnostic_stage(self, stage):
        # Call sites supply fixed labels only; no paths, request bodies or stderr.
        self._stage, self._stage_started = stage, time.monotonic()

    def _preflight_failure(self, error, boundary, started, deadline):
        at = time.monotonic()
        timed_out = isinstance(error, asyncio.TimeoutError) or getattr(error, "code", None) == "PM_NATIVE_VERSION_TIMEOUT"
        code = getattr(error, "code", f"PM_NATIVE_{boundary.upper()}_TIMEOUT" if timed_out else "native_probe_failed")
        self.preflight_diagnostic = {
            "boundary": boundary, "stage": self._stage, "code": code,
            "failure": "timeout" if timed_out else "cancelled" if isinstance(error, asyncio.CancelledError)
                else "boundary_rejected" if code == "PM_NATIVE_BOUNDARY_UNVERIFIED" else "runtime_error",
            "elapsed_s": round(max(0, at - started), 3),
            "stage_elapsed_s": round(max(0, at - (self._stage_started or started)), 3),
            "deadline_s": 5 if code == "PM_NATIVE_VERSION_TIMEOUT" else deadline,
        }
        return NativeRuntimeError(code) if isinstance(error, asyncio.TimeoutError) else error

    async def __aenter__(self):
        started = time.monotonic()
        self._diagnostic_stage("installation")
        try:
            await asyncio.wait_for(self._launch(), LAUNCH_TIMEOUT)
            return self
        except BaseException as error:
            failure = self._preflight_failure(error, "launch", started, LAUNCH_TIMEOUT)
            await self.close()
            if failure is error:
                raise
            raise failure from None

    async def __aexit__(self, *_):
        await self.close()

    async def _launch(self):
        _require(sys.platform == "linux", "PM_NATIVE_PLATFORM_UNSUPPORTED")
        executable = self.executable or shutil.which("codex")
        bwrap = shutil.which("bwrap")
        _require(executable and bwrap, "PM_NATIVE_EXECUTABLE_UNAVAILABLE")
        self.executable = str(Path(executable).resolve())
        self._diagnostic_stage("version")
        await _runtime_version(self.executable)
        self._diagnostic_stage("isolation_setup")
        home, native = _host_paths()
        self.scratch_root = self.scratch_root.resolve()
        _check_host_config()
        self.scratch_root.mkdir(parents=True, exist_ok=True)
        self.directory = Path(tempfile.mkdtemp(prefix=f"pm-native-{self.run_id}-", dir=self.scratch_root))
        private_home = self.directory / "profile"
        private_home.mkdir()
        self.workspace = self.directory / "workspace"
        self.workspace.mkdir()
        empty = self.directory / "empty-customizations"
        empty.mkdir()
        settings = _settings(private_home)
        (private_home / "config.toml").write_text(
            "\n".join(f"{key}={json.dumps(value)}" for key, value in settings.items()) + "\n")
        self._spool = (self.directory / "events.ndjson").open("ab")
        command = [bwrap, "--ro-bind", "/", "/", "--bind", str(self.directory), str(self.directory),
                   "--bind", str(private_home), str(native)]
        for path in (home / ".agents", home / ".codex"):
            if path.is_dir() and path.resolve() != native:
                command.extend(["--ro-bind", str(empty), str(path)])
        fd = None
        auth_path = Path(self.auth_file) if self.auth_file is not None else native / "auth.json"
        if self.use_auth and auth_path.is_file():
            fd = os.open(auth_path, os.O_RDONLY | os.O_NOFOLLOW)
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                os.close(fd)
                raise NativeRuntimeError("PM_NATIVE_AUTH_INVALID")
            (private_home / "auth.json").touch(mode=0o600)
            command.extend(["--ro-bind", f"/proc/self/fd/{fd}", str(native / "auth.json")])
        self.auth_bound = fd is not None
        command.extend(["--unshare-pid", "--die-with-parent", "--chdir", str(self.workspace)])
        if self.offline:
            command.append("--unshare-net")
        command.extend([self.executable, "app-server", "--strict-config", "--stdio"])
        allowed_env = {"PATH", "HOME", "CODEX_HOME", "LANG", "LC_ALL", "HTTPS_PROXY", "HTTP_PROXY",
                       "NO_PROXY", "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS"}
        environment = {key: value for key, value in os.environ.items() if key in allowed_env}
        try:
            self._diagnostic_stage("launch_receipt")
            self._spool_record({"source": "adapter", "kind": "process_launch_attempted", "run_id": self.run_id})
            self.process_launch_attempted = True
            try:
                self._diagnostic_stage("spawn")
                self.process = await _spawn(*command, cwd=self.workspace, env=environment,
                                            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                            stderr=asyncio.subprocess.PIPE, start_new_session=True,
                                            pass_fds=() if fd is None else (fd,), limit=MAX_LINE_BYTES)
            except BaseException:
                # Spawn may have reached the OS before cancellation or response loss.
                self._persisted = False
                raise
        finally:
            if fd is not None:
                os.close(fd)
        self._diagnostic_stage("process_identity")
        self.identity = _process_identity(self.process.pid)
        self._diagnostic_stage("process_receipt")
        await self._receipt({"kind": "process_started", "contract": CONTRACT, "run_id": self.run_id,
                             "process": self.identity, "started_at": time.time()})
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def _read(self):
        line = await self._read_line()
        try:
            event = json.loads(line)
            _require(isinstance(event, dict), "PM_NATIVE_EVENT_INVALID")
            _json(event)
        except (ValueError, UnicodeError, TypeError):
            await self._receipt({"kind": "invalid_native_event", "run_id": self.run_id,
                                 "raw_utf8": line.decode("utf-8", errors="replace")})
            raise NativeRuntimeError("PM_NATIVE_EVENT_INVALID") from None
        if self._sent:
            envelope = {"run_id": self.run_id, "seq": len(self._events) + 1, "event": event}
            self._spool_record({"source": "native", **envelope})
            self._events.append(envelope)
            try:
                await _call(self._event_sink, envelope)
            except BaseException:
                self._persisted = False
                raise
        _require(not ("method" in event and "id" in event), "PM_NATIVE_UNEXPECTED_REQUEST")
        return event

    async def _request(self, method, params):
        self._request_id += 1
        request_id = self._request_id
        await self._send({"id": request_id, "method": method, "params": params})
        while True:
            event = await self._read()
            if event.get("id") == request_id:
                _require("error" not in event and isinstance(event.get("result"), dict),
                         "PM_NATIVE_REQUEST_FAILED")
                return event["result"]
            _require("id" not in event, "PM_NATIVE_RESPONSE_MISMATCH")

    async def preflight(self) -> dict:
        if self.capability is None:
            started = time.monotonic()
            self._diagnostic_stage("initialize")
            try:
                self.capability = await asyncio.wait_for(self._preflight(), PREFLIGHT_TIMEOUT)
                self._diagnostic_stage("preflight_receipt")
                await self._receipt({"kind": "preflight", "run_id": self.run_id, "capability": self.capability})
            except BaseException as error:
                failure = self._preflight_failure(error, "preflight", started, PREFLIGHT_TIMEOUT)
                if failure is error:
                    raise
                raise failure from None
        return copy.deepcopy(self.capability)

    async def _preflight(self):
        await self._request("initialize", {"clientInfo": {"name": "batc_pm", "version": "1"},
                                           "capabilities": {"experimentalApi": True}})
        self._diagnostic_stage("initialized")
        await self._send({"method": "initialized", "params": {}})
        self._diagnostic_stage("config_read")
        config = await self._request("config/read", {"cwd": str(self.workspace), "includeLayers": True})
        self._diagnostic_stage("config_validate")
        validate_config(config.get("config"))
        self._diagnostic_stage("skills_read")
        skills = await self._request("skills/list", {"cwds": [str(self.workspace)], "forceReload": True})
        self._diagnostic_stage("skills_validate")
        _require(isinstance(skills.get("data"), list) and len(skills["data"]) == 1)
        _require(isinstance(skills["data"][0], dict))
        _require(skills["data"][0].get("skills") == [] and skills["data"][0].get("errors") == [])
        params = {"cwd": str(self.workspace), "ephemeral": True, "environments": [],
                  "selectedCapabilityRoots": [], "dynamicTools": [], "sandbox": "read-only",
                  "approvalPolicy": "never", "allowProviderModelFallback": False}
        if self.requested_model is not None:
            params["model"] = self.requested_model
        self._diagnostic_stage("thread_start")
        started = await self._request("thread/start", params)
        self._diagnostic_stage("thread_validate")
        thread = started.get("thread", {})
        _require(isinstance(thread, dict))
        _require(started.get("sandbox") == {"type": "readOnly", "networkAccess": False})
        _require(started.get("approvalPolicy") == "never" and started.get("instructionSources") == [])
        _require(thread.get("environments") == [] and thread.get("turns") == []
                 and thread.get("ephemeral") is True and started.get("cwd") == str(self.workspace))
        _require(isinstance(thread.get("id"), str) and bool(thread["id"]))
        _require(isinstance(started.get("model"), str) and bool(started["model"]))
        _require(self.requested_model is None or started["model"] == self.requested_model)
        self._diagnostic_stage("mcp_read")
        inventory = await self._request("mcpServerStatus/list", {"threadId": thread["id"], "limit": 100})
        self._diagnostic_stage("mcp_validate")
        _require(inventory.get("data") == [] and inventory.get("nextCursor") is None)
        return {"contract": CONTRACT, "adapter": "codex-native-linux", "version": VERSION, "source": SOURCE,
                "run_id": self.run_id, "thread_id": thread["id"], "actual_model": started["model"],
                "options_confirmed": True, "isolated_config_confirmed": True,
                "external_tools_disabled": True, "environment_access": False, "skills": [], "mcp_servers": [],
                "tool_catalog": "not_exposed_by_protocol", "model_execution_verified": False,
                "internal_tool_options_readback": "not_exposed_by_protocol",
                "auth_file_readable": self.auth_bound, "auth_validity": "unverified",
                "automatic_pm_turn": self.auth_bound and not self.offline,
                "config_digest": hashlib.sha256(_json(config["config"])).hexdigest()}

    async def run_turn(self, prompt: str, *, before_send, record_event,
                       context_digest: str | None = None, timeout: float = 300) -> dict:
        _require(not self._attempted, "PM_NATIVE_ALREADY_SENT")
        _require(isinstance(prompt, str) and bool(prompt.strip()) and len(prompt.encode()) <= MAX_LINE_BYTES,
                 "PM_NATIVE_PROMPT_INVALID")
        _require(0 < timeout <= 3600, "PM_NATIVE_TIMEOUT_INVALID")
        _require(context_digest is None or isinstance(context_digest, str)
                 and bool(re.fullmatch(r"[0-9a-f]{64}", context_digest)), "PM_NATIVE_CONTEXT_DIGEST_INVALID")
        _require(callable(before_send) and callable(record_event) and callable(self.record_lifecycle),
                 "PM_NATIVE_DURABLE_CALLBACKS_REQUIRED")
        capability = await self.preflight()
        _require(capability["automatic_pm_turn"], "PM_NATIVE_AUTH_UNAVAILABLE")
        self._attempted = True
        digest = _digest(prompt)
        client_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"batc-pm:{self.run_id}:{digest}"))
        receipt = {"kind": "send_intent", "run_id": self.run_id, "thread_id": capability["thread_id"],
                   "process": self.identity, "prompt_digest": digest, "client_message_id": client_id,
                   "context_digest": context_digest, "request_id": self._request_id + 1,
                   "capability": capability}
        self._spool_record({"source": "adapter", **receipt})
        try:
            await _call(before_send, receipt)
        except BaseException:
            self._persisted = False
            raise
        self._event_sink, self._sent = record_event, True
        kwargs = {"run_id": self.run_id, "thread_id": capability["thread_id"], "prompt_digest": digest,
                  "client_message_id": client_id, "actual_model": capability["actual_model"]}

        async def collect():
            response = await self._request("turn/start", {
                "threadId": capability["thread_id"], "clientUserMessageId": client_id,
                "input": [{"type": "text", "text": prompt}], "environments": [],
                "model": capability["actual_model"], "approvalPolicy": "never",
                "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
            })
            _require(isinstance(response.get("turn"), dict)
                     and isinstance(response["turn"].get("id"), str), "PM_NATIVE_TURN_ID_INVALID")
            while True:
                assessment = assess_turn(self._events, **kwargs)
                if assessment["reason"] != "native_completion_missing":
                    return assessment
                await self._read()

        try:
            result = await asyncio.wait_for(collect(), timeout)
        except (NativeRuntimeError, OSError, ValueError, asyncio.TimeoutError) as error:
            result = assess_turn(self._events, **kwargs, stream_complete=False)
            result["reason"] = getattr(error, "code", "native_timeout_or_transport_error")
        await self._receipt({"kind": "turn_assessment", "run_id": self.run_id, "assessment": result})
        return result


async def probe(scratch_root: Path, run_id: str, **kwargs) -> dict:
    """No model input. Returns an explicit unsupported report on a failed boundary check."""
    runtime = None
    try:
        runtime = NativePmRuntime(scratch_root, run_id, **kwargs)
        async with runtime:
            capability = await runtime.preflight()
        return {**capability, "process_exited": bool(runtime.exit_receipt
                                                     and runtime.exit_receipt["process_death_confirmed"])}
    except (NativeRuntimeError, OSError, ValueError, asyncio.TimeoutError) as error:
        return {"contract": CONTRACT, "adapter": "codex-native-linux", "automatic_pm_turn": False,
                "options_confirmed": False, "reason": getattr(error, "code", "native_probe_failed"),
                "diagnostic": copy.deepcopy(runtime.preflight_diagnostic) if runtime else None}
