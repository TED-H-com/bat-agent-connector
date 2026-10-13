"""One real owned-Git plan adoption from a bounded mocked native conversation."""
from __future__ import annotations

import copy
import json
import uuid

import pytest

from bat_agent_connector import api_auth, pm_native_journal, pm_plans, pm_runs
from bat_agent_connector import pm_conversation_format as format_
from bat_agent_connector import pm_conversation_proposals as proposals
from bat_agent_connector.operations import OperationError
from tests import test_pm_native_operations as native
from tests import test_pm_plan_operations as plans
from tests.operation_helpers import settle_operations
from tests.test_checkpoints import git
from tests.test_pm_native_runtime import protocol

world = plans.world
pm = plans.pm
ready = plans.ready
OWNER = plans.OWNER


def envelope(ref):
    values = [("login", "scope", "付費會員可使用 Email 登入", "proposal"),
        ("login_test", "quality", "正確密碼能登入，錯誤密碼不能登入", "proposal"),
        ("recovery", "scope", "會員可使用忘記密碼功能", "proposal"),
        ("recovery_test", "quality", "重設連結只能使用一次，舊密碼隨即失效", "proposal"),
        ("referral", "scope", "推薦碼不在第一版", "excluded")]
    return {"version": 1, "title": "會員登入與密碼重設", "summary": "兩項需求與驗收條件；推薦碼排除。",
        "candidates": [{"key": key, "domain": domain, "text": text, "kind": kind, "source_refs": [ref]}
            for key, domain, text, kind in values],
        "requirements": [{"id": "member.login", "source_key": "login", "acceptance_keys": ["login_test"], "priority": "must", "depends_on": []},
            {"id": "member.recovery", "source_key": "recovery", "acceptance_keys": ["recovery_test"], "priority": "must", "depends_on": ["member.login"]}],
        "removed_requirement_ids": [], "questions": []}


@pytest.fixture
async def proposal_world(ready, monkeypatch):
    w, ops = ready, ready["d"].ops
    proposals.install(ops)
    version = ops.db.execute("PRAGMA user_version").fetchone()[0]
    proposals.install(ops)
    assert ops.db.execute("PRAGMA user_version").fetchone()[0] == version
    api_auth.issue(ops.db, OWNER.actor, list(OWNER.scopes))
    message = await plans.act(w, "pm.message.append", {"content": "會員用 Email 登入與忘記密碼。第一版不要推薦碼。請整理成計畫。"})
    settings, instances = {"raw_reply": json.dumps(envelope("message." + message["message_id"]), ensure_ascii=False)}, []
    def reply_protocol(prompt, client_id):
        events = protocol(prompt, client_id)
        events[2]["params"]["item"]["text"] = settings["raw_reply"]
        events[-1]["params"]["turn"]["items"][-1]["text"] = settings["raw_reply"]
        return events
    monkeypatch.setattr(native, "protocol", reply_protocol)
    ops.context["pm_native_runtime_factory"] = lambda *args, **kwargs: native.FakeRuntime(*args, **kwargs, settings=settings, instances=instances)
    ops.context["pm_native_process_inspector"] = lambda identity: {"state": "unknown", "process": identity, "proof": "identity_unavailable", "observed_at": 1.0}
    w.update(settings=settings, instances=instances, message=message)
    yield w
    assert all(task.done() for task in ops._active.values())


def propose_request(w):
    head = pm_plans.head(w["d"].ops.db, w["workspace_id"])
    return {"action": proposals.ACTION, "target": plans.target(w), "params": {"purpose": "整理這次討論成為可審閱的計畫更新"},
        "preconditions": {"expected_version": plans.version(w), "expected_plan_version": head["version"],
            "expected_accepted_revision_id": head["accepted_revision_id"]}, "idempotency_key": uuid.uuid4().hex}


async def proposed(w, *, adapter=None):
    ops = w["d"].ops
    request = propose_request(w)
    if adapter is not None:
        request['params']['adapter'] = adapter
    op = ops.create(OWNER, **request)[0]
    await settle_operations(ops)
    done = ops.get(op["operation_id"])
    assert done["status"] == "succeeded", done
    return proposals.get_proposal(ops, OWNER, w["workspace_id"], done["result"]["outcome"]["proposal_id"])["proposal"]


def adoption_request(w, proposal):
    document, context = proposal["document"], proposal["context"]
    return {"action": proposals.ADOPT, "target": plans.target(w), "params": {"proposal_id": proposal["proposal_id"],
        "rationale": "採用登入與忘記密碼，推薦碼留在第一版之外。", "review": {"title": document["title"],
            "rows": [{"key": row["key"], "text": row["text"], "include": row["kind"] != "excluded",
                "status": "not_applicable" if row["kind"] == "excluded" else "user_confirmed"} for row in document["candidates"]],
            "requirements": copy.deepcopy(document["requirements"]), "removed_requirement_ids": document["removed_requirement_ids"]}},
        "preconditions": {"expected_version": context["thread_version"], "expected_plan_version": context["plan_head"]["version"],
            "expected_accepted_revision_id": context["plan_head"]["accepted_revision_id"], "expected_proposal_digest": proposal["document_digest"]},
        "idempotency_key": uuid.uuid4().hex}


async def complete(w, op):
    ops = w["d"].ops
    for _ in range(30):
        ops.db.execute("UPDATE operations SET next_run_at=0 WHERE status IN ('waiting_external','waiting_checks')")
        await settle_operations(ops)
        done = ops.get(op["operation_id"])
        if done["status"] in {"succeeded", "failed", "cancelled", "needs_attention", "uncertain"}:
            return done
    pytest.fail("Original review did not settle")


@pytest.mark.parametrize('adapter', [None, pm_native_journal.ADAPTER])
async def test_conversation_one_review_adopts_actual_requirements_without_referral(proposal_world, adapter):
    w, ops = proposal_world, proposal_world["d"].ops
    before = git(w["plan"]["worktree_path"], "rev-parse", "HEAD")
    proposal = await proposed(w, adapter=adapter)
    assert proposal["validation"]["status"] == "format_valid"
    assert pm_plans.head(ops.db, w["workspace_id"])["accepted_revision_id"] is None
    request = adoption_request(w, proposal)
    request["params"]["review"]["rows"][0]["text"] += "，停用帳號不可登入"
    op = ops.create(OWNER, **request)[0]
    done = await complete(w, op)
    assert done["status"] == "succeeded", done
    accepted = pm_plans.accepted_plan(ops, OWNER, w["workspace_id"])["plan"]
    requirements = accepted["document"]["requirements"]
    assert [row["id"] for row in requirements] == ["member.login", "member.recovery"]
    assert requirements[1]["depends_on"] == ["member.login"]
    assert all(row["confirmed_by"] == OWNER.actor for row in accepted["document"]["selected_inputs"])
    assert "推薦碼" not in accepted["document"]["markdown"]
    assert git(w["plan"]["worktree_path"], "rev-parse", "HEAD") == done["result"]["commit_sha"] != before
    assert ops.create(OWNER, **request)[0]["operation_id"] == op["operation_id"]
    assert len(w["instances"]) == 1 and w["instances"][0].sends == 1
    assert ops.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
    assert ops.db.execute("SELECT COUNT(*) FROM pm_conversation_children WHERE parent_id=?", (op["operation_id"],)).fetchone()[0] == 8
    next_context = ops.create(OWNER, **propose_request(w))[0]["external_refs"]["admission_binding"]["conversation_proposal"]["context"]
    excluded = next(row for row in next_context["inputs"] if row.get("review_disposition", {}).get("candidate_key") == "referral")
    assert excluded["review_disposition"]["included"] is False
    assert excluded["review_disposition"]["original_kind"] == "excluded"


@pytest.mark.parametrize("change", ["invented_source", "duplicate_key", "execution_field"])
async def test_invalid_model_output_preserves_raw_and_never_adopts(proposal_world, change):
    w = proposal_world
    raw = w["settings"]["raw_reply"]
    doc = json.loads(raw)
    if change == "invented_source":
        doc["candidates"][0]["source_refs"] = ["message.foreign"]
    elif change == "execution_field":
        doc["execute"] = "launch someone else's session"
    raw = json.dumps(doc, ensure_ascii=False)
    if change == "duplicate_key":
        raw = raw.replace('"version": 1', '"version": 1, "version": 1')
    w["settings"]["raw_reply"] = raw
    proposal = await proposed(w)
    assert proposal["raw_reply"] == raw and proposal["document"] is None
    assert proposal["validation"]["status"] == "needs_review"
    assert pm_plans.head(w["d"].ops.db, w["workspace_id"])["accepted_revision_id"] is None
    run = pm_runs.get(w["d"].ops.db, proposal["run_id"])
    with pytest.raises(OperationError, match="PM_OUTPUT_DESTINATION_REFUSED"):
        pm_native_journal.import_reply(w["d"].ops.journal, run["run_id"], run["generation"])


async def test_larger_accepted_plan_is_never_silently_replaced_by_bounded_proposal(proposal_world):
    w, ops = proposal_world, proposal_world["d"].ops
    source = pm_plans.get_plan(ops, OWNER, w["workspace_id"], w["revision"]["revision_id"])["plan"]["document"]["selected_inputs"][0]["input_id"]
    revision = await plans.draft(w, requirements=[{"id": "member.requirement." + str(index),
        "source_input_id": source, "acceptance_input_ids": [source], "priority": "must", "depends_on": []}
        for index in range(21)])
    assert (await plans.adopt(w, revision))["status"] == "succeeded"
    before = git(w["plan"]["worktree_path"], "rev-parse", "HEAD")
    with pytest.raises(OperationError, match="PM_CONVERSATION_BASELINE_TOO_LARGE"):
        ops.create(OWNER, **propose_request(w))
    assert w["instances"] == []
    assert pm_plans.head(ops.db, w["workspace_id"])["accepted_revision_id"] == revision["revision_id"]
    assert git(w["plan"]["worktree_path"], "rev-parse", "HEAD") == before


async def test_conflicting_discussion_and_revoked_owner_stop_new_children(proposal_world):
    w, ops = proposal_world, proposal_world["d"].ops
    proposal = await proposed(w)
    request = adoption_request(w, proposal)
    op = ops.create(OWNER, **request)[0]
    api_auth.revoke(ops.db, OWNER.actor)
    done = await complete(w, op)
    assert done["status"] == "failed" and done["error_code"] == "PM_CONVERSATION_AUTHORITY_UNAVAILABLE", done
    assert ops.db.execute("SELECT COUNT(*) FROM pm_conversation_children").fetchone()[0] == 0
    await plans.act(w, "pm.message.append", {"content": "先暫停，登入範圍有新的決定。"})
    request["idempotency_key"] = uuid.uuid4().hex
    with pytest.raises(OperationError, match="VERSION_CONFLICT"):
        ops.create(OWNER, **request)


@pytest.mark.parametrize("stop", ["credential", "cancel", "discussion"])
async def test_queued_local_child_rechecks_whole_original_owner_decision(proposal_world, stop):
    w, ops = proposal_world, proposal_world["d"].ops
    proposal = await proposed(w)
    parent = ops.create(OWNER, **adoption_request(w, proposal))[0]
    await ops._execute(parent["operation_id"])
    child = ops.db.execute("SELECT operation_id FROM pm_conversation_children WHERE parent_id=?", (parent["operation_id"],)).fetchone()[0]
    before = ops.db.execute("SELECT COUNT(*) FROM pm_inputs").fetchone()[0]
    if stop == "credential":
        api_auth.revoke(ops.db, OWNER.actor)
    elif stop == "cancel":
        ops.cancel(OWNER, parent["operation_id"])
    else:
        note = ops.create(OWNER, action="pm.message.append", target=plans.target(w), params={"content": "A new direction"},
            preconditions={"expected_version": plans.version(w)}, idempotency_key=uuid.uuid4().hex)[0]
        await ops._execute(note["operation_id"])
    await ops._execute(child)
    assert ops.get(child)["status"] == "failed", ops.get(child)
    assert ops.db.execute("SELECT COUNT(*) FROM pm_inputs").fetchone()[0] == before
    assert not pm_plans.head(ops.db, w["workspace_id"])["accepted_revision_id"]


async def test_lost_git_ack_and_lost_parent_receipt_recover_original_adoption(proposal_world):
    w, ops = proposal_world, proposal_world["d"].ops
    proposal = await proposed(w)
    parent = ops.create(OWNER, **adoption_request(w, proposal))[0]
    w["runner"].after["pm-revision"] = plans.lose
    child = None
    for _ in range(12):
        await ops._execute(parent["operation_id"])
        rows = ops.db.execute("SELECT operation_id FROM pm_conversation_children WHERE parent_id=? ORDER BY ordinal", (parent["operation_id"],)).fetchall()
        child = ops.get(rows[-1][0])
        await ops._execute(child["operation_id"])
        child = ops.get(child["operation_id"])
        if child["action"] == "pm.plan.adopt":
            break
    assert child["status"] == "uncertain", child
    commit = git(w["plan"]["worktree_path"], "rev-parse", "HEAD")
    original_children = [row[0] for row in rows]
    assert pm_runs.get_by_operation(ops.db, child["operation_id"])["state"] not in pm_runs.TERMINAL
    # Recover the one original child without allowing the parent worker to run.
    await ops._execute(child["operation_id"])
    assert ops.get(child["operation_id"])["status"] == "succeeded", ops.get(child["operation_id"])
    assert ops.get(parent["operation_id"])["result"] is None
    api_auth.revoke(ops.db, OWNER.actor)
    await ops._execute(parent["operation_id"])
    done = ops.get(parent["operation_id"])
    assert done["status"] == "succeeded" and done["result"]["commit_sha"] == commit, done
    assert done["result"]["child_operation_ids"] == original_children
    assert w["runner"].phases.count("pm-revision") == 1
    assert git(w["plan"]["worktree_path"], "rev-list", "--count", w["sha"] + "..HEAD") == "1"


async def test_http_rpc_mcp_share_exact_read_contract_and_bounded_queries(proposal_world, monkeypatch):
    from bat_agent_connector import mcp_server
    from tests.test_api_v1 import http, token
    from tests.test_pm_run_api import unpack
    w, d = proposal_world, proposal_world["d"]
    proposal = await proposed(w)
    auth = token(d, OWNER.actor, "observe")
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{w['port']}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    root = f"/api/v1/pm/workspaces/{w['workspace_id']}/conversation-proposals"
    try:
        tools = {tool.name: tool for tool in await server.list_tools()}
        for method, values, suffix in [
            ("pm_conversation_proposals_list", {"thread_id": proposal["thread_id"], "limit": 1}, "?thread_id=" + proposal["thread_id"] + "&limit=1"),
            ("pm_conversation_proposal_get", {"proposal_id": proposal["proposal_id"]}, "/" + proposal["proposal_id"]),
        ]:
            assert tools[method].annotations.read_only_hint
            status, expected = await http(w["port"], "GET", root + suffix, tok=auth)
            assert status == 200, expected
            params = {"workspace_id": w["workspace_id"], **values}
            assert await d.call_api(method, params, OWNER) == expected
            assert unpack(await server.call_tool(method, params)) == expected
        for suffix in ["?actor=", "?after=0&after=1", "?after=9223372036854775808", "?after=-1", "?limit=101",
                "?thread_id=", "/" + proposal["proposal_id"] + "?limit=1"]:
            assert (await http(w["port"], "GET", root + suffix, tok=auth))[0] in {404, 422}, suffix
        assert (await http(w["port"], "GET", root + "?after=9223372036854775807", tok=auth))[0] == 200
        for value in (True, "0", None):
            with pytest.raises(OperationError):
                await d.call_api("pm_conversation_proposals_list", {"workspace_id": w["workspace_id"], "after": value}, OWNER)
        global_caps = (await http(w["port"], "GET", "/api/v1/capabilities", tok=auth))[1]
        assert global_caps["features"]["pm_workspaces"]["conversation_proposals"]["adoption_scopes"] == ["observe", "manage", "start"]
    finally:
        await fleet.close()


def test_removed_commitments_require_explicit_original_ids_and_cycles_are_rejected():
    doc = envelope("message.original")
    raw = json.dumps(doc)
    assert format_.validate(raw, {"message.original"}, ["member.old"])[1]["status"] == "needs_review"
    doc["removed_requirement_ids"] = ["member.old"]
    assert format_.validate(json.dumps(doc), {"message.original"}, ["member.old"])[1]["status"] == "format_valid"
    doc["requirements"][0]["depends_on"] = ["member.recovery"]
    assert format_.validate(json.dumps(doc), {"message.original"}, ["member.old"])[1]["status"] == "needs_review"


def assert_adoptability(w, proposal, expected, reason):
    ops = w['d'].ops
    view = proposals.get_proposal(ops, OWNER, w['workspace_id'], proposal['proposal_id'])['proposal']
    listed = proposals.list_proposals(ops, OWNER, w['workspace_id'])['proposals']
    row = next(item for item in listed if item['proposal_id'] == proposal['proposal_id'])
    for item in (view, row):
        assert item['adoptable'] is expected
        assert item['adoptable_reason'] == reason


@pytest.mark.parametrize('renames', [1, 105])
async def test_title_only_rename_preserves_proposal_adoption(proposal_world, renames):
    from bat_agent_connector import pm_overview
    w, ops = proposal_world, proposal_world['d'].ops
    proposal = await proposed(w)
    ref = {'kind': 'proposal', 'id': proposal['proposal_id']}
    assert_adoptability(w, proposal, True, None)
    for index in range(renames):
        await plans.act(w, 'pm.thread.rename', {'title': f'Owner discussion title / 討論名稱 {index}'})
    assert_adoptability(w, proposal, True, None)
    assert any(a['ref'] == ref for a in pm_overview.read(ops, OWNER, w['workspace_id'])['attention'])
    done = await complete(w, ops.create(OWNER, **adoption_request(w, proposal))[0])
    assert done['status'] == 'succeeded', done
    assert not any(a['ref'] == ref for a in pm_overview.read(ops, OWNER, w['workspace_id'])['attention'])
    assert pm_plans.accepted_plan(ops, OWNER, w['workspace_id'])['plan']['revision_id'] == done['result']['revision_id']


async def test_new_message_invalidates_proposal_and_pending_decision(proposal_world):
    from bat_agent_connector import pm_overview
    w, ops = proposal_world, proposal_world['d'].ops
    proposal = await proposed(w)
    await plans.act(w, 'pm.message.append', {'content': 'Also support enterprise identities.'})
    assert_adoptability(w, proposal, False, 'content_changed')
    with pytest.raises(OperationError, match='VERSION_CONFLICT'):
        ops.create(OWNER, **adoption_request(w, proposal))
    out = pm_overview.read(ops, OWNER, w['workspace_id'])
    assert not any(a['ref'] == {'kind': 'proposal', 'id': proposal['proposal_id']} for a in out['attention'])


async def test_stale_proposal_history_does_not_consume_pending_budget(proposal_world):
    from bat_agent_connector import pm_overview
    w, ops = proposal_world, proposal_world['d'].ops
    for _ in range(51):
        await proposed(w)
    await plans.act(w, 'pm.message.append', {'content': 'Clarification for the next proposal.'})
    current = await proposed(w)
    out = pm_overview.read(ops, OWNER, w['workspace_id'])
    assert out['coverage']['sources']['proposals']['complete']
    assert [a['ref']['id'] for a in out['attention'] if a['ref']['kind'] == 'proposal'] == [current['proposal_id']]
    assert out['attention_counts']['decision'] is not None


@pytest.mark.parametrize('status', [None, 409, 413])
async def test_overview_reads_stored_proposal_title_and_degrades_limits(proposal_world, monkeypatch, status):
    from bat_agent_connector import pm_overview
    w, ops = proposal_world, proposal_world['d'].ops
    proposal = await proposed(w)
    def full_read(*a, **kw):
        pytest.fail('Overview must not reconstruct a full proposal for its title')
    monkeypatch.setattr(proposals, 'get_proposal', full_read)
    if status:
        def limited(*a):
            raise OperationError('LIMIT', 'Fixture proposal source limit', status)
        monkeypatch.setattr(proposals, 'require_visible', limited)
    out = pm_overview.read(ops, OWNER, w['workspace_id'])
    if status:
        assert not out['coverage']['sources']['proposals']['complete']
        assert out['attention_counts']['decision'] is None
    else:
        item = next(a for a in out['attention'] if a['ref'] == {'kind': 'proposal', 'id': proposal['proposal_id']})
        assert item['title'] == proposal['document']['title']


async def test_bound_plan_source_reference_uses_revision_operation(proposal_world, monkeypatch):
    from tests.test_pm_thread_contexts import create
    w, ops = proposal_world, proposal_world['d'].ops
    rid = w['revision']['revision_id']
    primary = pm_plans.get_plan(ops, OWNER, w['workspace_id'], rid)['plan']['operation_id']
    actual = pm_plans.source_operation_ids
    def reordered(*a, **kw):
        return sorted(actual(*a, **kw), key=lambda oid: oid == primary)
    monkeypatch.setattr(pm_plans, 'source_operation_ids', reordered)
    refs = [{'kind': 'plan_revision', 'id': rid}]
    thread, _ = await create(ops, w['workspace_id'], refs, who=OWNER)
    env = propose_request(w)
    env['target']['thread_id'] = thread['result']['thread_id']
    env['preconditions']['expected_version'] = 1
    op = ops.create(OWNER, **env)[0]
    context = op['external_refs']['admission_binding']['conversation_proposal']['context']
    assert context['source_references']['context.0']['operation_id'] == primary
    assert primary in context['source_operation_ids']
    await complete(w, op)


async def test_runtime_imported_message_invalidates_pending_proposal(proposal_world):
    from bat_agent_connector import pm_overview
    w, ops = proposal_world, proposal_world['d'].ops
    proposal = await proposed(w)
    analysis = native.request(ops, plans.target(w), params={'purpose': 'Analyze the latest conversation'},
        version=plans.version(w), who=OWNER)
    done = await complete(w, analysis)
    assert done['status'] == 'succeeded', done
    assert done['result']['outcome']['message_id']
    await plans.act(w, 'pm.thread.rename', {'title': 'Analysis discussion / 分析討論'})
    with pytest.raises(OperationError, match='VERSION_CONFLICT'):
        ops.create(OWNER, **adoption_request(w, proposal))
    out = pm_overview.read(ops, OWNER, w['workspace_id'])
    assert not any(a['ref'] == {'kind': 'proposal', 'id': proposal['proposal_id']} for a in out['attention'])


async def test_rename_during_adoption_is_retryable_after_original_children_finish(proposal_world):
    from dataclasses import replace

    from bat_agent_connector import pm_conversation_adoption as adoption
    w, ops = proposal_world, proposal_world['d'].ops
    proposal = await proposed(w)
    original = ops.actions['pm.input.record']
    attempts = []
    key = uuid.uuid4().hex

    def rename_request():
        return dict(action='pm.thread.rename', target=plans.target(w),
            params={'title': 'Reviewed direction / 已審閱的方向'},
            preconditions={'expected_version': plans.version(w)}, idempotency_key=key)

    async def racing(ctx):
        if (ctx.op.get('external_refs') or {}).get(adoption.LINK) and not attempts:
            ordinal = ops.db.execute('SELECT ordinal FROM pm_conversation_children WHERE operation_id=?',
                (ctx.operation_id,)).fetchone()[0]
            if ordinal == 1:
                before = ops.db.total_changes
                try:
                    ops.create(OWNER, **rename_request())
                except OperationError as refusal:
                    attempts.append((refusal, ops.db.total_changes - before))
                else:
                    attempts.append((None, ops.db.total_changes - before))
        return await original.run(ctx)

    ops.actions['pm.input.record'] = replace(original, run=racing)
    done = await complete(w, ops.create(OWNER, **adoption_request(w, proposal))[0])
    assert attempts
    refusal, writes = attempts[0]
    assert refusal is not None
    assert refusal.code == 'PM_CONVERSATION_ADOPTION_IN_PROGRESS' and refusal.status == 409
    assert 'Adoption in progress. Try again in a moment.' in refusal.message
    assert '正在採用提案，請稍後再試。' in refusal.message
    assert writes == 0
    assert done['status'] == 'succeeded', done
    assert all(child['status'] == 'succeeded' for child in adoption._children(ops.db, done))
    assert pm_plans.head(ops.db, w['workspace_id'])['accepted_revision_id'] == done['result']['revision_id']
    renamed = await complete(w, ops.create(OWNER, **rename_request())[0])
    assert renamed['status'] == 'succeeded', renamed
    assert ops.create(OWNER, **{**rename_request(), 'preconditions': renamed['preconditions']})[0]['operation_id'] == renamed['operation_id']
