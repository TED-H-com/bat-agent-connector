"""Policy and outcome classifications for bac:workspace-add-terminal."""

from __future__ import annotations

import asyncio
import pathlib
import re

import pytest

from bat_agent_connector import bac, channels, operations, registry, resource_policy, safety
from bat_agent_connector.config import SafetyConfig
from bat_agent_connector.errors import (
    BacOutcomeUnknown,
    BacReplyError,
    BacUnavailable,
    BatError,
    ChannelNotAllowed,
    ConnectionLost,
    InvokeError,
    InvokeTimeout,
    ResourceReadOnly,
    WriteRefused,
)
from bat_agent_connector.resource_policy import BY_ACTION, MUTATIONS, WriteGrant, check_grant
from bat_agent_connector.safety import Audit
from tests.conftest import adopt

ADD = "bac:workspace-add-terminal"
TAB_RULE = (
    "append-only tab for a session the connector just created; revision-checked where the host "
    "offers it, otherwise other tabs are verified unchanged"
)


def test_bac_outcome_unknown_error_hierarchy():
    assert issubclass(BacOutcomeUnknown, BatError)
    for other in (InvokeError, WriteRefused, ConnectionLost, InvokeTimeout):
        assert not issubclass(BacOutcomeUnknown, other)


def test_bac_outcome_unknown_ambiguity():
    assert isinstance(BacOutcomeUnknown("x"), operations.AMBIGUOUS)
    for cls in (InvokeTimeout, ConnectionLost, asyncio.TimeoutError, operations.AmbiguousOutcome):
        assert cls in operations.AMBIGUOUS
    for err in (
        BacUnavailable("x"),
        BacReplyError("WORKSPACE_NOT_FOUND", "m", False, {}),
        InvokeError("x"),
        ChannelNotAllowed("x"),
    ):
        assert not isinstance(err, operations.AMBIGUOUS)


def test_audit_error_outcome_for_bac_outcome_unknown():
    for err in (BacOutcomeUnknown("x"), ConnectionLost("x"), InvokeTimeout("x")):
        assert safety.audit_error_outcome(err) == "unknown"
    for err in (
        BacUnavailable("x"),
        BacReplyError("TERMINAL_EXISTS", "m", False, {}),
        InvokeError("x"),
    ):
        assert safety.audit_error_outcome(err) == "error"


def test_append_not_applied_codes():
    expected = {
        "INVALID_REQUEST",
        "PAYLOAD_TOO_LARGE",
        "CONTEXT_NOT_SUPPORTED",
        "NOT_OPTED_IN",
        "UNSUPPORTED_FEATURE",
        "UNKNOWN_CHANNEL",
        "PROFILE_NOT_FOUND",
        "PROFILE_NOT_LOCAL",
        "WORKSPACE_UNAVAILABLE",
        "WORKSPACE_TOO_LARGE",
        "WORKSPACE_NOT_FOUND",
        "TERMINAL_EXISTS",
        "REVISION_MISMATCH",
    }
    assert isinstance(bac.APPEND_NOT_APPLIED, frozenset)
    assert bac.APPEND_NOT_APPLIED == expected
    for excluded in (
        "WRITE_FAILED",
        "INTERNAL_ERROR",
        "RESPONSE_TOO_LARGE",
        "HOST_MISMATCH",
        "STORAGE_UNAVAILABLE",
        "UNSUPPORTED_VERSION",
    ):
        assert excluded not in bac.APPEND_NOT_APPLIED


def test_channel_tables_for_workspace_add_terminal():
    assert channels.GUARDED_CHANNELS == {"workspace:save", ADD}
    assert channels.is_write(ADD) is True
    for ch in ("bac:workspace-read", "bac:session-locate", "bac:hello"):
        assert channels.is_write(ch) is False
    assert channels.timeout_for(ADD) == 30.0
    for table in (
        channels.BAC_READ_CHANNELS,
        channels.HANDSHAKE_CHANNELS,
        channels.READ_CHANNELS,
        channels.WRITE_CHANNELS,
        channels.ORCHESTRATE_CHANNELS,
    ):
        assert ADD not in table
    for allow_writes in (False, True):
        for allow_orchestrate in (False, True):
            with pytest.raises(ChannelNotAllowed) as exc_info:
                channels.check_allowed(
                    ADD, allow_writes=allow_writes, allow_orchestrate=allow_orchestrate
                )
            assert str(exc_info.value) == "BAC channels are not available through invoke"


def test_mutation_table_for_workspace_register_tab():
    mut = BY_ACTION["workspace.register_tab"]
    assert mut.channels == {"workspace:save", ADD}
    assert [m.action for m in MUTATIONS if ADD in m.channels] == ["workspace.register_tab"]
    assert mut.rule == TAB_RULE
    assert resource_policy.SESSIONLESS_CHANNELS == {"workspace:save"}
    assert ADD in resource_policy.BAT_WRITE_CHANNELS


def test_grant_binding_for_workspace_add_terminal(controller_context):
    adopt("s-1")
    row = registry.get("h1", "s-1")
    ownership = {key: row[key] for key in ("controller_authority", "controller_incarnation")}
    tab = WriteGrant(
        "h1", "workspace.register_tab", "s-1", BY_ACTION["workspace.register_tab"].channels, **ownership
    )
    check_grant(tab, "h1", ADD, {"sessionId": "s-1"})
    for params in ({"sessionId": "s-2"}, {}, None, {"profileId": "default"}):
        with pytest.raises(ResourceReadOnly, match="GRANT_MISMATCH"):
            check_grant(tab, "h1", ADD, params)
    with pytest.raises(ResourceReadOnly, match="GRANT_MISMATCH"):
        check_grant(tab, "h2", ADD, {"sessionId": "s-1"})
    check_grant(tab, "h1", "workspace:save", {"profileId": "default"})

    send_grant = WriteGrant("h1", "session.send", "s-1", BY_ACTION["session.send"].channels, **ownership)
    with pytest.raises(ResourceReadOnly, match="GRANT_MISMATCH"):
        check_grant(send_grant, "h1", ADD, {"sessionId": "s-1"})

    legacy_tab = WriteGrant(
        "h1", "workspace.register_tab", "s-1", frozenset({"workspace:save"}), **ownership
    )
    with pytest.raises(ResourceReadOnly, match="GRANT_MISMATCH"):
        check_grant(legacy_tab, "h1", ADD, {"sessionId": "s-1"})


def test_authorize_register_tab_mints_grant_covering_add(controller_context):
    adopt("s-1")
    grant = resource_policy.authorize_register_tab("h1", "s-1")
    assert grant.channels == BY_ACTION["workspace.register_tab"].channels
    assert grant.session_id == "s-1"


async def test_session_policy_read_view_for_workspace_register_tab(fleet_factory):
    f = fleet_factory()
    try:
        view = await resource_policy.session_policy(f, "h1")
        entry = next(m for m in view["mutations"] if m["action"] == "workspace.register_tab")
        assert entry["channels"] == [ADD, "workspace:save"]
        assert entry["rule"] == TAB_RULE
    finally:
        await f.close()


def test_rate_limit_treats_workspace_add_terminal_as_start_channel(tmp_path):
    audit = Audit(
        SafetyConfig(write_min_interval_s=60, max_writes_per_hour=50),
        tmp_path / "audit.jsonl",
    )
    audit.record(host="h1", session_id="task-session", channel="claude:start-session", phase="attempt")
    audit.record(host="h1", session_id="task-session", channel=ADD, phase="attempt")

    audit.check_rate("h1", "task-session", initial_task_send=True)
    with pytest.raises(WriteRefused, match="write_min_interval_s"):
        audit.check_rate("h1", "task-session")

    audit.record(host="h1", session_id="task-session", channel="claude:send-message", phase="attempt")
    with pytest.raises(WriteRefused, match="write_min_interval_s"):
        audit.check_rate("h1", "task-session", initial_task_send=True)

    capped = Audit(
        SafetyConfig(write_min_interval_s=60, max_writes_per_hour=2),
        tmp_path / "audit-capped.jsonl",
    )
    capped.record(host="h1", session_id="task-session", channel="claude:start-session", phase="attempt")
    capped.record(host="h1", session_id="task-session", channel=ADD, phase="attempt")
    with pytest.raises(WriteRefused, match="max_writes_per_hour"):
        capped.check_rate("h1", "task-session", initial_task_send=True)


def test_design_document_repeats_the_lists_the_client_decides_from():
    """docs/design/host-contracts.md repeats five values of the code; they must change together."""
    path = pathlib.Path(__file__).resolve().parents[1] / "docs/design/host-contracts.md"
    section = path.read_text(encoding="utf-8").split("\n## Tab registration through the host\n", 1)[1]
    section = section.split("\n## ", 1)[0]
    flat = " ".join(section.split())

    def names(text: str) -> set[str]:
        return set(re.findall(r"`([A-Za-z_]+)`", text))

    refused = [line for line in section.splitlines() if "| Refused, with that code |" in line]
    assert len(refused) == 1
    assert "| The append is refused with `REVISION_MISMATCH` |" in section
    assert "| The append is refused with `TERMINAL_EXISTS` |" in section
    assert names(refused[0]) | {"REVISION_MISMATCH", "TERMINAL_EXISTS"} == bac.APPEND_NOT_APPLIED

    keys = flat.split("a registration with a key outside ", 1)[1].split(", for example", 1)[0]
    assert names(keys) == set(bac.REGISTRATION_KEYS)

    runtime = flat.split("keys the runtime maintains (", 1)[1].split(")", 1)[0]
    assert names(runtime) == bac.RUNTIME_AGENT_PARAMS

    assert f"({bac.REGISTRATION_BYTES} when the hello does not state one)" in flat
    assert f"no reply within {channels.timeout_for(bac.WORKSPACE_READ_CHANNEL):g} s or a lost connection (a busy" in flat
    assert f"no reply within {channels.timeout_for(ADD):g} s or a lost connection |" in flat
