"""A failed packaged publication producer leaves useful, fixture-only evidence."""
from __future__ import annotations

import runpy
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'desktop/tests/managed-upgrade-fixture.py'


@pytest.mark.parametrize('failure', ['timeout', 'exit', 'assertion'])
def test_publication_failure_preserves_redacted_seed_and_core_logs(tmp_path, monkeypatch, failure):
    fixture = runpy.run_path(str(SCRIPT))
    scope = fixture['publication_upgrade'].__globals__
    root = tmp_path / 'runtime'
    root.mkdir()
    previous = tmp_path / 'previous-source'
    handlers = previous / 'src/bat_agent_connector'
    handlers.mkdir(parents=True)
    for name in ('pr_publication', 'pm_work_start', 'pm_attempt_results'):
        (handlers / (name + '.py')).write_text('fixture handler\n')
    checkout = tmp_path / 'checkout'
    script = checkout / 'desktop/tests/managed-upgrade-fixture.py'
    script.parent.mkdir(parents=True)
    monkeypatch.setitem(scope, '__file__', str(script))
    monkeypatch.setenv('REPO_FAKE_TOKEN', 'fixture-provider-secret')
    secret = 'batc_fixture_bearer_secret'
    log = ('older diagnostic\n' + 'diagnostic line\n' * 350 + 'SEED STACK: waiting for managed attempt\n'
           f'{secret} fixture-provider-secret /Users/fixture/private/file.py\n')
    (root / 'publication-core.log').write_text('Owned core diagnostic\n' + secret)
    arguments = []

    class Seed:
        returncode = 7 if failure == 'exit' else 0

        def poll(self):
            return 7 if failure == 'exit' else None

        def wait(self, timeout):
            return self.returncode

    def popen(command, **kwargs):
        arguments.extend(command)
        kwargs['stdout'].write(log.encode())
        kwargs['stdout'].flush()
        if failure == 'assertion':
            (root / 'publication-seed.json').write_text('{"handlers": {}, "effects": {}}')
        return Seed()

    ticks = iter([0, 0, 46])
    monkeypatch.setitem(scope, 'time', SimpleNamespace(monotonic=lambda: next(ticks), sleep=lambda _: None))
    monkeypatch.setitem(scope, 'subprocess', SimpleNamespace(
        Popen=popen, DEVNULL=subprocess.DEVNULL, STDOUT=subprocess.STDOUT,
        TimeoutExpired=subprocess.TimeoutExpired))
    monkeypatch.setitem(scope, 'terminate_owned', lambda _: None)
    monkeypatch.setitem(scope, 'command', lambda *args, **kwargs:
        {'stopped': True} if kwargs.get('action') == 'stop' else {'runtime_version': '0.2.6'})
    with pytest.raises(AssertionError) as caught:
        fixture['publication_upgrade'](Path('previous'), Path('current'), previous, root, {})

    message = str(caught.value)
    assert 'SEED STACK: waiting for managed attempt' in message
    assert 'older diagnostic' not in message  # The assertion contains only the last 300 lines.
    assert '-o' in arguments and 'faulthandler_timeout=40' in arguments
    output = checkout / 'desktop/test-results/packaged-core-upgrade'
    for name in ('publication-seed.log', 'publication-core.log'):
        saved = (output / name).read_text()
        assert saved
        assert secret not in saved and 'fixture-provider-secret' not in saved
        assert '/Users/fixture' not in saved
    assert secret not in message and 'fixture-provider-secret' not in message
    assert '/Users/fixture' not in message
    assert 'Owned core diagnostic' in (output / 'publication-core.log').read_text()
