# Fixed adopted maintenance repair rules (W46)

Status: implementation contract; tests and shared integration are required before
claiming completion. This composes existing repository.continue and delivery; it
is not another agent, task queue, scanner, resolver or merge engine.

## Explicit executable policy

An optional `repair_rules` list in MaintenancePolicy preserves old canonical
policy bytes when omitted. Each owner-adopted rule contains a stable key, allowed
work kind, exact Issue labels, fixed release predicate, narrower repository paths,
an existing full repository.continue handoff envelope selecting an immutable
adopted engineering package, and a fixed verification condition. The package's
already associated central WorkItem and accepted requirements are authoritative.
No model-selected risk label or caller-provided execution envelope grants access.

The first supported predicate fixes baseline ID/digest, release_version and
commit_sha. The package code source SHA must equal that verified published
commit; its source_ref and delivery base_ref must equal the adopted maintenance
branch. A branch already ahead is blocked by the existing exact published-head
check. This narrow case does not assert compatibility with unpublished changes.
Rules do not follow a changed release implicitly. The complete automatic creation
of new WorkItems from owner-adopted templates remains unsupported.

Only configured Issue findings with the exact required labels are executable in
this first slice. The rule must explicitly narrow the configured Issue source.
Dependabot alert release applicability remains unknown; existing Dependabot PRs
are evidence of existing work, not permission to create a duplicate repair.
A current complete exact observation/digest/finding occurrence is required.
Disappearance from the next sample does not mean resolved.

An existing finding repair, WorkItem attempt, external PR or unknown launch blocks
new dispatch and links the original record. The same stable finding/work cannot
be restarted by another operation key, changed observation or policy revision.
Fixed package readiness, current adopted plan/scope, source access and the complete
original ancestor chain remain mandatory. Long Idea work does not dispatch.

## Parent, capacity and execution

`pm.maintenance.repair` targets workspace_id, selects policy_id, rule_key and a
fixed finding_ref {observation_id, document_digest, finding_id}, and uses the same
policy-version/baseline/lifecycle CAS fields as maintenance checks. Explicit dispatch
uses the existing start scope and additionally requires observe/manage; it cannot
borrow another credential's scope merely by sharing the actor name. The original
policy must have delegated observe/manage/start, and a single current nonrevoked,
nonexpired credential of the original real owner must still grant them. Scopes
are not combined, actor names do not become admin authority, and rotation retains
the same controller. Delivery additionally requires its existing current scopes.

A journal transaction reserves shared check/Review/repair concurrent and period
capacity, immutable finding/work dedup and an absolute deadline before creating
one existing repository.continue child. Child key, full request digest, actor,
controller and private original parent relation are persisted atomically. An
unknown outcome retains its reservation and original key. New attempts never
repair uncertainty by resending a start or clearing locks.

Before every original unsent repository/BAT frame, verify current policy,
original owner/scopes, baseline/version, package, lifecycle epoch, path rule and
deadline. Already-sent receipts can still reconcile after stop; no new frame is
admitted through receipt recovery. Existing/manual sessions and worktrees are
never injected into, resumed, interrupted or deleted. Shared source lineage
carries the repair parent alongside the existing package assignment into sessions,
worktrees, artifacts, verification, integration, PR and deployment receipts.

A fresh complete stable idle workload is required for new proactive admission.
After a repair starts, it is existing maintenance work, not a reason to rebuild a
Maintainer or force Development. New observed human/development work prevents new
proactive repair reservations; it does not kill running work. Current uncertainty
and capacity remain visible. The module never changes lifecycle stage itself.

A non-null hard cost_microusd ceiling is unsupported for automatic repair while
model/provider billing is unmeasured. Null explicitly selects unmeasured cost;
start count, policy period/concurrency and new-effect deadline remain enforced.
A deadline does not claim that an already-sent model/runtime has stopped. Unknown
or ongoing effects remain held for reconciliation. A terminal child with no original
session.start step (or a stopped parent with no child at all) proves that no runtime
was started; only that case releases capacity without delivery evidence. Per-period
counts and stable finding/work dedup are never refunded. Retry ceilings do not authorize
a new key or replay an uncertain effect. A proven-unsent restart consumes the
original repair's retry count in the same journal transaction as the step transition;
the exact request, original step row and latest original step-event sequence form
the idempotent reservation. Bookkeeping, existing registered lifecycle record actions, stopping an owned
session and sent-effect readback consume no retry. The shared source-visibility
gate still precedes local records; the exception grants no source access.

## Actual receipt progress and delivery limits

Successful dispatch means only a new owned execution was started. The parent
waits for fixed verification and delivery evidence; it never claims a bug fixed,
PR created, merged, deployed or released from operation/session success.

`pm.maintenance.repair.evidence.record` selects exact original evidence under the
repair's current record version. Existing observed verifier receipts and W20
integration/merge/deployment lineage are reused. Reported GitHub checks are not
complete semantic acceptance, required CI or required review by themselves. Those
remain explicit gates under the adopted package/policy and existing delivery
engines. Records distinguish observed verification, accepted criteria, PR linked,
verified merge, verified deployment and release baseline advancement.

New delivery effects must remain under the adopted maximum action/environment,
with a complete fixed-head diff (including rename source paths) inside both the
rule and policy path sets. Missing or truncated path coverage blocks publication.
These are publication checks in an owned checkout, not an operating-system sandbox
limiting an agent to selected subdirectories. A prompt is not policy enforcement.
Only native provider/repository identity and original central source receipts
can associate an existing owned target PR; matching number/path/prose is not
proven ownership or delivery. Foreign/existing repair PRs block duplicate starts.

There is currently no registered PR-create operation. Existing integration applies
to an existing target PR. This slice can dispatch and verify; delivery waits for
an exact proven existing central receipt and cannot invent PR or release success.
A later bounded PR-create extension must reuse the existing provider journal and
read-back contracts. Release adoption remains the existing verified-baseline
operation after an actual publication receipt; merge is not publication.

## Shared integration interfaces

New module installs actions and schema, offers capabilities/get/list/current run,
source_visible/operation_visible, bounded tick, require_child(ops, operation), and
require_new_effect(ops, operation), and
admit_restart(ops, operation_id, step_name, request) inside the existing
OperationService._step_restart transaction after the batch hook. Private table relations are authoritative;
public requests cannot supply them. Shared lineage adds a self root only for the
trusted pm_maintenance_repair admission tag and dispatches its original source
visibility to this module. Reservation attaches that root to the selected work
before child admission; existing pm_handoff then preserves the package assignment.

The existing maintenance capacity helper includes this module's reservations;
the daemon's existing tick invokes bounded eligible rule evaluation. Shared final
frame/new-effect gates invoke the guard after original receipt lookup. No extra
timer, native scheduler, direct SQL action bypass or expanded external MCP scope
is introduced. Historical operations/events/derived read models follow the same
full source chain. Whole records and page cursors are bounded, newest hidden
records never fall back to older visible results.

## Accountable quality and original PR publication

A repair owner records `kind=quality` with exact immutable
`{review_id, operation_id, version, document_digest}`. Historical reads preserve
that original assessment. New publication requires that same assessment still be
current and qualified for every original acceptance and DoD criterion, the exact
package, child, binding, scope, native repository and candidate. The rule's fixed
observed-runner condition must independently prove the same candidate tree.
A work_item.approve fact remains a work approval, never code review.

The parent composes one `github.pr.publish` operation after explicit quality
selection and current original policy/owner/integrate authority. Its whole request,
key, controller, original child and quality reference are immutable in one journal
transaction with admission. The private relation is required before preparation
and every later unsent frame. Public title/body are service-generated Issue-number
text only (`Maintenance repair for #N`, `Fixes #N`); draft is true. Package text,
private prompts and PM prose are never copied into the public payload.

The publication engine supplies server-measured candidate/tree/base and the entire
bounded diff. Its no-renames traversal contains both old and new rename paths.
Both rule and policy paths, original base release, quality and delivery ceiling
are checked before branch publication and before original POST. Only the original
native 201 receipt can establish ownership; uncertainty retains the original child
and reservation. A matching PR number, branch, marker or later lineage association
cannot replace that receipt. Successful publication is a PR creation fact, not
merge, deployment, release or automatic requirement acceptance. Updates of the
fixed repair candidate are currently unsupported.
