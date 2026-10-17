"""Bundle/contract checks; no daemon, BAT connection or agent runtime is started."""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from bat_agent_connector.config import Config, HostConfig
from bat_agent_connector.mcp_server import build_server
from scripts import generate_agent_skills as generator

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def bundle_repo(tmp_path):
    for relative in (generator.SOURCE, Path(generator.GENERATOR), Path("pyproject.toml"),
                     Path("src/bat_agent_connector/__init__.py"), Path("src/bat_agent_connector/api_v1.py")):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    return tmp_path


def run_generator(root, *args):
    return subprocess.run([sys.executable, str(root / generator.GENERATOR), *args],
                          capture_output=True, text=True, check=False)


def test_all_adapters_preserve_exact_canonical_workflow_and_digest():
    source = (ROOT / generator.SOURCE).read_bytes()
    body = source.decode().split("\n---\n", 1)[1]
    for relative, expected in generator.render(ROOT).items():
        assert (ROOT / relative).read_bytes() == expected
        text = expected.decode()
        embedded = text.split("<!-- BEGIN CANONICAL WORKFLOW -->\n", 1)[1]
        assert embedded.removesuffix("<!-- END CANONICAL WORKFLOW -->\n") == body
        assert f'canonical_sha256: "{hashlib.sha256(source).hexdigest()}"' in text
        assert f'canonical_source: "{generator.SOURCE.as_posix()}"' in text
        assert "GENERATED" in text
    # The generic package continues to ship this same file, not an adapter.
    assert '"skills/bat-agent-connector/SKILL.md" = "bat_agent_connector/skill/SKILL.md"' in (
        ROOT / "pyproject.toml").read_text()


def test_generation_is_deterministic_and_check_never_repairs_drift(bundle_repo):
    assert run_generator(bundle_repo, "--check").returncode == 1
    assert not (bundle_repo / "skills/hermes").exists()
    assert run_generator(bundle_repo).returncode == 0
    paths = list(generator.render(bundle_repo))
    snapshots = {p: (bundle_repo / p).read_bytes() for p in paths}
    assert run_generator(bundle_repo).returncode == 0
    assert run_generator(bundle_repo, "--check").returncode == 0
    assert snapshots == {p: (bundle_repo / p).read_bytes() for p in paths}
    changed = bundle_repo / paths[0]
    changed.write_bytes(changed.read_bytes() + b"\nManual policy drift.\n")
    before = changed.read_bytes()
    out = run_generator(bundle_repo, "--check")
    assert out.returncode == 1 and paths[0].as_posix() in out.stderr
    assert changed.read_bytes() == before
    assert run_generator(bundle_repo).returncode == 0
    assert changed.read_bytes() == snapshots[paths[0]]


def test_canonical_change_invalidates_both_bundles_and_updates_provenance(bundle_repo):
    assert run_generator(bundle_repo).returncode == 0
    original = generator.render(bundle_repo)
    source = bundle_repo / generator.SOURCE
    source.write_text(source.read_text() + "\nAdditional canonical instruction.\n")
    out = run_generator(bundle_repo, "--check")
    assert out.returncode == 1
    assert all(p.as_posix() in out.stderr for p in original)
    assert run_generator(bundle_repo).returncode == 0
    for relative, new in generator.render(bundle_repo).items():
        assert new != original[relative]
        assert (bundle_repo / relative).read_bytes() == new


@pytest.mark.parametrize("relative,pattern,new", [
    ("src/bat_agent_connector/api_v1.py", r'^CONTRACT_VERSION = "[^"]+"', 'CONTRACT_VERSION = "2099-01-01"'),
    ("src/bat_agent_connector/api_v1.py", r"^API_VERSION = \d+$", "API_VERSION = 999"),
    ("src/bat_agent_connector/__init__.py", r'^__version__ = "[^"]+"$', '__version__ = "999.0.0"'),
    ("pyproject.toml", r'^version = "[^"]+"$', 'version = "999.0.0"'),
])
def test_version_drift_requires_canonical_review_before_generation(bundle_repo, relative, pattern, new):
    assert run_generator(bundle_repo).returncode == 0
    before = {p: (bundle_repo / p).read_bytes() for p in generator.render(bundle_repo)}
    source = bundle_repo / relative
    original = source.read_text()
    changed, replacements = re.subn(pattern, new, original, count=1, flags=re.MULTILINE)
    assert replacements == 1 and changed != original, "Fixture must actually introduce version drift"
    source.write_text(changed)
    assert run_generator(bundle_repo, "--check").returncode == 1
    assert run_generator(bundle_repo).returncode == 1
    assert before == {p: (bundle_repo / p).read_bytes() for p in before}


async def test_skill_tool_references_and_recovery_parameters_match_discovered_schemas():
    # Discovery does not dial this deliberately unusable test host or read tokens.
    config = Config(hosts={"test": HostConfig("test", "wss://127.0.0.1:1/", "AA" * 32,
                                             "env:UNUSED_SKILL_TEST_TOKEN", writes=True, orchestrate=True)})
    server, fleet = build_server(config)
    try:
        tools = {t.name: t.input_schema for t in await server.list_tools()}
        text = (ROOT / generator.SOURCE).read_text()
        referenced = set(re.findall(r"\b([a-z]+_[a-z_]+)\(", text))
        assert referenced <= tools.keys(), f"Unknown tool references: {referenced - tools.keys()}"
        # The reconnect/lost-ACK/manual-source scenarios need these exact inputs;
        # a renamed/removed parameter must invalidate the skill's instructions.
        expected = {
            "capabilities_get": set(),
            "pm_follow_up_assessments_list": {"workspace_id", "intake_id", "after", "limit"},
            "pm_follow_up_assessment_get": {"workspace_id", "assessment_id"},
            "pm_brief_export_get": {"workspace_id", "snapshot_id", "format", "locale"},
            "pm_issue_mappings_list": {"workspace_id", "after", "limit"},
            "pm_issue_mapping_get": {"workspace_id", "mapping_id"},
            "work_status": {"task_id"},
            "work_item_get": {"work_item_id"},
            "operations_list": {"statuses", "action"},
            "operation_get": {"operation_id"},
            "operation_submit": {"action", "idempotency_key", "target", "params", "preconditions", "confirm"},
            "checkpoint_preview": {"host", "session_id"},
            "checkpoint_create": {"host", "session_id", "commit", "idempotency_key", "confirm"},
            "work_continue_from_checkpoint": {"checkpoint_id", "instructions", "idempotency_key", "confirm"},
            "repository_preview": {"repository", "host", "workspace_id", "source_ref"},
            "work_continue_from_repository": {"repository", "host", "workspace_id", "source_ref", "source_sha", "repository_id", "binding_digest", "prompt", "idempotency_key", "confirm"},
            "session_read": {"host", "session_id", "last_n", "offset", "include_tools", "max_chars", "after"},
            "session_wait": {"host", "session_id", "until", "timeout_s", "require_new", "after"},
            "session_record_verification": {"host", "session_id", "candidate_commit", "command", "exit_code", "environment", "log_ref", "confirm", "idempotency_key"},
            "worktree_merge": {"host", "session_id", "confirm", "idempotency_key"},
            "session_cleanup": {"host", "dry_run"},
            "session_send": {"host", "session_id", "message_id", "queue", "idempotency_key", "control_version", "confirm"},
            "session_continue": {"host", "session_id", "idempotency_key", "control_version", "confirm"},
            "session_answer": {"host", "session_id", "tool_use_id", "dont_ask_again", "idempotency_key", "control_version", "confirm"},
            "session_interrupt": {"host", "session_id", "idempotency_key", "control_version", "confirm"},
            "session_set_permissions": {"host", "session_id", "mode", "idempotency_key", "control_version", "confirm"},
        }
        for name, params in expected.items():
            assert params <= tools[name]["properties"].keys()
    finally:
        await fleet.close()


async def test_dependency_impact_description_teaches_the_captured_key_format():
    server, fleet = build_server(Config(hosts={}))
    try:
        tool = next(t for t in await server.list_tools() if t.name == 'pm_dependencies_impact_get')
        assert tool.input_schema['required'] == ['workspace_id', 'dependency']
        for hint in ('pmdepkey_', 'pm_dependencies_get', 'dependencies.<kind>[].key',
                     'edges[].dependency_key', 'declared[].dependency_key', 'package name', '422'):
            assert hint in tool.description
            assert hint in (ROOT / generator.SOURCE).read_text()
    finally:
        await fleet.close()


def test_skill_thread_actions_and_context_failures_match_the_current_workflow():
    text = (ROOT / generator.SOURCE).read_text()
    actions = text.split('Use ordinary `operation_submit`', 1)[1].split('##', 1)[0]
    assert '`pm.thread.create`: target `{workspace_id}`, params `{title, context?}`' in actions
    rename = actions.split('- `pm.thread.rename`:', 1)[1].split('\n- ', 1)[0]
    for hint in ('{workspace_id, thread_id}', '{title}', 'expected_version',
                 'PM_CONVERSATION_ADOPTION_IN_PROGRESS', '409'):
        assert hint in rename
    contract = (ROOT / 'docs/design/pm-overview-discussion-context.md').read_text()
    for code, status in (('PM_CONTEXT_SOURCE_UNAVAILABLE', '404'), ('PM_CONTEXT_INVALID', '422'),
                         ('PM_CONTEXT_TOO_LARGE', '413')):
        assert re.search(rf'{code}[^\n]*{status}', text)
        assert re.search(rf'{code}[^\n]*{status}', contract)
    assert 'workflow_version: "2026-10-16.1"' in text
    assert "pre-child guard pause puts the parent in `needs_attention`" not in text
    for code in (
        "PM_LIFECYCLE_STOPPED", "PM_LIFECYCLE_EPOCH_CHANGED", "PM_PACKAGE_SCOPE_CHANGED",
        "WORK_ITEM_CHANGED", "PROJECT_CHANGED", "PM_WORK_BINDING_CHANGED",
        "PM_PACKAGE_VERSION_CONFLICT", "WORK_ITEM_ARCHIVED", "PROJECT_ARCHIVED",
    ):
        assert code in text
    assert "stage `start_failed`" in text
    assert "PM_WORK_START_AUTHORITY_UNAVAILABLE" in text
    assert "uncertain child" in text
    assert "waiting in `needs_attention` is not rewritten" in text
