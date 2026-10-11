"""A07/A10: real task starts keep authority through awaited preparation and confinement."""
import pytest

from bat_agent_connector import confinement, registry, task_bat
from bat_agent_connector.errors import TaskControlRefused
from bat_agent_connector.task_core import TaskCoordinator
from bat_agent_connector.task_journal import Journal
from tests.conftest import controller_scope
from tests.test_confinement import MANAGED


@pytest.fixture
def journal(tmp_path, controller_context):
    value = Journal(tmp_path / 'tasks.db')
    with controller_scope(value):
        yield value
    value.close()


@pytest.mark.parametrize('boundary', ['admission', 'admission_refusal', 'carrier', 'connect', 'confinement', 'confinement_refusal'])
@pytest.mark.parametrize('control', ['pause', 'version', 'command'])
async def test_real_lead_start_refuses_changed_authority_before_transport(
        fleet_factory, mock, journal, monkeypatch, boundary, control):
    fleet = fleet_factory(writes=True, orchestrate=True, safety={'write_min_interval_s': 0}, **MANAGED)
    adapter = task_bat.BatTaskAdapter(fleet, journal=journal)
    coordinator = TaskCoordinator(journal, adapter)
    task = journal.submit(project='p', host='h1', workspace='demo-project', original_words='fixture start',
                          idempotency_key='start-guard')
    task_id = task['task_id']
    changed = False

    def change_authority():
        nonlocal changed
        if changed:
            return
        changed = True
        if control == 'command':
            command = next(c for c in journal.commands(task_id) if c['kind'] == 'start_lead')
            journal.command_status(command['command_id'], 'cancelled')
        else:
            journal.pause(task_id)
            if control == 'version':
                journal.resume(task_id)

    async def available(_task):
        return frozenset({'codex'})

    client = fleet.client('h1')
    invoke_checked = client._invoke_checked
    guard_start_frame = confinement.guard_start_frame
    start_decision = confinement.start_decision

    async def connect_after_control(channel, *args, **kwargs):
        if (boundary == 'carrier' and channel == 'worktree:create'
                or boundary == 'connect' and channel == 'claude:start-session'):
            change_authority()
        return await invoke_checked(channel, *args, **kwargs)

    async def confinement_after_control(*args, **kwargs):
        await guard_start_frame(*args, **kwargs)
        if boundary.startswith('confinement'):
            change_authority()
            if boundary == 'confinement_refusal':
                raise confinement.ConfinementRefused('HOST_ACCOUNT_UNVERIFIED', 'fixture check refused', sent=False)

    async def admission_after_control(*args, **kwargs):
        decision = await start_decision(*args, **kwargs)
        if boundary.startswith('admission'):
            change_authority()
            if boundary == 'admission_refusal':
                raise confinement.ConfinementRefused('HOST_ACCOUNT_UNVERIFIED', 'fixture admission refused', sent=False)
        return decision

    monkeypatch.setattr(confinement, 'start_decision', admission_after_control)
    monkeypatch.setattr(adapter, 'available_agents', available)
    monkeypatch.setattr(client, '_invoke_checked', connect_after_control)
    monkeypatch.setattr(confinement, 'guard_start_frame', confinement_after_control)
    try:
        with pytest.raises(TaskControlRefused):
            await coordinator._start(task, role='lead')
        current = journal.get(task_id)
        command = next(c for c in journal.commands(task_id) if c['kind'] == 'start_lead')
        sid = command['session_id']
        assert changed and current['paused'] == (control == 'pause')
        assert current['state'] == 'queued' and command['status'] == 'cancelled'
        assert 'claude:start-session' not in mock.channels()
        assert mock.channels().count('worktree:create') == (0 if boundary == 'carrier' or boundary.startswith('admission') else 1)
        entry = registry.get('h1', sid)
        assert entry is None or entry['start_sent'] is False
        creates = mock.channels().count('worktree:create')
        assert not await adapter.recover_start(current, role='lead', session_id=sid)
        assert 'claude:start-session' not in mock.channels()
        assert mock.channels().count('worktree:create') == creates
    finally:
        await fleet.close()
