"""Pure grammar and reply validators for BAC workspace and session-location channels."""
from __future__ import annotations

import copy
import json

import pytest

from bat_agent_connector import bac, channels
from bat_agent_connector.errors import BacUnavailable, BatError, ChannelNotAllowed

HOST_ID = "h_0123456789abcdef0123456789abcdef"
EMPTY_REVISION = "sha256:66c1b7a04a05ee220e10341c9baec59bdf92885b193c2838d527a235d2ca5a9c"
ALT_REVISION = "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


def minimal_registration(**overrides):
    base = {
        "id": "sess-claude-0001",
        "workspaceId": "ws-main",
        "title": "Agent 1",
        "type": "terminal",
        "cwd": "/srv/demo",
        "agentPreset": "claude-code",
    }
    base.update(overrides)
    return base


def full_registration(**overrides):
    base = minimal_registration(
        model="claude-opus-4-6",
        permissionMode="default",
        agentParams={"customKey": "customValue", "emptyAllowed": ""},
        worktreePath="/srv/demo-wt",
        worktreeBranch="feat/demo",
    )
    base.update(overrides)
    return base


def valid_workspace_read(**overrides):
    base = {
        "hostId": HOST_ID,
        "profileId": "default",
        "source": "persisted",
        "windowId": None,
        "revision": EMPTY_REVISION,
        "document": {
            "workspaces": [{"id": "ws-main"}],
            "activeWorkspaceId": "ws-main",
            "activeGroup": None,
            "terminals": [],
            "activeTerminalId": None,
        },
        "appendTargetEligible": True,
    }
    base.update(overrides)
    return base


def valid_location_row(**overrides):
    base = {
        "profileId": "default",
        "workspaceId": "ws-main",
        "terminalId": "sess-claude-0001",
        "windowId": None,
        "source": "persisted",
        "profileRevision": EMPTY_REVISION,
    }
    base.update(overrides)
    return base


def valid_session_locate(**overrides):
    base = {
        "hostId": HOST_ID,
        "locationRevision": ALT_REVISION,
        "disposition": "located",
        "consistency": "sequential",
        "atomic": False,
        "locations": [valid_location_row()],
    }
    base.update(overrides)
    return base


def valid_append_result(**overrides):
    base = {
        "hostId": HOST_ID,
        "profileId": "default",
        "source": "live",
        "windowId": "win-1",
        "terminalId": "sess-claude-0001",
        "appended": True,
        "revision": ALT_REVISION,
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "value",
    [
        "default",
        "A-b_c.9",
        "設定檔",
        "a" * 128,
        "設" * 42,
    ],
)
def test_valid_profile_id_accepts(value):
    assert bac.valid_profile_id(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "",
        "a" * 129,
        "設" * 43,
        "a:b",
        "a b",
        "a/b",
        "é",
        "ａ",
        "٣",
        "default\n",
        "㐀",
        None,
        1,
        True,
        b"default",
    ],
)
def test_valid_profile_id_refuses(value):
    assert bac.valid_profile_id(value) is False


@pytest.mark.parametrize(
    "value",
    [
        "sess-claude-0001",
        "a:b.c_d-9",
        "s" * 128,
    ],
)
def test_valid_session_id_accepts(value):
    assert bac.valid_session_id(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "",
        "s" * 129,
        "設",
        "a b",
        "a\n",
        None,
        7,
        True,
        b"sess-claude-0001",
    ],
)
def test_valid_session_id_refuses(value):
    assert bac.valid_session_id(value) is False


@pytest.mark.parametrize("value", [EMPTY_REVISION, ALT_REVISION])
def test_valid_revision_accepts(value):
    assert bac.valid_revision(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "sha256:" + "A" * 64,
        EMPTY_REVISION[:-1] + "C",
        "sha256:" + "a" * 63,
        "sha256:" + "a" * 65,
        "a" * 64,
        "sha512:" + "a" * 64,
        "sha256:g" + "a" * 63,
        EMPTY_REVISION + "\n",
        None,
        1,
        True,
        EMPTY_REVISION.encode("utf-8"),
    ],
)
def test_valid_revision_refuses(value):
    assert bac.valid_revision(value) is False


@pytest.mark.parametrize(
    "terminal",
    [
        minimal_registration(),
        full_registration(),
        minimal_registration(
            model=None,
            permissionMode=None,
            agentParams=None,
            worktreePath=None,
            worktreeBranch=None,
        ),
        minimal_registration(agentParams={"k": ""}),
        minimal_registration(
            agentParams={f"k{i}": f"v{i}" for i in range(bac.AGENT_PARAMS_LIMIT)}
        ),
    ],
)
def test_registration_error_accepts_valid(terminal):
    assert bac.registration_error(terminal) is None


@pytest.mark.parametrize(
    ("terminal", "expected_field"),
    [
        (None, "params.terminal"),
        ([], "params.terminal"),
        ("terminal", "params.terminal"),
        (minimal_registration(sdkSessionId="sdk-1"), "params.terminal"),
        ({k: v for k, v in minimal_registration().items() if k != "id"}, "params.terminal.id"),
        (minimal_registration(id=""), "params.terminal.id"),
        (minimal_registration(id="bad id"), "params.terminal.id"),
        (
            {k: v for k, v in minimal_registration().items() if k != "workspaceId"},
            "params.terminal.workspaceId",
        ),
        (minimal_registration(workspaceId=""), "params.terminal.workspaceId"),
        (minimal_registration(workspaceId="bad ws"), "params.terminal.workspaceId"),
        (
            {k: v for k, v in minimal_registration().items() if k != "title"},
            "params.terminal.title",
        ),
        (minimal_registration(title=""), "params.terminal.title"),
        (minimal_registration(title="bad\0title"), "params.terminal.title"),
        (minimal_registration(title=True), "params.terminal.title"),
        (
            {k: v for k, v in minimal_registration().items() if k != "type"},
            "params.terminal.type",
        ),
        (minimal_registration(type="pty"), "params.terminal.type"),
        (minimal_registration(type=None), "params.terminal.type"),
        (
            {k: v for k, v in minimal_registration().items() if k != "cwd"},
            "params.terminal.cwd",
        ),
        (minimal_registration(cwd=""), "params.terminal.cwd"),
        (minimal_registration(cwd="/srv/\0demo"), "params.terminal.cwd"),
        (
            {k: v for k, v in minimal_registration().items() if k != "agentPreset"},
            "params.terminal.agentPreset",
        ),
        (minimal_registration(agentPreset="other-preset"), "params.terminal.agentPreset"),
        (minimal_registration(agentPreset=None), "params.terminal.agentPreset"),
        (minimal_registration(model=""), "params.terminal.model"),
        (minimal_registration(model="m\0"), "params.terminal.model"),
        (minimal_registration(model=1), "params.terminal.model"),
        (minimal_registration(permissionMode=""), "params.terminal.permissionMode"),
        (minimal_registration(permissionMode="p\0"), "params.terminal.permissionMode"),
        (minimal_registration(permissionMode=False), "params.terminal.permissionMode"),
        (minimal_registration(agentParams=[]), "params.terminal.agentParams"),
        (
            minimal_registration(
                agentParams={f"k{i}": "v" for i in range(bac.AGENT_PARAMS_LIMIT + 1)}
            ),
            "params.terminal.agentParams",
        ),
        (minimal_registration(agentParams={"": "v"}), "params.terminal.agentParams"),
        (minimal_registration(agentParams={"k\0": "v"}), "params.terminal.agentParams"),
        (minimal_registration(agentParams={"k": 1}), "params.terminal.agentParams"),
        (minimal_registration(agentParams={"k": None}), "params.terminal.agentParams"),
        (minimal_registration(agentParams={"k": "v\0"}), "params.terminal.agentParams"),
        (minimal_registration(worktreePath=""), "params.terminal.worktreePath"),
        (minimal_registration(worktreePath="/tmp/\0wt"), "params.terminal.worktreePath"),
        (minimal_registration(worktreePath=1), "params.terminal.worktreePath"),
        (minimal_registration(worktreeBranch=""), "params.terminal.worktreeBranch"),
        (minimal_registration(worktreeBranch="feat/\0b"), "params.terminal.worktreeBranch"),
        (minimal_registration(worktreeBranch=1), "params.terminal.worktreeBranch"),
        (minimal_registration(id="bad id", sdkSessionId="extra"), "params.terminal"),
        (minimal_registration(id="bad id", title=""), "params.terminal.id"),
        (minimal_registration(model="", permissionMode=""), "params.terminal.model"),
        (minimal_registration(title="\ud800"), "params.terminal"),
    ],
)
def test_registration_error_fields_and_order(terminal, expected_field):
    assert bac.registration_error(terminal) == expected_field


def test_registration_error_size_bounds_and_utf8():
    base = minimal_registration(title="")
    overhead = len(json.dumps(base, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    exact = minimal_registration(title="x" * (bac.REGISTRATION_BYTES - overhead))
    one_over = minimal_registration(title="x" * (bac.REGISTRATION_BYTES - overhead + 1))
    assert bac.registration_error(exact) is None
    assert bac.registration_error(one_over) == "params.terminal"

    custom_max = overhead + 30
    assert bac.registration_error(minimal_registration(title="x" * 30), max_bytes=custom_max) is None
    assert (
        bac.registration_error(minimal_registration(title="x" * 31), max_bytes=custom_max)
        == "params.terminal"
    )

    # 10 CJK chars = 30 UTF-8 bytes (fits in custom_max), but 60 bytes if escaped as \uXXXX.
    cjk = minimal_registration(title="設" * 10)
    assert bac.registration_error(cjk, max_bytes=custom_max) is None


@pytest.mark.parametrize(
    ("stored", "candidate", "expected"),
    [
        (minimal_registration(), minimal_registration(), True),
        (full_registration(), full_registration(), True),
        (
            minimal_registration(sdkSessionId="sdk-1", pinned=True),
            minimal_registration(),
            True,
        ),
        (
            {k: v for k, v in minimal_registration().items() if k != "type"},
            minimal_registration(),
            True,
        ),
        (minimal_registration(type="pty"), minimal_registration(), False),
        (minimal_registration(), minimal_registration(model=None), True),
        (minimal_registration(model=None), minimal_registration(), True),
        (minimal_registration(model="m1"), minimal_registration(), False),
        (minimal_registration(), minimal_registration(model="m1"), False),
        (minimal_registration(), minimal_registration(permissionMode=None), True),
        (minimal_registration(permissionMode=None), minimal_registration(), True),
        (minimal_registration(permissionMode="p1"), minimal_registration(), False),
        (minimal_registration(), minimal_registration(worktreePath=None), True),
        (minimal_registration(worktreePath=None), minimal_registration(), True),
        (minimal_registration(worktreePath="/wt"), minimal_registration(), False),
        (minimal_registration(), minimal_registration(worktreeBranch=None), False),
        (minimal_registration(worktreeBranch=None), minimal_registration(), False),
        (
            minimal_registration(worktreeBranch=None),
            minimal_registration(worktreeBranch=None),
            True,
        ),
        (
            minimal_registration(worktreeBranch="feat/a"),
            minimal_registration(worktreeBranch="feat/a"),
            True,
        ),
        (
            minimal_registration(worktreeBranch="feat/a"),
            minimal_registration(worktreeBranch="feat/b"),
            False,
        ),
        (minimal_registration(), minimal_registration(agentParams=None), True),
        (minimal_registration(), minimal_registration(agentParams={}), True),
        (minimal_registration(agentParams=None), minimal_registration(), True),
        (minimal_registration(agentParams=None), minimal_registration(agentParams=None), True),
        (minimal_registration(agentParams=None), minimal_registration(agentParams={}), True),
        (minimal_registration(agentParams={}), minimal_registration(), True),
        (minimal_registration(agentParams={}), minimal_registration(agentParams=None), True),
        (minimal_registration(agentParams={}), minimal_registration(agentParams={}), True),
        (
            minimal_registration(agentParams={"sandboxMode": "x"}),
            minimal_registration(),
            True,
        ),
        (
            minimal_registration(agentParams={"sandboxMode": "a"}),
            minimal_registration(agentParams={"sandboxMode": "b"}),
            True,
        ),
        (
            minimal_registration(agentParams={"approvalPolicy": "a"}),
            minimal_registration(agentParams={"approvalPolicy": "b"}),
            True,
        ),
        (
            minimal_registration(agentParams={"other": "a"}),
            minimal_registration(agentParams={"other": "b"}),
            False,
        ),
        (
            minimal_registration(agentParams={"other": "a", "effortLevel": "high"}),
            minimal_registration(agentParams={"other": "a"}),
            True,
        ),
        (minimal_registration(agentParams=[]), minimal_registration(), False),
        (minimal_registration(id="sess-2"), minimal_registration(), False),
        (minimal_registration(workspaceId="ws-2"), minimal_registration(), False),
        (minimal_registration(title="Other"), minimal_registration(), False),
        (minimal_registration(cwd="/other"), minimal_registration(), False),
        (minimal_registration(agentPreset="codex-agent"), minimal_registration(), False),
        (minimal_registration(title=1), minimal_registration(title="1"), False),
        (None, minimal_registration(), False),
        ([], minimal_registration(), False),
        (minimal_registration(), minimal_registration(sdkSessionId="extra"), False),
        (
            minimal_registration(title="x" * (bac.REGISTRATION_BYTES + 100)),
            minimal_registration(title="x" * (bac.REGISTRATION_BYTES + 100)),
            True,
        ),
    ],
)
def test_registration_effectively_equal(stored, candidate, expected):
    assert bac.registration_effectively_equal(stored, candidate) is expected


def test_parse_workspace_read_valid_and_deep_copy():
    raw = valid_workspace_read(extraField="ignored", windowId="win-1", source="live")
    parsed = bac.parse_workspace_read(raw, host_id=HOST_ID, profile_id="default")
    assert set(parsed.keys()) == {
        "hostId",
        "profileId",
        "source",
        "windowId",
        "revision",
        "document",
        "appendTargetEligible",
    }
    assert "extraField" not in parsed
    raw["document"]["workspaces"].append({"id": "ws-mutated"})
    assert parsed["document"]["workspaces"] == [{"id": "ws-main"}]


@pytest.mark.parametrize(
    "missing_key",
    [
        "hostId",
        "profileId",
        "source",
        "windowId",
        "revision",
        "document",
        "appendTargetEligible",
    ],
)
def test_parse_workspace_read_missing_key(missing_key):
    payload = valid_workspace_read()
    del payload[missing_key]
    with pytest.raises(ValueError, match="invalid_reply"):
        bac.parse_workspace_read(payload, host_id=HOST_ID, profile_id="default")


@pytest.mark.parametrize(
    "bad_payload",
    [
        None,
        [],
        valid_workspace_read(hostId="h_" + "f" * 32),
        valid_workspace_read(hostId=123),
        valid_workspace_read(profileId="other"),
        valid_workspace_read(profileId=None),
        valid_workspace_read(source="cached"),
        valid_workspace_read(source=None),
        valid_workspace_read(windowId=123),
        valid_workspace_read(windowId=True),
        valid_workspace_read(revision="bad"),
        valid_workspace_read(document=None),
        valid_workspace_read(document=[]),
        valid_workspace_read(appendTargetEligible=1),
        valid_workspace_read(appendTargetEligible=0),
        valid_workspace_read(appendTargetEligible=None),
    ],
)
def test_parse_workspace_read_violations(bad_payload):
    with pytest.raises(ValueError, match="invalid_reply"):
        bac.parse_workspace_read(bad_payload, host_id=HOST_ID, profile_id="default")


def test_parse_session_locate_valid_counts_and_drops_unknown():
    zero = valid_session_locate(disposition="unregistered", locations=[], extra="dropped")
    parsed_zero = bac.parse_session_locate(zero, host_id=HOST_ID, session_id="sess-claude-0001")
    assert set(parsed_zero.keys()) == {
        "hostId",
        "locationRevision",
        "disposition",
        "consistency",
        "atomic",
        "locations",
    }
    assert parsed_zero["locations"] == []

    row1 = valid_location_row(profileId="p1", windowId="win-1", source="live", extraRow="drop")
    row2 = valid_location_row(profileId="p2", windowId=None, source="persisted", extraRow="drop")
    one = valid_session_locate(disposition="located", locations=[copy.deepcopy(row1)])
    parsed_one = bac.parse_session_locate(one, host_id=HOST_ID, session_id="sess-claude-0001")
    assert len(parsed_one["locations"]) == 1
    assert set(parsed_one["locations"][0].keys()) == {
        "profileId",
        "workspaceId",
        "terminalId",
        "windowId",
        "source",
        "profileRevision",
    }

    two = valid_session_locate(
        disposition="ambiguous", locations=[copy.deepcopy(row1), copy.deepcopy(row2)]
    )
    parsed_two = bac.parse_session_locate(two, host_id=HOST_ID, session_id="sess-claude-0001")
    assert [r["profileId"] for r in parsed_two["locations"]] == ["p1", "p2"]

    exact_limit = valid_session_locate(
        disposition="ambiguous",
        locations=[valid_location_row(profileId=f"p{i}") for i in range(3)],
    )
    assert (
        len(
            bac.parse_session_locate(
                exact_limit, host_id=HOST_ID, session_id="sess-claude-0001", max_rows=3
            )["locations"]
        )
        == 3
    )


@pytest.mark.parametrize(
    "missing_key",
    ["hostId", "locationRevision", "disposition", "consistency", "atomic", "locations"],
)
def test_parse_session_locate_missing_top_key(missing_key):
    payload = valid_session_locate()
    del payload[missing_key]
    with pytest.raises(ValueError, match="invalid_reply"):
        bac.parse_session_locate(payload, host_id=HOST_ID, session_id="sess-claude-0001")


@pytest.mark.parametrize(
    "missing_row_key",
    ["profileId", "workspaceId", "terminalId", "windowId", "source", "profileRevision"],
)
def test_parse_session_locate_missing_row_key(missing_row_key):
    row = valid_location_row()
    del row[missing_row_key]
    payload = valid_session_locate(locations=[row])
    with pytest.raises(ValueError, match="invalid_reply"):
        bac.parse_session_locate(payload, host_id=HOST_ID, session_id="sess-claude-0001")


@pytest.mark.parametrize(
    ("bad_payload", "max_rows"),
    [
        (None, bac.LOCATION_MATCHES),
        ([], bac.LOCATION_MATCHES),
        (valid_session_locate(hostId="h_" + "f" * 32), bac.LOCATION_MATCHES),
        (valid_session_locate(locationRevision="bad"), bac.LOCATION_MATCHES),
        (valid_session_locate(disposition="unknown"), bac.LOCATION_MATCHES),
        (valid_session_locate(disposition="located", locations=[]), bac.LOCATION_MATCHES),
        (valid_session_locate(disposition="ambiguous", locations=[]), bac.LOCATION_MATCHES),
        (
            valid_session_locate(disposition="unregistered", locations=[valid_location_row()]),
            bac.LOCATION_MATCHES,
        ),
        (
            valid_session_locate(disposition="ambiguous", locations=[valid_location_row()]),
            bac.LOCATION_MATCHES,
        ),
        (
            valid_session_locate(
                disposition="unregistered",
                locations=[valid_location_row(), valid_location_row(profileId="p2")],
            ),
            bac.LOCATION_MATCHES,
        ),
        (
            valid_session_locate(
                disposition="located",
                locations=[valid_location_row(), valid_location_row(profileId="p2")],
            ),
            bac.LOCATION_MATCHES,
        ),
        (valid_session_locate(consistency="atomic"), bac.LOCATION_MATCHES),
        (valid_session_locate(atomic=True), bac.LOCATION_MATCHES),
        (valid_session_locate(atomic=0), bac.LOCATION_MATCHES),
        (valid_session_locate(locations=None), bac.LOCATION_MATCHES),
        (valid_session_locate(locations=[None]), bac.LOCATION_MATCHES),
        (valid_session_locate(locations=[valid_location_row(profileId=1)]), bac.LOCATION_MATCHES),
        (
            valid_session_locate(locations=[valid_location_row(workspaceId=1)]),
            bac.LOCATION_MATCHES,
        ),
        (
            valid_session_locate(locations=[valid_location_row(terminalId="sess-other")]),
            bac.LOCATION_MATCHES,
        ),
        (valid_session_locate(locations=[valid_location_row(windowId=1)]), bac.LOCATION_MATCHES),
        (
            valid_session_locate(locations=[valid_location_row(source="cached")]),
            bac.LOCATION_MATCHES,
        ),
        (
            valid_session_locate(locations=[valid_location_row(profileRevision="bad")]),
            bac.LOCATION_MATCHES,
        ),
        (
            valid_session_locate(
                disposition="ambiguous",
                locations=[valid_location_row(profileId=f"p{i}") for i in range(3)],
            ),
            2,
        ),
    ],
)
def test_parse_session_locate_violations(bad_payload, max_rows):
    with pytest.raises(ValueError, match="invalid_reply"):
        bac.parse_session_locate(
            bad_payload, host_id=HOST_ID, session_id="sess-claude-0001", max_rows=max_rows
        )


def test_parse_append_result_valid():
    raw = valid_append_result(extra="ignored", windowId=None, source="persisted", appended=False)
    parsed = bac.parse_append_result(
        raw, host_id=HOST_ID, profile_id="default", terminal_id="sess-claude-0001"
    )
    assert set(parsed.keys()) == {
        "hostId",
        "profileId",
        "source",
        "windowId",
        "terminalId",
        "appended",
        "revision",
    }
    assert "extra" not in parsed
    assert parsed["appended"] is False


@pytest.mark.parametrize(
    "missing_key",
    ["hostId", "profileId", "source", "windowId", "terminalId", "appended", "revision"],
)
def test_parse_append_result_missing_key(missing_key):
    payload = valid_append_result()
    del payload[missing_key]
    with pytest.raises(ValueError, match="invalid_reply"):
        bac.parse_append_result(
            payload, host_id=HOST_ID, profile_id="default", terminal_id="sess-claude-0001"
        )


@pytest.mark.parametrize(
    "bad_payload",
    [
        None,
        [],
        valid_append_result(hostId="h_" + "f" * 32),
        valid_append_result(profileId="other"),
        valid_append_result(source="cached"),
        valid_append_result(windowId=123),
        valid_append_result(terminalId="sess-other"),
        valid_append_result(appended=1),
        valid_append_result(appended=0),
        valid_append_result(appended=None),
        valid_append_result(revision="bad"),
    ],
)
def test_parse_append_result_violations(bad_payload):
    with pytest.raises(ValueError, match="invalid_reply"):
        bac.parse_append_result(
            bad_payload, host_id=HOST_ID, profile_id="default", terminal_id="sess-claude-0001"
        )


def test_bac_read_channels_and_errors():
    assert channels.BAC_READ_CHANNELS == {
        bac.WORKSPACE_READ_CHANNEL,
        bac.SESSION_LOCATE_CHANNEL,
    }
    assert channels.is_write(bac.WORKSPACE_READ_CHANNEL) is False
    assert channels.is_write(bac.SESSION_LOCATE_CHANNEL) is False
    assert channels.timeout_for(bac.SESSION_LOCATE_CHANNEL) == 30.0
    assert channels.timeout_for(bac.WORKSPACE_READ_CHANNEL) == channels.DEFAULT_TIMEOUT
    for ch in channels.BAC_READ_CHANNELS:
        assert ch in bac.CHANNEL_FEATURES
        for allow_writes in (False, True):
            for allow_orchestrate in (False, True):
                with pytest.raises(ChannelNotAllowed):
                    channels.check_allowed(
                        ch, allow_writes=allow_writes, allow_orchestrate=allow_orchestrate
                    )
    assert all(feature in bac.FEATURES for feature in bac.CHANNEL_FEATURES.values())
    assert issubclass(BacUnavailable, BatError)
    assert isinstance(BacUnavailable("unavailable"), BatError)
