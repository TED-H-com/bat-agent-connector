"""Remote usage fixtures execute shipped code locally; never launch SSH or contact hosts."""
from __future__ import annotations

import json
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest

from bat_agent_connector.errors import ConfigError
from bat_agent_connector.usage_adapters import UsageInvalid
from bat_agent_connector.usage_config import UsageConfig, parse_usage
from bat_agent_connector.usage_host import UsageHost, helper_source
from bat_agent_connector.usage_host_helper import REPLY_BYTES, VERSION, execute
from tests.test_api_v1 import daemon  # noqa: F401
from tests.test_resource_usage import append, claude_usage, codex_usage, read, ready  # noqa: F401


class LocalHost(UsageHost):
    def __init__(self):
        super().__init__({"example-worker": "fixture-alias"}, timeout_s=10)
        self.offline = False
        self.reply = None

    def argv(self, source):
        if self.offline:
            raise UsageInvalid("host_unreachable")
        if self.reply is not None:
            return sys.executable, "-I", "-S", "-B", "-c", self.reply
        return sys.executable, "-I", "-S", "-B", "-c", helper_source(source)


@pytest.fixture
async def remote(ready):  # noqa: F811
    d, wid, source = ready
    source = replace(source, host="example-worker")
    d.usage_collector.config = UsageConfig(10, (source,))
    d.usage_collector.remote = LocalHost()
    return d, wid, source


@pytest.mark.parametrize("adapter", ["codex-rollout-v1", "claude-transcript-v1"])
async def test_remote_collect_attribution_privacy_and_replayed_page(remote, adapter):
    d, wid, source = remote
    source = replace(source, adapter=adapter, quota_pool=None)
    d.usage_collector.config = UsageConfig(10, (source,))
    if adapter.startswith("claude"):
        source.path.write_text("")
        append(source.path, claude_usage(), claude_usage())
    else:
        append(source.path, codex_usage(), codex_usage())
    host = d.usage_collector.remote
    page = await host.read(source, {}, {})
    serialized = json.dumps(page)
    for private in ("PRIVATE", "content", "prompt", "cwd", str(source.path), source.session_id):
        assert private not in serialized
    # Replay exactly the same successful response, including after cursor advancement.
    host.reply = "import sys;sys.stdin.buffer.read();sys.stdout.write(" + repr(serialized) + ")"
    await d.usage_collector.collect_once()
    await d.usage_collector.collect_once()
    result = read(d, wid, buckets="day")
    row = result["rows"][0]
    assert row["observations"] == 1
    assert row["host"] == row["host_id"] == "example-worker" and row["host_kind"] == "fleet_host"
    assert result["buckets"]["series"][0]["host"] == "example-worker"
    assert row["metrics"]["total"]["value"] == (65 if adapter.startswith("claude") else 120)
    assert result["capabilities"]["remote_collection"] is True


async def test_remote_unreachable_retains_cursor_null_then_catches_up(remote):
    d, wid, source = remote
    append(source.path, codex_usage())
    await d.usage_collector.collect_once()
    saved = d.journal.db.execute("SELECT cursor FROM resource_usage_sources").fetchone()[0]
    host = d.usage_collector.remote
    host.offline = True
    append(source.path, codex_usage(2))
    await d.usage_collector.collect_once()
    result = read(d, wid, buckets="day")
    assert result["rows"][0]["status"] == "host_unreachable"
    assert result["rows"][0]["metrics"] is None and result["rows"][0]["observations"] is None
    assert result["buckets"]["series"] == [] and result["quota_pools"][0]["snapshot"] is None
    assert d.journal.db.execute("SELECT cursor FROM resource_usage_sources").fetchone()[0] == saved
    host.offline = False
    await d.usage_collector.collect_once()
    assert read(d, wid)["rows"][0]["metrics"]["total"]["value"] == 240


async def test_remote_host_binding_change_and_inflight_revocation(remote):
    d, wid, source = remote
    append(source.path, codex_usage())
    original = d.usage_collector.remote.read

    async def revoked(*args):
        page = await original(*args)
        d.usage_collector.config = UsageConfig(10, (replace(source, host="example-other"),))
        return page

    d.usage_collector.remote.read = revoked
    await d.usage_collector.collect_once()
    assert d.journal.db.execute("SELECT count(*) FROM resource_usage_samples").fetchone()[0] == 0
    d.usage_collector.remote.read = original
    d.usage_collector.config = UsageConfig(10, (source,))
    await d.usage_collector.collect_once()
    for changed in (replace(source, host="example-other"), replace(source, host_identity="changed")):
        d.usage_collector.config = UsageConfig(10, (changed,))
        await d.usage_collector.collect_once()
        row = read(d, wid)["rows"][0]
        assert row["status"] == "binding_changed" and row["metrics"] is None


async def test_remote_lease_loss_and_transport_rebind_before_commit(remote, monkeypatch):
    d, wid, source = remote
    append(source.path, codex_usage())
    host = d.usage_collector.remote
    original = host.read

    async def lose(*args):
        page = await original(*args)
        monkeypatch.setattr(d.journal, "owner_valid", lambda: False)
        return page

    host.read = lose
    await d.usage_collector.collect_once()
    assert d.journal.db.execute("SELECT count(*) FROM resource_usage_samples").fetchone()[0] == 0
    monkeypatch.setattr(d.journal, "owner_valid", lambda: True)

    async def rebind(*args):
        page = await original(*args)
        host.aliases[source.host] = "fixture-other"
        return page

    host.read = rebind
    await d.usage_collector.collect_once()
    assert d.journal.db.execute("SELECT count(*) FROM resource_usage_samples").fetchone()[0] == 0


async def test_remote_helper_refuses_symlink_and_unconfigured_path(remote):
    d, _wid, source = remote
    moved = source.path.with_suffix(".old")
    source.path.rename(moved)
    source.path.symlink_to(moved)
    with pytest.raises(UsageInvalid, match="unsafe_file"):
        await d.usage_collector.remote.read(source, {}, {})
    binding = {"path": str(moved), "adapter": source.adapter, "session_id": source.session_id}
    with pytest.raises(UsageInvalid, match="invalid_reply"):
        execute(binding, {"version": VERSION, "cursor": {}, "parser": {}, "path": "/tmp/unconfigured.jsonl"})


@pytest.mark.parametrize("body", ["not-json", "[]", '{"version":99}', '{"version":1,"error":"PRIVATE"}',
    '{"version":1,"cursor":{},"parser":{"prompt":"PRIVATE"},"samples":[],"quota":null,"status":"current"}'])
async def test_remote_malformed_reply_keeps_cursor(remote, body):
    d, wid, source = remote
    append(source.path, codex_usage())
    await d.usage_collector.collect_once()
    saved = d.journal.db.execute("SELECT cursor FROM resource_usage_sources").fetchone()[0]
    d.usage_collector.remote.reply = "import sys;sys.stdin.buffer.read();sys.stdout.write(" + repr(body) + ")"
    await d.usage_collector.collect_once()
    assert read(d, wid)["rows"][0]["status"] == "invalid_reply"
    assert d.journal.db.execute("SELECT cursor FROM resource_usage_sources").fetchone()[0] == saved


async def test_remote_oversized_reply_and_timeout(remote):
    d, wid, _source = remote
    host = d.usage_collector.remote
    host.reply = f"import sys;sys.stdin.buffer.read();sys.stdout.buffer.write(b'x'*{REPLY_BYTES + 1})"
    await d.usage_collector.collect_once()
    assert read(d, wid)["rows"][0]["status"] == "invalid_reply"
    host.reply = "import time;time.sleep(5)"
    host.timeout_s = .05
    await d.usage_collector.collect_once()
    assert read(d, wid)["rows"][0]["status"] == "host_unreachable"


async def test_remote_bounded_pages_and_conservative_coverage(remote, monkeypatch):
    d, _wid, source = remote
    append(source.path, codex_usage())
    # The helper ships the reader source unchanged: >2000 lines requires a second cycle.
    append(source.path, *([{}] * 2000), codex_usage(2))
    monkeypatch.setattr("bat_agent_connector.usage_collector.now", lambda: "2026-10-01T00:00:00.000+00:00")
    await d.usage_collector.collect_once()
    row = d.journal.db.execute("SELECT * FROM resource_usage_sources").fetchone()
    assert row["status"] == "catching_up"
    assert json.loads(row["cursor"])["coverage_at"] == "2026-10-01T00:00:00.000+00:00"
    await d.usage_collector.collect_once()
    assert d.journal.db.execute("SELECT status FROM resource_usage_sources").fetchone()[0] == "current"
    assert d.journal.db.execute("SELECT count(*) FROM resource_usage_samples").fetchone()[0] == 2


def test_remote_config_validation_and_host_scoped_duplicates(tmp_path):
    entry = {"source_id": "coding", "workspace_id": "ws_" + "1" * 32, "adapter": "codex-rollout-v1",
             "path": str(tmp_path / "example.jsonl"), "session_id": "example-session", "host": "example-worker"}
    hosts = {"example-worker": SimpleNamespace(name="example-worker", url="wss://example.invalid",
        fingerprint="a" * 64, profile_id="default")}
    for host in (None, [], "unknown"):
        with pytest.raises(ConfigError, match="configured fleet host"):
            parse_usage({"usage": {"sources": [{**entry, "host": host}]}}, hosts)
    first = parse_usage({"usage": {"sources": [entry]}}, hosts).sources[0]
    hosts["example-worker"].fingerprint = "b" * 64
    second = parse_usage({"usage": {"sources": [entry]}}, hosts).sources[0]
    assert first.binding != second.binding
    local = {k: v for k, v in entry.items() if k != "host"}
    local["source_id"] = "central"
    assert len(parse_usage({"usage": {"sources": [entry, local]}}, hosts).sources) == 2
    with pytest.raises(ConfigError):
        parse_usage({"usage": {"sources": [{**entry, "path": "~/example.jsonl"}]}}, hosts)


def test_ssh_is_pinned_noninteractive_fixed_helper(remote):
    _d, _wid, source = remote
    argv = UsageHost({source.host: "fixture-alias"}).argv(source)
    assert argv[:7] == ("ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "fixture-alias", argv[6])
    assert argv[6].startswith("python3 -I -S -B -c ")
    for alias in ("-option", "two words", ""):
        with pytest.raises(UsageInvalid, match="host_unreachable"):
            UsageHost({source.host: alias}).argv(source)


@pytest.mark.parametrize("target", ["sample", "parser", "quota", "cursor"])
async def test_remote_rejects_extra_private_fields_in_valid_reply(remote, target):
    d, wid, source = remote
    append(source.path, codex_usage())
    host = d.usage_collector.remote
    page = await host.read(source, {}, {})
    if target == "sample":
        page["samples"][0]["content"] = "PRIVATE"
    elif target == "quota":
        page["quota"] = {"observed_at": page["samples"][0]["at"], "limit_id": "codex", "windows": [], "account": "PRIVATE"}
    else:
        page[target]["prompt"] = "PRIVATE"
    host.reply = "import sys;sys.stdin.buffer.read();sys.stdout.write(" + repr(json.dumps(page)) + ")"
    await d.usage_collector.collect_once()
    assert read(d, wid)["rows"][0]["status"] == "invalid_reply"
    assert "PRIVATE" not in "\n".join(d.journal.db.iterdump())
    assert d.journal.db.execute("SELECT cursor FROM resource_usage_sources").fetchone()[0] == "{}"


async def test_remote_quota_snapshot_is_allowlisted(remote):
    from datetime import datetime, timezone

    d, wid, source = remote
    rate = {"limit_id": "codex", "primary": {"used_percent": 25, "window_minutes": 300,
        "resets_at": int(datetime.now(timezone.utc).timestamp()) + 1000}, "credits": {"account": "PRIVATE"}}
    append(source.path, codex_usage(rate_limits=rate))
    await d.usage_collector.collect_once()
    pools = read(d, wid)["quota_pools"]
    assert pools[0]["snapshot"]["windows"][0]["used_percent"] == 25
    assert "PRIVATE" not in json.dumps(pools)


async def test_remote_alias_change_cannot_transfer_history(remote):
    d, wid, source = remote
    append(source.path, codex_usage())
    await d.usage_collector.collect_once()
    d.usage_collector.remote.aliases[source.host] = "fixture-other"
    await d.usage_collector.collect_once()
    row = read(d, wid)["rows"][0]
    assert row["status"] == "binding_changed" and row["metrics"] is None
