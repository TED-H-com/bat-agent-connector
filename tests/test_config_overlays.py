import os

import pytest

from bat_agent_connector.config import load_config
from bat_agent_connector.errors import ConfigError

HOST = '[hosts.example-host]\nurl="wss://127.0.0.1:9876/"\nfingerprint="' + 'AA' * 32 + '"\ntoken_ref="env:EXAMPLE_TOKEN"\n'


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    path.chmod(0o600)
    return path


def test_order_recursive_merge_and_array_replacement(tmp_path):
    base = write(tmp_path / 'hosts.toml', '[client]\nlabel="base"\nhuman_name="Example"\n[api]\nallowed_origins=["https://base.example"]\n')
    write(tmp_path / 'hosts.d/20-last.toml', '[client]\nlabel="last"\n[api]\nallowed_origins=["https://last.example"]\n')
    write(tmp_path / 'hosts.d/10-first.toml', HOST + '[client]\nlabel="first"\n')
    cfg = load_config(base)
    assert cfg.client_label == 'last'
    assert cfg.human_name == 'Example'
    assert cfg.api.allowed_origins == ('https://last.example',)
    assert set(cfg.hosts) == {'example-host'}
    assert cfg.path == base


@pytest.mark.parametrize('base_exists', [True, False])
def test_duplicate_host_names_both_sources(tmp_path, base_exists):
    base = tmp_path / 'hosts.toml'
    first = write(base if base_exists else tmp_path / 'hosts.d/10-first.toml', HOST)
    last = write(tmp_path / 'hosts.d/20-last.toml', '[hosts.example-host]\nwrites=false\n')
    with pytest.raises(ConfigError) as exc:
        load_config(base)
    assert 'example-host' in str(exc.value)
    assert str(first) in str(exc.value)
    assert str(last) in str(exc.value)


@pytest.mark.parametrize('selector', ['explicit', 'BATC_CONFIG', 'BATC_CONFIG_DIR'])
def test_missing_base_and_ignored_files(tmp_path, monkeypatch, selector):
    base = tmp_path / 'custom.toml'
    monkeypatch.delenv('BATC_CONFIG', raising=False)
    monkeypatch.delenv('BATC_CONFIG_DIR', raising=False)
    write(tmp_path / 'hosts.d/10-private.toml', HOST)
    for name in ['.hidden.toml', 'backup.toml~', 'backup.toml.bak', 'notes.txt', 'other.TOML']:
        write(tmp_path / 'hosts.d' / name, 'invalid TOML!')
    if selector == 'explicit':
        cfg = load_config(base)
    else:
        monkeypatch.setenv(selector, str(base if selector == 'BATC_CONFIG' else tmp_path))
        cfg = load_config()
    assert set(cfg.hosts) == {'example-host'}


def test_no_overlay_unchanged_and_missing_config(tmp_path):
    base = write(tmp_path / 'hosts.toml', HOST)
    assert load_config(base).hosts['example-host'].token_ref == 'env:EXAMPLE_TOKEN'
    base.unlink()
    with pytest.raises(ConfigError, match='config not found'):
        load_config(base)


def test_symlink_fragment_refused(tmp_path):
    target = write(tmp_path / 'target.toml', HOST)
    overlay = tmp_path / 'hosts.d'
    overlay.mkdir()
    (overlay / 'private.toml').symlink_to(target)
    with pytest.raises(ConfigError, match='private file'):
        load_config(tmp_path / 'hosts.toml')


@pytest.mark.skipif(os.name == 'nt', reason='POSIX permissions')
def test_public_fragment_refused(tmp_path):
    fragment = write(tmp_path / 'hosts.d/private.toml', HOST)
    fragment.chmod(0o644)
    with pytest.raises(ConfigError, match='private file'):
        load_config(tmp_path / 'hosts.toml')


def test_directory_fragment_refused(tmp_path):
    (tmp_path / 'hosts.d/not-file.toml').mkdir(parents=True)
    with pytest.raises(ConfigError, match='private file'):
        load_config(tmp_path / 'hosts.toml')


def test_parse_error_does_not_echo_content(tmp_path):
    write(tmp_path / 'hosts.d/private.toml', 'invalid-example-value!')
    with pytest.raises(ConfigError) as exc:
        load_config(tmp_path / 'hosts.toml')
    assert 'invalid-example-value' not in str(exc.value)
