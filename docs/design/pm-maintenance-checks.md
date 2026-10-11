# Bounded maintenance checks and server schedule (W48)

`pm.maintenance.check` composes the registered `pm.github.observe` operation,
then `pm.workload.capture`, through the existing `OperationService` worker. It
never starts a second runtime, model, repair, merge, task or lifecycle transition.
A complete observation remains evidence for the separate workload/owner gate;
it does not authorize Maintain by itself. Installed W49 uses this same tick and
capacity helper for [weekly report preparation](pm-maintenance-review.md).

## Admission and continuing authority

The action targets `workspace_id`, takes `params.policy_id`, and requires
`expected_policy_version`, `expected_baseline_id`, and
`expected_lifecycle_revision`. The current policy must be enabled and explicitly
adopt `github_open_prs_and_actions_v1`. The current stage must be Development or
Maintain. Idea, Planning, Paused and Closed do not automatically monitor.

Both `observe` and `manage` are required, along with an actual owner membership.
The controller must be the original adopting policy owner. The original policy
admission must have granted both scopes (or authenticated admin authority), and
one **current** nonrevoked, nonexpired API credential for that actor must
individually grant both scopes. Partial credentials are never combined. Rotation
for the same controller is supported. The actual current daemon admin credential
can provide authority only when the original policy had admin authority, its
actor matches the authenticated credential, and owner membership still exists;
an actor named `local-admin` is not authority. Credential/token identifiers are
not copied into public check documents.

Original policy sources are checked under both the requesting principal and the
current credential. Policy, baseline, lifecycle revision and execution epoch are
captured durably. Each new child and each unsent provider read verifies the same
original context, current membership/credential and deadline. A pause/reopen
cycle cannot lend a new epoch to an earlier check. A changed controller needs a
new policy adoption.

`require_child(ops, op)` first proves the private immutable parent/slot relation,
its request digest, exact persisted child envelope and actor, and the parent's
capacity reservation. A caller-supplied parent reference is insufficient.
Ordinary manual W45/W47 actions without a private parent relation retain their
own admission contract.

## Durable windows, reservations and bounded work

`tick(ops)` performs no provider IO and admits at most 32 workspace schedules per
invocation. A durable rolling cursor also bounds stopped-parent reconciliation
to 32 run rows and prevents larger installations from starving later workspaces.
The daemon owns when to invoke it; there is no module timer or extra engine.

A schedule identity combines the stable server identity, workspace and immutable
policy ID. Windows are anchored to the policy's original `created_at`, using its
`check_interval_seconds`. The first window is one full interval after adoption.
The current actual due window, policy revision, scheduled instant, admission
instant and missed-window count are saved atomically with the parent operation
and its run reservation. Only the most recent due window is admitted; a restart
never creates an unbounded historical catch-up loop. Duplicate and reversed-clock
windows cannot be readmitted. An original operation outcome is never replaced by
a retry for the same window.

Concurrent reservations count every unfinished workspace run, including older
policy revisions. Period run counts include every reservation for the policy's
fixed period, even stopped or failed checks; completion releases concurrency,
not period budget. Scheduled accepted operations reserve immediately, before
the worker runs. Manual accepted operations reserve when their handler executes;
excess accepted requests fail before any child exists. A window insertion failure
rolls back the operation and reservation together.

A run's absolute deadline begins at its durable reservation and survives daemon
restart or queue delay. `child_timeout(ops, op, timeout_s)` validates the same
parent chain and clamps each provider call to the smaller of its own timeout
and the remaining parent deadline. Deadline expiry blocks unsent calls; saved
original receipts remain available for reconciliation. Existing provider page,
row, byte and traversal limits also apply.

Each child slot has one durable key and original operation. No new child is
created to retry a failed/uncertain page. `retries_used=0` and the adopted retry
ceiling are explicit in the final receipt. The next scheduled window is a new
budgeted check, never a replay disguised as a retry. These GET-only checks make
no model requests; provider billing is unmeasured, charged cost is null, and a
policy cost ceiling does not claim provider billing is zero or measured.

## Completion, stopped work and source access

The immutable final check document records original policy/baseline/lifecycle/
epoch, deadline, ordered child operation IDs and results, and one outcome:

- `complete`: both original children succeeded and every workload source has
  complete coverage; this can still report active workload.
- `incomplete`: a child failed/cancelled or provider/workload coverage is incomplete.
- `stopped`: the original context stopped permitting another child, or a stopped
  parent was reconciled after its existing children settled.

Cancellation or pause does not resend a child. Nonterminal or uncertain external
children keep the reservation until reconciled. An interrupted local transaction
is treated as rolled back, matching lifecycle reconciliation; an unclassified
legacy effect remains uncertain. Final local receipt bookkeeping may run after
a pause or credential loss, but cannot admit new IO.

`get_run`, `current_run`, `list_runs`, `schedule_status` and
`operation_visible` follow the entire original policy/baseline/GitHub/workload
source chain. Hidden newest records are not replaced by older visible records.
Whole records are byte bounded; paginated lists advance over hidden rows without
exposing their documents. Check events, operation events, historical event reads
and all API/native transport routes must use the same operation visibility gate.

Schedule status exposes the latest window, next due instant, overdue flag,
current blocker and latest complete check time. Weekly Review execution becomes
available only when its registered action and schedule schema are both installed;
otherwise the response preserves the policy's intent with execution unavailable.
Missed windows and sampled empty evidence never claim whole-period coverage.

## Shared integration hooks

Install after W45/W47 actions and invoke `tick` from the existing daemon tick.
W47's nested `check()` calls `require_child` after cancellation checks, before
new reads, after a returned read and before publication. W45's capture effect
calls it before checking/collecting current workload. Finished receipt fast paths
precede these gates, preserving original-key recovery. W47's `read_once` calls
`child_timeout` immediately before `bounded_get`.

Domain tests use temporary disk journals and mocked providers. They cover
composition, explicit incomplete coverage, immutable receipts, dedup/coalescing,
atomic rollback, paused/Idea/closed refusal, live credential revocation/expiry/
rotation and scope intersection, owner checks, reservation and period limits,
queue/deadline expiry, forged envelopes, source withdrawal, original-key recovery,
and uncertain-child retention. The integration suite additionally exercises the
real shared hook placement and delayed-provider timeout propagation.
