"""Review-to-Idea frozen PM context preserves one original read authority."""
import asyncio
import copy
from dataclasses import replace

import pytest

from bat_agent_connector import (
    pm_access,
    pm_runs,
    pm_source_lineage,
    pm_source_reads,
    pm_workload,
    pm_workspace,
)
from bat_agent_connector import pm_maintenance_review as review
from bat_agent_connector import pm_maintenance_review_sources as sources
from bat_agent_connector.operations import OperationError
from tests import test_pm_review_planning_source as planning
from tests.test_pm_evidence import OWNER, READER, member
from tests.test_pm_release_baselines import execute, remove_repo
from tests.test_pm_runs import create as freeze_run

base_ops, release_ops, workload_ops, github_ops, ops = (
    planning.base_ops, planning.release_ops, planning.workload_ops, planning.github_ops, planning.ops)


def stored_bytes(ops, run, candidate):
    return (
        ops.db.execute('SELECT context FROM pm_runs WHERE run_id=?', (run['run_id'],)).fetchone()[0],
        ops.db.execute('SELECT document FROM pm_maintenance_review_events WHERE operation_id=?',
            (candidate['operation_id'],)).fetchone()[0],
        tuple(row[0] for row in ops.db.execute('SELECT document FROM pm_document_snapshots ORDER BY sequence')),
    )


@pytest.mark.parametrize('withdrawal', ['repository', 'membership'])
async def test_original_review_candidate_and_frozen_run_reads_share_no_later_authority(ops, monkeypatch, withdrawal):  # noqa: F811
    data = await planning.prepared(ops, monkeypatch)
    wid, tid = data['workspace_id'], data['thread_id']
    imported = await execute(ops, planning.request(ops, data))
    candidate = ops.get(data['event']['operation_id'])
    refs = candidate['external_refs']['admission_binding']['maintenance_review_sources']
    pm_runs.schema(ops.journal)
    # Exercise the existing local context-freeze domain seam, without claiming
    # a registered model attempt, session, process death or provider effect.
    run = freeze_run(ops, {'workspace_id': wid, 'thread_id': tid}, who=OWNER, max_chars=64000)
    assert run['status'] == 'prepared' and run['context']['inputs_by_status']['unresolved']
    assert imported['operation_id'] in pm_source_lineage.roots(ops.db, 'operation', run['operation_id'])
    original = copy.deepcopy(pm_runs.read(ops, READER, wid, run['run_id']))
    original_review = review.get_review(ops, READER, wid, data['event']['review_id'])
    original_bytes, changes = stored_bytes(ops, run, candidate), ops.db.total_changes
    assert sources.access(ops, READER, wid, refs) is None
    assert review.operation_visible(ops.db, READER, candidate)
    assert pm_runs.source_visible(ops.db, READER, run)
    # The wrappers retain their original bool/None and complete payload values.
    assert sources._sources(ops, READER, wid, refs) is None
    assert pm_runs._source_visible(ops.db, READER, run)
    with pm_source_reads.scope(ops.db, READER):
        assert pm_runs.read(ops, READER, wid, run['run_id']) == original
        assert review.get_review(ops, READER, wid, data['event']['review_id']) == original_review
        assert pm_runs.source_visible(ops.db, READER, run)
        assert sources.access(ops, READER, wid, refs) is None
    assert ops.db.total_changes == changes and stored_bytes(ops, run, candidate) == original_bytes

    # One actor with another credential cannot borrow the completed original
    # root decision from the surrounding scope. The denial remains at the
    # established membership boundary rather than a replacement source reader.
    other = replace(READER, credential_id='different-source-credential')
    require_member, denied = pm_workspace.require_member, []
    def membership(db, principal, *args, **kwargs):
        if principal.credential_id == other.credential_id:
            denied.append(principal.credential_id)
            raise OperationError('NOT_FOUND', 'Original credential cannot read this source', 404)
        return require_member(db, principal, *args, **kwargs)
    with monkeypatch.context() as credentials:
        credentials.setattr(pm_workspace, 'require_member', membership)
        with pm_source_reads.scope(ops.db, READER):
            assert pm_runs.source_visible(ops.db, READER, run)
            assert not pm_runs.source_visible(ops.db, other, run)
            assert not review.operation_visible(ops.db, other, candidate)
            with pytest.raises(OperationError) as hidden:
                sources.access(ops, other, wid, refs)
            assert hidden.value.status == 404
            assert pm_runs.source_visible(ops.db, READER, run)
    assert denied and ops.db.total_changes == changes

    events = ops.journal.api_events(resource_id=candidate['operation_id'])['events']
    assert events and all(pm_access.event_visible(ops.db, READER, event) for event in events)
    await asyncio.sleep(0)
    if withdrawal == 'repository':
        await remove_repo(ops, data['project_id'])
    else:
        await member(ops, {'workspace_id': wid}, READER.actor, None)
    assert not pm_runs.source_visible(ops.db, READER, run)
    assert not review.operation_visible(ops.db, READER, candidate)
    assert not pm_access.visible(ops.db, READER, imported)
    assert not any(pm_access.event_visible(ops.db, READER, event) for event in events)
    for read in (lambda: pm_runs.read(ops, READER, wid, run['run_id']),
                 lambda: review.get_review(ops, READER, wid, data['event']['review_id']),
                 lambda: sources.access(ops, READER, wid, refs)):
        with pytest.raises(OperationError) as hidden:
            read()
        assert hidden.value.status in (403, 404)
    assert stored_bytes(ops, run, candidate) == original_bytes
    assert pm_runs.get(ops.db, run['run_id'])['context'] == run['context']


async def test_review_original_workload_graph_reuses_complete_sources(ops, monkeypatch, record_property):  # noqa: F811
    data = await planning.prepared(ops, monkeypatch)
    candidate = ops.get(data['event']['operation_id'])
    original, calls = pm_workload._source_access, []
    def counted(*args):
        calls.append(args[3]['snapshot_id'])
        return original(*args)
    monkeypatch.setattr(pm_workload, '_source_access', counted)
    changes = ops.db.total_changes
    with monkeypatch.context() as prior:
        prior.setattr(pm_workload, '_access', counted)
        assert review.operation_visible(ops.db, READER, candidate)
    old = len(calls)
    calls.clear()
    assert review.operation_visible(ops.db, READER, candidate)
    new = len(calls)
    assert new == len(set(calls)) > 0
    assert new < old
    assert ops.db.total_changes == changes
    record_property('review_workload_prior_traversals', old)
    record_property('review_workload_canonical_traversals', new)
