# Bounded selected engineering batches (W38)

A batch composes existing `repository.continue` operations. It has no session
engine, second worker queue, provider/model adapter, or delivery engine. One
explicit selection fixes each package, code basis, source preview, WorkItem and
optional immutable spec artifact before any child is admitted.

## Commands and fixed selection

`pm.work_batch.start` requires the existing `observe`, `manage`, and `start`
scopes plus current workspace write membership and original controller authority.
Its target is `{workspace_id}`. Preconditions fix `expected_workspace_version`
and `expected_lifecycle_revision`. Params contain `entries`, `limits`, and a
bounded nonempty `rationale`.

Each of 1–16 entries has a distinct stable `key`, a `handoff` object containing
exact existing `target`, `params`, and `preconditions`, and `depends_on`. The
child action is always `repository.continue`; callers cannot provide its key,
authority, parent link, action, or lineage. Every entry must name an immutable
`pm_package_ref` in this workspace and the exact WorkItem/code/source contract.
Duplicate selected WorkItems, dangling dependencies and cycles are refused.
Requests have a whole UTF-8 byte bound. No latest package/source substitution or
truncation is allowed.

Limits are `max_starts` (1–16), `max_parallel` (1–8, no greater than max_starts),
`deadline_at` (finite absolute time, at most 24 hours ahead at initial admission),
and `max_retries` (0–3 original child worker retries before another unsent frame).
A start budget is consumed atomically by child operation INSERT, never released
because its outcome is unknown. Retry limits do not authorize a new child key or
replay an uncertain send. Token/provider cost remains unmeasured and is reported
as such; a start count is not a monetary spending limit.

The existing operation key fixes the entire selection. Same-key replay returns
that original operation under current access checks. The batch ID is
`pmbt_<original operation hex>`. Entries and original child links are immutable.
Current states and whole rejection reasons are journal projections, not edits to
the fixed request. One blocked entry does not discard independently eligible
selected entries.

## Authority, capacity, and recovery

The original actor/controller must still have one current credential carrying
all required scopes, current workspace write membership, original source access,
and the original lifecycle execution epoch. Scope union across credentials is
not allowed. Parent and child envelopes, generated keys, action, actor, controller,
and private persisted link must all match. Caller-provided parent metadata
cannot obtain this authority.

Before each new child INSERT, new external step/proven-unsent retry, and final
unsent BAT/Git frame, recheck the parent deadline, cancellation, lifecycle,
current credential/controller, fixed source access, and that child's reserved
capacity. Completed receipts and original sent-effect readback precede these
new-effect guards. Unknown start/send outcomes retain their original key and
capacity; no failure inference causes another start.

An admitted handoff counts against parallel capacity until its original dispatch
failed/cancelled with no uncertain external step, or authoritative fixed-result
verification settles that entry. `repository.continue` success only proves its
start/send contract, so it does not by itself release capacity for another entry.
An unavailable/stale result stays unresolved. Once all selected admissions and
original child dispatch operations have settled, the parent may finish with a
plain dispatch summary; it does not claim the running engineering sessions are
Done. A deadline, cancellation, pause/close, stale epoch or lost authority stops
further admission without killing existing or foreign sessions.

The parent uses only the existing operation worker and its bounded waits. It
never resumes another controller's operation. Its existing cancel action stops
new child effects through the private parent guard. Original child receipt
recovery remains available independently of a stopped parent.

## Prerequisites and immutable result evidence

An edge names a selected upstream entry and the explicit condition
`fixed_revision_verification`, with a fixed registered verification profile.
This condition is distinct from semantic requirement acceptance or DoD. Start
success, session silence, task checkboxes, WorkItem approval, prose, external
verification testimony and branch-name resemblance do not satisfy it.

The first registered profiles are:

- `observed_runner_v1`: an existing Task Service observed verification ID whose
  exact central task/session relation points to the original upstream child,
  whose full candidate commit/tree and output digest are valid, and whose
  observed exit status is zero. The required command digest is fixed by the
  selected condition. A missing authoritative relationship leaves it blocked.
- `github_reported_checks_v1`: an immutable complete W47 observation for the
  exact provider, numeric repository, and full head SHA. A fixed existing
  integration receipt must identify the original child as its source and prove
  delivery at that same SHA. All reported check runs and statuses at this head
  must pass, with nonempty verification evidence. Incomplete traversal, missing
  check evidence, mixed identities, pending/failed checks or withdrawn sources
  block it. This profile proves the reported checks only, not every conceivable
  test or an unstated required-check policy.

`pm.work_batch.evidence.record` records selected immutable evidence references
for original entries. Its target is `{workspace_id,batch_id}`; params contain
`evidence` and `rationale`; `expected_version` guards its append-only revision.
The command neither edits selected envelopes nor changes their original deadline,
budget or lifecycle epoch. Read-only original results can be recorded after a
stop, but cannot reopen investment. A waiting original parent may consume a
new proof only while its original investment guards still pass.

A batch proof never bypasses an intrinsic package readiness check. In particular,
unknown requirement dependencies remain unknown until their own semantic result
authority exists. Durable entry states distinguish absent prerequisite evidence,
failed verification, source unavailability, fixed-package unreadiness, exhausted
capacity, deadline, and original sent-effect uncertainty. Later evidence is a
new immutable revision, never a rewrite of the earlier blocked finding.

## Reads and shared integration interface

Domain module: `pm_work_batches`. Export `install(ops)`, `capabilities(ops=None)`,
`operation_visible(db,principal,operation)`, `get_batch(ops,principal,wid,batch_id)`,
`list_batches(ops,principal,wid,after=0,limit=50)`, and
`require_child(ops,operation)` for shared unsent-step/frame guards. IDs use
`pmbt_`; events use resource type `pm_work_batch` and original batch/operation IDs.

Suggested shared reads are GET `/pm/workspaces/{wid}/work-batches` and
`/pm/workspaces/{wid}/work-batches/{batch_id}`, RPC/MCP
`pm_work_batches_list` and `pm_work_batch_get`. Writes remain registered actions.
Reads require observe and current membership, hold one journal snapshot, and
check all original selected package/evidence roots. An inaccessible fixed batch
is refused whole; list scans omit whole rows while preserving their signed
64-bit cursor. Responses use actual public JSON spacing/UTF-8 bounds, bounded
whole entries and explicit scope/verification/cost coverage. Reads create no
operations and contact no provider or BAT host.

Tests use temporary real Git and fake existing transports. Mixed independent /
blocked entries, duplicate selection, exact-key recovery, native result identity,
late source withdrawal, pause/deadline between child admission and final frame,
lost start/send acknowledgement, unchanged foreign resources, and both supported
Python runtimes are acceptance evidence. This document alone is not completion.
