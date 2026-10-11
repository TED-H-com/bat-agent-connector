# Workload observations after release

This W45 slice implements v1.2 §13.9 and the workload evidence portions of
A51/A52/A60. It stores a fixed workload observation against an adopted maintenance
policy and a verified release baseline. It never changes lifecycle state, starts
a session, refreshes a provider, dispatches a repair, or rewrites a work claim as
Done. W46 policy adoption and W47 scheduling remain separate responsibilities.

## Original context and action

`pm.workload.capture` uses the existing operation worker and a single local
`ctx.effect` transaction. The action requires `manage` and current writable
workspace membership; it is an observation/record action, also available while
Paused or Closed. Reads require `observe` and membership. The controller does not
acquire any other session's control rights.

The exact request is:

```json
{
  "action": "pm.workload.capture",
  "target": {"workspace_id": "ws_<32 hex>"},
  "params": {"policy_id": "pmmp_<32 hex>"},
  "preconditions": {
    "expected_policy_version": 1,
    "expected_baseline_id": "pmrb_<32 hex>",
    "expected_lifecycle_revision": 0
  },
  "idempotency_key": "the original stable operation key"
}
```

The policy's `active_record` supplies scope, source identities, maximum observation
age and stability period. No public parameter supplies idle, complete, freshness,
controller identity or an alternative workspace path. Admission freezes policy
digest, current compatible baseline digest, lifecycle revision and execution
epoch in private admission metadata. The public request stays byte-for-byte
equivalent to its original structured fields.

The worker rechecks that context inside the snapshot insertion transaction.
Changes before this local effect require a new explicit request. After insertion,
the same effect receipt recovers the same `pmwl_<original operation suffix>`;
cancellation, pause, source withdrawal or lost reply cannot cause another capture.
Public replay still checks the original sources before exposing the receipt.

## Coverage and classifications

The collector reads a bounded number of whole records per source, within the same
journal transaction. Source hashes include current identities and revisions;
only narrow reference/status metadata is projected. It does not copy task text,
session pending text, process paths, credentials, provider URLs or logs.

| Source | Meaning of coverage and activity |
| --- | --- |
| Explicitly bound Connector project | Current work states, selected Sprint commitments and work links; a name match does not associate another project. |
| Tasks/commands | Only explicit task links or the project's configured Task-Service key within bound host/workspace pairs. Queued, waiting, blocked, unknown and unverified done claims remain blockers. |
| Operations/deployments | Exact project/repository/bound-workspace and linked sources. Original uncertain steps remain blockers even if an old parent operation has a terminal status. A pending provider deployment is still work. |
| BAT inventory | The adopted configured repository's exact host/workspace/profile bindings, matching inventory binding digest, authenticated complete enumeration and fresh host/session observations. Partial, lost, stale, mismatched or unmapped records prevent complete coverage. |
| GitHub | Existing linked delivery receipts are observable, but do not enumerate all remote PRs/CI. This slice therefore reports `github_repository_coverage_unavailable` for the adopted repository scope. |

The final classification is `development` when an actual commitment/activity is
known, `unknown` when required facts cannot be established, and `idle_candidate`
only when all required coverage is proved and no commitment remains. In this
slice there is no producer of complete GitHub repository observations, so public
capture cannot establish `idle_candidate`. This is an explicit capability gap,
not a caller override or a promise that the absent remote work was checked.
Future W47 provider observations must prove their exact repository, authenticated
read, complete bounded pagination and fixed PR/CI identities before satisfying
that coverage; a caller-supplied flag is not such evidence.

An active Sprint or a planned Sprint with selected work remains a commitment,
including after its clock time expires. Closing/cancelling is a separate explicit
management action. An unselected backlog and Idea discussions do not count as
active development. An ordinary managed repair is not silently relabelled as
Maintainer work; future repair exclusions require trusted maintenance lineage.

Only server-owned PM run/session bindings justify excluding PM activity. A
verified task result may establish that its unloaded/not-streaming session or
twice-enumerated-gone session is retained historical work. The observed test
commit/tree and reviewer commit/tree must match, and unknown command effects
remain blockers. Session idle, no tab, no tokens, a checked task list, work-item
approval, archive, or an existing worktree alone do not prove completed work.
Manual resources are read only. No worktree cleanup is needed to observe them.

`outside_scope` explicitly includes manual sessions unavailable to enumeration,
other adapters, background Git activity and unlinked remote work. It never
claims that the whole machine or every tool was observed.

## Immutable record and reads

`pm_workload_snapshots` uses idempotent DDL, append-only triggers and no
`user_version` allocation. A public record contains its sequence, fixed IDs,
author, original operation and document digest. The document includes:

- policy version, captured baseline, lifecycle revision and execution epoch;
- `as_of`, `expires_at`, `next_check_at`, exact adopted `scope`;
- `source_coverage`, `blocking_reasons`, narrow evidence `exclusions`;
- classification, source digest, activity digest and stability evidence;
- `quality: unassessed`, `network_checked: false` and
  `automatic_maintenance_authorized: false`.

The private source binding stores exact original policy/baseline and original
source references. Every read, original operation and event must recheck their
current visibility. A later policy/baseline head does not substitute for those
sources. Withdrawal hides the whole snapshot instead of removing a blocker and
presenting the remainder as idle. Initially inaccessible evidence is recorded
only as an anonymous coverage gap, with no hidden source identity or prose.

Internal interfaces:

```text
install(ops)
get_snapshot(ops, principal, workspace_id, snapshot_id)
current_snapshot(ops, principal, workspace_id)
list_snapshots(ops, principal, workspace_id, after=0, limit=20)
operation_visible(db, principal, original_operation)
evaluate_for_maintenance(ops, principal, workspace_id, snapshot_id, policy, now=...)
```

Read shape is `{workspace_id, snapshot: <record>}`; current additionally includes
`visibility_filtered`. Current never falls back to an older visible observation
when the newest one was withdrawn. List shape is
`{workspace_id, snapshots, next_cursor, visibility_filtered}`. Each whole response
is at most 2 MB in actual spaced UTF-8 JSON. Lists preserve complete records,
consume hidden records without revealing them, and return a progressing cursor
or 413 if one next visible record cannot fit. Detail and capture refuse a record
that cannot fit. Cursor validation rejects booleans, negative values and integers
outside SQLite's signed 64-bit range.

All public reads use one WAL snapshot for membership, original authority, row and
page selection. The shared API installs the policy before this action and routes
operation/event visibility through `operation_visible`. HTTP readers are
`/pm/workspaces/{workspace_id}/workload-snapshots`, `/current` and `/{snapshot_id}`.
RPC/MCP expose `pm_workload_snapshots_list`, `pm_workload_snapshot_current_get` and
`pm_workload_snapshot_get` with the same whole-record visibility. Tauri allows
only these exact GET paths, list limit and signed 64-bit sequence cursor. All
capture requests use the existing operation endpoint. Availability is reported
under `features.pm_workspaces.workload`.

## Stability and final admission

A stability window requires complete idle observations of the same policy,
baseline, lifecycle epoch, adopted scope and activity evidence. Their freshness
windows must overlap; a clock reversal, missing interval, changed source scope or
unfinished work resets the proof. Repeated stale observations cannot lengthen a
valid interval. The history scan is bounded and its limit is visible.

`evaluate_for_maintenance` requires the caller's final journal admission
transaction. It reopens the original record, validates current source access,
rechecks the trusted current policy and baseline, lifecycle stop/epoch, observation
age and current bounded source digest, and requires completed stability evidence.
Disabled/manual/continue-development policies do not qualify for automatic idle
maintenance. A positive return is workload evidence, not execution authority;
the lifecycle/scheduler must still apply its own current policy and action gates.
The present missing repository-wide coverage necessarily refuses this gate.

This slice does not certify an entire workspace's Definition of Done, create a
new completion state, drop attempts, stop foreign sessions, or replay queues when
the workspace reopens.
