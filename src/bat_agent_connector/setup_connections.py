"""Principal-local connection projection and coordinated personal setup intents."""
from __future__ import annotations

import hashlib
import json
import re

from . import connection_handoff as handoff
from . import dashboard_sync, platform_files
from . import managed_setup as setup
from .connection_source_descriptor import build_source_descriptor
from .operations import ActionDef, NeedsAttention, OperationError, Uncertain, Wait

PARAMS = frozenset({"source", "source_reference", "remote_profile_id", "service_ref", "workspace_ref",
                    "label", "preferences", "server_configuration"})


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


def source_descriptor(ops, principal, source, *, expired=False):
    """Base 9fd10de3 has no discovery helper: rebuild from local D3 snapshot bytes.

    This is deliberately separate from discovery and never probes BAT. Callers
    pass only a selector; the server recomputes the immutable public descriptor.
    """
    if not isinstance(source, dict):
        raise OperationError("INVALID_PARAMS", "Choose a typed source selector", 422)
    kind = source.get("kind")
    daemon = ops.context["daemon"]
    try:
        if kind == "bat_profile" and set(source) == {"kind", "profile_reference"}:
            selected = setup._text(source["profile_reference"], "profile reference")
            document = _document(ops)
            directory = setup._profile_directory(document)
            index = platform_files.read_private(directory / "index.json", max_bytes=setup.MAX_CONFIG)
            credential = platform_files.read_private(directory / "remote-tokens.enc.json", max_bytes=setup.MAX_CONFIG)
            profiles, _ = setup._profiles(document)
            profile = next((row for row in profiles if row["id"] == selected and row["token_available"]), None)
            if (not profile or index != platform_files.read_private(directory / "index.json", max_bytes=setup.MAX_CONFIG)
                    or credential != platform_files.read_private(directory / "remote-tokens.enc.json", max_bytes=setup.MAX_CONFIG)):
                _changed()
            association = {key: profile[key] for key in ("url", "fingerprint")}
            state = {"directory": hashlib.sha256(str(directory.resolve()).encode()).hexdigest(),
                "index": hashlib.sha256(index).hexdigest(), "credential": hashlib.sha256(credential).hexdigest()}
        elif kind == "staged_secret" and set(source) == {"kind", "secret_ref", "url", "fingerprint"}:
            setup._installation(daemon, principal)
            setup._secret(daemon, source["secret_ref"], "bat", expired=expired)
            directory = setup._installation(daemon) / "config/setup-secrets"
            ref = source["secret_ref"]
            state = {"reference": handoff.digest(ref),
                "metadata": hashlib.sha256(platform_files.read_private(directory / (ref + ".json"), max_bytes=4096)).hexdigest(),
                "credential": hashlib.sha256(platform_files.read_private(directory / (ref + ".token"), max_bytes=4096)).hexdigest()}
            selected = None
            association = {"url": setup._url(source["url"]),
                "fingerprint": setup.normalize_fingerprint(setup._text(source["fingerprint"], "fingerprint", 200))}
        else:
            raise OperationError("INVALID_PARAMS", "Unsupported source selector fields", 422)
        return build_source_descriptor(kind=kind, profile_reference=selected, association=association,
            availability_reason="SOURCE_AVAILABLE", source_state=state)
    except (OSError, ValueError, setup.ConfigError):
        _changed("Source is unavailable or changed; review again")


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
    if action not in handoff.ACTIONS or not principal.allows("observe" if action == "connection.reference.apply" else "manage"):
        raise OperationError("FORBIDDEN", "Connection action requires its existing scope", 403)
    if action == "setup.connection.apply":
        setup._installation(ops.context["daemon"], principal)
    if set(target) not in ({"installation_ref"}, {"installation_ref", "connection_id"}):
        raise OperationError("INVALID_TARGET", "Use installation_ref and optional existing connection_id", 422)
    setup._text(target["installation_ref"], "installation reference", 128)
    if "connection_id" in target and (not isinstance(target["connection_id"], str) or not re.fullmatch(r"conn_[0-9a-f]{32}", target["connection_id"])):
        raise OperationError("INVALID_TARGET", "Use a server-created connection ID", 422)
    if set(params) - PARAMS or not (PARAMS - {"server_configuration"}) <= params.keys():
        raise OperationError("INVALID_PARAMS", "Use the bounded connection choices", 422)
    if (set(pre) not in ({"expected_revision"}, {"expected_revision", "review_digest"})
            or type(pre.get("expected_revision")) is not int or pre["expected_revision"] < 0):
        raise OperationError("PRECONDITION_REQUIRED", "Supply the connection's expected_revision (0 for create)", 422)
    setup._text(params["label"], "connection label", 100)
    setup._text(params["remote_profile_id"], "remote profile ID")
    prefs = params["preferences"]
    if (not isinstance(prefs, dict) or set(prefs) - {"favorite", "order"}
            or "favorite" in prefs and type(prefs["favorite"]) is not bool
            or "order" in prefs and (type(prefs["order"]) is not int or not 0 <= prefs["order"] <= 10000)):
        raise OperationError("INVALID_PARAMS", "Preferences accept favorite and order only", 422)
    identity = dashboard_sync.identity(ops.journal, principal)
    before = _row(ops, identity["principal_id"], target)
    if "connection_id" in target and not before:
        raise OperationError("NOT_FOUND", "Connection not found in this principal and installation", 404)
    if pre["expected_revision"] != (before["revision"] if before else 0):
        _changed("Connection revision changed; review again")
    descriptor = source_descriptor(ops, principal, params["source"])
    if params["source_reference"] != descriptor["reference"]:
        _changed("Source snapshot changed; review again")
    if before and (any(before[key] != descriptor[key] for key in
            ("reference", "source_digest", "kind", "policy_version", "profile_reference", "association"))
            or before["remote_profile_id"] != params["remote_profile_id"]):
        _changed("Endpoint, pin or remote profile changed; add a separate connection")
    configuration = params.get("server_configuration")
    config_binding, candidate = {}, None
    if "server_configuration" in params:
        if action != "setup.connection.apply":
            raise OperationError("FORBIDDEN", "Reference associations cannot change server configuration", 403)
        if not isinstance(configuration, dict) or set(configuration) != {"target", "params", "preconditions"}:
            raise OperationError("INVALID_PARAMS", "Use a typed setup.host configuration request", 422)
        config_binding = setup._admit("setup.host")(ops, principal,
            configuration["target"], configuration["params"], configuration["preconditions"])
        candidate = setup._candidate(ops.context["daemon"], "setup.host", configuration["target"], configuration["params"])[0]
        configured = candidate.host(configuration["target"]["host"])
        if ({"url": setup._url(configured.url), "fingerprint": configured.fingerprint} != descriptor["association"]
                or configured.profile_id != params["remote_profile_id"]
                or descriptor["kind"] == "bat_profile" and configuration["params"].get("import_profile_id") != descriptor["profile_reference"]
                or descriptor["kind"] == "staged_secret" and configuration["params"].get("secret_ref") != params["source"]["secret_ref"]):
            _changed("Server configuration must use the selected source and profile")
    _choices(ops, params, descriptor, candidate)
    binding = {**config_binding, **identity, "installation_ref": target["installation_ref"],
        "connection_id": target.get("connection_id"), "create_intent": None if before else handoff.digest([target, params]),
        "source_descriptor": descriptor}
    changes = {key: params[key] for key in ("label", "preferences", "service_ref", "workspace_ref", "remote_profile_id")}
    plan = {"contract_version": 1, "action": action, "target": target, "expected_revision": pre["expected_revision"],
        "source": descriptor, "before": {key: before[key] for key in changes} if before else None,
        "after": changes, "server_configuration": ({key: config_binding[key] for key in ("before_revision", "after_revision")} if configuration else None),
        "effects": {"configuration_written": False, "operation_created": False, "probe_started": False,
            "on_apply": (["guarded_configuration"] if configuration else []) + ["connection_binding"] +
                (["native_effects"] if action == "setup.connection.apply" else [])}}
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
    if operation["action"] == "setup.connection.apply":
        setup._installation(ops.context["daemon"], principal)
        if (verb == "cancel" and operation["status"] not in {"succeeded", "failed", "cancelled"}
                and (operation.get("external_refs") or {}).get("native_effects_sent")):
            row = ops.db.execute("SELECT response FROM operation_steps WHERE operation_id=? AND name=?",
                (operation["operation_id"], handoff.STEP)).fetchone()
            receipt = json.loads(row[0]) if row and row[0] else None
            if not receipt or receipt["outcome"] != "refused":
                raise OperationError("CONNECTION_EFFECT_PENDING", "Read back the dispatched native effect before cancelling", 409)


def _source_guard(ctx):
    if source_descriptor(ctx.service, None, ctx.params["source"], expired=True) != ctx.admission_binding["source_descriptor"]:
        _changed("Source changed before saving; review again")


def _save(ctx):
    binding = ctx.admission_binding
    if binding["server_id"] != dashboard_sync._metadata(ctx.service.journal)["server_id"]:
        raise NeedsAttention("CONNECTION_IDENTITY_CHANGED", "Original journal identity is unavailable")
    target = {**ctx.target, "connection_id": ctx.target.get("connection_id") or "conn_" + ctx.operation_id[3:]}
    before = _row(ctx.service, binding["principal_id"], target)
    if (before["revision"] if before else 0) != ctx.preconditions["expected_revision"]:
        _changed("Connection revision changed before saving; review again")
    descriptor = binding["source_descriptor"]
    _source_guard(ctx)
    _choices(ctx.service, ctx.params, descriptor)
    revision = ctx.preconditions["expected_revision"] + 1
    values = (revision, ctx.params["service_ref"], ctx.params["workspace_ref"], ctx.params["label"],
        handoff.canonical(ctx.params["preferences"]).decode(), ctx.operation_id)
    if before:
        ctx.service.db.execute("""UPDATE connection_bindings SET revision=?,service_ref=?,workspace_ref=?,
            label=?,preferences=?,operation_id=?,receipt_refs='[]'
            WHERE principal=? AND installation_ref=? AND connection_id=?""",
            (*values, binding["principal_id"], target["installation_ref"], target["connection_id"]))
    else:
        ctx.service.db.execute("""INSERT INTO connection_bindings(principal,installation_ref,connection_id,
            revision,state,reference,source_digest,kind,policy_version,profile_reference,association,
            remote_profile_id,service_ref,workspace_ref,label,preferences,operation_id,receipt_refs)
            VALUES(?,?,?,?,'active',?,?,?,?,?,?,?,?,?,?,?,?,'[]')""",
            (binding["principal_id"], target["installation_ref"], target["connection_id"], revision,
                *(descriptor[key] for key in ("reference", "source_digest", "kind", "policy_version", "profile_reference")),
                handoff.canonical(descriptor["association"]).decode(), ctx.params["remote_profile_id"], *values[1:]))
    ctx.service.journal.api_event("operation", ctx.operation_id, "connection.binding.saved",
        {"connection_id": target["connection_id"], "revision": revision}, actor=ctx.actor)
    return {"connection_id": target["connection_id"], "installation_ref": target["installation_ref"], "revision": revision}


async def _native(ctx):
    row = ctx.service.db.execute("SELECT * FROM operation_steps WHERE operation_id=? AND name=?",
        (ctx.operation_id, handoff.STEP)).fetchone()
    if not row:
        ctx.check_cancel()
        handoff.prepare(ctx)
        raise Wait("waiting_external", "Connector association saved; native connection receipt pending")
    request = json.loads(row["request"])
    if not row["response"]:
        if handoff.now() > request["expires_at"]:
            sent = (ctx.service.get(ctx.operation_id)["external_refs"] or {}).get("native_effects_sent")
            if sent:
                ctx.service._step_status(ctx.operation_id, handoff.STEP, "uncertain")
                raise Uncertain(handoff.STEP, "Dispatched native effect has no receipt; read back the original effect")
            raise NeedsAttention("CONNECTION_HANDOFF_EXPIRED", "Handoff expired before dispatch; no native effect was sent")
        raise Wait("waiting_external", "Waiting for the original native effect receipt")
    receipt = json.loads(row["response"])
    if receipt["outcome"] == "uncertain":
        ctx.service._step_status(ctx.operation_id, handoff.STEP, "uncertain")
        raise Uncertain(handoff.STEP, "Native participant reported a third value; never resend automatically")
    if receipt["outcome"] == "refused":
        ctx.service._step_status(ctx.operation_id, handoff.STEP, "failed")
        raise NeedsAttention("CONNECTION_NATIVE_REFUSED", "Native participant refused the reviewed local effect")
    if row["status"] != "succeeded":
        ctx.service._step_done(ctx.operation_id, handoff.STEP, receipt)
    return receipt


async def _run(ctx):
    if ctx.op["action"] == "setup.connection.apply":
        setup._installation(ctx.service.context["daemon"])
        setup._require_setup_identity(ctx.service.context["daemon"], ctx.admission_binding)
        configuration = ctx.params.get("server_configuration")
        if configuration:
            await setup._run(ctx, action="setup.host", target=configuration["target"], params=configuration["params"],
                source_guard=lambda: _source_guard(ctx))
    result = ctx.effect("connection_binding", lambda: _save(ctx), request={
        "installation_ref": ctx.target["installation_ref"], "expected_revision": ctx.preconditions["expected_revision"]})
    if ctx.op["action"] == "setup.connection.apply":
        try:
            receipt = await _native(ctx)
        except OSError:
            raise Uncertain(handoff.STEP, "Native receipt needs reconciliation under the original operation") from None
        def project_receipt():
            ctx.service.db.execute("""UPDATE connection_bindings SET receipt_refs=?
                WHERE principal=? AND installation_ref=? AND connection_id=? AND operation_id=?""",
                (json.dumps([receipt["receipt_ref"]]), ctx.admission_binding["principal_id"],
                    result["installation_ref"], result["connection_id"], ctx.operation_id))
            return {"receipt_ref": receipt["receipt_ref"]}
        try:
            result = {**result, **ctx.effect("connection_receipt", project_receipt, receipt_only=True)}
        except OSError:
            raise Uncertain(handoff.STEP, "Native receipt projection needs reconciliation") from None
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
