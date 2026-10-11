"""Kanban byte limits account for the actual HTTP JSON format."""
from types import SimpleNamespace

import pytest

from bat_agent_connector import pm_evidence
from bat_agent_connector import pm_kanban as pm
from bat_agent_connector.operations import OperationError
from tests.test_pm_kanban import OUTSIDER, READER, bind_work, board, card, item, prepared, update
from tests.test_pm_kanban import ops as ops  # noqa: F401
from tests.test_pm_sprint_read_budget import wire_size


def freeze(monkeypatch):
    monkeypatch.setattr(pm, 'time', SimpleNamespace(time=lambda: 2_000_000_000.0))


@pytest.mark.parametrize('surface', ['card', 'board'])
async def test_actual_wire_limit_rejects_payload_that_only_compact_json_would_fit(ops, monkeypatch, surface):
    target = await prepared(ops)
    await update(ops, target, title='中文 "quoted" \\ path' * 5)
    freeze(monkeypatch)
    read = (lambda: pm.get_card(ops, READER, **target)) if surface == 'card' else (lambda: board(ops, target))
    original = read()
    compact = len(pm_evidence._json(original).encode('utf-8'))
    assert compact < wire_size(original)
    monkeypatch.setattr(pm, 'MAX_BOARD_BYTES', wire_size(original))
    assert read() == original
    before = ops.db.total_changes
    monkeypatch.setattr(pm, 'MAX_BOARD_BYTES', compact)
    with pytest.raises(OperationError, match='PM_KANBAN_TOO_LARGE') as error:
        read()
    assert error.value.status == 413 and ops.db.total_changes == before


async def test_board_separators_force_whole_card_prefix_with_correct_wip_and_continuation(ops, monkeypatch):
    target = await prepared(ops)
    pid = card(ops, target)['work']['project_id']
    second = {**target, 'work_item_id': await item(ops, pid, '第二份完整工作')}
    await bind_work(ops, second)
    await update(ops, target, state='doing')
    await update(ops, second, state='doing')
    freeze(monkeypatch)
    full = board(ops, target)
    bound = len(pm_evidence._json(full).encode('utf-8'))
    assert bound < wire_size(full)
    monkeypatch.setattr(pm, 'MAX_BOARD_BYTES', bound)
    first = board(ops, target)
    assert wire_size(first) <= bound
    assert first['cards'] == full['cards'][:1]
    assert first['coverage']['byte_limited'] and first['wip']['count'] == first['wip']['cards'] == 1
    assert first['next_cursor'] == full['cards'][0]['binding']['sequence']
    last = board(ops, target, after=first['next_cursor'])
    assert wire_size(last) <= bound
    assert last['cards'] == full['cards'][1:] and last['next_cursor'] is None
    assert last['wip']['count'] == 1


async def test_oversized_middle_card_cannot_be_skipped_to_smaller_later_work(ops, monkeypatch):
    target = await prepared(ops)
    pid = card(ops, target)['work']['project_id']
    middle = {**target, 'work_item_id': await item(ops, pid, '大' * 120)}
    await bind_work(ops, middle)
    last = {**target, 'work_item_id': await item(ops, pid, 'Small later work')}
    await bind_work(ops, last)
    freeze(monkeypatch)
    bound = wire_size(board(ops, target, limit=1)) + 64
    monkeypatch.setattr(pm, 'MAX_BOARD_BYTES', bound)
    first = board(ops, target)
    assert [value['work_item_id'] for value in first['cards']] == [target['work_item_id']]
    assert wire_size(first) <= bound
    with pytest.raises(OperationError, match='PM_KANBAN_TOO_LARGE') as error:
        board(ops, target, after=first['next_cursor'])
    assert error.value.status == 413


async def test_current_membership_is_checked_before_card_size_error(ops, monkeypatch):
    target = await prepared(ops)
    monkeypatch.setattr(pm, 'MAX_BOARD_BYTES', 1)
    for read in (lambda: pm.get_card(ops, OUTSIDER, **target), lambda: pm.get_board(ops, OUTSIDER, target['workspace_id'])):
        with pytest.raises(OperationError) as error:
            read()
        assert error.value.status == 404
