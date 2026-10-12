# Shared project context and Project Brief snapshots

This is the initial W12/W39 foundation from [PM v1.2](../product/pm-v1.2-plan.md)
sections 7, 10 and 12. `pm_snapshots.py` freezes one workspace context for Project
Brief and future views, reports and SVG renderers. Existing
[plan revisions](pm-plans.md) and [work evidence](pm-evidence.md) remain the sources
of truth. It does not create another progress counter or ask a model to decide
whether work is complete.

## Create an explicit snapshot

`pm.snapshot.create` uses the registered central `OperationService`:

```json
{
  "action": "pm.snapshot.create",
  "target": {"workspace_id": "ws_<32 hex>"},
  "params": {"title": "Weekly Project Brief"},
  "preconditions": {"expected_plan_version": 4},
  "idempotency_key": "original-request-key"
}
```

`title` is optional, nonempty when present, and limited to 200 characters. No
caller-supplied facts, scope decisions or completion counts are accepted. Creating
a snapshot requires both `observe` and `manage` plus contributor/owner membership.
Admission and execution recheck the plan head version and current membership.
An empty workspace has plan version zero and can have an honest snapshot with an
unavailable accepted baseline.

The journal transaction freezes:

- Durable server/workspace IDs, workspace name/version, `as_of`, journal cursor,
  creator, original operation and template version.
- Plan head version, latest-document reference, accepted document, document/scope
  revision IDs, source statuses, eight management domains and adoption decision.
- Explicit Connector project bindings and current work-binding revisions. Names
  and metadata versions are preserved for display; they never infer membership.
- Each selected work's then-latest saved capture ID, sequence, binding, source
  work version, capture time, coverage and visible immutable normalized facts.

Capture is not refreshed by this operation. Source observations may be older than
`as_of`: the snapshot fixes when this collection was assembled, not when every
remote system was checked. Each fact retains its original observation/capture
times and exact SHA where present. The snapshot cursor is the central journal's
event head, not a BAT replay cursor or remote synchronization watermark.

`capture_matches_binding` and `capture_matches_work_version` disclose exact
version differences. A missing capture remains missing. Later work updates,
new captures, new discussion inputs, or accepted-plan changes cannot rewrite this
snapshot. A work association to an older adopted plan does not itself establish
scope drift or prove which spec an old execution used.

The accepted document is shared workspace PM data. A newer draft is recorded as
`candidate_document`, with `is_current_commitment: false`, and never becomes an
accepted goal. InputRecord status, source, confirmation and verification claims
remain unchanged even inside an adopted document. For example, a `repo_evidence`
input is still an attributed `unverified_claim`; adopting a plan is not a repository
test result.

## Deterministic Brief projection

The returned `brief` always has five keys. Each has `status`, `items`, and explicit
`coverage`. Every item has a factual `kind`, readable `text`, source status and a
`source` reference; optional `details` retain the same normalized facts.

| Key | Source and behavior | Explicit limit |
| --- | --- | --- |
| `goals` | Fixed accepted governance/scope InputRecords; original wording, status and source references | An assumption stays an assumption; no accepted plan means unavailable goals |
| `delivery` | Selected runner-verification, merge, integration, deployment and original quality-assessment facts, plus clearly labeled work claims | `quality_unassessed` without selected assessments; otherwise fixed assessed counts, with current applicability and environment unassessed |
| `blockers` | Saved waiting/blocked/failed/needs-attention/uncertain source states and explicitly missing work captures | Partial states, not a complete diagnosis, dependency graph or inferred cause |
| `deviations` | `unassessed`, with no findings generated | No baseline/result assessment has occurred; older work bindings are not drift findings |
| `decisions` | Fixed adoption rationale and unresolved/conflicting inputs from that accepted document | No prioritization, new agent recommendation, or later thread input is silently included |

Source references use `plan_input` with revision/input IDs and the original
reference; `plan_adoption` with revision/decision IDs; `evidence` with its stable
evidence ID; or `work_binding` with work/binding IDs. Readable evidence text is a
deterministic description of a stored result. Optional details contain those
same facts, not a second interpretation.

Operation success is not a delivery result. A command test pass applies to its
fixed candidate commit; a merge is not deployment; a historical verified deployment
does not establish the current environment's health. Work state and approval remain
claims rather than DoD evaluations. Missing costs remain unknown. The projection
does not invent milestone, Sprint, risk, resource or forecast conclusions from
missing fields; the accepted document's existing domain statements stay available.

Explicitly selected [quality assessments](pm-quality-review.md) produce
`delivery.status: fixed_quality_assessments`. Its quality summary counts distinct
requirements in the original accepted scope and qualified work deliverables,
retaining each work contribution's acceptance and DoD result. A contribution must
match the exact work, binding and scope and have been applicable at selection.
All selected contributions must accept both requirement and DoD before that
requirement qualifies. Truncated work/evidence/byte coverage prevents requirement
qualification. `completion_ratio` remains null; these fixed counts do not establish
current applicability, environmental health or inferred scope deviations.

## Bounds and immutable recovery

The default context includes at most 100 project bindings, 100 work bindings and
500 distinct evidence facts. Shared evidence is included once; multiple work
associations point to that one ID. Per-source capture limits still apply and remain
visible in each capture's coverage. No unlinked global activity is scanned or counted
as if it were this workspace's complete workload.

The full response, including normalized facts and derived Brief, is bounded by
2,000,000 actual UTF-8 bytes. Admission to the snapshot context retains whole facts
and records `projects_truncated`, `work_truncated`, `evidence_truncated` or
`bytes_truncated`; it never slices source text into a misleading partial fact.
Existing selected work/capture references remain when their fact set must be
truncated. If the baseline alone cannot fit, creation fails before writing a snapshot.
Bounds are context coverage, not a complete-project denominator.

`pmsnap_…` derives from the original operation ID. Append-only snapshot and applied
receipt rows commit together with their event. A retry or recovered lost reply
returns the same ID and digest without recapturing newer data. Additive idempotent
DDL allocates no `user_version`. Latest snapshot uses insertion `sequence`, so
clock rollback cannot select an older snapshot as current.

`template_version: 1` identifies this deterministic projection. `snapshot_digest`
hashes the canonical frozen source document. Template evolution must preserve
the ability to render that version; it cannot silently reinterpret old snapshots.

## Current visibility of historical facts

Every list/get requires `observe` and current workspace membership. The immutable
accepted document follows that workspace access policy. Each external evidence
fact passes `pm_evidence.get_evidence` again for the reading principal; a snapshot
creator's privileges are never reused to authorize another reader.

If a fact becomes inaccessible, the response removes it and its per-work ID,
then recomputes all Brief items from the remaining fixed facts. It cannot survive
as a stale blocker, delivery result, count or source link. Coverage and visibility
mark filtering; stored history is retained. Source lookup only authorizes the
original ID/digest and never substitutes the newest source state.

`view_digest` hashes the returned context and projection under current visibility;
it can change when access changes. `snapshot_digest` continues to identify the
original immutable source document. This distinction prevents a filtered response
from claiming to be the unchanged complete original export. Lists contain metadata
only, without source-derived completion metrics or private fact summaries.

## Read functions

All functions take `(ops, principal, workspace_id, …)` and return `version: 1`:

- `list_snapshots(…, after=0, limit=50)` returns `snapshots` metadata and
  `next_cursor`. The integer page limit is 1–100; cursor is a nonnegative sequence.
- `get_snapshot(…, snapshot_id)` returns the fixed snapshot, filtered for current
  access, or 404. Another workspace's ID is not readable through this workspace.
- `latest_snapshot(…)` returns the latest inserted snapshot or `snapshot: null`.

Get/latest return `workspace_id` and a `snapshot` containing identity, sequence,
time/cursor, template/digests, baseline, candidate document, `projects`, `work`,
`evidence`, `coverage`, `brief`, and `visibility`. No read performs a new capture or
provider call. Dashboard, downstream views and [Brief exports](pm-brief-exports.md)
select one snapshot ID and use these same facts and revisions.

Saved context, factual Brief projection and Markdown/HTML/SVG Brief exports are
implemented. Explicit quality assessments also feed [Sprints](pm-sprints.md) and
[Roadmap observations](pm-roadmap.md) through their own fixed-source contracts.
Full semantic deviation interpretation, remaining view renderers and presentation
renderers remain separate work. Generating a snapshot neither completes a Review nor shares/publishes a
document or authorizes development, merge or deployment.
