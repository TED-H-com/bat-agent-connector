"""Shared managed launch, stop-hold reply and service-owner assertions."""

import contextlib
import json
from copy import deepcopy
from pathlib import Path

from bat_agent_connector.managed_runtime import _locked


def stable_launch(reply):
    value = deepcopy(reply)
    for field in ("bundled_runtime", "running_runtime"):
        if isinstance(value.get(field), dict):
            value[field].pop("observed_at", None)
    return value


def assert_stopped_reply(returncode, reply, lifecycle):
    """Exact real CLI reply, with only the installation's two times substituted."""
    golden = json.loads((Path(__file__).resolve().parents[2] / 'tests/fixtures/managed-stop-hold.json').read_text())
    assert returncode == golden['exit']
    assert lifecycle['state'] == 'stopped'
    expected = deepcopy(golden['reply'])
    for clock in ('requested_at', 'stopped_at'):
        expected['lifecycle'][clock] = lifecycle[clock]
    assert reply == expected


def service_files(data):
    # _spawn opens/truncates service.log before creating any service process.
    # Bytes plus file clocks catch a spawn even if its child never takes the lease.
    return {name: (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ctime_ns)
            for name in ('service.log', 'state/task-service.json') if (path := data / name).exists()}


@contextlib.contextmanager
def service_stays_stopped(data):
    """A refused packaged command cannot take this lease or spawn a service."""
    hold = (data / 'state/lifecycle.json').read_bytes()
    before = service_files(data)
    # Taking the real lease proves Stop released it; holding it across refusal
    # prevents even a transient service owner. Never trust or kill a recorded PID.
    with _locked(data / 'state/task-daemon.lock', blocking=False):
        yield
        assert service_files(data) == before, 'Held command must not spawn or replace an owner'
        assert (data / 'state/lifecycle.json').read_bytes() == hold, 'Refusal must preserve the stop hold'
    with _locked(data / 'state/task-daemon.lock', blocking=False):
        pass  # Still no owner after the refused launcher exits.
