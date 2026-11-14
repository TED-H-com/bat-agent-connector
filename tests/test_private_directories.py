"""POSIX private directory creation across missing parent levels."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from bat_agent_connector import platform_files
from bat_agent_connector.task_handoff import history_excerpt, original_words_archive
from bat_agent_connector.task_journal import Journal
from bat_agent_connector.task_verifier import ObservedVerifier, VerificationSettings

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits")


@pytest.fixture(autouse=True)
def group_writable_umask():
    previous = os.umask(0o002)
    try:
        yield
    finally:
        os.umask(previous)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_make_private_dirs_creates_every_missing_level_owner_only(tmp_path: Path) -> None:
    leaf = tmp_path / "a" / "b" / "c"
    platform_files.make_private_dirs(leaf)
    assert _mode(tmp_path / "a") == 0o700
    assert _mode(tmp_path / "a" / "b") == 0o700
    assert _mode(leaf) == 0o700


def test_make_private_dirs_leaves_existing_parents_and_sets_the_leaf(tmp_path: Path) -> None:
    parent = tmp_path / "a"
    parent.mkdir(mode=0o755)
    parent.chmod(0o755)
    leaf = parent / "b" / "c"
    platform_files.make_private_dirs(leaf)
    assert _mode(parent) == 0o755
    assert _mode(parent / "b") == 0o700
    assert _mode(leaf) == 0o700

    leaf.chmod(0o755)
    assert _mode(leaf) == 0o755
    platform_files.make_private_dirs(leaf)
    assert _mode(parent) == 0o755
    assert _mode(leaf) == 0o700


def test_journal_creates_missing_parents_owner_only(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "a" / "b" / "journal.sqlite")
    try:
        assert _mode(tmp_path / "a") == 0o700
        assert _mode(tmp_path / "a" / "b") == 0o700
        assert _mode(journal.path) == 0o600
    finally:
        journal.close()


def test_handoff_archives_create_missing_parents_owner_only(tmp_path: Path) -> None:
    words_dir = tmp_path / "words_a" / "words_b"
    original_words_archive("complete request", words_dir, "task-1")
    assert _mode(tmp_path / "words_a") == 0o700
    assert _mode(words_dir) == 0o700

    history_dir = tmp_path / "history_a" / "history_b"
    history_excerpt("chat history", history_dir, "task-1", force_archive=True)
    assert _mode(tmp_path / "history_a") == 0o700
    assert _mode(history_dir) == 0o700


def test_task_artifact_directory_created_owner_only(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "a" / "b"
    verifier = ObservedVerifier(VerificationSettings(artifact_dir=str(artifact_dir)))
    log_path, fd = verifier._log({"task_id": "task-1"})
    os.close(fd)
    assert _mode(tmp_path / "a") == 0o700
    assert _mode(artifact_dir) == 0o700
    assert _mode(log_path) == 0o600
