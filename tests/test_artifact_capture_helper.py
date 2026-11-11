"""Read-only artifact path helper cases retained from PR #512."""

import os

import pytest

from bat_agent_connector import artifact_capture_helper as helper


@pytest.mark.parametrize(("path", "parts"), (
    ("src/main.py", ["src", "main.py"]),
    ("notes.txt", ["notes.txt"]),
    ("deep/nested/file.txt", ["deep", "nested", "file.txt"]),
    ("file with spaces.txt", ["file with spaces.txt"]),
    ("x/y/z", ["x", "y", "z"]),
    ("x/.gitignore", ["x", ".gitignore"]),
))
def test_relative_parts(path, parts):
    assert helper.relative_parts(path) == parts


@pytest.mark.parametrize("path", (
    None, "", "x/" * 2050, "x\\y", "x/\x00/y", "x//y", "x/./y", "x/../y",
    ".git/config", ".GIT/config", "x/y/.git",
))
def test_relative_parts_refuses_unsafe_paths(path):
    with pytest.raises(helper.Refusal) as exc:
        helper.relative_parts(path)
    assert exc.value.code == "INVALID_CAPTURE_PATH"


@pytest.mark.parametrize("path", ("not/absolute", "/has/../parent", None))
def test_directory_rejects_unsafe_paths(path):
    with pytest.raises(helper.Refusal) as exc:
        helper.directory(path)
    assert exc.value.code == "SOURCE_UNAVAILABLE"


def test_directory_opens_valid_absolute_path(tmp_path):
    directory = tmp_path / "valid/dir"
    directory.mkdir(parents=True)
    fd = helper.directory(str(directory))
    try:
        assert os.fstat(fd) == directory.stat()
    finally:
        os.close(fd)


def test_open_file_resolves_parts(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "file.txt").write_text("hello")
    fd_root = helper.directory(str(root))
    try:
        fd_file = helper.open_file(fd_root, ["file.txt"])
        try:
            assert helper.read_chunk(fd_file, 5) == b"hello"
        finally:
            os.close(fd_file)
    finally:
        os.close(fd_root)
