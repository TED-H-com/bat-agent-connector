"""Filesystem ownership tests and isolated native-reader retry policy."""

from __future__ import annotations

import ast
import errno
import multiprocessing
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from bat_agent_connector import platform_files as files


@pytest.mark.parametrize("code,release,flags", [
    (32, True, os.O_RDONLY), (32, False, os.O_RDONLY),
    (5, False, os.O_RDONLY), (2, False, os.O_RDONLY),
    (112, False, os.O_RDONLY), (None, False, os.O_RDONLY),
    (32, False, os.O_RDWR), (32, False, os.O_CREAT),
])
def test_native_reader_retry_policy(code, release, flags):
    # Execute the production function with only the syscall/clock/CRT boundary
    # replaced. This proves policy on POSIX, not Windows sharing or ACL behavior.
    source = Path(__file__).parents[1] / "src/bat_agent_connector/windows_files.py"
    tree = ast.parse(source.read_text())
    body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "open_file"
            or isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id.startswith("PRIVATE_READ_")
                                                   for t in node.targets)]
    clock, attempts, sleeps = [0.0], [], []
    error = OSError(errno.EACCES, "native refusal")
    error.winerror = code

    def opening(*args, **kwargs):
        attempts.append(clock[0])
        assert len(attempts) < 100, "reader retry has no deadline"
        if release and len(attempts) > 1:
            return 123
        raise error

    def sleep(seconds):
        assert seconds > 0, "reader retried after its deadline"
        sleeps.append(seconds)
        clock[0] += seconds

    scope = {"os": SimpleNamespace(**{**vars(os), "O_BINARY": 0, "O_NOINHERIT": 0}),
             "time": SimpleNamespace(monotonic=lambda: clock[0], sleep=sleep), "_open": opening,
             "msvcrt": SimpleNamespace(open_osfhandle=lambda *_: 456), "_close": lambda _: None}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(source), "exec"), scope)
    def call():
        return scope["open_file"]("task-service.json", flags, parent=SimpleNamespace(handle=1))

    if release:
        assert call() == 456
        assert len(attempts) == 2 and sleeps
    else:
        with pytest.raises(OSError) as caught:
            call()
        assert caught.value is error and caught.value.winerror == code
        if code == 32 and flags == os.O_RDONLY:
            assert len(attempts) > 1 and 0.5 <= clock[0] < 0.521
        else:
            assert len(attempts) == 1 and not sleeps


def _replace_open(monkeypatch, opening):
    # Scope syscall injection to this module, not every stdlib user in pytest.
    monkeypatch.setattr(files, "os", SimpleNamespace(**{**vars(os), "open": opening}))


@pytest.mark.skipif(os.name == "nt", reason="POSIX openat create-race regression")
@pytest.mark.parametrize("name", ["launcher.lock", "task-daemon.lock"])
def test_concurrent_creator_does_not_report_missing_private_lease(tmp_path, monkeypatch, name):
    path = tmp_path / name
    attempts, winner = [], []

    def opening(leaf, flags, mode=0o777, *, dir_fd=None):
        if leaf == name and dir_fd is not None:
            attempts.append(flags)
            if flags & os.O_CREAT and not winner:
                # Another creator publishes the same private inode. The old
                # nonexclusive O_CREAT call then receives the observed ENOENT;
                # an exclusive create instead receives the ordinary EEXIST.
                fd = os.open(leaf, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=dir_fd)
                try:
                    os.write(fd, b"existing lease")
                    winner.append(os.fstat(fd).st_ino)
                finally:
                    os.close(fd)
                if not flags & os.O_EXCL:
                    raise FileNotFoundError(errno.ENOENT, "concurrent nonexclusive create", leaf)
        return os.open(leaf, flags, mode, dir_fd=dir_fd)

    _replace_open(monkeypatch, opening)
    fd = files.open_private_file(path, os.O_RDWR | os.O_CREAT)
    try:
        assert os.fstat(fd).st_ino == winner[0] == path.stat().st_ino
        assert os.read(fd, 100) == b"existing lease"
        assert all(not flags & os.O_CREAT or flags & os.O_EXCL for flags in attempts)
        assert len(attempts) == 3  # missing open, lost exclusive create, existing open
        files.lock(fd, blocking=False)
        files.unlock(fd)
    finally:
        os.close(fd)


@pytest.mark.skipif(os.name == "nt", reason="POSIX openat create-race regression")
def test_unsettled_private_creation_is_bounded_and_has_its_own_error(tmp_path, monkeypatch):
    attempts = []

    def opening(leaf, flags, mode=0o777, *, dir_fd=None):
        if leaf == "launcher.lock":
            attempts.append(flags)
            if flags & os.O_CREAT:
                raise FileExistsError(errno.EEXIST, "another creator won", leaf)
            raise FileNotFoundError(errno.ENOENT, "leaf disappeared", leaf)
        return os.open(leaf, flags, mode, dir_fd=dir_fd)

    _replace_open(monkeypatch, opening)
    with pytest.raises(files.PrivateFileCreateRaceError, match="open/create race did not settle") as caught:
        files.open_private_file(tmp_path / "launcher.lock", os.O_RDWR | os.O_CREAT)
    assert caught.value.errno == errno.EAGAIN
    assert caught.value.filename == "launcher.lock"
    assert len(attempts) == 16  # Eight lookup/create pairs, never an unbounded retry.
    assert not (tmp_path / "launcher.lock").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX flag and mode checks")
def test_exclusive_creation_and_read_only_missing_still_refuse(tmp_path):
    path = tmp_path / "private"
    files.atomic_write(path, b"preserve")
    with pytest.raises(FileExistsError):
        files.open_private_file(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_TRUNC)
    with pytest.raises(FileNotFoundError):
        files.open_private_file(tmp_path / "missing", os.O_RDONLY)
    assert path.read_bytes() == b"preserve"


@pytest.mark.skipif(os.name == "nt", reason="POSIX no-follow and private mode checks")
@pytest.mark.parametrize("invalid", ["symlink", "hardlink", "public", "foreign_owner"])
def test_concurrent_winner_is_validated_before_truncation(tmp_path, monkeypatch, invalid):
    name, path = "service.log", tmp_path / "service.log"
    victim = tmp_path / "victim"
    victim.write_bytes(b"preserve")
    victim.chmod(0o600)
    raced = []

    def opening(leaf, flags, mode=0o777, *, dir_fd=None):
        if leaf == name and flags & os.O_CREAT and not raced:
            raced.append(True)
            if invalid == "symlink":
                path.symlink_to(victim)
            elif invalid == "hardlink":
                os.link(victim, path)
            else:
                path.write_bytes(b"preserve")
                path.chmod(0o644 if invalid == "public" else 0o600)
        return os.open(leaf, flags, mode, dir_fd=dir_fd)

    _replace_open(monkeypatch, opening)
    if invalid == "foreign_owner":
        def foreign_stat(fd):
            info = os.fstat(fd)
            return SimpleNamespace(st_mode=info.st_mode, st_uid=os.getuid() + 1, st_nlink=info.st_nlink)

        monkeypatch.setattr(files.os, "fstat", foreign_stat)
    with pytest.raises((ValueError, OSError)):
        files.open_private_file(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    assert raced
    assert path.read_bytes() == victim.read_bytes() == b"preserve"


@pytest.mark.skipif(os.name == "nt", reason="POSIX openat error propagation")
@pytest.mark.parametrize("code", [errno.EACCES, errno.EIO, errno.ENOENT])
def test_exclusive_create_errors_are_not_hidden_by_retries(tmp_path, monkeypatch, code):
    attempts = []

    def opening(leaf, flags, mode=0o777, *, dir_fd=None):
        if leaf == "new.lock":
            attempts.append(flags)
            if flags & os.O_CREAT:
                raise OSError(code, "persistent create failure", leaf)
        return os.open(leaf, flags, mode, dir_fd=dir_fd)

    _replace_open(monkeypatch, opening)
    with pytest.raises(OSError) as caught:
        files.open_private_file(tmp_path / "new.lock", os.O_RDWR | os.O_CREAT)
    assert caught.value.errno == code
    assert len(attempts) == 2


def _create_and_increment(path, barrier, result):
    barrier.wait(timeout=10)
    fd = files.open_private_file(path, os.O_RDWR | os.O_CREAT)
    try:
        files.lock(fd)
        try:
            count = int(os.read(fd, 100) or b"0")
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, str(count + 1).encode())
            info = os.fstat(fd)
            result.put((info.st_dev, info.st_ino))
        finally:
            files.unlock(fd)
    finally:
        os.close(fd)


def test_simultaneous_first_creators_share_one_lock_inode(tmp_path):
    root = tmp_path / "owned"
    files.ensure_private_directory(root)
    path = root / "new.lock"
    context = multiprocessing.get_context("spawn")
    barrier, result = context.Barrier(5), context.Queue()
    processes = [context.Process(target=_create_and_increment, args=(path, barrier, result)) for _ in range(4)]
    deadline = time.monotonic() + 20
    try:
        for process in processes:
            process.start()
        barrier.wait(timeout=10)
        identities = [result.get(timeout=max(0, deadline - time.monotonic())) for _ in processes]
        for process in processes:
            process.join(timeout=max(0, deadline - time.monotonic()))
            assert process.exitcode == 0
        assert len(set(identities)) == 1
        assert path.read_bytes() == b"4"
        files.check_private(path)
    finally:
        for process in processes:
            if process.pid is not None and process.is_alive():
                process.terminate()
            if process.pid is not None:
                process.join(timeout=5)
        result.close()
        result.join_thread()


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
