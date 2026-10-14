import json
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
        read_text=lambda file: json.dumps(doc) if doc else None))
    # Even an installed Git distribution must never inspect/run Git in a checkout.
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("runtime invoked a process"))
    assert build_identity.build_identity() == {"version": __version__, "commit": commit, "source": source}


def test_bundled_provenance(monkeypatch, tmp_path):
    monkeypatch.setattr(build_identity.sys, "frozen", True, raising=False)
    monkeypatch.setattr(build_identity, "__file__", str(tmp_path / "build_identity.py"))
    (tmp_path / "build-info.json").write_text(json.dumps({"commit": "b" * 40}))
    assert build_identity.build_identity() == {"version": __version__, "commit": "b" * 40, "source": "bundled"}
    (tmp_path / "build-info.json").unlink()
    assert build_identity.build_identity()["commit"] is None
    assert build_identity.build_identity()["source"] == "bundled"
