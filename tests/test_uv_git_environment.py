"""Git-URL installs must not race automatic writers in uv's Git database."""
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from bat_agent_connector import update_install
from scripts import install_connector
from tests.package_build import _build_env, _command, _source_repo
from tests.test_build_identity import _installed_identity


def test_clean_env_discards_ambient_git_entries_and_applies_product_rule(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.fsmonitor")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "untrusted-command")
    env = update_install.clean_env(tmp_path)
    assert env["GIT_CONFIG_COUNT"] == "2"
    assert env["GIT_CONFIG_KEY_0"] == "maintenance.auto"
    assert env["GIT_CONFIG_KEY_1"] == "gc.auto"


@pytest.mark.parametrize("module", [update_install, install_connector])
def test_uv_git_environment_disables_automatic_writers(module):
    original = {"PATH": "fixture-path"}
    assert module.uv_git_env(original) == {
        "PATH": "fixture-path", "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "maintenance.auto", "GIT_CONFIG_VALUE_0": "false",
        "GIT_CONFIG_KEY_1": "gc.auto", "GIT_CONFIG_VALUE_1": "0",
    }
    assert original == {"PATH": "fixture-path"}


@pytest.mark.parametrize("module", [update_install, install_connector])
def test_uv_git_environment_appends_after_caller_entries(module):
    original = {
        "GIT_CONFIG_COUNT": "3",
        "GIT_CONFIG_KEY_0": "maintenance.strategy", "GIT_CONFIG_VALUE_0": "geometric",
        "GIT_CONFIG_KEY_1": "maintenance.auto", "GIT_CONFIG_VALUE_1": "true",
        "GIT_CONFIG_KEY_2": "gc.auto", "GIT_CONFIG_VALUE_2": "123",
    }
    observed = module.uv_git_env(original)
    assert observed == {
        **original, "GIT_CONFIG_COUNT": "5",
        "GIT_CONFIG_KEY_3": "maintenance.auto", "GIT_CONFIG_VALUE_3": "false",
        "GIT_CONFIG_KEY_4": "gc.auto", "GIT_CONFIG_VALUE_4": "0",
    }
    assert original["GIT_CONFIG_COUNT"] == "3"


@pytest.mark.parametrize("module", [update_install, install_connector])
@pytest.mark.parametrize("count", ["", "wrong", "-1", "+1", "1.0", " 1", "１", None,
                                 "2147483646", "999999999999999999999999"])
def test_uv_git_environment_rejects_malformed_count_without_changes(module, count):
    original = {"GIT_CONFIG_COUNT": count, "GIT_CONFIG_KEY_0": "caller-setting"}
    with pytest.raises(ValueError, match="invalid_git_config_count"):
        module.uv_git_env(original)
    assert original == {"GIT_CONFIG_COUNT": count, "GIT_CONFIG_KEY_0": "caller-setting"}


@pytest.mark.parametrize("count", ["0", "00", "1", "7"])
def test_bootstrap_and_product_git_environment_parity(count):
    environment = {"GIT_CONFIG_COUNT": count, "LANG": "C.UTF-8"}
    for number in range(int(count)):
        environment[f"GIT_CONFIG_KEY_{number}"] = "fixture.setting"
        environment[f"GIT_CONFIG_VALUE_{number}"] = str(number)
    assert install_connector.uv_git_env(environment) == update_install.uv_git_env(environment)


def test_real_vcs_installs_do_not_start_database_maintenance(tmp_path, monkeypatch):
    """Two real fetches at one URL/cache; inspect processes, never a race outcome."""
    assert shutil.which("uv") and shutil.which("git"), "Real install requires uv and Git"
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("UV_NO_CONFIG", "1")
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "uv-cache"))
    root = Path(__file__).resolve().parents[1]
    # Populate a private cache through the public locked sync, including the real
    # build backend. Never depend on another run's Git DB or inspect cache internals.
    seed = {**_build_env(tmp_path), "UV_PROJECT_ENVIRONMENT": str(tmp_path / "seed")}
    _command(["uv", "sync", "--locked", "--extra", "dev", "--python", sys.executable], cwd=root, env=seed)
    source, first = _source_repo(tmp_path, "first install candidate")
    environment = _build_env(tmp_path)

    # Emulate Git 2.54's default strategy on older Git too. These caller entries
    # precede the product rule, including a caller trying to enable maintenance.
    caller_entries = [("maintenance.strategy", "geometric"),
                      ("maintenance.geometric-repack.auto", "-1"),
                      ("maintenance.auto", "true"), ("maintenance.autoDetach", "true")]
    monkeypatch.setenv("GIT_CONFIG_COUNT", str(len(caller_entries)))
    for number, (key, value) in enumerate(caller_entries):
        monkeypatch.setenv(f"GIT_CONFIG_KEY_{number}", key)
        monkeypatch.setenv(f"GIT_CONFIG_VALUE_{number}", value)
    monkeypatch.setenv("GIT_TRACE2_CONFIG_PARAMS", "maintenance.auto,gc.auto,maintenance.strategy")

    maintenance = []
    for number in range(2):
        commit = first
        if number:
            # Publish the second commit only after the first install: uv may
            # fetch all refs, so precreating it would skip the second fetch.
            with (source / "README.md").open("a") as stream:
                stream.write("\nSecond fixture candidate at the same Git URL.\n")
            _command(["git", "add", "README.md"], cwd=source, env=environment)
            _command(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                      "commit", "-qm", "second install candidate"], cwd=source, env=environment)
            commit = _command(["git", "rev-parse", "HEAD"], cwd=source, env=environment)
            assert first != commit
        directory = tmp_path / f"install-{number}"
        directory.mkdir()
        trace = tmp_path / f"install-{number}.trace.jsonl"
        monkeypatch.setenv("GIT_TRACE2_EVENT", str(trace))
        identity = _installed_identity(f"git+{source.as_uri()}@{commit}", directory)
        assert identity["commit"] == commit
        events = [json.loads(line) for line in trace.read_text().splitlines()]
        fetches = {event["sid"] for event in events if event["event"] == "start"
                   and "fetch" in event["argv"] and source.as_uri() in event["argv"]}
        assert fetches, "Each distinct candidate must fetch into uv's database"
        children = [event for event in events if event["event"] == "child_start"
                    and event["sid"] in fetches and "maintenance" in event["argv"]]
        maintenance.extend(children)
        excerpt = [event for event in events if event["sid"] in fetches
                   and (event["event"] in {"start", "def_param"} or event in children)]
        print("UV_GIT_FETCH_TRACE " + json.dumps(excerpt), flush=True)
    assert not maintenance, "uv database fetch started automatic Git maintenance: " + json.dumps(maintenance)
