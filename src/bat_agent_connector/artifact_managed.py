"""Managed single-file capture and exact-revision review receipts; no execution effects."""

from __future__ import annotations

import json
import time

from . import artifact_capture as capture
from . import artifacts, checkpoints, registry, resource_policy, service
from .errors import BatError
from .operations import ActionDef, OperationError

EXECUTIONS = frozenset({"checkpoint.continue", "integration.handoff", "session.send", "session.start", "repository.continue"})


def install(ops):
    for action in (
        ActionDef("artifact.capture.managed", "manage", "Capture a managed execution's reviewed file",
                  run_capture, admit_capture, ("preview_id",), authorize_existing=capture._authorize_existing),
        ActionDef("artifact.accept", "approve", "Accept one exact managed artifact revision",
                  run_accept, admit_accept, ("artifact_id",), authorize_existing=authorize_accept),
    ):
        if action.name not in ops.actions:
            ops.register(action)


def _refuse(message="source lacks matching central accepted execution evidence"):
    raise OperationError("ARTIFACT_LINEAGE_UNPROVEN", message, 409)


def _selector(request):
    selected = {k: request[k] for k in ("execution_operation_id", "task_id", "command_id") if k in request}
    if (set(selected) not in ({"execution_operation_id"}, {"task_id", "command_id"})
            or any(not isinstance(v, str) or not v or len(v) > 256 for v in selected.values())):
        raise OperationError("INVALID_PARAMS", "select an execution_operation_id or task_id and command_id", 422)
    if "execution_operation_id" in selected and not artifacts.OPERATION_ID.fullmatch(selected["execution_operation_id"]):
        raise OperationError("INVALID_PARAMS", "execution_operation_id must be a full operation ID", 422)
    return selected


def _owner(ops, host, sid, entry):
    owners = ops.db.execute("SELECT task_id FROM tasks WHERE host=? AND (session_id=? OR reviewer_session_id=?)",
                            (host, sid, sid)).fetchall()
    ids = {row[0] for row in owners}
    if entry.get("task_id"):
        ids.add(entry["task_id"])
    if len(ids) > 1:
        _refuse("source has conflicting task ownership")
    return next(iter(ids), None)


def _command(ops, host, sid, entry, tid, cid):
    row = ops.db.execute("SELECT * FROM commands WHERE command_id=?", (cid,)).fetchone()
    task = ops.db.execute("SELECT * FROM tasks WHERE task_id=?", (tid,)).fetchone()
    accepted = ops.db.execute("""SELECT event_id,created_at FROM events WHERE task_id=?
        AND kind IN ('command_accepted','command_settled') AND json_extract(body,'$.command_id')=?
        ORDER BY event_id LIMIT 1""", (tid, cid)).fetchone()
    if (not row or not task or not accepted or row["task_id"] != tid or row["session_id"] != sid
            or row["kind"] != "send" or row["status"] not in {"accepted", "settled"}
            or task["host"] != host or task["session_id"] != sid
            or entry.get("task_id") != tid or entry.get("role") != "lead"
            or accepted["created_at"] < entry["created_at"]):
        _refuse()
    return {"task_id": tid, "command_id": cid, "message_id": row["message_id"],
            "command_created_at": row["created_at"], "accepted_event_id": accepted["event_id"],
            "accepted_at": accepted["created_at"], "payload_digest": artifacts.manifest_digest(json.loads(row["payload"]))}


def _step(ops, oid, name):
    row = ops.db.execute("SELECT request,response FROM operation_steps WHERE operation_id=? AND name=? AND status='succeeded'",
                         (oid, name)).fetchone()
    return (json.loads(row["request"]), json.loads(row["response"] or "{}")) if row else (None, None)


def lineage(ops, host, sid, entry, selector):
    owner = _owner(ops, host, sid, entry)
    if "task_id" in selector:
        if owner != selector["task_id"]:
            _refuse()
        return {"kind": "task_command", "host": host, "session_id": sid,
                **_command(ops, host, sid, entry, selector["task_id"], selector["command_id"])}
    oid = selector["execution_operation_id"]
    try:
        op = ops.get(oid)
    except OperationError:
        _refuse()
    if op["action"] not in EXECUTIONS or op["status"] != "succeeded":
        _refuse()
    refs, result = op.get("external_refs") or {}, op.get("result") or {}
    bound = refs.get("resolved_target") or op["target"]
    request, sent = _step(ops, oid, "send")
    if op["action"] == "session.send":
        target = {"host": bound.get("host") or refs.get("host"),
                  "session_id": bound.get("session_id") or refs.get("session_id")}
        if op["created_at"] < entry["created_at"]:
            _refuse("execution predates this session incarnation")
    else:
        target = result
        started, start_receipt = _step(ops, oid, "session.start")
        start_time = ops.db.execute("SELECT started_at,finished_at FROM operation_steps WHERE operation_id=? AND name='session.start'",
                                     (oid,)).fetchone()
        reservation, reserved = _step(ops, oid, "session.reserve") if op["action"] == "session.start" else (None, None)
        if op["action"] == "session.start":
            incarnation_matches = (entry.get("start_operation_id") == oid and reservation and reserved
                and reservation.get("session_id") == sid and reserved.get("session_id") == sid
                and reserved.get("created_at") == entry["created_at"])
        elif op["action"] == "repository.continue":
            from .repository_sync import carriers
            proof = next(carriers(ops, [op]), None)
            incarnation_matches = (proof and proof["carrier_proven"] and entry.get("start_operation_id") == oid
                and entry.get("repository_binding") == proof["binding_digest"]
                and entry.get("published_sha") == proof["source_sha"]
                and refs.get("repository_reservation") == {"session_id": sid, "created_at": entry["created_at"]})
        else:
            incarnation_matches = start_time and start_time["finished_at"] and start_time["started_at"] <= entry["created_at"] <= start_time["finished_at"]
        if (not start_time or not start_time["finished_at"] or not incarnation_matches
                or not started or not start_receipt or started.get("session_id") != sid
                or start_receipt.get("session_id") != sid or started.get("cwd") != entry.get("cwd")
                or start_receipt.get("cwd") != entry.get("cwd")):
            _refuse()
    if target.get("host") != host or target.get("session_id") != sid:
        _refuse()
    if op["action"] == "session.start" and (not start_receipt.get("started") or not request or not sent
            or request.get("message_id") != "batc-" + oid or sent.get("host") != host or sent.get("session_id") != sid):
        _refuse("initial prompt receipt does not match this standalone start")
    task_proof = {}
    if not owner and (refs.get("task_id") or bound.get("task_id")):
        _refuse("execution task ownership no longer matches")
    if owner:
        tid, cid = refs.get("task_id"), refs.get("command_id")
        if tid != owner or not isinstance(cid, str):
            _refuse("task execution requires its original accepted command")
        task_proof = _command(ops, host, sid, entry, tid, cid)
    if not task_proof and (not request or not sent or sent.get("accepted") is not True
                           or not request.get("message_id") or sent.get("message_id") != request["message_id"]):
        _refuse()
    return {"kind": "execution_operation", "execution_operation_id": oid, "action": op["action"],
            "host": host, "session_id": sid, "execution_created_at": op["created_at"],
            "execution_actor": op["actor"], "send_receipt_digest": artifacts.manifest_digest([request, sent]),
            **task_proof}


async def source(ops, host, sid, selector):
    if (not isinstance(host, str) or host not in ops.context["fleet"].config.hosts
            or not isinstance(sid, str) or not 6 <= len(sid) <= 256):
        raise OperationError("SOURCE_UNAVAILABLE", "use a configured host and full session ID", 409)
    adapter = ops.context["artifact_host"]
    if not adapter.available(host):
        raise OperationError("ARTIFACT_ADAPTER_UNAVAILABLE", "capture requires a configured SSH alias", 409)
    fleet = checkpoints._read_fleet(ops)
    c, hc = fleet.client(host), fleet.config.host(host)
    try:
        ws = await service._workspace(c)
        tabs = [t for t in ws.get("terminals", []) if isinstance(t, dict) and t.get("id") == sid]
        entry = registry.get(host, sid)
        if len(tabs) > 1 or not entry or not entry.get("created_at"):
            _refuse("source needs unambiguous managed creation evidence")
        # GUI registration is optional. A registry-backed headless session still
        # needs positive live metadata/root evidence below, never an inferred tab.
        tab = tabs[0] if tabs else {"id": sid, "_orchestrated": True, "agentPreset": entry.get("agent_preset")}
        cls = resource_policy.classify(hc, sid, terminal=tab, entries=registry.list_entries(host))
        if (cls.provenance != resource_policy.MANAGED or not cls.writable
                or entry.get("status") in registry.RETIRED or not tab.get("agentPreset")):
            _refuse("source is not a current managed session in an owned folder")
        proof = lineage(ops, host, sid, entry, selector)
        meta = await c.invoke("claude:get-session-meta", {"sessionId": sid})
        root = resource_policy.norm(meta.get("cwd")) if isinstance(meta, dict) else None
        repository = resource_policy.norm(await c.invoke("git:getRoot", {"cwd": root})) if root else None
        if (not root or root != cls.workdir or not repository or len(root.encode()) > 4096
                or len(repository.encode()) > 4096
                or (cls.workdir_owner == resource_policy.OWNER_CONNECTOR_WORKTREE and repository != root)
                or (cls.workdir_owner == resource_policy.OWNER_MANAGED_ROOT
                    and not resource_policy.in_managed_root(hc, repository))):
            raise OperationError("SOURCE_CHANGED", "managed source folder binding differs", 409)
        fields = ("created_at", "agent_preset", "cwd", "worktree_path", "branch", "task_id", "role",
                  "shares_worktree_with", "lead_session_id", "write_scope",
                  "controller_authority", "controller_incarnation")
        identity = {k: entry.get(k) for k in fields}
        current = registry.get(host, sid) or {}
        current_class = resource_policy.classify(hc, sid, terminal=tab, entries=registry.list_entries(host))
        if ({k: current.get(k) for k in fields} != identity or current.get("status") in registry.RETIRED
                or not current_class.writable or current_class.provenance != resource_policy.MANAGED
                or lineage(ops, host, sid, current, selector) != proof):
            raise OperationError("SOURCE_CHANGED", "managed execution binding changed while observed", 409)
    except BatError:
        raise OperationError("SOURCE_UNAVAILABLE", "managed source could not be observed", 409) from None
    settings = {"url": hc.url, "fingerprint": hc.fingerprint, "token_ref": hc.token_ref,
                "profile": hc.profile_id, "ssh_alias": adapter.aliases.get(host)}
    return {"host": host, "session_id": sid, "root": root, "repository_root": repository,
            "profile_id": hc.profile_id, "registry_identity": identity, "lineage": proof,
            "selector": selector, "provenance": resource_policy.MANAGED,
            "tab": {k: tab.get(k) for k in ("id", "workspaceId", "agentPreset", "sdkSessionId", "createdAt", "cwd", "worktreePath")},
            "runtime_identity": {k: meta.get(k) for k in ("sdkSessionId", "agentKind")},
            "configuration_binding": artifacts.manifest_digest(settings)}


def reader(selector):
    async def resolve(ops, host, sid):
        return await source(ops, host, sid, selector)
    return resolve


async def preview(ops, principal, request):
    if not principal.allows("observe"):
        raise OperationError("FORBIDDEN", "managed capture preview requires observe", 403)
    capture._principal(ops, principal)
    if not isinstance(request, dict) or set(request) - {"host", "session_id", "relative_path", "execution_operation_id", "task_id", "command_id"}:
        raise OperationError("INVALID_PARAMS", "use the managed capture source fields only", 422)
    if not {"host", "session_id", "relative_path"} <= set(request):
        raise OperationError("INVALID_PARAMS", "host, session_id and relative_path are required", 422)
    from .pm_source_lineage import require_document, require_session
    require_document(ops.db, principal, request)
    require_session(ops.db, principal, request["host"], request["session_id"])
    selector = _selector(request)
    path = capture._path(request["relative_path"])
    artifacts.safe_name(path.rsplit("/", 1)[-1])
    resolved = await source(ops, request["host"], request["session_id"], selector)
    require_document(ops.db, principal, resolved)
    return await capture.preview_source(ops, principal, resolved, path, source_reader=reader(selector))


def admit_capture(ops, principal, target, params, pre):
    return capture._admit(ops, principal, target, params, pre, provenance=resource_policy.MANAGED)


async def run_capture(ctx):
    document = (ctx.admission_binding or {}).get("capture") or {}
    selector = _selector((document.get("source") or {}).get("selector") or {})
    # The selector is nested in the accepted central source, never new client input.
    return await capture._run(ctx, source_reader=reader(selector), proof_kind="managed_capture")


def _accepted_source(ops, target, params):
    row = artifacts.get(ops.db, target["artifact_id"], target["revision"])
    if row["state"] != "ready" or row["digest"] != params["digest"]:
        raise OperationError("ARTIFACT_REVISION_MISMATCH", "review the exact ready revision and digest", 409)
    proof = row["source"]
    op = ops.get(row["operation_id"])
    doc = ((op.get("external_refs") or {}).get("admission_binding") or {}).get("capture")
    _, received = _step(ops, op["operation_id"], "capture.receive")
    if (op["action"] != "artifact.capture.managed" or op["status"] != "succeeded"
            or proof.get("kind") != "managed_capture" or not isinstance(doc, dict)
            or received != proof or proof.get("source") != doc.get("source")
            or proof.get("evidence") != doc.get("evidence")
            or proof.get("relative_path") != doc.get("relative_path")
            or proof.get("fingerprint") != artifacts.manifest_digest(doc)
            or proof["fingerprint"] != params["source_fingerprint"]
            or proof["evidence"]["digest"] != row["digest"]
            or proof["evidence"]["size_bytes"] != row["size_bytes"]
            or any((op.get("result") or {}).get(k) != row[k] for k in ("artifact_id", "revision", "digest"))):
        _refuse("acceptance requires the exact completed central managed capture receipt")
    return row, proof


def admit_accept(ops, principal, target, params, pre):
    if (set(target) != {"artifact_id", "revision"} or set(params) != {"digest", "source_fingerprint", "receipt"} or pre
            or type(target.get("revision")) is not int or not 1 <= target["revision"] <= 2**63 - 1
            or any(not isinstance(params[k], str) or not artifacts.DIGEST.fullmatch(params[k]) for k in ("digest", "source_fingerprint"))
            or not isinstance(params["receipt"], str) or not params["receipt"].strip() or len(params["receipt"]) > 2000):
        raise OperationError("INVALID_PARAMS", "accept an exact revision, digest, source fingerprint and review receipt", 422)
    try:
        params["receipt"].encode("utf-8")
    except UnicodeError:
        raise OperationError("INVALID_PARAMS", "review receipt must be valid UTF-8", 422) from None
    _accepted_source(ops, target, params)


def authorize_accept(ops, principal, op, verb):
    if not principal.allows("approve"):
        raise OperationError("FORBIDDEN", f"artifact acceptance {verb} requires approve", 403)


async def run_accept(ctx):
    def record():
        row, proof = _accepted_source(ctx.service, ctx.target, ctx.params)
        ctx.service.context["artifact_store"].read_content(row["artifact_id"], row["revision"])
        document = {"operation_id": ctx.operation_id, "actor": ctx.actor, "accepted_at": time.time(),
                    "artifact_id": row["artifact_id"], "revision": row["revision"], "digest": row["digest"],
                    "capture_operation_id": row["operation_id"], "source_fingerprint": proof["fingerprint"],
                    "lineage": proof["source"]["lineage"], "source_commit": proof["evidence"]["head_sha"],
                    "receipt": ctx.params["receipt"], "meaning": "artifact_revision_review"}
        ctx.service.db.execute("INSERT INTO artifact_acceptances(operation_id,artifact_id,revision,document) VALUES(?,?,?,?)",
                               (ctx.operation_id, row["artifact_id"], row["revision"], artifacts.canonical(document)))
        ctx.service.journal.api_event("artifact", row["artifact_id"], "artifact.accepted",
                                     {"operation_id": ctx.operation_id, "revision": row["revision"],
                                      "digest": row["digest"], "capture_operation_id": row["operation_id"]}, actor=ctx.actor)
        return document
    return ctx.effect("artifact.accept", record, request={**ctx.target, **ctx.params})
