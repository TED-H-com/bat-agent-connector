"""Page partitioning preserves record policy, response context and envelope gates."""
from __future__ import annotations

import copy
import json
import random
import sqlite3

import pytest

from bat_agent_connector import pm_source_lineage as lineage
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OperationError
from tests.pm_page_source_reference import namespace

OWNER = Principal('page-reader', frozenset({'observe'}))
LIMIT = ('PM_SOURCE_LINEAGE_LIMIT', 'Too many original sources for one document', 409)
HIDDEN = ('NOT_FOUND', 'Original resource sources are unavailable', 404)
DEPTH = ('PM_SOURCE_LINEAGE_LIMIT', 'Source references exceed the bounded document depth', 409)


@pytest.fixture
def sources(tmp_path, monkeypatch):
    db = sqlite3.connect(tmp_path / 'page-sources.db', isolation_level=None)
    db.execute('CREATE TABLE operations(operation_id TEXT,action TEXT,target TEXT,params TEXT)')
    db.execute('CREATE TABLE pm_source_lineage(resource_type TEXT,resource_id TEXT,original_operation_id TEXT,creation_operation_id TEXT)')
    for index in range(140):
        oid = f'op_{index:032x}'
        db.execute('INSERT INTO operations VALUES(?,?,?,?)', (oid, 'fixture.source', '{}', '{}'))
        for kind, identity in [('operation', oid), ('session', f'fixture-host/session-{index}')]:
            db.execute('INSERT INTO pm_source_lineage VALUES(?,?,?,?)', (kind, identity, oid, oid))
    hidden, calls = set(), []

    def visible(db, principal, identities):
        calls.extend(identities)
        return not hidden.intersection(identities)

    monkeypatch.setattr(lineage, '_visible_roots', visible)
    yield db, hidden, calls
    db.close()


def oid(index):
    return f'op_{index:032x}'


def row(index):
    return {'operation_id': oid(index), 'text': f'fixture-{index}'}


def bootstrap(page, **extra):
    return {'snapshot': {'part': page}, 'sync': {}, 'capabilities': {}, 'pagination': {}, **extra}


def outcome(reader, db, value):
    try:
        result = reader(db, OWNER, copy.deepcopy(value))
        return 'ok', json.dumps(result, ensure_ascii=False).encode()
    except OperationError as error:
        return 'error', (error.code, error.message, error.status)


@pytest.mark.parametrize('key', sorted(lineage._COLLECTIONS))
def test_all_established_collections_have_independent_roots(sources, key):
    db, _, _ = sources
    value = {key: [row(index) for index in range(66)], 'count': 66, 'next_cursor': 'fixed'}
    assert outcome(namespace()['read_response'], db, value) == ('error', LIMIT)
    assert lineage.read_response(db, OWNER, value) == value


@pytest.mark.parametrize('shape', ['deployment', 'runs', 'bootstrap', 'nested_bootstrap'])
def test_special_pages_have_independent_roots(sources, shape):
    db, _, _ = sources
    rows = [row(index) for index in range(66)]
    if shape == 'deployment':
        value = {'items': [dict(item, deployment_id=f'deployment-{i}') for i, item in enumerate(rows)], 'next_cursor': None}
    elif shape == 'runs':
        value = {'workspace_id': 'ws_fixture', 'runs': [dict(item, run_id=f'pmr_{i}', workspace_id='ws_fixture') for i, item in enumerate(rows)],
                 'next_cursor': None, 'coverage': {'scanned': 66, 'visibility_filtered': False}}
    else:
        value = bootstrap({'operations': rows[:33], 'events': rows[33:]})
        if shape == 'nested_bootstrap':
            value = bootstrap(value)
    assert outcome(namespace()['read_response'], db, value) == ('error', LIMIT)
    assert lineage.read_response(db, OWNER, value) == value


@pytest.mark.parametrize('wrapped', [False, True])
def test_hidden_rows_keep_cursor_flag_and_count(sources, wrapped):
    db, hidden, _ = sources
    hidden.add(oid(1))
    page = {'operations': [row(0), dict(row(1), text='PRIVATE-SENTINEL')], 'events': [row(2)],
            'count': 3, 'next_cursor': 'original-cursor'}
    value = bootstrap(page) if wrapped else page
    result = lineage.read_response(db, OWNER, value)
    assert 'PRIVATE-SENTINEL' not in json.dumps(result)
    actual = result['snapshot']['part'] if wrapped else result
    assert actual == {**page, 'operations': [row(0)], 'count': 2, 'source_visibility_filtered': True}
    assert outcome(namespace()['read_response'], db, value) == outcome(lineage.read_response, db, value)


@pytest.mark.parametrize('location', ['page', 'outer', 'snapshot', 'part'])
def test_inherited_host_drops_hidden_row(sources, location):
    db, hidden, _ = sources
    hidden.add(oid(0))
    page = {'sessions': [{'session_id': 'session-0', 'text': 'PRIVATE-SENTINEL'}, {'session_id': 'session-1'}], 'count': 2}
    value = {**page, 'host': 'fixture-host'}
    if location != 'page':
        value = bootstrap(page)
        target = value if location == 'outer' else value['snapshot'] if location == 'snapshot' else value['snapshot']['part']
        target['host'] = 'fixture-host'
    assert outcome(namespace()['read_response'], db, value) == ('error', HIDDEN)
    result = lineage.read_response(db, OWNER, value)
    actual = result if location == 'page' else result['snapshot']['part']
    assert actual['sessions'] == [{'session_id': 'session-1'}]
    assert actual['count'] == 1 and actual['source_visibility_filtered']
    assert 'PRIVATE-SENTINEL' not in json.dumps(result)
    unknown = {'host': 'fixture-host', 'unknown': page}
    assert outcome(lineage.read_response, db, unknown) == ('error', HIDDEN)


@pytest.mark.parametrize('number', [0, 1])
@pytest.mark.parametrize('location', ['page', 'snapshot', 'sync', 'capabilities', 'pagination'])
def test_hidden_envelope_refuses_whole_response(sources, number, location):
    db, hidden, _ = sources
    hidden.add(oid(100))
    page = {'operations': [row(i) for i in range(number)]}
    if location == 'page':
        value = {**page, 'metadata': row(100)}
    else:
        value = bootstrap(page)
        value[location]['metadata'] = row(100)
    assert outcome(lineage.read_response, db, value) == ('error', HIDDEN)


@pytest.mark.parametrize('number', [64, 65])
@pytest.mark.parametrize('location', ['row', 'envelope', 'unknown', 'top_list', 'nested_record', 'receipt', 'bootstrap_envelope'])
def test_individual_document_limits_are_unchanged(sources, number, location):
    db, _, _ = sources
    refs = {'operation_ids': [oid(i) for i in range(number)]}
    value = {'operations': [refs]}
    if location == 'envelope':
        value = {'operations': [], 'metadata': refs}
    elif location == 'unknown':
        value = {'unknown': [refs]}
    elif location == 'top_list':
        value = [refs]
    elif location == 'nested_record':
        value = {'record': {'operations': [refs]}}
    elif location == 'receipt':
        value = {'operation': {'result': refs}}
    elif location == 'bootstrap_envelope':
        value = bootstrap({'operations': []}, sync={'left': {'operation_ids': refs['operation_ids'][:32]}})
        value['snapshot']['metadata'] = {'operation_ids': refs['operation_ids'][32:]}
    expected = outcome(namespace()['read_response'], db, value)
    assert expected[0] == ('ok' if number == 64 else 'error')
    if number == 65:
        assert expected == ('error', LIMIT)
    assert outcome(lineage.read_response, db, value) == expected


@pytest.mark.parametrize('wrapped', [False, True])
@pytest.mark.parametrize('extra_depth', [0, 1])
def test_row_depth_is_counted_from_response_root(sources, wrapped, extra_depth):
    db, _, _ = sources
    depth = 4 if wrapped else 2
    nested = row(0)
    for _ in range(32 - depth + extra_depth):
        nested = {'nested': nested}
    value = {'operations': [nested]}
    if wrapped:
        value = bootstrap(value)
    expected = outcome(namespace()['read_response'], db, value)
    assert expected[0] == ('error' if extra_depth else 'ok')
    if extra_depth:
        assert expected == ('error', DEPTH)
    assert outcome(lineage.read_response, db, value) == expected


@pytest.mark.parametrize('change', ['missing_workspace', 'missing_coverage', 'wrong_coverage', 'missing_cursor', 'wrong_row', 'nested'])
def test_runs_registration_requires_the_complete_shape(sources, change):
    db, _, _ = sources
    value = {'workspace_id': 'ws_fixture', 'runs': [dict(row(i), workspace_id='ws_fixture', run_id=f'pmr_{i}') for i in range(66)],
             'coverage': {'scanned': 66, 'visibility_filtered': False}, 'next_cursor': None}
    if change == 'missing_workspace':
        del value['workspace_id']
    elif change == 'missing_coverage':
        del value['coverage']
    elif change == 'missing_cursor':
        del value['next_cursor']
    elif change == 'wrong_coverage':
        value['coverage']['scanned'] = True
    elif change == 'wrong_row':
        value['runs'][0]['workspace_id'] = 'different'
    else:
        value = {'record': value}
    assert outcome(lineage.read_response, db, value) == ('error', LIMIT)


def test_empty_lineage_scalar_and_unknown_shapes_are_unchanged(sources):
    db, _, _ = sources
    for value in [None, 3, 'text', [], {}, {'items': [row(0)]}, {'runs': [row(0)]}]:
        assert outcome(lineage.read_response, db, value) == outcome(namespace()['read_response'], db, value)
    db.execute('DELETE FROM pm_source_lineage')
    value = {'operations': [{'nested': [row(i) for i in range(100)]}]}
    assert lineage.read_response(db, OWNER, value) == value


def test_single_part_refusal_and_new_runs_visibility_cost(sources):
    db, _, calls = sources
    for wrap in [lambda value: value, bootstrap, lambda value: bootstrap(bootstrap(value))]:
        for key in ['operations', 'events', 'items']:
            value = wrap({key: [dict(row(i), deployment_id=f'dep-{i}') for i in range(66)], 'next_cursor': None})
            calls.clear()
            outcome(namespace()['read_response'], db, value)
            before = len(calls)
            calls.clear()
            assert outcome(lineage.read_response, db, value)[0] == 'ok'
            assert len(calls) <= before
    page = {'workspace_id': 'ws_fixture', 'runs': [dict(row(0), run_id=f'pmr_{i}', workspace_id='ws_fixture') for i in range(64)],
            'coverage': {'scanned': 64, 'visibility_filtered': False}, 'next_cursor': None}
    calls.clear()
    namespace()['read_response'](db, OWNER, page)
    assert len(calls) == 1
    calls.clear()
    lineage.read_response(db, OWNER, page)
    assert len(calls) == 1


def page_rows(value, inherited_host=None):
    """Count handler-returned fixture rows, before any visibility filtering."""
    if not isinstance(value, dict):
        return
    host = value.get('host', inherited_host)
    keys = set(namespace()['_COLLECTIONS'])
    if ('next_cursor' in value and isinstance(value.get('items'), list)
            and all(isinstance(item, dict) and 'deployment_id' in item for item in value['items'])):
        keys.add('items')
    for key in keys:
        for item in value.get(key, []) if isinstance(value.get(key), list) else []:
            yield item, host
    if (isinstance(value.get('snapshot'), dict) and isinstance(value.get('sync'), dict)
            and isinstance(value.get('capabilities'), dict) and 'pagination' in value):
        for part in value['snapshot'].values():
            yield from page_rows(part, value['snapshot'].get('host', host))


def visibility_calls(monkeypatch):
    calls = []
    original = lineage.visible_roots

    def counted(db, principal, identities):
        calls.append(frozenset(identities))
        return original(db, principal, identities)

    monkeypatch.setattr(lineage, 'visible_roots', counted)
    return calls


@pytest.mark.parametrize('shape,counts', [
    ('shared_operations', (65, 1)),
    ('returned_bootstrap', (192, 64)),
    ('inherited_session', (1, 1)),
])
def test_returned_page_exact_costs(sources, monkeypatch, shape, counts):
    """All return on 447da212; the corrected-cost assertions fail there."""
    db, _, roots = sources
    if shape == 'shared_operations':
        value = {'operations': [row(0) for _ in range(64)]}
    elif shape == 'returned_bootstrap':
        value = bootstrap({'operations': [row(i) for i in range(32)]})
        value['snapshot']['second'] = {'events': [row(i) for i in range(32, 64)]}
    else:
        value = {'host': 'fixture-host', 'sessions': [{'session_id': 'session-0'} for _ in range(64)]}
    calls = visibility_calls(monkeypatch)
    roots.clear()
    before = outcome(namespace()['read_response'], db, value)
    before_roots = tuple(roots)
    calls.clear()
    roots.clear()
    after = outcome(lineage.read_response, db, value)
    assert after == before and before[0] == 'ok'
    assert (len(before_roots), len(roots)) == counts
    rows = list(page_rows(value))
    inherited = sum(len(lineage.document_roots(db, item, inherited_host=host)
                        - lineage.document_roots(db, item)) for item, host in rows)
    assert len(roots) <= len(before_roots) + inherited
    assert len(calls) <= len(rows) + 1 and all(len(ids) <= 64 for ids in calls)
    if shape != 'inherited_session':
        assert inherited == 0 and len(roots) <= len(before_roots)


@pytest.mark.parametrize('distinct', [1, 2, 63, 64, 65])
def test_barrier_evaluates_each_distinct_completed_root_once(sources, distinct):
    db, _, calls = sources
    value = {'operations': [row(i % distinct) for i in range(2 * max(64, distinct))]}
    assert lineage.read_response(db, OWNER, value) == value
    assert len(calls) == len(set(calls)) == distinct


@pytest.mark.parametrize('count,width', [(2, 1025), (50, 43), (100, 43)])
def test_barrier_rows_own_their_node_budget_despite_page_total(sources, monkeypatch, count, width):
    from bat_agent_connector import pm_source_reads as reads
    db, _, _ = sources
    def visible(db, principal, identities):
        return all(reads.check(db, principal, 'barrier-fixture', (identity, n), lambda: True)
                   for identity in identities for n in range(width))
    monkeypatch.setattr(lineage, '_visible_roots', visible)
    value = {'operations': [row(i) for i in range(count)]}
    assert lineage.read_response(db, OWNER, value) == value
    # The same independent node guard still hides a single unprovable row.
    width = reads.MAX_NODES
    one = {'operations': [row(0)]}
    assert outcome(lineage.read_response, db, one) == outcome(namespace()['read_response'], db, one)
    assert not lineage.read_response(db, OWNER, one)['operations']


@pytest.mark.parametrize('incomplete', ['refusal', 'node_limit'])
def test_barrier_retries_uncompleted_proof_with_the_later_rows_budget(sources, monkeypatch, incomplete):
    from bat_agent_connector import pm_read_cache
    from bat_agent_connector import pm_source_reads as reads
    db, _, calls = sources
    def visible(db, principal, identities):
        calls.extend(identities)
        if len(calls) == 1:
            if incomplete == 'refusal':
                pm_read_cache.refuse()
            else:
                assert not all(reads.check(db, principal, 'fixture-limit', n, lambda: True)
                               for n in range(reads.MAX_NODES))
            return False
        return True
    monkeypatch.setattr(lineage, '_visible_roots', visible)
    value = {'operations': [dict(row(0), position=1), dict(row(0), position=2)]}
    result = lineage.read_response(db, OWNER, value)
    assert result['operations'] == [value['operations'][1]]
    assert calls == [oid(0), oid(0)]


@pytest.mark.parametrize('code', ['PM_SOURCE_READ_CHANGED', 'PM_SOURCE_LINEAGE_LIMIT'])
def test_exception_is_not_a_completed_root_proof_for_a_later_record(sources, monkeypatch, code):
    from bat_agent_connector import pm_source_reads as reads
    db, _, calls = sources
    def visible(db, principal, identities):
        calls.extend(identities)
        if len(calls) == 1:
            raise OperationError(code, 'Incomplete fixture proof', 409)
        return True
    monkeypatch.setattr(lineage, '_visible_roots', visible)
    # An actual barrier propagates this exception for the whole page. At the
    # primitive boundary, catching it must not publish a proof for another row.
    with reads.scope(db, OWNER):
        with pytest.raises(OperationError), reads.record(db, OWNER):
            lineage.visible_roots(db, OWNER, {oid(0)})
        with reads.record(db, OWNER):
            assert lineage.visible_roots(db, OWNER, {oid(0)})
    assert calls == [oid(0), oid(0)]


def test_hidden_root_is_proven_once_and_remains_hidden_in_every_row(sources):
    db, hidden, calls = sources
    hidden.add(oid(0))
    result = lineage.read_response(db, OWNER, {'operations': [row(0) for _ in range(100)]})
    assert result == {'operations': [], 'source_visibility_filtered': True}
    assert calls == [oid(0)]


def test_two_response_barriers_share_no_proof_after_withdrawal(sources):
    db, hidden, calls = sources
    value = {'operations': [row(0), row(0)]}
    assert lineage.read_response(db, OWNER, value) == value
    hidden.add(oid(0))
    assert lineage.read_response(db, OWNER, value) == {'operations': [], 'source_visibility_filtered': True}
    assert calls == [oid(0), oid(0)]


@pytest.mark.parametrize('change', ['mutation', 'commit'])
def test_barrier_rejects_changed_snapshot_and_closes_its_scope(sources, monkeypatch, change):
    db, _, _ = sources
    original = lineage._visible_roots
    def changed(db, principal, identities):
        if change == 'mutation':
            db.execute("UPDATE operations SET action=action")
        else:
            db.commit()
        return True
    monkeypatch.setattr(lineage, '_visible_roots', changed)
    with pytest.raises(OperationError) as error:
        lineage.read_response(db, OWNER, {'operations': [row(0)]})
    assert error.value.code == 'PM_SOURCE_READ_CHANGED'
    monkeypatch.setattr(lineage, '_visible_roots', original)
    assert lineage.read_response(db, OWNER, {'operations': [row(0)]}) == {'operations': [row(0)]}


async def test_barrier_rejects_a_same_task_scope_carried_across_a_loop_turn(sources):
    import asyncio

    from bat_agent_connector import pm_source_reads as reads
    db, _, _ = sources
    with pytest.raises(OperationError) as error, reads.scope(db, OWNER):
        await asyncio.sleep(0)
        lineage.read_response(db, OWNER, {'operations': [row(0)]})
    assert error.value.code == 'PM_SOURCE_READ_CHANGED'
    assert lineage.read_response(db, OWNER, {'operations': [row(0)]}) == {'operations': [row(0)]}


def test_report_receipts_require_the_complete_page_shape(sources):
    db, _, _ = sources
    records = [{**row(i), 'kind': 'roadmap_report_materialization',
                'materialization_id': f'pmrpt_{i:032x}', 'workspace_id': 'ws_fixture'} for i in range(65)]
    page = {'version': 1, 'workspace_id': 'ws_fixture', 'materializations': records,
            'next_after': 2**63 - 1, 'source_visibility_filtered': False}
    assert lineage.read_response(db, OWNER, page) == page
    for value in ({'materializations': records}, {'materialization': {'nested': records}},
                  {**page, 'version': True}, {**page, 'source_visibility_filtered': 'false'},
                  {**page, 'materializations': [{**r, 'kind': 'other'} for r in records]}):
        assert outcome(lineage.read_response, db, value) == ('error', LIMIT)
    oversized = {**page, 'materializations': [{**records[0], 'nested': records}]}
    assert outcome(lineage.read_response, db, oversized) == ('error', LIMIT)


def test_fixed_seed_reference_equivalence(sources, monkeypatch):
    db, hidden, roots = sources
    calls = visibility_calls(monkeypatch)
    hidden.update({oid(i) for i in range(100, 110)})
    rng = random.Random(447212)
    reference = namespace()['read_response']
    cases = {'same': 0, 'union': 0, 'inherited_host': 0, 'refusal': 0}
    for index in range(400):
        scenario = index % 4
        count = rng.randint(1, 12) if scenario != 1 else 66
        rows, expected_rows = [], []
        for i in range(count):
            identity = i if scenario == 1 else rng.choice([0, 1, 2, 100])
            own_host = rng.choice([False, True])
            item = ({'session_id': f'session-{identity}'} if scenario == 2 else row(identity))
            if own_host:
                item['host'] = 'fixture-host'
            for _ in range(rng.randrange(4)):
                item = {'member': [item], 'unicode': '來源'}
            rows.append(item)
            if oid(identity) not in hidden:
                expected_rows.append(item)
        # Guarantee an inherited-only hidden row for the third oracle branch.
        if scenario == 2:
            rows.append({'session_id': 'session-100', 'text': 'PRIVATE-SENTINEL'})
        value = {'host': 'fixture-host', 'operations': rows, 'events': [row(3)], 'count': len(rows) + 1}
        expected = {**value, 'operations': expected_rows}
        if len(rows) != len(expected_rows):
            expected.update(count=len(expected_rows) + 1, source_visibility_filtered=True)
        if scenario == 3:
            value['envelope'] = expected['envelope'] = {'operation_ids': [oid(i) for i in range(65)]} if index % 8 == 3 else row(100)
        for _ in range(rng.randrange(3)):
            value, expected = bootstrap(value), bootstrap(expected)
        roots.clear()
        old = outcome(reference, db, value)
        before_roots = tuple(roots)
        calls.clear()
        roots.clear()
        new = outcome(lineage.read_response, db, value)
        after_roots = tuple(roots)
        registered = list(page_rows(value))
        assert len(calls) <= len(registered) + 1, index
        assert all(len(ids) <= 64 for ids in calls), index
        inherited = sum(len(lineage.document_roots(db, item, inherited_host=host)
                            - lineage.document_roots(db, item)) for item, host in registered)
        if scenario in {0, 3}:
            assert inherited == 0, index
        if old[0] == 'ok':
            assert len(after_roots) <= len(before_roots) + inherited, index
        expected_bytes = json.dumps(expected, ensure_ascii=False).encode()
        if old[0] == 'ok':
            cases['same'] += 1
            assert new == old, index
        elif scenario == 1 and old == ('error', LIMIT):
            cases['union'] += 1
            assert new == ('ok', expected_bytes), index
        elif scenario == 2 and old == ('error', HIDDEN):
            cases['inherited_host'] += 1
            assert new == ('ok', expected_bytes), index
        elif scenario == 3:
            cases['refusal'] += 1
            assert new == old, index
        else:
            pytest.fail(f'Unclassified reference result at {index}: {old}')
    assert all(cases.values()), cases


def test_hidden_row_is_removed_before_whole_response_depth_validation(sources):
    db, hidden, _ = sources
    hidden.add(oid(0))
    nested = row(0)
    for _ in range(31):
        nested = {'nested': nested}
    value = {'operations': [nested], 'count': 1}
    expected = {'operations': [], 'count': 0, 'source_visibility_filtered': True}
    assert namespace()['read_response'](db, OWNER, value) == expected
    assert lineage.read_response(db, OWNER, value) == expected
