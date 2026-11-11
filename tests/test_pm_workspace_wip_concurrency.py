"""Two disk connections commit policy CAS; advisory admission is not serialized."""
from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

from bat_agent_connector import pm_workspace_wip as wip
from bat_agent_connector.operations import OpContext, OperationError, OperationService
from bat_agent_connector.task_journal import Journal
from tests.test_pm_discussions import OWNER
from tests.test_pm_evidence import ops as ops
from tests.test_pm_evidence import prepared
from tests.test_pm_workspace_wip import install, request


async def test_two_disk_connections_race_policy_cas_and_replay_original_receipt(ops):
    target = await prepared(ops)
    install(ops)
    parents = [ops.create(OWNER, **request(target["workspace_id"]))[0] for _ in range(2)]
    barrier = threading.Barrier(2)
    def commit(oid):
        journal = Journal(ops.journal.path)
        service = OperationService(journal)
        wip.install(service)
        try:
            parent = service.get(oid, steps=False)
            barrier.wait(timeout=15)
            try:
                return asyncio.run(wip._run(OpContext(service, parent)))
            except OperationError as exc:
                return exc.code
        finally:
            journal.close()
    # No event-loop operation workers are started by these synchronous admissions.
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(commit, [op["operation_id"] for op in parents]))
    assert outcomes.count("PM_WIP_POLICY_CHANGED") == 1, outcomes
    winner = next(value for value in outcomes if isinstance(value, dict))
    rows = ops.db.execute("SELECT * FROM pm_workspace_wip_policies").fetchall()
    assert len(rows) == 1 and rows[0]["revision"] == 1
    original = next(op for op in parents if op["operation_id"] == rows[0]["operation_id"])
    assert await wip._run(OpContext(ops, original)) == winner
    assert ops.db.execute("SELECT COUNT(*) FROM pm_workspace_wip_policies").fetchone()[0] == 1
