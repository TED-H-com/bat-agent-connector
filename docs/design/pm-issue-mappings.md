# Fixed work to GitHub Issue publication

This is the first W18 / A40 slice. One explicitly associated Connector WorkItem
maps to one Issue in one numeric GitHub repository. Engineering task order,
checkboxes, retries and a new package revision do not create additional issues.
This is a publication action, not coding authorization or completion evidence.

## Shared operation contract

`install(ops)` registers two `manage` actions; both also require `observe` and
current workspace write membership. They use the existing OperationService,
principal, exact idempotency key and journal. No second worker or queue is created.

`pm.issue.publish`:

```json
{
  "target": {"workspace_id": "ws_…", "work_item_id": "wi_…"},
  "params": {
    "package_id": "pmwp_…",
    "public_title": "Explicitly selected public title",
    "public_body": "Explicitly selected public Markdown"
  },
  "preconditions": {
    "expected_package_digest": "<64 hex>",
    "expected_mapping_revision": null
  }
}
```

An update supplies the exact previous `pmir_…` revision instead of null. Title is
1–200 characters; body is 1–32,000 characters. They are the only selected content
published. No plan, specification, evidence, private rationale, workspace ID,
checkout path or source link is automatically exported. The reserved opaque
operation marker is appended to support original-effect reconciliation.

The package must retain the original adopted plan, valid current scope, fixed
work fingerprint/association and current configured repository. Publication can
describe unresolved work; engineering readiness is not execution authorization.
Later publications must have the same work ID, original WorkBinding ID,
requirement IDs, numeric repository and repository name. A new scope association
requires explicit replanning; this action cannot silently repurpose the issue.
Technical tasks remain inside the work package. This slice does not offer an
issue-per-checkbox or task-key subdivision mode.

The first publication creates an issue. Later revisions create immutable new
comments on that exact native issue identity. They never patch the existing
title, body, labels, assignees, milestone or state. The comment contains the
explicitly selected public revision title and body.

`pm.issue.cancel` stops this workspace's mapping:

```json
{
  "target": {"workspace_id": "ws_…", "mapping_id": "pmim_…"},
  "params": {
    "rationale": "Private management reason",
    "public_reason": "Optional, explicitly selected public explanation"
  },
  "preconditions": {"expected_mapping_revision": "pmir_…"}
}
```

Omit `public_reason` for a purely local cancellation: no GitHub read or write is
required. Supplying it explicitly authorizes one append-only comment. Private
`rationale` is never included in that comment. Cancellation does not close the
issue, cancel coding work, stop a session, archive a repository, or imply a
release. An already cancelled mapping cannot implicitly reopen through publish.

## Durability and external concurrency

The mapping identity is derived from the workspace ID, WorkItem ID and numeric
repository ID. Original revisions and their package/publication digests, native
issue/comment IDs, operation IDs and actors are immutable. The database schema
is idempotent and does not allocate `user_version` or a data migration step.

A unique mapping reservation serializes different operation keys. While an
earlier POST is started, uncertain, or succeeded without its local record, a new
key cannot create another Issue. A prior definitively unsent/refused terminal
operation may yield its reservation. Once the original revision is recorded,
another explicit request must name that exact revision.

Repository identity and existing issue identity are read before the final
external step. The service then rechecks current membership, original source
visibility, scope, package, binding and cancellation immediately before sending.
The lifecycle integration binds the original PM execution epoch at operation
admission and checks each newly admitted `ctx.step`; stopped/reopened epochs
cannot introduce another unsent publication. Each create/comment has its own
durable step, using the shared operation machinery.

An unknown POST is never repeated. Recovery reads the same GitHub service and
numeric repository, scans a bounded original issue/comment collection, and
requires exactly one complete marker/content/native-identity match. Missing,
edited, duplicated, moved, incompletely paged or unreadable evidence remains
unknown. No search result, matching title, absence from a list, idle session, or
HTTP timeout is proof that a request was never sent. Successful response parsing
also validates the complete selected content and native identity; malformed
success replies remain uncertain.

The response receipt is followed by an atomic local revision/event transaction.
Confirmed external effects can finish local bookkeeping after cancellation,
source withdrawal or a lifecycle stop; public reads still enforce current
authority. A local rollback or lost response reuses the original external receipt
and never posts again. This does not bypass shared operation controls or clear
any unrelated lock.

GitHub's documented Issue PATCH does not provide an expected revision field.
The documented ETag conditional-request guidance applies to GET. A preflight GET
followed by PATCH would not establish an atomic protection against another
author's intervening edit. This implementation therefore uses creation and new
comments and makes no claim to safe destructive state/title/body synchronization.
Sources: [Issue update API](https://docs.github.com/en/rest/issues/issues#update-an-issue),
[conditional request guidance](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api#use-conditional-requests),
[Issue comments API](https://docs.github.com/en/rest/issues/comments#create-an-issue-comment).

## Read and integration contract

`get_mapping(ops, principal, workspace_id, mapping_id)` returns
`{version,workspace_id,mapping_id,current,revisions,execution_authorized:false}`.
The current revision and history retain their complete original documents.

`list_mappings(ops, principal, workspace_id, after=0, limit=50)` returns
`{version,workspace_id,mappings,next_cursor,coverage}`. Its cursor is the immutable
first revision sequence of each mapping, not mutable update time. Each entry is
the current complete revision. Clients refresh when mapping events arrive.
Responses are bounded to 2,000,000 actual JSON UTF-8 bytes; list pages retain a
whole-record prefix and forward cursor. Oversized complete detail fails with 413
rather than losing part of the public text or provenance.

Reads require current workspace membership and every original package source in
the mapping history, including a predecessor package's evidence. A withdrawn
source hides the complete quoted mapping, even if a later package omitted that
source. A filtered list can be empty with a forward cursor.

The integrator must route both actions through
`operation_visible(db, principal, operation)` before operation GET/list/wait,
same-key replay and events expose parameters, steps or results. This hook reads
the original durable admission context even before the first mapping exists.
Future revisions do not redefine an earlier operation's source scope. Event
resource `pm_issue_mapping`, event `pm.issue.recorded`, is resolved through its
original operation ID. General operation cancellation/recovery must retain the
same original source policy; hidden existing requests are still admitted and must
not receive `admission_refused: true`.

The core adds no ordinary artifact, new task, session, WorkBinding, execution,
PR, deployment, model call or live host operation. Shared HTTP/RPC/MCP/UI wiring
belongs to the integration slice. Remaining W18 work includes explicit external
issue-state synchronization policy, richer many-to-many associations and any
deliberate reopen/remap flow. This slice is not full W18 or M4 acceptance.
