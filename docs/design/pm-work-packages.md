# Fixed engineering work packages

This W35 increment freezes one reviewable engineering description for an existing
Connector work item. It implements the preparation portion of PM v1.2 §12.11 and
the fixed-source portion of A38. It does **not** implement coding handoff, W38
batches, dependency verification, execution authorization, or M4 delivery.

The existing journal and OperationService remain authoritative. A package creates
no task, attempt, session, worktree, ordinary artifact, PR, or deployment. Neither
plan adoption nor a `ready` engineering review result starts work. Source documents
are data: embedded slash commands, shell text, tasks and archive records are never
executed or adopted as scope. No OpenSpec file or human checkout is changed.

## One fixed package, several distinct sources

The immutable package records:

- The exact historical adopted plan revision, document digest, scope revision,
  original adoption operation and decision, and verified PM Git commit/tree.
- An original WorkBinding ID and digest, current work content fingerprint and
  version, and project identity/version. Its selected requirement IDs must belong
  to that binding's adopted plan. Requirement text, source status, acceptance IDs
  and their original InputRecord provenance are copied from that plan; the caller
  cannot replace them with invented requirements or confirmation claims.
- A separately selected **code** repository, numeric repository ID, binding
  digest, host/workspace target, full published commit SHA and source ref from the
  existing repository preview. A PM workspace's repository labels grant no path,
  network or execution authority. The selected repository also belongs to the
  explicitly associated Connector project.
- Optional fixed OpenSpec source IDs, paths, commit/blob identities, byte digests,
  complete original bytes, adapter/schema provenance and read coverage. W36 reads
  only its existing configured PM carrier and original pinned source commit. Code
  and specification commit identities are stored separately; neither is replaced
  by the local PM plan commit or a newer published head.
- Proportionate design text and a tailoring rationale, technical tasks and their
  dependency graph, proposed verification methods mapped to original acceptance
  IDs, and an intended delivery target and delivery considerations.
- Optional original evidence IDs/digests and a fixed Definition of Done revision.
  Shared DoD remains separate from a requirement's acceptance criteria. Both
  remain unassessed; a method, task checkbox or reported outcome is not a test.

The package has its own `pmwp_<original-operation-suffix>` ID, canonical SHA-256
document digest, immutable sequence, actor, timestamp and operation ID. Revising
it creates another package linked by `previous_package_id`; there is no update or
delete action. One predecessor can have only one successor. An existing attempt
or WorkBinding is never changed by package creation.

## Registered action

`pm.work_package.create` requires `observe` and `manage` scopes and current write
membership. Owners and contributors may describe implementation; this does not
give contributors authority to adopt product scope. Readers may read but cannot
create. Existing explicit authenticated administrator membership behavior is
preserved. Retry/cancellation retain the existing OperationService management
rules plus current workspace write membership and original source visibility;
these are local management operations, with no session control authority.

Exact target:

```json
{"workspace_id":"ws_…","work_item_id":"wi_…"}
```

Required parameters:

| Field | Contract |
| --- | --- |
| `title`, `rationale` | Nonempty UTF-8 text, at most 200 / 4,000 characters |
| `requirements` | 1–100 distinct requirement IDs from the selected WorkBinding |
| `source_refs` | 0–20 exact `{repository,commit_sha,path,source_id,sha256}` objects; supported W36 paths only |
| `code_source` | Exact repository preview `{repository,host,workspace_id,source_ref,source_sha,repository_id,binding_digest}`; its workspace ID is the BAT target, distinct from the PM workspace ID |
| `design` | `{status,text,source_ids}`; `described`, `not_applicable`, or `unresolved`; up to 128,000 characters |
| `tasks` | 1–100 `{key,description,requirement_ids,depends_on,source_ids}`; distinct stable keys, selected requirements, same-package dependencies and no cycles |
| `verification` | 1–100 `{key,requirement_id,acceptance_input_ids,method,expected_evidence,source_ids}`; acceptance IDs must belong to that exact requirement |
| `delivery` | `{repository,repository_id,base_ref,goal,migration,rollout,rollback,observation}`; an `integration_pr` goal additionally requires a positive `pull_number` |

Delivery `goal` is `reviewable_changes`, `pull_request`, or `integration_pr`.
Migration, rollout, rollback and observation each use `{status,text}`, with the
same three statuses as design. Small work can give a short explicit reason why a
consideration does not apply. An unresolved consideration blocks the handoff
review; saving the draft remains possible. Delivery target identity is an intent,
not proof an existing PR/environment is available or authorization to merge/deploy.

Optional parameters are exactly `previous_package_id`, `evidence_refs` (up to 30
`{evidence_id,fact_digest}` records), `dod_revision_id`, and `unresolved` (up to 30
nonempty questions). No supplied receipt, actor, status, completion claim, accepted
text, runtime prompt or execution flag is accepted.

Exact preconditions:

```json
{
  "expected_binding_id":"pmwb_…",
  "expected_work_fingerprint":"<64 hex>",
  "expected_scope_revision":"pmscope_…",
  "expected_previous_package_id":null
}
```

The last field is the exact previous package ID for an explicit revision. Current
scope must match the bound plan's adopted scope; simply observing a newer plan
cannot rebind existing work. A document-only plan revision with unchanged scope is
compatible. A scope change or prose change with unknown scope impact requires
explicit replanning and a new association/package.

## Admission and durable behavior

Admission stores principal, original plan/work/evidence/DoD/repository context and
description digest in `admission_binding`. It never edits the original public
target, params or preconditions: durableForm exact-envelope retries retain the
same request and key.

The existing worker performs `pm.package.sources`, a pure bounded read. It checks
current membership, configuration, work fingerprint, original association, scope
and package predecessor before and after repository preview and W36 reads. A
saved successful source receipt can be reused; an unfinished read may run again
because it has no mutation or execution effect. It must still return the same
fixed identity; moving the published code ref fails instead of substituting it.

`pm.package.record` atomically inserts the immutable complete package, local
receipt and event. It repeats the current guards and validates fixed bytes,
digests and provenance before committing. Cancellation or withdrawal before this
transaction prevents creation. Recovery first returns a succeeded original local
receipt, even after later membership loss, cancellation, a changed plan or moved
head. It neither repeats the read nor creates a new package. Public access to that
receipt independently rechecks current authority. Unknown results are never
repaired by creating another operation or clearing a lock.

The idempotent schema uses its own append-only table/triggers and no new
`user_version` or migration data step. Original work, attempts and PM plan rows are
unchanged.

## Read and visibility contract

Within one synchronous immutable snapshot, repeated original-source contexts
reuse the existing read memo by full principal, workspace and the exact
plan/work/evidence/Git/DoD inputs consumed by that predicate. Raw immutable
package metadata is decoded and verified once, with isolated document copies
for every caller. Completed ordinary context booleans can join a later guard;
conditional proofs stay private to their traversal, and errors or refusals do
not become ordinary grants. Writes or a new frame require fresh
proofs. Current configuration, lifecycle, membership at each new frame and
execution eligibility retain their existing checks.

`get_package(ops, principal, workspace_id, package_id)` returns
`{version,workspace_id,package}`. The package contains sequence, identifiers,
previous package, document digest, actor, timestamp, operation and full document.

`list_packages(ops, principal, workspace_id, *, work_item_id=None, after=0,
limit=50)` returns `{version,workspace_id,packages,next_cursor,coverage}`.
`after` is a nonnegative immutable sequence, `limit` is 1–100. Coverage reports
returned/scanned counts, source filtering, byte limiting and whether more rows
remain. Wall-clock changes cannot reorder records.

Each read rechecks current membership and **original** sources: original adoption
operation, immutable work binding, evidence IDs/fact digests, DoD revision, and
recorded PM source identity. It does not use the latest capture, plan or Git head
as a substitute. A revised narrative may quote a predecessor, so the whole
previous-package lineage remains part of its source policy even when explicit
evidence IDs are later omitted. Lineages are bounded to 256 packages.

Withdrawn evidence hides the complete quoted narrative. List filtering may return
an empty page with a forward cursor; consumers must continue by that cursor. No
source text is partially returned. Missing configuration does not erase a prior
workspace-owned snapshot: historical read access uses current PM membership and
original source visibility, while readiness/new creation checks current repository
configuration. Git history reads do not fetch or reread a human checkout.

All package/list/readiness responses fit 2,000,000 bytes using actual JSON wire
spacing and UTF-8 encoding. A stored complete package is limited to 1,500,000
bytes, including source bytes. Lists use a whole-record prefix; an oversized first
visible record yields a clear HTTP 413 error. They never skip an oversized visible
record or truncate a task, acceptance criterion or source document. A narrowed
read budget that cannot contain a full detail also yields 413.

The integrator must invoke DB-only
`operation_visible(db, principal, operation)` for this action before exposing
original params, step receipts or results through operation GET/list/events/MCP.
It loads the immutable original admission context by operation ID. It does not
construct a new service or borrow the operation creator's principal. Global event
integration must also consult that original operation, because event metadata is
not a separate source authorization. The shared entrypoints below apply this policy to the original admission even
before a package is recorded.

## Shared entrypoints

The operation catalog installs `pm.work_package.create`; creation uses the existing
`POST /api/v1/operations`, idempotency key, scopes, current membership and durable
receipts. The `features.pm_workspaces.work_package_prepare` flag advertises this
preparation action, separately from PM analysis context packages or execution.

All reads require `observe` and current workspace/source access:

| HTTP GET | RPC / MCP |
| --- | --- |
| `/pm/workspaces/{workspace_id}/work-packages?work_item_id&after&limit` | `pm_work_packages_list` |
| `/pm/workspaces/{workspace_id}/work-packages/{package_id}` | `pm_work_package_get` |
| `/pm/workspaces/{workspace_id}/work-packages/{package_id}/readiness` | `pm_work_package_readiness_get` |

HTTP paths are relative to `/api/v1`. List selectors are optional; detail and
readiness accept no query selectors. Unknown, repeated, blank or invalid
selectors fail rather than becoming defaults. MCP exposes these as read-only
tools and requires the caller's token; it never falls back to local admin. The
native bridge allows exactly the same read routes and bounded selectors. It
provides no direct package write, start or dispatch route.

Each public read uses the same synchronous journal contract. It never rereads
Git files, fetches, starts an adapter or changes package/work state. Readiness
reports only current local engineering review checks. Global operation GET/list,
operation events and `pm.work_package.created` events all consult the immutable
original admission and full predecessor source lineage. Withdrawing an original
source hides a later package that omitted its explicit evidence reference as well
as its operation parameters and event metadata. A pending admitted operation is
protected before a package row exists. Filtered list pages retain their forward
cursor, including pages with no visible packages.

## Engineering review readiness

`readiness(ops, principal, workspace_id, package_id)` performs current local
checks and returns explicit passed/blocked reasons. It requires:

- Current adopted scope, active work, fingerprint and association match the
  original package; this is the latest explicitly prepared package for that work.
- Current configured code binding and project repository association still match.
- Design and delivery considerations have descriptions or tailoring rationales;
  no unresolved engineering questions remain.
- Selected spec/code commits match. Differing commits remain valid stored facts,
  but this increment has no compatibility assessment and reports the gap.
- Every selected requirement has technical task coverage and every nonempty
  original acceptance set has a verification method. Source statuses remain
  user-confirmed; mere plan adoption does not convert an unresolved input.
- No unevaluated requirement dependency is treated as satisfied. Requirement
  dependencies block with an `unknown` assessment; checkbox/work claims do not
  settle them. Task graph edges describe order, not already-completed work.

`status: ready` means only `ready_for: engineering_handoff_review`. The response
always says `execution_available: false`, `execution_authorized: false`,
`dispatch_readiness: not_implemented`; runtime, budget and remote delivery target
are not checked, and quality remains unassessed. These names must not be shortened
to an executable or delivered status in the UI.

## Acceptance and remaining boundary

The focused tests use real temporary Git repositories behind mock transports.
They cover fixed adopted requirements and full source bytes, dirty checkout
preservation, no dispatch/artifact side effects, competing writers, exact-envelope
replay, original receipt recovery after withdrawal, stale work/scope rejection,
source-chain withdrawal, unknown dependencies, separate DoD and acceptance, and
whole UTF-8 package pagination/413.

This contributes to W35, A35 (no invented adopted feature), and A38's fixed-source
preparation. It does not claim A38 coding handoff or A39 verification complete.
Later W19/W38 integration must admit a package reference into the existing
repository dispatch command, recheck the original caller and current package
eligibility, map it into the new owned attempt's immutable execution specification,
and reconcile the original start/send outcome. It must preserve separate spec/code
commits, source policy and native output format; no second queue or session takeover
is introduced here.

## Shared review view

The workspace's Web/Tauri page lists readable packages and follows forward
cursors even through empty source-filtered pages. Selecting a package reads its
fixed document and current local readiness, checks their shared ID/digest, and
shows requirements, item acceptance, design, tasks, verification methods,
delivery considerations and the separately selected Definition of Done.
Source documents are literal text; code and specification commits remain distinct.

Refresh removes old source text before current reads. Permission/source failures
clear the previously quoted title and detail; leaving the page retires late
responses. A later readable list allows an explicit new selection, without
silently substituting a newer package for the original one. This view does not
create a package, execute a shell command, dispatch, verify quality or deliver.
Authorized agents prepare packages through the same registered operation.
