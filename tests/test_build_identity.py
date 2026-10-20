import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
import zipfile
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
    assert build_identity.build_identity() == {"version": __version__, "commit": commit, "source": source}


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
    assert build_identity.build_identity() == {
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
    assert build_identity.build_identity() == {"version": __version__, "commit": "b" * 40, "source": "bundled"}
    (tmp_path / "build-info.json").unlink()
    assert build_identity.build_identity()["commit"] is None
    assert build_identity.build_identity()["source"] == "bundled"


def _build_env(directory, commit=None):
    env = dict(os.environ)
    for key in ("PYTHONPATH", "PYTHONHOME", "BATC_BUILD_COMMIT", "GIT_DIR", "GIT_WORK_TREE",
                "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        env.pop(key, None)
    scratch = directory / "scratch"
    scratch.mkdir(exist_ok=True)
    env["TMPDIR"] = str(scratch)
    if commit is not None:
        env["BATC_BUILD_COMMIT"] = commit
    return env


def _command(args, *, cwd, env):
    result = subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True)
    assert result.returncode == 0, f"{args!r}\n{result.stdout}\n{result.stderr}"
    return result.stdout.strip()


def _source_repo(directory, label):
    """Copy the real build inputs, including the real hook when present."""
    root = Path(__file__).resolve().parents[1]
    repo = directory / "source"
    repo.mkdir()
    for name in (".gitignore", "pyproject.toml", "README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "hatch_build.py"):
        if (root / name).exists():
            shutil.copy2(root / name, repo / name)
    for name in ("src", "skills"):
        shutil.copytree(root / name, repo / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    env = _build_env(directory)
    for args in (["git", "init", "-q"], ["git", "add", "."],
                 ["git", "-c", "user.name=Build Fixture", "-c", "user.email=build@example.invalid",
                  "commit", "-qm", label]):
        _command(args, cwd=repo, env=env)
    head = _command(["git", "rev-parse", "HEAD"], cwd=repo, env=env)
    return repo, head


def _build(source, directory, mode="wheel", commit=None):
    out = directory / f"dist-{mode}"
    started = time.perf_counter()
    _command(["uv", "build", f"--{mode}", "--python", sys.executable, "--no-python-downloads",
              "--out-dir", str(out), str(source)], cwd=directory, env=_build_env(directory, commit))
    print(f"REAL BUILD {directory.name}/{mode}: {time.perf_counter() - started:.3f}s", flush=True)
    return next(out.glob("*.whl" if mode == "wheel" else "*.tar.gz"))


def _installed_identity(artifact, directory, *, frozen=False, shadow=None, commit=None):
    """An offline, dependency-free install; the resolver itself only needs stdlib."""
    env = _build_env(directory, commit)
    target = directory / "installed"
    _command(["uv", "venv", "--python", sys.executable, "--no-python-downloads", str(target)],
             cwd=directory, env=env)
    python = target / "bin" / "python"
    started = time.perf_counter()
    _command(["uv", "pip", "install", "--python", str(python), "--offline", "--no-deps", str(artifact)],
             cwd=directory, env=env)
    if str(artifact).startswith("git+"):
        print(f"REAL VCS BUILD+INSTALL: {time.perf_counter() - started:.3f}s", flush=True)
    script = (
        "import json, subprocess, sys; "
        + (f"sys.path.insert(0, {str(shadow)!r}); " if shadow else "")
        + ("sys.frozen = True; " if frozen else "")
        + "from bat_agent_connector.build_identity import build_identity; "
        "subprocess.run = lambda *a, **kw: (_ for _ in ()).throw(AssertionError('runtime process')); "
        "print(json.dumps(build_identity()))"
    )
    # Isolated mode and an unrelated cwd keep the build checkout off sys.path.
    return json.loads(_command([str(python), "-I", "-c", script], cwd=directory, env=env))


@pytest.fixture(scope="module")
def clean_wheel(tmp_path_factory):
    directory = tmp_path_factory.mktemp("clean-wheel")
    repo, head = _source_repo(directory, "clean candidate")
    wheel = _build(repo, directory)
    return directory, repo, head, wheel


def test_offline_wheel_keeps_candidate_commit(clean_wheel, tmp_path):
    directory, repo, head, wheel = clean_wheel
    assert _installed_identity(wheel, tmp_path) == {
        "version": __version__, "commit": head, "source": "package"}
    with zipfile.ZipFile(wheel) as archive:
        assert archive.namelist().count("bat_agent_connector/build-info.json") == 1
        assert json.loads(archive.read("bat_agent_connector/build-info.json")) == {"commit": head}
    assert not (repo / "src/bat_agent_connector/build-info.json").exists()
    assert _command(["git", "status", "--porcelain"], cwd=repo, env=_build_env(directory)) == ""


@pytest.mark.parametrize("mode,override", [
    ("dirty", None), ("override", "B" * 40), ("malformed", "not-a-commit"),
    ("too-long", "b" * 64), ("no-git", None), ("archive-override", "b" * 40),
    ("staged", None), ("deleted", None), ("dirty-override", "b" * 40),
])
def test_wheel_build_commit_sources(tmp_path, mode, override):
    repo, head = _source_repo(tmp_path, mode)
    if mode in {"dirty", "staged", "dirty-override"}:
        with (repo / "README.md").open("a") as stream:
            stream.write("\nA tracked change after the candidate commit.\n")
    if mode == "staged":
        _command(["git", "add", "README.md"], cwd=repo, env=_build_env(tmp_path))
    if mode == "deleted":
        (repo / "src/bat_agent_connector/cli.py").unlink()
    if mode in {"no-git", "archive-override"}:
        shutil.rmtree(repo / ".git")
    wheel = _build(repo, tmp_path, commit=override)
    expected = {"dirty": None, "staged": None, "deleted": None, "dirty-override": None,
                "no-git": None, "override": "b" * 40,
                "archive-override": "b" * 40}.get(mode, head)
    assert _installed_identity(wheel, tmp_path) == {
        "version": __version__, "commit": expected, "source": "package"}


@pytest.mark.parametrize("dirty", [False, True])
def test_sdist_preserves_provenance_without_borrowing_an_enclosing_checkout(tmp_path, dirty):
    repo, head = _source_repo(tmp_path, f"sdist dirty={dirty}")
    if dirty:
        (repo / "README.md").write_text("Changed tracked content\n")
    sdist = _build(repo, tmp_path, "sdist")
    with tarfile.open(sdist) as archive:
        names = archive.getnames()
        assert sum(name.endswith("src/bat_agent_connector/build-info.json") for name in names) == 1
        assert any(name.endswith("hatch_build.py") for name in names)
    # First exercise the real frontend's sdist extraction, then extract the same
    # producer archive under an unrelated Git checkout. Neither may relabel it.
    wheel = _build(sdist, tmp_path)
    assert _installed_identity(wheel, tmp_path) == {
        "version": __version__, "commit": None if dirty else head, "source": "package"}
    foreign = tmp_path / "foreign-checkout"
    foreign.mkdir()
    (foreign / "marker").write_text("Unrelated repository\n")
    env = _build_env(tmp_path)
    for args in (["git", "init", "-q"], ["git", "add", "marker"],
                 ["git", "-c", "user.name=Build Fixture", "-c", "user.email=build@example.invalid",
                  "commit", "-qm", "unrelated candidate"]):
        _command(args, cwd=foreign, env=env)
    with tarfile.open(sdist) as archive:
        # Owned producer output only; require regular files/directories contained
        # in the extraction root on both supported Python versions.
        for member in archive.getmembers():
            assert member.isfile() or member.isdir()
            assert (foreign / member.name).resolve().is_relative_to(foreign.resolve())
        archive.extractall(foreign, **({"filter": "data"} if hasattr(tarfile, "data_filter") else {}))
    extracted = next(foreign.glob("bat_agent_connector-*"))
    nested = tmp_path / "nested-build"
    nested.mkdir()
    nested_wheel = _build(extracted, nested)
    assert _installed_identity(nested_wheel, nested) == {
        "version": __version__, "commit": None if dirty else head, "source": "package"}


def test_frozen_resolver_reads_real_build_metadata(clean_wheel, tmp_path):
    _, _, head, wheel = clean_wheel
    assert _installed_identity(wheel, tmp_path, frozen=True) == {
        "version": __version__, "commit": head, "source": "bundled"}


def test_sdist_cannot_be_relabelled_by_a_different_override(clean_wheel, tmp_path):
    _, repo, _, _ = clean_wheel
    sdist = _build(repo, tmp_path, "sdist")
    wheel = _build(sdist, tmp_path, commit="b" * 40)
    assert _installed_identity(wheel, tmp_path) == {
        "version": __version__, "commit": None, "source": "package"}


def test_embedded_identity_cannot_identify_a_different_copy(clean_wheel, tmp_path):
    _, _, _, wheel = clean_wheel
    shadow = tmp_path / "different-copy"
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(shadow)
    # The other copy has real embedded metadata too. The installed distribution
    # still must not identify code imported from that different package path.
    assert _installed_identity(wheel, tmp_path, shadow=shadow) == {
        "version": __version__, "commit": None, "source": "package"}


@pytest.mark.parametrize("conflict", [False, True])
def test_real_vcs_install_reconciles_embedded_identity(tmp_path, conflict):
    repo, head = _source_repo(tmp_path, f"VCS conflict={conflict}")
    # uv, not the fixture, writes PEP 610 direct_url.json. Any embedded metadata
    # must likewise come from the actual Hatch build, including the conflict.
    identity = _installed_identity(f"git+{repo.as_uri()}@{head}", tmp_path,
                                   commit="b" * 40 if conflict else None)
    assert identity == {"version": __version__, "commit": None if conflict else head, "source": "vcs"}
