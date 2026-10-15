"""Measure original joined-source traversal, not a synthetic permission shortcut."""
from collections import Counter
from contextvars import ContextVar

import pytest

from bat_agent_connector import pm_access, pm_conversation_proposals, pm_source_lineage
from bat_agent_connector.operations import OperationError
from tests import pm_joined_lifecycle_helpers as story
from tests.test_pm_joined_lifecycle import bat_world, daemon, joined, world  # noqa: F401


def legacy_bound():
    """The exact pre-extraction conversation-only guard (06e79cf)."""
    access = ContextVar('fixture_original_conversation_access', default=None)
    def bounded(db, principal, kind, identity, check):
        identity_key = id(db), principal.actor, principal.scopes, principal.admin
        state, token = access.get(), None
        if state is None or state['identity'] != identity_key:
            state = {'identity': identity_key, 'active': set(), 'memo': {}}
            token = access.set(state)
        key = kind, identity
        try:
            if key in state['memo']:
                return state['memo'][key]
            if key in state['active'] or len(state['active']) >= 64 or len(state['memo']) + len(state['active']) >= 2048:
                return False
            state['active'].add(key)
            try:
                result = bool(check())
                state['memo'][key] = result
                return result
            finally:
                state['active'].remove(key)
        finally:
            if token is not None:
                access.reset(token)
    return bounded


def legacy_sources(db, principal, identities):
    if any(not pm_access.operation_source_visible(db, principal, identity) for identity in set(identities)):
        raise OperationError('PM_SOURCE_UNAVAILABLE', 'Original PM sources are unavailable', 404)


def measured(db, operation, monkeypatch, *, legacy, principal=story.OWNER):
    queries, roots = [], Counter()
    original = pm_source_lineage._visible_roots
    def visit(db, principal, identities):
        values = tuple(identities)
        roots.update(values)
        return original(db, principal, values)
    with monkeypatch.context() as local:
        local.setattr(pm_source_lineage, '_visible_roots', visit)
        if legacy:
            local.setattr(pm_source_lineage, 'visible_roots', visit)
            local.setattr(pm_access, 'visible', pm_access._visible)
            local.setattr(pm_access, 'require_operation_sources', legacy_sources)
            local.setattr(pm_conversation_proposals, '_bounded_access', legacy_bound())
        # This is the fixture's own connection; no global sqlite monkeypatch.
        db.set_trace_callback(lambda sql: queries.append(1) if sql.lstrip().upper().startswith('SELECT') else None)
        try:
            visible = pm_access.visible(db, principal, operation)
        finally:
            db.set_trace_callback(None)
    return visible, len(queries), roots


async def test_joined_quality_dependency_reuses_each_original_root_once(joined, mock, monkeypatch, record_property):  # noqa: F811
    w = await story.develop_original_requirements(joined, mock)
    ops = w['d'].ops
    child = ops.get(w['ledger'].references['execution_ids'][-1])
    changes = ops.db.total_changes
    old, old_queries, old_roots = measured(ops.db, child, monkeypatch, legacy=True)
    new, new_queries, new_roots = measured(ops.db, child, monkeypatch, legacy=False)
    assert old is new is True
    assert new_roots and set(new_roots) == set(old_roots)
    assert all(count == 1 for count in new_roots.values())
    assert sum(old_roots.values()) > sum(new_roots.values())
    assert old_queries > new_queries
    assert ops.db.total_changes == changes
    record_property('original_source_queries', old_queries)
    record_property('scoped_source_queries', new_queries)
    record_property('original_root_visits', sum(old_roots.values()))
    record_property('scoped_root_visits', sum(new_roots.values()))

    # Withdraw only the original conversation message at its established read
    # boundary, preserving the member, all saved children, files and receipts.
    initial = ops.db.execute('''SELECT m.operation_id FROM pm_messages m JOIN operations o ON o.operation_id=m.operation_id
        WHERE m.workspace_id=? AND o.action='pm.message.append' ORDER BY m.sequence LIMIT 1''',
        (w['workspace_id'],)).fetchone()[0]
    visible = pm_access.visible
    state = {'available': True}
    with monkeypatch.context() as source:
        source.setattr(pm_access, 'visible', lambda db, principal, op:
            (state['available'] or op['operation_id'] != initial) and visible(db, principal, op))
        assert pm_access.visible(ops.db, story.OWNER, child)
        state['available'] = False
        assert not pm_access.visible(ops.db, story.OWNER, child)
        with pytest.raises(OperationError):
            ops.get(child['operation_id'], principal=story.OWNER)
        state['available'] = True
        assert pm_access.visible(ops.db, story.OWNER, child)

    # A later role/source withdrawal is a fresh read, never the previous answer.
    member = dict(ops.db.execute('SELECT * FROM pm_workspace_members WHERE workspace_id=? AND actor=?',
        (w['workspace_id'], story.OWNER.actor)).fetchone())
    ops.db.execute('DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?',
        (w['workspace_id'], story.OWNER.actor))
    assert not pm_access.visible(ops.db, story.OWNER, child)
    columns = ','.join(member)
    ops.db.execute(f'INSERT INTO pm_workspace_members({columns}) VALUES({",".join("?" for _ in member)})', tuple(member.values()))
    assert pm_access.visible(ops.db, story.OWNER, child)
    story.assert_manual_preserved(w, mock)
