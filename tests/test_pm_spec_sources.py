"""Fixed Git OpenSpec reads with mock BAT and disk-owned repositories only."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest

from bat_agent_connector import pm_spec_sources, repository_sync
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OperationError
from bat_agent_connector.pm_spec_source_format import describe_path, markdown_observations, schema_declaration
from tests.test_checkpoints import bat_writes, git, snapshot
from tests.test_pm_worktree import OWNER, submit, world  # noqa: F401 - fixture dependencies
from tests.test_pm_worktree import pm as pm_fixture
from tests.test_repository_sync import REQUEST

pm = pm_fixture

FILES = {
    "openspec/config.yaml": "schema: spec-driven\ncontext: |\n  Ideas remain candidates.\n",
    "openspec/specs/identity/login/spec.md": "# Login\n### Requirement: Sign in\nThe service SHALL accept a valid password.\n#### Scenario: Invalid password\nAccess is denied.\n",
    "openspec/changes/recovery/.openspec.yaml": "schema: spec-driven\ncreated: 2026-10-11\n",
    "openspec/changes/recovery/proposal.md": "# Proposal\nConsider recovery by email. This is not adopted.\n",
    "openspec/changes/recovery/design.md": "# Design\n## Open questions\nDelivery provider remains undecided.\n",
    "openspec/changes/recovery/tasks.md": "# Tasks\n- [x] 1.1 Add the form\n- [ ] 1.2 Verify delivery\n- [~] 1.3 Review failure handling\n",
    "openspec/changes/recovery/specs/identity/login/spec.md": "## ADDED Requirements\n### Requirement: Recover access\nEmail expiry remains a proposal.\n",
    "openspec/changes/archive/2026-10-01-first-login/tasks.md": "- [x] Form implemented\n- [ ] Actual behavior not verified\n",
}


async def prepare(w, files=None, *, executable_paths=()):
    for name, content in (FILES if files is None else files).items():
        dest = w["human"] / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, tuple):
            dest.symlink_to(content[0])
        else:
            dest.write_bytes(content if isinstance(content, bytes) else content.encode())
        if name in executable_paths:
            dest.chmod(0o755)
    git(w["human"], "add", "openspec")
    git(w["human"], "commit", "-qm", "Add synthetic specification artifacts")
    git(w["human"], "push", str(w["remote"]), "main")
    w["state"]["sha"] = w["sha"] = git(w["human"], "rev-parse", "HEAD")
    preview = await repository_sync.source_preview(w["d"].ops, REQUEST)
    w["selection"] = {**preview["target"], **{key: preview[key] for key in ("source_ref", "source_sha", "repository_id", "binding_digest")}}
    op = await submit(w)
    assert op["status"] == "succeeded", op
    return w["plan"]


async def read(w, **kwargs):
    return await pm_spec_sources.read_sources(w["d"].ops, kwargs.pop("principal", OWNER), w["workspace_id"],
        repository=kwargs.pop("repository", "o/r"), commit_sha=kwargs.pop("commit_sha", w["sha"]), **kwargs)


def carrier_state(plan):
    index = Path(git(plan["worktree_path"], "rev-parse", "--git-path", "index"))
    return {"refs": git(plan["clone_path"], "for-each-ref"), "worktrees": git(plan["clone_path"], "worktree", "list", "--porcelain"),
            "config": git(plan["clone_path"], "config", "--local", "--list"),
            "index": (hashlib.sha256(index.read_bytes()).hexdigest(), index.stat().st_mtime_ns)}


async def test_reads_exact_git_sources_with_stable_identity_without_cli_or_writes(pm, mock):
    plan = await prepare(pm)
    human_before = snapshot(pm["human"])
    clone_before = carrier_state(plan)
    events = pm["d"].journal.api_head()
    before_calls = len(pm["runner"].phases)
    first = await read(pm)
    second = await read(pm)
    assert first == second
    assert first["commit_sha"] == pm["sha"] and first["repository_id"] == plan["repository_id"]
    assert first["tree_sha"] == git(pm["human"], "rev-parse", "HEAD^{tree}")
    assert first["coverage"]["content_complete"] is True and first["schema"]["cli_version"] is None
    assert first["schema"]["validation"] == "not_run" and first["execution_authorized"] is False
    by_path = {doc["path"]: doc for doc in first["documents"]}
    for name, content in FILES.items():
        doc = by_path[name]
        assert doc["content"] == content and doc["sha256"] == hashlib.sha256(content.encode()).hexdigest()
        assert doc["source_status"] == "repository_document" and doc["accepted_scope"] == "unknown"
    main = by_path["openspec/specs/identity/login/spec.md"]
    delta = by_path["openspec/changes/recovery/specs/identity/login/spec.md"]
    assert main["role"] == "main_spec" and delta["role"] == "delta_spec"
    assert main["capability"] == delta["capability"] == "identity/login"
    archived = by_path["openspec/changes/archive/2026-10-01-first-login/tasks.md"]
    assert archived["location"] == "archive" and archived["change_key"] == "2026-10-01-first-login"
    assert archived["counts_as_done"] is False and archived["merged"] == archived["deployed"] == "unknown"
    reports = by_path["openspec/changes/recovery/tasks.md"]["observations"]["task_reports"]
    assert [row["reported_checked"] for row in reports] == [True, False, False]
    assert all(row["verification"] == "unknown" and row["counts_as_done"] is False for row in reports)
    assert all(row["locator"]["identity_scope"] == "fixed_artifact_revision" for row in reports)
    assert pm["runner"].phases[before_calls:] == ["read.spec-sources"] * 2
    assert pm["d"].journal.api_head() == events and not bat_writes(mock)
    assert snapshot(pm["human"]) == human_before and carrier_state(plan) == clone_before


async def test_dirty_human_and_pm_worktree_bytes_never_replace_fixed_git_content(pm):
    plan = await prepare(pm)
    path = "openspec/changes/recovery/proposal.md"
    (pm["human"] / path).write_text("Human draft must remain untouched")
    owned_path = Path(plan["worktree_path"], path)
    owned_path.write_text("Uncommitted PM draft must remain untouched")
    before = snapshot(pm["human"])
    first = await read(pm, paths=[path])
    assert first["documents"][0]["content"] == FILES[path]
    assert first["coverage"]["working_tree_included"] is False
    assert first["coverage"]["worktree_clean"] == "not_checked"
    assert (pm["human"] / path).read_text() == "Human draft must remain untouched"
    assert owned_path.read_text() == "Uncommitted PM draft must remain untouched"
    assert snapshot(pm["human"]) == before


async def test_executable_markdown_is_data_and_source_identity_is_revision_scoped(pm):
    path = "openspec/changes/recovery/design.md"
    plan = await prepare(pm, {path: "#!/bin/sh\nprintf should-not-run\n"}, executable_paths=[path])
    first = await read(pm, paths=[path])
    doc = first["documents"][0]
    assert doc["content"].startswith("#!/bin/sh") and doc["validation"] == "not_run"
    row = {"path": path, "kind": "blob", "mode": "100755", "blob_sha": doc["blob_sha"], "sha256": doc["sha256"],
           "size": doc["size"], "content": base64.b64encode(doc["content"].encode()).decode()}
    other = pm_spec_sources._document(plan, "f" * 40, row)
    assert other["file_id"] == doc["file_id"] and other["source_id"] != doc["source_id"]


async def test_moved_published_head_does_not_substitute_another_revision(pm):
    await prepare(pm)
    pm["state"]["sha"] = "f" * 40
    result = await read(pm)
    assert result["commit_sha"] == pm["sha"]


@pytest.mark.parametrize("paths", [["../secrets"], ["/openspec/config.yaml"], ["openspec/../config.yaml"],
    ["openspec//config.yaml"], ["openspec\\config.yaml"], ["openspec/config.yaml", "openspec/config.yaml"],
    ["openspec/changes/foo/hooks.py"], ["openspec/specs/../spec.md"], [], "openspec/config.yaml", [None]])
async def test_unsafe_or_unsupported_path_is_rejected_before_host_read(pm, paths):
    await prepare(pm)
    count = len(pm["runner"].phases)
    with pytest.raises(OperationError, match="supported OpenSpec"):
        await read(pm, paths=paths)
    assert len(pm["runner"].phases) == count


@pytest.mark.parametrize("overrides,code", [({"repository": "other/project"}, "PM_SPEC_REPOSITORY_UNBOUND"),
    ({"commit_sha": "f" * 40}, "PM_SPEC_COMMIT_UNBOUND"), ({"commit_sha": "HEAD"}, "INVALID_PARAMS"),
    ({"principal": Principal("reader", frozenset({"observe"}))}, "NOT_FOUND"),
    ({"principal": Principal(OWNER.actor, frozenset({"manage"}))}, "FORBIDDEN")])
async def test_exact_repository_commit_and_current_authority_are_required(pm, overrides, code):
    await prepare(pm)
    count = len(pm["runner"].phases)
    with pytest.raises(OperationError) as caught:
        await read(pm, **overrides)
    assert caught.value.code == code and len(pm["runner"].phases) == count


async def test_capability_is_cheap_and_no_carrier_is_an_explicit_limitation(pm):
    ops = pm["d"].ops
    result = pm_spec_sources.capabilities(ops, OWNER, pm["workspace_id"])
    assert result["eligible"] is False and result["reason"] == "PM_SPEC_BINDING_UNAVAILABLE"
    assert result["cli_required"] is False and result["git_verified"] is False
    with pytest.raises(OperationError) as caught:
        await read(pm)
    assert caught.value.code == "PM_SPEC_BINDING_UNAVAILABLE" and not pm["runner"].phases
    await prepare(pm)
    count = len(pm["runner"].phases)
    assert pm_spec_sources.capabilities(ops, OWNER, pm["workspace_id"])["eligible"] is True
    assert len(pm["runner"].phases) == count


@pytest.mark.parametrize("content,reason", [(b"\xff\xfe", "non_utf8_document"), (b"text\0bytes", "binary_document"),
    (b"x" * 128001, "document_too_large"), (("../../outside",), "symlink_or_nonregular_blob")])
async def test_unreadable_documents_are_explicit_not_empty_success(pm, content, reason):
    path = "openspec/changes/recovery/tasks.md"
    await prepare(pm, {path: content})
    result = await read(pm)
    assert result["coverage"]["content_complete"] is False
    assert result["documents"][0]["reason"] == reason
    assert result["documents"][0]["content"] is None
    assert result["documents"][0]["counts_as_done"] is False


async def test_missing_selected_path_is_visible_and_unsupported_layout_is_not_claimed(pm):
    await prepare(pm, {"openspec/custom-spec.txt": "Not automatically mapped", "openspec/config.yaml": "schema: custom\n"})
    result = await read(pm)
    assert result["coverage"]["ignored_paths"] == 1
    assert result["documents"][0]["schema_declaration"]["status"] == "custom_name_declared"
    assert result["schema"]["effective_schema"] == "unknown"
    selected = await read(pm, paths=["openspec/changes/absent/tasks.md"])
    assert selected["documents"][0]["reason"] == "missing_at_commit"
    assert selected["coverage"]["content_complete"] is False


async def test_total_content_budget_can_be_retried_with_a_narrower_selection(pm):
    files = {f"openspec/specs/capability-{index}/spec.md": "界" * 40000 for index in range(4)}
    await prepare(pm, files)
    result = await read(pm)
    assert sum(row["status"] == "read" for row in result["documents"]) == 3
    unread = next(row for row in result["documents"] if row["status"] != "read")
    assert unread["reason"] == "selection_byte_budget"
    narrow = await read(pm, paths=[unread["path"]])
    assert narrow["documents"][0]["content"] == files[unread["path"]]


@pytest.mark.parametrize("change", ["marker", "pin", "config_hook", "metadata_symlink"])
async def test_wrong_repository_or_missing_fixed_object_never_falls_back_to_worktree(pm, change):
    plan = await prepare(pm)
    clone = Path(plan["clone_path"])
    if change == "marker":
        git(clone, "config", "batc.repository", "other/project")
    elif change == "pin":
        git(clone, "update-ref", "-d", "refs/batc/published/" + plan["operation_id"][3:])
    elif change == "config_hook":
        git(clone, "config", "core.fsmonitor", "touch unsafe")
    else:
        (clone / ".git" / "untrusted-link").symlink_to(pm["human"])
    with pytest.raises(OperationError) as caught:
        await read(pm)
    assert caught.value.code in {"REPOSITORY_CLONE_NOT_OURS", "PM_SPEC_COMMIT_UNAVAILABLE", "REPOSITORY_CLONE_TAMPERED"}
    assert not (clone / "unsafe").exists()


async def test_revocation_during_async_read_discards_document_bytes(pm):
    await prepare(pm)
    def revoke(_):
        pm["d"].ops.db.execute("DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?", (pm["workspace_id"], OWNER.actor))
    pm["runner"].after["read.spec-sources"] = revoke
    with pytest.raises(OperationError) as caught:
        await read(pm)
    assert caught.value.code == "NOT_FOUND"


async def test_host_reply_identity_and_blob_digest_are_rechecked(pm):
    await prepare(pm)
    original = pm["runner"].run
    async def forged(host, script, timeout_s=None):
        result = json.loads(await original(host, script, timeout_s))
        result["result"]["files"][0]["sha256"] = "f" * 64
        return json.dumps(result)
    pm["runner"].run = forged
    with pytest.raises(OperationError) as caught:
        await read(pm)
    assert caught.value.code == "PM_SPEC_READ_INVALID"


async def test_no_openspec_root_is_an_empty_bounded_catalog_not_a_validation_success(pm):
    op = await submit(pm)
    assert op["status"] == "succeeded"
    result = await read(pm)
    assert result["documents"] == [] and result["schema"]["validation"] == "not_run"
    assert "implementation_verification" in result["unknowns"]
    missing = await read(pm, paths=["openspec/config.yaml"])
    assert missing["documents"][0]["reason"] == "missing_at_commit"


async def test_symlink_openspec_root_is_not_followed(pm):
    await prepare(pm, {"openspec": ("../outside",)})
    with pytest.raises(OperationError) as caught:
        await read(pm)
    assert caught.value.code == "PM_SPEC_ROOT_UNSAFE"


@pytest.mark.parametrize("value,status", [("schema: spec-driven\n", "standard_name_declared"),
    ("schema: 'custom-flow' # comment\n", "custom_name_declared"), ("schema: {nested: true}\n", "unsupported_yaml"),
    ("schema: spec-driven\nschema: other\n", "unsupported_yaml"), ("context: unknown\n", "not_identified")])
def test_schema_declaration_is_partial_and_never_claims_effective_validation(value, status):
    result = schema_declaration(value)
    assert result["status"] == status and result["effective_schema"] == "unknown"


def test_checkbox_observations_do_not_convert_examples_or_unknown_markers_to_done():
    result = markdown_observations("- [X] Reported\n  + [~] Still uncertain\n1. [] Not checked\n```md\n- [x] Example\n```\n- [link](somewhere)\n", tasks=True)
    assert [row["reported_checked"] for row in result["task_reports"]] == [True, False, False, True]
    assert result["task_reports"][-1]["in_fenced_block"] is True
    assert all(row["counts_as_done"] is False for row in result["task_reports"])
    assert result["status"] == "lexical_observation_only"


def test_structural_observations_are_bounded_and_layout_recognition_preserves_nested_capability():
    result = markdown_observations("- [x] unchecked claim\n" * 501, tasks=True)
    assert result["status"] == "observation_limit" and result["task_report_count"] is None
    assert describe_path("openspec/specs/identity/session/spec.md")["capability"] == "identity/session"
    assert describe_path("openspec/changes/archive/2026-01-01-login/specs/identity/session/spec.md")["location"] == "archive"
