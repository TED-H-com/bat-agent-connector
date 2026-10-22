"""Source observations must never impersonate local completion or ownership."""
import pytest

from bat_agent_connector import pm_kanban
from tests.test_board_github_sync import Provider, issue, source_link, sync
from tests.test_work_items import PERSON, act, get, item, project
from tests.test_work_items import daemon as daemon


@pytest.mark.parametrize(('pr', 'reason', 'merged', 'column'), [
    (True, None, False, 'github_closed'),
    (True, None, True, 'github_done'),
    (False, 'completed', False, 'github_completed'),
    (False, 'not_planned', False, 'github_closed'),
    (False, 'duplicate', False, 'github_closed'),
    (False, None, False, 'github_closed'),
])
async def test_closure_is_source_observation_without_human_completion(daemon, pr, reason, merged, column):
    d = daemon
    ws = (await act(d, PERSON, 'pm.workspace.create', params={'name': 'Source status'}))['result']['workspace']['workspace_id']
    value = issue(1, pr=pr, state='closed', merged=merged)
    value['state_reason'] = reason
    d.ops.context['board_github_provider'] = Provider([value])
    await act(d, PERSON, 'pm.github.import', {'workspace_id': ws},
        {'repository': 'example/board', 'include_closed': True}, {'expected_revision': 0})
    card = pm_kanban.get_board(d.ops, PERSON, ws)['cards'][0]
    saved = get(d, card['work_item_id'])
    assert saved['state'] != 'done'
    assert saved['done_by'] is saved['done_at'] is None
    assert not saved['completion']['pending'] and not saved['completion']['approved']
    assert card['column'] == column
    assert saved['external']['source'] == 'github'
    assert saved['external']['state_reason'] == reason


@pytest.mark.parametrize('state', ['doing', 'waiting', 'todo'])
async def test_first_adoption_of_closed_source_preserves_human_lane(daemon, state):
    d = daemon
    pid = await project(d)
    iid = await item(d, pid, 'Human work', state=state, goal='https://github.com/example/board/pull/1')
    await source_link(d, iid, 'https://github.com/example/board/pull/1')
    d.ops.context['board_github_provider'] = Provider([issue(1, pr=True, state='closed', merged=True)])
    result = (await sync(d, pid))['result']
    assert result['created'] == 0 and result['adopted'] == 1
    saved = get(d, iid)
    assert saved['state'] == state
    assert saved['external']['board_lane'] is None
    assert saved['done_by'] is saved['done_at'] is None


async def test_draft_is_retained_as_source_status(daemon):
    d = daemon
    pid = await project(d)
    d.ops.context['board_github_provider'] = Provider([issue(1, pr=True, draft=True)])
    await sync(d, pid)
    iid = d.ops.db.execute('SELECT work_item_id FROM work_items').fetchone()[0]
    assert get(d, iid)['external']['draft'] is True


@pytest.mark.parametrize('mention', ['goal', 'request', 'acceptance', 'title', 'note', 'delivery', 'anchor'])
async def test_adoption_requires_one_explicit_primary_source_link(daemon, mention):
    d = daemon
    pid = await project(d)
    url = 'https://github.com/example/board/issues/1'
    fields = {mention: f'Blocked by {url}'} if mention in ('goal', 'request', 'acceptance', 'title') else {}
    iid = await item(d, pid, **{'title': 'Local dependency', **fields})
    if mention in ('note', 'delivery', 'anchor'):
        await act(d, PERSON, 'work_item.link', {'work_item_id': iid},
            {'kind': 'pull_request', 'ref': 'example/board#1', 'note': url + ('#issuecomment-123' if mention == 'anchor' else '')})
    d.ops.context['board_github_provider'] = Provider([issue(1)])
    result = (await sync(d, pid))['result']
    assert result['adopted'] == 0 and result['created'] == 1
    assert get(d, iid)['external'] is None


async def test_reopen_preserves_human_approval(daemon):
    d = daemon
    pid = await project(d)
    source = Provider([issue(1)])
    d.ops.context['board_github_provider'] = source
    await sync(d, pid)
    iid = d.ops.db.execute('SELECT work_item_id FROM work_items').fetchone()[0]
    await act(d, PERSON, 'work_item.approve', {'work_item_id': iid}, pre={'expected_fingerprint': get(d, iid)['completion']['fingerprint']})
    before = get(d, iid)
    source.values = [issue(1, state='closed', state_reason='completed')]
    await sync(d, pid)
    source.values = [issue(1)]
    await sync(d, pid)
    after = get(d, iid)
    assert after['completion'] == before['completion']
    assert after['state'] == 'done'


async def test_complete_traversal_marks_missing_and_reappearance_clears_it(daemon):
    d = daemon
    pid = await project(d)
    source = Provider([issue(1)])
    d.ops.context['board_github_provider'] = source
    await sync(d, pid)
    iid = d.ops.db.execute('SELECT work_item_id FROM work_items').fetchone()[0]
    source.values = []
    result = (await sync(d, pid))['result']
    assert result['status'] == 'missing'
    assert get(d, iid)['external']['availability'] == 'missing'
    assert get(d, iid)['state'] == 'todo'
    source.values = [issue(1)]
    assert (await sync(d, pid))['result']['status'] == 'synced'
    assert get(d, iid)['external']['availability'] == 'current'


async def test_deterministic_invalid_title_advances_schedule_and_is_visible(daemon):
    from bat_agent_connector import board_github
    from tests.operation_helpers import settle_operations
    d = daemon
    pid = await project(d)
    source = Provider([issue(1)])
    d.ops.context['board_github_provider'] = source
    await sync(d, pid)
    bad = issue(2)
    bad['title'] = 'invalid\x00title'
    source.values.append(bad)
    d.ops.db.execute('UPDATE board_github_sync SET next_sync=0')
    await board_github.tick(d.ops)
    await settle_operations(d.ops)
    saved = board_github.settings(d.ops.db, pid)
    assert saved['status'] == 'invalid_source'
    assert saved['next_sync'] > 0
    assert d.ops.db.execute('SELECT COUNT(*) FROM work_items').fetchone()[0] == 1
    source.values = [issue(1), issue(2)]
    d.ops.db.execute('UPDATE board_github_sync SET next_sync=1')
    await board_github.tick(d.ops)
    await settle_operations(d.ops)
    assert d.ops.db.execute('SELECT COUNT(*) FROM work_items').fetchone()[0] == 2


async def test_stable_node_identity_survives_repository_rename(daemon):
    d = daemon
    pid = await project(d)
    source = Provider([issue(1)])
    d.ops.context['board_github_provider'] = source
    await sync(d, pid)
    iid = d.ops.db.execute('SELECT work_item_id FROM work_items').fetchone()[0]
    before_key = get(d, iid)['external']['key']
    source.values[0]['html_url'] = 'https://github.com/example/renamed/issues/1'
    result = (await sync(d, pid, repository='example/renamed'))['result']
    assert result['created'] == 0
    assert get(d, iid)['external']['key'] == before_key
    assert get(d, iid)['external']['repository'] == 'example/renamed'


async def test_history_opt_in_is_not_wip_or_explicit_owner_association(daemon):
    from bat_agent_connector import pm_evidence, pm_overview
    d = daemon
    ws = (await act(d, PERSON, 'pm.workspace.create', params={'name': 'Import origin'}))['result']['workspace']['workspace_id']
    d.ops.context['board_github_provider'] = Provider([issue(n, state='closed', state_reason='completed') for n in range(1, 57)])
    first = await act(d, PERSON, 'pm.github.import', {'workspace_id': ws},
        {'repository': 'example/board'}, {'expected_revision': 0})
    pid = first['result']['project_id']
    assert first['result']['created'] == 0  # open-only by default
    second = await sync(d, pid, include_closed=True)
    assert second['result']['created'] == 56
    page = pm_evidence.list_work(d.ops, PERSON, ws)
    assert {b['association_basis'] for b in page['bindings']} == {'github_import'}
    cards = pm_kanban.get_board(d.ops, PERSON, ws)['cards']
    assert not any(c['flow']['wip'] for c in cards)
    assert pm_overview.read(d.ops, PERSON, ws)['coverage']['sources']['work']['complete']


async def test_first_failed_import_does_not_bind_project_or_enable_polling(daemon):
    from bat_agent_connector.board_github_provider import BoardGitHubError
    d = daemon
    ws = (await act(d, PERSON, 'pm.workspace.create', params={'name': 'Failure'}))['result']['workspace']['workspace_id']
    source = Provider()
    source.failure = BoardGitHubError('unreachable')
    d.ops.context['board_github_provider'] = source
    result = (await act(d, PERSON, 'pm.github.import', {'workspace_id': ws},
        {'repository': 'example/board'}, {'expected_revision': 0}))['result']
    assert result['status'] == 'unreachable'
    assert d.ops.db.execute('SELECT COUNT(*) FROM projects').fetchone()[0] == 0
    assert d.ops.db.execute('SELECT COUNT(*) FROM pm_project_bindings').fetchone()[0] == 0
    assert d.ops.db.execute('SELECT COUNT(*) FROM board_github_sync').fetchone()[0] == 0


async def test_polling_does_not_invalidate_settings_draft_or_work_start_version(daemon):
    from bat_agent_connector import board_github
    d = daemon
    pid = await project(d)
    source = Provider([issue(1, pr=True, draft=True)])
    d.ops.context['board_github_provider'] = source
    await sync(d, pid)
    iid = d.ops.db.execute('SELECT work_item_id FROM work_items').fetchone()[0]
    settings_before, item_before = board_github.settings(d.ops.db, pid), get(d, iid)
    source.values[0]['draft'] = False
    await act(d, PERSON, 'project.github.sync', {'project_id': pid}, pre={'expected_revision': settings_before['revision']})
    assert board_github.settings(d.ops.db, pid)['revision'] == settings_before['revision']
    assert get(d, iid)['version'] == item_before['version']
    assert get(d, iid)['updated_at'] == item_before['updated_at']
    await act(d, PERSON, 'project.github.configure', {'project_id': pid},
        {'repository': 'example/board', 'enabled': False}, {'expected_revision': settings_before['revision']})


@pytest.mark.parametrize(('code', 'enabled', 'status'), [('VERSION_CONFLICT', True, 'sync_failed'), ('PM_LIFECYCLE_STOPPED', False, 'lifecycle_stopped')])
async def test_scheduler_refusal_is_classified(daemon, monkeypatch, code, enabled, status):
    from bat_agent_connector import board_github
    from bat_agent_connector.operations import OperationError
    d = daemon
    pid = await project(d)
    d.ops.context['board_github_provider'] = Provider([issue(1)])
    await sync(d, pid)
    d.ops.db.execute('UPDATE board_github_sync SET next_sync=0')
    def refuse(*args, **kwargs):
        raise OperationError(code, 'Fixture admission refusal', 409)
    monkeypatch.setattr(d.ops, 'create', refuse)
    await board_github.tick(d.ops)
    settings = board_github.settings(d.ops.db, pid)
    assert settings['enabled'] is enabled
    assert settings['status'] == status
    assert settings['last_error'] == code
    assert settings['next_sync'] > 0


async def test_source_closure_override_survives_repeat_after_manual_move(daemon):
    d = daemon
    pid = await project(d)
    source = Provider([issue(1)])
    d.ops.context['board_github_provider'] = source
    await sync(d, pid)
    iid = d.ops.db.execute('SELECT work_item_id FROM work_items').fetchone()[0]
    await act(d, PERSON, 'work_item.update', {'work_item_id': iid}, {'state': 'waiting'}, {'expected_version': get(d, iid)['version']})
    source.values = [issue(1, state='closed', state_reason='completed')]
    await sync(d, pid)
    assert get(d, iid)['external']['board_lane'] == 'github_completed'
    await sync(d, pid)
    assert get(d, iid)['external']['board_lane'] == 'github_completed'
    assert get(d, iid)['state'] == 'waiting'


async def test_imported_work_with_local_activity_remains_in_overview_scan(daemon):
    from bat_agent_connector import pm_evidence
    d = daemon
    ws = (await act(d, PERSON, 'pm.workspace.create', params={'name': 'Local work'}))['result']['workspace']['workspace_id']
    d.ops.context['board_github_provider'] = Provider([issue(1)])
    await act(d, PERSON, 'pm.github.import', {'workspace_id': ws}, {'repository': 'example/board'}, {'expected_revision': 0})
    iid = d.ops.db.execute('SELECT work_item_id FROM work_items').fetchone()[0]
    assert pm_evidence.list_work(d.ops, PERSON, ws, include_automatic=False)['bindings'] == []
    await act(d, PERSON, 'work_item.update', {'work_item_id': iid}, {'state': 'doing'}, {'expected_version': get(d, iid)['version']})
    assert [b['work_item_id'] for b in pm_evidence.list_work(d.ops, PERSON, ws, include_automatic=False)['bindings']] == [iid]


async def test_local_child_refusal_rolls_back_batch_and_advances_next_intent(daemon, monkeypatch):
    from dataclasses import replace

    from bat_agent_connector import board_github
    from bat_agent_connector.operations import OperationError
    from tests.operation_helpers import settle_operations
    d = daemon
    pid = await project(d)
    source = Provider([issue(1)])
    d.ops.context['board_github_provider'] = source
    await sync(d, pid)
    before = d.ops.db.execute('SELECT pages FROM board_github_sync').fetchone()[0]
    original = d.ops.actions['work_item.create']
    async def refuse(ctx):
        raise OperationError('LOCAL_REFUSED', 'Fixture local refusal', 409)
    monkeypatch.setitem(d.ops.actions, 'work_item.create', replace(original, run=refuse))
    source.values.extend([issue(2), issue(3)])
    d.ops.db.execute('UPDATE board_github_sync SET next_sync=0')
    await board_github.tick(d.ops)
    await settle_operations(d.ops)
    settings = board_github.settings(d.ops.db, pid)
    assert settings['status'] == 'apply_failed' and settings['last_error'] == 'LOCAL_REFUSED'
    assert settings['next_sync'] > 0
    assert d.ops.db.execute('SELECT COUNT(*) FROM work_items').fetchone()[0] == 1
    assert d.ops.db.execute('SELECT pages FROM board_github_sync').fetchone()[0] == before
    monkeypatch.setitem(d.ops.actions, 'work_item.create', original)
    d.ops.db.execute('UPDATE board_github_sync SET next_sync=1')
    await board_github.tick(d.ops)
    await settle_operations(d.ops)
    assert d.ops.db.execute('SELECT COUNT(*) FROM work_items').fetchone()[0] == 3


async def test_schema_reinstall_preserves_existing_ids_keys_and_schema_version(daemon):
    from bat_agent_connector import board_github
    d = daemon
    pid = await project(d)
    d.ops.context['board_github_provider'] = Provider([issue(1)])
    await sync(d, pid)
    before = [tuple(row) for row in d.ops.db.execute('SELECT work_item_id,github_external,version FROM work_items')]
    version = d.ops.db.execute('PRAGMA user_version').fetchone()[0]
    board_github.install(d.ops)
    board_github.install(d.ops)
    assert [tuple(row) for row in d.ops.db.execute('SELECT work_item_id,github_external,version FROM work_items')] == before
    assert d.ops.db.execute('PRAGMA user_version').fetchone()[0] == version


async def test_configured_polling_before_first_success_backs_off_and_recovers(daemon):
    from bat_agent_connector import board_github
    from bat_agent_connector.board_github_provider import BoardGitHubError
    from tests.operation_helpers import settle_operations
    d = daemon
    pid = await project(d)
    await act(d, PERSON, 'project.github.configure', {'project_id': pid},
        {'repository': 'example/board', 'enabled': True}, {'expected_revision': 0})
    source = Provider([issue(1)])
    source.failure = BoardGitHubError('rate_limited', 900)
    d.ops.context['board_github_provider'] = source
    await board_github.tick(d.ops)
    await settle_operations(d.ops)
    settings = board_github.settings(d.ops.db, pid)
    assert settings['status'] == 'rate_limited'
    assert settings['next_sync'] > 0 and settings['last_sync'] is None
    source.failure = None
    d.ops.db.execute('UPDATE board_github_sync SET next_sync=1')
    await board_github.tick(d.ops)
    await settle_operations(d.ops)
    assert d.ops.db.execute('SELECT COUNT(*) FROM work_items').fetchone()[0] == 1


async def test_corrected_closed_reason_updates_existing_source_lane(daemon):
    d = daemon
    pid = await project(d)
    source = Provider([issue(1)])
    d.ops.context['board_github_provider'] = source
    await sync(d, pid)
    iid = d.ops.db.execute('SELECT work_item_id FROM work_items').fetchone()[0]
    await act(d, PERSON, 'work_item.update', {'work_item_id': iid}, {'state': 'waiting'}, {'expected_version': get(d, iid)['version']})
    source.values = [issue(1, state='closed', state_reason='not_planned')]
    await sync(d, pid)
    assert get(d, iid)['external']['board_lane'] == 'github_closed'
    source.values = [issue(1, state='closed', state_reason='completed')]
    await sync(d, pid)
    assert get(d, iid)['external']['board_lane'] == 'github_completed'
    assert get(d, iid)['state'] == 'waiting' and get(d, iid)['done_by'] is None
