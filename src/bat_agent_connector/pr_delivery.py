"""Immutable PR merge scopes and metadata operations; no local Git or task ownership is inferred."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
import weakref
from datetime import datetime

from .github import GitHubAmbiguous
from .operations import RERUN, NeedsAttention, OperationError, Wait

METADATA_SETTLE_S = 10 * 60
SCOPE_REFRESH_S = 60
PREVIEW_RETENTION_S = 24 * 3600
FILE_FIELDS = ("filename", "status", "additions", "deletions", "changes", "previous_filename")


def digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def metadata(pr: dict) -> dict:
    return {"title": pr.get("title") or "", "body": pr.get("body") or ""}


async def read(coro, ctx=None):
    status, body = await coro
    if ctx is not None:
        from .delivery import _read_result
        return _read_result(status, body, "read PR merge evidence", ctx)
    if status != 200:
        raise OperationError("MERGE_SCOPE_UNPROVEN", f"GitHub scope read returned {status}: "
                             f"{str(body.get('message') or '')[:160]}")
    return body


async def pages(fetch, ctx=None) -> list:
    result = []
    for page in range(1, 10001):
        body = await read(fetch(page), ctx)
        items = body.get("items")
        if not isinstance(items, list):
            raise OperationError("MERGE_SCOPE_UNPROVEN", "GitHub omitted the paginated list")
        result.extend(items)
        if len(items) < 100:
            return result
    raise OperationError("MERGE_SCOPE_UNPROVEN", "GitHub pagination did not terminate")


async def commit_range(gh, repository, base, head, ctx=None) -> dict:
    first = await read(gh.compare(repository, base, head), ctx)
    total = first.get("total_commits")
    commits = list(first.get("commits") or [])
    if not isinstance(total, int) or not (first.get("merge_base_commit") or {}).get("sha"):
        raise OperationError("MERGE_SCOPE_UNPROVEN", "comparison omitted its total or merge base")
    page = 2
    while len(commits) < total:
        body = await read(gh.compare(repository, base, head, page=page), ctx)
        more = body.get("commits") or []
        if not more or body.get("total_commits") != total:
            raise OperationError("MERGE_SCOPE_UNPROVEN", "incomplete paginated commit comparison")
        commits.extend(more)
        page += 1
    if len(commits) != total or len({c.get("sha") for c in commits}) != total:
        raise OperationError("MERGE_SCOPE_UNPROVEN", "comparison has missing or duplicate commits")
    for c in commits:
        if (not isinstance(c, dict) or not re.fullmatch(r"[0-9a-f]{40}", str(c.get("sha") or ""))
                or not isinstance(c.get("parents"), list)
                or any(not re.fullmatch(r"[0-9a-f]{40}", str(p.get("sha") or "")) for p in c["parents"])):
            raise OperationError("MERGE_SCOPE_UNPROVEN", "comparison omitted commit identity or parents")
    return {"merge_base_sha": first["merge_base_commit"]["sha"], "status": first.get("status"),
            "commits": [{"sha": c["sha"], "parents": [p["sha"] for p in c.get("parents", [])],
                         "message": (c.get("commit") or {}).get("message", "")} for c in commits],
            "files": [{k: f[k] for k in FILE_FIELDS if k in f} for f in first.get("files") or []],
            "files_may_be_truncated": len(first.get("files") or []) >= 300}


async def ancestor(gh, repository, base, head, ctx=None) -> bool:
    body = await read(gh.compare(repository, base, head), ctx)
    merge_base = (body.get("merge_base_commit") or {}).get("sha")
    if not merge_base or body.get("status") not in {"ahead", "behind", "identical", "diverged"}:
        raise OperationError("MERGE_SCOPE_UNPROVEN", "comparison omitted ancestry evidence")
    return merge_base == base


def identity(pr: dict) -> dict:
    head, base = pr.get("head") or {}, pr.get("base") or {}
    return {"number": pr.get("number"), "title": pr.get("title"), "html_url": pr.get("html_url"),
            "head_sha": head.get("sha"), "head_ref": head.get("ref"),
            "head_repo_id": (head.get("repo") or {}).get("id"), "base_sha": base.get("sha"),
            "base_ref": base.get("ref"), "repository_id": (base.get("repo") or {}).get("id")}


async def scope(ops, repository: str, number: int, method: str, ctx=None) -> dict:
    gh = ops.context["github"]
    pr = await read(gh.pull(repository, number), ctx)
    target = identity(pr)
    result = {"repository": repository, "provider": gh.cfg.api_url, "target": target, "method": method,
              "commits": [], "files": [], "stacks": [], "affected_prs": [], "blocking": [], "warnings": []}
    def block(code, message):
        result["blocking"].append({"code": code, "message": message})
    try:
        repo = await read(gh.repository(repository), ctx)
        if (not target["head_repo_id"] or not target["repository_id"] or not target["base_ref"]
                or repo.get("id") != target["repository_id"]
                or not re.fullmatch(r"[0-9a-f]{40}", target["head_sha"] or "")
                or not re.fullmatch(r"[0-9a-f]{40}", target["base_sha"] or "")):
            raise OperationError("MERGE_SCOPE_UNPROVEN", "missing or inconsistent repository/head/base identity")
        stacks = await pages(lambda page: gh.stacks(repository, number, page=page), ctx)
        affected = {}
        for entry in stacks:
            stack = await read(gh.stack(repository, entry["number"]), ctx)
            members = stack.get("pull_requests")
            if not isinstance(members, list) or number not in [p.get("number") for p in members]:
                raise OperationError("MERGE_SCOPE_UNPROVEN", "inconsistent native stack membership")
            result["stacks"].append({"number": stack["number"], "members": [p["number"] for p in members]})
            target_index = [p["number"] for p in members].index(number)
            for index, member in enumerate(members):
                if member["number"] != number and member.get("state") == "open":
                    full = await read(gh.pull(repository, member["number"]), ctx)
                    affected[full["number"]] = {**identity(full), "reason": "native_stack",
                                                "would_merge": index < target_index,
                                                "effect": "merge" if index < target_index else "branch_rebase"}
            block("STACKED_PR_UNSUPPORTED", "native stack members may merge or have their branches rebased")
        comparison = await commit_range(gh, repository, target["base_sha"], target["head_sha"], ctx)
        result.update(comparison)
        if comparison["merge_base_sha"] != target["base_sha"]:
            result["warnings"].append("head is behind or diverged from base; GitHub up-to-date rules apply")
        all_prs = await pages(lambda page: gh.pulls(repository, page=page), ctx)
        if any(not identity(p)["number"] or not identity(p)["repository_id"] or not identity(p)["head_repo_id"]
               or not identity(p)["head_ref"] or not identity(p)["base_ref"]
               or not re.fullmatch(r"[0-9a-f]{40}", str(identity(p)["head_sha"] or "")) for p in all_prs):
            raise OperationError("MERGE_SCOPE_UNPROVEN", "open PR list omitted relationship identity")
        # Walk base -> head-ref dependencies, including forks by repository identity, in either direction.
        chain = {number}
        changed = True
        while changed:
            changed = False
            known = [pr, *[p for p in all_prs if p["number"] in chain and p["number"] != number]]
            for other in all_prs:
                if other["number"] in chain:
                    continue
                oi = identity(other)
                if any((oi["head_ref"] == identity(k)["base_ref"]
                        and oi["head_repo_id"] == identity(k)["repository_id"])
                       or (oi["base_ref"] == identity(k)["head_ref"]
                           and oi["repository_id"] == identity(k)["head_repo_id"]) for k in known):
                    chain.add(other["number"])
                    affected.setdefault(other["number"], {**oi, "reason": "branch_chain", "would_merge": False,
                                                         "effect": "dependency"})
                    changed = True
        if len(chain) > 1 and not stacks:
            block("STACKED_PR_UNSUPPORTED", "dependent PR branch chain is not supported")
        for other in all_prs:
            oi = identity(other)
            if other["number"] == number or other["number"] in affected:
                continue
            if oi["base_ref"] == target["base_ref"] and oi["repository_id"] == target["repository_id"]:
                reachable = await ancestor(gh, repository, oi["head_sha"], target["head_sha"], ctx)
                if reachable and not await ancestor(gh, repository, oi["head_sha"], target["base_sha"], ctx):
                    affected[other["number"]] = {**oi, "reason": "indirect_merge", "would_merge": method == "merge"}
                    if method == "merge":
                        block("MERGE_SCOPE_EXPANDED", f"PR #{other['number']} would be indirectly merged")
                    else:
                        result["warnings"].append(f"PR #{other['number']} shares included commits; {method} does not "
                                                  "normally close it by SHA reachability")
        result["affected_prs"] = [affected[n] for n in sorted(affected)]
        again = identity(await read(gh.pull(repository, number), ctx))
        if again != target:
            raise OperationError("MERGE_SCOPE_UNPROVEN", "PR moved while reading the scope; reload its preview")
    except (OperationError, GitHubAmbiguous, KeyError, TypeError, AttributeError) as exc:
        block("MERGE_SCOPE_UNPROVEN", str(exc)[:240])
    return result


def save_preview(ops, document: dict) -> dict:
    now, value_digest = time.time(), digest(document)
    with ops.journal.tx():
        # An admitted operation still reads its immutable scope after expiry (e.g. a merge queue wait).
        protected = {json.loads(r["params"]).get("preview_id") for r in ops.db.execute(
            "SELECT params FROM operations WHERE action IN ('github.pr.merge','delivery.merge_and_deploy') "
            "AND status NOT IN ('succeeded','failed','cancelled')")}
        for row in ops.db.execute("SELECT preview_id FROM pr_merge_previews WHERE expires_at<?",
                                  (now - PREVIEW_RETENTION_S,)).fetchall():
            if row["preview_id"] not in protected:
                ops.db.execute("DELETE FROM pr_merge_previews WHERE preview_id=?", (row["preview_id"],))
        ops.db.execute("DELETE FROM pr_merge_scope_reads WHERE checked_at<?", (now - PREVIEW_RETENTION_S,))
        row = ops.db.execute("SELECT document FROM pr_merge_previews WHERE repository=? AND pull_number=? "
                             "AND digest=? AND expires_at>? ORDER BY created_at DESC LIMIT 1",
                             (document["repository"], document["target"]["number"], value_digest, now)).fetchone()
        if row:
            doc = json.loads(row["document"])
        else:
            doc = {**document, "preview_id": "mpv_" + uuid.uuid4().hex, "digest": value_digest,
                   "created_at": now, "expires_at": now + 3600}
            ops.db.execute("INSERT INTO pr_merge_previews VALUES (?,?,?,?,?,?,?)",
                           (doc["preview_id"], doc["repository"], doc["target"]["number"], json.dumps(doc),
                            doc["digest"], doc["created_at"], doc["expires_at"]))
            from .pm_delivery_lineage import preserve_preview
            preserve_preview(ops, doc)
            from .observation import summary
            ops.journal.api_event("pr_merge_preview", doc["preview_id"], "delivery.merge_previewed", summary(doc, pr=True),
                                  context={"observer": "delivery-service", "evidence": [{"table": "pr_merge_previews", "id": doc["preview_id"]}]})
        ops.db.execute("INSERT OR REPLACE INTO pr_merge_scope_reads VALUES (?,?,?,?,?)",
                       (doc["repository"], doc["target"]["number"], doc["method"], doc["preview_id"], now))
    return doc


async def card_preview(ops, repository: str, number: int, method: str, pr: dict, *, from_event: bool) -> dict:
    locks = ops.context.setdefault("pr_merge_preview_locks", weakref.WeakValueDictionary())
    key = (repository, number, method)
    lock = locks.setdefault(key, asyncio.Lock())
    async with lock:
        row = ops.db.execute("SELECT p.document,r.checked_at FROM pr_merge_scope_reads r "
                             "JOIN pr_merge_previews p ON p.preview_id=r.preview_id "
                             "WHERE r.repository=? AND r.pull_number=? AND r.method=?",
                             key).fetchone()
        if from_event and row and time.time() - row["checked_at"] < SCOPE_REFRESH_S:
            cached = json.loads(row["document"])
            current = identity(pr)
            if (cached["expires_at"] > time.time()
                    and all(cached["target"][f] == current[f] for f in ("head_sha", "base_sha"))):
                return cached
        document = await scope(ops, repository, number, method)
        if document["target"] != identity(pr):
            document["blocking"].append({"code": "MERGE_SCOPE_UNPROVEN",
                                        "message": "PR moved while loading its card; reload"})
        return save_preview(ops, document)


def get_preview(db, preview_id: str) -> dict:
    row = db.execute("SELECT document FROM pr_merge_previews WHERE preview_id=?", (preview_id,)).fetchone()
    if not row:
        raise OperationError("PREVIEW_NOT_FOUND", "merge preview not found; read github_pr_preview first", 404)
    return json.loads(row["document"])


def merge_envelope(doc: dict, *, recipe: str | None = None) -> dict:
    return {"action": "delivery.merge_and_deploy" if recipe else "github.pr.merge",
            "target": {"repository": doc["repository"], "pull_number": doc["target"]["number"],
                       **({"recipe": recipe} if recipe else {})},
            "params": {"preview_id": doc["preview_id"], "method": doc["method"]},
            "preconditions": {"expected_head_sha": doc["target"]["head_sha"],
                              "expected_base_sha": doc["target"]["base_sha"], "preview_digest": doc["digest"]}}


async def check_scope(ctx, doc: dict, *, expiry: bool = True):
    from .delivery import _sent_write
    if expiry and doc["expires_at"] < time.time():
        raise OperationError("PREVIEW_EXPIRED", "merge preview expired; read github_pr_preview again", 409)
    try:
        fresh = await scope(ctx.service, doc["repository"], doc["target"]["number"], doc["method"],
                            ctx if _sent_write(ctx) else None)
    except GitHubAmbiguous as exc:
        raise NeedsAttention("MERGE_SCOPE_UNPROVEN", str(exc)) from None
    for field, code in (("head_sha", "TARGET_HEAD_CHANGED"), ("base_sha", "TARGET_BASE_CHANGED")):
        if fresh["target"][field] != doc["target"][field]:
            diff = {"field": field, "reviewed": doc["target"][field], "observed": fresh["target"][field]}
            ctx.set_refs(scope_difference=diff)
            raise OperationError(code, json.dumps(diff), 409)
    if fresh["blocking"]:
        b = fresh["blocking"][0]
        raise NeedsAttention(b["code"], b["message"])
    expected = {k: v for k, v in doc.items() if k not in {"preview_id", "digest", "created_at", "expires_at"}}
    if digest(fresh) != doc["digest"]:
        ctx.set_refs(scope_difference={"reviewed": expected, "observed": fresh})
        raise OperationError("MERGE_SCOPE_CHANGED", "merge scope changed; review a new github_pr_preview", 409)


async def verify_merge(ctx, pr: dict, doc: dict) -> dict:
    gh, repository = ctx.service.context["github"], doc["repository"]
    merged, head, base = pr.get("merge_commit_sha"), doc["target"]["head_sha"], doc["target"]["base_sha"]
    previous = ctx.service.db.execute("SELECT name,status,response,seq FROM operation_steps WHERE operation_id=? "
                                      "AND (name='merge.verify' OR name LIKE 'merge.verify.retry.%') "
                                      "ORDER BY seq DESC LIMIT 1", (ctx.operation_id,)).fetchone()
    if previous and previous["status"] == "succeeded":
        saved = json.loads(previous["response"] or "{}")
        if saved.get("verified"):
            ctx.set_refs(merge_receipt=saved)
            return saved
    name = (previous["name"] if previous and previous["status"] in {"started", "uncertain"}
            else f"merge.verify.retry.{previous['seq']}" if previous else "merge.verify")
    ctx.set_refs(merged_sha=merged, merge_receipt={"merged_sha": merged, "verified": False,
                                                "preview_id": doc["preview_id"]})
    async def verify():
        try:
            if not pr.get("merged") or not merged or (pr.get("head") or {}).get("sha") != head:
                raise OperationError("MERGE_RESULT_UNVERIFIABLE", "GitHub has not proven the reviewed head was merged")
            commit = await read(gh.commit(repository, merged), ctx)
            parents = commit.get("parents") or []
            method = doc["method"]
            if not parents or (method == "merge" and (len(parents) != 2 or parents[1].get("sha") != head)):
                raise OperationError("MERGE_RESULT_UNVERIFIABLE", "merge commit parents do not prove the reviewed head")
            onto = parents[0]["sha"]
            if method == "rebase":
                cursor = merged
                for _ in doc["commits"]:
                    c = await read(gh.commit(repository, cursor), ctx)
                    ps = c.get("parents") or []
                    if len(ps) != 1:
                        raise OperationError("MERGE_RESULT_UNVERIFIABLE", "cannot determine the rebase destination")
                    cursor = ps[0]["sha"]
                onto = cursor
            tip = await read(gh.commit(repository, doc["target"]["base_ref"]), ctx)
            if (not await ancestor(gh, repository, base, onto, ctx)
                    or not await ancestor(gh, repository, merged, tip["sha"], ctx)):
                raise OperationError("MERGE_RESULT_UNVERIFIABLE", "merged result is not on the reviewed base history")
            extra = await commit_range(gh, repository, base, onto, ctx)
            candidates = {p["number"]: p for p in doc["affected_prs"]}
            # Re-read current stack membership: a stack created during the final GET -> PUT window matters too.
            for stack in await pages(lambda page: gh.stacks(repository, doc["target"]["number"], page=page), ctx):
                full = await read(gh.stack(repository, stack["number"]), ctx)
                members = full.get("pull_requests", [])
                target_number = doc["target"]["number"]
                if (target_number in [m["number"] for m in members]
                        and any(m["number"] != target_number for m in members)
                        and full["number"] not in [s["number"] for s in doc["stacks"]]):
                    observed = []
                    for member in members:
                        other = await read(gh.pull(repository, member["number"]), ctx)
                        oi = identity(other)
                        observed.append({"number": oi["number"], "state": other.get("state"),
                                         "head_sha": oi["head_sha"], "base_ref": oi["base_ref"]})
                    ctx.set_refs(merge_receipt={"merged_sha": merged, "verified": False,
                                               "preview_id": doc["preview_id"],
                                               "stacks": [{"number": full["number"], "members": observed}],
                                               "affected_prs": [m for m in observed if m["number"] != target_number]})
                    raise NeedsAttention("MERGE_RESULT_SCOPE_CHANGED",
                                         f"native stack #{full['number']} appeared after the reviewed preview")
                for member in full.get("pull_requests", []):
                    if member["number"] != doc["target"]["number"]:
                        candidates.setdefault(member["number"], identity(await read(gh.pull(repository, member["number"]), ctx)))
            # Include PRs that appeared during acceptance and vanished from stack membership after merging.
            # Only results reachable from this merge, outside its destination base, can be attributed to it.
            for other in await recent_prs(gh, repository, ctx.op["created_at"], ctx):
                oi = identity(other)
                merged_at = other.get("merged_at")
                if merged_at:
                    try:
                        if datetime.fromisoformat(merged_at.replace("Z", "+00:00")).timestamp() < int(ctx.op["created_at"]):
                            continue
                    except (ValueError, TypeError):
                        raise OperationError("MERGE_RESULT_UNVERIFIABLE", "merged PR omitted a valid merge time") from None
                if (oi["number"] != doc["target"]["number"] and (merged_at or other.get("merged"))
                        and oi["base_ref"] == doc["target"]["base_ref"]
                        and oi["repository_id"] == doc["target"]["repository_id"]):
                    if await ancestor(gh, repository, oi["head_sha"], head, ctx):
                        candidates.setdefault(oi["number"], oi)
            outcomes = []
            for number, candidate in candidates.items():
                other = await read(gh.pull(repository, number), ctx)
                independent = merged_after = False
                if other.get("merged"):
                    other_sha = other.get("merge_commit_sha")
                    if not other_sha:
                        raise OperationError("MERGE_RESULT_UNVERIFIABLE", "affected PR omitted its merged SHA")
                    independent = await ancestor(gh, repository, other_sha, onto, ctx)
                    swept = not independent and await ancestor(gh, repository, other_sha, merged, ctx)
                    merged_after = not independent and not swept and await ancestor(gh, repository, merged, other_sha, ctx)
                    if not independent and not swept and not merged_after:
                        raise OperationError("MERGE_RESULT_UNVERIFIABLE", f"cannot attribute affected PR #{number}")
                    if swept:
                        ctx.set_refs(merge_receipt={"merged_sha": merged, "affected_prs": outcomes +
                                                   [{"number": number, "swept_in": True}]})
                        raise NeedsAttention("MERGE_RESULT_SCOPE_CHANGED", f"PR #{number} was swept into this merge")
                outcomes.append({"number": number, "merged": bool(other.get("merged")),
                                 "independent": independent or merged_after, "merged_after": merged_after,
                                 "reviewed_head": candidate.get("head_sha")})
            return {"verified": True, "merged_sha": merged, "merged_onto_base_sha": onto,
                       "base_moved": onto != base, "other_commits_count": len(extra["commits"]),
                       "other_commits": extra["commits"], "affected_prs": outcomes}
        except GitHubAmbiguous:
            return {"verification_pending": True, "merged_sha": merged}
        except NeedsAttention as exc:
            return {"verification_error": exc.code, "message": exc.message, "merged_sha": merged}
        except (KeyError, TypeError, ValueError, AttributeError):
            return {"verification_error": "MERGE_RESULT_UNVERIFIABLE", "merged_sha": merged,
                    "message": "GitHub omitted required merge evidence"}
        except OperationError as exc:
            if exc.code.startswith("GITHUB_"):
                return {"read_refused_before_write": True, "verification_error": exc.code,
                        "message": exc.message, "merged_sha": merged}
            return {"verification_error": "MERGE_RESULT_UNVERIFIABLE", "message": str(exc), "merged_sha": merged}
    async def reread(_):
        return RERUN  # verification is read-only, including recovery after a process exits mid-read
    from .pm_delivery_lineage import readback_step
    receipt = await readback_step(ctx, name, verify, request={"merged_sha": merged, "preview_id": doc["preview_id"]},
                                 reconcile=reread)
    if receipt.get("verification_pending"):
        from .delivery import _check_wait
        _check_wait(ctx, "merge_verification_wait_started_at")
        raise Wait("waiting_external", "waiting for merge verification evidence", 30)
    if receipt.get("verification_error"):
        if receipt.get("read_refused_before_write"):
            raise OperationError(receipt["verification_error"], receipt["message"])
        raise NeedsAttention(receipt["verification_error"], receipt["message"])
    ctx.set_refs(merge_receipt=receipt)
    return receipt


async def recent_prs(gh, repository: str, created_at: float, ctx=None) -> list:
    result = []
    for page in range(1, 10001):
        items = (await read(gh.pulls(repository, page=page, state="all", sort="updated", direction="desc"), ctx))["items"]
        for pr in items:
            # REST timestamps have second precision; retain the whole admission second.
            updated_at = datetime.fromisoformat(pr["updated_at"].replace("Z", "+00:00")).timestamp()
            if updated_at < int(created_at):
                return result
            result.append(pr)
        if len(items) < 100:
            return result
    raise OperationError("MERGE_RESULT_UNVERIFIABLE", "updated PR pagination did not terminate")


def metadata_settlement(ops, operation_id: str) -> dict | None:
    row = ops.db.execute("SELECT document FROM pr_metadata_settlements WHERE operation_id=?", (operation_id,)).fetchone()
    return json.loads(row["document"]) if row else None


def save_metadata_settlement(ops, operation_id: str, receipt: dict) -> dict:
    with ops.journal.tx():
        inserted = ops.db.execute("INSERT OR IGNORE INTO pr_metadata_settlements VALUES (?,?)",
                                 (operation_id, json.dumps(receipt)))
        if inserted.rowcount:
            from .observation import summary
            ops.journal.api_event("operation", operation_id, "delivery.metadata_settled", summary(receipt, pr=True),
                                  context={"observer": "delivery-service", "entry_point": "daemon",
                                           "evidence": [{"table": "pr_metadata_settlements", "id": operation_id}]})
    return metadata_settlement(ops, operation_id)


def settle_not_applied(ops, operation_id: str, started_at: float, observed: dict) -> dict | None:
    if time.time() - started_at < METADATA_SETTLE_S:
        return None
    receipt = {"status": "not_applied", "code": "PR_METADATA_NOT_APPLIED", "observed": observed,
               "settled_at": time.time()}
    return save_metadata_settlement(ops, operation_id, receipt)


def admit_update(ops, principal, target, params, pre):
    from .delivery import _gh, _repo_or_403
    _gh(ops)
    repository = _repo_or_403(ops, target.get("repository"))
    if not ops.context["github_config"].repos[repository.lower()].allow_pr_update:
        raise OperationError("PR_UPDATE_DISABLED", f"allow_pr_update is disabled for {repository}", 403)
    if not isinstance(target.get("pull_number"), int) or isinstance(target["pull_number"], bool) or target["pull_number"] < 1:
        raise OperationError("INVALID_TARGET", "pull_number must be a positive integer", 422)
    if (not params or set(params) - {"title", "body"}
            or any(not isinstance(v, str) for v in params.values())
            or ("title" in params and not params["title"].strip())):
        raise OperationError("INVALID_PARAMS", "provide title (nonempty) and/or body strings only", 422)
    if not re.fullmatch(r"[0-9a-f]{64}", str(pre.get("expected_metadata_digest") or "")):
        raise OperationError("PRECONDITION_REQUIRED", "expected_metadata_digest from github_pr_preview is required", 422)
    for row in ops.db.execute("SELECT operation_id,target,status FROM operations WHERE action='github.pr.update'"):
        t = json.loads(row["target"])
        if t.get("repository", "").lower() == repository.lower() and t.get("pull_number") == target["pull_number"]:
            if metadata_settlement(ops, row["operation_id"]):
                continue
            unresolved = ops.db.execute("SELECT 1 FROM operation_steps WHERE operation_id=? "
                                        "AND name='pr.metadata.write' AND status IN ('started','uncertain')",
                                        (row["operation_id"],)).fetchone()
            if row["status"] not in {"succeeded", "failed", "cancelled"} or unresolved:
                raise OperationError("PR_UPDATE_IN_PROGRESS", f"{row['operation_id']} has an unresolved metadata update", 409)


async def run_update(ctx):
    from .delivery import _gh, _read, _read_result, _repo_or_403
    settled = metadata_settlement(ctx.service, ctx.operation_id)
    if settled and settled["status"] == "conflict":
        raise NeedsAttention(settled["code"], "write readback differs; review before/intended/observed metadata")
    gh = _gh(ctx.service)
    repository, number = _repo_or_403(ctx.service, ctx.target["repository"]), ctx.target["pull_number"]
    async def plan():
        status, pr = await gh.pull(repository, number)
        if status != 200:
            raise OperationError(f"GITHUB_{status}", "cannot read PR metadata")
        before = metadata(pr)
        if not (pr.get("base", {}).get("repo") or {}).get("id"):
            raise OperationError("REPOSITORY_ID_CHANGED", "PR omitted repository identity")
        return {"before": before, "after": {**before, **ctx.params},
                "repository_id": (pr.get("base", {}).get("repo") or {}).get("id"),
                "html_url": pr.get("html_url")}
    async def reread(_):
        return RERUN  # read only; no write could have happened
    p = await ctx.step("pr.metadata.plan", plan, request={"repository": repository, "pull_number": number},
                       reconcile=reread)
    if digest(p["before"]) != ctx.preconditions["expected_metadata_digest"]:
        ctx.set_refs(metadata_difference={"before": p["before"], "intended": p["after"]})
        raise OperationError("PR_METADATA_CHANGED", "PR title/body changed; read github_pr_preview and edit again", 409)
    async def write():
        pr = await _read(gh.pull(repository, number), "re-read metadata immediately before PATCH")
        if metadata(pr) != p["before"] or (pr.get("base", {}).get("repo") or {}).get("id") != p["repository_id"]:
            return {"conflict_before_write": True, "observed": metadata(pr)}
        status, _ = await gh.update_pull(repository, number, ctx.params)
        return {"http_status": status, "write_acknowledged": status == 200}
    async def reconcile(_):
        settled = metadata_settlement(ctx.service, ctx.operation_id)
        if settled:
            if settled["status"] == "conflict":
                return {"conflict_after_write": True, "observed": settled["observed"]}
            return {"not_applied": settled}
        status, pr = await gh.pull(repository, number)
        _read_result(status, pr, "read back uncertain PR metadata", ctx)
        observed = metadata(pr)
        if observed == p["after"]:
            return {"http_status": 200, "observed_intent": True, "write_acknowledged": False}
        if observed == p["before"]:
            step = ctx.service.db.execute("SELECT started_at FROM operation_steps WHERE operation_id=? "
                                          "AND name='pr.metadata.write'", (ctx.operation_id,)).fetchone()
            settled = settle_not_applied(ctx.service, ctx.operation_id, step["started_at"], observed)
            return {"not_applied": settled} if settled else None  # never PATCH twice
        return {"conflict_after_write": True, "observed": observed}
    w = await ctx.step("pr.metadata.write", write, request={"repository": repository, "pull_number": number,
                                                           **p, "fields": ctx.params}, reconcile=reconcile)
    if w.get("not_applied"):
        ctx.set_refs(metadata_settlement=w["not_applied"], verification_pending=False)
        raise OperationError("PR_METADATA_NOT_APPLIED", "metadata remained unchanged after the 10 minute settle window; "
                             "review a fresh digest before starting a new update", 409)
    if w.get("conflict_before_write"):
        ctx.set_refs(metadata_difference={**p, "observed": w["observed"]})
        raise OperationError("PR_METADATA_CHANGED", "metadata changed immediately before PATCH", 409)
    if w.get("conflict_after_write"):
        ctx.set_refs(metadata_difference={**p, "observed": w["observed"]})
        raise NeedsAttention("PR_METADATA_CONFLICT", "another editor changed PR metadata; no retry or undo was sent")
    if w.get("http_status") != 200:
        raise OperationError(f"GITHUB_{w.get('http_status')}", "GitHub refused the metadata update")
    ctx.set_refs(write_acknowledged=w.get("write_acknowledged"), verification_pending=True)
    previous = ctx.service.db.execute("SELECT name,status,response,seq FROM operation_steps WHERE operation_id=? "
                                      "AND (name='pr.metadata.verify' OR name LIKE 'pr.metadata.verify.retry.%') "
                                      "ORDER BY seq DESC LIMIT 1", (ctx.operation_id,)).fetchone()
    retry = previous and previous["status"] == "succeeded" and json.loads(previous["response"] or "{}").get("http_status") != 200
    name = (f"pr.metadata.verify.retry.{previous['seq']}" if retry
            else previous["name"] if previous else "pr.metadata.verify")
    async def verify():
        status, pr = await gh.pull(repository, number)
        try:
            _read_result(status, pr, "read back PR metadata", ctx)
        except NeedsAttention as exc:
            return {"http_status": status, "read_refused": True, "code": exc.code, "message": exc.message}
        return {"http_status": status, "observed": metadata(pr),
                "repository_id": (pr.get("base", {}).get("repo") or {}).get("id")}
    v = await ctx.step(name, verify, request={"after_digest": digest(p["after"])}, reconcile=reread)
    if v.get("read_refused"):
        raise NeedsAttention(v["code"], v["message"])
    if v["http_status"] != 200:
        raise NeedsAttention("PR_METADATA_UNVERIFIABLE", "cannot read back the metadata write")
    observed = v["observed"]
    ctx.set_refs(metadata_difference={**p, "observed": observed}, verification_pending=False)
    if observed != p["after"] or v["repository_id"] != p["repository_id"]:
        if w.get("write_acknowledged"):
            ctx.set_refs(metadata_reconciliation="PR_METADATA_CONFLICT")
            receipt = {"status": "conflict", "code": "PR_METADATA_CONFLICT", "observed": observed,
                       "settled_at": time.time()}
            save_metadata_settlement(ctx.service, ctx.operation_id, receipt)
        raise NeedsAttention("PR_METADATA_CONFLICT", "write readback differs; review before/intended/observed metadata")
    result = {"repository": repository, "repository_id": p["repository_id"], "pull_number": number,
              **observed, "before_digest": digest(p["before"]), "after_digest": digest(observed),
              "html_url": p["html_url"], "verified": True, "observed_intent": w.get("observed_intent", False)}
    return result


async def reconcile_metadata(ops):
    """Read unresolved cancelled writes as well; cancellation cannot make a late PATCH safe."""
    gh = ops.context.get("github")
    if not gh:
        return
    rows = ops.db.execute("SELECT s.operation_id,s.request,s.status,s.started_at,o.external_refs FROM operation_steps s "
                          "JOIN operations o ON o.operation_id=s.operation_id "
                          "WHERE (o.status='cancelled' OR o.cancel_requested=1 "
                          "OR (o.status='needs_attention' AND o.error_code='UNCERTAIN_UNRESOLVED')) "
                          "AND NOT EXISTS (SELECT 1 FROM pr_metadata_settlements m WHERE m.operation_id=s.operation_id) "
                          "AND s.name='pr.metadata.write'").fetchall()
    for row in rows:
        refs = json.loads(row["external_refs"] or "{}")
        unresolved = row["status"] in {"started", "uncertain"}
        if not unresolved and not refs.get("verification_pending"):
            continue
        p = json.loads(row["request"])
        try:
            status, pr = await gh.pull(p["repository"], p["pull_number"])
        except GitHubAmbiguous:
            continue
        if status != 200:
            continue
        observed = metadata(pr)
        if observed == p["after"]:
            if unresolved:
                ops._step_done(row["operation_id"], "pr.metadata.write",
                               {"http_status": 200, "observed_intent": True, "write_acknowledged": False}, reconciled=True)
            ops._merge_refs(row["operation_id"], {"verification_pending": False, "observed_intent": True,
                                                 "metadata_difference": {**p, "observed": observed}})
        elif unresolved and observed == p["before"]:
            settle_not_applied(ops, row["operation_id"], row["started_at"], observed)
        elif not unresolved or time.time() - row["started_at"] >= METADATA_SETTLE_S:
            ops._merge_refs(row["operation_id"], {"verification_pending": False,
                                                 "metadata_reconciliation": "PR_METADATA_CONFLICT",
                                                 "metadata_difference": {**p, "observed": observed}})
            if unresolved:
                receipt = {"status": "conflict", "code": "PR_METADATA_CONFLICT", "observed": observed,
                           "settled_at": time.time()}
                # Record refs first so a restart cannot skip them once the settlement excludes this row.
                save_metadata_settlement(ops, row["operation_id"], receipt)
