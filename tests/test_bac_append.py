"""Client tab registration through bac:workspace-add-terminal and whole-document fallback."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json

import pytest

from bat_agent_connector import bac, registry
from bat_agent_connector import client as client_mod
from bat_agent_connector.client import BatClient
from bat_agent_connector.errors import (
    BacOutcomeUnknown,
    ChannelNotAllowed,
    ConnectionLost,
    InvokeError,
    InvokeTimeout,
    ResourceReadOnly,
    WriteRefused,
)
from bat_agent_connector.resource_policy import BY_ACTION, WriteGrant
from tests.conftest import adopt, make_config
from tests.mockbat import MockBat

ADD = "bac:workspace-add-terminal"
READ = "bac:workspace-read"
HELLO = "bac:hello"
NEW_RESULT_KEYS = frozenset({"route", "identical", "refusal", "source", "window_id", "revision"})


@pytest.fixture
async def host(controller_context):
    mock = MockBat(bac=True)
    mock.bac.enable_append(mock)
    await mock.start()
    try:
        yield mock
    finally:
        await mock.stop()


def client_for(host, **kwargs):
    cfg = make_config(host, writes=True, orchestrate=True, tabs=True)
    return BatClient(cfg.host("h1"), device_id="bac-test", **kwargs)


def tab_grant(sid: str) -> WriteGrant:
    adopt(sid)
    entry = registry.get("h1", sid)
    return WriteGrant(
        "h1",
        "workspace.register_tab",
        sid,
        BY_ACTION["workspace.register_tab"].channels,
        controller_authority=entry["controller_authority"],
        controller_incarnation=entry["controller_incarnation"],
    )


def terminal(sid: str, **extra) -> dict:
    return {
        "id": sid,
        "workspaceId": "ws-1",
        "title": "T",
        "type": "terminal",
        "cwd": "/srv/demo",
        "agentPreset": "claude-code",
        **extra,
    }


def bac_frames(mock: MockBat, channel: str) -> list[dict]:
    if mock.bac is None:
        return []
    return [f for f in mock.bac.frames if f.get("channel") == channel]


def feature_frames(mock: MockBat) -> list[dict]:
    if mock.bac is None:
        return []
    return [f for f in mock.bac.frames if f.get("channel") != HELLO]


def expected_document(mock: MockBat) -> dict:
    raw = mock.ws_doc
    return copy.deepcopy({
        "workspaces": raw.get("workspaces", []),
        "activeWorkspaceId": raw.get("activeWorkspaceId"),
        "activeGroup": raw.get("activeGroup"),
        "terminals": raw.get("terminals", []),
        "activeTerminalId": raw.get("activeTerminalId"),
    })


def calc_revision(obj: object) -> str:
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def assert_no_legacy_frames(mock: MockBat) -> None:
    channels = mock.channels()
    assert "workspace:load" not in channels
    assert "workspace:save" not in channels


# --------------------------------------------------------------------------- 1. Append through the host


async def test_append_through_host(host):
    before = copy.deepcopy(host.ws_doc)
    rev_before = calc_revision(expected_document(host))
    term = terminal("sess-add-1")
    grant = tab_grant("sess-add-1")
    client = client_for(host)
    try:
        res = await client.append_workspace_terminal("default", term, grant=grant)
    finally:
        await client.close()

    expected_ws = copy.deepcopy(before)
    expected_ws["terminals"].append(copy.deepcopy(term))
    assert host.ws_doc == expected_ws
    rev_after = calc_revision(expected_document(host))
    assert res == {
        "appended": True,
        "saved": True,
        "route": "bac",
        "source": "persisted",
        "window_id": None,
        "revision": rev_after,
    }
    assert [f["channel"] for f in host.bac.frames] == [HELLO, READ, ADD]
    assert_no_legacy_frames(host)
    write_frame = bac_frames(host, ADD)[0]
    assert set(write_frame) == {"type", "id", "channel", "params"}
    assert write_frame["params"] == {
        "profileId": "default",
        "expectedRevision": rev_before,
        "terminal": term,
    }


# --------------------------------------------------------------------------- 2. Whole-document route unchanged


@pytest.mark.parametrize("case", ["no-bac"])
async def test_whole_document_route_unchanged(controller_context, case):
    mock = MockBat(bac=False)
    sid = f"sess-legacy-{case}"
    term = terminal(sid)

    await mock.start()
    try:
        client = client_for(mock)
        try:
            res = await client.append_workspace_terminal("default", term, grant=tab_grant(sid))
        finally:
            await client.close()
    finally:
        await mock.stop()

    assert res == {
        "appended": True,
        "saved": True,
        "verified_previous_terminals_kept": True,
        "lost_terminals": [],
        "lost_workspaces": [],
    }
    assert NEW_RESULT_KEYS.isdisjoint(res)
    assert mock.channels() == ["workspace:load", "workspace:load", "workspace:save", "workspace:load"]
    assert bac_frames(mock, READ) == []
    assert bac_frames(mock, ADD) == []


async def test_default_registration_bytes_when_limit_omitted(host):
    del host.bac.limits["registrationBytes"]
    sid = "sess-default-limit"
    client = client_for(host)
    try:
        res = await client.append_workspace_terminal("default", terminal(sid), grant=tab_grant(sid))
    finally:
        await client.close()
    assert res == {
        "appended": True,
        "saved": True,
        "route": "bac",
        "source": "persisted",
        "window_id": None,
        "revision": calc_revision(expected_document(host)),
    }


# --------------------------------------------------------------------------- 3. Gate order


async def test_gate_order_host_and_whole_document(host):
    events: list[str] = []
    frames_at_send: list[str] = []

    async def before_frame():
        events.append("frame")

    def on_write_channel(ch: str):
        events.append(f"channel:{ch}")

    def before_send():
        events.append("send")
        frames_at_send.extend(f["channel"] for f in host.bac.frames)

    def on_transport():
        events.append("transport")

    sid = "sess-gate-order-1"
    client = client_for(host)
    try:
        res = await client.append_workspace_terminal(
            "default",
            terminal(sid),
            grant=tab_grant(sid),
            before_frame=before_frame,
            on_write_channel=on_write_channel,
            before_send=before_send,
            on_transport=on_transport,
        )
    finally:
        await client.close()

    assert res["appended"] is True
    assert events == ["frame", f"channel:{ADD}", "send", "transport"]
    assert frames_at_send == [HELLO, READ]

    legacy_events: list[str] = []

    async def legacy_before_frame():
        legacy_events.append("frame")

    def legacy_on_write_channel(ch: str):
        legacy_events.append(f"channel:{ch}")

    def legacy_before_send():
        legacy_events.append("send")

    def legacy_on_transport():
        legacy_events.append("transport")

    legacy_mock = MockBat(bac=False)
    await legacy_mock.start()
    try:
        sid2 = "sess-gate-order-2"
        legacy_client = client_for(legacy_mock)
        try:
            res2 = await legacy_client.append_workspace_terminal(
                "default",
                terminal(sid2),
                grant=tab_grant(sid2),
                before_frame=legacy_before_frame,
                on_write_channel=legacy_on_write_channel,
                before_send=legacy_before_send,
                on_transport=legacy_on_transport,
            )
        finally:
            await legacy_client.close()
    finally:
        await legacy_mock.stop()

    assert res2["appended"] is True
    assert legacy_events == ["frame", "channel:workspace:save", "send", "transport"]


# --------------------------------------------------------------------------- 4. A refusing gate sends nothing


@pytest.mark.parametrize("which", ["before_frame", "on_write_channel", "before_send"])
async def test_refusing_gate_sends_nothing(host, which):
    before = copy.deepcopy(host.ws_doc)
    boom = WriteRefused("stop")
    transport_called = False

    async def bf():
        if which == "before_frame":
            raise boom

    def owc( _ch: str):
        if which == "on_write_channel":
            raise boom

    def bs():
        if which == "before_send":
            raise boom

    def ot():
        nonlocal transport_called
        transport_called = True

    sid = f"sess-refuse-{which}"
    client = client_for(host)
    try:
        with pytest.raises(WriteRefused) as exc_info:
            await client.append_workspace_terminal(
                "default",
                terminal(sid),
                grant=tab_grant(sid),
                before_frame=bf,
                on_write_channel=owc,
                before_send=bs,
                on_transport=ot,
            )
    finally:
        await client.close()

    assert exc_info.value is boom
    assert bac_frames(host, ADD) == []
    assert transport_called is False
    assert host.ws_doc == before
    assert_no_legacy_frames(host)


# --------------------------------------------------------------------------- 5. The final gate checks the grant again


async def test_final_gate_checks_grant_again(host, monkeypatch):
    real_check_grant = client_mod.check_grant
    calls: dict[str, list[dict | None]] = {}
    raise_on_second_add = True
    late_exc = WriteRefused("late")

    def wrapped_check_grant(grant, host_name, channel, target=None):
        real_check_grant(grant, host_name, channel, target)
        calls.setdefault(channel, []).append(target)
        if raise_on_second_add and channel == ADD and len(calls[channel]) == 2:
            raise late_exc

    monkeypatch.setattr(client_mod, "check_grant", wrapped_check_grant)
    before = copy.deepcopy(host.ws_doc)
    transport_called = False

    def ot():
        nonlocal transport_called
        transport_called = True

    sid = "sess-grant-recheck"
    grant = tab_grant(sid)
    client = client_for(host)
    try:
        with pytest.raises(WriteRefused) as exc_info:
            await client.append_workspace_terminal(
                "default", terminal(sid), grant=grant, on_transport=ot
            )
        assert exc_info.value is late_exc
        assert bac_frames(host, ADD) == []
        assert transport_called is False
        assert host.ws_doc == before

        calls.clear()
        raise_on_second_add = False
        res = await client.append_workspace_terminal("default", terminal(sid), grant=grant)
        assert res["appended"] is True
        assert calls == {
            "workspace:save": [{"sessionId": sid}],
            ADD: [{"sessionId": sid}, {"sessionId": sid}],
        }
    finally:
        await client.close()


# --------------------------------------------------------------------------- 6. Grant binding


async def test_grant_binding(host):
    before = copy.deepcopy(host.ws_doc)
    wrong_session_grant = tab_grant("other-session")
    adopt("new-1")

    client0 = client_for(host)
    try:
        with pytest.raises(ResourceReadOnly):
            await client0.append_workspace_terminal("default", terminal("new-1"), grant=None)
    finally:
        await client0.close()
    assert host.frames == []
    assert host.bac.frames == []

    client1 = client_for(host)
    try:
        with pytest.raises(ResourceReadOnly) as exc_info:
            await client1.append_workspace_terminal(
                "default", terminal("new-1"), grant=wrong_session_grant
            )
    finally:
        await client1.close()
    assert exc_info.value.code == "GRANT_MISMATCH"
    assert [f["channel"] for f in host.bac.frames] == [HELLO]
    assert host.ws_doc == before

    host.bac.frames.clear()
    entry = registry.get("h1", "new-1")
    save_only_grant = WriteGrant(
        "h1",
        "workspace.register_tab",
        "new-1",
        frozenset({"workspace:save"}),
        controller_authority=entry["controller_authority"],
        controller_incarnation=entry["controller_incarnation"],
    )
    client2 = client_for(host)
    try:
        with pytest.raises(ResourceReadOnly):
            await client2.append_workspace_terminal(
                "default", terminal("new-1"), grant=save_only_grant
            )
    finally:
        await client2.close()
    assert [f["channel"] for f in host.bac.frames] == [HELLO]
    assert host.ws_doc == before

    legacy_mock = MockBat(bac=False)
    await legacy_mock.start()
    try:
        legacy_client = client_for(legacy_mock)
        try:
            res = await legacy_client.append_workspace_terminal(
                "default", terminal("new-1"), grant=wrong_session_grant
            )
        finally:
            await legacy_client.close()
    finally:
        await legacy_mock.stop()
    assert res == {
        "appended": True,
        "saved": True,
        "verified_previous_terminals_kept": True,
        "lost_terminals": [],
        "lost_workspaces": [],
    }


# --------------------------------------------------------------------------- 7. Tier and arguments


async def test_tier_and_arguments(host):
    grant = tab_grant("sess-tier")
    cfg_no_tabs = make_config(host, writes=True, orchestrate=True, tabs=False)
    no_tabs_client = BatClient(cfg_no_tabs.host("h1"), device_id="bac-test")
    try:
        with pytest.raises(ChannelNotAllowed):
            await no_tabs_client.append_workspace_terminal(
                "default", terminal("sess-tier"), grant=grant
            )
    finally:
        await no_tabs_client.close()
    assert host.frames == []
    assert host.bac.frames == []

    client = client_for(host)
    try:
        no_id = {k: v for k, v in terminal("sess-tier").items() if k != "id"}
        with pytest.raises(InvokeError):
            await client.append_workspace_terminal("default", no_id, grant=grant)
        assert host.frames == []
        assert host.bac.frames == []

        with pytest.raises(ChannelNotAllowed):
            await client.invoke(ADD, {"profileId": "default"}, grant=grant)
        assert bac_frames(host, ADD) == []

        with pytest.raises(ChannelNotAllowed):
            await client.guard_read(ADD, {})
        assert bac_frames(host, ADD) == []
    finally:
        await client.close()


# --------------------------------------------------------------------------- 8. Already registered


async def test_already_registered_equal_different_and_duplicate(host):
    sid = "sess-present"
    cand = terminal(sid)
    grant = tab_grant(sid)

    # (a) Equal except for runtime/host-stored fields
    host.ws_doc["terminals"] = [
        {**cand, "sdkSessionId": "sdk-99", "agentParams": {"sandboxMode": "workspace-write"}}
    ]
    before_a = copy.deepcopy(host.ws_doc)
    rev_a = calc_revision(expected_document(host))
    callbacks: list[str] = []

    async def bf():
        callbacks.append("before_frame")

    client = client_for(host)
    try:
        res_a = await client.append_workspace_terminal(
            "default",
            cand,
            grant=grant,
            before_frame=bf,
            on_write_channel=lambda ch: callbacks.append(f"channel:{ch}"),
            before_send=lambda: callbacks.append("before_send"),
            on_transport=lambda: callbacks.append("on_transport"),
        )
        assert res_a == {
            "appended": False,
            "reason": "already present",
            "identical": True,
            "route": "bac",
            "revision": rev_a,
        }
        assert [f["channel"] for f in host.bac.frames] == [HELLO, READ]
        assert bac_frames(host, ADD) == []
        assert callbacks == []
        assert host.ws_doc == before_a

        # (b) Stored with another title
        host.bac.frames.clear()
        host.ws_doc["terminals"] = [terminal(sid, title="Different")]
        before_b = copy.deepcopy(host.ws_doc)
        res_b = await client.append_workspace_terminal("default", cand, grant=grant)
        assert res_b == {
            "appended": False,
            "saved": False,
            "reason": "already present",
            "identical": False,
            "route": "bac",
        }
        assert bac_frames(host, ADD) == []
        assert host.ws_doc == before_b

        # (c) Stored twice (two equal entries)
        host.bac.frames.clear()
        host.ws_doc["terminals"] = [ copy.deepcopy(cand), copy.deepcopy(cand) ]
        before_c = copy.deepcopy(host.ws_doc)
        res_c = await client.append_workspace_terminal("default", cand, grant=grant)
        assert res_c == {
            "appended": False,
            "saved": False,
            "reason": "already present",
            "identical": False,
            "route": "bac",
        }
        assert bac_frames(host, ADD) == []
        assert host.ws_doc == before_c
    finally:
        await client.close()


# --------------------------------------------------------------------------- 9. Unknown workspace


async def test_unknown_workspace_refused_locally(host):
    before = copy.deepcopy(host.ws_doc)
    sid = "sess-ws-missing"
    client = client_for(host)
    try:
        res = await client.append_workspace_terminal(
            "default", terminal(sid, workspaceId="ws-missing"), grant=tab_grant(sid)
        )
    finally:
        await client.close()

    assert res == {
        "appended": False,
        "saved": False,
        "reason": "host refused the registration",
        "refusal": "WORKSPACE_NOT_FOUND",
        "route": "bac",
    }
    assert bac_frames(host, ADD) == []
    assert host.ws_doc == before
    assert_no_legacy_frames(host)


# --------------------------------------------------------------------------- 10. Read refusal is a certain refusal


async def test_read_refusal_is_certain_refusal(host):
    before = copy.deepcopy(host.ws_doc)
    host.bac.read_error = "WORKSPACE_UNAVAILABLE"
    sid = "sess-read-refused"
    client = client_for(host)
    try:
        res = await client.append_workspace_terminal(
            "default", terminal(sid), grant=tab_grant(sid)
        )
    finally:
        await client.close()

    assert res == {
        "appended": False,
        "saved": False,
        "reason": "host refused the registration",
        "refusal": "WORKSPACE_UNAVAILABLE",
        "route": "bac",
    }
    assert bac_frames(host, ADD) == []
    assert host.ws_doc == before
    assert_no_legacy_frames(host)


# --------------------------------------------------------------------------- 11. Document client cannot judge goes to host


async def test_unjudgeable_document_goes_to_host(host):
    host.ws_doc["terminals"] = {}
    before = copy.deepcopy(host.ws_doc)
    sid = "sess-unjudgeable"
    client = client_for(host)
    try:
        res = await client.append_workspace_terminal(
            "default", terminal(sid), grant=tab_grant(sid)
        )
    finally:
        await client.close()

    assert res == {
        "appended": False,
        "saved": False,
        "reason": "host refused the registration",
        "refusal": "WORKSPACE_UNAVAILABLE",
        "route": "bac",
    }
    assert len(bac_frames(host, ADD)) == 1
    assert host.ws_doc == before
    assert_no_legacy_frames(host)


# --------------------------------------------------------------------------- 12. Certain refusals of the append


_CERTAIN_REFUSAL_CODES = sorted(bac.APPEND_NOT_APPLIED - {"REVISION_MISMATCH", "TERMINAL_EXISTS"})


@pytest.mark.parametrize("code", _CERTAIN_REFUSAL_CODES, ids=_CERTAIN_REFUSAL_CODES)
async def test_certain_append_refusals(host, code):
    before = copy.deepcopy(host.ws_doc)
    host.bac.append_error = code
    sid = f"sess-refusal-{code}"
    client = client_for(host)
    try:
        res = await client.append_workspace_terminal(
            "default", terminal(sid), grant=tab_grant(sid)
        )
    finally:
        await client.close()

    assert res == {
        "appended": False,
        "saved": False,
        "reason": "host refused the registration",
        "refusal": code,
        "route": "bac",
    }
    assert len(bac_frames(host, ADD)) == 1
    assert host.ws_doc == before
    assert_no_legacy_frames(host)


async def test_append_refusal_terminal_exists(host):
    before = copy.deepcopy(host.ws_doc)
    host.bac.append_error = "TERMINAL_EXISTS"
    sid = "sess-refusal-exists"
    client = client_for(host)
    try:
        res = await client.append_workspace_terminal(
            "default", terminal(sid), grant=tab_grant(sid)
        )
    finally:
        await client.close()

    assert res == {
        "appended": False,
        "saved": False,
        "reason": "already present",
        "identical": False,
        "route": "bac",
    }
    assert len(bac_frames(host, ADD)) == 1
    assert host.ws_doc == before
    assert_no_legacy_frames(host)


# --------------------------------------------------------------------------- 13. Revision retry


async def test_revision_retry_succeeds_on_second_attempt(host, monkeypatch):
    pauses: list[float] = []

    async def record_pause(delay: float) -> None:
        pauses.append(delay)

    monkeypatch.setattr(client_mod, "_retry_pause", record_pause)
    other = terminal("sess-other-concurrent")
    first_rev = calc_revision(expected_document(host))
    append_calls = 0

    def on_before_append():
        nonlocal append_calls
        append_calls += 1
        if append_calls == 1:
            host.ws_doc["terminals"].append(copy.deepcopy(other))

    host.bac.before_append = on_before_append
    send_count = 0
    write_channels: list[str] = []
    transport_count = 0

    def bs():
        nonlocal send_count
        send_count += 1

    def owc(ch: str):
        write_channels.append(ch)

    def ot():
        nonlocal transport_count
        transport_count += 1

    sid = "sess-retry-ok"
    cand = terminal(sid)
    client = client_for(host)
    try:
        res = await client.append_workspace_terminal(
            "default",
            cand,
            grant=tab_grant(sid),
            before_send=bs,
            on_write_channel=owc,
            on_transport=ot,
        )
    finally:
        await client.close()

    assert res == {
        "appended": True,
        "saved": True,
        "route": "bac",
        "source": "persisted",
        "window_id": None,
        "revision": calc_revision(expected_document(host)),
    }
    assert [f["channel"] for f in feature_frames(host)] == [READ, ADD, READ, ADD]
    adds = bac_frames(host, ADD)
    assert adds[0]["params"]["expectedRevision"] == first_rev
    second_read_doc = copy.deepcopy(expected_document(host))
    second_read_doc["terminals"] = [t for t in second_read_doc["terminals"] if t["id"] != sid]
    second_rev = calc_revision(second_read_doc)
    assert adds[1]["params"]["expectedRevision"] == second_rev
    assert second_rev != first_rev
    assert pauses == [0.5]
    assert send_count == 2
    assert write_channels == [ADD, ADD]
    assert transport_count == 2
    stored_ids = [t["id"] for t in host.ws_doc["terminals"]]
    assert stored_ids.count(other["id"]) == 1
    assert stored_ids.count(sid) == 1
    assert_no_legacy_frames(host)


@pytest.mark.parametrize("retries,expected_pauses", [(2, [0.5]), (1, [])])
async def test_revision_retry_exhausted(host, monkeypatch, retries, expected_pauses):
    pauses: list[float] = []

    async def record_pause(delay: float) -> None:
        pauses.append(delay)

    monkeypatch.setattr(client_mod, "_retry_pause", record_pause)
    counter = 0

    def always_change():
        nonlocal counter
        counter += 1
        host.ws_doc["terminals"].append(terminal(f"sess-conc-{counter}"))

    host.bac.before_append = always_change
    sid = f"sess-give-up-{retries}"
    client = client_for(host)
    try:
        res = await client.append_workspace_terminal(
            "default", terminal(sid), grant=tab_grant(sid), retries=retries
        )
    finally:
        await client.close()

    assert res == {
        "appended": False,
        "saved": False,
        "reason": "workspace kept changing; gave up to avoid clobbering",
        "route": "bac",
    }
    assert len(bac_frames(host, ADD)) == retries
    assert pauses == expected_pauses
    assert all(t["id"] != sid for t in host.ws_doc["terminals"])
    assert_no_legacy_frames(host)


# --------------------------------------------------------------------------- 14. Unknown outcomes: one write frame, then nothing


async def test_unknown_outcome_write_failed(host):
    before = copy.deepcopy(host.ws_doc)
    host.bac.append_fault = "write_failed"
    sid = "sess-write-failed"
    client = client_for(host)
    try:
        with pytest.raises(BacOutcomeUnknown) as exc_info:
            await client.append_workspace_terminal(
                "default", terminal(sid), grant=tab_grant(sid)
            )
    finally:
        await client.close()

    assert "WRITE_FAILED" in str(exc_info.value)
    assert host.bac.host_id not in str(exc_info.value)
    assert len(bac_frames(host, ADD)) == 1
    assert host.ws_doc == before
    assert_no_legacy_frames(host)


async def test_unknown_outcome_write_failed_applied(host):
    host.bac.append_fault = "write_failed_applied"
    sid = "sess-write-failed-applied"
    term = terminal(sid)
    client = client_for(host)
    try:
        with pytest.raises(BacOutcomeUnknown):
            await client.append_workspace_terminal("default", term, grant=tab_grant(sid))
    finally:
        await client.close()

    assert len(bac_frames(host, ADD)) == 1
    assert host.ws_doc["terminals"][-1] == term
    assert_no_legacy_frames(host)


async def test_unknown_outcome_internal_error_and_unlisted_codes(host):
    before = copy.deepcopy(host.ws_doc)
    host.bac.append_fault = "internal_error"
    sid1 = "sess-internal-error"
    client = client_for(host)
    try:
        with pytest.raises(BacOutcomeUnknown):
            await client.append_workspace_terminal(
                "default", terminal(sid1), grant=tab_grant(sid1)
            )
        assert len(bac_frames(host, ADD)) == 1
        assert host.ws_doc == before
        assert_no_legacy_frames(host)

        host.bac.frames.clear()
        host.bac.append_error = "RESPONSE_TOO_LARGE"
        sid2 = "sess-resp-too-large"
        with pytest.raises(BacOutcomeUnknown):
            await client.append_workspace_terminal(
                "default", terminal(sid2), grant=tab_grant(sid2)
            )
        assert len(bac_frames(host, ADD)) == 1
        assert host.ws_doc == before
        assert_no_legacy_frames(host)

        host.bac.frames.clear()
        host.bac.append_error = None
        host.bac.result_override[ADD] = {
            "ok": False,
            "error": {
                "code": "SOMETHING_NEW",
                "message": "Unlisted error code",
                "retryable": False,
                "details": {},
            },
        }
        sid3 = "sess-something-new"
        with pytest.raises(BacOutcomeUnknown):
            await client.append_workspace_terminal(
                "default", terminal(sid3), grant=tab_grant(sid3)
            )
        assert len(bac_frames(host, ADD)) == 1
        assert_no_legacy_frames(host)
    finally:
        await client.close()


@pytest.mark.parametrize(
    "override_kind",
    ["bogus-value", "wrong-terminal-id", "wrong-host-id", "appended-int"],
)
async def test_unknown_outcome_invalid_reply(host, override_kind):
    sid = f"sess-bad-reply-{override_kind}"
    valid_value = {
        "hostId": host.bac.host_id,
        "profileId": "default",
        "source": "persisted",
        "windowId": None,
        "terminalId": sid,
        "appended": True,
        "revision": "sha256:" + "a" * 64,
    }
    if override_kind == "bogus-value":
        override = {"ok": True, "value": "bogus"}
    elif override_kind == "wrong-terminal-id":
        override = {"ok": True, "value": {**valid_value, "terminalId": "other-id"}}
    elif override_kind == "wrong-host-id":
        override = {"ok": True, "value": {**valid_value, "hostId": "h_" + "9" * 32}}
    else:
        override = {"ok": True, "value": {**valid_value, "appended": 1}}

    host.bac.result_override[ADD] = override
    client = client_for(host)
    try:
        with pytest.raises(BacOutcomeUnknown) as exc_info:
            await client.append_workspace_terminal(
                "default", terminal(sid), grant=tab_grant(sid)
            )
    finally:
        await client.close()

    msg = str(exc_info.value)
    assert msg.endswith("invalid BAC reply")
    assert "bogus" not in msg
    assert host.bac.host_id not in msg
    assert len(bac_frames(host, ADD)) == 1
    assert_no_legacy_frames(host)


async def test_unknown_outcome_lost_reply_and_lost_request(host):
    sid1 = "sess-lost-reply"
    term1 = terminal(sid1)
    host.bac.append_fault = "lost_reply"
    client1 = client_for(host)
    try:
        with pytest.raises(ConnectionLost):
            await client1.append_workspace_terminal("default", term1, grant=tab_grant(sid1))
    finally:
        await client1.close()
    assert len(bac_frames(host, ADD)) == 1
    assert host.ws_doc["terminals"][-1] == term1
    assert_no_legacy_frames(host)

    host.bac.frames.clear()
    before2 = copy.deepcopy(host.ws_doc)
    sid2 = "sess-lost-request"
    host.bac.append_fault = "lost_request"
    client2 = client_for(host)
    try:
        with pytest.raises(ConnectionLost):
            await client2.append_workspace_terminal(
                "default", terminal(sid2), grant=tab_grant(sid2)
            )
    finally:
        await client2.close()
    assert len(bac_frames(host, ADD)) == 1
    assert host.ws_doc == before2
    assert_no_legacy_frames(host)


async def test_unknown_outcome_timeout_and_busy(host, monkeypatch):
    pauses: list[float] = []

    async def record_pause(delay: float) -> None:
        pauses.append(delay)

    monkeypatch.setattr(client_mod, "_retry_pause", record_pause)
    monkeypatch.setattr(
        client_mod, "timeout_for", lambda channel: 0.3 if channel == ADD else 15.0
    )

    sid1 = "sess-timeout"
    term1 = terminal(sid1)
    host.bac.before_append = lambda: setattr(host.bac, "drop_feature_replies", True)
    client1 = client_for(host)
    try:
        with pytest.raises(InvokeTimeout):
            await client1.append_workspace_terminal("default", term1, grant=tab_grant(sid1))
    finally:
        await client1.close()
    assert len(bac_frames(host, ADD)) == 1
    assert host.ws_doc["terminals"][-1] == term1
    assert_no_legacy_frames(host)

    host.bac.drop_feature_replies = False
    host.bac.before_append = None
    host.bac.frames.clear()
    pauses.clear()
    before2 = copy.deepcopy(host.ws_doc)
    sid2 = "sess-busy"
    client2 = client_for(host)
    try:
        with pytest.raises(InvokeError) as exc_info:
            await client2.append_workspace_terminal(
                "default",
                terminal(sid2),
                grant=tab_grant(sid2),
                before_send=lambda: setattr(host.bac, "busy_replies", 1),
            )
    finally:
        await client2.close()
    assert "Remote server is busy" in str(exc_info.value)
    assert len(bac_frames(host, ADD)) == 1
    assert host.ws_doc == before2
    assert pauses == []
    assert_no_legacy_frames(host)


# --------------------------------------------------------------------------- 15. Read-back after an unknown outcome


async def test_read_back_after_unknown_outcome(host):
    sid1 = "sess-readback-applied"
    term1 = terminal(sid1)
    grant1 = tab_grant(sid1)
    host.bac.append_fault = "lost_reply"
    client1 = client_for(host)
    try:
        with pytest.raises(ConnectionLost):
            await client1.append_workspace_terminal("default", term1, grant=grant1)
        assert len(bac_frames(host, ADD)) == 1

        res1 = await client1.append_workspace_terminal("default", term1, grant=grant1)
        assert res1 == {
            "appended": False,
            "reason": "already present",
            "identical": True,
            "route": "bac",
            "revision": calc_revision(expected_document(host)),
        }
        assert len(bac_frames(host, ADD)) == 1
        hellos = bac_frames(host, HELLO)
        assert len(hellos) == 2
        assert hellos[1]["params"]["expectedHostId"] == host.bac.host_id
    finally:
        await client1.close()

    host.bac.frames.clear()
    sid2 = "sess-readback-unapplied"
    term2 = terminal(sid2)
    grant2 = tab_grant(sid2)
    host.bac.append_fault = "lost_request"
    client2 = client_for(host)
    try:
        with pytest.raises(ConnectionLost):
            await client2.append_workspace_terminal("default", term2, grant=grant2)
        assert len(bac_frames(host, ADD)) == 1

        res2 = await client2.append_workspace_terminal("default", term2, grant=grant2)
        assert res2 == {
            "appended": True,
            "saved": True,
            "route": "bac",
            "source": "persisted",
            "window_id": None,
            "revision": calc_revision(expected_document(host)),
        }
        assert len(bac_frames(host, ADD)) == 2
    finally:
        await client2.close()


# --------------------------------------------------------------------------- 16. Connection changes between read and write


async def test_connection_changes_between_read_and_write_feature_disabled(host):
    before_a = copy.deepcopy(host.ws_doc)
    sid_a = "sess-conn-change-a"
    term_a = terminal(sid_a)
    grant_a = tab_grant(sid_a)
    client_a = client_for(host)
    try:
        async def bf_a():
            host.bac.features["workspaceAddTerminal"] = False
            await client_a.close()
            await client_a.connect()

        res_a1 = await client_a.append_workspace_terminal(
            "default", term_a, grant=grant_a, before_frame=bf_a
        )
        assert res_a1 == {
            "appended": False,
            "saved": False,
            "reason": "host refused the registration",
            "refusal": "BAC_UNAVAILABLE",
            "route": "bac",
        }
        assert bac_frames(host, ADD) == []
        assert_no_legacy_frames(host)
        assert host.ws_doc == before_a

        res_a2 = await client_a.append_workspace_terminal("default", term_a, grant=grant_a)
        assert res_a2 == {
            "appended": True,
            "saved": True,
            "verified_previous_terminals_kept": True,
            "lost_terminals": [],
            "lost_workspaces": [],
        }
        assert "workspace:save" in host.channels()
        assert host.ws_doc["terminals"][-1] == term_a
    finally:
        await client_a.close()


async def test_connection_changes_between_read_and_write_closed(host):
    before_b = copy.deepcopy(host.ws_doc)
    sid_b = "sess-conn-change-b"
    client_b = client_for(host)
    try:
        async def bf_b():
            await client_b.close()

        with pytest.raises(ConnectionLost):
            await client_b.append_workspace_terminal(
                "default", terminal(sid_b), grant=tab_grant(sid_b), before_frame=bf_b
            )
        assert bac_frames(host, ADD) == []
        assert_no_legacy_frames(host)
        assert host.ws_doc == before_b
    finally:
        await client_b.close()


async def test_connection_changes_between_read_and_write_hello_in_progress(host):
    before_c = copy.deepcopy(host.ws_doc)
    sid_c = "sess-conn-change-c"
    client_c = client_for(host)
    try:
        async def bf_c():
            client_c.bac = None

        res_c = await client_c.append_workspace_terminal(
            "default", terminal(sid_c), grant=tab_grant(sid_c), before_frame=bf_c
        )
        assert res_c == {
            "appended": False,
            "saved": False,
            "reason": "host refused the registration",
            "refusal": "BAC_UNAVAILABLE",
            "route": "bac",
        }
        assert bac_frames(host, ADD) == []
        assert_no_legacy_frames(host)
        assert host.ws_doc == before_c
        assert client_c.connected is True
    finally:
        await client_c.close()


# --------------------------------------------------------------------------- 17. Two registrations at once


async def test_two_registrations_at_once(host, monkeypatch):
    pauses: list[float] = []

    async def record_pause(delay: float) -> None:
        pauses.append(delay)

    monkeypatch.setattr(client_mod, "_retry_pause", record_pause)

    sid1 = "sess-gather-1"
    sid2 = "sess-gather-2"
    grant1 = tab_grant(sid1)
    grant2 = tab_grant(sid2)
    client = client_for(host)
    try:
        r1, r2 = await asyncio.gather(
            client.append_workspace_terminal("default", terminal(sid1), grant=grant1),
            client.append_workspace_terminal("default", terminal(sid2), grant=grant2),
        )
        assert r1["appended"] is True and r1["route"] == "bac"
        assert r2["appended"] is True and r2["route"] == "bac"
        stored_ids = [t["id"] for t in host.ws_doc["terminals"]]
        assert stored_ids.count(sid1) == 1
        assert stored_ids.count(sid2) == 1
        add_count = len(bac_frames(host, ADD))
        assert add_count in (2, 3)
        if add_count == 3:
            assert pauses == [0.5]
        else:
            assert pauses == []

        # Concurrent calls with the same terminal
        sid_same = "sess-gather-same"
        term_same = terminal(sid_same)
        grant_same = tab_grant(sid_same)
        s1, s2 = await asyncio.gather(
            client.append_workspace_terminal("default", term_same, grant=grant_same),
            client.append_workspace_terminal("default", term_same, grant=grant_same),
        )
        rev_after = calc_revision(expected_document(host))
        expected_appended = {
            "appended": True,
            "saved": True,
            "route": "bac",
            "source": "persisted",
            "window_id": None,
            "revision": rev_after,
        }
        expected_identical = {
            "appended": False,
            "reason": "already present",
            "identical": True,
            "route": "bac",
            "revision": rev_after,
        }
        assert sorted([s1, s2], key=lambda d: d["appended"]) == [
            expected_identical,
            expected_appended,
        ]
        assert [t["id"] for t in host.ws_doc["terminals"]].count(sid_same) == 1
    finally:
        await client.close()
