import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bat_agent_connector import __version__, build_identity


@pytest.mark.parametrize("doc,source,commit", [
    ({"url": "https://example.invalid/repository", "vcs_info": {"vcs": "git", "commit_id": "a" * 40}}, "vcs", "a" * 40),
    (None, "package", None),
    ({"dir_info": {"editable": True}}, "package", None),
    ({"vcs_info": {"vcs": "git", "commit_id": "not a hash"}}, "vcs", None),
])
def test_installed_provenance(monkeypatch, doc, source, commit):
    monkeypatch.setattr(build_identity.metadata, "distribution", lambda name: SimpleNamespace(
        locate_file=lambda file: Path(build_identity.__file__).parent,
        read_text=lambda file: json.dumps(doc) if doc else None))
    # Even an installed Git distribution must never inspect/run Git in a checkout.
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("runtime invoked a process"))
    assert build_identity._read_build_identity() == {"version": __version__, "commit": commit, "source": source}


@pytest.mark.parametrize("same_package", [True, False])
def test_provenance_belongs_to_running_package(monkeypatch, tmp_path, same_package):
    installed = tmp_path / "installed" / "bat_agent_connector"
    running = installed if same_package else tmp_path / "checkout" / "bat_agent_connector"
    installed.mkdir(parents=True)
    running.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(build_identity, "__file__", str(running / "build_identity.py"))
    monkeypatch.setattr(build_identity.metadata, "distribution", lambda name: SimpleNamespace(
        locate_file=lambda file: installed,
        read_text=lambda file: json.dumps({"vcs_info": {"vcs": "git", "commit_id": "a" * 40}})))
    assert build_identity._read_build_identity() == {
        "version": __version__, "commit": "a" * 40 if same_package else None,
        "source": "vcs" if same_package else "package"}


@pytest.mark.parametrize("value,valid", [
    ("a" * 40, True), ("a" * 64, True), ("a" * 7, False),
    ("a" * 41, False), ("a" * 63, False), ("a" * 65, False), ("A" * 40, False),
])
def test_one_full_commit_format(value, valid):
    assert build_identity._commit(value) == (value if valid else None)


def test_bundled_provenance(monkeypatch, tmp_path):
    monkeypatch.setattr(build_identity.sys, "frozen", True, raising=False)
    monkeypatch.setattr(build_identity, "__file__", str(tmp_path / "build_identity.py"))
    (tmp_path / "build-info.json").write_text(json.dumps({"commit": "b" * 40}))
    assert build_identity._read_build_identity() == {"version": __version__, "commit": "b" * 40, "source": "bundled"}
    (tmp_path / "build-info.json").unlink()
    assert build_identity._read_build_identity()["commit"] is None
    assert build_identity._read_build_identity()["source"] == "bundled"


def test_loaded_identity_returns_copies_and_keeps_its_original_provenance(monkeypatch):
    original = build_identity.build_identity()
    monkeypatch.setattr(build_identity, "_read_build_identity", lambda: pytest.fail("read installation after startup"))
    changed = build_identity.build_identity()
    changed.update(version="999.0.0", commit="a" * 40, source="vcs")
    assert build_identity.build_identity() == original


def test_unknown_loaded_commit_stays_unknown_when_metadata_later_appears(monkeypatch):
    distribution = SimpleNamespace(
        locate_file=lambda file: Path(build_identity.__file__).parent,
        read_text=lambda file: None)
    monkeypatch.setattr(build_identity.metadata, "distribution", lambda name: distribution)
    spec = importlib.util.spec_from_file_location("bat_agent_connector._identity_fixture", build_identity.__file__)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    original = loaded.build_identity()
    assert original == {"version": __version__, "commit": None, "source": "package"}
    distribution.read_text = lambda file: json.dumps({"vcs_info": {"vcs": "git", "commit_id": "a" * 40}})
    assert loaded._read_build_identity()["commit"] == "a" * 40
    assert loaded.build_identity() == original
