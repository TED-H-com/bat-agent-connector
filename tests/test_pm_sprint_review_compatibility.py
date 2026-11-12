"""Capture on detached ce3df78a with this test's pinned setup and BATC_CAPTURE_SPRINT_REVIEW_LEGACY=1."""

import itertools
import json
import os
import secrets
import subprocess
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from bat_agent_connector import pm_discussions, pm_governance, pm_native_runtime, pm_plans, pm_workspace
from tests import test_pm_meetings as meetings
from tests.test_pm_discussions import OWNER, settle

sprint_ops = meetings.sprint_ops
ops = meetings.ops
FIXTURE = Path(__file__).parent / "fixtures" / "sprint-review-legacy.json"


@pytest.fixture(autouse=True)
def fixed_native_installation(tmp_path, monkeypatch):
    # Match the availability golden's module-owned isolation before ops setup.
    # Fake readable auth is an installation hint, never real provider evidence.
    native = tmp_path / "native"
    native.mkdir()
    (native / "auth.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(pm_native_runtime, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(pm_native_runtime, "shutil", SimpleNamespace(which=lambda _: "/fixture/tool"))
    monkeypatch.setattr(pm_native_runtime, "_host_paths", lambda: (tmp_path, native))
    monkeypatch.setattr(pm_native_runtime, "SYSTEM_CONFIG_ROOT", tmp_path / "absent-system-config")

    def forbidden(*args, **kwargs):
        raise AssertionError("Legacy reads must not launch a native runtime or discover its version")

    monkeypatch.setattr(pm_native_runtime, "_spawn", forbidden)
    monkeypatch.setattr(pm_native_runtime, "_runtime_version", forbidden)


async def test_legacy_envelopes_hashes_events_and_response_bytes(ops, monkeypatch):
    counter = itertools.count(1000)
    monkeypatch.setattr(uuid, "uuid4", lambda: uuid.UUID(int=next(counter)))
    monkeypatch.setattr(time, "time", lambda: 1800000000.0)
    monkeypatch.setattr(pm_workspace, "server_id", lambda db: "00000000-0000-0000-0000-000000000001")
    tokens = itertools.count(1)
    monkeypatch.setattr(secrets, "token_hex", lambda n: f"{next(tokens):0{n * 2}x}")
    pm_governance.install(ops)
    data, _, snap, target = await meetings.meeting(ops, kind="review")
    captured = []

    async def save(body, reader):
        cursor = ops.journal.api_head()
        operation = await settle(ops, ops.create(OWNER, **body)[0])
        events = [
            dict(row)
            for row in ops.db.execute("SELECT * FROM api_events WHERE seq>? ORDER BY seq", (cursor,))
        ]
        captured.append(
            {
                "request": body,
                "operation": operation,
                "request_hash": ops.db.execute(
                    "SELECT request_hash FROM operations WHERE operation_id=?", (operation["operation_id"],)
                ).fetchone()[0],
                "result": operation["result"],
                "events": events,
                "read": reader(operation["result"]),
            }
        )
        return operation["result"]

    fields = dict(
        title="Keep direction",
        description="Evidence remains incomplete",
        owner_actor=OWNER.actor,
        source_refs=[{"kind": "requirement", "id": "membership.login"}],
        rationale="Preserve the original direction",
    )
    gov = await save(
        dict(
            action="pm.governance.record",
            target={"workspace_id": data["workspace_id"]},
            params=dict(
                kind="change", root_cause_key="legacy-direction", snapshot_id=snap["snapshot_id"], **fields
            ),
            preconditions={"expected_plan_version": pm_plans.head(ops.db, data["workspace_id"])["version"]},
            idempotency_key="legacy.record",
        ),
        lambda r: pm_governance.get_record(ops, OWNER, r["workspace_id"], r["record_id"]),
    )
    for action, params in [
        ("revise", fields),
        ("decide", {"disposition": "adopted_direction", "rationale": "Keep direction explicitly"}),
    ]:
        gov = await save(
            dict(
                action="pm.governance." + action,
                target={k: gov[k] for k in ("workspace_id", "record_id")},
                params=params,
                preconditions={"expected_version": gov["version"]},
                idempotency_key="legacy." + action,
            ),
            lambda r: pm_governance.get_record(ops, OWNER, r["workspace_id"], r["record_id"]),
        )
    await meetings.note(ops, target)
    draft = await save(
        meetings.request(ops, target, "draft", **meetings.draft_params("review")),
        lambda r: meetings.read(ops, target),
    )
    await save(
        meetings.request(
            ops,
            target,
            "complete",
            draft_revision_id=draft["draft_revision_id"],
            discussion_summary="Review recorded",
            decision_ids=["retain_direction"],
        ),
        lambda r: meetings.read(ops, target),
    )
    ws = data["workspace_id"]
    created = await settle(
        ops,
        ops.create(
            OWNER,
            action="pm.thread.create",
            target={"workspace_id": ws},
            params={"title": "Next idea"},
            preconditions={"expected_version": pm_workspace.get_workspace(ops.db, OWNER, ws)["version"]},
            idempotency_key="legacy.idea",
        )[0],
    )
    tid = created["result"]["thread_id"]

    def thread(_):
        return pm_discussions.get_thread(ops, OWNER, ws, tid)

    inp = await save(
        dict(
            action="pm.input.record",
            target={"workspace_id": ws, "thread_id": tid},
            params={
                "slot": "scope",
                "value": "Explore later",
                "status": "unresolved",
                "source_ref": {"kind": "statement", "ref": "Review discussion"},
            },
            preconditions={"expected_version": 1},
            idempotency_key="legacy.input",
        ),
        thread,
    )
    await save(
        dict(
            action="pm.thread.plan",
            target={"workspace_id": ws, "thread_id": tid},
            params={"selected_input_ids": [inp["input_id"]], "rationale": "Explicit Planning only"},
            preconditions={"expected_version": 2},
            idempotency_key="legacy.plan",
        ),
        thread,
    )
    # Server identity is created by fixture setup before the deterministic clock/UUIDs.
    server = pm_workspace.server_id(ops.db)
    # Match ApiV1._send_json serialization, including insertion order and spaces.
    encoded = json.dumps(captured, ensure_ascii=False, default=str).replace(server, "fixed-server") + "\n"
    if os.environ.get("BATC_CAPTURE_SPRINT_REVIEW_LEGACY") == "1":
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=FIXTURE.parents[2], text=True
        ).strip()
        assert head == "ce3df78adaa3ca96b28ab84afe3c2d99880320b9", "Capture only on the legacy baseline"
        FIXTURE.write_text(encoded, encoding="utf-8")
    assert encoded == FIXTURE.read_text(encoding="utf-8")
    assert len(captured) == 7
