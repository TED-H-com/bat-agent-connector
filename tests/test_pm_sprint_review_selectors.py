"""Central typed selector shape refusals agree across all operation transports."""

import copy

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from bat_agent_connector import controller_authority, mcp_server, pm_workspace
from bat_agent_connector.operations import OperationError
from tests import test_pm_sprint_review_decisions as decisions
from tests.test_api_v1 import daemon as daemon_fixture
from tests.test_api_v1 import http, token
from tests.test_api_v1 import served as served_fixture
from tests.test_pm_sprint_review import basis

ops, sprint_ops = decisions.ops, decisions.sprint_ops
daemon, served = daemon_fixture, served_fixture

BAD = [
    pytest.param([], id="list"),
    pytest.param({}, id="dict"),
    pytest.param(None, id="null"),
    pytest.param(1, id="number"),
    pytest.param("", id="empty"),
    pytest.param("x" * 257, id="oversize"),
    pytest.param("bad id", id="syntax"),
    pytest.param(True, id="boolean"),
]


async def requests(ops):
    target, fixed, proof = await decisions.decided(ops)
    source_op = ops.get(
        ops.db.execute("SELECT operation_id FROM operations WHERE action='pm.governance.record'").fetchone()[
            0
        ]
    )
    proposal = {
        key: copy.deepcopy(source_op[key])
        for key in ("action", "target", "params", "preconditions", "idempotency_key")
    }
    complete = decisions.m.request(
        ops,
        target,
        "complete",
        draft_revision_id=fixed["revision_id"],
        discussion_summary="Explicit direction",
        decision_ids=["retain_direction"],
        governance_decisions=[proof],
    )
    selected = basis()
    selected["results"] = [
        {
            "attempt_id": "att_" + "a" * 32,
            "result_id": "res_" + "a" * 32,
            "digest": "sha256:" + "a" * 64,
            "assessment_ref": {
                "review_id": "pmqr_" + "a" * 32,
                "operation_id": "op_" + "a" * 32,
                "version": 2,
                "document_digest": "a" * 64,
                "candidate_commit": "a" * 40,
            },
        }
    ]
    selected["feedback_items"] = [
        {"id": "note", "source_ref": {"kind": "meeting_note", "note_id": "pmmnote_" + "a" * 32}},
        {
            "id": "input",
            "source_ref": {
                "kind": "discussion_input",
                "thread_id": "pmt_" + "a" * 32,
                "input_id": "pmi_" + "a" * 32,
                "operation_id": "op_" + "a" * 32,
            },
        },
    ]
    selected["governance_revision_ids"] = ["pmgrev_" + "a" * 32]
    selected["follow_up_ref"] = {
        "intake_id": "pmfu_" + "a" * 32,
        "document_digest": "a" * 64,
        "issue_ids": ["pmfui_" + "a" * 32],
    }
    proposal_row = decisions.m.draft_params("review")["decisions"][0]
    proposal_row.update(owner_actor=decisions.OWNER.actor, follow_up_ids=["check_quality"])
    draft_params = decisions.m.draft_params(
        "review", review_basis=selected, decisions=[proposal_row], follow_ups=[decisions.m.follow_up()]
    )
    draft_params["output"]["feedback"] = []
    draft = decisions.m.request(ops, target, "draft", **draft_params)
    idea = await decisions.settle(
        ops,
        ops.create(
            decisions.OWNER,
            action="pm.thread.create",
            target={"workspace_id": target["workspace_id"]},
            params={"title": "Explicit next scope"},
            preconditions={
                "expected_version": pm_workspace.get_workspace(
                    ops.db, decisions.OWNER, target["workspace_id"]
                )["version"]
            },
            idempotency_key="selector-idea",
        )[0],
    )
    input_request = dict(
        action="pm.input.record",
        target={"workspace_id": target["workspace_id"], "thread_id": idea["result"]["thread_id"]},
        params=dict(
            slot="scope",
            value="Authored intent",
            status="unresolved",
            source_ref=dict(
                kind="sprint_review_decision",
                meeting_id=target["meeting_id"],
                completion_id="pmmc_" + "a" * 32,
                completion_digest="a" * 64,
                decision_id="retain_direction",
            ),
        ),
        preconditions={"expected_version": 1},
        idempotency_key="selector-input",
    )
    cases = []
    for field in ("meeting_id", "draft_revision_id", "draft_digest", "decision_id"):
        cases.append(("proposal." + field, proposal, ("params", "review_proposal_ref", field)))
    for field in ("decision_id", "revision_id", "document_digest"):
        cases.append(("proof." + field, complete, ("params", "governance_decisions", 0, field)))
    paths = [("results", 0, field) for field in ("attempt_id", "result_id", "digest")]
    paths += [
        ("results", 0, "assessment_ref", field)
        for field in ("review_id", "operation_id", "version", "document_digest", "candidate_commit")
    ]
    paths += [("feedback_items", 0, "id"), ("feedback_items", 0, "source_ref", "note_id")]
    paths += [
        ("feedback_items", 1, "source_ref", field) for field in ("thread_id", "input_id", "operation_id")
    ]
    paths += [
        ("governance_revision_ids", 0),
        ("follow_up_ref", "intake_id"),
        ("follow_up_ref", "document_digest"),
        ("follow_up_ref", "issue_ids", 0),
    ]
    cases += [
        ("basis." + ".".join(map(str, path)), draft, ("params", "review_basis", *path)) for path in paths
    ]
    cases += [
        ("decision.follow_up_id", draft, ("params", "decisions", 0, "follow_up_ids", 0)),
        ("decision.owner_actor", draft, ("params", "decisions", 0, "owner_actor")),
    ]
    cases += [
        ("input." + field, input_request, ("params", "source_ref", field))
        for field in ("meeting_id", "completion_id", "completion_digest", "decision_id")
    ]
    return cases


@pytest.mark.parametrize("bad", BAD)
@pytest.mark.parametrize("transport", ["service", "http", "rpc", "mcp"])
async def test_every_selector_refuses_bad_shapes_centrally(ops, served, monkeypatch, mock, transport, bad):
    d, port = served
    with controller_authority.use(controller_authority.binding(ops.db, decisions.OWNER), ops.journal):
        cases = await requests(ops)
    old_service = d.ops, d.journal
    d.ops, d.journal = ops, ops.journal
    fleet = None
    try:
        auth = token(d, decisions.OWNER.actor, "observe", "manage")
        monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
        monkeypatch.setenv("BATC_API_TOKEN", auth)
        server, fleet = mcp_server.build_server(d.fleet.config, principal_only=True)
        failures = []
        before = ops.db.total_changes, list(mock.invokes)
        for label, template, path in cases:
            if label == "decision.owner_actor" and bad == "bad id":
                continue  # Existing actor names are bounded text, not stable item keys.
            request = copy.deepcopy(template)
            parent = request
            for part in path[:-1]:
                parent = parent[part]
            parent[path[-1]] = copy.deepcopy(bad)
            request["idempotency_key"] = "malformed-" + transport + "-" + label
            try:
                if transport == "service":
                    ops.create(decisions.OWNER, **request)
                    observed = "accepted"
                elif transport == "http":
                    status, body = await http(
                        port,
                        "POST",
                        "/api/v1/operations",
                        tok=auth,
                        body={k: v for k, v in request.items() if k != "idempotency_key"},
                        headers={"Idempotency-Key": request["idempotency_key"]},
                    )
                    observed = (status, body.get("error", {}).get("code"))
                elif transport == "rpc":
                    status, body = await http(
                        port, "POST", "/rpc", tok=auth, body={"method": "op_submit", "params": request}
                    )
                    observed = (status, body.get("error"))
                else:
                    await server.call_tool("operation_submit", {**request, "wait_s": 0, "confirm": True})
                    observed = "accepted"
            except OperationError as exc:
                observed = (exc.status, exc.code)
            except ToolError as exc:
                observed = (
                    "INVALID_PARAMS" if "INVALID_PARAMS" in str(exc) else type(exc).__name__ + ": " + str(exc)
                )
            except Exception as exc:
                observed = type(exc).__name__
            expected = (
                "INVALID_PARAMS"
                if transport == "mcp"
                else (400 if transport == "rpc" else 422, "INVALID_PARAMS")
            )
            if observed != expected:
                failures.append((label, observed, expected))
        assert (ops.db.total_changes, list(mock.invokes)) == before
        assert not failures, failures
    finally:
        if fleet:
            await fleet.close()
        d.ops, d.journal = old_service


# Adapted from the independent review probe; no evidence-directory import.
@pytest.mark.parametrize("where", ["proposal", "completion"])
@pytest.mark.parametrize("bad", [[], {}])
async def test_malformed_typed_reference_returns_domain_refusal(ops, where, bad):
    target, fixed, proof = await decisions.decided(ops)
    if where == "proposal":
        op = ops.get(
            ops.db.execute(
                "SELECT operation_id FROM operations WHERE action='pm.governance.record'"
            ).fetchone()[0]
        )
        req = {
            k: copy.deepcopy(op[k])
            for k in ("action", "target", "params", "preconditions", "idempotency_key")
        }
        req["params"]["review_proposal_ref"]["draft_revision_id"] = bad
    else:
        req = decisions.m.request(
            ops,
            target,
            "complete",
            draft_revision_id=fixed["revision_id"],
            discussion_summary="Explicit direction",
            decision_ids=["retain_direction"],
            governance_decisions=[dict(proof, revision_id=bad)],
        )
    req["idempotency_key"] = "malformed-" + where
    with pytest.raises(OperationError):
        ops.create(decisions.OWNER, **req)


# Adapted from the independent review probe; no evidence-directory import.
@pytest.mark.parametrize("where", ["proposal", "completion"])
async def test_malformed_reference_http_refuses_as_client_error(ops, served, where):
    d, port = served
    with controller_authority.use(controller_authority.binding(ops.db, decisions.OWNER), ops.journal):
        target, fixed, proof = await decisions.decided(ops)
    if where == "proposal":
        op = ops.get(
            ops.db.execute(
                "SELECT operation_id FROM operations WHERE action='pm.governance.record'"
            ).fetchone()[0]
        )
        req = {
            k: copy.deepcopy(op[k])
            for k in ("action", "target", "params", "preconditions", "idempotency_key")
        }
        req["params"]["review_proposal_ref"]["draft_revision_id"] = []
    else:
        req = decisions.m.request(
            ops,
            target,
            "complete",
            draft_revision_id=fixed["revision_id"],
            discussion_summary="Explicit direction",
            decision_ids=["retain_direction"],
            governance_decisions=[dict(proof, revision_id=[])],
        )
    req["idempotency_key"] = "http-malformed-" + where
    original = d.ops, d.journal
    d.ops, d.journal = ops, ops.journal
    try:
        auth = token(d, decisions.OWNER.actor, "observe", "manage")
        status, body = await http(
            port,
            "POST",
            "/api/v1/operations",
            tok=auth,
            body={k: v for k, v in req.items() if k != "idempotency_key"},
            headers={"Idempotency-Key": req["idempotency_key"]},
        )
        print("HTTP_MALFORMED", where, status, body)
        assert status == 422 and body["error"]["code"] == "INVALID_PARAMS"
    finally:
        d.ops, d.journal = original


@pytest.mark.parametrize("bad", BAD)
async def test_fixed_result_helper_validates_all_selectors_before_lookup(ops, bad):
    from bat_agent_connector.pm_attempt_result_reads import read_fixed_receipt
    from tests.test_pm_sprint_review import prepared

    _, _, _, target, _ = await prepared(ops)
    before = ops.db.total_changes
    for offset in range(3):
        args = ["att_" + "a" * 32, "res_" + "a" * 32, "sha256:" + "a" * 64]
        args[offset] = bad
        with pytest.raises(OperationError) as error:
            read_fixed_receipt(ops, decisions.OWNER, target["workspace_id"], *args)
        assert (error.value.status, error.value.code) == (422, "INVALID_PARAMS")
    assert ops.db.total_changes == before


@pytest.mark.parametrize("transport", ["service", "http", "rpc", "mcp"])
async def test_missing_scope_precedes_malformed_review_selectors(ops, served, monkeypatch, transport):
    from bat_agent_connector.api_auth import Principal

    d, port = served
    with controller_authority.use(controller_authority.binding(ops.db, decisions.OWNER), ops.journal):
        _, request, path = (await requests(ops))[1]
    request = copy.deepcopy(request)
    parent = request
    for part in path[:-1]:
        parent = parent[part]
    parent[path[-1]] = []
    request["idempotency_key"] = "shape-without-manage"
    old_service = d.ops, d.journal
    d.ops, d.journal = ops, ops.journal
    fleet = None
    try:
        auth = token(d, decisions.OWNER.actor, "observe")
        if transport == "service":
            with pytest.raises(OperationError) as error:
                ops.create(Principal(decisions.OWNER.actor, frozenset({"observe"})), **request)
            assert (error.value.status, error.value.code) == (403, "FORBIDDEN")
        elif transport == "http":
            status, body = await http(
                port,
                "POST",
                "/api/v1/operations",
                tok=auth,
                body={k: v for k, v in request.items() if k != "idempotency_key"},
                headers={"Idempotency-Key": request["idempotency_key"]},
            )
            assert (status, body["error"]["code"]) == (403, "FORBIDDEN")
        elif transport == "rpc":
            status, body = await http(
                port, "POST", "/rpc", tok=auth, body={"method": "op_submit", "params": request}
            )
            assert (status, body["error"]) == (400, "FORBIDDEN")
        else:
            monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
            monkeypatch.setenv("BATC_API_TOKEN", auth)
            server, fleet = mcp_server.build_server(d.fleet.config, principal_only=True)
            with pytest.raises(ToolError, match="FORBIDDEN"):
                await server.call_tool("operation_submit", {**request, "wait_s": 0, "confirm": True})
    finally:
        if fleet:
            await fleet.close()
        d.ops, d.journal = old_service
