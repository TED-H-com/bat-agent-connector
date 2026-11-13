"""Regression probes adapted from the independent roadmap report review (F1–F5, N1–N2).

Fault injection is restricted to these temporary journals. Expected behavior replaces
review characterization assertions; the original product triggers are preserved.
"""
from __future__ import annotations

import asyncio
import copy
import json
from contextlib import contextmanager

import pytest

from bat_agent_connector import pm_access, pm_plans
from bat_agent_connector import pm_roadmap_report_layout as layout
from bat_agent_connector import pm_roadmap_report_sources as sources
from bat_agent_connector import pm_roadmap_reports as reports
from bat_agent_connector.operations import OperationError
from tests.test_api_v1 import http, token
from tests.test_pm_plans import planning
from tests.test_pm_roadmap import OWNER, READER, revision
from tests.test_pm_roadmap_report_api import daemon, served  # noqa: F401
from tests.test_pm_roadmap_report_sources import ops as ops_fixture
from tests.test_pm_roadmap_report_sources import read, selected
from tests.test_pm_snapshots import adopt, adoption, draft, snapshot

ops = ops_fixture


@contextmanager
def fault(ops):
    ops.db.execute('SAVEPOINT report_fault')
    try:
        yield
    finally:
        ops.db.execute('ROLLBACK TO report_fault')
        ops.db.execute('RELEASE report_fault')


def document(ops, table, key, identity, value):
    ops.db.execute(f'DROP TRIGGER IF EXISTS {table}_update_immutable')
    ops.db.execute(f'UPDATE {table} SET document=? WHERE {key}=?', (json.dumps(value), identity))


async def raw_http(port, path, auth):
    reader, writer = await asyncio.open_connection('127.0.0.1', port)
    writer.write(f'GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAuthorization: Bearer {auth}\r\n\r\n'.encode())
    await writer.drain()
    data = await reader.read()
    writer.close()
    await writer.wait_closed()
    headers, _, body = data.partition(b'\r\n\r\n')
    return int(headers.split(b' ')[1]), body


async def test_f1_malformed_authority_has_identical_raw_404(served, monkeypatch):  # noqa: F811
    d, port = served
    target, *_ = await planning(d.ops)
    plan = await draft(d.ops, target)
    wid = target['workspace_id']
    await revision(d.ops, wid, milestones=[])
    frozen = await snapshot(d.ops, wid)
    observed = await revision(d.ops, wid, 'observation', snapshot_id=frozen['snapshot_id'])
    path = f"/api/v1/pm/workspaces/{wid}/roadmap/revisions/{observed['revision_id']}/report"
    auth = token(d, OWNER.actor, 'observe')
    saved = sources.load_fixed(d.ops, OWNER, wid, observed['revision_id'])['snapshot']
    original = pm_access.require_operation_sources
    plan_row = pm_plans._revision(d.ops.db, wid, plan['revision_id'])
    def hidden(db, principal, ids):
        if plan_row['operation_id'] in ids:
            raise OperationError('HIDDEN', 'Private source', 404)
        return original(db, principal, ids)
    with monkeypatch.context() as patch:
        patch.setattr(pm_access, 'require_operation_sources', hidden)
        expected = await raw_http(port, path, auth)
    assert expected[0] == 404
    cases = []
    for bad in (None, [], 'wrong', 7):
        cases.append(('snapshot', {**saved, 'baseline': bad}))
        cases.append(('snapshot', {**saved, 'candidate_document': {'revision_id': bad}}))
        cases.append(('snapshot', {**saved, 'evidence': [bad]}))
        cases.append(('snapshot', {**saved, 'maintenance': {'access_refs': [bad]}}))
        cases.append(('plan', bad))
    for bad in (None, {}, 'wrong', 7):
        cases.append(('snapshot', {**saved, 'evidence': bad}))
        cases.append(('snapshot', {**saved, 'maintenance': {'access_refs': bad}}))
        cases.append(('plan', {**plan_row['document'], 'selected_inputs': bad}))
    for kind, value in cases:
        for condition in ('ordinary', 'large', 'hidden_later'):
            with fault(d.ops), monkeypatch.context() as patch:
                if condition == 'hidden_later':
                    patch.setattr(pm_access, 'require_operation_sources', hidden)
                damaged = copy.deepcopy(value)
                if kind == 'snapshot':
                    if condition == 'large':
                        damaged['padding'] = 'x' * (sources.MAX_SOURCE_BYTES + 1)
                    document(d.ops, 'pm_document_snapshots', 'snapshot_id', frozen['snapshot_id'], damaged)
                else:
                    document(d.ops, 'pm_plan_revisions', 'revision_id', plan['revision_id'], damaged)
                    if condition == 'large':
                        document(d.ops, 'pm_document_snapshots', 'snapshot_id', frozen['snapshot_id'],
                                 {**saved, 'padding': 'x' * (sources.MAX_SOURCE_BYTES + 1)})
                assert await raw_http(port, path, auth) == expected, (kind, condition, type(value))
    # Narrow boundary backstops, never a catch across verification or rendering.
    for error in (AttributeError, IndexError):
        with monkeypatch.context() as patch:
            def malformed(*_):
                raise error('private malformed source')
            patch.setattr(sources._Closure, 'collect', malformed)
            assert await raw_http(port, path, auth) == expected


async def test_f2_missing_named_operations_and_exact_snapshot_adoption_refuse(ops):  # noqa: F811
    chosen = await selected(ops)
    fixed = read(ops, chosen)
    closure = sources._Closure(ops, READER, chosen[0]['workspace_id']).collect(chosen[3]['revision_id'])
    identities = {fixed[k]['operation_id'] for k in ('baseline', 'observation')}
    identities.add(chosen[2]['operation_id'])
    for plan in closure[-2].values():
        identities.update(pm_plans.source_operation_ids(ops.db, chosen[0]['workspace_id'], plan['revision_id'], include_adoption=False))
    for row in closure[-1].values():
        identities.update((row['operation_id'], row['result']['commit_operation_id']))
    for identity in identities:
        with fault(ops):
            ops.db.execute('DELETE FROM operations WHERE operation_id=?', (identity,))
            with pytest.raises(OperationError, match='PM_REPORT_SOURCE_UNAVAILABLE') as error:
                read(ops, chosen)
            assert error.value.status == 404
    target, old, *_ = chosen
    new = await draft(ops, target, title='Later accepted scope')
    adopted = adopt(adoption(ops, target, new['revision_id'], previous=old['revision_id']))
    frozen = await snapshot(ops, target['workspace_id'])
    observed = await revision(ops, target['workspace_id'], 'observation', snapshot_id=frozen['snapshot_id'])
    args = ops, READER, target['workspace_id'], observed['revision_id']
    sources.load_fixed(*args)
    with fault(ops):
        ops.db.execute('DROP TRIGGER pm_plan_adoptions_delete_immutable')
        ops.db.execute('DELETE FROM pm_plan_adoptions WHERE decision_id=?', (adopted['decision_id'],))
        with pytest.raises(OperationError, match='PM_REPORT_SOURCE_UNAVAILABLE'):
            sources.load_fixed(*args)


async def test_f3_complete_escape_set_from_real_admission(ops):  # noqa: F811
    chosen = await selected(ops)
    wid = chosen[0]['workspace_id']
    title = 'Title\u061c\u200e\u200f\ufdd0\ufdef'
    fixed = read(ops, chosen)
    milestone = fixed['baseline']['document']['milestones'][0]
    item = {k: milestone[k] for k in ('milestone_id', 'title', 'plan_revision_id', 'requirement_ids', 'work_binding_ids', 'baseline_date', 'depends_on')}
    await revision(ops, wid, title=title, milestones=[item])
    observed = await revision(ops, wid, 'observation', snapshot_id=chosen[2]['snapshot_id'])
    for format in reports.FORMATS:
        content = reports.get_report(ops, READER, wid, observed['revision_id'], format=format)['content']
        for char in title[5:]:
            assert char not in content
        assert '\\u061c' in content
    points = [*range(32), *range(0x7F, 0xA0), 0x061C, 0x200E, 0x200F,
              *range(0x202A, 0x202F), *range(0x2066, 0x206A), *range(0xFDD0, 0xFDF0),
              *(plane * 0x10000 + last for plane in range(17) for last in (0xFFFE, 0xFFFF))]
    # CR and TAB retain the existing LF/tab normalization before control escaping.
    for cp in set(points) - {9, 10, 13}:
        assert layout.literal(chr(cp)) == f'\\u{cp:04x}'
    assert layout.normalize('\ud800\udfff') == '\\ud800\\udfff'


async def test_f4_capability_uses_each_existing_dependency_flag(served):  # noqa: F811
    d, port = served
    auth = token(d, OWNER.actor, 'observe')
    for action, flag in [('pm.roadmap.baseline.set', 'roadmap'), ('pm.snapshot.create', 'brief_snapshots'), ('pm.plan.draft', 'plan_drafting')]:
        removed = d.ops.actions.pop(action)
        try:
            status, result = await http(port, 'GET', '/api/v1/capabilities', tok=auth)
            features = result['features']['pm_workspaces']
            assert status == 200 and features[flag] is False
            assert 'roadmap_report' not in features
        finally:
            d.ops.actions[action] = removed


async def test_f5_future_template_before_v1_proof(ops):  # noqa: F811
    chosen = await selected(ops)
    closure = sources._Closure(ops, READER, chosen[0]['workspace_id']).collect(chosen[3]['revision_id'])
    saved, observed = closure[3]['document'], closure[0]['document']
    saved['template_version'] = 2
    del saved['baseline']['document_revision']
    observed['snapshot_digest'] = sources.digest(saved)
    with fault(ops):
        document(ops, 'pm_document_snapshots', 'snapshot_id', saved['snapshot_id'], saved)
        ops.db.execute('UPDATE pm_document_snapshots SET snapshot_digest=? WHERE snapshot_id=?', (sources.digest(saved), saved['snapshot_id']))
        document(ops, 'pm_roadmap_revisions', 'revision_id', chosen[3]['revision_id'], observed)
        ops.db.execute('UPDATE pm_roadmap_revisions SET document_digest=? WHERE revision_id=?', (sources.digest(observed), chosen[3]['revision_id']))
        with pytest.raises(OperationError, match='PM_EXPORT_TEMPLATE_UNAVAILABLE') as error:
            read(ops, chosen)
        assert error.value.status == 409


async def test_n1_authorized_relationship_mismatches_never_filter_silently(ops):  # noqa: F811
    chosen = await selected(ops)
    original = sources._Closure(ops, READER, chosen[0]['workspace_id']).collect(chosen[3]['revision_id'])
    for case in ('evidence', 'milestone', 'quality', 'adoption'):
        closure = copy.deepcopy(original)
        observed = closure[0]['document']
        if case == 'evidence':
            observed['milestones'][0]['evidence_ids'] = ['pme_' + 'f' * 32]
        elif case == 'milestone':
            observed['milestones'][0]['milestone_id'] = 'different'
        elif case == 'quality':
            observed['milestones'][0]['quality_context']['scope_revision'] = 'pmscope_' + 'f' * 32
        else:
            next(iter(closure[-1].values()))['result']['decision_id'] = 'pmdec_' + 'f' * 32
        closure[0]['document_digest'] = sources.digest(observed)
        with pytest.raises(OperationError, match='PM_REPORT_SOURCE_CHANGED') as error:
            sources._verify(closure)
        assert error.value.status == 409


async def test_n2_all_legacy_adoptions_are_canonical_and_required(ops, monkeypatch):  # noqa: F811
    chosen = await selected(ops)
    original = dict(ops.db.execute('SELECT * FROM pm_plan_adoptions').fetchone())
    duplicate = dict(original)
    duplicate.update(operation_id='op_' + 'e' * 32, decision_id='pmdec_' + 'e' * 32, created_at=original['created_at'] + 10)
    result = json.loads(duplicate['result'])
    result.update(decision_id=duplicate['decision_id'], commit_operation_id=duplicate['operation_id'])
    duplicate['result'] = json.dumps(result)
    # Synthetic legacy duplicate has real operation-row existence, as required by F2.
    operation = dict(ops.db.execute('SELECT * FROM operations WHERE operation_id=?', (original['operation_id'],)).fetchone())
    operation.update(operation_id=duplicate['operation_id'], idem_key='legacy-duplicate')
    for table, row in [('operations', operation), ('pm_plan_adoptions', duplicate)]:
        ops.db.execute(f"INSERT INTO {table} ({','.join(row)}) VALUES ({','.join('?' for _ in row)})", tuple(row.values()))
    args = ops, READER, chosen[0]['workspace_id'], chosen[3]['revision_id']
    before = [reports.get_report(*args, format=f, locale=locale) for f in reports.FORMATS for locale in ('en', 'zh-TW')]
    rows = ops.db.execute('SELECT rowid,* FROM pm_plan_adoptions').fetchall()
    ops.db.execute('DROP TRIGGER pm_plan_adoptions_update_immutable')
    ops.db.execute('UPDATE pm_plan_adoptions SET rowid=rowid+100')
    for index, row in enumerate(reversed(rows)):
        ops.db.execute('UPDATE pm_plan_adoptions SET rowid=? WHERE operation_id=?', (index + 1, row['operation_id']))
    assert [reports.get_report(*args, format=f, locale=locale) for f in reports.FORMATS for locale in ('en', 'zh-TW')] == before
    manifest = read(ops, chosen)['source_manifest']
    assert {row['id'] for row in manifest if row['kind'] == 'adoption'} == {original['decision_id'], duplicate['decision_id']}
    gate = pm_access.require_operation_sources
    def deny(db, principal, identities):
        if duplicate['operation_id'] in identities:
            raise OperationError('HIDDEN', 'Legacy source unavailable', 404)
        return gate(db, principal, identities)
    monkeypatch.setattr(pm_access, 'require_operation_sources', deny)
    with pytest.raises(OperationError, match='PM_REPORT_SOURCE_UNAVAILABLE'):
        read(ops, chosen)


async def test_lone_surrogate_admission_and_corruption_are_distinct(ops):  # noqa: F811
    chosen = await selected(ops)
    wid = chosen[0]['workspace_id']
    # JSON escape decoding is performed before OperationService admission.
    text = json.loads('"\\ud800"')
    before = ops.db.total_changes
    for action, target, params in [
        ('pm.workspace.create', {}, {'name': text}),
        ('pm.snapshot.create', {'workspace_id': wid}, {'title': text}),
        ('pm.roadmap.baseline.set', {'workspace_id': wid}, {'rationale': text}),
        ('pm.plan.draft', chosen[0], {'title': text}),
        ('pm.input.record', chosen[0], {'value': text})]:
        with pytest.raises(OperationError, match='INVALID_REQUEST') as error:
            ops.create(OWNER, action=action, target=target, params=params, idempotency_key='surrogate')
        assert error.value.status == 422
    assert ops.db.total_changes == before
    saved = copy.deepcopy(read(ops, chosen)['snapshot'])
    saved['title'] = text
    with fault(ops):
        document(ops, 'pm_document_snapshots', 'snapshot_id', saved['snapshot_id'], saved)
        with pytest.raises(OperationError, match='PM_REPORT_SOURCE_UNAVAILABLE') as error:
            read(ops, chosen)
        assert error.value.status == 404
    from tests.test_pm_roadmap_reports import fixture_projection
    fixed = fixture_projection()
    fixed['snapshot']['workspace_name'] = text
    for format in reports.FORMATS:
        result = reports.render_fixed(fixed, format=format)
        assert text not in result['content'] and '\\ud800' in result['content']
