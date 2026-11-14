"""Real read-only Fleet client, mock BAT and fake GitHub transport boundaries."""
from __future__ import annotations

import asyncio
import hashlib

import pytest

from bat_agent_connector import client as bat_client
from bat_agent_connector import errlog, work_items
from tests.test_api_v1 import daemon, served, token  # noqa: F401
from tests.test_history_continuation_access import db_snapshot, request_body, retain
from tests.test_pm_work_packages import OWNER, pm, ready, world  # noqa: F401
from tests.test_rpc_scope_order import raw_post


@pytest.fixture(autouse=True)
def diagnostic_path(tmp_path, monkeypatch):
    path = tmp_path / 'diagnostics' / 'errors.jsonl'
    monkeypatch.setenv('BAT_ERROR_LOG', str(path))
    return path


def files(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('mode,frames,status', [('busy-once', 2, 200), ('busy-exhausted', 3, 409),
    ('invoke-error', 1, 409), ('timeout', 1, 409), ('rotation', 1, 409)])
async def test_real_read_client_frames_and_diagnostics(ready, tmp_path, monkeypatch, mock, diagnostic_path, mode, frames, status):  # noqa: F811
    w = ready
    d, wid = w['d'], w['workspace_id']
    _, ref = await retain(d, wid, OWNER, tmp_path, monkeypatch)
    pid = work_items._get_item(d.ops.db, w['target']['work_item_id'])['project_id']
    body = request_body(ref, pid, w['state']['sha'])
    auth = token(d, OWNER.actor, *OWNER.scopes)
    released, completed = asyncio.Event(), asyncio.Event()
    count = 0

    def host(_):
        nonlocal count
        count += 1
        if mode == 'busy-once' and count == 2:
            return mock.ws_doc
        raise ValueError('Remote server is busy' if mode.startswith('busy') else 'Synthetic read failure')

    async def timeout(_):
        try:
            await released.wait()
            return mock.ws_doc
        finally:
            completed.set()

    async def no_git(*args, **kwargs):
        pytest.fail('Preview must not run a Git command')

    monkeypatch.setattr(d.ops.context['git_runner'], 'run', no_git)
    if mode == 'rotation':
        monkeypatch.setattr(errlog, 'LIMIT', 2048)
        diagnostic_path.parent.mkdir()
        diagnostic_path.write_bytes(b'x' * 2048)
        for index in range(1, 5):
            diagnostic_path.with_name(f'errors.jsonl.{index}').write_text(f'previous-{index}\n')
    if mode == 'timeout':
        real_timeout = bat_client.timeout_for
        monkeypatch.setattr(bat_client, 'timeout_for', lambda channel: 0.05 if channel == 'workspace:load' else real_timeout(channel))
    mock.handlers['workspace:load'] = timeout if mode == 'timeout' else host
    table_before, disk_before = db_snapshot(d.ops.db), files(tmp_path)
    sql = []
    frame_at, github_at = len(mock.invokes), len(w['gh'].requests)
    d.ops.db.set_trace_callback(sql.append)
    try:
        result_status, _ = await raw_post(w['port'], f'/api/v1/pm/workspaces/{wid}/history-continuation-preview', auth, body)
    finally:
        d.ops.db.set_trace_callback(None)
        released.set()
        if mode == 'timeout':
            await asyncio.wait_for(completed.wait(), 5)
        del mock.handlers['workspace:load']
    assert result_status == status
    # One invoke request, with the shared client's unchanged three-attempt bound.
    assert [frame['channel'] for frame in mock.invokes[frame_at:]] == ['workspace:load'] * frames
    assert len(w['gh'].requests) - github_at == (2 if status == 200 else 0)
    assert db_snapshot(d.ops.db) == table_before
    assert not any(s.lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE', 'CREATE', 'REPLACE')) for s in sql)
    after = files(tmp_path)
    changed = {p for p in after.keys() | disk_before.keys() if after.get(p) != disk_before.get(p)}
    # The logger owns the active JSONL, its companion lock, and its rotated files.
    expected = {'diagnostics/errors.jsonl', 'diagnostics/.errors.jsonl.lock'}
    if mode == 'rotation':
        expected.update(f'diagnostics/errors.jsonl.{index}' for index in range(1, 5))
        assert diagnostic_path.with_name('errors.jsonl.1').read_bytes() == b'x' * 2048
        for index in range(2, 5):
            assert diagnostic_path.with_name(f'errors.jsonl.{index}').read_text() == f'previous-{index - 1}\n'
        assert {path.name for path in diagnostic_path.parent.iterdir()} == {path.split('/')[-1] for path in expected}
    assert changed == expected
    assert diagnostic_path.is_file()
    assert not d.inventory.fleet.client('h1')._pending
