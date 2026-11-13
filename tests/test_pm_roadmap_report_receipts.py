"""Receipt response bytes include UTF-8 paths, envelopes and continuation cursors."""
import json
from types import SimpleNamespace

import pytest

from bat_agent_connector import pm_roadmap_report_materialization as reports
from bat_agent_connector.operations import OperationError
from bat_agent_connector.task_journal import Journal
from tests.test_pm_plan_operations import OWNER


@pytest.fixture
def receipts(tmp_path, monkeypatch):
    journal = Journal(tmp_path / 'receipts.db')
    reports.schema(journal)
    ops = SimpleNamespace(db=journal.db, journal=journal)
    wid, hidden, projections, examined = 'ws_' + '1' * 32, set(), {}, []
    monkeypatch.setattr(reports, '_read', lambda *args: None)
    def visible(db, principal, row):
        examined.append(row['operation_id'])
        return row['operation_id'] not in hidden
    monkeypatch.setattr(reports, 'source_visible', visible)
    # The real carrier accepts fixed ASCII paths. A synthetic projection tests
    # the read bound independently, including future non-ASCII metadata paths.
    monkeypatch.setattr(reports, '_record', lambda db, wid, mid: projections[mid])
    def add(sequence, padding=0):
        mid, oid = f'pmrpt_{sequence:032x}', f'op_{sequence:032x}'
        journal.db.execute('''INSERT INTO pm_roadmap_report_materializations
            (sequence, materialization_id, workspace_id, observation_revision_id,
             source_set_digest, view_digest, renderer_version, format, locale,
             output_digest, byte_length, git_receipt, operation_id, actor, created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (sequence, mid, wid, 'pmro_' + '2' * 32, 'a' * 64, 'b' * 64, 1,
             'markdown', 'zh-TW', 'c' * 64, 1, '{}', oid, OWNER.actor, 1))
        projections[mid] = {'kind': 'roadmap_report_materialization', 'workspace_id': wid,
            'materialization_id': mid, 'operation_id': oid,
            'git_receipt': {'file_digests': {'docs/pm/報告.md': 'a' * 64}}, 'padding': '字' * padding}
        return mid, oid
    yield ops, wid, add, hidden, projections, examined
    journal.close()


def size(value):
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8'))


def test_receipt_get_counts_the_complete_utf8_envelope(receipts, monkeypatch):
    ops, wid, add, _, projections, _ = receipts
    mid, _ = add(1, 30)
    response = reports.get_materialization(ops, OWNER, wid, mid)
    assert response['materialization']['kind'] == 'roadmap_report_materialization'
    assert '報告.md' in json.dumps(response, ensure_ascii=False)
    assert reports._read_size(response) == size(response) > size(projections[mid])
    monkeypatch.setattr(reports, 'MAX_READ_BYTES', size(response))
    assert reports.get_materialization(ops, OWNER, wid, mid) == response
    monkeypatch.setattr(reports, 'MAX_READ_BYTES', size(response) - 1)
    with pytest.raises(OperationError) as error:
        reports.get_materialization(ops, OWNER, wid, mid)
    assert (error.value.code, error.value.status) == ('PM_REPORT_MATERIALIZATION_READ_TOO_LARGE', 413)


def test_receipt_bytes_defer_a_visible_row_without_consuming_it(receipts, monkeypatch):
    ops, wid, add, _, projections, examined = receipts
    first, _ = add(1, 80)
    second, oid = add(2, 80)
    candidate = {'version': 1, 'workspace_id': wid, 'materializations': [projections[first]],
                 'next_after': reports.MAX_CURSOR, 'source_visibility_filtered': False}
    monkeypatch.setattr(reports, 'MAX_READ_BYTES', size(candidate))
    page = reports.list_materializations(ops, OWNER, wid)
    assert page['materializations'] == [projections[first]]
    assert page['next_after'] == 1 and page['source_visibility_filtered'] is False
    assert size(page) <= reports.MAX_READ_BYTES
    following = reports.list_materializations(ops, OWNER, wid, after=page['next_after'])
    assert following['materializations'] == [projections[second]]
    assert following['next_after'] is None
    assert examined.count(oid) == 2  # Deferred authorization is repeated in the new request.


def test_receipt_largest_cursor_and_envelope_are_reserved(receipts, monkeypatch):
    ops, wid, add, _, projections, _ = receipts
    first, _ = add(reports.MAX_CURSOR - 1)
    add(reports.MAX_CURSOR)
    page = reports.list_materializations(ops, OWNER, wid, limit=1)
    assert page['next_after'] == reports.MAX_CURSOR - 1
    monkeypatch.setattr(reports, 'MAX_READ_BYTES', size(page))
    assert reports.list_materializations(ops, OWNER, wid, limit=1) == page
    assert page['materializations'] == [projections[first]]
    assert reports.list_materializations(ops, OWNER, wid, after=reports.MAX_CURSOR)['next_after'] is None


def test_receipt_empty_hidden_pages_advance_to_the_terminal_marker(receipts):
    ops, wid, add, hidden, _, _ = receipts
    for sequence in (1, 2, 3):
        _, oid = add(sequence)
        hidden.add(oid)
    cursor = 0
    for expected in (1, 2, None):
        page = reports.list_materializations(ops, OWNER, wid, after=cursor, limit=1)
        assert page['materializations'] == [] and page['source_visibility_filtered'] is True
        assert page['next_after'] == expected
        cursor = expected


@pytest.mark.parametrize('singular', [False, True])
def test_receipt_oversized_first_row_refuses_only_after_source_access(receipts, singular):
    ops, wid, add, hidden, _, _ = receipts
    mid, oid = add(1, 700_000)
    def read():
        return (reports.get_materialization(ops, OWNER, wid, mid) if singular
                else reports.list_materializations(ops, OWNER, wid))
    with pytest.raises(OperationError) as error:
        read()
    assert error.value.status == 413
    assert error.value.code == 'PM_REPORT_MATERIALIZATION_READ_TOO_LARGE'
    hidden.add(oid)
    if singular:
        with pytest.raises(OperationError) as error:
            read()
        assert (error.value.code, error.value.status) == ('NOT_FOUND', 404)
    else:
        page = read()
        assert not page['materializations'] and page['next_after'] is None


def test_receipt_hidden_prefix_advances_before_oversized_visible_row(receipts):
    ops, wid, add, hidden, _, _ = receipts
    _, oid = add(1)
    hidden.add(oid)
    add(2, 700_000)
    page = reports.list_materializations(ops, OWNER, wid)
    assert page['materializations'] == [] and page['next_after'] == 1
    assert page['source_visibility_filtered'] is True
    with pytest.raises(OperationError) as error:
        reports.list_materializations(ops, OWNER, wid, after=1)
    assert error.value.status == 413
