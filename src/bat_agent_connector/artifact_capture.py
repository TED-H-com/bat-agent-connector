"""B1 manual single-file capture: stateless reviewed intent, existing durable artifact store."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time

from . import artifacts, checkpoints, dashboard_sync, registry, resource_policy, service
from .artifact_capture_helper import Refusal, relative_parts
from .errors import BatError
from .operations import RERUN, ActionDef, AmbiguousOutcome, Cancelled, OperationError

TTL_S = 600
MAX_TOKEN_BYTES = 24576
MAX_CONCURRENT_READS = 4
ERRORS = frozenset({"SOURCE_CHANGED", "SOURCE_UNAVAILABLE", "CAPTURE_FILE_UNSAFE", "ARTIFACT_TOO_LARGE",
                    "ARTIFACT_ADAPTER_UNAVAILABLE", "INVALID_CAPTURE_PATH"})


def install(ops, admin_token):
    ops.context["capture_key"] = hmac.digest(admin_token.encode(), b"batc.artifact.capture.v1", "sha256")
    ops.context.setdefault("capture_slots", asyncio.Semaphore(MAX_CONCURRENT_READS))
    if "artifact.capture" not in ops.actions:
        ops.register(ActionDef("artifact.capture", "manage", "Capture a reviewed manual-session file",
                               _run, _admit, ("preview_id",), authorize_existing=_authorize_existing))


def _bytes(value):
    return artifacts.canonical(value).encode()


def _b64(data):
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _principal(ops, principal):
    if not principal.credential_id:
        raise OperationError("FORBIDDEN", "capture requires an authenticated API credential", 403)
    return hmac.digest(ops.context["capture_key"], _bytes({
        "identity": dashboard_sync.identity(ops.journal, principal),
        "credential": principal.credential_id}), "sha256").hex()


def _path(value):
    try:
        return "/".join(relative_parts(value))
    except (Refusal, UnicodeError):
        raise OperationError("INVALID_CAPTURE_PATH", "choose one safe relative file path", 422) from None


async def _source(ops, host, sid):
    """Resolve a live manual tab without refreshing inventory or authorizing any write."""
    if (not isinstance(host, str) or host not in ops.context["fleet"].config.hosts
            or not isinstance(sid, str) or not 6 <= len(sid) <= 256):
        raise OperationError("SOURCE_UNAVAILABLE", "use a configured host and full session ID", 409)
    inventory = ops.context["inventory"]
    observed = inventory.get_session(host, sid)
    if not observed or observed.get("provenance") != resource_policy.MANUAL or not observed.get("first_seen_at"):
        raise OperationError("CAPTURE_SOURCE_NOT_MANUAL", "source needs observed manual-session evidence", 409)
    adapter = ops.context["artifact_host"]
    if not adapter.available(host):
        raise OperationError("ARTIFACT_ADAPTER_UNAVAILABLE", "capture requires a configured SSH alias", 409)
    fleet = checkpoints._read_fleet(ops)
    c, hc = fleet.client(host), fleet.config.host(host)
    try:
        ws = await service._workspace(c)
        tabs = [t for t in ws.get("terminals", []) if isinstance(t, dict) and t.get("id") == sid]
        if len(tabs) != 1 or not tabs[0].get("agentPreset"):
            raise OperationError("CAPTURE_SOURCE_NOT_MANUAL", "source needs one current manual agent tab", 409)
        tab = tabs[0]
        cls = resource_policy.classify(hc, sid, terminal=tab, entries=registry.list_entries(host))
        if cls.provenance != resource_policy.MANUAL:
            raise OperationError("CAPTURE_SOURCE_NOT_MANUAL", "source is no longer classified as manual", 409)
        meta = await c.invoke("claude:get-session-meta", {"sessionId": sid})
        root = resource_policy.norm((meta or {}).get("cwd") if isinstance(meta, dict) else None)
        root = root or resource_policy.norm(tab.get("worktreePath") or tab.get("cwd"))
        repository = resource_policy.norm(await c.invoke("git:getRoot", {"cwd": root})) if root else None
        if not root or not repository or len(root.encode()) > 4096 or len(repository.encode()) > 4096:
            raise OperationError("SOURCE_UNAVAILABLE", "source needs an observed repository folder", 409)
        # An await above may allow another coordinator to reserve/adopt this ID.
        if resource_policy.classify(hc, sid, terminal=tab, entries=registry.list_entries(host)).provenance != resource_policy.MANUAL:
            raise OperationError("CAPTURE_SOURCE_NOT_MANUAL", "source creation evidence changed", 409)
    except BatError:
        raise OperationError("SOURCE_UNAVAILABLE", "manual source could not be observed", 409) from None
    settings = {"url": hc.url, "fingerprint": hc.fingerprint, "token_ref": hc.token_ref,
                "profile": hc.profile_id, "ssh_alias": adapter.aliases.get(host)}
    return {"host": host, "session_id": sid, "root": root, "repository_root": repository,
            "profile_id": hc.profile_id, "first_seen_at": observed["first_seen_at"],
            "tab": {k: tab.get(k) for k in ("id", "workspaceId", "agentPreset", "sdkSessionId", "createdAt", "cwd", "worktreePath")},
            "configuration_binding": artifacts.manifest_digest(settings), "provenance": "manual"}


async def _read(ops, source, path, mode, expected=None, *, source_reader=None):
    source_reader = source_reader or _source
    request = {"mode": mode, "root": source["root"], "repository_root": source["repository_root"],
               "relative_path": path, "max_file_bytes": ops.context["artifact_store"].settings.max_file_bytes}
    if expected is not None:
        request["expected"] = expected
    if ops.context["capture_slots"].locked():
        raise OperationError("CAPTURE_BUSY", "capture read slots are busy; retry later", 409)
    async with ops.context["capture_slots"]:
        # Revalidate after waiting for a read slot, immediately before opening source bytes.
        if await source_reader(ops, source["host"], source["session_id"]) != source:
            raise OperationError("SOURCE_CHANGED", "source binding changed; preview again", 409)
        result, data = await ops.context["artifact_host"].capture(source["host"], request)
        if await source_reader(ops, source["host"], source["session_id"]) != source:
            raise OperationError("SOURCE_CHANGED", "source binding changed while read", 409)
    if result.get("ok") is not True:
        code = result.get("code") if result.get("code") in ERRORS else "SOURCE_UNAVAILABLE"
        raise OperationError(code, "capture refused; inspect the source and preview again", 409)
    evidence = result.get("evidence")
    if (not isinstance(evidence, dict) or type(evidence.get("size_bytes")) is not int
            or not 0 <= evidence["size_bytes"] <= request["max_file_bytes"]
            or not artifacts.DIGEST.fullmatch(str(evidence.get("digest")))
            or not checkpoints.SHA.fullmatch(str(evidence.get("head_sha")))
            or evidence.get("helper_version") != 1
            or not isinstance(evidence.get("root_identity"), dict) or not isinstance(evidence.get("file_identity"), dict)
            or len(_bytes(evidence)) > 4096):
        raise OperationError("SOURCE_UNAVAILABLE", "invalid source read evidence", 409)
    if (expected is not None and evidence != expected) or (mode == "capture" and
            (len(data) != evidence["size_bytes"] or hashlib.sha256(data).hexdigest() != evidence["digest"])):
        raise OperationError("SOURCE_CHANGED", "captured bytes differ from the reviewed evidence", 409)
    return evidence, data


async def preview(ops, principal, request):
    if not principal.allows("observe"):
        raise OperationError("FORBIDDEN", "capture preview requires observe", 403)
    _principal(ops, principal)
    if not isinstance(request, dict) or set(request) != {"host", "session_id", "relative_path"}:
        raise OperationError("INVALID_PARAMS", "preview needs host, session_id and relative_path only", 422)
    path = _path(request["relative_path"])
    artifacts.safe_name(path.rsplit("/", 1)[-1])
    source = await _source(ops, request["host"], request["session_id"])
    return await preview_source(ops, principal, source, path)


async def preview_source(ops, principal, source, path, *, source_reader=None):
    """Sign only a centrally resolved source; callers supply a fixed read-only resolver."""
    identity = _principal(ops, principal)
    from .pm_source_lineage import require_document
    require_document(ops.db, principal, source)
    try:
        evidence, _ = await _read(ops, source, path, "preview", source_reader=source_reader)
    except AmbiguousOutcome:
        raise OperationError("SOURCE_UNAVAILABLE", "source read did not complete; preview again", 409) from None
    require_document(ops.db, principal, source)
    document = {"source": source, "relative_path": path, "evidence": evidence}
    fingerprint = artifacts.manifest_digest(document)
    now = time.time()
    claims = {"document": document, "fingerprint": fingerprint, "principal": identity,
              "actor": principal.actor, "iat": now, "exp": now + TTL_S}
    raw = _bytes(claims)
    token = "cap1." + _b64(raw) + "." + _b64(hmac.digest(ops.context["capture_key"], b"cap1." + raw, "sha256"))
    if len(token) > MAX_TOKEN_BYTES:
        raise OperationError("SOURCE_UNAVAILABLE", "source identity exceeds the preview bound", 409)
    return {**document, "fingerprint": fingerprint, "preview_id": "acpv_" + hashlib.sha256(raw).hexdigest()[:32],
            "preview_token": token, "issued_at": now, "expires_at": now + TTL_S, "snapshot": False}


def _decode(ops, token):
    try:
        if not isinstance(token, str) or len(token) > MAX_TOKEN_BYTES:
            raise ValueError()
        version, raw, sig = token.split(".")
        body = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        expected = _b64(hmac.digest(ops.context["capture_key"], b"cap1." + body, "sha256"))
        if version != "cap1" or _b64(body) != raw or not hmac.compare_digest(expected, sig):
            raise ValueError()
        claims = json.loads(body)
        if _bytes(claims) != body or claims["exp"] != claims["iat"] + TTL_S:
            raise ValueError()
    except (ValueError, KeyError, TypeError, AttributeError):
        raise OperationError("PREVIEW_TOKEN_INVALID", "capture preview is invalid", 409) from None
    if claims["exp"] <= time.time():
        raise OperationError("PREVIEW_EXPIRED", "capture preview expired; preview again", 409)
    return claims, "acpv_" + hashlib.sha256(body).hexdigest()[:32]


def upload_params(document):
    evidence = document["evidence"]
    return {"display_name": document["relative_path"].rsplit("/", 1)[-1],
            "media_type": "application/octet-stream", "size_bytes": evidence["size_bytes"],
            "expected_digest": evidence["digest"]}


def _admit(ops, principal, target, params, pre, *, provenance="manual"):
    if not principal.allows("observe"):
        raise OperationError("FORBIDDEN", "capture requires manage and observe", 403)
    if set(target) != {"preview_id"} or set(params) != {"preview_token"} or set(pre) != {"expected_fingerprint"}:
        raise OperationError("INVALID_PARAMS", "use the fixed capture preview and fingerprint", 422)
    claims, preview_id = _decode(ops, params["preview_token"])
    if (claims["principal"] != _principal(ops, principal) or claims["actor"] != principal.actor
            or target["preview_id"] != preview_id or pre["expected_fingerprint"] != claims["fingerprint"]):
        raise OperationError("PREVIEW_MISMATCH", "preview identity or reviewed fingerprint differs", 409)
    if claims["document"]["source"].get("provenance") != provenance:
        raise OperationError("PREVIEW_MISMATCH", "preview source does not match this capture action", 409)
    ops.context["artifact_store"].validate_upload({}, upload_params(claims["document"]), {})
    return {"capture": claims["document"], "preview_id": preview_id, "fingerprint": claims["fingerprint"],
            "principal": claims["principal"]}


def _authorize_existing(ops, principal, op, verb):
    """Check current authority against accepted evidence without re-expiring or re-admitting intent."""
    if not principal.allows("manage") or not principal.allows("observe"):
        raise OperationError("FORBIDDEN", f"capture {verb} requires manage and observe", 403)
    binding = (op.get("external_refs") or {}).get("admission_binding") or {}
    if op["actor"] != principal.actor or binding.get("principal") != _principal(ops, principal):
        raise OperationError("FORBIDDEN", f"capture {verb} requires the original authenticated credential", 403)


async def _run(ctx, *, source_reader=None, proof_kind="manual_capture"):
    store = ctx.service.context["artifact_store"]
    binding = ctx.admission_binding
    if not binding or not isinstance(binding.get("capture"), dict):
        raise OperationError("PREVIEW_MISMATCH", "capture lacks central admission evidence", 409)
    document = binding["capture"]
    params = upload_params(document)

    async def reserve():
        return store.reserve_revision(ctx, {}, params, {})

    async def rerun(_request):
        return RERUN

    def row():
        return dict(store.db.execute("SELECT * FROM artifact_uploads WHERE operation_id=?", (ctx.operation_id,)).fetchone())

    async def received(_request):
        current = row()
        proof = store.db.execute("SELECT document FROM artifact_capture_sources WHERE operation_id=?", (ctx.operation_id,)).fetchone()
        if current["receive_state"] != "complete" or not proof:
            return RERUN  # no remote mutation; a new read must still match the original review
        path = store.root / "staging" / ctx.operation_id / f"a{current['attempt']:04d}" / "content"
        if store.published(current) is None:
            try:
                if artifacts._file_hash(path) != (params["size_bytes"], params["expected_digest"]):
                    raise OperationError("ARTIFACT_CONTENT_UNAVAILABLE", "capture staging differs from its receipt", 409)
            except FileNotFoundError:
                raise OperationError("ARTIFACT_CONTENT_UNAVAILABLE", "capture staging is unavailable", 409) from None
        return json.loads(proof[0])

    async def receive():
        evidence, data = await _read(ctx.service, document["source"], document["relative_path"],
                                     "capture", document["evidence"], source_reader=source_reader)
        from .pm_source_lineage import require_new_effect
        require_new_effect(ctx.service, ctx.operation_id)
        try:
            ctx.check_cancel()
        except Cancelled:
            return {"captured": False, "cancelled_before_staging": True}
        proof = {"kind": proof_kind, "operation_id": ctx.operation_id, "actor": ctx.actor,
                 "preview_id": binding["preview_id"], "fingerprint": binding["fingerprint"],
                 "source": document["source"], "relative_path": document["relative_path"],
                 "evidence": evidence, "captured_at": time.time(), "snapshot": False}
        # Receipt before staging completion; complete bytes + this proof are both needed on recovery.
        store.db.execute("INSERT OR REPLACE INTO artifact_capture_sources(operation_id,document) VALUES(?,?)",
                         (ctx.operation_id, artifacts.canonical(proof)))
        stream = asyncio.StreamReader()
        stream.feed_data(data)
        stream.feed_eof()
        try:
            await store.receive_reserved(ctx.operation_id, row(), stream, len(data), ctx.actor)
        except OperationError as exc:
            if exc.code != "UPLOAD_EXPIRED" or not ctx.service.get(ctx.operation_id, steps=False)["cancel_requested"]:
                raise
            return {"captured": False, "cancelled_during_staging": True}
        return proof

    try:
        reserved = await ctx.step("capture.reserve", reserve, reconcile=rerun)
        ctx.set_refs(artifact_id=reserved["artifact_id"], revision=reserved["revision"])
        receipt = await ctx.step("capture.receive", receive, reconcile=received)
        if receipt.get("captured") is False:
            ctx.check_cancel()
        current = row()
        return await artifacts.finish_revision(ctx, current, existing=store.published(current), event="artifact.captured")
    finally:
        asyncio.get_running_loop().call_soon(store.schedule_reap, ctx.operation_id)
