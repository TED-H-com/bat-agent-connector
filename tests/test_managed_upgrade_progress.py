"""A seed deadline distinguishes useful progress from diagnostic heartbeats."""
from __future__ import annotations

import json
import runpy
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'desktop/tests/managed-upgrade-fixture.py'


class Seed:
    def __init__(self, root, *, ready_at=None, progress_until=0, samples=False):
        self.now = 0
        self.output = root / 'publication-seed.json'
        self.log = root / 'publication-seed.log'
        self.log.touch()
        self.ready_at = ready_at
        self.progress_until = progress_until
        self.samples = samples

    def poll(self):
        return None

    def sleep(self, _):
        # Virtual seconds only; no subprocess or wall-clock sleep.
        self.now += 1
        if self.now <= self.progress_until and self.now % 5 == 0:
            self.event('host.call.end')
        if self.samples:
            self.event('fixture.setup.sample')
        if self.ready_at is not None and self.now >= self.ready_at:
            self.output.write_text('{}')

    def event(self, event):
        with self.log.open('a') as log:
            log.write('[seed-timeline] ' + json.dumps({'event': event, 'elapsed_s': self.now}) + '\n')


@pytest.fixture
def fixture(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPT.parent))
    return runpy.run_path(str(SCRIPT))


def wait(fixture, seed):
    fixture['wait_for_seed'](seed, seed.output, seed.log,
                             monotonic=lambda: seed.now, sleep=seed.sleep)


def test_slow_seed_with_real_progress_can_become_ready_after_old_fixed_bound(fixture, tmp_path):
    seed = Seed(tmp_path, ready_at=55, progress_until=55)
    wait(fixture, seed)
    assert seed.now == 55 and seed.output.exists()


def test_hung_seed_fails_at_inactivity_bound_even_with_stack_samples(fixture, tmp_path):
    seed = Seed(tmp_path, samples=True)
    with pytest.raises(AssertionError, match='Original source producer did not become ready.*no progress'):
        wait(fixture, seed)
    assert seed.now == fixture['SEED_INACTIVITY_TIMEOUT_S']


def test_progress_without_readiness_still_fails_at_hard_ceiling(fixture, tmp_path):
    seed = Seed(tmp_path, progress_until=1000)
    with pytest.raises(AssertionError, match='Original source producer did not become ready.*ceiling'):
        wait(fixture, seed)
    assert seed.now == fixture['SEED_READINESS_CEILING_S']
