"""Package fixture inputs follow the real project's build declarations."""
import shutil
import sys

import pytest

from bat_agent_connector import __version__
from bat_agent_connector.update_install import clean_env
from scripts.validate_connector_update import (
    ROOT,
    copy_fixture_source,
    fixture_build_inputs,
    installed_build_provenance,
)
from tests.package_build import _build_env, _command, _source_repo


def test_fixture_includes_declared_custom_hook(tmp_path):
    copy_fixture_source(ROOT, tmp_path)
    assert (tmp_path / "hatch_build.py").read_bytes() == (ROOT / "hatch_build.py").read_bytes()


def test_installed_provenance_does_not_need_the_build_cache(tmp_path):
    # uv and the real hook produce both metadata files. A fresh runtime HOME has
    # no build cache; installed provenance must remain sufficient on its own.
    source, commit = _source_repo(tmp_path, "installed provenance without build cache")
    root = tmp_path / "installation"
    environment = root / "tools/bat-agent-connector"
    env = _build_env(tmp_path)
    _command(["uv", "venv", "--python", sys.executable, "--no-python-downloads", str(environment)],
             cwd=tmp_path, env=env)
    _command(["uv", "pip", "install", "--python", str(environment / "bin/python"), "--offline", "--no-deps",
              f"git+{source.as_uri()}@{commit}"], cwd=tmp_path, env=env)
    home = tmp_path / "runtime-home"
    home.mkdir(mode=0o700)
    assert not (home / ".cache").exists()
    facts = installed_build_provenance(root, {"version": __version__, "commit": commit},
                                       env=clean_env(home))
    assert facts == {"identity": {"version": __version__, "commit": commit, "source": "vcs"},
                     "embedded_commit": commit, "vcs_commit": commit}


def test_fixture_follows_relocated_metadata_and_license_globs(tmp_path):
    root, destination = tmp_path / "source", tmp_path / "fixture"
    root.mkdir()
    destination.mkdir()
    copy_fixture_source(ROOT, root)
    relocations = {
        "README.md": "docs/package.md", "LICENSE": "legal/main.txt",
        "THIRD_PARTY_NOTICES.md": "legal/third-party.txt", "hatch_build.py": "hooks/provenance.py",
    }
    for before, after in relocations.items():
        (root / after).parent.mkdir(exist_ok=True)
        (root / before).rename(root / after)
    shutil.copy2(root / "hooks/provenance.py", root / "hooks/target.py")
    config = (root / "pyproject.toml").read_text().replace(
        'readme = "README.md"', 'readme = {file = "docs/package.md", content-type = "text/markdown"}'
    ).replace('license-files = ["LICENSE", "THIRD_PARTY_NOTICES.md"]', 'license-files = ["legal/*.txt"]'
    ).replace('path = "hatch_build.py"', 'path = "hooks/provenance.py"')
    config += '\n[tool.hatch.build.targets.sdist.hooks.custom]\npath = "hooks/target.py"\n'
    config += '\n[tool.hatch.build.targets.wheel.force-include]\n"docs/package.md" = "package-notes.md"\n'
    (root / "pyproject.toml").write_text(config)
    copy_fixture_source(root, destination)
    for name in [*relocations.values(), "hooks/target.py"]:
        assert (destination / name).read_bytes() == (root / name).read_bytes()
    for name in relocations:
        assert not (destination / name).exists()


def test_default_custom_hook_path_is_still_a_declared_input(tmp_path):
    config = (ROOT / "pyproject.toml").read_text().replace('path = "hatch_build.py"\n', '')
    (tmp_path / "pyproject.toml").write_text(config)
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(ROOT / name, tmp_path / name)
    assert "hatch_build.py" in fixture_build_inputs(tmp_path)


@pytest.mark.parametrize("field", ["readme", "hook", "license"])
def test_declared_input_cannot_escape_the_source_tree(tmp_path, field):
    config = (ROOT / "pyproject.toml").read_text()
    before = {"readme": 'readme = "README.md"', "hook": 'path = "hatch_build.py"',
              "license": 'license-files = ["LICENSE", "THIRD_PARTY_NOTICES.md"]'}[field]
    after = {"readme": 'readme = "../outside.md"', "hook": 'path = "../outside.py"',
             "license": 'license-files = ["../*.txt"]'}[field]
    (tmp_path / "pyproject.toml").write_text(config.replace(before, after))
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(ROOT / name, tmp_path / name)
    with pytest.raises(ValueError, match="within the source tree"):
        fixture_build_inputs(tmp_path)
