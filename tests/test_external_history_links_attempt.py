"""Existing Attempt reads remain authoritative; history relations change no execution."""
from dataclasses import replace

import pytest

from bat_agent_connector import pm_evidence, pm_reads, session_history, work_items
from bat_agent_connector import pm_external_history as refs
from bat_agent_connector.operations import OperationError
from bat_agent_connector.session_history_config import HistoryConfig, HistorySource
from tests.operation_helpers import settle_operations
from tests.test_external_history import SESSION, append, message
from tests.test_pm_work_attempt_result import (  # noqa: F401
    _start_managed_attempt,
    pm,
    ready,
    start_ready,
    world,
)
from tests.test_pm_work_start import STARTER_OWNER as OWNER
from tests.test_work_items import act


async def test_attempt_reader_match_mismatch_and_no_completion(start_ready, mock, tmp_path, monkeypatch):  # noqa: F811
    w = start_ready
    await _start_managed_attempt(w, mock)
    d, wid, target = w['d'], w['workspace_id'], w['target']
    attempt = pm_reads.read(d.ops, OWNER, 'pm_work_attempts_list', target)['attempts'][0]
    source = HistorySource('example', wid, 'codex-history-jsonl-v1', tmp_path / 'history.jsonl', SESSION, (OWNER.actor,))
    append(source.path, {'type': 'session_meta', 'payload': {'id': SESSION, 'cli_version': '0.160.0'}}, message('Retained result'))
    d.history_collector.config = HistoryConfig(10, (source,))
    monkeypatch.setattr(d.journal, 'owner_valid', lambda: True)
    await d.history_collector.collect_once()
    entry = session_history.read(d.ops, OWNER, wid, source.source_id)['entries'][0]
    ref = refs.preview(d.ops.db, OWNER, wid, source.source_id, entry['entry_id'], entry['content_digest'], {'kind': 'entry'})['ref']
    def request(wi, key):
        return {'action': 'pm.history.link', 'target': {'workspace_id': wid, 'work_item_id': wi},
            'params': {'fixed_ref': ref, 'attempt_id': attempt['attempt_id']}, 'idempotency_key': key,
            'preconditions': {'expected_work_version': work_items._get_item(d.ops.db, wi)['version'],
                'expected_binding_id': pm_evidence._binding(d.ops.db, wid, wi)['binding_id']}}
    def state():
        return (pm_reads.read(d.ops, OWNER, 'pm_work_attempt_get', {'workspace_id': wid, 'attempt_id': attempt['attempt_id']}),
            work_items.work_item_get(d.ops.db, target['work_item_id']),
            pm_reads.read(d.ops, OWNER, 'pm_overview_get', {'workspace_id': wid})['progress'])
    before = state()
    op, _ = d.ops.create(OWNER, **request(target['work_item_id'], 'attach-attempt'))
    await settle_operations(d.ops)
    result = d.ops.get(op['operation_id'], principal=OWNER)
    assert result['status'] == 'succeeded', result
    link = pm_reads.read(d.ops, OWNER, 'pm_history_link_get', {'workspace_id': wid, 'link_id': result['result']['link_id']})['link']
    assert link['attempt_id'] == attempt['attempt_id']
    assert state() == before
    pid = work_items._get_item(d.ops.db, target['work_item_id'])['project_id']
    other = (await act(d, OWNER, 'work_item.create', {'project_id': pid}, {'title': 'Other Work'}))['result']['work_item_id']
    await act(d, OWNER, 'pm.work.bind', {'workspace_id': wid, 'work_item_id': other}, {'rationale': 'Explicit'},
        {'expected_work_version': 1, 'expected_binding_id': None})
    with pytest.raises(OperationError, match='PM_HISTORY_ATTEMPT_MISMATCH') as exc:
        d.ops.create(OWNER, **request(other, 'wrong-attempt'))
    assert exc.value.status == 409
    invalid = request(other, 'hidden-attempt')
    invalid['params']['attempt_id'] = 'att_' + 'f' * 32
    with pytest.raises(OperationError, match='PM_HISTORY_SOURCE_UNAVAILABLE') as exc:
        d.ops.create(OWNER, **invalid)
    assert exc.value.status == 404
    d.history_collector.config = HistoryConfig(10, (replace(source, readers=()),))
    with pytest.raises(OperationError):
        d.ops.get(op['operation_id'], principal=OWNER)
