# Workspace lifecycle and stopping new investment

This implements the first W43/W50 lifecycle slice from v1.2 §7.6, §13.13,
§14.4 and §19.5 (A49, A61–A63). It uses the existing Connector operation worker,
SQLite journal and PM writer. It does not create a scheduler or a second delivery
engine. Stage changes do not alter thread mode, Sprint commitments, work items,
attempts, or native sessions.

## States and explicit decisions

New workspaces begin in `idea`, lifecycle revision 0 and execution epoch 0.
Existing workspaces without a lifecycle head read as `stage:null`,
`coverage:legacy_unclassified`. Reads do not provision a phase or infer one from
sessions, plans, worktrees or repository names. There is no bulk data migration
and no new `user_version` allocation.

The active stages are `idea`, `planning`, and `development`. An explicit stage
label does not adopt a plan, dispatch work, or authorize an agent run. These
retain their existing operation and scope requirements. Independent Idea
threads can remain open while the workspace is in Development.

`paused` and `closed` immediately stop new investment once their decision commits.
Their projected status is `pausing` or `closing` while any captured external
step or writer remains unresolved and has not been explicitly retained. The
original operation may still be running or awaiting read-back. A finished stop
is not evidence that all requirements are Done, that another controller's work
stopped, or that an external repository was archived.

`maintain` is deliberately unavailable: this slice has no ReleaseBaseline or
adopted maintenance policy. Choosing it returns `PM_MAINTENANCE_UNAVAILABLE`.
Release remains an independent delivery fact; no release success is invented.

All lifecycle decisions require `manage` and an actual current `owner` membership.
An administrator without that membership cannot silently decide product direction.
The owner is checked again when the local decision commits. Replay of an already
committed original request needs current membership and its source authority,
not a second decision or an extra `observe` scope.

## Public domain contract

`install(ops)` registers these exact actions:

| Action | Params |
| --- | --- |
| `pm.lifecycle.set_stage` | `stage`, `reason` |
| `pm.lifecycle.pause` | `reason` |
| `pm.lifecycle.close` | `reason` |
| `pm.lifecycle.reopen` | `stage`, `reason` |
| `pm.lifecycle.retain_inflight` | `transition_id`, `operation_ids`, `reason` |

Every action targets exactly `{workspace_id}` and requires
`{expected_lifecycle_revision: <nonnegative integer>}`. Reasons contain 1–4000
UTF-8 characters. Active stage choices are the three listed above. `set_stage`
cannot reopen a stopped workspace. Closed workspaces require `reopen`; pausing
can become closure with an explicit close decision. Revision conflicts refuse
fresh decisions. Caller params and preconditions are never enriched or rewritten.

`retain_inflight` names the current exact transition (`pmlt_[0-9a-f]{32}`) and
1–100 distinct original operation IDs. Each must have been captured, still be
unresolved, and remain visible to the owner. Its reason and attribution are
immutable history. Retention accepts ongoing custody of that original unknown;
it does not make a receipt successful, cancel a process, transfer its controller,
release the PM writer, or authorize another attempt. Another retention decision
must use the new current lifecycle revision and transition.

Action receipts contain `workspace_id`, `transition_id`, `revision`, `stage`,
`execution_epoch` and the status at that decision. Retention adds `retained_count`.
The immutable original receipt remains replayable after later lifecycle changes.
Current settlement is read from the lifecycle projection rather than rewriting
that receipt.

Read functions all receive `(ops, principal, workspace_id)` and require `observe`
plus current membership:

- `get_lifecycle(...)`: `lifecycle` head and projected status; optional current
  `transition`; `transition_visibility_filtered`; `blocked_actions`,
  `record_actions`, `available_stages`, unavailable `maintenance`, and
  `inflight_summary`.
- `list_transitions(..., after=0, limit=20)`: whole metadata records under
  `transitions`, `next_cursor`, and `visibility_filtered`.
- `get_transition(..., transition_id)`: fixed `transition` metadata and a current
  `inflight_summary`.
- `list_inflight(..., transition_id=None, after=0, limit=20)`: whole records under
  `inflight`, selected `transition_id`, `next_cursor`, `visibility_filtered`.
  Only `None` means the current transition; invalid selectors cannot fall back.

Metadata has `sequence`, `transition_id`, `workspace_id`, `revision`, `action`,
`from_stage`, `to_stage`, `execution_epoch`, `reason`, `actor`, `created_at`,
`effective_sequence` and a fixed local management `snapshot`. Inflight records
contain `sequence`, original `operation_id`, `action`, `actor`,
`operation_status`, `captured_at`, `disposition`, unresolved `steps:[{name,status}]`,
optional `writer:{run_id,generation,status}`, and optional
`retention:{reason,actor,created_at,transition_id}`. They expose no process PID,
path, raw request or response. Disposition is `unresolved`, `retained`, `settled`
or `held` (an old queued intent with no unknown external effect).

`capabilities(ops)` returns `{version:1,gated_actions,record_actions}`.
`gated_actions` contains the exact registered PM investment actions with a bound
workspace, excluding global workspace creation. `get_lifecycle.blocked_actions`
contains this set while stopped. UI recovery of an admitted original key is
separate from enabling a new intent. The backend gate is authoritative.

Reads use the actual JSON encoding budget, including spacing/escaping, bounded
at 2,000,000 bytes. Page cursors are nonnegative SQLite 64-bit integers and limits
are 1–100. Pages keep whole records in order. A single oversized mandatory record
returns 413; it is never cut or skipped. Source-filtered pages may be empty while
the cursor advances. Any nonterminal cursor must move forward.

## Serialized stopping and execution epochs

Lifecycle transitions, original operation INSERT, and fresh external step start
or proven-unsent restart use the same `Journal.tx()` writer transaction
(`BEGIN IMMEDIATE`). The stop's `effective_sequence` is its committed journal
event sequence. The operation's domain admission callback alone is not the
final barrier: it runs before the operation INSERT transaction, so a stop can
win after that callback. The final epoch capture/check is inside the INSERT.

Pausing, closing and reopening increment the execution epoch. A new operation
captures that epoch in the private immutable lifecycle operation table, never in
caller params. A stage-label revision alone does not invalidate active work.
An old queued or proven-unsent operation cannot acquire the reopened epoch.
Explicit new intent is required; reopening does not clone, retry, or replay a
queue. Pre-feature operations without an epoch binding are treated as epoch 0.

Every `OpContext.step` fresh start records its lifecycle admission atomically with
the step's started receipt. This is the external admission point. A pre-stop
admitted bounded step counts as inflight even if its HTTP request or native turn
has not yet been sent. It may finish after the stop. The next separate step must
pass the new lifecycle check. Callers must make separate external actions separate
steps; a future create-issue and later comment are two steps, for example.

A started/uncertain step first runs its original reconciliation. A proven result
is saved without new admission. `RERUN` proves the old effect did not occur; it
must pass the current epoch before any send. If stopped or from an older epoch,
that proven-unsent step is marked failed with the lifecycle reason and is not
misrepresented as still unknown. A partial Git transaction may complete only
through its existing deterministic exact-original-transaction recovery contract.
No generic retry or cancellation rule is loosened.

Local `OpContext.effect` checks lifecycle inside the local mutation transaction,
not just at its earlier started marker. A started local receipt means its effect
transaction rolled back; it does not authorize that mutation after stopping.
Older direct-journal PM handlers check after their original receipt lookup and
inside their mutation transaction. Original committed local receipts return first.

`receipt_only=True` retains its existing strict proven-bookkeeping meaning.
`lifecycle_bookkeeping=True` allows the verified native reply import after stop
while preserving the ordinary cancellation check and every current source,
requester and native-completeness check. Neither flag may mark planning, scope
adoption, a new runtime, or a new external call as result recovery.

The small generic hooks also accept `pm_lineage:{workspace_id}` from a trusted
registered action admission binding. This supports future non-PM-prefix coding
handoffs without creating another execution engine. Caller params cannot provide
that authority. Existing independent human/other-controller coding operations
have no such lineage and are not paused, signalled or rewritten by this feature.

## Existing effects and permitted local records

| Existing path | Stop boundary and recovery |
| --- | --- |
| Native PM / Follow-up analysis | Freeze/new writer and `pm.native.execute` admission gated; an admitted single process/turn may finish; exact terminal/death proof import allowed; unknown keeps original process/run/writer |
| `pm.run.prepare` | Fixed selection, fetch, owned carrier and snapshot are separate admitted steps; later steps stop; proven preparation receipt may finish |
| `pm.plan.adopt` | Freeze/fetch/carrier/commit gated; original Git read-back and receipt saving allowed; the new accepted-plan decision is separately gated |
| Fixed engineering package | New source-read step and new package record gated; existing original package receipt remains replayable; no coding dispatch exists here |
| Plans, discussions, Roadmap, Sprints, meetings, governance | Local investment decisions gated in their journal effect transaction; existing domain ownership/versions/sources unchanged |

The explicit exceptions are local manual records/results with no runtime or
external action: workspace membership; message append and input record (not
confirmation/Planning); evidence capture; local snapshot creation; Roadmap
observation over fixed evidence; meeting notes/completion/follow-up result record;
linking an already adopted governance decision; Sprint close/cancel. These still
use their existing authorization, fixed sources and versions. Paused/Closed is
not permission to create new analysis, plan draft/adoption, work selection,
meeting setup, engineering package, or scope decision. If an excepted action is
later extended with a new external step, that step still has to pass lifecycle.

## Closure/reopen snapshots and source visibility

Each decision records current local workspace version, accepted-plan head,
journal position and explicit coverage. ReleaseBaseline and maintenance policy
remain null until implemented. The snapshot says external observation was not
requested, delivery is unassessed, unfinished work is unchanged, and automatic
resumption is false. Reopen refreshes these local heads; it does not call remote
services, dispatch work, or claim fresh release/health verification.

Original inflight refs are captured separately rather than serialized into an
unbounded decision receipt. Current external-step status and writer receipts
provide settlement. Failed/terminal operations with a still unknown external
step remain visible and unresolved. Retention can settle the management stop
without erasing that uncertainty.

Every inflight row is checked against the original operation's **current** source
visibility. Withdrawn rows, their IDs and paths are not returned; summaries expose
only visible counts and a filtering flag. A retained decision can quote those
sources in its reason, so its transition detail, history, original operation and
events also use that original-source policy. The safe lifecycle head remains
readable when that metadata is hidden. A retention batch can include several
source domains; even the retention reason shown beside a still-visible operation
must pass visibility for the whole original batch. Otherwise the row keeps its
safe `retained` disposition but omits retention details and marks
`retention_visibility_filtered:true`. UI must not treat a hidden transition as
an active workspace or permission to submit new work.

This slice does not archive GitHub repos, close external issues/PRs, delete
worktrees, revoke Fleet access, stop production, or signal another controller's
session. Maintenance schedules and new GitHub delivery paths must integrate the
same barrier when they are implemented; they are not claimed complete here.
