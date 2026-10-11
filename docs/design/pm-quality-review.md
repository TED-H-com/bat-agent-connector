# Fixed acceptance and Definition of Done review

This contract closes the quality projection gap in W05, W14, W15 and W35. It
builds on fixed engineering packages, original delivery receipts, registered
verification adapters and explicit review responsibility. It starts no session,
command, provider request, test runner, merge or deployment. Implementation and
verification are still separate from requirement acceptance and shared DoD.

## Existing contracts and the gap

| Existing authority | Reuse | Present limit |
| --- | --- | --- |
| `pm_work_packages` | Immutable package, original adopted plan/scope, WorkBinding, acceptance InputRecord IDs, proposed verification mappings, optional DoD | Methods are descriptions; readiness leaves quality unassessed |
| `pm_sprints` | Stable DoD criterion IDs, immutable revision and original Sprint selection | Closing currently records every selected item as unassessed and not Done |
| `pm_evidence` | Immutable evidence IDs, observed verifier facts, original-source gates, capture/replay deduplication | Successful facts do not evaluate a requirement or DoD |
| `pm_work_batch_evidence` | Exact original child → verifier or integration → GitHub head receipt checks | Fixed revision verification explicitly is not semantic acceptance |
| `pm_delivery_lineage` | Source-gated integration/merge/deployment facts with immutable handoff provenance | Delivery stages remain independent of quality |
| `pm_kanban`, `pm_snapshots` | Shared current and frozen projections | Management approval and merged PRs cannot become quality-approved delivery |

Relevant acceptance scenarios are A07/A08 (original-key recovery and CAS), A10
(shared views), A11/A12 (merged or timebox-ended is not Done), A15 (qualified
release need not await a meeting), A19/A20 (fixed result identity and original
receipts), A24/A29 (no double counting or scope rewrite), A35/A38/A39 (adopted
requirements and immutable spec; archive proves no acceptance), and A45/A48
(same snapshot and accountable, deduplicated contributions).

## Immutable review subject and responsibility

A review fixes one original handoff operation and its exact package reference.
The operation must be an existing successful `repository.continue` with trusted
`pm_handoff` binding whose package identity/digest matches. A matching repository,
branch, title, WorkItem checkbox or path is insufficient. The selected full
candidate commit is a proposed result identity until original verifier receipts
prove its relationship to that handoff.

The stored context includes the entire original criterion text/provenance, plan
revision/document digest/scope/adoption, WorkBinding and package digest, original
execution operation/controller, provider/repository identity, candidate SHA,
DoD revision/document digest, responsible reviewer actor, and verification policy
for every criterion. Only a current workspace owner may establish or supersede
this responsibility/policy. The reviewer must be a current owner or contributor;
self-review is explicit and visible, never silently represented as independence.
Changing reviewers requires a new immutable review under owner authority.

Requirement acceptance keys use `(requirement_id, acceptance_input_id)` from the
original package. DoD keys use the original DoD criterion IDs. Criterion text is
always loaded from these originals. No caller can omit a required criterion,
replace its text, invent a confirmed source, or pass an unrecognized criterion.
Missing/unknown original acceptance remains a visible gap and cannot qualify.

When the package fixes a DoD, the review must use that revision. When it does
not, an owner can explicitly select one historically adopted DoD in the same
original scope; the record labels it as a review selection. Omitting DoD remains
`not_defined` and prevents a fully qualified Increment. A later or weaker DoD
cannot replace an existing Sprint's original DoD when computing that Sprint.

## Registered actions

All actions use existing `observe` and `manage` scopes, current membership and
OperationService idempotency. They are local management records, permitted to
preserve results after pause/close; they authorize no new engineering effects.
Each final journal transaction repeats source, responsibility and CAS checks.
Successful original receipt replay precedes fresh revision checks.

`pm.quality.review.create` has target `{workspace_id,work_item_id}` and params:

- `package_ref`: exact `{workspace_id,package_id,document_digest}`.
- `execution_operation_id` and full `candidate_commit`.
- `dod_revision_id`: exact original revision or explicit null.
- `reviewer_actor`: the responsible current workspace writer.
- `criteria`: one `{criterion,condition}` for every original criterion. Criterion
  is `{kind:"acceptance",requirement_id,input_id}` or
  `{kind:"dod",criterion_id}`. Condition is a registered fixed-verification
  profile, including its required fixed command digest where applicable.
- `rationale`: why this responsibility and evidence policy are appropriate.

Preconditions contain `expected_previous_review_id` (explicit null for the
first review of the same package/result/DoD subject). The generated identity is
`pmqr_<original operation hex>`. A new policy creates a new review, links the
original predecessor and replaces only that exact subject's current pointer.
It never changes old assessment records or silently falls back to old acceptance.

`pm.quality.review.record` has target `{workspace_id,review_id}`, precondition
`expected_version`, and params `{conclusions,rationale}`. Only the original
responsible reviewer may record the assessment, while still a workspace writer.
Every conclusion names one original criterion, a verdict (`accepted`, `rejected`,
`unassessed`), a concrete rationale, and an exact registered proof reference or
null. A record describes the whole original matrix; omissions are refused.
The server freezes the reviewer statement alongside independently evaluated
proof facts and coverage. Caller-supplied `passed`, quality totals, actor or
proof statuses are not trusted fields.

`pm.quality.review.revoke` uses the same target/version precondition and a reason.
The responsible reviewer or a current workspace owner may append revocation.
Revocation prevents future use, preserves every previous conclusion and original
receipt, and never kills sessions or rewrites a historical snapshot. Read projections
include the exact `revocation` record, with attributed original reason and receipt,
alongside the preserved assessment; before withdrawal this field is null.

All matrices, histories and responses are bounded whole UTF-8 JSON documents.
A review has at most 200 criteria and 32 immutable assessments, plus one reserved
terminal revocation record. Its predecessor chain is bounded separately at 32.
Publication reserves the terminal record and later applicability metadata within
the whole UTF-8 budget so reaching a limit cannot prevent withdrawal. Oversized whole records fail
explicitly, never trim original criteria or skip inaccessible source checks.

## Independent verification and semantic judgment

Registered adapters initially reuse `observed_runner_v1` and
`github_reported_checks_v1` from the fixed batch evidence contract. They verify
exact original child/session/controller or original integration-source and
provider/numeric repository/full SHA identity. The adapter's selected commit
must equal the review's fixed candidate. The integration provider and numeric
repository must also equal the original handoff source identity, even when
an unrelated repository contains the same Git commit. No latest branch/head is substituted.
All source operations, original verifier task and saved GitHub observation remain
subject to current source visibility.

A reviewer conclusion is a reasoned, attributed decision about what the original
criterion means and what the cited result demonstrates. The registered adapter
independently establishes whether that result is a genuine successful fixed
verification. These facts are both required for an accepted criterion:

1. The original criterion was explicitly confirmed and is covered by this exact
   immutable policy and conclusion.
2. The original responsible reviewer accepts it and provides a rationale tied
   to that criterion and cited proof.
3. Its registered fixed-result proof passes, identifies the review candidate,
   and retains its original receipt digest and current source visibility.

CI success without the review conclusion, a review checkbox without verification,
archive, merge, deployment, session success, a pasted report or model prose cannot
satisfy this conjunction. Failed proof or reviewer rejection is distinct from
missing/unproven evidence. DoD and each requirement's acceptance are evaluated
separately; all original acceptance and all original DoD must pass to qualify the
whole result. Qualification claims only the declared fixed evidence policy and
attributed review, not unobserved tests or a model-generated confidence score.

An assessment is immutable. A newer contradictory verifier record, withdrawn
source, revocation, superseding review, different candidate or incompatible scope
makes its current applicability false without mutating what was concluded then.
A subsequent assessment is a new immutable record with a new version.

## Projections and consumer hooks

New domain modules own review schema/actions, source adapters and pure bounded
projection. Root integrates install/capabilities/API/MCP/native/skills and shared
views. Suggested reads are:

- `/pm/workspaces/{wid}/quality-reviews` with bounded signed-64-bit cursor.
- `/pm/workspaces/{wid}/quality-reviews/{review_id}`.
- `/pm/workspaces/{wid}/work/{work_item_id}/quality` for current explicitly bound
  work, plus an exact-context internal projection for frozen Sprint/snapshot use.

Export `install(ops)`, `capabilities()`,
`operation_visible(db,principal,operation)`, `get_review`, `list_reviews`, and
`for_work(ops,principal,wid,work_item_id,...)`, and
`get_assessment(ops,principal,wid,review_id,*,operation_id,version,document_digest)`.
The last helper selects only an exact original assessment receipt and returns its
immutable stored conclusion, labeled `current_applicability: not_evaluated`. New
assessments, revocation and supersession cannot replace that historical value;
complete original source/privacy checks still apply. Callers first use
`evaluate_review` for current applicability when selecting a frozen close result.
Shared reads hold one SQLite read
snapshot and refuse an inaccessible original whole, including ancestors and
proof sources. No public read creates an operation or performs provider I/O.

The projection returns original review/assessment IDs and digests, separate
`acceptance` and `dod`, `qualified`, criterion gaps, historical outcome and current
applicability reasons. It also carries exact package, binding, scope, candidate
and DoD references. A current WorkItem's new binding/scope does not inherit an
old review. Historical callers select exact originals and cannot use a latest
DoD, package, code result or review as a silent replacement.

Root integration points:

- Kanban: add quality next to existing management completion. Keep the existing
  content-approval column semantics; do not relabel them as verified quality.
- Sprint close: use each original selected binding and original DoD; freeze
  qualifying assessment IDs/digests and explicit gaps in the close record. Missing
  evidence is not Done. Later assessments never rewrite the closed Sprint.
- Evidence/snapshots: collect immutable assessment facts under original source
  gates, deduplicate by their stable IDs, and freeze one common projection so
  Dashboard/Brief/SVG use the same denominator and result.
- Delivery: show qualification alongside push/merge/deploy/runtime facts. Existing
  delivery policy/authority remains the authority for sending external effects.
  A qualified result does not require a completed Sprint Review meeting.
- Dependencies: expose an exact accepted-requirement predicate for the original
  adopted scope and original requirement ID. W38's existing
  `fixed_revision_verification` remains distinct. Any semantic dependency adapter
  must select an explicit immutable assessment receipt and recheck its original
  applicability at child admission/frame; no successful start or arbitrary latest
  WorkItem is a prerequisite proof. The shared integration must carry that fixed
  reference through existing package readiness, rather than bypassing readiness.

## Required offline acceptance evidence

Use real fixed package/handoff disk Git fixtures and existing fake transports.
Positive tests must demonstrate successful original verification plus complete
responsible review producing separate accepted requirements and DoD. Negative
cases include CI-only, reviewer-only, missing/duplicate/unknown criteria, one
failed DoD criterion, wrong actor, removed reviewer before final transaction,
wrong native result identity, missing verification, changed source or plan,
revocation, predecessor/record CAS races and exact-key lost-response replay.
Current and frozen projections must preserve original scope and DoD, deduplicate
multiple attempts, and hide all original proof sources after withdrawal. No test
may write a live provider/host, invoke a model, or modify a human checkout.

### Shared frontend review workflow

`workspaceQualityReviews` owns one workspace panel and its WorkItem selector. It
reads only visible original work packages, successful fixed handoff operations,
saved DoD revisions, review history, and registered verification receipts. The
owner appoints a responsible current writer and the complete original criterion
policy; the reviewer records a rationale and proof selection for every criterion.
The central computes qualification. A passing check selection alone is never an
acceptance conclusion. Source withdrawal removes private facts and selector
labels while retaining user drafts and the durable operation key for recovery.

The module exposes `element`, `setWork(workItemId)`, `refresh()`, `withdraw()`, and
`selection()`. `onSelectionChange(workItemId, ref)` receives a value only after
explicitly choosing a currently qualified assessment. Its exact fields are
`review_id`, original assessment `operation_id`, `version`, `document_digest`, and
`candidate_commit`. Refresh clears selection, then restores it only if the same
original assessment remains qualified; a later assessment never replaces it.
Consumers must re-read this exact selection and validate their own binding,
scope, candidate, and DoD before admitting a new effect. The navigation selector
chooses the work context; it does not select a package or candidate implicitly.

The view uses the existing design tokens, compact panel/field/action components,
and English/zh-TW translations. Original criteria, hashes, and receipt details
wrap without abbreviation. Appointing, recording, and revoking use the existing
durable form and explicit original-request recovery, including stale-version
rebase rather than silently replacing a saved version.

The optional `pm.execution.verify` producer uses `requiredScope: 'operate'` in
the shared durable form, plus observe and current workspace writer authority.
`workspaceExecutionVerification` is reusable by quality and repair views. Its
`setSources` accepts original execution operation identities; configuration is
read from `/executions/{id}/verification-config`. The central supplies the clean
candidate, tree, configured command digest and fixed handoff digest. There is no
caller command or task ownership inference. The original durable operation key
survives changed candidate/configuration and uncertain replies.

Source-gated `execution-verifications/{operation_id}` receipts are selected with
`{verification_operation_id,candidate_commit,tree_hash}`. The exact verifier
operation contributes its original command digest and execution identity; the
quality adapter remains responsible for independently checking the proof and
complete criterion acceptance. An available configured command may establish a
review policy before its first receipt exists, but cannot itself satisfy it.
