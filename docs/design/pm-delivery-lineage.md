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

## Explicit limits

The associations describe Connector-selected targets and observed receipts.
They do not infer attribution for arbitrary outside commits from branch names or
claim that a SHA implements an accepted requirement. Human checkout/session
ownership is unchanged. Fork/native stack restrictions, configured deployment
verification and uncertain-result handling remain those of the existing engine.

Implementation and test evidence are recorded separately from this contract;
this document alone is not a completed W20/W21 acceptance claim.
