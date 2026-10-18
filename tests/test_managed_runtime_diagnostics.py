"""Bounded failure locations never publish private package paths or tracebacks."""

import errno
import json
import sys

import pytest

from bat_agent_connector import managed_runtime as runtime


def test_missing_manifest_reports_only_registered_phase_and_storage(tmp_path):
    try:
        runtime._manifest(tmp_path / "private-directory")
    except FileNotFoundError as error:
        assert runtime._failure_location(error) == {
            "phase": "manifest", "storage": "open" if sys.platform == "win32" else "directory_walk"}
    else:
        pytest.fail("missing manifest must still fail")


def test_unregistered_code_and_exception_text_cannot_create_location():
    try:
        raise FileNotFoundError(errno.ENOENT, "private-provider-token", "/private/customer-name")
    except FileNotFoundError as error:
        assert runtime._failure_location(error) == {"phase": "unknown", "storage": "none"}


def test_native_spawn_failure_preserves_errno_without_paths_or_traceback(tmp_path, monkeypatch, capsys):
    root = tmp_path / "private-customer-data"
    missing = str(tmp_path / "private-provider-token" / "missing-executable")
    monkeypatch.setattr(runtime, "_service_command", lambda _: [missing])
    monkeypatch.setattr(sys, "argv", ["managed-runtime", "ensure", "--data-dir", str(root)])
    with pytest.raises(SystemExit) as result:
        runtime.main()
    assert result.value.code == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "protocol": 1, "error": "MANAGED_RUNTIME_UNAVAILABLE",
        "message": "FileNotFoundError: managed installation needs recovery",
        "errno": errno.ENOENT, "winerror": 2 if sys.platform == "win32" else None,
        "location": {"phase": "spawn", "storage": "none"},
    }
    assert all(value not in captured.out for value in (str(tmp_path), "private-provider-token", "Traceback"))
    assert (root / "installation.json").is_file()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode check; Windows ACL fixtures cover privacy")
def test_insecure_root_keeps_the_existing_refusal_and_reports_directory_phase(tmp_path, monkeypatch, capsys):
    root = tmp_path / "private-customer-data"
    root.mkdir(mode=0o755)
    root.chmod(0o755)
    monkeypatch.setattr(sys, "argv", ["managed-runtime", "ensure", "--data-dir", str(root)])
    with pytest.raises(SystemExit):
        runtime.main()
    captured = capsys.readouterr()
    failure = json.loads(captured.out)
    assert failure["location"] == {"phase": "root", "storage": "directory"}
    assert failure["message"] == "ValueError: managed installation needs recovery"
    assert str(root) not in captured.out
    assert not list(root.iterdir())


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX pinned path walk")
def test_missing_service_directory_is_not_misreported_as_launcher_lock(tmp_path):
    with pytest.raises(FileNotFoundError) as caught:
        runtime._running(tmp_path / "private-customer-directory")
    assert runtime._failure_location(caught.value) == {
        "phase": "service_lease", "storage": "directory_walk"}
    assert "private-customer-directory" not in json.dumps(runtime._failure_location(caught.value))


@pytest.mark.parametrize("name,label", [("launcher.lock", "launcher_lease"), ("task-daemon.lock", "service_lease")])
def test_missing_fixed_lease_leaf_has_only_a_public_object_label(tmp_path, name, label):
    import os

    with pytest.raises(FileNotFoundError) as caught:
        runtime.platform_files.open_private_file(tmp_path / name, os.O_RDONLY)
    location = runtime._failure_location(caught.value)
    assert location["phase"] == "unknown"
    assert location["storage"] == "open"
    # Windows supplies a full native filename; it remains unreported.
    if sys.platform != "win32":
        assert location["object"] == label
    assert str(tmp_path) not in json.dumps(location)
