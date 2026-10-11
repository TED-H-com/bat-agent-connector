"""Native evidence tests use mocked app-server replies; real probes never send a prompt."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from bat_agent_connector import pm_native_runtime as native

RUN, THREAD, TURN, CLIENT, MODEL = "owned-run", "owned-thread", "owned-turn", "owned-message", "fixture-model"
PROMPT = "Compare the supplied evidence. Do not adopt or execute anything."


def config():
    result = {"mcp_servers": {}}
    for dotted, value in native._settings(Path("/private-profile")).items():
        cursor = result
        parts = dotted.split(".")
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[parts[-1]] = value
    return result


def protocol(prompt=PROMPT, client_id=CLIENT):
    return [
        {"id": 6, "result": {"turn": {"id": TURN, "status": "inProgress", "items": []}}},
        {"method": "turn/started", "params": {"threadId": THREAD, "turn": {"id": TURN}}},
        {"method": "item/completed", "params": {"threadId": THREAD, "turnId": TURN,
             "item": {"type": "agentMessage", "id": "answer", "text": "A proposal, not an adopted plan."}}},
        {"method": "turn/completed", "params": {"threadId": THREAD, "turn": {
            "id": TURN, "status": "completed", "error": None, "itemsView": "full", "items": [
                {"type": "userMessage", "id": "native-user", "clientId": client_id,
                 "content": [{"type": "text", "text": prompt}]},
                {"type": "agentMessage", "id": "answer", "text": "A proposal, not an adopted plan.",
                 "phase": "final_answer"},
            ]}}},
    ]


def envelopes(events=None):
    return [{"run_id": RUN, "seq": index, "event": event}
            for index, event in enumerate(protocol() if events is None else events, 1)]


def assess(events=None, **kwargs):
    return native.assess_turn(envelopes() if events is None else events, run_id=RUN, thread_id=THREAD,
                             prompt_digest=native._digest(PROMPT), client_message_id=CLIENT,
                             actual_model=MODEL, **kwargs)


def test_complete_turn_keeps_native_message_model_and_source():
    result = assess()
    assert result == {"status": "completed", "complete": True, "reason": None,
                      "content": "A proposal, not an adopted plan.", "run_id": RUN,
                      "thread_id": THREAD, "native_turn_id": TURN, "native_message_id": "answer",
                      "actual_model": MODEL, "source_event_seqs": [4]}


@pytest.mark.parametrize("count", [0, 1, 2, 3])
def test_ack_or_streamed_answer_is_not_completion(count):
    result = assess(envelopes()[:count])
    assert not result["complete"] and result["content"] is None


@pytest.mark.parametrize("mutation", [
    lambda rows: rows[0].update(seq=True),
    lambda rows: rows[1].update(seq=7),
    lambda rows: rows[1].update(run_id="other"),
    lambda rows: rows[1].update(event=[]),
    lambda rows: rows[1]["event"].update(params=[]),
    lambda rows: rows[1]["event"]["params"].update(threadId="other"),
    lambda rows: rows[1]["event"]["params"].update(turn=[]),
    lambda rows: rows[0]["event"]["result"]["turn"].update(id="other"),
    lambda rows: rows[0]["event"].update(error={"message": "failure"}),
    lambda rows: rows[2]["event"]["params"].update(turnId="other"),
    lambda rows: rows[2]["event"]["params"]["item"].update(type="mcpToolCall"),
    lambda rows: rows[2]["event"].update(id=55),
    lambda rows: rows[-1]["event"]["params"].update(turn=None),
    lambda rows: rows[-1]["event"]["params"]["turn"].update(id="other"),
    lambda rows: rows[-1]["event"]["params"]["turn"].update(itemsView="incremental"),
    lambda rows: rows[-1]["event"]["params"]["turn"].update(items=None),
    lambda rows: rows[-1]["event"]["params"]["turn"]["items"][0].update(clientId="other"),
    lambda rows: rows[-1]["event"]["params"]["turn"]["items"][0].update(content=[None]),
    lambda rows: rows[-1]["event"]["params"]["turn"]["items"][0]["content"][0].update(text="different input"),
    lambda rows: rows[-1]["event"]["params"]["turn"]["items"][1].update(id="native-user"),
    lambda rows: rows[-1]["event"]["params"]["turn"]["items"][1].update(type="commandExecution"),
    lambda rows: rows[-1]["event"]["params"]["turn"]["items"][1].update(text="x" * (native.MAX_OUTPUT_CHARS + 1)),
])
def test_unproven_provenance_never_imports_an_answer(mutation):
    rows = envelopes()
    mutation(rows)
    result = assess(rows)
    assert not result["complete"] and result["content"] is None


@pytest.mark.parametrize("status", ["failed", "interrupted", "inProgress", None])
def test_noncompleted_terminal_is_incomplete(status):
    rows = envelopes()
    rows[-1]["event"]["params"]["turn"]["status"] = status
    result = assess(rows)
    assert result["status"] == "incomplete" and result["content"] is None


def test_gap_multiple_turns_and_late_items_cannot_be_imported():
    for extra in (protocol()[1], protocol()[2], protocol()[-1]):
        result = assess(envelopes([*protocol(), extra]))
        assert not result["complete"]
    assert assess(stream_complete=False)["reason"] == "native_stream_incomplete"


@pytest.mark.parametrize("path,value", [
    ("mcp_servers", {"inherited": {"command": "unsafe"}}),
    ("features.hooks", True), ("features.apps", True), ("features.multi_agent_v2", True),
    ("features.shell_tool", True), ("features.plugins", True),
    ("skills.include_instructions", True), ("skills.bundled.enabled", True),
    ("agents.enabled", True), ("project_doc_max_bytes", 1000),
    ("cloud.skills.enabled", True), ("tools.update_plan.enabled", True),
    ("features", None), ("notify", ["unsafe"]), ("approval_policy", "on-request"),
])
def test_boundary_rejects_unsafe_or_unreadable_config(path, value):
    doc = config()
    parts = path.split(".")
    parent = doc
    for part in parts[:-1]:
        parent = parent[part]
    parent[parts[-1]] = value
    with pytest.raises(native.NativeRuntimeError, match="BOUNDARY_UNVERIFIED"):
        native.validate_config(doc)


class FakeStdin:
    def __init__(self, process):
        self.process = process

    def write(self, data):
        self.process.receive(json.loads(data))

    async def drain(self):
        pass

    def close(self):
        self.process.returncode = 0
        self.process.stdout.feed_eof()


class FakeProcess:
    pid = 424242

    def __init__(self, cwd, *, bad_config=False, mode="completed"):
        self.cwd, self.bad_config, self.mode = cwd, bad_config, mode
        self.returncode = None
        self.requests = []
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_eof()
        self.stdin = FakeStdin(self)

    def output(self, event):
        self.stdout.feed_data(json.dumps(event).encode() + b"\n")

    def receive(self, request):
        self.requests.append(request)
        method = request["method"]
        if "id" not in request:
            return
        if method == "initialize":
            result = {}
        elif method == "config/read":
            result = {"config": config()}
            if self.bad_config:
                result["config"]["features"]["hooks"] = True
        elif method == "skills/list":
            result = {"data": [{"cwd": str(self.cwd), "skills": [], "errors": []}]}
        elif method == "thread/start":
            result = {"thread": {"id": THREAD, "environments": [], "turns": [], "ephemeral": True},
                      "cwd": str(self.cwd), "model": MODEL, "approvalPolicy": "never",
                      "sandbox": {"type": "readOnly", "networkAccess": False}, "instructionSources": []}
        elif method == "mcpServerStatus/list":
            result = {"data": [], "nextCursor": None}
        elif method == "turn/start":
            if self.mode == "invalid":
                self.stdout.feed_data(b"not a native JSON object\n")
                return
            events = protocol(request["params"]["input"][0]["text"], request["params"]["clientUserMessageId"])
            events[0]["id"] = request["id"]
            for event in events if self.mode == "completed" else events[:3]:
                self.output(event)
            if self.mode == "lost":
                self.stdout.feed_eof()
            return
        else:
            raise AssertionError(method)
        self.output({"id": request["id"], "result": result})

    async def wait(self):
        while self.returncode is None:
            await asyncio.sleep(0.001)
        return self.returncode


@pytest.fixture
def fake_runtime(tmp_path, monkeypatch):
    host = tmp_path / "host"
    (host / ".codex").mkdir(parents=True)
    (host / ".agents").mkdir()
    (host / ".codex" / "auth.json").write_text("fixture-only")
    monkeypatch.setattr(native, "_host_paths", lambda: (host, host / ".codex"))
    monkeypatch.setattr(native, "SYSTEM_CONFIG_ROOT", tmp_path / "absent-system-config")
    monkeypatch.setattr(native.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(native, "_process_identity", lambda pid: {
        "pid": pid, "start_ticks": 25, "boot_id_digest": "a" * 64})

    async def version(_):
        return native.VERSION

    monkeypatch.setattr(native, "_runtime_version", version)
    options, processes, commands = {}, [], []

    async def spawn(*command, **kwargs):
        commands.append((command, kwargs))
        process = FakeProcess(kwargs["cwd"], **options)
        processes.append(process)
        return process

    monkeypatch.setattr(native, "_spawn", spawn)
    return tmp_path / "scratch", options, processes, commands


async def test_one_turn_preserves_intent_events_exit_and_readonly_auth_binding(fake_runtime):
    scratch, _, processes, commands = fake_runtime
    lifecycle, events, intents = [], [], []
    async with native.NativePmRuntime(scratch, RUN, model=MODEL, record_lifecycle=lifecycle.append) as runtime:
        cap = await runtime.preflight()
        assert cap["automatic_pm_turn"] and cap["options_confirmed"]
        assert cap["tool_catalog"] == "not_exposed_by_protocol"
        assert not cap["model_execution_verified"] and cap["auth_validity"] == "unverified"
        result = await runtime.run_turn(PROMPT, context_digest="c" * 64,
                                        before_send=intents.append, record_event=events.append)
        assert result["complete"] and result["native_message_id"] == "answer"
        with pytest.raises(native.NativeRuntimeError, match="ALREADY_SENT"):
            await runtime.run_turn(PROMPT, before_send=intents.append, record_event=events.append)
    assert not runtime.directory.exists()
    assert runtime.exit_receipt["process_death_confirmed"]
    assert intents[0]["context_digest"] == "c" * 64
    assert intents[0]["prompt_digest"] == native._digest(PROMPT)
    assert intents[0]["capability"] == cap
    assert events == runtime._events
    assert [event["seq"] for event in events] == [1, 2, 3, 4]
    assert [entry["kind"] for entry in lifecycle] == ["process_started", "preflight", "turn_assessment", "process_exited"]
    assert [request["method"] for request in processes[0].requests].count("turn/start") == 1
    command, kwargs = commands[0]
    assert command[:4] == ("/bin/bwrap", "--ro-bind", "/", "/")
    assert "--unshare-pid" in command and "--die-with-parent" in command
    assert kwargs["start_new_session"] and len(kwargs["pass_fds"]) == 1
    with pytest.raises(OSError):
        os.fstat(kwargs["pass_fds"][0])
    assert "fixture-only" not in json.dumps([lifecycle, events, intents])


async def test_unsafe_config_fails_before_any_thread_or_model_start(fake_runtime):
    scratch, options, processes, _ = fake_runtime
    options["bad_config"] = True
    result = await native.probe(scratch, RUN)
    assert not result["automatic_pm_turn"] and not result["options_confirmed"]
    assert [request["method"] for request in processes[0].requests] == ["initialize", "initialized", "config/read"]
    assert processes[0].returncode == 0 and not list(scratch.iterdir())


@pytest.mark.parametrize("mode", ["lost", "timeout", "invalid"])
async def test_unknown_stream_never_resends_and_process_death_is_separate_evidence(fake_runtime, mode):
    scratch, options, processes, _ = fake_runtime
    options["mode"] = mode
    lifecycle, events = [], []
    async with native.NativePmRuntime(scratch, RUN, record_lifecycle=lifecycle.append) as runtime:
        result = await runtime.run_turn(PROMPT, before_send=lambda _: None,
                                        record_event=events.append, timeout=0.03)
        assert result["status"] == "unknown" and result["content"] is None
    assert runtime.exit_receipt["process_death_confirmed"]
    assert runtime.exit_receipt["sent"]
    assert [request["method"] for request in processes[0].requests].count("turn/start") == 1
    if mode == "invalid":
        assert any(entry.get("raw_utf8") == "not a native JSON object\n" for entry in lifecycle)


@pytest.mark.parametrize("failure", ["intent", "event", "exit"])
async def test_persistence_failure_retains_private_original_receipts(fake_runtime, failure):
    scratch, _, _, _ = fake_runtime
    lifecycle = []

    def fail(_):
        raise OSError("journal unavailable")

    def save(entry):
        if failure == "exit" and entry["kind"] == "process_exited":
            fail(entry)
        lifecycle.append(entry)

    runtime = native.NativePmRuntime(scratch, RUN, record_lifecycle=save)
    try:
        async with runtime:
            if failure == "intent":
                with pytest.raises(OSError, match="journal unavailable"):
                    await runtime.run_turn(PROMPT, before_send=fail, record_event=lambda _: None)
            else:
                result = await runtime.run_turn(PROMPT, before_send=lambda _: None,
                                                record_event=fail if failure == "event" else lambda _: None)
                assert result["complete"] is (failure == "exit")
        assert runtime.retained_directory == runtime.directory
        assert runtime.exit_receipt["process_death_confirmed"]
        rows = [json.loads(line) for line in (runtime.directory / "events.ndjson").read_text().splitlines()]
        assert rows[-1]["kind"] == "process_exited"
        if failure == "event":
            assert any(row["source"] == "native" for row in rows)
    finally:
        shutil.rmtree(runtime.directory)


async def test_cancel_waits_for_owned_process_death(fake_runtime):
    scratch, options, _, _ = fake_runtime
    options["mode"] = "timeout"
    lifecycle, intents = [], []
    runtime = native.NativePmRuntime(scratch, RUN, record_lifecycle=lifecycle.append)

    async def run():
        async with runtime:
            await runtime.run_turn(PROMPT, before_send=intents.append, record_event=lambda _: None)

    task = asyncio.create_task(run())
    while not intents:
        await asyncio.sleep(0.001)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert lifecycle[-1]["process_death_confirmed"] and lifecycle[-1]["sent"]
    assert not runtime.directory.exists()


def test_managed_configuration_is_not_bypassed(tmp_path, monkeypatch):
    system = tmp_path / "system"
    system.mkdir()
    monkeypatch.setattr(native, "SYSTEM_CONFIG_ROOT", system)
    for name in ["config.toml", "managed_config.toml", "requirements.toml"]:
        path = system / name
        path.write_text("# operator policy")
        with pytest.raises(native.NativeRuntimeError, match="MANAGED_CONFIG_UNSUPPORTED"):
            native._check_host_config()
        path.unlink()


async def test_platform_failure_is_explicit_without_spawning(tmp_path, monkeypatch):
    monkeypatch.setattr(native.sys, "platform", "darwin")
    result = await native.probe(tmp_path, RUN)
    assert result["reason"] == "PM_NATIVE_PLATFORM_UNSUPPORTED"
    assert not result["automatic_pm_turn"]


async def test_launch_attempt_window_retains_original_metadata(fake_runtime, monkeypatch):
    scratch, _, _, _ = fake_runtime

    async def fail_spawn(*_, **__):
        raise OSError("spawn outcome unavailable")

    monkeypatch.setattr(native, "_spawn", fail_spawn)
    runtime = native.NativePmRuntime(scratch, RUN)
    try:
        with pytest.raises(OSError):
            await runtime.__aenter__()
        assert runtime.process_launch_attempted and runtime.process is None
        assert runtime.retained_directory == runtime.directory
        record = json.loads((runtime.directory / "events.ndjson").read_text())
        assert record["kind"] == "process_launch_attempted"
    finally:
        shutil.rmtree(runtime.directory)


async def test_prelaunch_rejection_is_provably_not_started(fake_runtime, monkeypatch):
    scratch, _, processes, _ = fake_runtime

    async def bad_version(_):
        raise native.NativeRuntimeError("PM_NATIVE_VERSION_UNSUPPORTED")

    monkeypatch.setattr(native, "_runtime_version", bad_version)
    runtime = native.NativePmRuntime(scratch, RUN)
    with pytest.raises(native.NativeRuntimeError, match="VERSION_UNSUPPORTED"):
        await runtime.__aenter__()
    assert runtime.process_launch_attempted is False and not processes


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only namespace boundary")
async def test_real_offline_preflight_is_zero_prompt_and_leaves_no_process_or_files(tmp_path):
    if not shutil.which("codex") or not shutil.which("bwrap"):
        pytest.skip("Pinned Codex and bubblewrap are optional installed runtimes")
    try:
        await native._runtime_version(shutil.which("codex"))
    except native.NativeRuntimeError:
        pytest.skip("Installed Codex is outside the pinned adapter contract")
    lifecycle = []
    result = await native.probe(tmp_path, "offline-preflight", use_auth=False, offline=True,
                                record_lifecycle=lifecycle.append)
    # Unsupported kernel user namespaces are an explicit capability failure, not a fake pass.
    if not result["options_confirmed"] and not lifecycle:
        pytest.skip(f"Host cannot start isolated runtime: {result.get('reason')}")
    assert result["options_confirmed"], result
    assert result["process_exited"] and not result["automatic_pm_turn"]
    assert result["mcp_servers"] == [] and result["skills"] == []
    assert result["auth_file_readable"] is False and result["model_execution_verified"] is False
    assert lifecycle[-1]["sent"] is False and lifecycle[-1]["process_death_confirmed"]
    assert not list(tmp_path.iterdir())


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only namespace boundary")
async def test_real_preflight_does_not_inherit_project_tools_or_hooks(tmp_path):
    if not shutil.which("codex") or not shutil.which("bwrap"):
        pytest.skip("Pinned Codex and bubblewrap are optional installed runtimes")
    try:
        await native._runtime_version(shutil.which("codex"))
    except native.NativeRuntimeError:
        pytest.skip("Installed Codex is outside the pinned adapter contract")
    (tmp_path / ".git").mkdir()
    settings = tmp_path / ".codex"
    settings.mkdir()
    config_file = settings / "config.toml"
    config_file.write_text("features.hooks=true\nfeatures.multi_agent=true\nfeatures.shell_tool=true\n")
    original = config_file.read_bytes()
    result = await native.probe(tmp_path / "scratch", "inherited-config-test", use_auth=False, offline=True)
    assert result["options_confirmed"], result
    assert not result["automatic_pm_turn"] and result["process_exited"]
    assert config_file.read_bytes() == original
    assert not list((tmp_path / "scratch").iterdir())


@pytest.mark.skipif(sys.platform != "linux", reason="Linux process identity")
def test_process_inspection_never_signals_or_confuses_reused_ids():
    identity = native._process_identity(os.getpid())
    assert native.inspect_process(identity)["state"] == "alive"
    reused = {**identity, "start_ticks": identity["start_ticks"] + 1}
    assert native.inspect_process(reused)["proof"] == "pid_reused"
    previous_boot = {**identity, "boot_id_digest": "0" * 64}
    assert native.inspect_process(previous_boot)["proof"] == "host_rebooted"
    absent = {**identity, "pid": 2 ** 30}
    assert native.inspect_process(absent)["proof"] == "original_pid_absent"
    assert native.inspect_process({"pid": os.getpid()})["state"] == "unknown"


@pytest.mark.skipif(sys.platform != "linux", reason="Linux process identity")
async def test_process_inspection_distinguishes_original_exited_child():
    child = await asyncio.create_subprocess_exec(sys.executable, "-c", "import sys; sys.stdin.read()",
                                                 stdin=asyncio.subprocess.PIPE)
    identity = native._process_identity(child.pid)
    assert native.inspect_process(identity)["state"] == "alive"
    child.stdin.close()
    await asyncio.wait_for(child.wait(), 5)
    assert native.inspect_process(identity)["state"] == "dead"


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only namespace boundary")
async def test_real_readonly_mount_cannot_change_host_canary(tmp_path):
    bwrap = shutil.which("bwrap")
    if not bwrap:
        pytest.skip("bubblewrap is optional")
    allowed = tmp_path / "owned-writable"
    allowed.mkdir()
    canary = tmp_path / "host-canary"
    canary.write_text("unchanged")
    program = "import pathlib,sys\ntry:\n pathlib.Path(sys.argv[1]).write_text('changed')\nexcept OSError as e:\n sys.exit(0 if e.errno == 30 else 2)\nsys.exit(3)"
    process = await asyncio.create_subprocess_exec(
        bwrap, "--ro-bind", "/", "/", "--bind", str(allowed), str(allowed), "--unshare-net",
        "--unshare-pid", "--die-with-parent", sys.executable, "-c", program, str(canary),
        env={**os.environ, "PYTHONHASHSEED": "0"},
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        _, stderr = await asyncio.wait_for(process.communicate(), 5)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    if process.returncode != 0 and b"Operation not permitted" in stderr:
        pytest.skip("Kernel does not permit this user's namespace creation")
    assert process.returncode == 0, stderr.decode(errors="replace")
    assert canary.read_text() == "unchanged"
