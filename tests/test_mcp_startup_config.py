"""Startup host configuration handling for bat-agent-connector-mcp profiles."""

from __future__ import annotations

from pathlib import Path

import pytest

from bat_agent_connector import mcp_server
from bat_agent_connector.config import Config, load_config, parse_config
from bat_agent_connector.errors import ConfigError, ConfigNotFound
from bat_agent_connector.mcp_server import build_server

HOST = (
    "[hosts.example-host]\n"
    'url = "wss://example.invalid:9876/"\n'
    'fingerprint = "' + "A" * 64 + '"\n'
    'token_ref = "env:EXAMPLE_TOKEN"\n'
)


def write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    path.chmod(0o600)
    return path


def _prepare_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("BATC_CONFIG", raising=False)
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    monkeypatch.setenv("BATC_CONFIG_DIR", str(cfg_dir))
    return cfg_dir


class _Runner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def run(self, transport: str, **kwargs: object) -> None:
        self.calls.append((transport, kwargs))


def _record_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[tuple[Config, bool, bool]], _Runner]:
    built: list[tuple[Config, bool, bool]] = []
    runner = _Runner()

    def fake_build_server(
        cfg: Config, *, read_only: bool = False, principal_only: bool = False
    ) -> tuple[_Runner, None]:
        built.append((cfg, read_only, principal_only))
        return runner, None

    monkeypatch.setattr(mcp_server, "build_server", fake_build_server)
    return built, runner


def test_principal_only_starts_without_host_config_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_dir = _prepare_env(tmp_path, monkeypatch)
    built, runner = _record_startup(monkeypatch)
    mcp_server.main(["--principal-only"])
    assert len(built) == 1
    cfg, read_only, principal_only = built[0]
    assert cfg.hosts == {}
    assert cfg.path == cfg_dir / "hosts.toml"
    assert principal_only is True
    assert read_only is False
    assert runner.calls == [("stdio", {})]


def test_principal_only_read_only_starts_without_host_config_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_dir = _prepare_env(tmp_path, monkeypatch)
    built, runner = _record_startup(monkeypatch)
    mcp_server.main(["--principal-only", "--read-only"])
    assert len(built) == 1
    cfg, read_only, principal_only = built[0]
    assert cfg.hosts == {}
    assert cfg.path == cfg_dir / "hosts.toml"
    assert principal_only is True
    assert read_only is True
    assert runner.calls == [("stdio", {})]


def test_operator_profile_requires_host_config_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _prepare_env(tmp_path, monkeypatch)
    built, runner = _record_startup(monkeypatch)
    with pytest.raises(ConfigNotFound, match="config not found"):
        mcp_server.main([])
    assert built == []
    assert runner.calls == []


def test_principal_only_missing_explicit_config_arg_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_dir = _prepare_env(tmp_path, monkeypatch)
    built, runner = _record_startup(monkeypatch)
    missing = cfg_dir / "missing.toml"
    with pytest.raises(ConfigNotFound, match="config not found"):
        mcp_server.main(["--principal-only", "--config", str(missing)])
    assert built == []
    assert runner.calls == []


def test_principal_only_missing_batc_config_env_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_dir = _prepare_env(tmp_path, monkeypatch)
    built, runner = _record_startup(monkeypatch)
    missing = cfg_dir / "missing.toml"
    monkeypatch.setenv("BATC_CONFIG", str(missing))
    with pytest.raises(ConfigNotFound, match="config not found"):
        mcp_server.main(["--principal-only"])
    assert built == []
    assert runner.calls == []


def test_principal_only_loads_existing_default_hosts_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_dir = _prepare_env(tmp_path, monkeypatch)
    built, runner = _record_startup(monkeypatch)
    write(cfg_dir / "hosts.toml", HOST)
    mcp_server.main(["--principal-only"])
    assert len(built) == 1
    cfg, read_only, principal_only = built[0]
    assert set(cfg.hosts) == {"example-host"}
    assert principal_only is True
    assert read_only is False
    assert runner.calls == [("stdio", {})]


def test_principal_only_invalid_default_hosts_toml_raises_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_dir = _prepare_env(tmp_path, monkeypatch)
    built, runner = _record_startup(monkeypatch)
    write(cfg_dir / "hosts.toml", "invalid = [toml\n")
    with pytest.raises(ConfigError) as exc:
        mcp_server.main(["--principal-only"])
    assert type(exc.value) is ConfigError
    assert built == []
    assert runner.calls == []


def test_principal_only_loads_private_fragment_without_hosts_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_dir = _prepare_env(tmp_path, monkeypatch)
    built, runner = _record_startup(monkeypatch)
    write(cfg_dir / "hosts.d/10-a.toml", HOST)
    mcp_server.main(["--principal-only"])
    assert len(built) == 1
    cfg, read_only, principal_only = built[0]
    assert set(cfg.hosts) == {"example-host"}
    assert principal_only is True
    assert read_only is False
    assert runner.calls == [("stdio", {})]


def test_load_config_missing_file_raises_config_not_found_subclass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_dir = _prepare_env(tmp_path, monkeypatch)
    assert issubclass(ConfigNotFound, ConfigError)
    with pytest.raises(ConfigNotFound, match="config not found"):
        load_config(cfg_dir / "missing.toml")


async def test_real_principal_only_server_from_empty_config_registers_central_tools_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_dir = _prepare_env(tmp_path, monkeypatch)
    server, fleet = build_server(parse_config({}, cfg_dir / "hosts.toml"), principal_only=True)
    try:
        names = {t.name for t in await server.list_tools()}
        assert {
            "capabilities_get",
            "inventory_hosts",
            "inventory_sessions",
            "operation_submit",
        } <= names
        assert not ({"session_send", "hosts_list"} & names)
    finally:
        await fleet.close()
