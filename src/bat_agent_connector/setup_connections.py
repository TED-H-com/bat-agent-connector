"""Principal-local connection projection and coordinated personal setup intents."""
from __future__ import annotations

import json
import re
import secrets
import sqlite3

from . import connection_handoff as handoff
from . import dashboard_sync, platform_files
from . import managed_setup as setup
from . import setup_source_discovery as discovery
from .connection_contract import CONTRACT, VERSION, invalid_plan, validate_native_plan
from .connection_source_descriptor import build_source_descriptor
from .operations import TERMINAL, ActionDef, NeedsAttention, OperationError, Uncertain, Wait

PARAMS = frozenset({"source", "source_reference", "remote_profile_id", "service_ref", "workspace_ref",
                    "label", "preferences", "server_configuration", "native_plan"})
CREATE_REQUIRED = frozenset({"source", "source_reference", "remote_profile_id", "service_ref", "workspace_ref", "label", "preferences"})


def _changed(message="Connection inputs changed; review again"):
    raise OperationError("SETUP_REVIEW_CHANGED", message, 409)


def _document(ops):
    daemon = ops.context["daemon"]
    if getattr(daemon, "managed_installation", None):
        return setup._configuration(daemon)[2]
    path = ops.context["fleet"].config.path
    if path:
        return setup.tomllib.loads(platform_files.read_private(path, max_bytes=setup.MAX_CONFIG).decode())
    return {}


def _source_params(source):
    if not isinstance(source, dict):
        raise OperationError("INVALID_PARAMS", "Choose a typed source selector", 422)
    if "source_digest" in source and source.get("kind") == "bat_profile":
        descriptor = _public_descriptor(source)
        source = {key: descriptor[key] for key in ("kind", "profile_reference")}
    if source.get("kind") == "bat_profile" and set(source) == {"kind", "profile_reference"}:
        return {"import_profile_id": source["profile_reference"]}
    if source.get("kind") == "staged_secret" and set(source) == {"kind", "secret_ref", "url", "fingerprint"}:
        return {key: source[key] for key in ("secret_ref", "url", "fingerprint")}
    raise OperationError("INVALID_PARAMS", "Unsupported source selector fields", 422)


def source_descriptor(ops, principal, source, *, expected_digest=None):
    """Adapt typed connection choices to the sole discovery source authority."""
    daemon = ops.context["daemon"]
    if principal is not None:
        setup._installation(daemon, principal)
    params = _source_params(source)
    document = _document(ops)
    if expected_digest is not None:
        return discovery.validate_source(daemon, document, {**params, "source_digest": expected_digest}, return_source=True)
    return discovery.resolve_source(daemon, document, params)[0]


def _public_descriptor(source):
    """A caller-held association claim, with no source resolution or permission grant."""
    keys = {"reference", "source_digest", "kind", "policy_version", "profile_reference", "association", "availability_reason"}
    try:
        if not isinstance(source, dict) or set(source) != keys:
            raise ValueError
        built = build_source_descriptor(**{key: source[key] for key in (
            "kind", "profile_reference", "association", "availability_reason")}, source_state={"claim": "0" * 64})
        if (type(source["policy_version"]) is not int or source["policy_version"] != built["policy_version"]
                or not isinstance(source["source_digest"], str) or not re.fullmatch(r"[0-9a-f]{64}", source["source_digest"])
                or source["reference"] != "source_" + source["source_digest"]):
            raise ValueError
    except (ValueError, TypeError, KeyError):
        raise OperationError("INVALID_PARAMS", "Use a bounded public source descriptor already held by the caller", 422) from None
    return json.loads(handoff.canonical(source))


def _saved_descriptor(before):
    return {**{key: before[key] for key in ("reference", "source_digest", "kind", "policy_version", "profile_reference", "association")},
        "availability_reason": "SOURCE_AVAILABLE"}


def _row(ops, principal_id, target):
    row = ops.db.execute("SELECT * FROM connection_bindings WHERE principal=? AND installation_ref=? AND connection_id=?",
        (principal_id, target["installation_ref"], target.get("connection_id"))).fetchone()
    return _decode(row) if row else None


def _decode(row):
    value = dict(row)
    for key in ("association", "preferences", "receipt_refs"):
        value[key] = json.loads(value[key])
    value.pop("principal")
    return value


def _require_connection_available(ops, principal_id, target):
    from .connection_dependencies import open_connection_operation
    row = open_connection_operation(ops, principal_id, target)
    if row:
        raise OperationError("CONNECTION_BUSY", f"Connection operation {row['operation_id']} is still open; finish or cancel it first / "
            f"連線操作 {row['operation_id']} 尚未完成；請先完成或取消該操作", 409)


def revision_matches(ops, operation):
    binding = operation["external_refs"]["admission_binding"]
    before = _row(ops, binding["principal_id"], operation["target"])
    return (before["revision"] if before else 0) == operation["preconditions"]["expected_revision"]


def _choices(ops, params, descriptor, config=None):
    service, workspace = params.get("service_ref"), params.get("workspace_ref")
    if service is not None:
        setup._text(service, "service reference")
        config = config or ops.context["fleet"].config
        host = config.hosts.get(service)
        if not host or {"url": setup._url(host.url), "fingerprint": host.fingerprint} != descriptor["association"] or host.profile_id != params["remote_profile_id"]:
            _changed("Select an already readable service matching this source and profile")
    if workspace is not None:
        setup._text(workspace, "workspace reference")
        inventory = ops.context["inventory"]
        row = ops.db.execute("SELECT body FROM discovery_latest WHERE host=? AND profile_id=? AND binding=?",
            (service, params["remote_profile_id"], inventory._binding(service))).fetchone() if service in inventory.config.hosts else None
        if not row or workspace not in json.loads(row[0]).get("coverage", {}).get("workspace_ids", []):
            _changed("Select a workspace already readable in this service's current observation")


def _plan(ops, principal, action, target, params, pre):
    if not isinstance(action, str) or action not in handoff.ACTIONS or not principal.allows(
            "observe" if action == "connection.reference.apply" else "manage"):
        raise OperationError("FORBIDDEN", "Connection action requires its existing scope", 403)
    personal = action == "setup.connection.apply"
    if personal:
        setup._installation(ops.context["daemon"], principal)
        if "native_plan" not in params:
            raise OperationError("NATIVE_REVIEW_REQUIRED", "Finish this connection review in the desktop app / "
                "請到桌面應用程式完成此連線檢閱", 409)
        validate_native_plan(params["native_plan"])
    elif "native_plan" in params:
        invalid_plan()
    if set(target) not in ({"installation_ref"}, {"installation_ref", "connection_id"}):
        raise OperationError("INVALID_TARGET", "Use installation_ref and optional existing connection_id", 422)
    setup._text(target["installation_ref"], "installation reference", 128)
    if "connection_id" in target and (not isinstance(target["connection_id"], str) or not re.fullmatch(r"conn_[0-9a-f]{32}", target["connection_id"])):
        raise OperationError("INVALID_TARGET", "Use a server-created connection ID", 422)
    if not params or set(params) - PARAMS:
        raise OperationError("INVALID_PARAMS", "Use the bounded connection choices", 422)
    if "source_reference" in params and (not isinstance(params["source_reference"], str)
            or not re.fullmatch(r"source_[0-9a-f]{64}", params["source_reference"])):
        raise OperationError("INVALID_PARAMS", "Use a bounded source reference", 422)
    if "server_configuration" in params and not personal:
        raise OperationError("FORBIDDEN", "Reference associations cannot change server configuration", 403)
    if (set(pre) not in ({"expected_revision"}, {"expected_revision", "review_digest"})
            or type(pre.get("expected_revision")) is not int or pre["expected_revision"] < 0):
        raise OperationError("PRECONDITION_REQUIRED", "Supply the connection's expected_revision (0 for create)", 422)
    identity = dashboard_sync.identity(ops.journal, principal)
    before = _row(ops, identity["principal_id"], target)
    if "connection_id" in target and not before:
        raise OperationError("NOT_FOUND", "Connection not found in this principal and installation", 404)
    if pre["expected_revision"] != (before["revision"] if before else 0):
        _changed("Connection revision changed; review again")
    _require_connection_available(ops, identity["principal_id"], target)
    fields = ("label", "preferences", "service_ref", "workspace_ref", "remote_profile_id")
    if not before and not CREATE_REQUIRED <= params.keys():
        raise OperationError("INVALID_PARAMS", "Create needs the full connection choices and a source", 422)
    changes = {key: params.get(key, before[key] if before else None) for key in fields}
    setup._text(changes["label"], "connection label", 100)
    setup._text(changes["remote_profile_id"], "remote profile ID")
    prefs = changes["preferences"]
    if (not isinstance(prefs, dict) or set(prefs) - {"favorite", "order"}
            or "favorite" in prefs and type(prefs["favorite"]) is not bool
            or "order" in prefs and (type(prefs["order"]) is not int or not 0 <= prefs["order"] <= 10000)):
        raise OperationError("INVALID_PARAMS", "Preferences accept favorite and order only", 422)
    if before and not personal:
        descriptor = _saved_descriptor(before)
        offered = params.get("source")
        if offered is not None:
            if not isinstance(offered, dict):
                raise OperationError("INVALID_PARAMS", "Use a typed source claim", 422)
            if "source_digest" in offered:
                association = _public_descriptor(offered)["association"]
            elif offered.get("kind") == "staged_secret" and set(offered) == {"kind", "secret_ref", "url", "fingerprint"}:
                if not isinstance(offered["secret_ref"], str) or not setup.SECRET_REF.fullmatch(offered["secret_ref"]):
                    raise OperationError("INVALID_PARAMS", "Use an opaque staged reference", 422)
                try:
                    association = {"url": setup._url(offered["url"]),
                        "fingerprint": setup.normalize_fingerprint(setup._text(offered["fingerprint"], "fingerprint", 200))}
                except setup.ConfigError:
                    raise OperationError("INVALID_PARAMS", "Use a bounded fingerprint", 422) from None
            elif offered.get("kind") == "bat_profile" and set(offered) == {"kind", "profile_reference"}:
                setup._text(offered["profile_reference"], "profile reference")
                association = descriptor["association"]  # A selector is not a request to re-resolve/rebind.
            else:
                raise OperationError("INVALID_PARAMS", "Use only the bounded public descriptor or source selector fields", 422)
            if association != descriptor["association"]:
                _changed("Endpoint or pin changed; add a separate connection")
    elif not personal and isinstance(params.get("source"), dict) and "source_digest" in params["source"]:
        descriptor = _public_descriptor(params["source"])
        if params.get("source_reference") != descriptor["reference"]:
            _changed()
    elif before and "source" not in params and "server_configuration" not in params:
        descriptor = _saved_descriptor(before)
    else:
        reference = params.get("source_reference")
        descriptor = source_descriptor(ops, principal, params.get("source"), expected_digest=reference[7:] if reference else None)
        if not secrets.compare_digest(reference or "", descriptor["reference"]):
            _changed("Source snapshot changed; review again")
    if before and (before["association"] != descriptor["association"] or before["remote_profile_id"] != changes["remote_profile_id"]):
        _changed("Endpoint, pin or remote profile changed; add a separate connection")
    configuration = params.get("server_configuration")
    config_binding, candidate, config_plan = {}, None, None
    if "server_configuration" in params:
        if not isinstance(configuration, dict) or set(configuration) != {"target", "params", "preconditions"}:
            raise OperationError("INVALID_PARAMS", "Use a typed setup.host configuration request", 422)
        config_binding = setup._admit("setup.host")(ops, principal,
            configuration["target"], configuration["params"], configuration["preconditions"])
        candidate = setup._candidate(ops.context["daemon"], "setup.host", configuration["target"], configuration["params"])[0]
        configured = candidate.host(configuration["target"]["host"])
        if ({"url": setup._url(configured.url), "fingerprint": configured.fingerprint} != descriptor["association"]
                or configured.profile_id != changes["remote_profile_id"]
                or descriptor["kind"] == "bat_profile" and configuration["params"].get("import_profile_id") != descriptor["profile_reference"]
                or descriptor["kind"] == "staged_secret" and configuration["params"].get("secret_ref") != params["source"]["secret_ref"]):
            _changed("Server configuration must use the selected source and profile")
        config_preview = setup.preview(ops.context["daemon"], principal, {"action": "setup.host",
            "target": configuration["target"], "params": configuration["params"],
            "preconditions": {"config_revision": config_binding["before_revision"]}})["preview"]
        config_plan = {"target": configuration["target"], "changes": config_preview["changes"],
            **{key: config_binding[key] for key in ("before_revision", "after_revision")}}
    _choices(ops, changes, descriptor, candidate)
    # Existing immutable provenance and receipt references survive metadata-only changes.
    binding = {**config_binding, **identity, "installation_ref": target["installation_ref"],
        "connection_id": target.get("connection_id"), "create_intent": None if before else handoff.digest([target, params]),
        "source_descriptor": descriptor, "projection": changes}
    if personal:
        binding["native_plan"] = params["native_plan"]
    plan = {"contract_version": VERSION, "action": action, "target": target, "expected_revision": pre["expected_revision"],
        "source": descriptor if personal else None, "before": {key: before[key] for key in fields} if before else None,
        "after": changes, "server_configuration": config_plan,
        "effects": {"configuration_written": False, "operation_created": False, "probe_started": False,
            "on_apply": (["guarded_configuration"] if configuration else []) + ["connection_binding"] +
                (["native_effects"] if personal else [])}}
    if personal:
        plan["native_plan"] = params["native_plan"]
    review = handoff.digest({**identity, "action": action, "target": target, "params": params,
        "expected_revision": pre["expected_revision"], "plan": plan})
    return {**binding, "review_digest": review}, {**plan, "review_digest": review}


def preview(ops, principal, body):
    if not isinstance(body, dict) or set(body) != {"action", "target", "params", "preconditions"} or any(
            not isinstance(body[key], dict) for key in ("target", "params", "preconditions")):
        raise OperationError("INVALID_PARAMS", "Preview accepts one typed connection intent", 422)
    return {"preview": _plan(ops, principal, body["action"], body["target"], body["params"], body["preconditions"])[1]}


def _admit(action):
    def admit(ops, principal, target, params, pre):
        binding, _ = _plan(ops, principal, action, target, params, pre)
        if pre.get("review_digest") != binding["review_digest"]:
            _changed()
        return binding
    return admit


def _authorize(ops, principal, operation, verb):
    if not handoff.visible(ops.db, principal, operation):
        raise OperationError("FORBIDDEN", "Connection belongs to the original effective principal", 403)
    _require_intent(operation)
    if operation["action"] == "setup.connection.apply":
        setup._installation(ops.context["daemon"], principal)
        if verb == "cancel" and operation["status"] not in TERMINAL:
            row = ops.db.execute("SELECT response FROM operation_steps WHERE operation_id=? AND name=?",
                (operation["operation_id"], handoff.STEP)).fetchone()
            history = handoff.receipts(row) if row else []
            receipt = history[-1] if history else None
            if receipt and receipt["outcome"] != "uncertain":
                if (operation["status"] == "needs_attention" and operation["error_code"] == "CONNECTION_PROJECTION_CHANGED"
                        and receipt["outcome"] in {"applied", "unchanged"}):
                    return  # This one recorded projection conflict has an explicit, evidence-preserving exit.
                message = ("Native participant refused without writing; the runner will finish this operation as failed / "
                    "本機參與者已拒絕且未寫入；執行程序將以失敗結束此操作") if receipt["outcome"] == "refused" else (
                    "Remaining native publication was abandoned; the runner will finish failed, retaining existing effects / "
                    "已放棄剩餘本機發布；執行程序將以失敗結束，保留既有效果") if receipt["outcome"] == "abandoned" else (
                    "Definitive native evidence is retained; complete this operation instead of abandoning it / "
                    "已保留本機確定回執；應完成此操作，不能放棄已證實的效果")
                raise OperationError("CONNECTION_EFFECT_PROVEN", message, 409)
            if (operation["status"] != "needs_attention"
                    and (operation.get("external_refs") or {}).get("native_effects_sent")):
                raise OperationError("CONNECTION_EFFECT_PENDING", "Read back the dispatched native effect before cancelling / "
                    "請先查回已派送的本機效果，再取消操作", 409)


def _require_intent(operation):
    binding = operation["external_refs"]["admission_binding"]
    target = operation["target"]
    if (binding.get("installation_ref") != target["installation_ref"]
            or binding.get("connection_id") != target.get("connection_id")
            or not target.get("connection_id") and binding.get("create_intent") != handoff.digest([target, operation["params"]])):
        raise OperationError("CONNECTION_INTENT_CHANGED", "The original connection intent binding changed", 409)


def abandon(ops, principal, operation):
    """Record one human decision, preserving all uncertain evidence and unrelated locks."""
    with ops.journal.tx():
        if ops._running(operation["operation_id"]):
            raise OperationError("CONNECTION_OPERATION_RUNNING", "Wait for the current runner to exit before cancelling / "
                "請等待目前的執行程序退出後再取消", 409)
        row = ops.db.execute("SELECT * FROM operation_steps WHERE operation_id=? AND name=?",
            (operation["operation_id"], handoff.STEP)).fetchone()
        history = handoff.receipts(row) if row else []
        if history and history[-1]["outcome"] != "uncertain":
            raise OperationError("CONNECTION_EFFECT_PROVEN", "A definitive native receipt cannot be abandoned / 不能放棄已有確定回執的本機效果", 409)
        if row:
            decision = {"code": "LOCAL_EFFECT_NEVER_PROVEN", "outcome": "local effect never proven", "actor": principal.actor}
            saved = {"receipts": handoff.receipts(row), "decisions": [decision]}
            ops.db.execute("UPDATE operation_steps SET response=? WHERE operation_id=? AND name=?",
                (handoff.canonical(saved).decode(), operation["operation_id"], handoff.STEP))
            ops._step_status(operation["operation_id"], handoff.STEP, "failed", error=decision)
            ops.journal.api_event("operation", operation["operation_id"], "connection.native.unknown_closed", decision, actor=principal.actor)
        ops.db.execute("UPDATE operations SET cancel_requested=1 WHERE operation_id=?", (operation["operation_id"],))
        return ops._transition(operation["operation_id"], "cancelled", actor=principal.actor,
            reason="Human closed this operation with unknown local result; local publication remains unresolved / "
                "人員以本機結果未知結束此操作；本機發布仍未釐清")


def cancel_projection_conflict(ops, principal, operation):
    """Close a proven local effect whose shared association lost its accepted revision."""
    with ops.journal.tx():
        if ops._running(operation["operation_id"]):
            raise OperationError("CONNECTION_OPERATION_RUNNING", "Wait for the current runner to exit before cancelling / "
                "請等待目前的執行程序退出後再取消", 409)
        current = ops.get(operation["operation_id"], steps=False)
        row = ops.db.execute("SELECT response FROM operation_steps WHERE operation_id=? AND name=?",
            (operation["operation_id"], handoff.STEP)).fetchone()
        history = handoff.receipts(row) if row else []
        if (current["status"] != "needs_attention" or current["error_code"] != "CONNECTION_PROJECTION_CHANGED"
                or not history or history[-1]["outcome"] not in {"applied", "unchanged"}):
            raise OperationError("CONNECTION_EFFECT_PROVEN", "Only the recorded projection conflict can be closed / "
                "僅能結束已記錄的關聯投影衝突", 409)
        outcome = history[-1]["outcome"]
        local = "Local configuration was written / 本機設定已寫入" if outcome == "applied" else (
            "Local configuration was already unchanged / 本機設定已符合檢閱內容，未另行寫入")
        message = local + "; shared association was not saved because it changed / 共用關聯已變更，因此未儲存"
        decision = {"code": "CONNECTION_PROJECTION_NOT_SAVED", "message": message,
            "native_outcome": outcome, "actor": principal.actor}
        ops._merge_refs(operation["operation_id"], {"projection_decision": decision})
        ops.journal.api_event("operation", operation["operation_id"], "connection.projection.cancelled", decision, actor=principal.actor)
        ops.db.execute("UPDATE operations SET cancel_requested=1 WHERE operation_id=?", (operation["operation_id"],))
        return ops._transition(operation["operation_id"], "cancelled", actor=principal.actor, reason=message)


def validate_operation_source(ops, operation):
    source = operation["params"].get("source")
    if source is not None:
        source_descriptor(ops, None, source,
            expected_digest=operation["external_refs"]["admission_binding"]["source_descriptor"]["source_digest"])


def _source_guard(ctx):
    validate_operation_source(ctx.service, ctx.op)


def _save(ctx):
    binding = ctx.admission_binding
    if binding["server_id"] != dashboard_sync._metadata(ctx.service.journal)["server_id"]:
        raise NeedsAttention("CONNECTION_IDENTITY_CHANGED", "Original journal identity is unavailable")
    target = {**ctx.target, "connection_id": ctx.target.get("connection_id") or "conn_" + ctx.operation_id[3:]}
    before = _row(ctx.service, binding["principal_id"], target)
    if (before["revision"] if before else 0) != ctx.preconditions["expected_revision"]:
        if ctx.op["action"] == "setup.connection.apply":
            raise NeedsAttention("CONNECTION_PROJECTION_CHANGED", "Native outcome is proven but the connection revision changed; reconcile the original operation / "
                "本機結果已證實，但連線版本已變更；請協調原操作的投影")
        _changed("Connection revision changed before saving; review again")
    descriptor = binding["source_descriptor"]
    choices = binding["projection"]
    if ctx.op["action"] == "connection.reference.apply":
        _choices(ctx.service, choices, descriptor)
    revision = ctx.preconditions["expected_revision"] + 1
    values = (revision, choices["service_ref"], choices["workspace_ref"], choices["label"],
        handoff.canonical(choices["preferences"]).decode(), ctx.operation_id)
    receipt_refs = list(before["receipt_refs"]) if before else []
    native = ctx.service.db.execute("SELECT response FROM operation_steps WHERE operation_id=? AND name=?",
        (ctx.operation_id, handoff.STEP)).fetchone()
    if native:
        receipt_refs.extend(item["receipt_ref"] for item in handoff.receipts(native) if item["receipt_ref"] not in receipt_refs)
    if before:
        ctx.service.db.execute("""UPDATE connection_bindings SET revision=?,service_ref=?,workspace_ref=?,
            label=?,preferences=?,operation_id=?,receipt_refs=?
            WHERE principal=? AND installation_ref=? AND connection_id=?""",
            (*values, json.dumps(receipt_refs), binding["principal_id"], target["installation_ref"], target["connection_id"]))
    else:
        ctx.service.db.execute("""INSERT INTO connection_bindings(principal,installation_ref,connection_id,
            revision,state,reference,source_digest,kind,policy_version,profile_reference,association,
            remote_profile_id,service_ref,workspace_ref,label,preferences,operation_id,receipt_refs)
            VALUES(?,?,?,?,'active',?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (binding["principal_id"], target["installation_ref"], target["connection_id"], revision,
                *(descriptor[key] for key in ("reference", "source_digest", "kind", "policy_version", "profile_reference")),
                handoff.canonical(descriptor["association"]).decode(), choices["remote_profile_id"], *values[1:], json.dumps(receipt_refs)))
    ctx.service.journal.api_event("operation", ctx.operation_id, "connection.binding.saved",
        {"connection_id": target["connection_id"], "revision": revision}, actor=ctx.actor)
    return {"connection_id": target["connection_id"], "installation_ref": target["installation_ref"], "revision": revision}


async def _native(ctx):
    row = ctx.service.db.execute("SELECT * FROM operation_steps WHERE operation_id=? AND name=?",
        (ctx.operation_id, handoff.STEP)).fetchone()
    if not row:
        ctx.check_cancel()
        handoff.prepare(ctx)
        raise Wait("waiting_external", "Connection intent accepted; waiting for native receipt before saving the association / 連線意圖已接受；等待本機回執後儲存關聯")
    request = json.loads(row["request"])
    history = handoff.receipts(row)
    if not history:
        if handoff.now() > request["expires_at"]:
            sent = (ctx.service.get(ctx.operation_id)["external_refs"] or {}).get("native_effects_sent")
            if sent:
                ctx.service._step_status(ctx.operation_id, handoff.STEP, "uncertain")
                raise Uncertain(handoff.STEP, "Dispatched native effect has no receipt; read back the original effect")
        raise Wait("waiting_external", "Waiting for the original native effect receipt before saving the association / 等待原本機效果回執後儲存關聯")
    receipt = history[-1]
    if receipt["outcome"] == "uncertain":
        ctx.service._step_status(ctx.operation_id, handoff.STEP, "uncertain")
        raise Uncertain(handoff.STEP, "Native participant reported a third value; never resend automatically")
    if receipt["outcome"] in {"refused", "abandoned"}:
        ctx.service._step_status(ctx.operation_id, handoff.STEP, "failed")
        raise OperationError(receipt["code"], CONTRACT["codes"][receipt["code"]]["reason"], 409)
    if row["status"] != "succeeded":
        ctx.service._step_done(ctx.operation_id, handoff.STEP, {"receipts": history})
    return receipt


async def _run(ctx):
    _require_intent(ctx.op)
    if ctx.op["action"] == "setup.connection.apply":
        setup._installation(ctx.service.context["daemon"])
        setup._require_setup_identity(ctx.service.context["daemon"], ctx.admission_binding)
        configuration = ctx.params.get("server_configuration")
        if configuration:
            await setup._run(ctx, action="setup.host", target=configuration["target"], params=configuration["params"],
                source_guard=lambda: _source_guard(ctx))
    if ctx.op["action"] == "setup.connection.apply":
        try:
            if not configuration and "source" in ctx.params and not ctx.service.db.execute(
                    "SELECT 1 FROM operation_steps WHERE operation_id=? AND name=?", (ctx.operation_id, handoff.STEP)).fetchone():
                _source_guard(ctx)
            receipt = await _native(ctx)
        except (OSError, sqlite3.Error):
            raise Uncertain(handoff.STEP, "Native receipt needs reconciliation under the original operation") from None
    # A successful operation and its projection become visible in the same commit.
    try:
        if ctx.op["action"] == "setup.connection.apply":
            # Keep proven local evidence linked even if the following projection transaction cannot commit.
            ctx.set_refs(connection_id=ctx.target.get("connection_id") or "conn_" + ctx.operation_id[3:],
                receipt_refs=[item["receipt_ref"] for item in handoff.receipts(ctx.service.db.execute(
                    "SELECT response FROM operation_steps WHERE operation_id=? AND name=?", (ctx.operation_id, handoff.STEP)).fetchone())])
        with ctx.service.journal.tx():
            result = ctx.effect("connection_binding", lambda: _save(ctx), request={
                "installation_ref": ctx.target["installation_ref"], "expected_revision": ctx.preconditions["expected_revision"]})
            if ctx.op["action"] == "setup.connection.apply":
                result = {**result, "receipt_ref": receipt["receipt_ref"]}
            ctx.service._transition(ctx.operation_id, "succeeded", result=result)
    except (OSError, sqlite3.Error):
        raise Uncertain("connection_binding", "Projection and success receipt need the original transaction replay") from None
    except OperationError as error:
        if ctx.op["action"] == "setup.connection.apply":
            raise NeedsAttention("CONNECTION_COMPLETION_BLOCKED", f"Proven native receipt retained; completion requires attention ({error.code}) / "
                "已保留確定的本機回執；完成原操作需要處理") from None
        raise
    return result


def list_connections(ops, principal, installation_ref, *, after=None, limit=50):
    setup._text(installation_ref, "installation reference", 128)
    if type(limit) is not int or not 1 <= limit <= 200 or after is not None and (
            not isinstance(after, str) or not re.fullmatch(r"conn_[0-9a-f]{32}", after)):
        raise OperationError("INVALID_PARAMS", "Use a connection cursor and limit 1-200", 422)
    identity = dashboard_sync.identity(ops.journal, principal)
    rows = ops.db.execute("""SELECT * FROM connection_bindings WHERE principal=? AND installation_ref=?
        AND connection_id>? ORDER BY connection_id LIMIT ?""",
        (identity["principal_id"], installation_ref, after or "", limit + 1)).fetchall()
    return {"connections": [_decode(row) for row in rows[:limit]],
        "next_after": rows[limit - 1]["connection_id"] if len(rows) > limit else None}


def install(ops):
    for name, scope in (("connection.reference.apply", "observe"), ("setup.connection.apply", "manage")):
        if name not in ops.actions:
            ops.register(ActionDef(name, scope, "Save a principal-local connection association" if scope == "observe" else
                "Coordinate personal Connector configuration and native connection evidence",
                _run, _admit(name), target_keys=("installation_ref",), authorize_existing=_authorize))
