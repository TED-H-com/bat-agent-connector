"""The actual Review-to-Idea DAG reuses only complete repair source decisions."""
import asyncio
import copy
from collections import Counter
from dataclasses import replace

import pytest

from bat_agent_connector import pm_access, pm_runs, pm_source_reads
from bat_agent_connector import pm_maintenance_repairs as repairs
from bat_agent_connector.operations import OperationError
from tests import test_pm_joined_lifecycle as story
from tests.pm_joined_lifecycle_helpers import OWNER
from tests.test_pm_joined_lifecycle import bat_world, daemon, joined, world  # noqa: F401
from tests.test_pm_publication_source_reads import measured
from tests.test_pm_runs import create as freeze_run


class SourcesChecked(Exception):
    """Stop before the independent late model/reconnect/adoption story."""


async def test_complete_repair_sources_are_reused_in_original_review_pm_context(joined, mock, monkeypatch, record_property):  # noqa: F811
    inspected = []

    async def inspect(w, mock, monkeypatch):
        ops, wid = w['d'].ops, w['workspace_id']
        parent = repairs._operation(ops, w['ledger'].references['repair_operation_id'])
        assert parent['status'] == 'succeeded'
        target = {'workspace_id': wid, 'thread_id': w['ledger'].references['next_idea_thread_id']}
        # Freeze the existing local context seam, not another model execution.
        # Its actual original Review candidate carries the complete repair DAG.
        run = freeze_run(ops, target, who=OWNER, max_chars=64000)
        assert run['status'] == 'prepared'
        source, calls = repairs._access, Counter()
        def counted(reader, principal, original):
            calls[original['operation_id']] += 1
            return source(reader, principal, original)
        monkeypatch.setattr(repairs, '_access', counted)
        changes = ops.db.total_changes
        def read():
            return pm_runs.source_visible(ops.db, OWNER, run)
        with monkeypatch.context() as prior:
            prior.setattr(repairs, '_full_access', repairs._full_access_sources)
            old, old_queries = measured(ops.db, read)
        old_visits = calls.copy()
        calls.clear()
        new, new_queries = measured(ops.db, read)
        assert old is new is True
        assert old_visits[parent['operation_id']] > calls[parent['operation_id']] == 1
        assert new_queries < old_queries
        assert ops.db.total_changes == changes
        record_property('review_repair_prior_source_queries', old_queries)
        record_property('review_repair_completed_source_queries', new_queries)
        record_property('review_repair_prior_traversals', old_visits[parent['operation_id']])
        record_property('review_repair_completed_traversals', calls[parent['operation_id']])

        original = copy.deepcopy(pm_runs.read(ops, OWNER, wid, run['run_id']))
        with pm_source_reads.scope(ops.db, OWNER):
            assert repairs._full_access(ops, OWNER, parent) is None
            assert repairs._full_access(ops, OWNER, parent) is None
            poisoned = copy.deepcopy(parent)
            binding = poisoned['external_refs']['admission_binding']
            binding['pm_maintenance_repair']['finding_ref']['document_digest'] = '0' * 64
            binding['pm_maintenance_repair_digest'] = repairs.checks._digest(binding['pm_maintenance_repair'])
            with pytest.raises(OperationError) as changed:
                repairs._full_access(ops, OWNER, poisoned)
            assert changed.value.code == 'NOT_FOUND'
            assert pm_runs.read(ops, OWNER, wid, run['run_id']) == original

        # Same-principal re-entry is a partial backedge, not a reusable grant.
        key = ('repair_sources', parent['operation_id'])
        partial = []
        def reenter(reader, principal, original):
            state = pm_source_reads._READ.get()
            assert key not in state['memo']
            assert repairs._full_access(reader, principal, original) is None
            assert key not in state['memo']
            partial.append(original['operation_id'])
            return source(reader, principal, original)
        with monkeypatch.context() as active:
            active.setattr(repairs, '_access', reenter)
            with pm_source_reads.scope(ops.db, OWNER):
                assert repairs._full_access(ops, OWNER, parent) is None
                assert pm_source_reads._READ.get()['memo'][key] is True
                assert repairs._full_access(ops, OWNER, parent) is None
        assert partial == [parent['operation_id']] and not repairs._READING.get()

        other = replace(OWNER, credential_id='different-original-credential')
        failed = []
        def deny(reader, principal, original):
            if principal.credential_id == other.credential_id:
                failed.append(principal.credential_id)
                raise OperationError('ORIGINAL_SOURCE_DENIED', 'Original source unavailable', 404)
            return source(reader, principal, original)
        with monkeypatch.context() as credentials:
            credentials.setattr(repairs, '_access', deny)
            with pm_source_reads.scope(ops.db, OWNER):
                assert repairs._full_access(ops, OWNER, parent) is None
                with pm_source_reads.scope(ops.db, other):
                    for _ in range(2):
                        with pytest.raises(OperationError) as hidden:
                            repairs._full_access(ops, other, parent)
                        assert hidden.value.code == 'ORIGINAL_SOURCE_DENIED'
                        assert key not in pm_source_reads._READ.get()['memo']
                assert repairs._full_access(ops, OWNER, parent) is None
        assert failed == [other.credential_id, other.credential_id]
        assert not repairs._READING.get()

        parent_events = ops.journal.api_events(resource_id=parent['operation_id'], limit=500)['events']
        assert parent_events and all(pm_access.event_visible(ops.db, OWNER, event) for event in parent_events)
        assert ops.get(parent['operation_id'], principal=OWNER)['operation_id'] == parent['operation_id']
        first = ops.db.execute('''SELECT m.operation_id FROM pm_messages m JOIN operations o ON o.operation_id=m.operation_id
            WHERE m.workspace_id=? AND o.action='pm.message.append' ORDER BY m.sequence LIMIT 1''', (wid,)).fetchone()[0]
        visible = pm_access.visible
        with monkeypatch.context() as withdrawn:
            withdrawn.setattr(pm_access, 'visible', lambda db, principal, op:
                op['operation_id'] != first and visible(db, principal, op))
            await asyncio.sleep(0)
            assert not pm_runs.source_visible(ops.db, OWNER, run)
            with pytest.raises(OperationError) as hidden:
                pm_runs.read(ops, OWNER, wid, run['run_id'])
            assert hidden.value.status == 404
            with pytest.raises(OperationError) as hidden:
                ops.get(parent['operation_id'], principal=OWNER)
            assert hidden.value.status == 404
            assert not any(pm_access.event_visible(ops.db, OWNER, event) for event in parent_events)
        assert pm_runs.read(ops, OWNER, wid, run['run_id']) == original
        assert ops.db.total_changes == changes
        inspected.append(run['run_id'])
        raise SourcesChecked

    monkeypatch.setattr(story, 'reconnect_and_replan', inspect)
    with pytest.raises(SourcesChecked):
        await story.test_same_ledger_development_and_semantic_dependency(joined, mock, monkeypatch, record_property)
    assert len(inspected) == 1
