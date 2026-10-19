"""GitHub board mapping, legacy adoption, durable API and background authorization."""
import asyncio
import uuid

import pytest

from bat_agent_connector import api_auth, board_github, pm_evidence, pm_kanban
from bat_agent_connector.board_github_provider import BoardGitHubError, normalize
from bat_agent_connector.operations import OperationError
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import http, token, write_frames
from tests.test_work_items import PERSON, VIEWER, act, get, item, project
from tests.test_work_items import daemon as daemon
from tests.test_work_items import served as served


def issue(number, *, pr=False, state='open', merged=False, draft=False):
    value = {'number': number, 'node_id': f'I_node_{number}', 'title': f'GitHub #{number}', 'state': state,
        'html_url': f'https://github.com/example/board/{"pull" if pr else "issues"}/{number}'}
    if pr:
        value['pull_request'] = {'merged_at': '2026-01-01T00:00:00Z' if merged else None}
        value['draft'] = draft
    return value


class Provider:
    def __init__(self, values=()):
        self.values, self.calls, self.failure = list(values), [], None

    async def collect(self, repo, cached):
        self.calls.append((repo, cached))
        if self.failure:
            raise self.failure
        return {'items': [normalize(repo, value) for value in self.values],
            'pages': {'1': {'etag': '"fixture"', 'items': [], 'more': False}}, 'authenticated': False}


async def sync(d, pid, **params):
    return await act(d, PERSON, 'project.github.import', {'project_id': pid},
        {'repository': 'example/board', **params}, {'expected_revision': board_github.settings(d.ops.db, pid)['revision']})


@pytest.mark.parametrize(('pr', 'state', 'merged', 'draft', 'lane'), [
    (False, 'open', False, False, 'todo'), (True, 'open', False, False, 'doing'),
    (True, 'open', False, True, 'doing'), (False, 'closed', False, False, 'done'),
    (True, 'closed', False, False, 'done'), (True, 'closed', True, False, 'done'),
])
def test_mapping(pr, state, merged, draft, lane):
    assert normalize('example/board', issue(1, pr=pr, state=state, merged=merged, draft=draft))['lane'] == lane


async def test_adopts_96_seeded_snapshot_cards_and_import_twice_creates_nothing(daemon):
    d = daemon
    pid = await project(d, repositories=['example/board'])
    source = Provider([issue(n, pr=n > 70, draft=n % 3 == 0) for n in range(1, 97)])
    d.ops.context['board_github_provider'] = source
    original = []
    for value in source.values:
        n, url = value['number'], value['html_url']
        # The snapshot used the ordinary project/item API, URL in descriptions
        # or in the note of the established pull_request link, without new keys.
        iid = await item(d, pid, f'#{n} Snapshot title', state='doing' if n > 70 else 'todo',
            goal='' if n % 2 else f'Imported from GitHub: {url}', request=f'Original local notes {n}' if n % 2 == 0 else f'[GitHub]({url})')
        if n > 70:
            await act(d, PERSON, 'work_item.link', {'work_item_id': iid},
                {'kind': 'pull_request', 'ref': f'example/board#{n}', 'note': url})
        original.append(get(d, iid))
    first = await sync(d, pid)
    assert first['result']['created'] == 0 and first['result']['adopted'] == 96
    versions = [get(d, row['work_item_id'])['version'] for row in original]
    again = await sync(d, pid)
    assert again['result']['created'] == again['result']['adopted'] == again['result']['updated'] == 0
    assert d.ops.db.execute('SELECT COUNT(*) FROM work_items').fetchone()[0] == 96
    for before, version in zip(original, versions):
        after = get(d, before['work_item_id'])
        assert all(after[key] == before[key] for key in ('work_item_id', 'goal', 'title', 'request', 'state', 'created_at'))
        assert after['version'] == version
        assert after['external']['key'] == f'example/board#{after["external"]["number"]}:{after["external"]["node_id"]}'
    assert board_github.settings(d.ops.db, pid)['enabled'] is True
    # Exact original operation recovery does not read GitHub again.
    saved = first['result']
    assert saved['github_sync']['revision'] == 1


async def test_preserves_manual_moves_edits_archives_and_closes_only_on_transition(daemon):
    d = daemon
    pid = await project(d)
    source = Provider([issue(1)])
    d.ops.context['board_github_provider'] = source
    await sync(d, pid)
    iid = d.ops.db.execute('SELECT work_item_id FROM work_items').fetchone()[0]
    async def move(state):
        await act(d, PERSON, 'work_item.update', {'work_item_id': iid}, {'state': state, 'title': 'My edited title'},
            {'expected_version': get(d, iid)['version']})
    await move('waiting')
    await move('todo')  # returning to the imported lane still counts as manual
    source.values[0] = issue(1, pr=True)
    await sync(d, pid)
    assert get(d, iid)['state'] == 'todo'
    source.values[0] = issue(1, pr=True, state='closed', merged=True)
    await sync(d, pid)
    assert get(d, iid)['state'] == 'done'
    assert get(d, iid)['title'] == 'My edited title'
    assert get(d, iid)['completion']['approved'] is False
    await move('doing')
    await sync(d, pid)  # already closed upstream: don't undo a later human move
    assert get(d, iid)['state'] == 'doing'
    source.values[0] = issue(1, pr=True)
    await sync(d, pid)
    assert get(d, iid)['state'] == 'doing'
    await act(d, PERSON, 'work_item.update', {'work_item_id': iid}, {'archived': True}, {'expected_version': get(d, iid)['version']})
    source.values[0] = issue(1, pr=True, state='closed')
    await sync(d, pid)
    assert get(d, iid)['archived'] and get(d, iid)['state'] == 'doing'
    source.values = []
    await sync(d, pid)
    assert d.ops.db.execute('SELECT COUNT(*) FROM work_items').fetchone()[0] == 1


async def test_legacy_manual_move_and_ambiguous_identity_are_preserved(daemon):
    d = daemon
    pid = await project(d)
    a = await item(d, pid, 'Local', goal='https://github.com/example/board/issues/1', state='waiting')
    for _ in range(2):
        await item(d, pid, 'Duplicate legacy', goal='https://github.com/example/board/issues/2')
    d.ops.context['board_github_provider'] = Provider([issue(1), issue(2)])
    result = (await sync(d, pid))['result']
    assert result['created'] == 0 and result['adopted'] == 1 and result['conflicts'] == 1
    assert get(d, a)['state'] == 'waiting'
    d.ops.context['board_github_provider'].values[0]['node_id'] = 'I_replaced'
    assert (await sync(d, pid))['result']['conflicts'] == 2
    assert get(d, a)['external']['node_id'] == 'I_node_1'


async def test_rate_limit_defers_manual_and_scheduled_reads_and_reuses_cache(daemon):
    d = daemon
    pid = await project(d)
    source = Provider([issue(1)])
    d.ops.context['board_github_provider'] = source
    await sync(d, pid)
    source.failure = BoardGitHubError('rate_limited', 1800)
    result = (await sync(d, pid))['result']
    assert result['status'] == 'rate_limited'
    assert source.calls[-1][1]['1']['etag'] == '"fixture"'
    assert result['github_sync']['next_sync'] - result['github_sync']['last_sync'] >= 1800
    assert (await sync(d, pid))['result']['deferred'] is True
    await board_github.tick(d.ops)
    assert len(source.calls) == 2
    assert d.ops.db.execute('SELECT COUNT(*) FROM work_items').fetchone()[0] == 1


async def test_background_polling_after_restart_disable_and_revocation(daemon):
    d = daemon
    pid = await project(d)
    d.ops.context['board_github_provider'] = Provider([issue(1)])
    auth = token(d, 'sync-user', 'observe', 'manage')
    principal = api_auth.authenticate(d.ops.db, auth, d._admin_token)
    await act(d, principal, 'project.github.import', {'project_id': pid}, {'repository': 'example/board'}, {'expected_revision': 0})
    d.ops.db.execute('UPDATE board_github_sync SET next_sync=0')
    board_github.install(d.ops)  # additive schema and durable settings survive reinitialization
    d.ops.context['board_github_provider'] = Provider([issue(1), issue(2)])
    await board_github.tick(d.ops)
    await settle_operations(d.ops)
    assert d.ops.db.execute('SELECT COUNT(*) FROM work_items').fetchone()[0] == 2
    api_auth.revoke(d.ops.db, principal.actor)
    d.ops.db.execute('UPDATE board_github_sync SET next_sync=0')
    await board_github.tick(d.ops)
    assert board_github.settings(d.ops.db, pid)['enabled'] is False
    assert board_github.settings(d.ops.db, pid)['status'] == 'authorization_required'


async def test_workspace_http_import_links_board_and_exposes_same_project_api(served):
    d, port = served
    auth = token(d, PERSON.actor, 'observe', 'manage', 'approve')
    ws = (await act(d, PERSON, 'pm.workspace.create', params={'name': 'Example board', 'repositories': ['git@github.com:example/board.git']}))['result']['workspace']['workspace_id']
    d.ops.context['board_github_provider'] = Provider([issue(1), issue(2, pr=True, draft=True), issue(3, pr=True, state='closed', merged=True)])
    status, data = await http(port, 'GET', f'/api/v1/pm/workspaces/{ws}/projects', tok=auth)
    assert status == 200 and data['github_sync']['suggested_repository'] == 'example/board'
    body = {'action': 'pm.github.import', 'target': {'workspace_id': ws},
        'params': {'repository': 'example/board', 'project_id': None}, 'preconditions': {'expected_revision': 0}}
    key = str(uuid.uuid4())
    status, response = await http(port, 'POST', '/api/v1/operations', tok=auth, body=body, headers={'Idempotency-Key': key})
    assert status == 202, response
    await settle_operations(d.ops)
    op = d.ops.get(response['operation']['operation_id'])
    assert op['status'] == 'succeeded', op
    pid = op['result']['project_id']
    cards = pm_kanban.get_board(d.ops, PERSON, ws)['cards']
    assert {card['column'] for card in cards} == {'backlog', 'in_progress', 'github_done'}
    assert not any(card['completion']['approved'] for card in cards)
    assert len(pm_evidence.list_work(d.ops, PERSON, ws)['bindings']) == 3
    status, detail = await http(port, 'GET', f'/api/v1/projects/{pid}', tok=auth)
    assert status == 200 and detail['github_sync']['enabled'] is True
    assert len(detail['work_items']) == 3 and detail['work_items'][0]['external']['node_id']
    status, replay = await http(port, 'POST', '/api/v1/operations', tok=auth, body=body, headers={'Idempotency-Key': key})
    assert status in (200, 202) and replay['operation']['operation_id'] == op['operation_id']
    assert len(d.ops.context['board_github_provider'].calls) == 1
    # Existing project import, another fresh key, never duplicates board bindings.
    body['params']['project_id'] = pid
    body['preconditions']['expected_revision'] = 1
    status, response = await http(port, 'POST', '/api/v1/operations', tok=auth, body=body, headers={'Idempotency-Key': str(uuid.uuid4())})
    assert status == 202, response
    await settle_operations(d.ops)
    assert len(pm_evidence.list_work(d.ops, PERSON, ws)['bindings']) == 3
    observer = token(d, VIEWER.actor, 'observe')
    status, _ = await http(port, 'POST', '/api/v1/operations', tok=observer, body=body, headers={'Idempotency-Key': str(uuid.uuid4())})
    assert status == 403
    outsider = token(d, 'outside', 'observe', 'manage')
    status, _ = await http(port, 'POST', '/api/v1/operations', tok=outsider, body=body, headers={'Idempotency-Key': str(uuid.uuid4())})
    assert status == 404
    with pytest.raises(OperationError):
        d.ops.create(PERSON, action='project.github.sync', target={'project_id': pid}, params={},
            preconditions={'expected_revision': 0}, idempotency_key=str(uuid.uuid4()))


@pytest.mark.parametrize('value', ['git@github.com:Example/Board.git', 'https://github.com/Example/Board.git', 'ssh://git@github.com/Example/Board.git', 'Example/Board'])
def test_remote_detection(value):
    assert board_github.suggestion([value]) == 'example/board'
    assert board_github.suggestion([value, 'another/repo']) is None


async def test_http_rate_limit_and_toggle_keep_existing_cards(served):
    d, port = served
    pid = await project(d)
    source = Provider([issue(1)])
    d.ops.context['board_github_provider'] = source
    await sync(d, pid)
    auth = token(d, PERSON.actor, 'observe', 'manage')
    source.failure = BoardGitHubError('rate_limited', 900)
    body = {'action': 'project.github.sync', 'target': {'project_id': pid}, 'params': {},
        'preconditions': {'expected_revision': 1}}
    status, response = await http(port, 'POST', '/api/v1/operations', tok=auth, body=body, headers={'Idempotency-Key': str(uuid.uuid4())})
    assert status == 202
    await settle_operations(d.ops)
    assert d.ops.get(response['operation']['operation_id'])['result']['status'] == 'rate_limited'
    status, detail = await http(port, 'GET', f'/api/v1/projects/{pid}', tok=auth)
    assert status == 200 and detail['github_sync']['status'] == 'rate_limited'
    assert len(detail['work_items']) == 1
    body.update(action='project.github.configure', params={'repository': 'example/board', 'enabled': False}, preconditions={'expected_revision': 2})
    status, _ = await http(port, 'POST', '/api/v1/operations', tok=auth, body=body, headers={'Idempotency-Key': str(uuid.uuid4())})
    assert status == 202
    await settle_operations(d.ops)
    assert board_github.settings(d.ops.db, pid)['enabled'] is False
    d.ops.db.execute('UPDATE board_github_sync SET next_sync=0')
    before = len(source.calls)
    await board_github.tick(d.ops)
    assert len(source.calls) == before


async def test_registered_child_receipts_local_steps_and_manual_continue(daemon):
    d = daemon
    ws = (await act(d, PERSON, 'pm.workspace.create', params={'name': 'Board'}))['result']['workspace']['workspace_id']
    source = Provider([issue(1)])
    d.ops.context['board_github_provider'] = source
    first = await act(d, PERSON, 'pm.github.import', {'workspace_id': ws},
        {'repository': 'example/board'}, {'expected_revision': 0})
    pid = first['result']['project_id']
    row = d.ops.db.execute('SELECT * FROM work_items WHERE project_id=?', (pid,)).fetchone()
    iid = row['work_item_id']
    assert d.ops.get(row['operation_id'])['action'] == 'work_item.create'
    binding = pm_evidence.list_work(d.ops, PERSON, ws)['bindings'][0]
    assert d.ops.get(binding['operation_id'])['action'] == 'pm.work.bind'
    await act(d, PERSON, 'work_item.update', {'work_item_id': iid}, {'steps': [{'text': 'Local acceptance', 'done': False}]},
        {'expected_version': get(d, iid)['version']})
    source.values = [issue(1, state='closed'), issue(2)]
    await sync(d, pid)
    closed = get(d, iid)
    assert not closed['steps'][0]['done'] and not closed['completion']['approved']
    assert next(c for c in pm_kanban.get_board(d.ops, PERSON, ws)['cards'] if c['work_item_id'] == iid)['column'] == 'github_done'
    assert (await sync(d, pid))['result']['updated'] == 0
    assert next(c for c in pm_kanban.get_board(d.ops, PERSON, ws)['cards'] if c['work_item_id'] == iid)['column'] == 'github_done'
    # Completing local work remains a separate explicit human action.
    await act(d, PERSON, 'work_item.approve', {'work_item_id': iid}, pre={'expected_fingerprint': closed['completion']['fingerprint']})
    await act(d, PERSON, 'work_item.continue', {'work_item_id': iid}, pre={'expected_fingerprint': get(d, iid)['completion']['fingerprint']})
    await sync(d, pid)
    assert get(d, iid)['state'] == 'doing'
    assert next(c for c in pm_kanban.get_board(d.ops, PERSON, ws)['cards'] if c['work_item_id'] == iid)['column'] == 'in_progress'


async def test_long_valid_repository_keeps_child_operation_keys_bounded(daemon):
    d = daemon
    pid = await project(d)
    repo = 'a' * 39 + '/' + 'b' * 100
    value = issue(1)
    value['html_url'] = f'https://github.com/{repo}/issues/1'
    d.ops.context['board_github_provider'] = Provider([value])
    result = (await sync(d, pid, repository=repo))['result']
    assert result['created'] == 1
    assert all(len(row[0]) <= 200 for row in d.ops.db.execute("SELECT idem_key FROM operations WHERE action='work_item.create'"))


async def test_new_workspace_board_imports_open_work_before_closed_history(daemon):
    d = daemon
    ws = (await act(d, PERSON, 'pm.workspace.create', params={'name': 'Open work first'}))['result']['workspace']['workspace_id']
    d.ops.context['board_github_provider'] = Provider([issue(1, state='closed'), issue(2)])
    await act(d, PERSON, 'pm.github.import', {'workspace_id': ws}, {'repository': 'example/board'}, {'expected_revision': 0})
    cards = pm_kanban.get_board(d.ops, PERSON, ws)['cards']
    assert cards[0]['work']['external']['number'] == 2 and cards[0]['column'] == 'backlog'
    assert cards[1]['work']['external']['number'] == 1 and cards[1]['column'] == 'github_done'


async def test_http_adopts_snapshot_url_and_preserves_moves_until_github_closes(served, mock):
    d, port = served
    pid = await project(d)
    iid = await item(d, pid, 'Seeded snapshot', goal='https://github.com/example/board/issues/1', state='waiting')
    source = Provider([issue(1)])
    d.ops.context['board_github_provider'] = source
    auth = token(d, PERSON.actor, 'observe', 'manage')
    async def submit(action, target, params, pre):
        status, response = await http(port, 'POST', '/api/v1/operations', tok=auth,
            body={'action': action, 'target': target, 'params': params, 'preconditions': pre},
            headers={'Idempotency-Key': str(uuid.uuid4())})
        assert status == 202, response
        await settle_operations(d.ops)
        op = d.ops.get(response['operation']['operation_id'])
        assert op['status'] == 'succeeded', op
        return op['result']
    first = await submit('project.github.import', {'project_id': pid}, {'repository': 'example/board'}, {'expected_revision': 0})
    assert first['created'] == 0 and first['adopted'] == 1
    status, detail = await http(port, 'GET', f'/api/v1/work-items/{iid}', tok=auth)
    assert status == 200 and detail['work_item']['state'] == 'waiting'
    assert detail['work_item']['external']['node_id'] == 'I_node_1'
    await submit('work_item.update', {'work_item_id': iid}, {'state': 'todo'}, {'expected_version': detail['work_item']['version']})
    await submit('project.github.sync', {'project_id': pid}, {}, {'expected_revision': 1})
    assert (await http(port, 'GET', f'/api/v1/work-items/{iid}', tok=auth))[1]['work_item']['state'] == 'todo'
    repeat = await submit('project.github.sync', {'project_id': pid}, {}, {'expected_revision': 2})
    assert repeat['created'] == repeat['adopted'] == repeat['updated'] == 0
    source.values = [issue(1, state='closed')]
    await submit('project.github.sync', {'project_id': pid}, {}, {'expected_revision': 3})
    final = (await http(port, 'GET', f'/api/v1/work-items/{iid}', tok=auth))[1]['work_item']
    assert final['state'] == 'done' and not final['completion']['approved']
    assert final['title'] == 'Seeded snapshot'
    project_view = (await http(port, 'GET', f'/api/v1/projects/{pid}', tok=auth))[1]
    assert [row['work_item_id'] for row in project_view['work_items']] == [iid]
    assert not write_frames(mock)


async def test_cancel_during_github_read_never_applies_cards_or_enables_sync(daemon):
    d = daemon
    pid = await project(d)
    class BlockedProvider(Provider):
        def __init__(self):
            super().__init__([issue(1)])
            self.entered, self.release = asyncio.Event(), asyncio.Event()

        async def collect(self, repo, cached):
            self.entered.set()
            await self.release.wait()
            return await super().collect(repo, cached)
    source = BlockedProvider()
    d.ops.context['board_github_provider'] = source
    op, _ = d.ops.create(PERSON, action='project.github.import', target={'project_id': pid},
        params={'repository': 'example/board'}, preconditions={'expected_revision': 0}, idempotency_key=str(uuid.uuid4()))
    await d.ops.run_due()
    try:
        await asyncio.wait_for(source.entered.wait(), 5)
        d.ops.cancel(PERSON, op['operation_id'])
    finally:
        source.release.set()
        await settle_operations(d.ops)
    assert d.ops.get(op['operation_id'])['status'] == 'cancelled'
    assert d.ops.db.execute('SELECT COUNT(*) FROM work_items').fetchone()[0] == 0
    assert board_github.settings(d.ops.db, pid)['revision'] == 0
