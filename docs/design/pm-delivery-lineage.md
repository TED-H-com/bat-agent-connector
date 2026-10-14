# Fixed PM engineering delivery (W20 / W21)

This extends the existing integration, merge and deployment actions. It does not
introduce another delivery engine or turn a successful provider operation into
requirement acceptance. Integration, merge, deployment and verified runtime
health remain separate facts.

## Reviewed gaps

The W19 source graph stopped at an execution, its sessions, work and artifacts.
Integration apply refers only to a saved preview; merge refers to another saved
preview; deploy selects a fixed commit or a saved deployment. Those identities
must retain the original package before these actions can be admitted. Merely
adding action names to the derivative allowlist would lose privacy and lifecycle
authority at the next step.

Existing integration already fixes source SHAs, target PR/head and preview
digest, uses an owned Git area and records uncertain pushes. Existing merge
already checks native repository identity, provider, reviewed head/base, scope,
CI and merge policy. Existing deployment fixes recipe, environment generation,
source, workflow/run attempt and verification. These contracts remain in force.

## Identity and authority

Saved preview and deployment identities resolve through their original recorded
operations. A provider object is identified by provider origin, numeric
repository identity and exact PR number or full commit SHA. Server-selected
targets and actual delivery receipts create append-only source associations;
branch names, local paths, prose and caller-supplied lineage do not.

All original package roots remain visible to the caller. One operation cannot
mix independently managed PM workspaces. Current original specification and work
association checks remain at new effects. Existing integrate, merge and deploy
scopes authorize the same actions for contributors; this extension adds no
owner-only approval. The lifecycle epoch is captured by the existing operation
INSERT and checked before every new step or proven-unsent retry.

A target may gain PM provenance after an ordinary operation was admitted. Native
provider/repository/PR or revision identities are checked again before new
effects. Newly discovered roots are refused instead of attaching a new workspace
or lifecycle epoch to that old operation. Repository renames cannot bypass this
check. The caller reviews a new intent with all original roots included.
Conflict-resolution handoffs resolve the exact original apply preview, including
its current native target sources. Public reads follow those saved references;
new-effect admission compares against only the immutable roots captured at the
original operation INSERT. A later read relationship cannot grant old authority.

Recorded targets and sent effects establish provenance and privacy, not evidence
that a push, merge or deploy succeeded. Only the existing confirmed receipts
report those outcomes. A verified merged revision may seed a later explicit
deployment, retaining the same original package roots. No automatic deployment
or new dispatch key is generated.

## Recovery and public reads

New owned Git mutations, merge submissions and workflow dispatches check the
original sources at their final send boundary. An original sent effect retains
its recorded identity and is reconciled using the existing read-back path even
if sources are later withdrawn. Reconciliation may preserve internal receipts;
it cannot authorize a new merge or deployment after a lifecycle stop.

Integration previews/results, PR previews/cards, deployment history/status,
operations, events and PM result projections share the original-source gate.
Whole inaccessible collection records are omitted without resetting upstream
cursors; inaccessible singular documents are refused. Source revocation during
an awaited read is checked again before returning. Historical receipts are not
rewritten or copied into a new operation to recover them.

The PM work projection is `pm_delivery_lineage.for_work`: it returns the exact
original package IDs/digests and saved delivery operation identities. Integration
push/receipt facts, observed merge and verified merge facts, and deployment
source/environment/workflow/run/verification facts remain separate. It does not
change WorkItem completion, Sprint acceptance or requirement verification.
Reads make no BAT/provider requests and create no operations. Pagination uses a
nonnegative signed 64-bit scan cursor, at most 100 whole operation records and a
192 KB response budget. Source filtering retains scan progress. A single record
over the budget is refused whole.

An uncertain async merge is not declared failed merely because the PR is still
open when source access is revoked. The existing request may complete later;
read-back keeps the original request pending and never uses lost access to
justify another PUT. After a recorded send, registered merge/deployment reads
may observe that original result and save a local receipt. The local
`receipt_only` step itself never calls a provider, and it cannot admit the next
deployment after a lifecycle stop.

## Explicit limits

The associations describe Connector-selected targets and observed receipts.
They do not infer attribution for arbitrary outside commits from branch names or
claim that a SHA implements an accepted requirement. Human checkout/session
ownership is unchanged. Fork/native stack restrictions, configured deployment
verification and uncertain-result handling remain those of the existing engine.

Implementation and test evidence are recorded separately from this contract;
this document alone is not a completed W20/W21 acceptance claim.

The shared read route is `GET /pm/workspaces/{workspace_id}/work/{work_item_id}/deliveries`,
with `after` and `limit` only. RPC/MCP use `pm_work_deliveries_list`. Observe and
current workspace membership are required. The native bridge allows only that
fixed GET shape. Membership, original packages and receipt facts share one
SQLite read snapshot; the byte limit counts the ordinary JSON wire encoding.

The Session inspector validates this same contract and groups receipts by provider
origin, numeric repository ID and PR number. It preserves the last validated rows
while loading; transient failures mark them Not updated, or Unknown without a prior
read. Forbidden, gone or removed sources clear their cached receipts and counts.
Partial pages name only the grouped PR rows shown and retain a more marker.
Unchanged refreshes retain their DOM and inspector scroll position. PR links use
the recorded provider origin; a verified current deployment remains distinct from
a merge or requirement acceptance.
