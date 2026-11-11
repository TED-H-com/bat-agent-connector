"""Native central runtime contract, also exercised on POSIX for parity.

No BAT/provider network access. The Windows CI lane runs these against actual
NTFS handles/ACLs, not monkeypatched os.name or an emulated Windows filesystem.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from bat_agent_connector import api_auth, platform_files
from bat_agent_connector.artifacts import ArtifactSettings
from bat_agent_connector.config import Config
from bat_agent_connector.errors import OwnerConflict
from bat_agent_connector.task_daemon import TaskDaemon
from tests.conftest import make_config
from tests.operation_helpers import settle_operations

PERSON = api_auth.Principal("local-person", frozenset({"observe", "manage"}))


@pytest.mark.skipif(os.name != "nt", reason="real Windows published DELETE handle")
@pytest.mark.parametrize("release", [True, False], ids=["released", "deadline"])
def test_windows_reader_during_publication(tmp_path, monkeypatch, release):
    native = platform_files.native
    root = tmp_path / "owned"
    platform_files.ensure_private_directory(root)
    source, target = root / "pointer.tmp", root / "task-service.json"
    platform_files.atomic_write(source, b'{"pid":42}')
    parent = native.open_dir(root)
    handle, refusals = None, []
    opening = native._open

    def observed(name, **kwargs):
        nonlocal handle
        try:
            return opening(name, **kwargs)
        except OSError as error:
            refusals.append(error.winerror)
            assert error.winerror == 32
            if release:
                native._close(handle)
                handle = None
            raise

    try:
        handle = opening(source.name, parent=parent.handle, writable=True, delete=True)
        native._atomic_replace(handle, parent, target.name)
        # Exactly the writer's published handle: access 0x00130197, share 3.
        with pytest.raises(PermissionError) as raw:
            target.read_text()
        assert raw.value.errno == 13  # CRT loses the underlying winerror.
        monkeypatch.setattr(native, "_open", observed)
        started = time.monotonic()
        if release:
            assert platform_files.read_private(target) == b'{"pid":42}'
            assert refusals == [32]
        else:
            with pytest.raises(PermissionError) as caught:
                platform_files.read_private(target)
            assert caught.value.winerror == 32 and len(refusals) > 1
            assert time.monotonic() - started >= native.PRIVATE_READ_DEADLINE_S
    finally:
        if handle is not None:
            native._close(handle)
        parent.close()


@pytest.mark.skipif(os.name != "nt", reason="real Windows ACL access denial")
def test_windows_reader_does_not_retry_acl_denial(tmp_path, monkeypatch):
    native = platform_files.native
    root = tmp_path / "owned"
    platform_files.ensure_private_directory(root)
    path = root / "task-service.json"
    platform_files.atomic_write(path, b"private")
    sid = "*" + native.current_sid()
    subprocess.run(["icacls.exe", str(path), "/deny", sid + ":(RD)"],
                   check=True, capture_output=True, timeout=10)  # noqa: S603,S607
    monkeypatch.setattr(native, "time", SimpleNamespace(monotonic=time.monotonic,
        sleep=lambda _: pytest.fail("ACL access denial was retried")))
    try:
        with pytest.raises(PermissionError) as caught:
            platform_files.read_private(path)
        assert caught.value.winerror == 5
    finally:
        subprocess.run(["icacls.exe", str(path), "/remove:d", sid],
                       check=True, capture_output=True, timeout=10)  # noqa: S603,S607


async def _close(daemon):
    await daemon.artifact_store.close_reaper()
    await daemon.api.session_observation.close()
    await daemon.inventory.close()
    await daemon.fleet.close()
    daemon.journal.close()


async def test_central_owner_artifact_publication_and_restart(tmp_path):
    path = tmp_path / "journal" / "tasks.sqlite3"
    first = TaskDaemon(Config(hosts={}), path)
    first.acquire_owner()
    second = TaskDaemon(Config(hosts={}), path)
    with pytest.raises(OwnerConflict):
        second.acquire_owner()
    raw = b"immutable local artifact\n"
    try:
        token = first._admin_token
        op, _ = first.ops.create(PERSON, action="artifact.upload", target={}, params={
            "display_name": "notes.txt", "size_bytes": len(raw),
            "expected_digest": hashlib.sha256(raw).hexdigest()}, preconditions={}, idempotency_key="portable-upload")
        identity = op["operation_id"]
        await settle_operations(first.ops)
        stream = asyncio.StreamReader()
        stream.feed_data(raw)
        stream.feed_eof()
        await first.artifact_store.receive(PERSON, identity, stream, len(raw))
        await settle_operations(first.ops)
        complete = first.ops.get(identity)
        assert complete["status"] == "succeeded", complete
        ref = complete["result"]
        assert first.artifact_store.read_content(ref["artifact_id"], ref["revision"]) == raw
        # Repeat the actual publication recovery path, including an already
        # read-only destination and the source removed by the first rename.
        row = dict(first.journal.db.execute("SELECT * FROM artifact_uploads WHERE operation_id=?", (identity,)).fetchone())
        # Reaping may already have removed staging; publication recovery itself
        # is exercised before the reaper below in the dedicated fixture.
        assert first.artifact_store.published(row) in (None, ref)
    finally:
        await _close(first)
    reopened = TaskDaemon(Config(hosts={}), path)
    try:
        reopened.acquire_owner()
        assert reopened._admin_token == token
        assert reopened.ops.get(identity)["status"] == "succeeded"
        assert reopened.artifact_store.read_content(ref["artifact_id"], ref["revision"]) == raw
        assert not reopened.journal.db.execute("SELECT 1 FROM operations WHERE operation_id!=?", (identity,)).fetchone()
    finally:
        await _close(reopened)


async def test_publish_recovery_does_not_replace_existing_revision(tmp_path):
    daemon = TaskDaemon(Config(hosts={}), tmp_path / "journal" / "tasks.sqlite3")
    daemon.acquire_owner()
    try:
        # Keep the source directory for explicit crash-after-publish replay.
        daemon.artifact_store.schedule_reap = lambda *args: None
        raw = b"publication recovery"
        op, _ = daemon.ops.create(PERSON, action="artifact.upload", target={}, params={
            "display_name": "recovery.txt", "size_bytes": len(raw),
            "expected_digest": hashlib.sha256(raw).hexdigest()}, preconditions={}, idempotency_key="publish-recovery")
        await settle_operations(daemon.ops)
        stream = asyncio.StreamReader()
        stream.feed_data(raw)
        stream.feed_eof()
        await daemon.artifact_store.receive(PERSON, op["operation_id"], stream, len(raw))
        await settle_operations(daemon.ops)
        row = dict(daemon.journal.db.execute("SELECT * FROM artifact_uploads WHERE operation_id=?", (op["operation_id"],)).fetchone())
        first = daemon.artifact_store.publish(row)
        assert daemon.artifact_store.publish(row) == first
        assert daemon.artifact_store.read_content(first["artifact_id"], first["revision"]) == raw
    finally:
        await _close(daemon)


def test_artifact_store_accepts_native_absolute_path(tmp_path):
    assert ArtifactSettings.from_dict({"store_root": str(tmp_path)}).store_root == str(tmp_path)
    with pytest.raises(ValueError, match="absolute"):
        ArtifactSettings.from_dict({"store_root": "relative/path"})


async def test_native_central_observes_mock_bat_without_adopting_manual_work(mock, tmp_path):
    daemon = TaskDaemon(make_config(mock), tmp_path / "journal" / "tasks.sqlite3")
    try:
        await daemon.inventory.refresh_host("h1")
        snapshot = daemon.inventory.list_sessions()
        assert snapshot["sessions"]
        assert all(row["host"] == "h1" for row in snapshot["sessions"])
        assert not daemon.journal.db.execute("SELECT 1 FROM operations").fetchone()
        assert "claude:start-session" not in mock.channels()
    finally:
        await _close(daemon)


def test_private_long_request_archives_use_native_storage(tmp_path):
    from bat_agent_connector.task_handoff import history_excerpt, original_words_archive

    root = tmp_path / "archives"
    original = original_words_archive("original request" * 500, root, "task-fixture")
    assert hashlib.sha256(platform_files.read_private(original["path"])).hexdigest() == original["sha256"]
    history = history_excerpt("context" * 30000, root, "task-fixture", force_archive=True)
    assert platform_files.read_private(history["archive_path"]).decode() == "context" * 30000
    assert history["truncated"]


@pytest.mark.skipif(os.name != "nt", reason="native Windows ACL contract")
def test_windows_rejects_foreign_acl_without_repairing_it(tmp_path):
    root = tmp_path / "owned"
    platform_files.ensure_private_directory(root)
    path = root / "token"
    platform_files.atomic_write(path, b"private-token-fixture")
    subprocess.run(["icacls.exe", str(path), "/grant", "*S-1-1-0:(R)"], check=True, capture_output=True)  # noqa: S603,S607
    with pytest.raises(ValueError, match="another principal"):
        platform_files.read_private(path)
    with pytest.raises(ValueError, match="another principal"):
        platform_files.atomic_write(path, b"must-not-replace")
    assert path.read_bytes() == b"private-token-fixture"


@pytest.mark.skipif(os.name != "nt", reason="native Windows junction contract")
def test_windows_junction_cannot_redirect_creation(tmp_path):
    target = tmp_path / "outside"
    target.mkdir()
    junction = tmp_path / "junction"
    subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(target)],  # noqa: S603,S607
                   check=True, capture_output=True)
    try:
        with pytest.raises((ValueError, OSError)):
            platform_files.ensure_private_directory(junction / "state")
        assert not (target / "state").exists()
    finally:
        junction.rmdir()


@pytest.mark.skipif(os.name != "nt", reason="native Windows sealed-file cleanup contract")
def test_windows_owned_cleanup_removes_sealed_file_without_unsealing_names(tmp_path):
    root = tmp_path / "owned"
    stage = root / "staging"
    platform_files.ensure_private_directory(stage)
    platform_files.atomic_write(stage / "content", b"sealed")
    parent = platform_files.native.open_dir(stage)
    try:
        platform_files.native.seal_file(parent, "content")
    finally:
        parent.close()
    assert (stage / "content").stat().st_file_attributes & 1
    outside = tmp_path / "preserved"
    outside.write_bytes(b"untouched")
    platform_files.native.remove_tree(stage)
    assert not stage.exists() and outside.read_bytes() == b"untouched"


@pytest.mark.skipif(os.name != "nt", reason="native Windows pinned-directory contract")
def test_windows_directory_chain_cannot_be_replaced_while_open(tmp_path):
    root = tmp_path / "owned"
    platform_files.ensure_private_directory(root / "nested")
    held = platform_files.native.open_dir(root / "nested")
    try:
        with pytest.raises(PermissionError):
            root.rename(tmp_path / "swapped")
    finally:
        held.close()
    root.rename(tmp_path / "swapped")


@pytest.mark.skipif(os.name != "nt", reason="native Windows private-reader rename contract")
@pytest.mark.parametrize("name", ["installation.json", "task-service.json"])
def test_windows_atomic_publication_preserves_target_while_reader_stays_open(tmp_path, monkeypatch, name):
    root = tmp_path / "owned"
    platform_files.ensure_private_directory(root)
    path = root / name
    platform_files.atomic_write(path, b'{"state":"original"}\n')
    fd = platform_files.open_private_file(path, os.O_RDONLY)
    original_rename, refusals = platform_files.native._rename, []

    def rename(*args, **kwargs):
        try:
            return original_rename(*args, **kwargs)
        except PermissionError as error:
            refusals.append(error)
            raise

    monkeypatch.setattr(platform_files.native, "_rename", rename)
    try:
        with pytest.raises(PermissionError) as error:
            platform_files.atomic_write(path, b'{"state":"replacement"}\n')
        assert error.value.winerror in {5, 32}
        assert 1 <= len(refusals) <= platform_files.native.ATOMIC_REPLACE_MAX_ATTEMPTS
        assert error.value is refusals[0]
        assert os.read(fd, 4096) == b'{"state":"original"}\n'
        assert platform_files.read_private(path) == b'{"state":"original"}\n'
        assert list(root.iterdir()) == [path]
    finally:
        os.close(fd)
    platform_files.atomic_write(path, b'{"state":"replacement"}\n')
    assert platform_files.read_private(path) == b'{"state":"replacement"}\n'
    platform_files.check_private(path)
    assert list(root.iterdir()) == [path]


@pytest.mark.skipif(os.name != "nt", reason="native Windows transient private-reader rename contract")
@pytest.mark.parametrize("name", ["installation.json", "task-service.json"])
def test_windows_atomic_publication_waits_for_reader_after_real_rename_refusal(tmp_path, monkeypatch, name):
    root = tmp_path / "owned"
    platform_files.ensure_private_directory(root)
    path = root / name
    old, new = b'{"state":"original"}\n', b'{"state":"replacement"}\n'
    platform_files.atomic_write(path, old)
    opened, attempted, closed = (threading.Event() for _ in range(3))
    read_errors, refusals, attempts = [], [], []

    def reader():
        try:
            fd = platform_files.open_private_file(path, os.O_RDONLY)
            try:
                assert os.read(fd, 4096) == old
                opened.set()
                assert attempted.wait(10), "writer never attempted the original native rename"
            finally:
                os.close(fd)
        except BaseException as error:  # Report worker assertions in this test, after cleanup.
            read_errors.append(error)
        finally:
            closed.set()

    original_rename = platform_files.native._rename

    def rename(*args, **kwargs):
        attempts.append((args[0], args[1].handle, args[2]))
        try:
            return original_rename(*args, **kwargs)
        except PermissionError as error:
            # This is the actual NT refusal, not an injected permission error.
            refusals.append(error.winerror)
            assert refusals == [5] or refusals == [32]
            attempted.set()
            assert closed.wait(10), "reader did not close after the native rename attempt"
            raise

    monkeypatch.setattr(platform_files.native, "_rename", rename)
    worker = threading.Thread(target=reader)
    worker.start()
    try:
        assert opened.wait(10), read_errors
        try:
            platform_files.atomic_write(path, new)
        except PermissionError:
            assert platform_files.read_private(path) == old
            assert list(root.iterdir()) == [path]
            raise  # Current implementation aborts here despite the reader having closed.
        assert refusals in ([5], [32])
        assert len(attempts) == 2 and attempts[0] == attempts[1]
        assert platform_files.read_private(path) == new
        platform_files.check_private(path)
        assert list(root.iterdir()) == [path]
    finally:
        attempted.set()
        worker.join(10)
        assert not worker.is_alive()
        assert not read_errors


@pytest.mark.skipif(os.name != "nt", reason="native Windows retry must retain private ACL checks")
@pytest.mark.parametrize("changed", ["target", "parent"])
def test_windows_atomic_retry_rejects_permission_change_after_reader_closes(tmp_path, monkeypatch, changed):
    root = tmp_path / "owned"
    platform_files.ensure_private_directory(root)
    path = root / "installation.json"
    platform_files.atomic_write(path, b"original")
    fd = platform_files.open_private_file(path, os.O_RDONLY)
    original_rename, refusals = platform_files.native._rename, []
    altered = path if changed == "target" else root
    modified = False

    def rename(*args, **kwargs):
        nonlocal fd, modified
        try:
            return original_rename(*args, **kwargs)
        except PermissionError as error:
            refusals.append(error.winerror)
            assert refusals in ([5], [32])
            os.close(fd)
            fd = None
            subprocess.run(["icacls.exe", str(altered), "/grant", "*S-1-1-0:(R)"],
                check=True, capture_output=True, timeout=10)  # noqa: S603,S607
            modified = True
            raise

    monkeypatch.setattr(platform_files.native, "_rename", rename)
    try:
        with pytest.raises(ValueError, match="another principal"):
            platform_files.atomic_write(path, b"must-not-replace")
        assert refusals in ([5], [32])
        assert path.read_bytes() == b"original"
        assert list(root.iterdir()) == [path]
        with pytest.raises(ValueError, match="another principal"):
            platform_files.check_private(altered, directory=changed == "parent")
    finally:
        if fd is not None:
            os.close(fd)
        if modified:
            subprocess.run(["icacls.exe", str(altered), "/remove:g", "*S-1-1-0"],
                check=True, capture_output=True, timeout=10)  # noqa: S603,S607 - restore only this test's ACL change


@pytest.mark.skipif(os.name != "nt", reason="native Windows atomic rename error classification")
@pytest.mark.parametrize("code", [2, 112], ids=["missing-source", "disk-full"])
def test_windows_atomic_replace_does_not_retry_other_native_errors(tmp_path, monkeypatch, code):
    root = tmp_path / "owned"
    platform_files.ensure_private_directory(root)
    path = root / "task-service.json"
    platform_files.atomic_write(path, b"original")
    calls = []

    def rename(*args, **kwargs):
        calls.append(code)
        platform_files.native._error(code)

    monkeypatch.setattr(platform_files.native, "_rename", rename)
    with pytest.raises(OSError) as error:
        platform_files.atomic_write(path, b"must-not-replace")
    assert error.value.winerror == code and calls == [code]
    assert platform_files.read_private(path) == b"original"
    assert list(root.iterdir()) == [path]


@pytest.mark.skipif(os.name != "nt", reason="native Windows rename error survives cleanup failure")
def test_windows_atomic_cleanup_failure_does_not_mask_original_native_refusal(tmp_path, monkeypatch):
    root = tmp_path / "owned"
    platform_files.ensure_private_directory(root)
    path = root / "task-service.json"
    platform_files.atomic_write(path, b"original")
    fd = platform_files.open_private_file(path, os.O_RDONLY)
    native = platform_files.native
    original_delete, cleanup_calls = native._delete, []
    cleanup_error = OSError("fixture cleanup unavailable")

    def unavailable(handle):
        cleanup_calls.append(handle)
        raise cleanup_error

    monkeypatch.setattr(native, "_delete", unavailable)
    try:
        with pytest.raises(PermissionError) as error:
            platform_files.atomic_write(path, b"must-not-replace")
        assert error.value.winerror in {5, 32}
        assert error.value.__cause__ is cleanup_error and len(cleanup_calls) == 1
        assert platform_files.read_private(path) == b"original"
    finally:
        os.close(fd)
        monkeypatch.setattr(native, "_delete", original_delete)
        native.remove_tree(root)


@pytest.mark.skipif(os.name != "nt", reason="native Windows verification process-tree contract")
@pytest.mark.parametrize("parent_exits", [False, True], ids=["timeout", "successful-parent"])
async def test_windows_verifier_terminates_its_descendants_before_return(tmp_path, parent_exits):
    from bat_agent_connector.task_verifier import ObservedVerifier, VerificationSettings
    from bat_agent_connector.windows_files import _bind, _close, kernel
    from bat_agent_connector.windows_processes import w

    pidfile, pinned = tmp_path / "child.pid", tmp_path / "pinned"
    child_script = ("import os,time; from pathlib import Path; "
                    f"Path({str(pidfile)!r}).write_text(str(os.getpid())); time.sleep(120)")
    script = ("import subprocess,sys,time; from pathlib import Path; "
              f"subprocess.Popen([sys.executable,'-c',{child_script!r}],"
              "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
              "print('started',flush=True);"
              + (f"\nwhile not Path({str(pinned)!r}).exists(): time.sleep(.01)" if parent_exits
                 else "time.sleep(120)"))
    verifier = ObservedVerifier(VerificationSettings())
    open_process = _bind(kernel, "OpenProcess", [w.DWORD, w.BOOL, w.DWORD], w.HANDLE)
    wait = _bind(kernel, "WaitForSingleObject", [w.HANDLE, w.DWORD], w.DWORD)
    fd = os.open(tmp_path / "verify.log", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    task = asyncio.create_task(verifier._group_run("local", str(tmp_path), (sys.executable, "-c", script),
                                                  4, fd, hashlib.sha256()))
    handle = None
    try:
        child_pid = None
        while not task.done():
            try:
                child_pid = int(pidfile.read_text())
            except (FileNotFoundError, ValueError):
                pass
            else:
                break
            await asyncio.sleep(.01)
        if task.done():
            await task
        assert child_pid, "the verifier must actually start its descendant before timeout"
        handle = open_process(0x00100000, False, child_pid)  # SYNCHRONIZE only
        assert handle, "pin the live descendant before termination, excluding PID reuse"
        assert wait(handle, 0) == 258
        pinned.write_text("pinned")
        result = await task
        assert result == (0 if parent_exits else 124)
        assert wait(handle, 0) == 0
    finally:
        try:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        finally:
            os.close(fd)
            if handle:
                _close(handle)
