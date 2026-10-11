# Owned pull request publication

`github.pr.publish` closes the gap between an owned PM engineering result and a
new central-owned GitHub pull request. Existing `integration.apply` modifies an
already selected PR and does not prove its creation. This action uses the existing
`integrate` scope, with `observe` and `manage` for its PM sources. It does not merge,
deploy, accept a requirement, or silently turn provider candidates into ownership.

## Fixed request and authority

```
target: {workspace_id, repository, host, source_operation_id}
params: {candidate_sha, base_ref, public_title, public_body, draft, quality_ref?}
preconditions: {repository_id, expected_base_sha}
quality_ref: {review_id, operation_id, version, document_digest}
```

The source is an original successful `repository.continue` operation with a fixed
engineering package and accepted managed execution creation evidence. The original
controller, source/provider/native repository/host, original request digest,
managed worktree identity and candidate/base SHAs are saved at admission. A fresh
single credential must authorize the original actor, and membership, source access,
package readiness, configured host tiers, lifecycle epoch, controller and managed
resource policy must still permit every new effect. Manual sessions and worktrees
are never mutation targets. No caller can select a publication branch, a repair
parent, a lease, a native PR identity or a preexisting ownership receipt.

For a maintenance repair, only the trusted private original repair-child relation
establishes ancestry. The parent additionally records its exact publication child
request. `require_publication_child` checks that record before new effects.
`require_publication` checks the explicitly selected immutable quality assessment,
original candidate/tree/package/binding/scope/DoD, accepted review and complete
allowed paths immediately before the branch push and POST. The original adopted
policy, current credential, deadline and delivery ceiling still apply. W20 roots
provide privacy; they do not establish this creation relationship.

## Bounded branch creation

The managed integration bare repository is reused, including its isolated local
Git configuration, disabled hooks, exact host/repository markers, physical path and
symlink checks, and mutex. The original execution stays read-only. Its current tip
must equal the selected full candidate SHA. That candidate must descend from its
original creation base and from the selected expected PR base. The fixed change is
limited to 200 commits, 500 complete changed paths and 128 KiB of path output.
Rename detection is disabled so both removed and added paths are included. Git
quotes control characters; the central receipt preserves decoded exact names.

The only pushed ref is generated from the durable operation ID:
`refs/heads/batc/pr-<operation hex>`. The only lease is the explicit empty old OID
for that exact ref. The only refspec is the pinned candidate to that ref. This is
create-only compare-and-set, including when a racing foreign ref is an ancestor or
already names the same candidate; no existing ref is rewritten or adopted. The
provider base is checked again and the final local authority check runs while the
integration mutex is held immediately before the host frame.

An original successful porcelain `[new branch]` result is saved in the guarded
integration area before returning the host response. Recovery requires that exact
intent-bound host receipt and an observed remote head equal to the candidate. A
matching remote SHA without the original receipt remains unresolved. A host crash
between the push and receipt write therefore needs operator investigation; it
cannot trigger another push or infer branch ownership from its generated name.

The [Git push documentation](https://git-scm.com/docs/git-push) specifies that an
explicit empty expected value requires the named ref to be absent. No other
force-with-lease form, force flag, arbitrary refspec or deletion is exposed here.

## Original PR creation receipt

Before sending, bounded GETs recheck the provider's native repository, exact base
and generated branch candidate. The original POST contains only the selected
public title/body, generated head, selected base and draft flag;
`maintainer_can_modify` is false. Redirects are refused. The caller deadline is
30 seconds and the response body limit is 256,000 bytes; at most one abandoned
POST worker per client can remain outstanding. Late data is not reused by another
operation. Each provider GET is limited to 20 seconds and 256,000 bytes. Each Git
host frame is capped at the smaller of 120 seconds and the configured integration
timeout. A repair's remaining absolute deadline additionally bounds every new
GET, host frame and POST; original receipt recovery is separate from investment.

Only a complete original HTTP 201 response can produce a native creation receipt.
Its native repository/PR ID/node ID/number, provider origin, exact source child,
child request digest, optional trusted repair parent, candidate/tree/base/branch,
quality reference and original response digest are recorded in append-only
`pr_creation_receipts`. The receipt insertion and a local durable step receipt
commit in one journal transaction before the outer external POST step completes.
This is receipt-only bookkeeping and remains allowed after a stop or withdrawal.
Recovery first settles this original receipt, without repeating the POST.

The [GitHub creation API](https://docs.github.com/en/rest/pulls/pulls#create-a-pull-request)
documents 201 as creation success and selects branches by name. Its documented
request schema has no head/base SHA compare-and-set or idempotency field. Thus
preflight reads cannot eliminate a race during POST. A 201 with different observed
head/base preserves its native identity with `target_verified=false` and requires
attention; it cannot pass the strict repair delivery ownership seam. Publication
does not claim that later branch state or future verification is unchanged.

If the original 201 is lost, one bounded GET page may expose a candidate count as
unresolved recovery evidence. Branch, text, number, node ID, marker or matching
commit found later is never promoted to an original creation receipt. Even an
empty complete search does not prove that the POST was unsent. The original
operation/key and uncertain step are retained; the POST is never resent.

## Read and integration contract

`install`, `capabilities` and `operation_visible` integrate with central action and
read surfaces. Root entrypoints register this derived action for PM source lineage
and install the ordinary operation/event/source read gates before advertising it.

`get_publication(ops, principal, operation_id)` returns operation status, optional
creation receipt and unresolved recovery summary. `list_creation_receipts(ops,
principal, repair_parent_id, child_operation_id, after=0, limit=20)` returns
`{creation_receipts:[{sequence,receipt}],next_after}`, capped at 50 candidates per
page. Source-filtered pages preserve the original cursor even when empty.

`require_creation_receipt(ops, principal, publication_operation_id, *,
child_operation_id, provider_origin, repository_id, repair_parent_id=None,
native_pr_id=None)` requires the complete original receipt, `target_verified=true`
and all supplied exact identities. It is a read helper, so a stopped or expired
repair may still inspect its receipt if the caller currently has all original
source access. Withdrawal hides receipt metadata, operation evidence and linked
delivery evidence. No read helper invokes the repair investment guard.

Offline validation uses task-owned disk Git repositories and fake HTTP providers.
Neither CI nor these tests constitute live GitHub publication acceptance.
