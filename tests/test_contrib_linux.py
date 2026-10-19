"""Standalone Linux helpers use only fake backends and disk fixtures."""
import configparser
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LINUX = ROOT / "contrib/linux"
# The standalone TOML helper requires 3.11+, independently of Connector's runtime.
PYTHON = shutil.which("python3.13") or shutil.which("python3.11") or shutil.which("python3") or sys.executable


def test_unit():
    unit = configparser.ConfigParser(interpolation=None)
    unit.read(LINUX / "systemd/bat-connector-tunnel@.service")
    service = unit["Service"]
    assert service["EnvironmentFile"] == "%h/.config/bat-agent-connector/tunnels/%i.env"
    assert service["ExecStartPre"].startswith("%h/.local/bin/batc-tunnel-check ")
    assert service["ExecStart"] == (
        "/usr/bin/ssh -N -o BatchMode=yes -o ExitOnForwardFailure=yes "
        "-o ServerAliveInterval=30 -o ServerAliveCountMax=3 "
        "-L ${BATC_TUNNEL_LOCAL}:${BATC_TUNNEL_REMOTE} -- ${BATC_TUNNEL_SSH_ALIAS}"
    )
    assert service["Restart"] == "always"
    assert service["RestartSec"] == "15s"
    assert service["NoNewPrivileges"] == service["PrivateTmp"] == "yes"
    assert unit["Install"]["WantedBy"] == "default.target"


@pytest.mark.parametrize("local,remote,alias,ok", [
    ("127.0.0.2:9002", "localhost:9876", "example-host", True),
    ("[::1]:1", "[::1]:65535", "example-host", True),
    ("0.0.0.0:9002", "localhost:9876", "example-host", False),
    ("[::2]:9002", "localhost:9876", "example-host", False),
    ("127.0.0.1:0", "localhost:9876", "example-host", False),
    ("127.0.0.1:65536", "localhost:9876", "example-host", False),
    ("127.0.0.1:9002", "localhost:0", "example-host", False),
    ("127.0.0.1:9002", "bad host:123", "example-host", False),
    ("127.0.0.1:9002", "localhost:9876", "-option", False),
    ("127.0.0.1:9002", "localhost:9876", "two words", False),
])
def test_tunnel(tmp_path, local, remote, alias, ok):
    envfile = tmp_path / "tunnel.env"
    envfile.write_text(f"BATC_TUNNEL_LOCAL={local}\nBATC_TUNNEL_REMOTE={remote}\nBATC_TUNNEL_SSH_ALIAS={alias}\n")
    result = subprocess.run([sys.executable, str(LINUX / "batc-tunnel-check"), str(envfile)], capture_output=True)
    assert (result.returncode == 0) == ok
    if not ok:
        assert b"tunnel-check:" in result.stderr


@pytest.fixture
def provision(tmp_path, monkeypatch):
    version = subprocess.check_output([PYTHON, "-c", "import sys; print(sys.version_info >= (3,11))"])
    if version.strip() != b"True":
        pytest.skip("standalone provision helper requires Python 3.11+")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("bw", "bws", "hook"):
        path = bindir / name
        path.write_text(f"#!{PYTHON}\n" + '''import json, os, sys
from pathlib import Path
Path(os.environ["CALLS"]).write_text(json.dumps(sys.argv[1:]))
value = os.environ["FAKE_VALUE"]
if os.environ.get("BACKEND_FAIL"):
    print(value, file=sys.stderr)
    sys.exit(1)
print(json.dumps({"value": value}) if Path(sys.argv[0]).name == "bws" else value)
''')
        path.chmod(0o700)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.defpath)
    monkeypatch.setenv("BW_SESSION", "mock-session")
    monkeypatch.setenv("BWS_ACCESS_TOKEN", "mock-access")
    monkeypatch.setenv("FAKE_VALUE", "synthetic-token-value")
    monkeypatch.setenv("CALLS", str(tmp_path / "calls"))

    def run(backend="command", extra=(), second=False):
        target = tmp_path / "private/nested/token"
        entry = {"bitwarden": 'item = "example-item"', "bws": 'id = "example-id"',
                 "command": 'argv = ["hook", "example-arg"]'}[backend]
        spec = tmp_path / "sources.toml"
        spec.write_text(f'[example-host]\ntarget = "{target}"\nbackend = "{backend}"\n{entry}\n' + (
            f'[other-host]\ntarget = "{tmp_path / "other"}"\nbackend = "command"\nargv = ["missing"]\n'
            if second else ""))
        result = subprocess.run([PYTHON, str(LINUX / "batc-provision-tokens"), "--spec", str(spec), *extra],
                                capture_output=True, text=True)
        assert "synthetic-token-value" not in result.stdout + result.stderr
        return result, target
    return run


@pytest.mark.parametrize("backend,args", [
    ("bitwarden", ["get", "password", "example-item"]),
    ("bws", ["secret", "get", "example-id", "--output", "json"]),
    ("command", ["example-arg"]),
])
def test_provision(provision, tmp_path, backend, args):
    result, target = provision(backend)
    assert result.returncode == 0, result.stderr
    assert target.read_bytes() == b"synthetic-token-value"
    assert target.stat().st_mode & 0o777 == 0o600
    assert target.parent.stat().st_mode & 0o777 == 0o700
    assert target.parent.parent.stat().st_mode & 0o777 == 0o700
    assert json.loads((tmp_path / "calls").read_text()) == args
    target.chmod(0o644)
    assert provision(backend)[0].returncode == 0
    assert target.stat().st_mode & 0o777 == 0o600


def test_symlink(provision, tmp_path):
    target = tmp_path / "private/nested/token"
    target.parent.mkdir(parents=True)
    original = tmp_path / "original"
    original.write_text("unchanged")
    target.symlink_to(original)
    result, _ = provision()
    assert result.returncode != 0
    assert "symlink" in result.stderr
    assert original.read_text() == "unchanged"
    assert not (tmp_path / "calls").exists()


def test_missing_session(provision, monkeypatch):
    monkeypatch.delenv("BW_SESSION")
    result, target = provision("bitwarden")
    assert result.returncode != 0
    assert "BW_SESSION" in result.stderr
    assert not target.exists()


@pytest.mark.parametrize("value", ["", "x" * 65537])
def test_invalid_value(provision, monkeypatch, value):
    monkeypatch.setenv("FAKE_VALUE", value)
    result, target = provision()
    assert result.returncode != 0
    assert not target.exists()


def test_backend_failure(provision, monkeypatch):
    monkeypatch.setenv("BACKEND_FAIL", "1")
    result, target = provision()
    assert result.returncode != 0
    assert not target.exists()


def test_dry_run(provision, tmp_path):
    result, target = provision(extra=["--dry-run"])
    assert result.returncode == 0
    assert not target.parent.exists()
    assert not (tmp_path / "calls").exists()


def test_only(provision, tmp_path):
    result, target = provision(extra=["--only", "example-host"], second=True)
    assert result.returncode == 0
    assert target.exists()
    assert not (tmp_path / "other").exists()
    assert provision(extra=["--only", "unknown"])[0].returncode != 0


def test_exact_limit_and_newline(provision, monkeypatch):
    monkeypatch.setenv("FAKE_VALUE", "x" * 65536)
    result, target = provision()
    assert result.returncode == 0
    assert len(target.read_bytes()) == 65536
    monkeypatch.setenv("FAKE_VALUE", "synthetic-token-value\n")
    result, target = provision()
    assert result.returncode == 0
    assert target.read_bytes() == b"synthetic-token-value\n"


def test_missing_bws_access(provision, monkeypatch):
    monkeypatch.delenv("BWS_ACCESS_TOKEN")
    result, target = provision("bws")
    assert result.returncode != 0
    assert "BWS_ACCESS_TOKEN" in result.stderr
    assert not target.exists()


def test_tunnel_missing_and_duplicate_keys(tmp_path):
    path = tmp_path / "env"
    for text in ("", (LINUX / "systemd/tunnel.env.example").read_text() + "BATC_TUNNEL_LOCAL=127.0.0.1:1\n"):
        path.write_text(text)
        result = subprocess.run([sys.executable, str(LINUX / "batc-tunnel-check"), str(path)], capture_output=True)
        assert result.returncode != 0
