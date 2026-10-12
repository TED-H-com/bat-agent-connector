"""Publication uses real disk Git and original PM assignments, never live providers."""
from __future__ import annotations

import asyncio
import copy
import json
import sqlite3
import uuid
from pathlib import Path
from urllib.parse import unquote

import pytest

from bat_agent_connector import api_auth, pm_source_lineage
from bat_agent_connector import pr_publication as publication
from bat_agent_connector import pr_publication_host as host
from bat_agent_connector.github import GitHubAmbiguous
from bat_agent_connector.operations import OperationError
from tests.test_checkpoints import git, snapshot
from tests.test_integration import commit
from tests.test_pm_delivery_lineage import (  # noqa: F401
    DELIVERER,
    fixed,
    pm,
    ready,
    settle,
    world,
)
from tests.test_pm_delivery_lineage import (
    delivery_ready as delivery_ready_fixture,
)
from tests.test_pm_lifecycle import decide
from tests.test_pm_package_handoff import revoke

delivery_ready = delivery_ready_fixture


@pytest.fixture
async def pub(delivery_ready, monkeypatch):
    w = delivery_ready
    ops = w["d"].ops
    publication.install(ops)
    api_auth.issue(ops.db, DELIVERER.actor, sorted(DELIVERER.scopes))
    monkeypatch.setattr(pm_source_lineage, "DERIVED_ACTIONS", pm_source_lineage.DERIVED_ACTIONS | {publication.ACTION})
    client = ops.context["github"]
    original_read = client.bounded_get
    w["posts"], w["post_payloads"] = [], []

    async def read(path, **kwargs):
        if "/git/ref/heads/" in path:
            ref = unquote(path.split("/git/ref/heads/", 1)[1])
            sha = git(w["remote"], "rev-parse", "refs/heads/" + ref)
            return {"status": 200, "payload": {"ref": "refs/heads/" + ref, "object": {"sha": sha}}, "metadata": {}}
        if path.endswith("/pulls"):
            return {"status": 200, "payload": copy.deepcopy(w["post_payloads"]), "metadata": {}}
        return await original_read(path, **kwargs)

    async def create(repository, **body):
        w["posts"].append({"repository": repository, **body})
        payload = {"id": 800 + len(w["posts"]), "node_id": "PR_fixture_" + str(len(w["posts"])),
            "number": 20 + len(w["posts"]), "head": {"ref": body["head"],
                "sha": git(w["remote"], "rev-parse", "refs/heads/" + body["head"]), "repo": {"id": w["gh"].repository_id}},
            "base": {"ref": body["base"], "sha": git(w["remote"], "rev-parse", "refs/heads/" + body["base"]),
                "repo": {"id": w["gh"].repository_id}}}
        if w.get("after_post"):
            w["after_post"](payload)
        w["post_payloads"].append(payload)
        if w.get("lost_post"):
            raise GitHubAmbiguous("Original 201 reply lost")
        return 201, payload

    monkeypatch.setattr(client, "bounded_get", read)
    monkeypatch.setattr(client, "create_pull", create)
    return w


def envelope(w):
    return {"action": publication.ACTION, "target": {"workspace_id": w["workspace_id"], "repository": "o/r",
        "host": "h1", "source_operation_id": w["original"]["operation_id"]},
        "params": {"candidate_sha": w["result_sha"], "base_ref": "main", "public_title": "Selected implementation",
            "public_body": "Public text selected for this fixed implementation.", "draft": True},
        "preconditions": {"repository_id": w["gh"].repository_id, "expected_base_sha": w["state"]["sha"]},
        "idempotency_key": uuid.uuid4().hex}


def create(w, request=None):
    return w["d"].ops.create(DELIVERER, **(request or envelope(w)))[0]


async def test_original_creation_receipt_binds_exact_owned_result_and_replays(pub):
    w, ops = pub, pub["d"].ops
    before = snapshot(w["human"])
    request = envelope(w)
    done = await settle(w, create(w, request))
    assert done["status"] == "succeeded", done
    receipt = publication.require_creation_receipt(ops, DELIVERER, done["operation_id"],
        child_operation_id=w["original"]["operation_id"], provider_origin=ops.context["github"].cfg.api_url,
        repository_id=w["gh"].repository_id)
    assert receipt["candidate_sha"] == w["result_sha"] and receipt["native_pr_id"] == 801
    assert receipt["base_ref"] == "main" and receipt["base_sha"] == w["state"]["sha"]
    assert receipt["child_request_digest"] == publication._child_digest(w["original"])
    assert receipt["repair_parent_id"] is None and receipt["target_verified"] is True
    assert git(w["remote"], "rev-parse", "refs/heads/" + receipt["branch_ref"]) == w["result_sha"]
    assert snapshot(w["human"]) == before
    assert create(w, request)["operation_id"] == done["operation_id"]
    assert len(w["posts"]) == 1 and w["runner"].ran["publication-push"] == 1
    assert publication.list_creation_receipts(ops, DELIVERER, None, w["original"]["operation_id"])["creation_receipts"][0]["receipt"] == receipt
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        ops.db.execute("DELETE FROM pr_creation_receipts")


async def test_unknown_post_matching_native_pr_is_never_adopted_or_resent(pub):
    w, ops = pub, pub["d"].ops
    w["lost_post"] = True
    request = envelope(w)
    done = await settle(w, create(w, request))
    assert done["status"] in {"uncertain", "needs_attention"}, done
    assert len(w["posts"]) == 1
    assert ops.db.execute("SELECT COUNT(*) FROM pr_creation_receipts").fetchone()[0] == 0
    assert publication.get_publication(ops, DELIVERER, done["operation_id"])["recovery"]["ownership_established"] is False
    assert create(w, request)["operation_id"] == done["operation_id"]
    with pytest.raises(OperationError, match="PR_CREATION_UNPROVEN"):
        publication.require_creation_receipt(ops, DELIVERER, done["operation_id"],
            child_operation_id=w["original"]["operation_id"], provider_origin=ops.context["github"].cfg.api_url,
            repository_id=w["gh"].repository_id)


async def test_source_withdrawal_blocks_new_effect_and_hides_completed_receipt(pub):
    w, ops = pub, pub["d"].ops
    done = await settle(w, create(w))
    assert done["status"] == "succeeded", done
    another = create(w)
    count = len(w["runner"].scripts)
    revoke(w)
    failed = await settle(w, another)
    assert failed["status"] == "failed", failed
    assert len(w["posts"]) == 1 and len(w["runner"].scripts) == count
    assert not publication.operation_visible(ops.db, DELIVERER, done)
    with pytest.raises(OperationError, match="NOT_FOUND"):
        publication.get_publication(ops, DELIVERER, done["operation_id"])


async def test_original_201_is_saved_after_source_withdrawal(pub):
    w, ops = pub, pub["d"].ops
    w["after_post"] = lambda _: revoke(w)
    done = await settle(w, create(w))
    assert done["status"] == "succeeded", done
    assert ops.db.execute("SELECT COUNT(*) FROM pr_creation_receipts").fetchone()[0] == 1
    assert not publication.operation_visible(ops.db, DELIVERER, done)


async def test_post_receipt_survives_crash_before_outer_step_completion(pub, monkeypatch):
    w, ops = pub, pub["d"].ops
    original = ops._step_done
    lost = []
    def finish(oid, name, response, **kwargs):
        if name == publication.POST and not lost:
            lost.append(True)
            raise asyncio.CancelledError("Process lost outer POST completion")
        return original(oid, name, response, **kwargs)
    monkeypatch.setattr(ops, "_step_done", finish)
    op = create(w)
    with pytest.raises(asyncio.CancelledError):
        await ops._execute(op["operation_id"])
    assert ops.get(op["operation_id"])["status"] == "running"
    done = await settle(w, op)
    assert done["status"] == "succeeded", done
    assert len(w["posts"]) == 1
    assert publication._raw_receipt(ops, done["operation_id"])["native_pr_id"] == 801
    assert ops.db.execute("SELECT status FROM operation_steps WHERE operation_id=? AND name=?",
        (done["operation_id"], publication.POST)).fetchone()[0] == "succeeded"


async def test_raced_201_keeps_native_identity_but_refuses_owned_target_proof(pub):
    w, ops = pub, pub["d"].ops
    w["after_post"] = lambda payload: payload["head"].update(sha=w["state"]["sha"])
    done = await settle(w, create(w))
    assert done["status"] == "needs_attention" and done["error_code"] == "PR_CREATED_TARGET_CHANGED", done
    saved = publication.get_publication(ops, DELIVERER, done["operation_id"])["creation_receipt"]
    assert saved["native_pr_id"] == 801 and not saved["target_verified"]
    with pytest.raises(OperationError, match="PR_CREATION_UNPROVEN"):
        publication.require_creation_receipt(ops, DELIVERER, done["operation_id"],
            child_operation_id=w["original"]["operation_id"], provider_origin=ops.context["github"].cfg.api_url,
            repository_id=w["gh"].repository_id)


async def test_lost_push_reply_uses_original_host_receipt_without_second_push(pub):
    w = pub
    w["runner"].lose_after.add("publication-push")
    done = await settle(w, create(w))
    assert done["status"] == "succeeded", done
    assert w["runner"].ran["publication-push"] == 1 and len(w["posts"]) == 1


@pytest.mark.parametrize('existing', ['ancestor', 'candidate'])
async def test_foreign_branch_race_cannot_fast_forward_or_create_pr(pub, existing):
    w = pub
    op = create(w)
    ref = "refs/heads/" + host.branch(op["operation_id"])
    foreign = w['state']['sha'] if existing == 'ancestor' else w['result_sha']
    if existing == 'candidate':
        # A separate publisher must have supplied the object before its racing
        # ref creation; fetching bytes here neither creates nor adopts that ref.
        git(w['remote'], 'fetch', '--no-tags', '--no-write-fetch-head',
            w['original']['result']['worktree_path'], foreign)
    w["runner"].inject["publication-push"] = f'git --git-dir="{w["remote"]}" update-ref {ref} {foreign}'
    done = await settle(w, op)
    if existing == 'ancestor':
        assert done["status"] == "failed" and done["error_code"] == "PR_BRANCH_CREATE_REFUSED", done
    else:
        # Git can report an already matching ref as up-to-date. That is no
        # original creation receipt; keep it unresolved without adopting it.
        assert done['status'] == 'needs_attention', done
        assert publication.get_publication(w['d'].ops, DELIVERER, op['operation_id'])['creation_receipt'] is None
    assert git(w["remote"], "rev-parse", ref) == foreign
    assert w['runner'].ran['publication-push'] == 1 and w["posts"] == []


async def test_live_credential_revocation_stops_unsent_branch(pub):
    w, ops = pub, pub["d"].ops
    op = create(w)
    ops.db.execute("UPDATE api_principals SET revoked_at=1 WHERE actor=?", (DELIVERER.actor,))
    done = await settle(w, op)
    assert done["status"] == "failed" and done["error_code"] == "PR_AUTHORITY_UNAVAILABLE", done
    assert w["runner"].ran["publication-push"] == 0 and w["posts"] == []


def test_caller_cannot_supply_owned_branch_or_repair_parent(pub):
    w = pub
    for key, value in (("branch_ref", "main"), ("repair_parent_id", "op_" + "a" * 32)):
        request = envelope(w)
        request["params"][key] = value
        with pytest.raises(OperationError, match="INVALID_PARAMS"):
            create(w, request)


async def test_matching_remote_without_original_push_receipt_remains_unresolved(pub):
    w = pub
    op = create(w)
    area = op["external_refs"]["admission_binding"]["pm_pr_publication"]["config"]["area"]
    w["runner"].after["publication-push"] = lambda: (Path(area) / "repo.git" / ("batc-pr-create-" + op["operation_id"])).unlink()
    w["runner"].lose_after.add("publication-push")
    done = await settle(w, op)
    assert done["status"] in {"uncertain", "needs_attention"}, done
    assert git(w["remote"], "rev-parse", "refs/heads/" + host.branch(op["operation_id"])) == w["result_sha"]
    assert w["runner"].ran["publication-push"] == 1 and w["posts"] == []


async def test_withdrawal_after_branch_creation_stops_new_post(pub):
    w = pub
    w["runner"].after["publication-push"] = lambda: revoke(w)
    done = await settle(w, create(w))
    assert done["status"] == "failed", done
    assert w["runner"].ran["publication-push"] == 1 and w["posts"] == []


async def test_receipt_remains_readable_after_workspace_pause(pub):
    w, ops = pub, pub["d"].ops
    done = await settle(w, create(w))
    assert done["status"] == "succeeded", done
    await decide(ops, w["workspace_id"], who=DELIVERER)
    assert publication.get_publication(ops, DELIVERER, done["operation_id"])["creation_receipt"]["native_pr_id"] == 801
    with pytest.raises(OperationError, match="PM_LIFECYCLE_STOPPED"):
        create(w)


async def test_complete_paths_preserve_control_characters_and_both_rename_sides(pub):
    w, ops = pub, pub["d"].ops
    wt = w["original"]["result"]["worktree_path"]
    w["result_sha"] = commit(wt, "line\nbreak.txt", "explicit path\n", "Add quoted filename")
    git(wt, "mv", "hello.txt", "renamed.txt")
    git(wt, "commit", "-qm", "Rename implementation")
    w["result_sha"] = git(wt, "rev-parse", "HEAD")
    done = await settle(w, create(w))
    assert done["status"] == "succeeded", done
    pinned = json.loads(ops.db.execute("SELECT response FROM operation_steps WHERE operation_id=? "
        "AND name='pr.publication.prepare'", (done["operation_id"],)).fetchone()[0])
    assert pinned["files_complete"] and {"line\nbreak.txt", "hello.txt", "renamed.txt"} <= set(pinned["files"])


async def test_source_tip_movement_refuses_publication_of_stale_candidate(pub):
    w = pub
    op = create(w)
    commit(w["original"]["result"]["worktree_path"], "newer.txt", "later unselected work\n", "Advance source")
    done = await settle(w, op)
    assert done["status"] == "failed" and done["error_code"] == "PR_SOURCE_MOVED", done
    assert w["runner"].ran["publication-push"] == 0 and w["posts"] == []
