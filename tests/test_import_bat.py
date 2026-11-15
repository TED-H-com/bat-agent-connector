from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from bat_agent_connector import cli
from bat_agent_connector.config import DEFAULT_BAT_PROFILES_DIR, parse_config, tomllib
from bat_agent_connector.errors import ConfigError
from bat_agent_connector.importer import (
    import_notes,
    import_source,
    read_bat_profiles,
    render_hosts_toml,
    unresolved_tokens,
)


def _write_profiles_index(pdir: Path, profiles: list[dict]) -> None:
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "index.json").write_text(json.dumps({"profiles": profiles}), encoding="utf-8")


def _write_two_remote_one_local_index(pdir: Path) -> None:
    _write_profiles_index(
        pdir,
        [
            {"id": "local-default", "type": "local"},
            {
                "id": "box-b",
                "name": "Box B",
                "type": "remote",
                "remoteHost": "127.0.0.10",
                "remotePort": 9010,
                "remoteFingerprint": "CD:" * 31 + "CD",
                "remoteProfileId": "default",
            },
            {
                "id": "box-a",
                "name": "Box A",
                "type": "remote",
                "remoteHost": "127.0.0.9",
                "remotePort": 9009,
                "remoteFingerprint": "AB:" * 31 + "AB",
                "remoteProfileId": "default",
            },
        ],
    )


def _write_one_remote_index(pdir: Path) -> None:
    _write_profiles_index(
        pdir,
        [
            {
                "id": "box-a",
                "name": "Box A",
                "type": "remote",
                "remoteHost": "127.0.0.9",
                "remotePort": 9009,
                "remoteFingerprint": "AB:" * 31 + "AB",
                "remoteProfileId": "default",
            },
        ],
    )


def _write_token_store(pdir: Path, payload: dict) -> None:
    pdir.mkdir(parents=True, exist_ok=True)
    store_path = pdir / "remote-tokens.enc.json"
    store_path.write_text(json.dumps(payload), encoding="utf-8")
    store_path.chmod(0o600)


def _bind_cli_import_source(
    monkeypatch: pytest.MonkeyPatch, *, platform: str, environment: dict[str, str], home: Path
) -> None:
    monkeypatch.setattr(
        cli,
        "import_source",
        lambda named, **_: import_source(
            named, platform=platform, environment=environment, home=home
        ),
    )


def test_case_01_import_source_linux_default(tmp_path: Path) -> None:
    directory, recorded = import_source(None, platform="linux", environment={}, home=tmp_path)
    assert directory == tmp_path / ".local/share/org.tonyq.better-agent-terminal/profiles"
    assert recorded == DEFAULT_BAT_PROFILES_DIR


def test_case_02_import_source_darwin_default(tmp_path: Path) -> None:
    directory, recorded = import_source(None, platform="darwin", environment={}, home=tmp_path)
    assert directory == tmp_path / "Library/Application Support/org.tonyq.better-agent-terminal/profiles"
    assert recorded == str(directory)


def test_case_03_import_source_win32_appdata_and_legacy(tmp_path: Path) -> None:
    appdata = tmp_path / "AppData/Roaming"
    directory, recorded = import_source(
        None, platform="win32", environment={"APPDATA": str(appdata)}, home=tmp_path
    )
    assert directory == appdata / "BetterAgentTerminal/profiles"
    assert recorded == str(directory)

    legacy_appdata = tmp_path / "LegacyAppData/Roaming"
    (legacy_appdata / "org.tonyq.better-agent-terminal").mkdir(parents=True)
    legacy_dir, legacy_recorded = import_source(
        None, platform="win32", environment={"APPDATA": str(legacy_appdata)}, home=tmp_path
    )
    assert legacy_dir == legacy_appdata / "org.tonyq.better-agent-terminal/profiles"
    assert legacy_recorded == str(legacy_dir)


def test_case_04_import_source_win32_missing_appdata(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as exc_info:
        import_source(None, platform="win32", environment={}, home=tmp_path)
    assert str(exc_info.value).startswith("DATA_DIRECTORY_UNAVAILABLE:")


def test_case_05_import_source_named_tilde_path(tmp_path: Path) -> None:
    for platform in ("linux", "darwin", "win32"):
        directory, recorded = import_source(
            "~/bat/profiles", platform=platform, environment={}, home=tmp_path
        )
        assert directory == tmp_path / "bat/profiles"
        assert recorded == "~/bat/profiles"


def test_case_06_import_source_named_regular_file(tmp_path: Path) -> None:
    regular_file = tmp_path / "not-a-directory"
    regular_file.write_text("fixture", encoding="utf-8")
    with pytest.raises(ConfigError) as exc_info:
        import_source(str(regular_file), platform="linux", environment={}, home=tmp_path)
    assert str(exc_info.value).startswith("DATA_DIRECTORY_INVALID:")


def test_case_07_encrypted_store_marks_hosts_and_warns(tmp_path: Path) -> None:
    pdir = tmp_path / "profiles"
    _write_two_remote_one_local_index(pdir)
    _write_token_store(pdir, {"enc": True, "data": "SYNTHETIC_CIPHERTEXT"})

    profiles = read_bat_profiles(pdir)
    unresolved = unresolved_tokens(pdir, profiles)
    assert unresolved == {"box-a": "TOKEN_STORE_ENCRYPTED", "box-b": "TOKEN_STORE_ENCRYPTED"}

    text = render_hosts_toml(profiles, profiles_dir=str(pdir), unresolved=unresolved)
    comment = (
        '# TOKEN_STORE_ENCRYPTED: BAT\'s token store cannot supply this token now; '
        'use "env:NAME" or "file:/path/to/token".'
    )
    assert f'{comment}\ntoken_ref = "bat-profile:box-a"' in text
    assert f'{comment}\ntoken_ref = "bat-profile:box-b"' in text

    cfg = parse_config(tomllib.loads(text))
    assert cfg.host("box-a").token_ref == "bat-profile:box-a"
    assert cfg.host("box-b").token_ref == "bat-profile:box-b"

    notes = import_notes(pdir, profiles, profiles, unresolved)
    assert len(notes) == 1
    assert notes[0].startswith("warning: TOKEN_STORE_ENCRYPTED:")
    assert notes[0].endswith("(BAT profiles: box-a, box-b)")


def test_case_08_partial_tokens_marks_only_missing_host(tmp_path: Path) -> None:
    pdir = tmp_path / "profiles"
    _write_two_remote_one_local_index(pdir)
    _write_token_store(
        pdir,
        {"enc": False, "data": json.dumps({"tokens": {"box-a": "SYNTHETIC_TOKEN_BOX_A"}})},
    )

    profiles = read_bat_profiles(pdir)
    unresolved = unresolved_tokens(pdir, profiles)
    assert unresolved == {"box-b": "TOKEN_MISSING"}

    text = render_hosts_toml(profiles, profiles_dir=str(pdir), unresolved=unresolved)
    comment = (
        '# TOKEN_MISSING: BAT\'s token store cannot supply this token now; '
        'use "env:NAME" or "file:/path/to/token".'
    )
    assert f'{comment}\ntoken_ref = "bat-profile:box-b"' in text
    assert f'{comment}\ntoken_ref = "bat-profile:box-a"' not in text
    assert text.count("# TOKEN_MISSING:") == 1


def test_case_09_missing_store_file_reports_unreadable(tmp_path: Path) -> None:
    pdir = tmp_path / "profiles"
    _write_two_remote_one_local_index(pdir)

    profiles = read_bat_profiles(pdir)
    unresolved = unresolved_tokens(pdir, profiles)
    assert unresolved == {"box-a": "TOKEN_STORE_UNREADABLE", "box-b": "TOKEN_STORE_UNREADABLE"}


def test_case_10_all_tokens_readable_leaves_output_unchanged(tmp_path: Path) -> None:
    pdir = tmp_path / "profiles"
    _write_two_remote_one_local_index(pdir)
    _write_token_store(
        pdir,
        {
            "enc": False,
            "data": json.dumps(
                {"tokens": {"box-a": "SYNTHETIC_TOKEN_BOX_A", "box-b": "SYNTHETIC_TOKEN_BOX_B"}}
            ),
        },
    )

    profiles = read_bat_profiles(pdir)
    unresolved = unresolved_tokens(pdir, profiles)
    assert unresolved == {}

    text_with = render_hosts_toml(profiles, profiles_dir=str(pdir), unresolved=unresolved)
    text_without = render_hosts_toml(profiles, profiles_dir=str(pdir))
    assert text_with == text_without
    assert import_notes(pdir, profiles, profiles, unresolved) == []


def test_case_11_import_notes_for_local_only_and_unmatched_only(tmp_path: Path) -> None:
    local_dir = tmp_path / "local-profiles"
    _write_profiles_index(local_dir, [{"id": "local-only", "type": "local"}])
    local_profiles = read_bat_profiles(local_dir)
    notes_local = import_notes(local_dir, local_profiles, local_profiles, {})
    assert len(notes_local) == 1
    assert notes_local[0].startswith("note: ")
    assert str(local_dir / "index.json") in notes_local[0]

    remote_dir = tmp_path / "remote-profiles"
    _write_two_remote_one_local_index(remote_dir)
    profiles = read_bat_profiles(remote_dir)
    chosen = [p for p in profiles if p["id"] in ["no-such-profile"]]
    notes_only = import_notes(remote_dir, profiles, chosen, {})
    assert len(notes_only) == 1
    assert notes_only[0].startswith("note: --only matched no remote profile")
    assert "box-a, box-b" in notes_only[0]


def test_case_12_cli_stdout_linux_default_encrypted_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pdir = tmp_path / ".local/share/org.tonyq.better-agent-terminal/profiles"
    _write_one_remote_index(pdir)
    _write_token_store(pdir, {"enc": True, "data": "SYNTHETIC_CIPHERTEXT"})
    _bind_cli_import_source(monkeypatch, platform="linux", environment={}, home=tmp_path)

    rc = cli.main(["import-bat", "--output", "-"])
    captured = capsys.readouterr()
    assert rc == 0

    profs = read_bat_profiles(pdir)
    expected_text = render_hosts_toml(
        profs,
        profiles_dir=DEFAULT_BAT_PROFILES_DIR,
        unresolved=unresolved_tokens(pdir, profs),
    )
    assert captured.out == expected_text + "\n"
    assert 'profiles_dir = "~/.local/share/org.tonyq.better-agent-terminal/profiles"' in captured.out
    assert "warning: TOKEN_STORE_ENCRYPTED:" in captured.err
    assert "warning:" not in captured.out
    assert "note:" not in captured.out


def test_case_13_cli_file_output_with_readable_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pdir = tmp_path / "custom-profiles"
    _write_one_remote_index(pdir)
    secret = "SYNTHETIC_TOKEN_NEVER_COPIED"
    _write_token_store(pdir, {"enc": False, "data": json.dumps({"tokens": {"box-a": secret}})})
    out_file = tmp_path / "out" / "hosts.toml"
    _bind_cli_import_source(monkeypatch, platform="linux", environment={}, home=tmp_path)

    rc = cli.main(["import-bat", "--profiles-dir", str(pdir), "--output", str(out_file)])
    captured = capsys.readouterr()
    assert rc == 0
    assert stat.S_IMODE(out_file.stat().st_mode) == 0o600

    expected_text = render_hosts_toml(read_bat_profiles(pdir), profiles_dir=str(pdir))
    content = out_file.read_text(encoding="utf-8")
    assert content == expected_text
    assert captured.out == f"wrote {out_file} (1 hosts, writes disabled)\n"
    assert captured.err == ""
    assert secret not in captured.out
    assert secret not in captured.err
    assert secret not in content


def test_case_14_cli_refuses_overwrite_without_warnings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pdir = tmp_path / "custom-profiles"
    _write_one_remote_index(pdir)
    _write_token_store(pdir, {"enc": True, "data": "SYNTHETIC_CIPHERTEXT"})
    out_file = tmp_path / "hosts.toml"
    out_file.write_text("existing-config\n", encoding="utf-8")
    _bind_cli_import_source(monkeypatch, platform="linux", environment={}, home=tmp_path)

    rc = cli.main(["import-bat", "--profiles-dir", str(pdir), "--output", str(out_file)])
    captured = capsys.readouterr()
    assert rc == 2
    assert out_file.read_text(encoding="utf-8") == "existing-config\n"
    assert "exists; pass --force" in captured.err
    assert not any(line.startswith(("warning:", "note:")) for line in captured.err.splitlines())


def test_case_15_cli_win32_missing_appdata_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out_file = tmp_path / "hosts.toml"
    _bind_cli_import_source(monkeypatch, platform="win32", environment={}, home=tmp_path)

    rc = cli.main(["import-bat", "--output", str(out_file)])
    captured = capsys.readouterr()
    assert rc == 1
    err_lines = captured.err.splitlines()
    assert len(err_lines) == 1
    assert err_lines[0].startswith("error: DATA_DIRECTORY_UNAVAILABLE: ")
