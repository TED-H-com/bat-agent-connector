"""Job policy and creation refusal never hide a real launch failure or duplicate a child."""
import ctypes as c
import io
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from bat_agent_connector import detached_process as detached


@pytest.mark.parametrize(("inside", "flags", "allowed"), [
    (0, 0, True), (1, 0x800, True), (1, 0x1000, True), (1, 0x2000, False),
])
def test_windows_job_policy(inside, flags, allowed):
    kernel = Mock()

    def in_job(process, job, pointer):
        c.cast(pointer, c.POINTER(c.c_int)).contents.value = inside
        return 1

    def limits(job, kind, pointer, size, returned):
        assert kind == 2 and size == c.sizeof(detached._Limits)
        c.cast(pointer, c.POINTER(detached._Limits)).contents.flags = flags
        return 1

    kernel.IsProcessInJob.side_effect = in_job
    kernel.QueryInformationJobObject.side_effect = limits
    assert detached.job_allows_breakaway(kernel) is allowed


def test_failed_policy_query_is_conservative():
    kernel = Mock()
    kernel.IsProcessInJob.return_value = 0
    assert not detached.job_allows_breakaway(kernel)


@pytest.mark.parametrize("allowed", [True, False])
def test_windows_spawn_flags_and_fixed_warning(monkeypatch, allowed):
    monkeypatch.setattr(detached, "job_allows_breakaway", lambda: allowed)
    popen = Mock()
    monkeypatch.setattr(detached, "subprocess", SimpleNamespace(Popen=popen))
    log = io.BytesIO()
    child = detached.spawn(["fixed-runtime", "serve"], windows=True, creationflags=0x208, stderr=log)
    assert child is popen.return_value
    assert popen.call_args.kwargs["creationflags"] == 0x208 | (detached.BREAKAWAY if allowed else 0)
    assert log.getvalue() == (b"" if allowed else detached.WARNING)


def test_access_denied_retries_once_without_breakaway(monkeypatch):
    monkeypatch.setattr(detached, "job_allows_breakaway", lambda: True)
    denied = OSError("synthetic private data must not be logged")
    denied.winerror = 5
    popen = Mock(side_effect=[denied, object()])
    monkeypatch.setattr(detached, "subprocess", SimpleNamespace(Popen=popen))
    log = io.BytesIO()
    detached.spawn(["fixed-runtime"], windows=True, creationflags=0x208, stderr=log)
    assert [call.kwargs["creationflags"] for call in popen.call_args_list] == [0x1000208, 0x208]
    assert log.getvalue() == detached.WARNING


def test_other_spawn_errors_are_not_retried(monkeypatch):
    monkeypatch.setattr(detached, "job_allows_breakaway", lambda: True)
    popen = Mock(side_effect=OSError("launch failed"))
    monkeypatch.setattr(detached, "subprocess", SimpleNamespace(Popen=popen))
    with pytest.raises(OSError):
        detached.spawn(["fixed-runtime"], windows=True, creationflags=0x208, stderr=io.BytesIO())
    assert popen.call_count == 1


def test_posix_preserves_new_session_without_querying_windows(monkeypatch):
    policy = Mock(side_effect=AssertionError("Windows-only"))
    monkeypatch.setattr(detached, "job_allows_breakaway", policy)
    popen = Mock()
    monkeypatch.setattr(detached, "subprocess", SimpleNamespace(Popen=popen))
    detached.spawn(["fixed-runtime"], windows=False, start_new_session=True, stderr=io.BytesIO())
    assert popen.call_args.kwargs["start_new_session"] is True
    assert "creationflags" not in popen.call_args.kwargs
