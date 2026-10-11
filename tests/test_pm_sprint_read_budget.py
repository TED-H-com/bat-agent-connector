"""Complete Sprint facts survive byte-bounded, advancing history pages."""
from __future__ import annotations

import copy
import json

import pytest

from bat_agent_connector import pm_sprints as pm
from bat_agent_connector.operations import OperationError
from tests.test_pm_sprints import OWNER, READER, active, change, create, prepared, revise, selections
from tests.test_pm_sprints import ops as ops  # noqa: F401


def wire_size(value):
    return len(json.dumps(value, ensure_ascii=False).encode('utf-8'))


def budget_for(monkeypatch, value):
    """Count metadata itself without assuming max_bytes has a fixed digit count."""
    document = copy.deepcopy(value)
    budget = 0
    while True:
        if 'coverage' in document:
            document['coverage']['max_bytes'] = budget
        size = wire_size(document)
        if size == budget:
            monkeypatch.setattr(pm, 'MAX_READ_BYTES', budget)
            return budget
        budget = size


def assert_bounded(value):
    assert wire_size(value) <= pm.MAX_READ_BYTES


async def test_sprint_current_goal_and_selection_are_complete_even_when_histories_do_not_fit(ops, monkeypatch):
    data = await prepared(ops)
    target = await create(ops, data, goal='目標' * 2000)
    await change(ops, target, 'select', items=selections(data, (OWNER.actor, OWNER.actor), how='做法' * 2000))
    complete = pm.get_sprint(ops, READER, **target)
    baseline = copy.deepcopy(complete)
    for key in ('goal_revisions', 'selection_revisions', 'history'):
        baseline[key] = []
    for kind in pm.HISTORY:
        baseline['history_next_cursors'][kind] = 0
        baseline['coverage']['histories'][kind] = {'after': 0, 'returned': 0, 'has_more': True, 'byte_limited': True}
    budget_for(monkeypatch, baseline)
    result = pm.get_sprint(ops, READER, **target)
    assert result == baseline | {'coverage': result['coverage']}
    assert result['goal'] == complete['goal']
    assert result['selected_items'] == complete['selected_items']
    assert result['sprint'] == complete['sprint']
    assert result['quality'] == 'unassessed'
    assert result['history_next_cursors'] == {'goals': 0, 'selections': 0, 'events': 0}
    assert_bounded(result)
    # A standalone history page can still retrieve records that do not fit beside
    # the current state; zero here means start at the first record, not exhaustion.
    page = pm.list_history(ops, READER, **target, kind='selections', after=0)
    assert page['records'] == complete['selection_revisions']
    assert page['next_cursor'] is None
    assert_bounded(page)


async def test_mandatory_state_overflow_errors_instead_of_hiding_current_work(ops, monkeypatch):
    data = await prepared(ops)
    target = await active(ops, data)
    before = ops.db.total_changes
    monkeypatch.setattr(pm, 'MAX_READ_BYTES', 100)
    with pytest.raises(OperationError, match='PM_SPRINT_READ_TOO_LARGE') as error:
        pm.get_sprint(ops, READER, **target)
    assert error.value.status == 413
    assert ops.db.total_changes == before


@pytest.mark.parametrize('kind', ['goals', 'selections', 'events'])
async def test_history_byte_prefixes_advance_without_loss_and_preserve_complete_records(ops, monkeypatch, kind):
    data = await prepared(ops)
    target = await create(ops, data, goal='原目標' * 1000)
    await change(ops, target, 'goal.revise', goal='新目標' * 1000)
    await change(ops, target, 'select', items=selections(data, how='原做法' * 1000))
    await change(ops, target, 'select', items=selections(data, how='新做法' * 1000))
    all_records = pm.list_history(ops, READER, **target, kind=kind)['records']
    single_pages = [pm.list_history(ops, READER, **target, kind=kind, after=0 if index == 0 else all_records[index - 1]['sequence'], limit=1)
                    for index in range(len(all_records))]
    # The largest whole record fits; at least the first two cannot fit together.
    monkeypatch.setattr(pm, 'MAX_READ_BYTES', max(map(wire_size, single_pages)) + 64)
    after, records, pages = 0, [], []
    while True:
        page = pm.list_history(ops, READER, **target, kind=kind, after=after)
        assert_bounded(page)
        assert page['records']
        records.extend(page['records'])
        pages.append(page)
        cursor = page['next_cursor']
        if cursor is None:
            break
        assert cursor > after
        assert page['coverage']['has_more']
        after = cursor
    assert records == all_records
    assert any(page['coverage']['byte_limited'] for page in pages)
    assert pages[-1]['coverage']['has_more'] is False
    assert pages[-1]['coverage']['byte_limited'] is False


async def test_history_count_limit_is_distinct_from_byte_limit(ops):
    data = await prepared(ops)
    target = await active(ops, data)
    result = pm.list_history(ops, READER, **target, kind='events', limit=1)
    assert result['coverage'] == {'max_bytes': pm.MAX_READ_BYTES, 'after': 0, 'returned': 1,
                                  'has_more': True, 'byte_limited': False}
    assert result['next_cursor'] == result['records'][0]['sequence']
    empty = pm.list_history(ops, READER, **target, kind='events', after=10**9)
    assert empty['records'] == [] and empty['next_cursor'] is None
    assert empty['coverage']['returned'] == 0 and not empty['coverage']['has_more']
    assert not empty['coverage']['byte_limited']


@pytest.mark.parametrize('surface', ['revisions', 'sprints', 'history'])
async def test_single_oversized_list_record_is_explicit_error_not_stalled_cursor(ops, monkeypatch, surface):
    data = await prepared(ops)
    target = await active(ops, data)
    monkeypatch.setattr(pm, 'MAX_READ_BYTES', 600)
    readers = {
        'revisions': lambda: pm.list_revisions(ops, READER, data['workspace_id'], 'dod'),
        'sprints': lambda: pm.list_sprints(ops, READER, data['workspace_id']),
        'history': lambda: pm.list_history(ops, READER, **target, kind='selections'),
    }
    with pytest.raises(OperationError, match='PM_SPRINT_READ_TOO_LARGE'):
        readers[surface]()


@pytest.mark.parametrize('surface', ['revisions', 'sprints'])
async def test_artifact_and_sprint_lists_are_byte_bounded_complete_prefixes(ops, monkeypatch, surface):
    data = await prepared(ops)
    await create(ops, data, capacity_note='第一輪' * 1000)
    await create(ops, data, capacity_note='第二輪' * 1000)
    await revise(ops, data['workspace_id'], 'product_goal', plan_revision_id=data['plan'], text='產品目標' * 1000)
    key = 'revisions' if surface == 'revisions' else 'sprints'
    read = (lambda **kw: pm.list_revisions(ops, READER, data['workspace_id'], 'product_goal', **kw)) if surface == 'revisions' else (lambda **kw: pm.list_sprints(ops, READER, data['workspace_id'], **kw))
    complete = read()[key]
    pages = [read(limit=1), read(after=complete[0]['sequence'], limit=1)]
    monkeypatch.setattr(pm, 'MAX_READ_BYTES', max(map(wire_size, pages)) + 64)
    first = read()
    second = read(after=first['next_cursor'])
    assert first['coverage']['byte_limited'] and first['next_cursor'] > 0
    assert first[key] + second[key] == complete
    assert second['next_cursor'] is None
    assert_bounded(first)
    assert_bounded(second)


@pytest.mark.parametrize('surface', ['artifacts', 'artifact'])
async def test_current_artifacts_are_whole_or_explicitly_too_large(ops, monkeypatch, surface):
    data = await prepared(ops)
    read = (lambda: pm.get_artifacts(ops, READER, data['workspace_id'])) if surface == 'artifacts' else (lambda: pm.get_artifact(ops, READER, data['workspace_id'], 'dod', data['dod']))
    complete = read()
    monkeypatch.setattr(pm, 'MAX_READ_BYTES', wire_size(complete))
    assert read() == complete
    monkeypatch.setattr(pm, 'MAX_READ_BYTES', wire_size(complete) - 1)
    with pytest.raises(OperationError, match='PM_SPRINT_READ_TOO_LARGE'):
        read()


async def test_utf8_and_json_escaping_are_counted_with_actual_http_separators(ops, monkeypatch):
    data = await prepared(ops)
    await revise(ops, data['workspace_id'], 'product_goal', plan_revision_id=data['plan'], text='中文字\n"quoted"\\path' * 100)
    complete = pm.list_revisions(ops, READER, data['workspace_id'], 'product_goal')
    budget = budget_for(monkeypatch, complete)
    exact = pm.list_revisions(ops, READER, data['workspace_id'], 'product_goal')
    assert wire_size(exact) == budget
    assert exact['coverage']['has_more'] is False
    monkeypatch.setattr(pm, 'MAX_READ_BYTES', budget - 1)
    prefix = pm.list_revisions(ops, READER, data['workspace_id'], 'product_goal')
    assert len(prefix['revisions']) == 1
    assert prefix['coverage']['byte_limited'] is True
    assert_bounded(prefix)


async def test_full_history_rows_are_loaded_only_until_the_first_nonfitting_record(ops, monkeypatch):
    data = await prepared(ops)
    target = await create(ops, data, goal='目標' * 1000)
    for index in range(5):
        await change(ops, target, 'goal.revise', goal=f'{index}: ' + '目標' * 1000)
    one = pm.list_history(ops, READER, **target, kind='goals', limit=1)
    monkeypatch.setattr(pm, 'MAX_READ_BYTES', wire_size(one) + 64)
    loaded = []
    original = pm._history_record

    def load(db, kind, sequence):
        loaded.append(sequence)
        return original(db, kind, sequence)

    monkeypatch.setattr(pm, '_history_record', load)
    result = pm.list_history(ops, READER, **target, kind='goals')
    assert len(result['records']) == 1 and len(loaded) == 2
    assert result['next_cursor'] == loaded[0]
    assert result['coverage']['byte_limited'] is True


async def test_default_cap_bounds_multiple_megabytes_of_valid_dod_history(ops):
    data = await prepared(ops)
    for revision in range(4):
        await revise(ops, data['workspace_id'], 'dod', plan_revision_id=data['plan'],
                     criteria=[{'id': f'quality.{index}', 'text': str(revision) + '驗' * 3999} for index in range(50)])
    first = pm.list_revisions(ops, READER, data['workspace_id'], 'dod')
    assert_bounded(first)
    assert first['coverage']['byte_limited'] and first['next_cursor'] is not None
    second = pm.list_revisions(ops, READER, data['workspace_id'], 'dod', after=first['next_cursor'])
    assert_bounded(second)
    assert second['next_cursor'] is None
    all_records = first['revisions'] + second['revisions']
    assert len(all_records) == 5
    assert wire_size(all_records) > 2_000_000
    for revision, record in enumerate(all_records[1:]):
        assert record['document']['criteria'] == [{'id': f'quality.{index}', 'text': str(revision) + '驗' * 3999} for index in range(50)]
