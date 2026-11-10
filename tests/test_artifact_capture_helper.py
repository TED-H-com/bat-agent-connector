import os

import pytest

from bat_agent_connector import artifact_capture_helper as helper


def test_relative_parts():
    # Valid paths
    assert helper.relative_parts("src/main.py") == ["src", "main.py"]
    assert helper.relative_parts("notes.txt") == ["notes.txt"]
    assert helper.relative_parts("deep/nested/file.txt") == ["deep", "nested", "file.txt"]
    assert helper.relative_parts("file_with_spaces.txt") == ["file_with_spaces.txt"]
    assert helper.relative_parts("x/y/z") == ["x", "y", "z"]
    assert helper.relative_parts("x/.gitignore") == ["x", ".gitignore"]

    # Invalid paths
    with pytest.raises(helper.Refusal) as e:
        helper.relative_parts(None)
    assert e.value.code == "INVALID_CAPTURE_PATH"

    with pytest.raises(helper.Refusal) as e:
        helper.relative_parts("")
    assert e.value.code == "INVALID_CAPTURE_PATH"

    with pytest.raises(helper.Refusal) as e:
        helper.relative_parts("x/" * 2050)
    assert e.value.code == "INVALID_CAPTURE_PATH"

    with pytest.raises(helper.Refusal) as e:
        helper.relative_parts("x\\y")
    assert e.value.code == "INVALID_CAPTURE_PATH"

    with pytest.raises(helper.Refusal) as e:
        helper.relative_parts("x/\x00/y")
    assert e.value.code == "INVALID_CAPTURE_PATH"

    with pytest.raises(helper.Refusal) as e:
        helper.relative_parts("x//y")
    assert e.value.code == "INVALID_CAPTURE_PATH"

    with pytest.raises(helper.Refusal) as e:
        helper.relative_parts("x/./y")
    assert e.value.code == "INVALID_CAPTURE_PATH"

    with pytest.raises(helper.Refusal) as e:
        helper.relative_parts("x/../y")
    assert e.value.code == "INVALID_CAPTURE_PATH"

    with pytest.raises(helper.Refusal) as e:
        helper.relative_parts(".git/config")
    assert e.value.code == "INVALID_CAPTURE_PATH"

    with pytest.raises(helper.Refusal) as e:
        helper.relative_parts(".GIT/config")
    assert e.value.code == "INVALID_CAPTURE_PATH"

    with pytest.raises(helper.Refusal) as e:
        helper.relative_parts("x/y/.git")
    assert e.value.code == "INVALID_CAPTURE_PATH"


def test_directory_rejects_unsafe_paths():
    with pytest.raises(helper.Refusal) as e:
        helper.directory("not/absolute")
    assert e.value.code == "SOURCE_UNAVAILABLE"

    with pytest.raises(helper.Refusal) as e:
        helper.directory("/has/../parent")
    assert e.value.code == "SOURCE_UNAVAILABLE"

    with pytest.raises(helper.Refusal) as e:
        helper.directory(None)
    assert e.value.code == "SOURCE_UNAVAILABLE"


def test_directory_opens_valid_absolute_path(tmp_path):
    d = tmp_path / "valid/dir"
    d.mkdir(parents=True)
    fd = helper.directory(str(d))
    assert fd > 0
    os.close(fd)


def test_open_file_resolves_parts(tmp_path):
    d = tmp_path / "root"
    d.mkdir()
    f = d / "file.txt"
    f.write_text("hello")
    fd_root = helper.directory(str(d))
    fd_file = helper.open_file(fd_root, ["file.txt"])
    assert fd_file > 0
    assert helper.read_chunk(fd_file, 5) == b"hello"
    os.close(fd_file)
    os.close(fd_root)
