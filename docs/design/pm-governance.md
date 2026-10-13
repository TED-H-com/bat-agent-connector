# Sourced risk, change and decision records

This is the first W17 governance domain from [PM v1.2](../product/pm-v1.2-plan.md)
§10.6, §12.4–5 and §12.14. `pm_governance.py` records RAID findings, reported
scope/schedule/quality/resource drift, change proposals, direction decisions and
links to actual plan adoption. The central journal, existing workspace membership,
immutable plans, DocumentSnapshots and OperationService remain authoritative.
No model, provider, BAT, issue creation, work dispatch or delivery call is made.

## Fixed management context

Each record has a stable `pmgov_<32 hex>` identity, a kind (`risk`, `assumption`,
`issue`, `dependency`, `drift` or `change`) and an explicit `root_cause_key`.
Creation selects one existing Project Brief snapshot containing an adopted plan.
The snapshot can contain a historically adopted baseline: the current plan head
version is an admission/CAS check, not permission to replace that baseline with
today's accepted pointer. A snapshot with only a draft is insufficient.

The record freezes snapshot ID/digest, collection time and journal cursor, accepted
plan revision/digest, scope revision and coverage. Each revision selects at least
one concrete source from this snapshot:

| `source_refs[].kind` | `id` means | Saved meaning |
| --- | --- | --- |
| `plan_input` | Original selected InputRecord ID | Exact wording, source, confirmation and information status |
| `requirement` | Requirement ID in that exact adopted revision | Original behavior and acceptance-source statements |
| `evidence` | Included immutable evidence ID | Normalized original fact, source identity/digest and observation times |
| `work_binding` | Exact included work-binding revision | Explicit work association, with no inferred execution spec |

Arbitrary operation IDs, later evidence, similarly named requirements in another
plan and the latest binding for the same work cannot be substituted. Source IDs
and facts are server derived; callers cannot supply a claimed confirmation or
verification field. Requirement impacts use IDs from the fixed accepted plan.
An old binding alone is not a drift finding. A reported drift must select an
observed evidence fact and a plan input or requirement and name its dimension.
The semantic finding remains the recording actor's interpretation, not a computed
proof of drift.

A record also contains title, description, accountable owner, affected requirement
IDs, trigger, response, review time, alternatives and rationale. Its owner must
currently be an explicit workspace owner or contributor. A missing trigger,
response or review time is `null`; the domain does not invent deadlines or risk
scores. `review_at` accepts a real ISO calendar timestamp with seconds and explicit
`Z` or numeric timezone, or `null`. It is an intended review time, not a scheduler.

`status` is `open`, `monitoring` or `reported_resolved`. Resolution and mitigation
remain attributed management reports with `verification_status: unassessed`.
An assumption retains `assessment_basis: attributed_assumption`; other records
use `attributed_management_finding`. Neither a selected repository claim nor a
successful operation becomes DoD, deployment verification or a confirmed
requirement through this record.

## Explicit root causes and duplicate handling

The pair `(kind, root_cause_key)` is unique within a workspace. Reanalysis of that
same explicit cause returns the existing record and revision with
`deduplicated: true` and `input_applied: false`. It does not rewrite wording,
create another history revision or emit a second governance finding event.
Ordinary operation history still records the actual request and result.
A changed response or additional symptom needs an explicit revision.

The domain does not infer semantic equivalence from text. It indexes each selected
original source identity. Evidence identity includes its original source kind,
source ID and fact digest, so another capture of the same fact does not create a
new event identity. A new cause using a source already referenced by the same kind
produces `PM_GOVERNANCE_DUPLICATE_CANDIDATE` with currently readable record IDs.
To record genuinely different risks supported by that same PR/fact, supply:

```json
{"disambiguation": {"record_ids": ["pmgov_<existing>"],
                    "rationale": "The same failure also reveals a distinct resource dependency."}}
```

All overlapping records must be named. The new revision preserves this explicit
relationship and reason. Existing records remain unchanged. Inaccessible overlaps
fail as unavailable sources; their identifiers or text are not disclosed. This is
an explicit grouping and distinction contract, not automated causal analysis.

## Registered actions

`install(ops)` installs additive tables and the four actions below. All require
`observe` and `manage` and current workspace write membership. Admission and the
transaction that applies the operation recheck authority and CAS.

| Action | Target | Preconditions | Parameters |
| --- | --- | --- | --- |
| `pm.governance.record` | `workspace_id` | `expected_plan_version` | `kind`, `root_cause_key`, `snapshot_id` and full finding fields |
| `pm.governance.revise` | `workspace_id`, `record_id` | `expected_version` | Full finding fields |
| `pm.governance.decide` | `workspace_id`, `record_id` | `expected_version` | `disposition: adopted_direction\|rejected`, `rationale` |
| `pm.governance.adoption.link` | `workspace_id`, `record_id` | `expected_version` | `adoption_operation_id`, `rationale` |

Full finding fields are `title`, `description`, `owner_actor`, `source_refs`,
`rationale`; optional `affected_requirement_ids`, `trigger`, `response`, `review_at`,
`status`, `dimension`, `alternatives`, `proposed_revision_id`, `disambiguation`.
A proposed revision is only valid for a change and must be an existing immutable
plan revision in the same workspace. The domain saves its exact document and
structured-scope digests. It does not draft or adopt that plan.

Only the assigned current writer or an explicit current workspace owner can revise
a record. Revision is a complete replacement of management fields, within the
original immutable snapshot/plan context. Selected sources can be added from that
same snapshot but cannot be removed. It never refreshes the snapshot or changes
kind/root cause. Every old document, actor, reason, time, version and source remains
in append-only history. Revising a change proposal clears its current direction
and adoption links; the old decisions remain in history and the new proposal needs
its own explicit decision.

An issue recorded against adopted revision N can be set to `reported_resolved`
after N+1 is adopted. No new snapshot or plan-head precondition is required for
`pm.governance.revise`: its original snapshot remains the source boundary, and
`expected_version` refers to the governance record. This also works when N+1
removes an affected requirement; keep the original affected IDs and sources when
reporting resolution. Resolution does not rebase the finding, verify acceptance
or apply scope.

The UI sequence for "Resolve issue" is:

1. Read `pm_governance_get(workspace_id, record_id)` for the current record version
   and full document. The assigned writer or current workspace owner needs observe
   and manage scopes; the original snapshot must still be readable.
2. Submit the existing `pm.governance.revise` action with that workspace/record
   target and `preconditions: {expected_version: record.version}`. Send the full
   finding fields, retaining original affected IDs and sources, with
   `status: reported_resolved` and the user's resolution rationale. Build
   `source_refs` as `{kind, id}` from the saved `document.sources`; keep optional
   management fields and disambiguation. Do not send `snapshot_id`, a new plan
   version, or server-derived decision/verification fields.
3. Settle the original durable operation and re-read the record and Overview.
   Recover a lost response using the same operation key. A version conflict needs
   a fresh read; source withdrawal rejects the effect through the existing gate.

The resolved record and its original context/history remain in the register;
Overview omits it from blocker attention/counts and current blocked requirements.
`verification_status` stays `unassessed`. Quoting fresh evidence outside the fixed
snapshot is a separate context/follow-up requirement; a new snapshot alone cannot
be substituted into this revision action.

Direction decisions and adoption links require actual current **owner membership**;
a generic administrator is not an impersonated direction owner. Decisions only
apply to change proposals. The proposed accountable owner must still be a writer.
An `adopted_direction` is deliberately not an accepted plan; it saves disposition,
actor, reason and exact proposal revision with `scope_applied: false`.
A rejection is preserved equally, and a later reconsideration adds another
immutable revision rather than deleting the rejection.

`adoption.link` requires a chosen direction with a fixed proposed plan. It reads
the original `pm.plan.adopt` operation and durable adoption row, requiring:

- The original operation actually succeeded and is visible to the caller.
- Its workspace and candidate revision match the proposal exactly.
- Its `previous_revision_id` matches the original snapshot's adopted baseline.
- Its adopted document digest matches the proposed immutable document.

Only this link records `scope_applied: true` as a historical adoption fact. It saves
the original operation, decision, revision, scope revision, digest, actor and time.
It does not adopt again, claim that this remains today's accepted version, update
work, change an old attempt or execute anything. A later accepted pointer cannot
change the original link.

Local revision, head update, source index, event and operation receipt commit in
one `ctx.effect` transaction. IDs derive from the original operation. Reusing the
original idempotency key returns that same result, including after newer revisions.
Receipt recovery can finish internal bookkeeping after source or membership loss;
public reads, replay access and new effects still require current authority.
The module allocates no `user_version` or new migration data step.

## Reads and source access

```python
get_record(ops, principal, workspace_id, record_id)
list_records(ops, principal, workspace_id, *, kind=None, after=0, limit=50)
list_history(ops, principal, workspace_id, record_id, *, after=0, limit=50)
```

Get returns `{version: 1, workspace_id, record, revision, coverage,
execution_authorized: false}`. `record` contains immutable origin/context and the
current version/revision pointer. `revision` contains its exact document, digest,
previous revision, actor, creation time and original operation ID. Lists return
`records: [{record, revision}]`; history returns `revisions` with the same fixed
context. Both return a sequence `next_cursor` and coverage. History uses durable
insertion order, not wall-clock sorting.

Every read needs observe and current workspace membership. Since management text
can quote any original snapshot fact, the entire derived record becomes unreadable
when any original snapshot source is withdrawn. Evidence is reauthorized through
its existing central operation/controller/private-workspace policy; the author's
privileges are not borrowed. Lists omit inaccessible records while consuming their
underlying sequence and reporting `visibility_filtered`/`scanned`. They do not
expose their titles, owners or counts as visible findings. Original stored history
is preserved. No read silently substitutes a newer snapshot or fact.

Shared access integration must delegate governance operations to
`operation_visible(db, principal, operation)` and `pm_governance` domain events to
`event_visible(db, principal, event)`. These read-only hooks reload the complete
original operation because shared event feeds may pass only partial rows. They
check the original admission context, any additional snapshot quoted by a duplicate
analysis, and the final result's context. They never fall back to current record
heads or create an OperationService/worker. Original-key replay uses the same gate.
This standalone domain does not modify shared API registration or feed routing;
the integrating layer installs these hooks before exposing actions.

A saved finding document is bounded to 200,000 actual UTF-8 JSON bytes. A complete
get/list/history wire response, including metadata, coverage, context and JSON
spacing, is bounded to 2,000,000 bytes. Pages keep whole records and resume before
a record that does not fit; an oversized first record fails with 413 rather than
being skipped or truncated. Limits are 1–100 records and nonnegative sequence
cursors. Source references are limited to 50 and alternatives to ten.

## Remaining lifecycle behavior

This foundation records owned, sourced findings and durable decisions. It does not
claim automatic risk discovery, likelihood/impact scoring, semantic verification,
causal merging, source refresh, a maintenance scheduler, notifications or a complete
change-set execution workflow. Fresh observations from a different snapshot need
a future explicit context/follow-up operation; current revisions intentionally keep
the original source boundary. Work/spec changes still use their existing explicitly
authorized actions. Resolving a finding does not prove an improvement effective.

## Shared service entry points

The workspace service registers these reads under the same observe permission:
`GET /pm/workspaces/{workspace_id}/governance?kind&after&limit`,
`GET /pm/workspaces/{workspace_id}/governance/{record_id}`, and
`GET /pm/workspaces/{workspace_id}/governance/{record_id}/history?after&limit`.
Their RPC/MCP counterparts are `pm_governance_list`, `pm_governance_get` and
`pm_governance_history_list`. No hidden actor override, detail history limit or
unrecognized query is accepted. The native bridge validates the same route/query
shape; backend membership and original source checks remain authoritative.
Shared `operation_get`, operation lists and event feeds apply the original
admission source gate before returning any governance narration.
