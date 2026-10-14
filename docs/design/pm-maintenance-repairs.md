# Fixed adopted maintenance repair rules (W46)

Status: implementation contract; tests and shared integration are required before
claiming completion. This composes existing repository.continue and delivery; it
is not another agent, task queue, scanner, resolver or merge engine.

Each synchronous repair frame obtains current credentials and runs all existing
policy, source, rule, lifecycle and deadline checks. The policy/baseline source
read and the complete synchronous guard share completed original-source booleans
only through `pm_source_reads.scope`; no guard result or eligibility is memoized.
Scopes end before any provider await or journal mutation, and every subsequent
page/effect obtains a fresh scope. Credential identity, source withdrawal and
the existing cycle/depth/node bounds remain unchanged.

Within that synchronous snapshot, complete repair-source decisions may be reused
by canonical original operation ID and the full principal/credential identity.
Every call still checks membership and the original target/admission context;
no caller projection can replace the canonical operation. A traversal underneath
any active repair for that same principal remains uncached, including the exact
owned-child backedge. Failed predicates propagate their original error and never
become a positive decision. Repair depth has its own existing 64-node allowance
while sharing the 2048-node read budget. This memo contains only booleans, never
repair documents, current eligibility, live credentials or final-frame results.

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

## Bounded delivery composition

New publication children for an adopted `merge` or `deploy` ceiling create a
non-draft PR. A `pull_request` ceiling still creates a draft. The actual request
and private relation are immutable: an older stored draft is never silently
rewritten or marked ready for review by a metadata update.

After publication, the original parent may create bounded private
`pm.github.observe` children and one private `github.pr.merge` or
`delivery.merge_and_deploy` child. These use the parent's existing reservation;
they do not create another maintenance check or compete with that parent for its
own `max_parallel=1` capacity. New observation attempts obey the original check
interval and absolute repair deadline. The first observation is included; each
later observation consumes one of the same original retry reservations used for
proven-unsent restarts. An unknown observation is read back at its original key,
never replaced with a fresh attempt.

Automatic required-CI selection is permitted only when the original policy
explicitly adopts `github_required_status_checks_v1`. A current, complete,
two-pass, passing observation must match the original owned PR's native identity,
full head, base, branch and original policy source binding. Existing explicit
evidence selection remains supported. The server freezes the selected observation
ID, digest and native PR ID inside the delivery child's original admission
binding. Later observations cannot replace it. Its original sources gate
historical reads even after expiry; every new delivery effect additionally
requires that exact selection still be current, fresh and passing.

The parent uses the existing complete merge preview and registered delivery
engines. The private row freezes the whole request, preview/head/base preconditions,
controller and selected CI reference atomically with child creation. Existing
in-flight delivery is a blocker, not a source of ownership. A deploy ceiling
requires exactly one configured recipe matching the original repository,
maintenance branch and adopted environment, a ready verifier, native repository
identity, fixed recipe digest and environment generation. Ambiguity blocks.

Before each unsent observation page and delivery frame, the original owner,
credential, policy, baseline, source chain, lifecycle epoch and deadline are
rechecked. Private delivery HTTP calls use the remaining original deadline,
bounded response reads and one outstanding transport worker. A timed-out worker
may still be finishing; its result is discarded and it never creates a retry.
Uncertain async merge remains read-only until the original native result can be
proven. The manual delivery path retains its existing behavior. Receipt recovery
has a separate bounded read budget after stop/deadline and cannot send a new
merge or deployment.

Source validation uses a fresh memo within one synchronous, immutable database
snapshot and one fixed repair traversal guard. Its back-edge assumptions remain
unchanged for that traversal only. Conditional answers never enter the outer
request memo; nested guards cannot reuse or populate the parent memo. The private
memo is discarded before leaving the guard, and writes, transaction changes, different
principals and inherited tasks/threads retain the shared read-cache barriers.
No memo spans an await or a new provider frame. Those frames still recheck the
complete current original source chain, owner, scopes, policy and deadline.

Only an original succeeded delivery child with its verified merge/deployment
receipts yields the parent outcome `merged` or `deployed`. These are distinct
from version publication and release-baseline advancement. Runtime capacity
remains held until original runtime termination is independently proven.

At a `pull_request` ceiling the original parent finishes with
`outcome=published_for_review` after the private publication child is terminal and
its durable original HTTP 201 proves the fixed native PR. This is a local receipt
of an already completed effect and may settle after pause, deadline or credential
revocation. It does not claim the repair fixed, merged, deployed or released.
Public reads continue to require the complete original source chain.

Publication completion and runtime capacity are separate. The original
`repository.continue` success proves session start, not runtime termination. The
parent therefore retains `capacity_held=true` and leaves `finished_at` unset until
the [original coding-turn completion contract](pm-repair-runtime-completion.md)
can be reconciled with its exact verification and delivery. It proves completion
of the original input, not death of BAT's shared runtime process. Idle inventory,
an interrupt ACK, synthetic aborted turn-end, disappearance or a stop/unload ACK
cannot replace that proof. In the audited BAT Codex adapter, interrupt updates
local running state after ACK and shared-runtime session removal does not kill
that shared process. Without an original first-send origin, complete native replay
and unchanged authenticated input identity, the execution remains unproven and
capacity stays held. Older executions cannot acquire that origin retrospectively.
The reconciler neither fabricates a terminal receipt nor sends an ineffective
interrupt solely to release capacity.

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

The parent can compose one private `github.pr.publish` child after explicit fixed
quality evidence. Only its original durable HTTP 201 native-identity receipt proves
owned PR creation. An unknown POST is never resent or adopted from matching branch,
number or prose. Publication of the owned draft PR does not prove required CI,
merge, deployment or a new release baseline; those retain their separate original
receipt and policy gates.

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

## Same-channel deployment baseline advancement

An original verified repair deployment can select a successor to its adopted
deployment baseline through one private `pm.release_baseline.register` child.
The existing baseline must already use a deployment source, and the adopted
policy must permit deployment to that exact environment. The child fixes the
original repository, merged/deployed commit, maintenance branch, prior baseline
ID/version and original source receipts. GitHub Release channels and registry
versions are not converted into deployment environments.

The original registration action retains owner, scope, lifecycle, source and CAS
checks. Its provider reads use the current credential and the original repair's
bounded deadline and response budget. A stopped or failed new selection records
`baseline_update.status=not_updated` and preserves the verified delivery fact;
it does not send another deployment or registration under a new key. A committed
registration recovers from its original receipt even if the parent has not yet
recorded completion or a later deployment supersedes the original environment
generation. That historical receipt does not claim the old version is currently
live. Baseline advancement itself does not prove runtime completion, acceptance,
or release held maintenance capacity.
