"""Stop midway through actual disk Git adoption; never rewrite human checkouts."""
from __future__ import annotations

import pytest

from bat_agent_connector import pm_lifecycle as life
from bat_agent_connector import pm_plans, pm_runs
from tests.test_checkpoints import git, snapshot
from tests.test_pm_lifecycle import decide
from tests.test_pm_plan_operations import OWNER, adopt, adoption, interrupt_host, lose, receipt, run
from tests.test_pm_plan_operations import ready as plan_ready
from tests.test_pm_worktree import pm as pm_fixture
from tests.test_pm_worktree import resume
from tests.test_repository_sync import world as repository_world

world = repository_world
pm = pm_fixture
base_ready = plan_ready


@pytest.fixture
async def ready(base_ready):
    life.install(base_ready['d'].ops)
    return base_ready


@pytest.mark.parametrize('lost_ack', [False, True])
async def test_post_git_stop_preserves_verified_fact_but_does_not_adopt_or_resume_old_epoch(ready, monkeypatch, lost_ack):
    w, ops = ready, ready['d'].ops
    original_human = snapshot(w['human'])
    host_run, armed = w['runner'].run, [True]
    async def pause_after_commit(host, script, timeout_s=None):
        result = await host_run(host, script, timeout_s)
        if armed[0] and w['runner'].phases[-1] == 'pm-revision':
            armed[0] = False
            await decide(ops, w['workspace_id'], who=OWNER)
            if lost_ack:
                lose(None)
        return result
    monkeypatch.setattr(w['runner'], 'run', pause_after_commit)
    first = await adopt(w)
    if lost_ack:
        assert first['status'] == 'uncertain', first
        first = await resume(w, first)
    assert first['error_code'] == 'PM_LIFECYCLE_STOPPED', first
    proof = receipt(w, first)
    assert proof and git(w['plan']['worktree_path'], 'rev-parse', 'HEAD') == proof['commit_sha']
    assert not pm_plans.head(ops.db, w['workspace_id'])['accepted_revision_id']
    assert run(w, first)['state'] == 'completed_service'
    assert w['runner'].phases.count('pm-revision') == 1
    await decide(ops, w['workspace_id'], 'reopen', who=OWNER, params={'stage': 'planning'})
    # A new explicit decision can adopt the existing exact original Git fact.
    fresh = await adopt(w)
    assert fresh['status'] == 'succeeded', fresh
    assert fresh['result']['commit_sha'] == proof['commit_sha']
    assert fresh['result']['commit_operation_id'] == first['operation_id']
    assert w['runner'].phases.count('pm-revision') == 1 and snapshot(w['human']) == original_human


async def test_unknown_partial_git_continues_only_original_transaction_then_stops_new_adoption(ready, monkeypatch):
    w, ops = ready, ready['d'].ops
    interrupt_host(w, monkeypatch, point='branch')
    op = await adopt(w)
    assert op['status'] == 'uncertain', op
    original = run(w, op)
    stopped = await decide(ops, w['workspace_id'], 'close', who=OWNER)
    assert stopped['result']['status'] == 'closing'
    assert pm_runs.require_writer(w['d'].journal, original['run_id'], original['generation'])
    recovered = await resume(w, op)
    assert recovered['error_code'] == 'PM_LIFECYCLE_STOPPED', recovered
    assert receipt(w, recovered) and run(w, recovered)['state'] == 'completed_service'
    assert not pm_plans.head(ops.db, w['workspace_id'])['accepted_revision_id']
    assert life.get_lifecycle(ops, OWNER, w['workspace_id'])['lifecycle']['status'] == 'closed'


async def test_unsent_queued_git_adoption_after_stop_never_allocates_writer_or_changes_head(ready):
    w, ops = ready, ready['d'].ops
    before = git(w['plan']['worktree_path'], 'rev-parse', 'HEAD')
    old = adoption(w)
    await decide(ops, w['workspace_id'], who=OWNER)
    await ops._execute(old['operation_id'])
    stopped = ops.get(old['operation_id'])
    assert stopped['error_code'] == 'PM_LIFECYCLE_STOPPED'
    assert run(w, stopped) is None and receipt(w, stopped) is None
    assert git(w['plan']['worktree_path'], 'rev-parse', 'HEAD') == before
