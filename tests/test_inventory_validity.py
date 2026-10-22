"""The inventory producer owns deadlines; reading a cached result is not a BAT check."""
from datetime import datetime
from types import SimpleNamespace
import time

from bat_agent_connector import inventory
from tests.test_api_v1 import MANUAL, http, token
from tests.test_api_v1 import daemon as api_daemon
from tests.test_api_v1 import served as api_served

daemon, served = api_daemon, api_served


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


async def test_inventory_read_deadlines_and_quiet_refresh(served, monkeypatch):
    d, port = served
    clock = {'now': 1790000000.0}
    monkeypatch.setattr(inventory, 'time', SimpleNamespace(time=lambda: clock['now'], strftime=time.strftime, gmtime=time.gmtime))
    auth = token(d, 'validity-reader', 'observe')
    await d.inventory.refresh_host('h1')
    async def read(path):
        status, data = await http(port, 'GET', '/api/v1' + path, tok=auth)
        assert status == 200
        return data
    host = (await read('/hosts'))['hosts'][0]
    detail = (await read(f'/sessions/h1/{MANUAL}'))['session']
    axis = detail['state']['evidence']['activity']
    assert stamp(host['valid_until']) == clock['now'] + d.inventory.settings.stale_after_s
    assert stamp(axis['valid_until']) == stamp(axis['observed_at']) + d.inventory.settings.stale_after_s
    assert detail['state']['evidence']['connection']['valid_until'] == host['valid_until']
    clock['now'] += 61
    assert not (await read(f'/sessions/h1/{MANUAL}'))['session']['stale']
    before = d.journal.api_events(0, 500)['next_cursor']
    await d.inventory.refresh_host('h1')
    assert d.journal.api_events(0, 500)['next_cursor'] == before
    renewed = (await read(f'/sessions/h1/{MANUAL}'))['session']['state']['evidence']['activity']
    assert stamp(renewed['valid_until']) == clock['now'] + d.inventory.settings.stale_after_s
    clock['now'] = stamp(renewed['valid_until'])
    assert (await read(f'/sessions/h1/{MANUAL}'))['session']['stale']


async def test_failed_host_keeps_success_deadline_and_activity_reason(served, mock, monkeypatch):
    d, port = served
    clock = {'now': 1790000000.0}
    monkeypatch.setattr(inventory, 'time', SimpleNamespace(time=lambda: clock['now'], strftime=time.strftime, gmtime=time.gmtime))
    await d.inventory.refresh_host('h1')
    previous = d.inventory.hosts()[0]
    def fail(_):
        raise RuntimeError('fixture read failed')
    mock.handlers['workspace:load'] = fail
    clock['now'] += 2
    await d.inventory.refresh_host('h1')
    host = d.inventory.hosts()[0]
    assert host['valid_until'] == previous['valid_until']
    assert host['stale'] and host['stale_reason'] == 'host_unreachable'
    row = d.inventory.get_session('h1', MANUAL)
    assert row['state']['evidence']['activity']['stale']
    assert row['stale_reason'] == 'host_unreachable'
