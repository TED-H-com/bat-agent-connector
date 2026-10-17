"""Original publication proof has one source-gated read contract in all clients."""
import copy
import json
import uuid

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from bat_agent_connector import mcp_server, pm_access, pm_reads, pm_source_lineage, pr_publication
from bat_agent_connector.operations import OperationError
from tests import test_pr_publication as base
from tests.test_api_v1 import http, token
from tests.test_pm_lifecycle import decide
from tests.test_pm_run_api import unpack
from tests.test_pm_work_attempt_result import (
    _arm_publication,
    _publish_request,
    _publish_rows,
    _result,
    _set_turn_messages,
    _settle_publish,
    _start_managed_attempt,
)
from tests.test_pm_work_attempt_result import start_ready as start_ready  # noqa: F401

world, pm, ready, fixed, delivery_ready = base.world, base.pm, base.ready, base.fixed, base.delivery_ready
pub = base.pub


async def test_publication_original_receipt_routes_capabilities_and_withdrawal(pub, monkeypatch):
    w, d = pub, pub['d']
    who = base.DELIVERER
    assert base.publication.ACTION in pm_source_lineage.DERIVED_ACTIONS
    done = await base.settle(w, base.create(w))
    assert done['status'] == 'succeeded', done
    oid, wid = done['operation_id'], w['workspace_id']
    auth = token(d, who.actor, *who.scopes)
    path = f'/api/v1/pm/workspaces/{wid}/pr-publications/{oid}'
    params = {'workspace_id': wid, 'publication_operation_id': oid}
    monkeypatch.setenv('BATC_TASK_URL', f"http://127.0.0.1:{w['port']}/rpc")
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    before = len(w['posts']), len(w['runner'].scripts), d.ops.db.total_changes
    try:
        tools = {tool.name: tool for tool in await server.list_tools()}
        assert tools['pm_pr_publication_get'].annotations.read_only_hint
        status, data = await http(w['port'], 'GET', path, tok=auth)
        assert status == 200 and data['creation_receipt']['native_pr_id'] == 801
        assert await d.call_api('pm_pr_publication_get', params, who) == data
        assert unpack(await server.call_tool('pm_pr_publication_get', params)) == data
        assert (len(w['posts']), len(w['runner'].scripts), d.ops.db.total_changes) == before
        assert (await http(w['port'], 'GET', path+'?refresh=true', tok=auth))[0] == 422
        for bad in (None, [], 1, 'op_invalid'):
            with pytest.raises(OperationError) as malformed:
                pm_reads.read(d.ops, who, 'pm_pr_publication_get', {**params, 'publication_operation_id': bad})
            assert malformed.value.status == 422
        for scopes, allowed in [(('observe',), False), (('observe', 'integrate'), False), (('observe', 'manage'), False),
                (('observe', 'manage', 'integrate'), True)]:
            status, caps = await http(w['port'], 'GET', '/api/v1/capabilities', tok=token(d, who.actor, *scopes))
            assert status == 200
            assert next(a['allowed'] for a in caps['actions'] if a['action'] == base.publication.ACTION) is allowed
        base.revoke(w)
        assert not pm_access.visible(d.ops.db, who, done)
        assert (await http(w['port'], 'GET', path, tok=auth))[0] == 404
        assert (await http(w['port'], 'GET', '/api/v1/operations/'+oid, tok=auth))[0] == 404
        with pytest.raises(ToolError, match='NOT_FOUND'):
            await server.call_tool('pm_pr_publication_get', params)
        status, events = await http(w['port'], 'GET', '/api/v1/events?after=0&limit=500', tok=auth)
        assert status == 200 and oid not in json.dumps(events)
    finally:
        await fleet.close()


def _deliverer_token(w):
    who = base.DELIVERER
    return token(w["d"], who.actor, *who.scopes)


async def test_publish_admission_refusal_is_flagged(pub):
    w = pub
    request = base.envelope(w)
    request["params"]["draft"] = "yes"
    status, payload = await http(w["port"], "POST", "/api/v1/operations", tok=_deliverer_token(w), body=request)
    assert status == 422, payload
    assert payload["error"]["code"] == "INVALID_PARAMS"
    assert payload["error"]["admission_refused"] is True


async def test_publish_reused_key_conflict_has_no_admission_refused_flag(pub):
    w = pub
    request = base.envelope(w)
    auth = _deliverer_token(w)
    first_status, first = await http(w["port"], "POST", "/api/v1/operations", tok=auth, body=request)
    assert first_status == 202 and first["created"] is True, first
    again = copy.deepcopy(request)
    again["params"] = {**again["params"], "public_title": "A different public title"}
    status, payload = await http(w["port"], "POST", "/api/v1/operations", tok=auth, body=again)
    assert status == 409, payload
    assert payload["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert "admission_refused" not in payload["error"]


async def test_publish_lifecycle_stop_refusal_is_flagged(pub):
    w = pub
    await decide(w["d"].ops, w["workspace_id"], who=base.DELIVERER)
    request = base.envelope(w)
    status, payload = await http(w["port"], "POST", "/api/v1/operations", tok=_deliverer_token(w), body=request)
    assert status == 409, payload
    assert payload["error"]["code"] == "PM_LIFECYCLE_STOPPED"
    assert payload["error"]["admission_refused"] is True


async def test_publish_round_trip_submits_the_result_entry(start_ready, mock, monkeypatch):
    from tests.test_checkpoints import git
    from tests.test_integration import commit

    w = start_ready
    att = await _start_managed_attempt(w, mock, key="e04-publish-round")  # gitleaks:allow (test idempotency key)
    worktree = att["worktree_path"]
    git(worktree, "config", "user.name", "fixture")
    git(worktree, "config", "user.email", "fixture@example.invalid")
    candidate = commit(worktree, "implementation.txt", "selected implementation\n", "Implement selected work")
    _set_turn_messages(
        mock,
        att["session_id"],
        att["turn_marker"],
        streaming=False,
        assistant_id="m-asst-round",
        assistant_text="The selected work is ready to publish.",
    )
    await w["d"].inventory._refresh_scheduled("h1")
    _arm_publication(w, monkeypatch)
    result = await _result(w, att)
    allowed, blocked = _publish_rows(result)
    assert blocked == [] and len(allowed) == 1
    entry = allowed[0]
    for typed in ("public_title", "public_body", "draft"):
        assert typed not in entry["prefilled_params"]
    assert entry["prefilled_params"]["candidate_sha"] == candidate
    request = _publish_request(
        entry,
        title="Selected implementation",
        body="Public text selected for this fixed implementation.",
        draft=True,
        key=uuid.uuid4().hex,
    )
    status, payload = await http(w["port"], "POST", "/api/v1/operations", tok=w["owner_token"], body=request)
    assert status == 202 and payload["created"] is True, payload
    done = await _settle_publish(w["d"].ops, payload["operation"]["operation_id"])
    assert done["status"] == "succeeded", done
    path = f"/api/v1/pm/workspaces/{att['workspace_id']}/pr-publications/{done['operation_id']}"
    read_status, proof = await http(w["port"], "GET", path, tok=w["owner_token"])
    assert read_status == 200, proof
    receipt = proof["creation_receipt"]
    assert receipt["candidate_sha"] == candidate
    assert receipt["target_verified"] is True
    assert receipt["base_ref"] == entry["prefilled_params"]["base_ref"]
    assert receipt["base_sha"] == entry["preconditions"]["expected_base_sha"]
    verified = pr_publication.require_creation_receipt(
        w["d"].ops,
        base.DELIVERER,
        done["operation_id"],
        child_operation_id=att["handoff_operation_id"],
        provider_origin=w["d"].ops.context["github"].cfg.api_url,
        repository_id=entry["preconditions"]["repository_id"],
    )
    assert verified["candidate_sha"] == candidate and verified["target_verified"] is True
