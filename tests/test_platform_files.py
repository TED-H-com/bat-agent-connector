"""Real filesystem / kernel ownership tests; no simulated Windows flags."""

from __future__ import annotations

import multiprocessing
import os

import pytest

from bat_agent_connector import platform_files as files


def _contender(path, result):
    fd = files.open_private_file(path, os.O_RDWR | os.O_CREAT)
    try:
        try:
            files.lock(fd, blocking=False)
        except BlockingIOError:
            result.put("busy")
        else:
            result.put("owned")
            files.unlock(fd)
    finally:
        os.close(fd)


def test_lease_is_kernel_owned_and_recovers_after_close(tmp_path):
    root = tmp_path / "owned"
    files.ensure_private_directory(root)
    lease = root / "service.lock"
    fd = files.open_private_file(lease, os.O_RDWR | os.O_CREAT)
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    try:
        files.lock(fd, blocking=False)
        for expected in ("busy", "owned"):
            process = context.Process(target=_contender, args=(lease, results))
            process.start()
            try:
                assert results.get(timeout=10) == expected
                process.join(10)
                assert process.exitcode == 0
            finally:
                if process.is_alive():
                    process.terminate()
                    process.join(10)
            if expected == "busy":
                files.unlock(fd)
    finally:
        os.close(fd)
        results.close()
        results.join_thread()


def test_atomic_private_state_replaces_only_its_file(tmp_path, monkeypatch):
    root = tmp_path / "owned-資料"
    files.ensure_private_directory(root)
    unrelated = tmp_path / "current-directory"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)
    path = root / "identity-資料.json"
    files.atomic_write(path, b"old")
    files.atomic_write(path, b"new")
    assert files.read_private(path) == b"new"
    assert list(root.iterdir()) == [path]
    assert list(unrelated.iterdir()) == []
    files.check_private(root, directory=True)
    files.check_private(path)
    with pytest.raises(ValueError, match="size limit"):
        files.read_private(path, max_bytes=2)


def test_hardlinked_private_file_is_not_truncated_or_replaced(tmp_path):
    root = tmp_path / "owned"
    files.ensure_private_directory(root)
    victim, alias = root / "victim", root / "alias"
    files.atomic_write(victim, b"preserve")
    os.link(victim, alias)
    with pytest.raises((ValueError, OSError)):
        files.open_private_file(alias, os.O_WRONLY | os.O_TRUNC)
    with pytest.raises((ValueError, OSError)):
        files.atomic_write(alias, b"replace")
    assert victim.read_bytes() == alias.read_bytes() == b"preserve"


async def test_registry_first_use_leaves_state_ready_for_service_owner(mock, tmp_path, controller_context):
    from bat_agent_connector import registry
    from bat_agent_connector.task_daemon import TaskDaemon
    from tests.conftest import adopt, make_config

    adopt("fixture-before-service")
    files.check_private(registry.registry_path().parent, directory=True)
    daemon = TaskDaemon(make_config(mock), tmp_path / "journal" / "tasks.sqlite3")
    try:
        daemon.acquire_owner()
        assert daemon._lease_fd is not None
        assert registry.get("h1", "fixture-before-service")["status"] == "active"
    finally:
        await daemon.artifact_store.close_reaper()
        await daemon.api.session_observation.close()
        await daemon.inventory.close()
        await daemon.fleet.close()
        daemon.journal.close()


@pytest.mark.skipif(os.name == "nt", reason="Windows junction and ACL cases have native fixtures")
def test_symlink_ancestor_cannot_redirect_private_creation(tmp_path):
    target = tmp_path / "outside"
    target.mkdir(mode=0o700)
    link = tmp_path / "redirect"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(OSError):
        files.ensure_private_directory(link / "state")
    assert not (target / "state").exists()


@pytest.mark.skipif(os.name == "nt", reason="Windows privacy uses DACLs, not permission bits")
def test_existing_public_directory_is_not_adopted(tmp_path):
    path = tmp_path / "foreign"
    path.mkdir(mode=0o755)
    with pytest.raises(ValueError, match="private"):
        files.ensure_private_directory(path)
    assert path.stat().st_mode & 0o777 == 0o755
