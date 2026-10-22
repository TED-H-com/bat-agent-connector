"""Read-only snapshots shared by source and packaged continuity fixtures."""
from __future__ import annotations

import asyncio
import hashlib
import re
from pathlib import Path
from urllib.parse import unquote


def _identifier(name):
    return '"' + name.replace('"', '""') + '"'


def snapshot_rows(db, table, *, where='', args=()):
    """Keep the producer's column names and values, including across JSON transport."""
    cursor = db.execute(f'SELECT * FROM {_identifier(table)} {where} ORDER BY rowid', args)
    return {'columns': [column[0] for column in cursor.description], 'rows': [list(row) for row in cursor]}


def assert_rows_preserved(db, table, saved, *, where='', args=(), allow_append=False):
    """Old columns are exact; added columns retain their declared defaults on old rows."""
    columns, expected = saved['columns'], saved['rows']
    current = db.execute(f'PRAGMA table_info({_identifier(table)})').fetchall()
    missing = set(columns) - {column[1] for column in current}
    assert not missing, f'{table}: missing preserved columns {sorted(missing)}'
    selection = ','.join(_identifier(column) for column in columns)
    actual = [list(row) for row in db.execute(
        f'SELECT {selection} FROM {_identifier(table)} {where} ORDER BY rowid', args)]
    assert actual[:len(expected)] == expected, f'{table}: preserved values changed'
    if not allow_append:
        assert len(actual) == len(expected), f'{table}: row count changed'
    for column in current:
        if column[1] in columns:
            continue
        default = column[4] if column[4] is not None else 'NULL'
        # SQLite applies the column's storage affinity to its declared default.
        # BINARY prevents a column collation from accepting a changed text value.
        matches = [row[0] for row in db.execute(
            f'SELECT ({_identifier(column[1])} COLLATE BINARY) IS ({default}) '
            f'FROM {_identifier(table)} {where} ORDER BY rowid', args)]
        assert all(matches[:len(expected)]), f'{table}.{column[1]}: added column differs from its declared default'


def checkout_files(directory):
    return {path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in directory.rglob('*') if path.is_file()}


async def crash_after_201(w, monkeypatch, envelope, principal):
    """Use the loaded version's producer; only lose outer completion."""
    from bat_agent_connector import pr_publication

    ops, original = w['d'].ops, w['d'].ops._step_done

    def lost(oid, name, response, **kwargs):
        if name == pr_publication.POST:
            raise asyncio.CancelledError('Fixture lost outer completion after durable 201 receipt')
        return original(oid, name, response, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(ops, '_step_done', lost)
        operation = ops.create(principal, **envelope)[0]
        cancelled = False
        try:
            await ops._execute(operation['operation_id'])
        except asyncio.CancelledError:
            cancelled = True
        state = ops.get(operation['operation_id'])
        assert cancelled, (state['status'], state['error_code'], state['status_reason'])
    assert state['status'] == 'running'
    receipt = pr_publication._raw_receipt(ops, operation['operation_id'])
    assert receipt['http_status'] == 201 and receipt['native_pr_id'] == 801 and receipt['target_verified'] is True
    assert len(w['posts']) == w['runner'].ran['publication-push'] == 1
    return envelope, operation, snapshot_rows(ops.db, 'pr_creation_receipts')


async def seed_publication(w, mock, monkeypatch, data):
    """Shared scenario; imports resolve to the selected release's source/tests.

    The packaged driver loads this stdlib-only helper by path with the original
    release first on PYTHONPATH. No current-version fixture package is mixed into
    that producer, and its receipt/operation rows are never fabricated.
    """
    from bat_agent_connector import __version__, managed_setup, platform_files, pr_publication
    from tests.test_api_v1 import http
    from tests.test_checkpoints import git
    from tests.test_integration import RaceRunner, commit
    from tests.test_pm_delivery_lineage import DELIVERER
    from tests.test_pm_work_attempt_result import (
        _enable_integrate,
        _publish_request,
        _publish_rows,
        _result,
        _set_turn_messages,
        _start_managed_attempt,
    )

    attempt = await _start_managed_attempt(w, mock, key='managed-continuity-work')
    worktree = attempt['worktree_path']
    git(worktree, 'config', 'user.name', 'fixture')
    git(worktree, 'config', 'user.email', 'fixture@example.invalid')
    candidate = commit(worktree, 'implementation.txt', 'Selected implementation\n', 'Implement fixed work')
    mock.commit_files[candidate] = [{'file': 'implementation.txt', 'status': 'A'}]
    mock.commit_diffs[(candidate, 'implementation.txt')] = git(worktree, 'show', '--format=', candidate, '--', 'implementation.txt')
    _set_turn_messages(mock, attempt['session_id'], attempt['turn_marker'], streaming=False)
    await w['d'].inventory._refresh_scheduled('h1')
    _enable_integrate(w)
    w['runner'] = RaceRunner()
    w['d'].ops.context['git_runner'] = w['runner']
    previous = w['gh'].before_request
    w['posts'] = []

    def provide(method, path, body):
        if method == 'GET' and '/git/ref/heads/' in path:
            ref = unquote(path.split('/git/ref/heads/', 1)[1])
            answer = {'ref': 'refs/heads/' + ref, 'object': {'sha': git(w['remote'], 'rev-parse', 'refs/heads/' + ref)}}
            w['gh'].script.append((method, re.escape(path) + '$', 200, {}, answer))
        elif method == 'POST' and path == '/repos/o/r/pulls':
            w['posts'].append(body)
            answer = w['gh'].add_pr(21, git(w['remote'], 'rev-parse', 'refs/heads/' + body['head']),
                head_ref=body['head'], id=801, node_id='PR_fixture_1', title=body['title'], body=body['body'], draft=body['draft'])
            answer['base'].update(ref=body['base'], sha=git(w['remote'], 'rev-parse', 'refs/heads/' + body['base']))
            w['gh'].script.append((method, re.escape(path) + '$', 201, {}, answer))
        else:
            previous(method, path, body)

    w['gh'].before_request = provide
    allowed, blocked = _publish_rows(await _result(w, attempt))
    assert not blocked and len(allowed) == 1
    envelope = _publish_request(allowed[0], title='Selected fixed implementation',
        body='Synthetic publication continuity evidence.', draft=True, key='managed-original-publication')
    envelope, operation, receipt = await crash_after_201(w, monkeypatch, envelope, DELIVERER)
    # Retain the original SSH host declaration; changing/dropping it must still
    # refuse original-location recovery rather than silently reroute effects.
    platform_files.atomic_write(data / 'config/hosts.toml', managed_setup._encode(w['raw']))
    status, work = await http(w['port'], 'GET', '/api/v1/work-items/' + attempt['work_item_id'], tok=w['owner_token'])
    assert status == 200
    result = await _result(w, attempt)
    return {'operation_id': operation['operation_id'], 'request': envelope, 'token': w['owner_token'],
        'attempt': attempt, 'receipt': receipt,
        'rows': {table: snapshot_rows(w['d'].ops.db, table) for table in ('work_items', 'work_attempts', 'session_results')},
        'work_item_view': work['work_item'], 'result_results': result['results'],
        'manual_checkout': str(w['human']), 'manual_files': checkout_files(w['human']),
        'producer_version': __version__, 'producer_root': str(Path(pr_publication.__file__).resolve().parents[1]),
        'handlers': {name: hashlib.sha256(Path(pr_publication.__file__).with_name(name + '.py').read_bytes()).hexdigest()
            for name in ('pr_publication', 'pm_work_start', 'pm_attempt_results')},
        'effects': {'create_pull': len(w['posts']), 'push': w['runner'].ran['publication-push']},
        'operation_events': snapshot_rows(w['d'].ops.db, 'api_events', where='WHERE resource_id=?', args=(operation['operation_id'],))}
