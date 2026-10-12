"""Same-ledger saved publication reads preserve every original source boundary."""
import asyncio
from dataclasses import replace

import pytest

from bat_agent_connector import pm_access, pm_source_reads, pr_publication
from bat_agent_connector import pm_execution_verification as verifier
from bat_agent_connector import pm_maintenance_repair_publication as publication
from bat_agent_connector import pm_maintenance_repairs as repairs
from bat_agent_connector import pm_quality_reviews as quality
from bat_agent_connector.operations import OperationError
from tests import pm_joined_maintenance_helpers as maintenance
from tests.pm_joined_lifecycle_helpers import OWNER
from tests.test_pm_joined_lifecycle import bat_world, daemon, joined, world  # noqa: F401
from tests.test_pm_joined_lifecycle import test_same_ledger_development_and_semantic_dependency as run_story
from tests.test_pm_quality_source_reads import prior_quality_access


class PublicationChecked(Exception):
    """This source test ends at the durable PR, before the delivery story."""


def prior_verifier_visible(db, principal, original):
    """The exact verifier source predicate before typed boolean reuse."""
    try:
        from bat_agent_connector.pm_quality_reviews import _Reader
        ops = _Reader(db)
        op = verifier._op(ops, original['operation_id'])
        if op['action'] != verifier.ACTION:
            return False
        verifier._access(ops, principal, op)
        return True
    except (OperationError, KeyError, TypeError, ValueError):
        return False


def measured(db, read):
    count = 0
    def trace(sql):
        nonlocal count
        count += sql.lstrip().upper().startswith('SELECT')
    db.set_trace_callback(trace)
    try:
        result = read()
    finally:
        db.set_trace_callback(None)
    return result, count


async def check_publication(w, parent, published, monkeypatch, record_property):
    ops = w['d'].ops
    child = repairs._child(ops, parent)
    intent = pr_publication._intent(published)
    def read():
        return pr_publication.get_publication(ops, OWNER, published['operation_id'])
    with monkeypatch.context() as prior:
        prior.setattr(verifier, 'operation_visible', prior_verifier_visible)
        prior.setattr(pr_publication, '_quality_access', pr_publication._quality_sources)
        prior.setattr(pr_publication, 'operation_visible', pr_publication._operation_visible)
        old, old_queries = measured(ops.db, read)
        _, old_repair_queries = measured(ops.db, lambda: repairs._full_access(ops, OWNER, parent))
    new, new_queries = measured(ops.db, read)
    _, new_repair_queries = measured(ops.db, lambda: repairs._full_access(ops, OWNER, parent))
    with monkeypatch.context() as prior_quality:
        prior_quality.setattr(quality, '_access', prior_quality_access)
        _, prior_quality_queries = measured(ops.db, lambda: repairs._full_access(ops, OWNER, parent))
    assert new_repair_queries * 3 < prior_quality_queries * 2
    assert old == new and new['status'] == 'succeeded'
    receipt = new['creation_receipt']
    assert receipt['target_verified'] and receipt['http_status'] == 201
    assert receipt['child_operation_id'] == child['operation_id']
    assert new_queries * 3 < old_queries
    assert new_repair_queries * 2 < old_repair_queries
    for key, value in (('publication_prior_queries', old_queries), ('publication_scoped_queries', new_queries),
            ('repair_prior_queries', old_repair_queries), ('repair_verifier_scoped_queries', new_repair_queries)):
        record_property(key, value)
    record_property('repair_before_quality_source_queries', prior_quality_queries)

    originals = [parent, child, published, ops.get(intent['quality_ref']['operation_id'])]
    verifying = ops.get(ops.db.execute("SELECT operation_id FROM operations WHERE action=? "
        "AND json_extract(target,'$.execution_operation_id')=? ORDER BY created_at DESC LIMIT 1",
        (verifier.ACTION, child['operation_id'])).fetchone()[0])
    originals.append(verifying)
    # Enter through the verifier first, with and without an existing read scope.
    # Its saved quality -> owned child -> same repair edge is valid in the
    # original predicate; the typed wrapper must preserve both read orders.
    with monkeypatch.context() as prior:
        prior.setattr(verifier, 'operation_visible', prior_verifier_visible)
        assert verifier.operation_visible(ops.db, OWNER, verifying)
        with pm_source_reads.scope(ops.db, OWNER):
            assert verifier.operation_visible(ops.db, OWNER, verifying)
    assert verifier.operation_visible(ops.db, OWNER, verifying)
    with pm_source_reads.scope(ops.db, OWNER):
        assert verifier.operation_visible(ops.db, OWNER, verifying)
    events = []
    for op in originals:
        assert ops.get(op['operation_id'], principal=OWNER)['operation_id'] == op['operation_id']
        events.extend(ops.journal.api_events(resource_id=op['operation_id'], limit=500)['events'])
    assert events and all(pm_access.event_visible(ops.db, OWNER, event) for event in events)

    # The memo reads canonical rows, never trusts an operation projection; each
    # credential gets its own complete predicate, including an explicit denial.
    access, calls = verifier._access, []
    other = replace(OWNER, credential_id='different-original-credential')
    def credential_access(reader, principal, op):
        calls.append(principal.credential_id)
        if principal.credential_id == other.credential_id:
            raise OperationError('NOT_FOUND', 'Original credential cannot read this source', 404)
        return access(reader, principal, op)
    with monkeypatch.context() as credentials:
        credentials.setattr(verifier, '_access', credential_access)
        with pm_source_reads.scope(ops.db, OWNER):
            assert verifier.operation_visible(ops.db, OWNER, verifying)
            before = len(calls)
            assert verifier.operation_visible(ops.db, OWNER, {'operation_id': verifying['operation_id'], 'action': 'untrusted'})
            assert len(calls) == before
            assert not verifier.operation_visible(ops.db, other, verifying)
            assert verifier.operation_visible(ops.db, OWNER, verifying)
    assert other.credential_id in calls

    first = ops.db.execute('''SELECT m.operation_id FROM pm_messages m JOIN operations o ON o.operation_id=m.operation_id
        WHERE m.workspace_id=? AND o.action='pm.message.append' ORDER BY m.sequence LIMIT 1''',
        (w['workspace_id'],)).fetchone()[0]
    visible = pm_access.visible
    with monkeypatch.context() as withdrawn:
        withdrawn.setattr(pm_access, 'visible', lambda db, principal, op:
            op['operation_id'] != first and visible(db, principal, op))
        await asyncio.sleep(0)  # Later frames cannot inherit the prior positive read.
        assert not verifier.operation_visible(ops.db, OWNER, verifying)
        for op in originals:
            with pytest.raises(OperationError) as error:
                ops.get(op['operation_id'], principal=OWNER)
            assert error.value.status == 404
        assert not any(pm_access.event_visible(ops.db, OWNER, event) for event in events)
        with pytest.raises(OperationError) as error:
            read()
        assert error.value.status == 404
    assert read() == new

    member = dict(ops.db.execute('SELECT * FROM pm_workspace_members WHERE workspace_id=? AND actor=?',
        (w['workspace_id'], OWNER.actor)).fetchone())
    ops.db.execute('DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?', (w['workspace_id'], OWNER.actor))
    assert not verifier.operation_visible(ops.db, OWNER, verifying)
    with pytest.raises(OperationError) as error:
        read()
    assert error.value.status == 404
    columns = ','.join(member)
    ops.db.execute(f'INSERT INTO pm_workspace_members({columns}) VALUES({",".join("?" for _ in member)})', tuple(member.values()))
    assert read() == new


async def test_saved_publication_reuses_original_verifier_sources_without_losing_privacy(joined, mock, monkeypatch, record_property):  # noqa: F811
    advance, checked = maintenance.advance, []
    async def stop_at_publication(w, parent, rounds=3):
        for _ in range(rounds):
            parent = await advance(w, parent, rounds=1)
            published = publication.child(w['d'].ops, parent)
            if published and published['status'] == 'succeeded':
                await check_publication(w, parent, published, monkeypatch, record_property)
                checked.append(published['operation_id'])
                raise PublicationChecked
        return parent
    monkeypatch.setattr(maintenance, 'advance', stop_at_publication)
    with pytest.raises(PublicationChecked):
        await run_story(joined, mock, monkeypatch, record_property)
    assert len(checked) == 1
