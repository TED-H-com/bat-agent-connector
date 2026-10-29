"""Shared writer contract; only temporary local spool writes."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bat_agent_connector import errlog

FIXTURE = json.loads((Path(__file__).parent / 'fixtures' / 'errlog.json').read_text())


@pytest.mark.parametrize('secret', FIXTURE['secrets'])
def test_shared_secret_corpus(secret):
    assert errlog.redact(secret) != secret


@pytest.mark.parametrize('case', FIXTURE['fingerprints'])
def test_shared_fingerprint(case):
    assert errlog.fingerprint(case['component'], case['code'], case['msg']) == case['fingerprint']


def test_records_collapse_threads_and_redact(tmp_path, monkeypatch):
    path = tmp_path / 'errors.jsonl'
    monkeypatch.setenv('BAT_ERROR_LOG', str(path))
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: errlog.log_error('bat-session', 'session.resume.failed',
                      'token=hidden', context={'password': 'tiny', 'nested': ['Bearer hidden']}), range(30)))
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(records) == 1
    record = records[0]
    assert record['count'] == 30
    assert set(record) == {'ts', 'machine_id', 'component', 'version', 'level', 'code', 'msg',
                           'fingerprint', 'count', 'context'}
    assert 'hidden' not in path.read_text()
    assert 'tiny' not in path.read_text()
    assert record['context']['password'] == '[REDACTED]'


def test_fixed_window_rotation_and_io_failure(tmp_path, monkeypatch):
    path = tmp_path / 'errors.jsonl'
    monkeypatch.setenv('BAT_ERROR_LOG', str(path))
    errlog.log_error('bat-connector', 'connector.connect.failed', 'failure')
    old = json.loads(path.read_text())
    old['ts'] = (datetime.now(timezone.utc) - timedelta(seconds=61)).isoformat()
    path.write_text(json.dumps(old) + '\n')
    errlog.log_error('bat-connector', 'connector.connect.failed', 'failure')
    assert len(path.read_text().splitlines()) == 2
    monkeypatch.setattr(errlog, 'LIMIT', 1)
    for i in range(7):
        errlog.log_error('bat-connector', f'connector.failure.{chr(97+i)}', 'failure')
    assert len(list(tmp_path.glob('errors.jsonl*'))) == 5
    monkeypatch.setenv('BAT_ERROR_LOG', str(tmp_path))
    errlog.log_error('bat-connector', 'connector.connect.failed', 'failure')


def test_symlink_not_followed(tmp_path, monkeypatch):
    target = tmp_path / 'private'
    target.write_text('unchanged')
    path = tmp_path / 'errors.jsonl'
    path.symlink_to(target)
    monkeypatch.setenv('BAT_ERROR_LOG', str(path))
    errlog.log_error('bat-session', 'session.start.failed', 'error')
    assert target.read_text() == 'unchanged'


@pytest.mark.parametrize('case', FIXTURE['redaction_cases'])
def test_redaction_exact(case):
    assert errlog.redact(case['msg']) == case['expected']


@pytest.mark.parametrize('failure,code', [('connect', 'connector.connect.failed'),
                                         ('auth', 'connector.auth.failed')])
async def test_connect_error_hook_without_network(tmp_path, monkeypatch, failure, code):
    from bat_agent_connector.client import BatClient
    from bat_agent_connector.config import HostConfig
    from bat_agent_connector.errors import AuthError, ConnectionLost

    path = tmp_path / 'errors.jsonl'
    monkeypatch.setenv('BAT_ERROR_LOG', str(path))
    client = BatClient(HostConfig('example', 'wss://example.invalid', '0' * 64, 'env:UNUSED'),
                       device_id='fixture')
    error = AuthError('password=hidden') if failure == 'auth' else ConnectionLost('token=hidden')

    async def fail():
        raise error

    monkeypatch.setattr(client, '_connect_locked', fail)
    with pytest.raises(type(error)) as caught:
        await client.connect()
    assert caught.value is error
    record = json.loads(path.read_text())
    assert record['component'] == 'bat-connector'
    assert record['code'] == code
    assert 'hidden' not in path.read_text()


def test_malformed_record_does_not_disable_logging(tmp_path, monkeypatch):
    path = tmp_path / 'errors.jsonl'
    path.write_text('partial old record\n')
    monkeypatch.setenv('BAT_ERROR_LOG', str(path))
    errlog.log_error('bat-connector', 'connector.connect.failed', 'failure')
    assert json.loads(path.read_text().splitlines()[-1])['code'] == 'connector.connect.failed'


@pytest.mark.parametrize('channel,component,code', [
    ('claude:client-resume', 'bat-session', 'session.resume.failed'),
    ('codex:send-message', 'bat-session', 'session.invoke.failed'),
    ('workspace:get', 'bat-connector', 'connector.invoke.failed'),
])
def test_invoke_error_classification(tmp_path, monkeypatch, channel, component, code):
    from bat_agent_connector.client import _record_invoke_error

    path = tmp_path / 'errors.jsonl'
    monkeypatch.setenv('BAT_ERROR_LOG', str(path))
    _record_invoke_error(channel, 'password=hidden')
    record = json.loads(path.read_text())
    assert record['component'] == component
    assert record['code'] == code
    assert 'hidden' not in path.read_text()
