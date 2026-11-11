"""Fixed retained references; synthetic Codex/Claude producer shapes, no live paths."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from bat_agent_connector import pm_external_history as refs
from bat_agent_connector.operations import OperationError
from bat_agent_connector.session_history_config import HistoryConfig
from tests.test_api_v1 import daemon  # noqa: F401
from tests.test_external_history import AT, SESSION, append, message, read, ready  # noqa: F401
from tests.test_pm_api import OWNER

TEXT = " \tA🧪e\u0301\r\nZ "


@pytest.fixture(params=['codex', 'claude'])
async def retained(ready, request):  # noqa: F811
    d, wid, source = ready
    if request.param == 'claude':
        source = replace(source, adapter='claude-history-jsonl-v1')
        source.path.write_text('')
        d.history_collector.config = HistoryConfig(10, (source,))
        record = {'type': 'assistant', 'uuid': 'message-1', 'timestamp': AT, 'sessionId': SESSION,
            'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': TEXT},
                {'type': 'thinking', 'thinking': 'OMITTED'}, {'type': 'tool_use', 'name': 'Read', 'input': {}}]}}
    else:
        record = message(TEXT)
    append(source.path, record)
    await d.history_collector.collect_once()
    return d, wid, source, read(d, wid, source)['entries'][0], record


def preview(case, selection=None, **changes):
    d, wid, source, entry, _ = case
    params = dict(workspace_id=wid, source_id=source.source_id, entry_id=entry['entry_id'],
        expected_content_digest=entry['content_digest'], selection=selection or {'kind': 'entry'})
    return refs.preview(d.journal.db, OWNER, **(params | changes))


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
        separators=(',', ':')).encode()).hexdigest()


async def test_reference_canonical_form_and_retained_read_only_resolution(retained):
    d, wid, source, entry, original = retained
    before = d.journal.db.total_changes
    result = preview(retained)
    ref = result['ref']
    assert set(ref) == {'schema', 'server_id', 'workspace_id', 'source_id', 'entry_id', 'source_digest',
        'content_digest', 'selection', 'excerpt_digest'}
    assert ref['schema'] == 'external_history_ref/v1' and ref['workspace_id'] == wid
    # The existing reader hashes the original JSONL bytes including the newline.
    assert ref['source_digest'] == hashlib.sha256(json.dumps(original).encode() + b'\n').hexdigest()
    saved = json.loads(d.journal.db.execute('SELECT content FROM session_history_entries WHERE entry_id=?',
        (entry['entry_id'],)).fetchone()[0])
    assert ref['content_digest'] == digest({k: v for k, v in saved.items() if k != 'source_offset'})
    assert ref['excerpt_digest'] == digest({'ref': {k: v for k, v in ref.items() if k != 'excerpt_digest'},
        'content': result['content']})
    assert result['content']['parts'] == entry['parts']
    assert result['content']['parts'][0]['text'] == TEXT
    assert result['history_complete'] is False and result['execution_status'] == 'unknown'
    assert result['content']['clipped'] is entry['clipped']
    assert result['content']['omitted_parts'] == entry['omitted_parts']
    assert str(source.path) not in json.dumps(result) and SESSION not in json.dumps(result)
    source.path.unlink()  # Resolution never opens the live file or refreshes collection.
    assert refs.resolve(d.journal.db, OWNER, ref) == result
    assert d.journal.db.total_changes == before


async def test_half_open_codepoints_preserve_combining_marks_and_whitespace(retained):
    selected = {'kind': 'text', 'part_index': 0, 'start_codepoint': 3, 'end_codepoint': 6}
    result = preview(retained, selected)
    assert result['content']['parts'] == [{'kind': 'text', 'text': '🧪e\u0301'}]
    assert preview(retained, {'kind': 'text', 'part_index': 0, 'start_codepoint': 0,
        'end_codepoint': len(TEXT)})['content']['parts'][0]['text'] == TEXT
    assert result['ref']['selection'] == selected
    assert result['ref']['excerpt_digest'] != preview(retained)['ref']['excerpt_digest']


@pytest.mark.parametrize('selection', [None, {}, {'kind': 'entry', 'path': '/srv/example'},
    {'kind': 'text', 'part_index': True, 'start_codepoint': 0, 'end_codepoint': 1},
    {'kind': 'text', 'part_index': -1, 'start_codepoint': 0, 'end_codepoint': 1},
    {'kind': 'text', 'part_index': 128, 'start_codepoint': 0, 'end_codepoint': 1},
    {'kind': 'text', 'part_index': 0, 'start_codepoint': 3, 'end_codepoint': 3},
    {'kind': 'text', 'part_index': 0, 'start_codepoint': -1, 'end_codepoint': 2},
    {'kind': 'text', 'part_index': 0, 'start_codepoint': 0, 'end_codepoint': 99}])
async def test_selection_refusals(retained, selection):
    d, wid, source, entry, _ = retained
    with pytest.raises(OperationError, match='PM_HISTORY_REFERENCE_INVALID'):
        refs.preview(d.journal.db, OWNER, wid, source.source_id, entry['entry_id'], entry['content_digest'], selection)


@pytest.mark.parametrize('field', ['source_digest', 'content_digest', 'excerpt_digest', 'server_id', 'entry_id'])
async def test_resolve_compares_every_fixed_identity_and_digest(retained, field):
    result = preview(retained)
    ref = dict(result['ref'])
    ref[field] = ('0' if ref[field][0] != '0' else '1') + ref[field][1:]
    with pytest.raises(OperationError):
        refs.resolve(retained[0].journal.db, OWNER, ref)
    with pytest.raises(OperationError, match='PM_HISTORY_REFERENCE_CHANGED'):
        preview(retained, expected_content_digest='0' * 64)


async def test_same_entry_append_and_binding_changes_never_retarget_ref(retained):
    d, wid, source, _, record = retained
    result = preview(retained)
    append(source.path, record)
    await d.history_collector.collect_once()
    assert preview(retained) == result
    d.history_collector.config = HistoryConfig(10, (replace(source, path=source.path.with_suffix('.other.jsonl')),))
    with pytest.raises(OperationError, match='PM_HISTORY_SOURCE_UNAVAILABLE'):
        refs.resolve(d.journal.db, OWNER, result['ref'])


@pytest.mark.parametrize('text,valid', [('x' * 16000, True), ('x' * 16001, True), ('🧪' * 16000, False)], ids=['at-limit', 'collector-clipped', 'escaped-byte-limit'])
async def test_retained_clipping_and_escaped_response_bound(ready, text, valid):  # noqa: F811
    d, wid, source = ready
    append(source.path, message(text))
    await d.history_collector.collect_once()
    entry = read(d, wid, source)['entries'][0]
    case = (d, wid, source, entry, None)
    if not valid:
        with pytest.raises(OperationError, match='PM_HISTORY_REFERENCE_INVALID'):
            preview(case)
    else:
        result = preview(case)
        assert result['content']['parts'][0]['text'] == text[:16000]
        assert result['content']['clipped'] is (len(text) > 16000)
        assert len(json.dumps(result, ensure_ascii=True).encode()) <= 128 * 1024


@pytest.mark.parametrize('text', ['x' * 16001, '\ud800'], ids=['oversized', 'surrogate'])
async def test_invalid_retained_text_is_refused_not_shortened(retained, text):
    d, _, source, entry, _ = retained
    saved = json.loads(d.journal.db.execute('SELECT content FROM session_history_entries WHERE entry_id=?',
        (entry['entry_id'],)).fetchone()[0])
    saved['entry_id'] = 'shi_' + 'f' * 32
    saved['parts'] = [{'kind': 'text', 'text': text}]
    # Deliberately corrupt a new retained row; immutable originals are not edited.
    content_digest = '0' * 64 if text == '\ud800' else digest({k: v for k, v in saved.items() if k != 'source_offset'})
    d.journal.db.execute('INSERT INTO session_history_entries VALUES(?,?,?,?,?)',
        (2, source.source_id, saved['entry_id'], json.dumps(saved), content_digest))
    with pytest.raises(OperationError, match='PM_HISTORY_REFERENCE_INVALID'):
        preview(retained, entry_id=saved['entry_id'], expected_content_digest=content_digest)


async def test_tool_marker_can_be_referenced_but_not_selected_as_text(retained):
    d, wid, source, entry, _ = retained
    if source.adapter == 'codex-history-jsonl-v1':
        append(source.path, {'timestamp': AT, 'type': 'response_item', 'payload': {'type': 'function_call',
            'id': 'marker', 'name': 'Read', 'arguments': 'OMITTED'}})
        await d.history_collector.collect_once()
        entry = read(d, wid, source)['entries'][1]
        index = 0
    else:
        index = 1
    case = (d, wid, source, entry, None)
    assert preview(case)['content']['omitted_parts'] > 0
    with pytest.raises(OperationError, match='PM_HISTORY_REFERENCE_INVALID'):
        preview(case, {'kind': 'text', 'part_index': index, 'start_codepoint': 0, 'end_codepoint': 1})


@pytest.mark.parametrize('change', ['truncate', 'replace', 'unlink', 'conflict'])
async def test_collection_failure_refuses_previously_fixed_reference(retained, change):
    d, _, source, _, original = retained
    ref = preview(retained)['ref']
    if change == 'truncate':
        source.path.write_text('')
    elif change == 'replace':
        other = source.path.with_suffix('.new')
        other.write_bytes(source.path.read_bytes())
        other.replace(source.path)
    elif change == 'unlink':
        source.path.unlink()
    else:
        original['timestamp'] = '2026-10-18T10:00:01Z'
        append(source.path, original)
    await d.history_collector.collect_once()
    with pytest.raises(OperationError, match='PM_HISTORY_SOURCE_UNAVAILABLE'):
        refs.resolve(d.journal.db, OWNER, ref)


async def test_ref_cannot_add_live_path_or_change_schema(retained):
    ref = preview(retained)['ref']
    for changed in (ref | {'path': '/srv/example/history.jsonl'}, ref | {'schema': 'external_history_ref/v2'}):
        with pytest.raises(OperationError, match='PM_HISTORY_REFERENCE_INVALID'):
            refs.resolve(retained[0].journal.db, OWNER, changed)
