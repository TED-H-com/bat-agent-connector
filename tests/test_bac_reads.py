"""Client reads of a host's workspace and session locations over bac/1."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json

import pytest

from bat_agent_connector import bac
from bat_agent_connector import client as client_mod
from bat_agent_connector.client import BatClient
from bat_agent_connector.errors import (
    BacReplyError,
    BacUnavailable,
    ChannelNotAllowed,
    InvokeError,
    InvokeTimeout,
)
from tests.conftest import make_config
from tests.mockbat import MockBat


@pytest.fixture
async def host():
    mock = MockBat(bac=True)
    mock.bac.enable_workspace(mock)
    await mock.start()
    try:
        yield mock
    finally:
        await mock.stop()


def client_for(host, **kwargs):
    cfg = make_config(host, writes=True, orchestrate=True, tabs=True)
    return BatClient(cfg.host("h1"), device_id="bac-test", **kwargs)


def feature_frames(mock: MockBat) -> list[dict]:
    return [f for f in mock.bac.frames if f.get("channel") != "bac:hello"]


def hello_frames(mock: MockBat) -> list[dict]:
    return [f for f in mock.bac.frames if f.get("channel") == "bac:hello"]


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


async def test_workspace_read(host):
    doc = expected_document(host)
    rev = calc_revision(doc)
    expected = {
        "hostId": host.bac.host_id,
        "profileId": "default",
        "source": "persisted",
        "windowId": None,
        "revision": rev,
        "document": doc,
        "appendTargetEligible": True,
    }
    before_ws = copy.deepcopy(host.ws_doc)
    client = client_for(host)
    try:
        res = await client.bac_workspace_read("default")
        assert res == expected
        assert res["appendTargetEligible"] is True
        assert len(host.bac.frames) == 2
        assert host.bac.frames[0]["channel"] == "bac:hello"
        ff = feature_frames(host)
        assert len(ff) == 1
        frame = ff[0]
        assert set(frame) == {"type", "id", "channel", "params"}
        assert frame["type"] == "invoke"
        assert frame["channel"] == "bac:workspace-read"
        assert frame["params"] == {"profileId": "default"}
        assert isinstance(frame["id"], str) and frame["id"].startswith("batc-")
        res["document"]["workspaces"].append({"id": "mutated", "name": "mutated", "folderPath": "/tmp/m"})
        res["document"]["activeWorkspaceId"] = "mutated"
        assert host.ws_doc == before_ws
    finally:
        await client.close()

    ro_cfg = make_config(host, writes=False, orchestrate=False, tabs=False)
    ro_client = BatClient(ro_cfg.host("h1"), device_id="bac-test")
    try:
        ro_res = await ro_client.bac_workspace_read("default")
        assert ro_res == expected
    finally:
        await ro_client.close()


async def test_session_locate(host):
    doc = expected_document(host)
    prof_rev = calc_revision(doc)
    loc_rev = calc_revision([["default", "persisted", None, prof_rev]])
    first_row = {
        "profileId": "default",
        "workspaceId": "ws-1",
        "terminalId": "sess-claude-0001",
        "windowId": None,
        "source": "persisted",
        "profileRevision": prof_rev,
    }
    client = client_for(host)
    try:
        res = await client.bac_session_locate("sess-claude-0001")
        assert res == {
            "hostId": host.bac.host_id,
            "locationRevision": loc_rev,
            "disposition": "located",
            "consistency": "sequential",
            "atomic": False,
            "locations": [first_row],
        }
        ff = feature_frames(host)
        assert len(ff) == 1
        assert ff[0]["channel"] == "bac:session-locate"
        assert ff[0]["params"] == {"sessionId": "sess-claude-0001"}

        missing = await client.bac_session_locate("sess-unknown-9999")
        assert missing == {
            "hostId": host.bac.host_id,
            "locationRevision": loc_rev,
            "disposition": "unregistered",
            "consistency": "sequential",
            "atomic": False,
            "locations": [],
        }

        second_row = {
            "profileId": "work",
            "workspaceId": "ws-2",
            "terminalId": "sess-claude-0001",
            "windowId": "win-2",
            "source": "live",
            "profileRevision": prof_rev,
        }
        host.bac.extra_locations = [second_row]
        ambig = await client.bac_session_locate("sess-claude-0001")
        assert ambig == {
            "hostId": host.bac.host_id,
            "locationRevision": loc_rev,
            "disposition": "ambiguous",
            "consistency": "sequential",
            "atomic": False,
            "locations": [first_row, second_row],
        }
    finally:
        await client.close()


async def test_bac_supports(host):
    client = client_for(host)
    try:
        assert client.bac_supports() is False
        assert client.bac_supports("workspaceRead") is False
        await client.bac_workspace_read("default")
        assert client.bac_supports() is True
        assert client.bac_supports("workspaceRead") is True
        assert client.bac_supports("workspaceRead", "sessionLocate") is True
        assert client.bac_supports("workspaceAddTerminal") is False
        assert client.bac_supports("workspaceRead", "gitObserve") is False
        assert client.bac_supports("unknownFeature") is False
    finally:
        await client.close()
    assert client.bac_supports() is False
    assert client.bac_supports("workspaceRead") is False

    plain = MockBat()
    await plain.start()
    try:
        async with client_for(plain) as plain_client:
            assert plain_client.bac_supports() is False
            assert plain_client.bac_supports("workspaceRead") is False
    finally:
        await plain.stop()


async def test_not_available_without_advertisement():
    mock = MockBat()
    await mock.start()
    try:
        client = client_for(mock)
        try:
            with pytest.raises(BacUnavailable) as exc_info:
                await client.bac_workspace_read("default")
            assert str(exc_info.value) == "h1: bac:workspace-read is not available on this connection"
            with pytest.raises(BacUnavailable) as exc_info:
                await client.bac_session_locate("sess-claude-0001")
            assert str(exc_info.value) == "h1: bac:session-locate is not available on this connection"
            assert [f for f in mock.frames if str(f.get("channel", "")).startswith("bac:")] == []
            assert client.connected is True
            assert await client.invoke("app:get-version") == "9.9.9"
        finally:
            await client.close()
    finally:
        await mock.stop()


async def test_not_available_features_off():
    mock = MockBat(bac=True)
    await mock.start()
    try:
        client = client_for(mock)
        try:
            with pytest.raises(BacUnavailable) as exc_info:
                await client.bac_workspace_read("default")
            assert str(exc_info.value) == "h1: bac:workspace-read is not available on this connection"
            with pytest.raises(BacUnavailable) as exc_info:
                await client.bac_session_locate("sess-claude-0001")
            assert str(exc_info.value) == "h1: bac:session-locate is not available on this connection"
            assert len(hello_frames(mock)) == 1
            assert feature_frames(mock) == []
            assert client.connected is True
            assert await client.invoke("app:get-version") == "9.9.9"
        finally:
            await client.close()
    finally:
        await mock.stop()


async def test_not_available_unsupported_advertisement(host):
    host.bac.advertisement = {"protocol": "bac/2", "minor": 0}
    client = client_for(host)
    try:
        with pytest.raises(BacUnavailable) as exc_info:
            await client.bac_workspace_read("default")
        assert str(exc_info.value) == "h1: bac:workspace-read is not available on this connection"
        with pytest.raises(BacUnavailable) as exc_info:
            await client.bac_session_locate("sess-claude-0001")
        assert str(exc_info.value) == "h1: bac:session-locate is not available on this connection"
        assert host.bac.frames == []
        assert [f for f in host.frames if str(f.get("channel", "")).startswith("bac:")] == []
        assert client.connected is True
        assert await client.invoke("app:get-version") == "9.9.9"
    finally:
        await client.close()


async def test_not_available_refused_hello(host):
    host.bac.storage_error = "STORAGE_UNAVAILABLE"
    client = client_for(host)
    try:
        with pytest.raises(BacUnavailable) as exc_info:
            await client.bac_workspace_read("default")
        assert str(exc_info.value) == "h1: bac:workspace-read is not available on this connection"
        with pytest.raises(BacUnavailable) as exc_info:
            await client.bac_session_locate("sess-claude-0001")
        assert str(exc_info.value) == "h1: bac:session-locate is not available on this connection"
        assert len(hello_frames(host)) == 1
        assert feature_frames(host) == []
        assert client.connected is True
        assert await client.invoke("app:get-version") == "9.9.9"
    finally:
        await client.close()


async def test_not_available_locate_disabled(host):
    host.bac.features["sessionLocate"] = False
    client = client_for(host)
    try:
        res = await client.bac_workspace_read("default")
        assert res["profileId"] == "default"
        with pytest.raises(BacUnavailable) as exc_info:
            await client.bac_session_locate("sess-claude-0001")
        assert str(exc_info.value) == "h1: bac:session-locate is not available on this connection"
        ff = feature_frames(host)
        assert [f["channel"] for f in ff] == ["bac:workspace-read"]
        assert client.connected is True
        assert await client.invoke("app:get-version") == "9.9.9"
    finally:
        await client.close()


@pytest.mark.parametrize("bad_profile", ["a:b", "", "a b", None, 7])
async def test_invalid_profile_id_refused_before_connect(host, bad_profile):
    client = client_for(host)
    try:
        with pytest.raises(BacUnavailable) as exc_info:
            await client.bac_workspace_read(bad_profile)  # type: ignore[arg-type]
        assert str(exc_info.value) == "h1: bac:workspace-read cannot address this profile"
        assert str(exc_info.value).endswith("cannot address this profile")
        assert host.frames == []
        assert client.connected is False
    finally:
        await client.close()


@pytest.mark.parametrize("bad_session", ["設", "", "a b", None])
async def test_invalid_session_id_refused_before_connect(host, bad_session):
    client = client_for(host)
    try:
        with pytest.raises(BacUnavailable) as exc_info:
            await client.bac_session_locate(bad_session)  # type: ignore[arg-type]
        assert str(exc_info.value) == "h1: bac:session-locate cannot address this session"
        assert str(exc_info.value).endswith("cannot address this session")
        assert host.frames == []
        assert client.connected is False
    finally:
        await client.close()


async def test_error_envelopes_not_retried(host):
    async with client_for(host) as client:
        host.bac.read_error = "WORKSPACE_UNAVAILABLE"
        with pytest.raises(BacReplyError) as exc_info:
            await client.bac_workspace_read("default")
        assert exc_info.value.code == "WORKSPACE_UNAVAILABLE"
        assert exc_info.value.details == {}
        assert exc_info.value.retryable is False
        assert len(feature_frames(host)) == 1

        host.bac.read_error = None
        host.bac.frames.clear()
        with pytest.raises(BacReplyError) as exc_info:
            await client.bac_workspace_read("other")
        assert exc_info.value.code == "PROFILE_NOT_FOUND"
        assert exc_info.value.details == {}
        assert exc_info.value.retryable is False
        assert len(feature_frames(host)) == 1

        host.bac.frames.clear()
        host.bac.locate_error = "LOCATION_UNAVAILABLE"
        with pytest.raises(BacReplyError) as exc_info:
            await client.bac_session_locate("sess-claude-0001")
        assert exc_info.value.code == "LOCATION_UNAVAILABLE"
        assert exc_info.value.details == {"profileIds": []}
        assert exc_info.value.retryable is False
        assert len(feature_frames(host)) == 1


@pytest.mark.parametrize(
    "kind,bad_fn,leaked_tokens",
    [
        (
            "read",
            lambda valid, other_host: {"ok": True, "value": {**valid, "hostId": other_host}},
            [],
        ),
        (
            "read",
            lambda valid, other_host: {"ok": True, "value": {**valid, "profileId": "other-profile-secret"}},
            ["other-profile-secret"],
        ),
        (
            "read",
            lambda valid, other_host: {"ok": True, "value": {**valid, "revision": "sha256:" + "A" * 64}},
            ["A" * 64],
        ),
        ("read", lambda valid, other_host: {"ok": True, "value": []}, []),
        ("read", lambda valid, other_host: {"ok": True}, []),
        ("read", lambda valid, other_host: None, []),
        ("read", lambda valid, other_host: "text", ["text"]),
        (
            "read",
            lambda valid, other_host: {"ok": False, "error": {"code": "lowercase"}},
            ["lowercase"],
        ),
        (
            "locate",
            lambda valid, other_host: {
                "ok": True,
                "value": {
                    **valid,
                    "locations": [{**valid["locations"][0], "terminalId": "sess-other-9999"}],
                },
            },
            ["sess-other-9999"],
        ),
        (
            "locate",
            lambda valid, other_host: {
                "ok": True,
                "value": {
                    **valid,
                    "disposition": "located",
                    "locations": [valid["locations"][0], copy.deepcopy(valid["locations"][0])],
                },
            },
            [],
        ),
    ],
)
async def test_invalid_replies(host, kind, bad_fn, leaked_tokens):
    doc = expected_document(host)
    rev = calc_revision(doc)
    other_host = "h_" + "a" * 32
    if kind == "read":
        channel = bac.WORKSPACE_READ_CHANNEL
        valid = {
            "hostId": host.bac.host_id,
            "profileId": "default",
            "source": "persisted",
            "windowId": None,
            "revision": rev,
            "document": doc,
            "appendTargetEligible": True,
        }
    else:
        channel = bac.SESSION_LOCATE_CHANNEL
        row = {
            "profileId": "default",
            "workspaceId": "ws-1",
            "terminalId": "sess-claude-0001",
            "windowId": None,
            "source": "persisted",
            "profileRevision": rev,
        }
        valid = {
            "hostId": host.bac.host_id,
            "locationRevision": calc_revision([["default", "persisted", None, rev]]),
            "disposition": "located",
            "consistency": "sequential",
            "atomic": False,
            "locations": [row],
        }
    host.bac.result_override = {channel: bad_fn(valid, other_host)}
    async with client_for(host) as client:
        with pytest.raises(InvokeError, match="invalid BAC reply") as exc_info:
            if kind == "read":
                await client.bac_workspace_read("default")
            else:
                await client.bac_session_locate("sess-claude-0001")
        msg = str(exc_info.value)
        assert msg == f"h1: {channel}: invalid BAC reply"
        assert host.bac.host_id not in msg
        assert other_host not in msg
        for token in leaked_tokens:
            assert token not in msg
        assert len(feature_frames(host)) == 1


async def test_limit_from_hello(host):
    doc = expected_document(host)
    rev = calc_revision(doc)
    host.bac.extra_locations = [
        {
            "profileId": "work",
            "workspaceId": "ws-2",
            "terminalId": "sess-claude-0001",
            "windowId": None,
            "source": "persisted",
            "profileRevision": rev,
        }
    ]
    host.bac.limits["locationMatches"] = 1
    async with client_for(host) as client1:
        with pytest.raises(InvokeError, match="invalid BAC reply") as exc_info:
            await client1.bac_session_locate("sess-claude-0001")
        assert str(exc_info.value) == "h1: bac:session-locate: invalid BAC reply"

    host.bac.limits["locationMatches"] = 100
    async with client_for(host) as client2:
        res = await client2.bac_session_locate("sess-claude-0001")
        assert res["disposition"] == "ambiguous"
        assert len(res["locations"]) == 2


async def test_busy_and_other_invoke_error(host, monkeypatch):
    pauses: list[float] = []

    async def record_pause(delay: float) -> None:
        pauses.append(delay)

    monkeypatch.setattr(client_mod, "_retry_pause", record_pause)
    async with client_for(host) as client:
        host.bac.busy_replies = 2
        res = await client.bac_workspace_read("default")
        assert res["profileId"] == "default"
        assert len(feature_frames(host)) == 3
        assert pauses == [0.5, 1.0]

        host.bac.frames.clear()
        pauses.clear()
        host.bac.busy_replies = 3
        with pytest.raises(InvokeError) as exc_info:
            await client.bac_workspace_read("default")
        assert "Remote server is busy" in str(exc_info.value)
        assert len(feature_frames(host)) == 3
        assert pauses == [0.5, 1.0]

        pauses.clear()
        calls = 0

        async def fake_roundtrip(frame, timeout, on_transport=None):
            nonlocal calls
            calls += 1
            return {"type": "invoke-error", "id": "x", "error": "boom"}

        monkeypatch.setattr(client, "_roundtrip", fake_roundtrip)
        with pytest.raises(InvokeError, match="boom"):
            await client.bac_workspace_read("default")
        assert calls == 1
        assert pauses == []


async def test_timeout_and_recovery(host, monkeypatch):
    pauses: list[float] = []

    async def record_pause(delay: float) -> None:
        pauses.append(delay)

    monkeypatch.setattr(client_mod, "_retry_pause", record_pause)
    deadline = {"seconds": 0.2}
    monkeypatch.setattr(client_mod, "timeout_for", lambda channel: deadline["seconds"])
    host.bac.drop_feature_replies = True
    client = client_for(host)
    try:
        with pytest.raises(InvokeTimeout):
            await client.bac_workspace_read("default")
        assert len(feature_frames(host)) == 1
        assert pauses == []

        host.bac.drop_feature_replies = False
        deadline["seconds"] = 15.0
        res = await client.bac_workspace_read("default")
        assert res["profileId"] == "default"
        assert len(feature_frames(host)) == 2
    finally:
        await client.close()


async def test_reconnect_sends_expected_host_id(host, monkeypatch):
    pauses: list[float] = []

    async def record_pause(delay: float) -> None:
        pauses.append(delay)

    monkeypatch.setattr(client_mod, "_retry_pause", record_pause)
    client = client_for(host)
    try:
        res1 = await client.bac_workspace_read("default")
        await host.drop_all()
        res2 = await client.bac_workspace_read("default")
        assert res2 == res1
        hellos = hello_frames(host)
        assert len(hellos) == 2
        assert "expectedHostId" not in hellos[0]["params"]
        assert hellos[1]["params"]["expectedHostId"] == host.bac.host_id
    finally:
        await client.close()


async def test_changed_identity_after_reconnect(host, monkeypatch):
    pauses: list[float] = []

    async def record_pause(delay: float) -> None:
        pauses.append(delay)

    monkeypatch.setattr(client_mod, "_retry_pause", record_pause)
    first_id = host.bac.host_id
    client = client_for(host)
    try:
        await client.bac_workspace_read("default")
        assert len(feature_frames(host)) == 1
        host.bac.host_id = "h_" + "f" * 32
        await host.drop_all()
        with pytest.raises(BacUnavailable) as exc_info:
            await client.bac_workspace_read("default")
        assert str(exc_info.value) == "h1: bac:workspace-read is not available on this connection"
        assert len(feature_frames(host)) == 1
        hellos = hello_frames(host)
        assert len(hellos) == 2
        assert hellos[1]["params"]["expectedHostId"] == first_id
    finally:
        await client.close()


async def test_hello_in_progress_succeeds_when_restored(host, monkeypatch):
    client = client_for(host)
    try:
        await client.connect()
        saved = client.bac
        ws = client._ws
        client.bac = None
        real_connect = client.connect
        calls = 0

        async def wrapped_connect() -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                client.bac = saved
            await real_connect()

        monkeypatch.setattr(client, "connect", wrapped_connect)
        res = await client.bac_workspace_read("default")
        assert res["profileId"] == "default"
        assert calls == 2
        assert len(feature_frames(host)) == 1
        assert client._ws is ws
    finally:
        await client.close()


async def test_hello_in_progress_raises_when_never_restored(host, monkeypatch):
    client = client_for(host)
    try:
        await client.connect()
        ws = client._ws
        client.bac = None
        real_connect = client.connect
        calls = 0

        async def wrapped_connect() -> None:
            nonlocal calls
            calls += 1
            await real_connect()

        monkeypatch.setattr(client, "connect", wrapped_connect)
        with pytest.raises(BacUnavailable) as exc_info:
            await client.bac_workspace_read("default")
        assert str(exc_info.value) == "h1: bac:workspace-read is not available on this connection"
        assert calls == 3
        assert feature_frames(host) == []
        assert client._ws is ws
        assert client.connected is True
    finally:
        await client.close()


async def test_other_paths_stay_closed(host):
    async with client_for(host) as client:
        assert client.bac_supports("workspaceRead", "sessionLocate") is True
        for channel, params in (
            ("bac:workspace-read", {"profileId": "default"}),
            ("bac:session-locate", {"sessionId": "sess-claude-0001"}),
        ):
            with pytest.raises(ChannelNotAllowed):
                await client.invoke(channel, params)
            with pytest.raises(ChannelNotAllowed):
                await client.guard_read(channel, params)
        assert feature_frames(host) == []


async def test_concurrency_five_reads(host):
    client = client_for(host)
    try:
        results = await asyncio.gather(*(client.bac_workspace_read("default") for _ in range(5)))
        assert len(results) == 5
        assert all(r == results[0] for r in results)
        assert len(hello_frames(host)) == 1
        ff = feature_frames(host)
        assert len(ff) == 5
        ids = {f["id"] for f in ff}
        assert len(ids) == 5
        assert all(isinstance(mid, str) and mid.startswith("batc-") for mid in ids)
    finally:
        await client.close()
