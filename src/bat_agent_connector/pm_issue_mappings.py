"""Publish explicitly selected text for fixed work, preserving external authors.

One Connector work item maps to one Issue in one numeric repository, independent
of engineering task order. Revisions are new comments, never destructive PATCHes.
All writes use the existing operation journal; an unknown POST is never resent.
"""
from __future__ import annotations

import copy
import json
import re
import time

from . import pm_plans, pm_sprints, pm_workspace, repository_sync
from . import pm_work_packages as packages
from .github import GitHubAmbiguous
from .operations import RERUN, TERMINAL, ActionDef, Cancelled, OperationError, OperationService, Wait

PUBLISH = "pm.issue.publish"
CANCEL = "pm.issue.cancel"
ACTIONS = {PUBLISH, CANCEL}
MAPPING = re.compile(r"pmim_[0-9a-f]{32}")
REVISION = re.compile(r"pmir_[0-9a-f]{32}")
MAX_REVISIONS = 256
MAX_READ_BYTES = 2_000_000
MAX_PAGES = 20
MAX_PREFLIGHTS = 64
EXTERNAL_STEP = "pm.issue.publish.external"
RECORD_STEP = "pm.issue.record"


def _bad(code, message, status=409):
    return OperationError(code, message, status)


def _exact(value, required, optional=()):
    if not isinstance(value, dict) or set(required) - value.keys() or value.keys() - set(required) - set(optional):
        raise _bad("INVALID_PARAMS", "Supply exactly the issue mapping fields", 422)


def _text(value, field, limit):
    value = pm_plans._text(value, field, limit)
    if "<!-- connector-pm-issue:" in value:
        raise _bad("INVALID_PARAMS", "The publication marker namespace is reserved", 422)
    return value


def _checked(value):
    if packages._size(value) > MAX_READ_BYTES:
        raise _bad("PM_ISSUE_READ_TOO_LARGE", "The complete issue mapping exceeds the read limit", 413)
    return value


def schema(journal):
    packages.schema(journal)
    with journal.tx():
        journal.db.execute("""CREATE TABLE IF NOT EXISTS pm_issue_revisions (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, revision_id TEXT NOT NULL UNIQUE,
            mapping_id TEXT NOT NULL, workspace_id TEXT NOT NULL, work_item_id TEXT NOT NULL,
            previous_revision_id TEXT UNIQUE, document TEXT NOT NULL, document_digest TEXT NOT NULL,
            operation_id TEXT NOT NULL UNIQUE, created_at REAL NOT NULL, actor TEXT NOT NULL)""")
        journal.db.execute("CREATE INDEX IF NOT EXISTS pm_issue_mapping_history ON pm_issue_revisions(workspace_id,mapping_id,sequence)")
        # A reservation is not a publication fact. It remains held while any
        # original POST may have run, including a crashed worker's started step.
        journal.db.execute("""CREATE TABLE IF NOT EXISTS pm_issue_claims (
            mapping_id TEXT PRIMARY KEY, operation_id TEXT NOT NULL)""")
        for verb in ("UPDATE", "DELETE"):
            journal.db.execute(f"CREATE TRIGGER IF NOT EXISTS pm_issue_{verb.lower()}_immutable BEFORE {verb} ON pm_issue_revisions "
                               "BEGIN SELECT RAISE(ABORT, 'PM issue history is immutable'); END")


def install(ops):
    schema(ops.journal)
    for action, keys in ((PUBLISH, ("workspace_id", "work_item_id")), (CANCEL, ("workspace_id", "mapping_id"))):
        if action not in ops.actions:
            ops.register(ActionDef(action, "manage", "Publish selected issue text" if action == PUBLISH
                else "Stop this workspace's issue tracking; optionally publish a selected reason",
                _run, _admitter(action), keys, authorize_existing=_authorize_existing))


def _member(ops, principal, wid, *, write=False):
    if not principal.allows("observe") or write and not principal.allows("manage"):
        raise _bad("FORBIDDEN", "Issue mappings require observe; changes also require manage", 403)
    return pm_workspace.require_member(ops.db, principal, wid, write=write)


def _rows(db, wid, mid):
    if not isinstance(mid, str) or not MAPPING.fullmatch(mid):
        raise _bad("PM_ISSUE_NOT_FOUND", "Issue mapping not found", 404)
    rows = db.execute("SELECT * FROM pm_issue_revisions WHERE workspace_id=? AND mapping_id=? ORDER BY sequence",
                      (wid, mid)).fetchall()
    result, previous = [], None
    for row in rows:
        doc = json.loads(row["document"])
        if (row["previous_revision_id"] != previous or packages._digest(doc) != row["document_digest"]
                or any(doc.get(key) != row[key] for key in ("revision_id", "mapping_id", "workspace_id", "work_item_id", "operation_id"))):
            raise _bad("PM_ISSUE_SOURCE_UNAVAILABLE", "Original issue history cannot be verified", 404)
        result.append({**dict(row), "document": doc})
        previous = row["revision_id"]
    return result


def _access(ops, principal, wid, rows):
    _member(ops, principal, wid)
    for row in rows:
        ref = row["document"]["package_ref"]
        package = packages._record(ops.db, wid, ref["package_id"])
        if package["document_digest"] != ref["document_digest"]:
            raise _bad("PM_ISSUE_SOURCE_UNAVAILABLE", "Original package unavailable", 404)
        packages._access(ops, principal, package)


def _package(ops, principal, wid, pid, expected_digest):
    package = packages._record(ops.db, wid, pid)
    packages._access(ops, principal, package)
    if package["document_digest"] != expected_digest:
        raise _bad("PM_ISSUE_PACKAGE_CHANGED", "Select the exact original package digest")
    return package


def _mapping_id(wid, work_id, repository_id):
    return "pmim_" + packages._digest([wid, work_id, repository_id])[:32]


def _relation(package):
    doc = package["document"]
    return {"work_item_id": package["work_item_id"], "binding_id": doc["context"]["work_ref"]["binding_id"],
            "requirement_ids": sorted(r["id"] for r in doc["requirements"]),
            "repository_id": doc["code_source"]["repository_id"], "repository": doc["code_source"]["repository"]}


def _repository_config(ops, package):
    doc, code = package["document"], package["document"]["code_source"]
    current = repository_sync.config_snapshot(ops, {key: code[key] for key in ("repository", "host", "workspace_id")})
    if repository_sync.digest(current) != doc["context"]["code_config_digest"]:
        raise _bad("PM_ISSUE_REPOSITORY_CHANGED", "The selected repository binding changed")
    client = ops.context.get("github")
    if client is None:
        raise _bad("GITHUB_NOT_CONFIGURED", "GitHub publication is unavailable", 503)
    return client


def _current_package(ops, principal, package):
    readiness = packages.readiness(ops, principal, package["workspace_id"], package["package_id"])
    # A useful issue may describe unresolved engineering work. Only identity and
    # scope gates authorize this publication; readiness never authorizes coding.
    needed = {"scope", "work", "package_revision", "code_configuration", "project_repository"}
    if any(c["status"] != "passed" for c in readiness["checks"] if c["check"] in needed):
        raise _bad("PM_ISSUE_CONTEXT_CHANGED", "Read the current scope, work association and package before publishing")
    _repository_config(ops, package)


def _validate(ops, principal, action, target, params, pre):
    wid = target.get("workspace_id")
    _member(ops, principal, wid, write=True)
    _exact(pre, {"expected_mapping_revision", "expected_package_digest"} if action == PUBLISH else {"expected_mapping_revision"})
    expected = pre["expected_mapping_revision"]
    if expected is not None and (not isinstance(expected, str) or not REVISION.fullmatch(expected)):
        raise _bad("INVALID_PARAMS", "Select the original mapping revision", 422)
    if action == PUBLISH:
        _exact(target, ("workspace_id", "work_item_id"))
        _exact(params, ("package_id", "public_title", "public_body"))
        if not isinstance(pre["expected_package_digest"], str) or not pm_plans.DIGEST.fullmatch(pre["expected_package_digest"]):
            raise _bad("INVALID_PARAMS", "Select the exact package digest", 422)
        title, body = _text(params["public_title"], "public_title", 200), _text(params["public_body"], "public_body", 32_000)
        package = _package(ops, principal, wid, params["package_id"], pre["expected_package_digest"])
        if package["work_item_id"] != target["work_item_id"]:
            raise _bad("PM_ISSUE_WORK_MISMATCH", "Select this work item's fixed package")
        _current_package(ops, principal, package)
        relation = _relation(package)
        mid = _mapping_id(wid, package["work_item_id"], relation["repository_id"])
        rows = _rows(ops.db, wid, mid)
        selected = {"public_title": title, "public_body": body}
        kind = "revision" if rows else "create"
    else:
        _exact(target, ("workspace_id", "mapping_id"))
        _exact(params, ("rationale",), ("public_reason",))
        rationale = _text(params["rationale"], "rationale", 4000)
        mid = target["mapping_id"]
        rows = _rows(ops.db, wid, mid)
        if not rows:
            raise _bad("PM_ISSUE_NOT_FOUND", "Issue mapping not found", 404)
        ref = rows[-1]["document"]["package_ref"]
        package = _package(ops, principal, wid, ref["package_id"], ref["document_digest"])
        relation = _relation(package)
        selected = {"rationale": rationale}
        if "public_reason" in params:
            selected["public_reason"] = _text(params["public_reason"], "public_reason", 8000)
            _repository_config(ops, package)
        kind = "cancel"
    _access(ops, principal, wid, rows)
    if (rows[-1]["revision_id"] if rows else None) != expected:
        raise _bad("PM_ISSUE_VERSION_CONFLICT", "Read the original mapping revision before changing it")
    if rows and rows[-1]["document"]["state"] == "cancelled":
        raise _bad("PM_ISSUE_CANCELLED", "This workspace stopped tracking this mapping; it is not implicitly reopened")
    if rows and rows[0]["document"]["relation"] != relation:
        raise _bad("PM_ISSUE_ASSOCIATION_CHANGED", "The original work, requirements and repository association must match")
    if len(rows) >= MAX_REVISIONS:
        raise _bad("PM_ISSUE_HISTORY_LIMIT", "Issue mapping revision history is full")
    return {"mapping_id": mid, "workspace_id": wid, "work_item_id": package["work_item_id"], "kind": kind,
            "relation": relation, "package_ref": {"package_id": package["package_id"], "document_digest": package["document_digest"]},
            "previous_revision_id": expected, "selected": selected,
            "issue": rows[-1]["document"]["issue"] if rows else None,
            "github_api_url": (rows[-1]["document"]["github_api_url"] if action == CANCEL
                else ops.context["github"].cfg.api_url)}


def _admitter(action):
    def admit(ops, principal, target, params, pre):
        with ops.journal.tx():
            intent = _validate(ops, principal, action, target, params, pre)
        return {"principal": pm_workspace.principal_binding(principal), "issue_intent": intent,
                "issue_intent_digest": packages._digest(intent)}
    return admit


def operation_visible(db, principal, operation):
    row = db.execute("SELECT * FROM operations WHERE operation_id=?", (operation.get("operation_id"),)).fetchone()
    if not row or row["action"] not in ACTIONS:
        return False
    op = OperationService._decode(row)
    binding = (op.get("external_refs") or {}).get("admission_binding") or {}
    intent = binding.get("issue_intent")
    try:
        if not intent or packages._digest(intent) != binding.get("issue_intent_digest"):
            return False
        reader = packages._SourceReader(db)
        wid = op["target"]["workspace_id"]
        _member(reader, principal, wid)
        ref = intent["package_ref"]
        _package(reader, principal, wid, ref["package_id"], ref["document_digest"])
        prior = intent["previous_revision_id"]
        rows = _rows(db, wid, intent["mapping_id"])
        if prior:
            positions = [index for index, row in enumerate(rows) if row["revision_id"] == prior]
            if len(positions) != 1:
                return False
            _access(reader, principal, wid, rows[:positions[0] + 1])
        return True
    except (OperationError, KeyError, TypeError, ValueError):
        return False


def _authorize_existing(ops, principal, op, verb):
    _member(ops, principal, op["target"]["workspace_id"], write=True)
    if not operation_visible(ops.db, principal, op):
        raise _bad("NOT_FOUND", "Original issue mapping sources are unavailable", 404)


def _current(ctx, principal):
    ctx.check_cancel()
    value = _validate(ctx.service, principal, ctx.op["action"], ctx.target, ctx.params, ctx.preconditions)
    if value != (ctx.admission_binding or {}).get("issue_intent"):
        raise _bad("PM_ISSUE_CONTEXT_CHANGED", "The admitted issue mapping context changed")
    return value


def _claim(ctx, intent):
    db, mid = ctx.service.db, intent["mapping_id"]
    row = db.execute("SELECT operation_id FROM pm_issue_claims WHERE mapping_id=?", (mid,)).fetchone()
    if row and row[0] != ctx.operation_id:
        op = db.execute("SELECT status FROM operations WHERE operation_id=?", (row[0],)).fetchone()
        step = db.execute("SELECT status FROM operation_steps WHERE operation_id=? AND name=?", (row[0], EXTERNAL_STEP)).fetchone()
        recorded = db.execute("SELECT 1 FROM pm_issue_revisions WHERE operation_id=?", (row[0],)).fetchone()
        # A successful original publication is accounted for by this request's
        # expected revision. A proven unsent/refused failed operation can yield
        # its reservation. An uncertain or unrecorded successful POST cannot.
        if not recorded and (not op or op[0] not in TERMINAL or step and step[0] != "failed"):
            raise _bad("PM_ISSUE_PENDING", "The original issue publication must be reconciled first")
    db.execute("INSERT INTO pm_issue_claims(mapping_id,operation_id) VALUES(?,?) ON CONFLICT(mapping_id) "
               "DO UPDATE SET operation_id=excluded.operation_id", (mid, ctx.operation_id))
    return {"mapping_id": mid, "operation_id": ctx.operation_id}


def _marker(ctx):
    opaque = packages._digest([ctx.operation_id, ctx.admission_binding["issue_intent_digest"]])
    return f"<!-- connector-pm-issue:{opaque} -->"


def _publication(ctx, intent):
    selected = intent["selected"]
    if intent["kind"] == "cancel":
        if "public_reason" not in selected:
            return None
        text = "Tracking stopped in the originating workspace.\n\n" + selected["public_reason"]
    elif intent["kind"] == "create":
        text = selected["public_body"]
    else:
        text = selected["public_title"] + "\n\n" + selected["public_body"]
    return {"body": text + "\n\n" + _marker(ctx), "title": selected.get("public_title"),
            "kind": "issue" if intent["kind"] == "create" else "comment"}


def _positive(value):
    return type(value) is int and value > 0


async def _repository(gh, intent):
    if gh.cfg.api_url != intent["github_api_url"]:
        raise _bad("PM_ISSUE_REPOSITORY_CHANGED", "The original GitHub service identity changed")
    status, body = await gh.repository(intent["relation"]["repository"])
    if (status != 200 or type(body.get("id")) is not int or body["id"] != intent["relation"]["repository_id"]
            or body.get("full_name", "").lower() != intent["relation"]["repository"].lower()):
        raise _bad("PM_ISSUE_REPOSITORY_CHANGED", "GitHub did not confirm the exact selected repository")


def _api_base(gh, intent):
    return intent["github_api_url"].rstrip("/") + gh._repo(intent["relation"]["repository"])


def _issue_identity(gh, intent, body):
    if (not isinstance(body, dict) or not _positive(body.get("id")) or not _positive(body.get("number"))
            or "pull_request" in body or body.get("repository_url") != _api_base(gh, intent)
            or body.get("url") != _api_base(gh, intent) + "/issues/" + str(body["number"])):
        raise GitHubAmbiguous("GitHub issue identity could not be verified")
    return {"id": body["id"], "number": body["number"], "repository_id": intent["relation"]["repository_id"],
            "repository": intent["relation"]["repository"]}


def _receipt(gh, intent, publication, body):
    if not isinstance(body, dict) or body.get("body") != publication["body"]:
        raise GitHubAmbiguous("GitHub did not return the exact selected publication")
    if publication["kind"] == "issue":
        issue = _issue_identity(gh, intent, body)
        if body.get("title") != publication["title"]:
            raise GitHubAmbiguous("GitHub did not return the original issue title")
        native_id = issue["id"]
    else:
        issue = intent["issue"]
        if (not _positive(body.get("id")) or body.get("issue_url") != _api_base(gh, intent) + "/issues/" + str(issue["number"])
                or body.get("url") != _api_base(gh, intent) + "/issues/comments/" + str(body["id"])):
            raise GitHubAmbiguous("GitHub did not confirm the original comment identity")
        native_id = body["id"]
    return {"issue": issue, "kind": publication["kind"], "native_id": native_id,
            "body_digest": packages._digest(publication["body"]), "publication_digest": packages._digest(publication),
            "confirmed_at": time.time(), "counts_as_done": False}


async def _read_issue(gh, intent):
    issue = intent["issue"]
    status, body = await gh.issue(issue["repository"], issue["number"])
    if status != 200 or _issue_identity(gh, intent, body) != issue:
        raise _bad("PM_ISSUE_REMOTE_CHANGED", "The original issue identity is unavailable")
    return body


async def _reconcile(gh, intent, publication):
    # Reads may continue after source withdrawal/stop to account for a previously
    # admitted effect. Their results remain under the original public read gate.
    try:
        await _repository(gh, intent)
        if publication["kind"] == "comment":
            await _read_issue(gh, intent)
        matches = []
        for page in range(1, MAX_PAGES + 1):
            status, body = await (gh.issues(intent["relation"]["repository"], page=page) if publication["kind"] == "issue"
                else gh.issue_comments(intent["relation"]["repository"], intent["issue"]["number"], page=page))
            if status != 200 or not isinstance(body.get("items"), list):
                return None
            items = body["items"]
            for item in items:
                if isinstance(item, dict) and item.get("body") == publication["body"] and "pull_request" not in item:
                    matches.append(_receipt(gh, intent, publication, item))
            if len(items) < 100:
                return matches[0] if len(matches) == 1 else None
        return None  # Incomplete pagination is unknown, never permission to POST.
    except (OperationError, GitHubAmbiguous, KeyError, TypeError, ValueError):
        return None


async def _run(ctx):
    db = ctx.service.db
    saved = db.execute("SELECT response FROM operation_steps WHERE operation_id=? AND name=? AND status='succeeded'",
                       (ctx.operation_id, RECORD_STEP)).fetchone()
    if saved:
        return json.loads(saved[0])
    principal = pm_workspace.admitted_principal(ctx)
    intent = (ctx.admission_binding or {}).get("issue_intent")
    if not intent or packages._digest(intent) != ctx.admission_binding.get("issue_intent_digest"):
        raise _bad("PM_ISSUE_CONTEXT_UNAVAILABLE", "The original publication intent is unavailable")
    publication = _publication(ctx, intent)
    existing = db.execute("SELECT * FROM operation_steps WHERE operation_id=? AND name=?", (ctx.operation_id, EXTERNAL_STEP)).fetchone()
    if not existing:
        _current(ctx, principal)
        ctx.effect("pm.issue.reserve", lambda: _claim(ctx, _current(ctx, principal)))
    receipt = None
    if publication is not None:
        gh = ctx.service.context.get("github")
        if gh is None:
            raise Wait("waiting_external", "Original GitHub publication needs its configured connection", 30)
        if not existing:
            previous = db.execute("SELECT name,status FROM operation_steps WHERE operation_id=? AND name LIKE 'pm.issue.preflight.%' ORDER BY seq DESC LIMIT 1",
                                  (ctx.operation_id,)).fetchone()
            number = int(previous["name"].rsplit(".", 1)[1]) if previous else 0
            if previous is None or previous["status"] == "succeeded":
                number += 1
            if number > MAX_PREFLIGHTS:
                raise _bad("PM_ISSUE_PREFLIGHT_LIMIT", "The original publication requires review after repeated interrupted preflights")

            async def preflight():
                try:
                    _current(ctx, principal)
                    await _repository(gh, intent)
                    if intent["issue"] is not None:
                        await _read_issue(gh, intent)
                    _current(ctx, principal)
                    return {"repository_id": intent["relation"]["repository_id"], "issue": intent["issue"]}
                except Cancelled:
                    raise _bad("CANCELLED", "Issue preflight stopped without publication") from None

            async def reread(_):
                return RERUN  # Pure bounded reads; restart still passes the epoch gate.

            # A successful read from a previous worker attempt cannot authorize
            # an as-yet-unsent POST against a now-transferred/replaced repository.
            await ctx.step(f"pm.issue.preflight.{number}", preflight, reconcile=reread,
                request={"intent_digest": ctx.admission_binding["issue_intent_digest"]})
            _current(ctx, principal)

        async def send():
            _current(ctx, principal)  # Final unsent frame; no intervening host read.
            if gh.cfg.api_url != intent["github_api_url"]:
                raise _bad("PM_ISSUE_REPOSITORY_CHANGED", "The original GitHub service identity changed")
            if publication["kind"] == "issue":
                status, body = await gh.create_issue(intent["relation"]["repository"], title=publication["title"], body=publication["body"])
            else:
                status, body = await gh.create_issue_comment(intent["relation"]["repository"], intent["issue"]["number"], body=publication["body"])
            if status != 201:
                if 400 <= status < 500:
                    raise _bad("PM_ISSUE_PUBLICATION_REFUSED", f"GitHub refused the publication ({status})")
                raise GitHubAmbiguous("GitHub publication returned an unproven response")
            return _receipt(gh, intent, publication, body)

        async def reconcile(_):
            return await _reconcile(gh, intent, publication)

        receipt = await ctx.step(EXTERNAL_STEP, send, request={"intent_digest": ctx.admission_binding["issue_intent_digest"],
            "publication_digest": packages._digest(publication)}, reconcile=reconcile)

    def record():
        if receipt is None:
            _current(ctx, principal)  # Local-only cancellation is a new decision.
        else:
            # Receipt-only recovery cannot create a second external effect, adopt
            # scope, or confer read permission after membership/source withdrawal.
            if receipt["publication_digest"] != packages._digest(publication):
                raise _bad("PM_ISSUE_RECEIPT_MISMATCH", "Original publication receipt cannot be verified")
        rows = _rows(db, intent["workspace_id"], intent["mapping_id"])
        if (rows[-1]["revision_id"] if rows else None) != intent["previous_revision_id"]:
            raise _bad("PM_ISSUE_VERSION_CONFLICT", "The original publication lineage changed")
        now, rid = time.time(), "pmir_" + ctx.operation_id[3:]
        document = {**copy.deepcopy(intent), "revision_id": rid, "operation_id": ctx.operation_id,
            "issue": receipt["issue"] if receipt else intent["issue"], "receipt": receipt,
            "state": "cancelled" if intent["kind"] == "cancel" else "tracking", "external_issue_state": "not_changed",
            "external_content_policy": "create_or_append_only", "execution_authorized": False, "counts_as_done": False}
        db.execute("""INSERT INTO pm_issue_revisions(revision_id,mapping_id,workspace_id,work_item_id,previous_revision_id,
            document,document_digest,operation_id,created_at,actor) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (rid, intent["mapping_id"], intent["workspace_id"], intent["work_item_id"], intent["previous_revision_id"],
             packages._json(document), packages._digest(document), ctx.operation_id, now, ctx.actor))
        result = {"workspace_id": intent["workspace_id"], "mapping_id": intent["mapping_id"], "revision_id": rid,
            "work_item_id": intent["work_item_id"], "state": document["state"], "issue": document["issue"],
            "publication": receipt, "external_issue_state": "not_changed", "execution_authorized": False}
        ctx.service.journal.api_event("pm_issue_mapping", intent["mapping_id"], "pm.issue.recorded",
                                     {**result, "operation_id": ctx.operation_id}, actor=ctx.actor)
        return result

    return ctx.effect(RECORD_STEP, record, receipt_only=receipt is not None)


def get_mapping(ops, principal, workspace_id, mapping_id):
    with ops.journal.tx():
        _member(ops, principal, workspace_id)
        rows = _rows(ops.db, workspace_id, mapping_id)
        if not rows:
            raise _bad("PM_ISSUE_NOT_FOUND", "Issue mapping not found", 404)
        _access(ops, principal, workspace_id, rows)
        return _checked({"version": 1, "workspace_id": workspace_id, "mapping_id": mapping_id,
            "current": rows[-1], "revisions": rows, "execution_authorized": False})


def list_mappings(ops, principal, workspace_id, *, after=0, limit=50):
    with ops.journal.tx():
        _member(ops, principal, workspace_id)
        pm_sprints._page(after, limit)
        if after > 2**63 - 1:
            raise _bad("INVALID_PARAMS", "after exceeds the journal sequence range", 422)
        ids = ops.db.execute("""SELECT mapping_id,MIN(sequence) AS first_sequence FROM pm_issue_revisions
            WHERE workspace_id=? GROUP BY mapping_id HAVING MIN(sequence)>? ORDER BY first_sequence LIMIT ?""",
            (workspace_id, after, limit + 1)).fetchall()
        result = {"version": 1, "workspace_id": workspace_id, "mappings": [], "next_cursor": None,
            "coverage": {"returned": 0, "scanned": 0, "visibility_filtered": False, "byte_limited": False, "has_more": False}}
        cursor = after
        for index, row in enumerate(ids[:limit]):
            before = copy.deepcopy(result)
            try:
                rows = _rows(ops.db, workspace_id, row["mapping_id"])
                _access(ops, principal, workspace_id, rows)
                result["mappings"].append(rows[-1])
            except OperationError as error:
                # A lineage limit on one mapping must not fail the rest of the page.
                if error.status not in (404, 409):
                    raise
                result["coverage"]["visibility_filtered"] = True
            more = index + 1 < len(ids)
            result["next_cursor"] = row["first_sequence"] if more else None
            result["coverage"].update(scanned=index + 1, returned=len(result["mappings"]), has_more=more)
            if packages._size(result) > MAX_READ_BYTES:
                if not index:
                    raise _bad("PM_ISSUE_READ_TOO_LARGE", "One complete mapping exceeds the page limit", 413)
                result = before
                result["next_cursor"] = cursor
                result["coverage"].update(byte_limited=True, has_more=True)
                break
            cursor = row["first_sequence"]
        return _checked(result)
