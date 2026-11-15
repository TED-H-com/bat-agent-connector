"""Host terminal append (`bac:workspace-add-terminal`) caller integration in orchestrate and task_bat."""

from __future__ import annotations

import copy
import json

import pytest

from bat_agent_connector import bac, orchestrate, registry, safety
from bat_agent_connector.task_journal import Journal
from tests.conftest import controller_scope
from tests.mockbat import MockBac
from tests.test_bac_append import ADD, READ, calc_revision, expected_document
from tests.test_confinement import MANAGED
from tests.test_confinement_recovery import reviewer_adapter


@pytest.fixture
def append_host(mock):
    mock.bac = MockBac()
    mock.bac.enable_append(mock)
    return mock


@pytest.fixture
def effects(monkeypatch):
    seen = []
    original = safety.Audit.record_after_effect

    def record(self, *, effect_outcome, effect_sent, **fields):
        seen.append({"outcome": effect_outcome, "sent": effect_sent, **fields})
        return original(self, effect_outcome=effect_outcome, effect_sent=effect_sent, **fields)

    monkeypatch.setattr(safety.Audit, "record_after_effect", record)
    return seen


def audit_rows() -> list[dict]:
    try:
        return [json.loads(x) for x in safety.audit_path().read_text().splitlines()]
    except OSError:
        return []


def add_frames(mock) -> list[dict]:
    return [f for f in mock.bac.frames if f.get("channel") == ADD]


def read_frames(mock) -> list[dict]:
    return [f for f in mock.bac.frames if f.get("channel") == READ]


def _tab_row() -> dict:
    rows = [r for r in audit_rows() if r.get("phase") == "append-terminal"]
    assert len(rows) == 1
    return rows[0]


def _tab_effect(effects: list[dict]) -> dict:
    matched = [e for e in effects if e.get("phase") == "append-terminal"]
    assert len(matched) == 1
    return matched[0]


async def test_session_start_appended_by_host(fleet_factory, append_host, effects):
    f = fleet_factory(writes=True, orchestrate=True, tabs=True)
    try:
        r = await orchestrate.session_start(f, "h1", "demo-project", confirm=True)
    finally:
        await f.close()

    rev_after = calc_revision(expected_document(append_host))
    assert r["tab"] == {
        "appended": True,
        "saved": True,
        "route": "bac",
        "source": "persisted",
        "window_id": None,
        "revision": rev_after,
    }
    assert r["note"] is None
    assert "workspace:save" not in append_host.channels()
    assert append_host.channels()[-2:] == [READ, ADD]
    assert len(read_frames(append_host)) == 1
    assert len(add_frames(append_host)) == 1
    assert append_host.ws_doc["terminals"][-1]["id"] == r["session_id"]
    assert registry.get("h1", r["session_id"])["tab_registered"] is True

    row = _tab_row()
    assert row["channel"] == bac.WORKSPACE_ADD_TERMINAL_CHANNEL
    assert row["ok"] is True

    eff = _tab_effect(effects)
    assert eff["sent"] is True
    assert eff["outcome"] == "accepted"
    assert eff["channel"] == bac.WORKSPACE_ADD_TERMINAL_CHANNEL


async def test_session_start_equal_registration_already_there(fleet_factory, append_host, effects):
    def store_equal():
        append_host.ws_doc["terminals"].append(
            copy.deepcopy(append_host.bac.frames[-1]["params"]["terminal"])
        )

    append_host.bac.before_append = store_equal
    f = fleet_factory(writes=True, orchestrate=True, tabs=True)
    try:
        r = await orchestrate.session_start(f, "h1", "demo-project", confirm=True)
    finally:
        await f.close()

    current_rev = calc_revision(expected_document(append_host))
    assert r["tab"] == {
        "appended": False,
        "reason": "already present",
        "identical": True,
        "route": "bac",
        "revision": current_rev,
    }
    assert r["note"] is None
    assert registry.get("h1", r["session_id"])["tab_registered"] is True
    assert [t["id"] for t in append_host.ws_doc["terminals"]].count(r["session_id"]) == 1

    row = _tab_row()
    assert row["channel"] == bac.WORKSPACE_ADD_TERMINAL_CHANNEL
    assert row["ok"] is True

    eff = _tab_effect(effects)
    assert eff["sent"] is True
    assert eff["outcome"] == "accepted"
    assert eff["channel"] == bac.WORKSPACE_ADD_TERMINAL_CHANNEL


async def test_session_start_different_registration_same_id(fleet_factory, append_host, effects):
    def store_different():
        other = copy.deepcopy(append_host.bac.frames[-1]["params"]["terminal"])
        other["title"] = "Someone else"
        append_host.ws_doc["terminals"].append(other)

    append_host.bac.before_append = store_different
    f = fleet_factory(writes=True, orchestrate=True, tabs=True)
    try:
        r = await orchestrate.session_start(f, "h1", "demo-project", confirm=True)
    finally:
        await f.close()

    assert r["tab"] == {
        "appended": False,
        "saved": False,
        "reason": "already present",
        "identical": False,
        "route": "bac",
    }
    assert isinstance(r["note"], str) and r["note"].startswith("no GUI tab registered")
    assert registry.get("h1", r["session_id"])["tab_registered"] is False

    row = _tab_row()
    assert row["channel"] == bac.WORKSPACE_ADD_TERMINAL_CHANNEL
    assert row["ok"] is False

    eff = _tab_effect(effects)
    assert eff["sent"] is True
    assert eff["outcome"] == "error"
    assert eff["channel"] == bac.WORKSPACE_ADD_TERMINAL_CHANNEL


async def test_session_start_certain_refusal_of_append(fleet_factory, append_host, effects):
    append_host.bac.append_error = "WORKSPACE_UNAVAILABLE"
    f = fleet_factory(writes=True, orchestrate=True, tabs=True)
    try:
        r = await orchestrate.session_start(f, "h1", "demo-project", confirm=True)
    finally:
        await f.close()

    assert r["tab"] == {
        "appended": False,
        "saved": False,
        "reason": "host refused the registration",
        "refusal": "WORKSPACE_UNAVAILABLE",
        "route": "bac",
    }
    assert len(add_frames(append_host)) == 1
    assert "workspace:save" not in append_host.channels()
    assert registry.get("h1", r["session_id"])["tab_registered"] is False

    row = _tab_row()
    assert row["channel"] == bac.WORKSPACE_ADD_TERMINAL_CHANNEL
    assert row["ok"] is False

    eff = _tab_effect(effects)
    assert eff["sent"] is True
    assert eff["outcome"] == "error"
    assert eff["channel"] == bac.WORKSPACE_ADD_TERMINAL_CHANNEL


async def test_session_start_refused_by_read_nothing_sent(fleet_factory, append_host, effects):
    append_host.bac.read_error = "WORKSPACE_UNAVAILABLE"
    f = fleet_factory(writes=True, orchestrate=True, tabs=True)
    try:
        r = await orchestrate.session_start(f, "h1", "demo-project", confirm=True)
    finally:
        await f.close()

    assert r["tab"] == {
        "appended": False,
        "saved": False,
        "reason": "host refused the registration",
        "refusal": "WORKSPACE_UNAVAILABLE",
        "route": "bac",
    }
    assert len(read_frames(append_host)) == 1
    assert add_frames(append_host) == []
    assert "workspace:save" not in append_host.channels()
    assert registry.get("h1", r["session_id"])["tab_registered"] is False

    row = _tab_row()
    assert row["channel"] == bac.WORKSPACE_ADD_TERMINAL_CHANNEL
    assert row["ok"] is False

    eff = _tab_effect(effects)
    assert eff["sent"] is False
    assert eff["outcome"] == "error"
    assert eff["channel"] == bac.WORKSPACE_ADD_TERMINAL_CHANNEL


async def test_session_start_unknown_outcome_registration_applied(
    fleet_factory, append_host, effects
):
    append_host.bac.append_fault = "write_failed_applied"
    f = fleet_factory(writes=True, orchestrate=True, tabs=True)
    try:
        r = await orchestrate.session_start(f, "h1", "demo-project", confirm=True)
    finally:
        await f.close()

    assert r["tab"] == {
        "appended": False,
        "error": "h1: bac:workspace-add-terminal: WRITE_FAILED",
    }
    assert append_host.channels()[-1] == ADD
    assert "workspace:save" not in append_host.channels()
    assert len(add_frames(append_host)) == 1
    assert registry.get("h1", r["session_id"])["tab_registered"] is False

    row = _tab_row()
    assert row["channel"] == bac.WORKSPACE_ADD_TERMINAL_CHANNEL
    assert row["ok"] is False

    eff = _tab_effect(effects)
    assert eff["sent"] is True
    assert eff["outcome"] == "unknown"
    assert eff["channel"] == bac.WORKSPACE_ADD_TERMINAL_CHANNEL


async def test_session_start_reply_lost(fleet_factory, append_host, effects):
    append_host.bac.append_fault = "lost_reply"
    f = fleet_factory(writes=True, orchestrate=True, tabs=True)
    try:
        r = await orchestrate.session_start(f, "h1", "demo-project", confirm=True)
    finally:
        await f.close()

    assert r["tab"]["appended"] is False
    assert "error" in r["tab"]
    assert len(add_frames(append_host)) == 1
    assert "workspace:save" not in append_host.channels()
    assert registry.get("h1", r["session_id"])["tab_registered"] is False

    row = _tab_row()
    assert row["channel"] == bac.WORKSPACE_ADD_TERMINAL_CHANNEL
    assert row["ok"] is False

    eff = _tab_effect(effects)
    assert eff["sent"] is True
    assert eff["outcome"] == "unknown"
    assert eff["channel"] == bac.WORKSPACE_ADD_TERMINAL_CHANNEL


async def test_session_start_host_without_append_unchanged(fleet_factory, mock, effects):
    f = fleet_factory(writes=True, orchestrate=True, tabs=True)
    try:
        r = await orchestrate.session_start(f, "h1", "demo-project", confirm=True)
    finally:
        await f.close()

    assert "route" not in r["tab"]
    assert r["tab"]["appended"] is True
    assert r["note"] is None
    assert mock.channels().count("workspace:save") == 1

    row = _tab_row()
    assert row["channel"] == "workspace:save"
    assert row["ok"] is True

    eff = _tab_effect(effects)
    assert eff["sent"] is True
    assert eff["outcome"] == "accepted"
    assert eff["channel"] == "workspace:save"


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("appended", True),
        ("equal-already-there", True),
        ("different-already-there", False),
    ],
    ids=["appended", "equal-already-there", "different-already-there"],
)
async def test_task_start_reviewer_host_append(
    fleet_factory, append_host, tmp_path, monkeypatch, case, expected
):
    if case == "equal-already-there":
        append_host.bac.before_append = lambda: append_host.ws_doc["terminals"].append(
            copy.deepcopy(append_host.bac.frames[-1]["params"]["terminal"])
        )
    elif case == "different-already-there":
        def store_different():
            other = copy.deepcopy(append_host.bac.frames[-1]["params"]["terminal"])
            other["title"] = "Someone else"
            append_host.ws_doc["terminals"].append(other)

        append_host.bac.before_append = store_different

    fleet = fleet_factory(writes=True, orchestrate=True, tabs=True, **MANAGED)
    journal = Journal(tmp_path / "tasks.db")
    with controller_scope(journal):
        adapter, task = reviewer_adapter(fleet, append_host, journal, monkeypatch)
        adapter.register_tabs = True
        try:
            sid = await adapter.start(
                task, role="reviewer", agent="codex", session_id="review-tab"
            )
            assert sid == "review-tab"
            row = registry.get("h1", "review-tab")
            assert row["status"] == "active"
            assert len(add_frames(append_host)) == 1
            assert "workspace:save" not in append_host.channels()
            assert row["tab_registered"] is expected
        finally:
            await fleet.close()
            journal.close()


@pytest.mark.parametrize(
    ("tab", "expected"),
    [
        (
            {
                "appended": True,
                "saved": True,
                "route": "bac",
                "source": "persisted",
                "window_id": None,
                "revision": "sha256:" + "0" * 64,
            },
            True,
        ),
        (
            {
                "appended": False,
                "reason": "already present",
                "identical": True,
                "route": "bac",
                "revision": "sha256:" + "0" * 64,
            },
            True,
        ),
        (
            {
                "appended": False,
                "saved": False,
                "reason": "already present",
                "identical": False,
                "route": "bac",
            },
            False,
        ),
        (
            {
                "appended": False,
                "saved": False,
                "reason": "host refused the registration",
                "refusal": "WORKSPACE_UNAVAILABLE",
                "route": "bac",
            },
            False,
        ),
        (
            {
                "appended": False,
                "saved": False,
                "reason": "workspace kept changing; gave up to avoid clobbering",
                "route": "bac",
            },
            False,
        ),
        (
            {
                "appended": True,
                "saved": True,
                "verified_previous_terminals_kept": True,
                "lost_terminals": [],
                "lost_workspaces": [],
            },
            True,
        ),
        ({"appended": False, "reason": "already present"}, False),
        ({"appended": False, "error": "x"}, False),
        (None, False),
        ({}, False),
        ({"appended": 1}, False),
        ({"identical": "yes"}, False),
    ],
    ids=[
        "bac-appended",
        "bac-identical",
        "bac-different",
        "bac-refused",
        "bac-kept-changing",
        "legacy-appended",
        "legacy-already-present",
        "error-dict",
        "none",
        "empty",
        "appended-non-bool",
        "identical-non-bool",
    ],
)
def test_tab_is_registered(tab, expected):
    result = orchestrate.tab_is_registered(tab)
    assert isinstance(result, bool)
    assert result is expected
