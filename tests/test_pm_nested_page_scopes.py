"""Real collection readers must not replenish an enclosing source traversal."""
from contextlib import ExitStack, contextmanager, nullcontext
from dataclasses import replace

import pytest

from bat_agent_connector import pm_access, pm_read_cache, pm_runs
from bat_agent_connector import pm_roadmap_report_materialization as reports
from bat_agent_connector import pm_source_reads as reads
from tests.test_pm_plan_operations import OWNER
from tests.test_pm_roadmap_report_materialization import fixed as fixed_fixture
from tests.test_pm_roadmap_report_materialization import materialize
from tests.test_pm_roadmap_report_materialization import pm as pm_fixture
from tests.test_pm_roadmap_report_materialization import ready as ready_fixture
from tests.test_pm_roadmap_report_materialization import spec_fixed as spec_fixed_fixture
from tests.test_pm_roadmap_report_materialization import world as world_fixture

fixed, pm, ready, spec_fixed, world = fixed_fixture, pm_fixture, ready_fixture, spec_fixed_fixture, world_fixture


def pages(w, principal, op):
    ops, wid = w['d'].ops, w['workspace_id']
    event = ops.db.execute("SELECT seq FROM api_events WHERE resource_type='operation' AND resource_id=? ORDER BY seq DESC LIMIT 1",
                           (op['operation_id'],)).fetchone()
    return {
        'operations': ops.list(principal=principal, action=reports.ACTION, limit=2),
        'events': pm_access.filter_events(ops.db, principal, ops.journal.api_events(event[0] - 1, 1)),
        'runs': pm_runs.list_runs(ops, principal, wid, limit=100),
        'receipts': reports.list_materializations(ops, principal, wid, limit=2),
    }


def selected_counts(result, op):
    oid = op['operation_id']
    return [sum(row.get('operation_id') == oid for row in result['operations']['operations']),
            sum(row['resource_id'] == oid for row in result['events']['events']),
            sum(row.get('operation_id') == oid for row in result['runs']['runs']),
            sum(row.get('operation_id') == oid for row in result['receipts']['materializations'])]


@pytest.mark.parametrize('switch', ['same', 'actor', 'credential', 'a_b_a'])
@pytest.mark.parametrize('exhausted', [False, True])
@pytest.mark.parametrize('guarded', [False, True])
async def test_real_pages_preserve_live_scope_chain(fixed, monkeypatch, switch, exhausted, guarded):
    w, db = fixed, fixed['d'].ops.db
    op = await materialize(w)
    principal = replace(OWNER, credential_id='nested-outer')
    other = replace(principal, credential_id='nested-other')
    selected = (replace(principal, actor='nested-admin', admin=True) if switch == 'actor'
                else other if switch == 'credential' else principal)
    original, observed = reads.record, []

    @contextmanager
    def inspect(db, who):
        with original(db, who) as state:
            observed.append(state['budget'] is outer_budget)
            yield state

    def run(legacy=False):
        nonlocal outer_budget
        with reads.scope(db, principal) as outer:
            outer_budget = outer['budget']
            def nested():
                if exhausted:
                    for index in range(reads.MAX_NODES - 1):
                        assert reads.check(db, principal, 'test_leaf', str(index), lambda: True)
                    assert outer_budget['nodes'] == reads.MAX_NODES
                with ExitStack() as scopes:
                    if switch == 'a_b_a':
                        scopes.enter_context(reads.scope(db, other))
                        scopes.enter_context(reads.scope(db, principal))
                    result.update(pages(w, selected, op))
                return True
            result = {}
            with monkeypatch.context() as patch, pm_read_cache.guarded() if guarded else nullcontext():
                # Before record boundaries existed, every nested row used an
                # ordinary source scope. This is the legacy nested-budget oracle.
                patch.setattr(reads, 'record', reads.scope if legacy else inspect)
                assert reads.check(db, principal, 'test_root', 'outer', nested)
            if exhausted:
                assert outer_budget['nodes'] == reads.MAX_NODES
            # The same still-open scope gets independent rows after the walk ends.
            after = pages(w, selected, op)
            assert selected_counts(after, op) == [1, 1, 1, 1]
            return result

    outer_budget = None
    expected = run(legacy=True)
    actual = run()
    assert actual == expected  # Complete responses, including scan/cursor metadata.
    # Receipt authorization can complete without charging an original-root node;
    # exhaustion is not an unconditional denial. Preserve the complete legacy result.
    assert selected_counts(actual, op)[:3] == ([0] * 3 if exhausted else [1] * 3)
    assert observed and all(observed)
