# Fixed affected-work preview

This read extends the fixed comparison in [pm-plan-diff](pm-plan-diff.md).
It connects selected historical work to changed requirements and acceptance
conditions, without deciding semantic impact or changing that work. Review is
optional: it introduces no adoption or admission gate. Governance review fields
and shared review UI are planned follow-ups, not available capabilities. Model
analysis is outside this contract.

## Selector and interfaces

`pm_plan_impact_preview` takes exactly `workspace_id`, `snapshot_id`,
`from_revision_id`, `to_revision_id`, and `binding_ids`. All are required.
IDs use their existing canonical forms; bindings are 1–50 distinct `pmwb_` IDs.
RPC/MCP take a list of strings. HTTP GET
`/api/v1/pm/workspaces/{workspace_id}/plan-impact` takes the other four fields
as query parameters; `binding_ids` is one comma-separated value. Empty values,
duplicate keys/IDs, extra fields, noncanonical IDs and GET bodies are refused.
MCP preserves string selectors without JSON pre-parsing and rejects extra keys.
HTTP, daemon, MCP and the restricted native bridge share this read contract.

The saved snapshot must be wholly readable, without source filtering, and its
adopted baseline must equal `from_revision_id`. Each selected binding must occur
in that snapshot. Both fixed plans and bindings must have their retained creation
events at or before the snapshot's journal cursor. The read never creates a
snapshot or selects a newer baseline. If a new snapshot is needed, the existing
explicit `pm.snapshot.create` operation remains separate.

The read runs within one SQLite read transaction, including authorization and
joins. It performs no provider/collector I/O, DDL, durable cache writes, operations,
events or markers. Nested existing readers share that read point. It does not
install a domain or call readiness to substitute current facts for history.

## Retained evidence and exact joins

The snapshot's cursor is a creation boundary, not replay of mutable work state.
Only retained events at/before that cursor discover candidate subjects. Never
backfill a missing event from wall time, a table sequence, a current operation
result, or today's Attempt projection. The snapshot itself was saved after its
cursor: its immutable digest and source-authorized reader establish its identity;
its `pm.snapshot.created` event is not incorrectly required before that cursor.

| Edge | Exact retained event and original relation |
| --- | --- |
| Plan revision | `pm.plan.drafted`, resource `pm_plan`, exact revision/operation/workspace, as checked by `pm_plan_diff`. |
| Selected WorkBinding | `pm.work.bind`, resource `pm_evidence`, exact workspace/binding/operation; snapshot fixes Work ID, title and version. |
| Package candidate | `operation.accepted` for `pm.work_package.create`, resource `operation`, exact operation target and original `expected_binding_id`. It proves an intent, not package creation. |
| Fixed package | `pm.work_package.created`, resource `pm_work_package`, exact package/operation/workspace; saved document's binding, Work, original plan and digest must agree. Acceptance and verification-method edges come from that immutable package. |
| Attempt admission | `operation.accepted` for `work.start`, exact workspace/Work and immutable `work_attempts.start_operation_id`/admission digest. Same Work alone does not assign a package. |
| Attempt → package/execution | `work.start.succeeded`, resource `pm_work_attempt`, exact original Attempt/start/Work/package/execution IDs. Verify against original immutable child relation, package and handoff admission. Without this event before the cursor, report `association_unknown`; do not infer from current child progress. |
| Execution/handoff | `operation.accepted` for `repository.continue`, original `pm_handoff.contract` and its digest, exact package reference and Work. It proves admission of that specification, not execution success. A direct handoff can have no Attempt. |
| Issue candidate | `operation.accepted` for `pm.issue.publish` or `pm.issue.cancel`, original issue intent's Work, binding and package. It does not prove publication. |
| Published mapping revision | `pm.issue.recorded`, resource `pm_issue_mapping`, exact mapping/revision/operation/workspace/Work; saved revision's package, binding and numeric repository/native issue identity. The saved receipt, not a new GitHub read, supplies publication evidence. |

Candidate discovery is bounded and restricted to selected Work IDs and workspace.
An original operation envelope is used only to interpret its retained event;
mutable status/result/updated time never contributes to the matrix. Event payloads
must match canonical original records. Ambiguous or inconsistent evidence fails
closed. Same-work packages on another binding are excluded, not silently rebound.
An Attempt without a provable selected-package assignment is explicitly unknown.

Proof checks include resource type/ID and original actor, and require every
checked body field to be present with the exact JSON value (including null and
boolean types). `operation.accepted` checks action, target and entry against the
original envelope. `pm.work_package.created` checks workspace, Work, operation,
package ID, document digest, predecessor, `execution_authorized:false` and
`execution_spec:not_assigned`. `pm.issue.recorded` checks workspace, Work,
operation, mapping/revision IDs, native issue identity, publication receipt,
state, external issue state and execution authorization against the exact saved
revision. `work.start.succeeded` checks workspace, Work, start/Attempt IDs,
package/execution IDs and package reuse against the original admission and child
relation. Its session payload does not prove an impact edge and is neither
copied nor used to infer execution progress or completion.

### Indexed discovery and construction cost

The existing issue/package startup schema installs two idempotent indexes; the
read never installs them. There is no table, column, trigger, data step,
`user_version` change or backfill. In the expressions below `W` is
`CASE WHEN kind='operation.accepted' THEN json_extract(body,'$.target.workspace_id')
ELSE json_extract(body,'$.workspace_id') END`; `R` is
`CASE WHEN kind='operation.accepted' THEN COALESCE(json_extract(body,'$.target.work_item_id'),
json_extract(body,'$.target.mapping_id')) ELSE json_extract(body,'$.work_item_id') END`.

- `CREATE INDEX IF NOT EXISTS pm_impact_event_relation ON api_events((W),(R),seq)`
  has the partial predicate `kind IN ('pm.work_package.created','work.start.succeeded',
  'pm.issue.recorded') OR (kind='operation.accepted' AND resource_type='operation'
  AND json_extract(body,'$.action') IN ('pm.work_package.create','work.start',
  'pm.issue.publish','pm.issue.cancel'))`. Unrelated kinds/actions create no index
  entries; inserts still evaluate the partial predicate and therefore incur work.
  Both Work and cancellation-mapping event arms use equality on W/R and
  an index range `seq <= cursor`; neither scans the global event prefix.
- `CREATE INDEX IF NOT EXISTS pm_impact_issue_work ON
  pm_issue_revisions(workspace_id,work_item_id,mapping_id)` supplies distinct
  immutable mapping identities for the cancellation arm. Locators are not proof:
  a retained, validated admission/domain event still establishes every boundary.

Package identities use existing `pm_package_work`; their original handoff
envelopes use existing `pm_handoff_package`. Each handoff admission uses
`api_events_resource(resource_type,resource_id,seq)` with `seq <= cursor`.
Direct handoff admissions have no PM IDs, so discovering their envelope IDs
cannot use a cursor range. Its cost is the selected Work's immutable package
locator rows plus the original handoff envelopes for those packages (including
later ones), with one indexed cursor lookup per envelope. Mapping locators cost
the selected Work's indexed immutable mapping history; no unrelated workspace,
Work or operation scan is permitted. Locators never authorize or establish a
creation boundary, and never acquire a cap that could invalidate a fixed older
selector after later inserts. Only eligible retained event rows at/before the
cursor consume the candidate budget; excluded bindings and post-cursor rows do
not. Private Attempt eligibility reads at most two matching retained finishes:
one can assign a binding, two establish ambiguity without repeatedly loading all
duplicate proofs. There is no ordering by `created_at` or substitution of current heads.

The plan startup schema also installs `CREATE INDEX IF NOT EXISTS
pm_plan_adoption_revision ON pm_plan_adoptions(workspace_id,revision_id,created_at)`.
It serves the original-source operation, prior scope revision and displayed
adoption lookups in `pm_plans`; the preview never installs it. Lookups select the
maximum timestamp using this index, then its first rowid, preserving the old
scan's result for tied historical rows without a temporary sort. Ordinary adoption
rejects adopting the same revision again; original-operation replay returns the
saved result. Different revisions may share a timestamp, and the schema also
permits historical duplicate revisions, so tie preservation remains explicit.

After complete source authorization, an incremental UTF-8 wire-size budget checks
the fixed response shell and each subject before appending it. Encoding stops as
soon as the size provably exceeds 150 KiB, before hashing an oversized matrix.
The complete final wire representation is checked again. Comparison and source
readers retain their own existing bounds; this read does not change their replies.

A retained admission without its domain creation event yields a subject keyed by
the original operation, with `creation_boundary_unproven`; it never acquires a
package/issue ID from a later result. If both admission and creation evidence were
removed, no subject can be discovered: coverage always says
`retained_events_only` and `undiscoverable_history: creation_boundary_unproven`.
An empty subject list is therefore not proof that no affected work exists.
Loss of required plan/binding creation evidence is an explicit comparison error.

Current readers may check additional original-source history for authorization;
those later facts never become comparison content. For the same selector and
retained evidence, later Work titles/bindings, issue heads and Attempt progress
cannot change the canonical matrix while its sources remain readable.

## Matrix, unknowns and limits

Response: `{version:1, workspace_id, matrix, impact_digest}`. The digest is SHA-256
of canonical UTF-8 JSON `matrix` (sorted object keys, compact separators, no NaN).
The matrix contains the normalized selector, snapshot digest/cursor, the original
fixed plan comparison, selected snapshot Work rows, stable typed subjects and
coverage. Subjects retain only original identities, exact package requirements,
acceptance/verification mappings and references; no whole package source bytes,
issue body, session transcript or current execution state is copied.

The comparison's directly changed requirements, dependency candidates, acceptance
source differences, unclassified prose and original source statuses remain
unchanged. Subject joins do not declare semantic invalidation, acceptance,
completion, cost or delivery verification. Missing acceptance stays unknown;
DoD is separate. `association_unknown` differs from
`creation_boundary_unproven`; publication without a proven receipt is
`publication_unknown`. Cross-scope issue republication is `unsupported`:
retain the existing issue relation and native ID, with no migration or new issue
workaround. Task reordering never changes the issue identity.

Limits: 1–50 selected bindings, 100 total joined subjects, 1,000 retained candidate
event rows inspected, and 150 KiB for the complete response including digest.
An admission and its later domain event each consume one candidate-row slot;
subjects deduplicate by original operation/type. Excluded bindings invoke no
reader and contribute no subject, error or candidate count. Original immutable
envelopes/relations establish eligibility privately; an Attempt without a
retained, assignable finish remains selected as `association_unknown`.

Request validation (422), snapshot/plan source denial, and baseline/binding
selection mismatch (409) may precede selected-source preflight. Preflight includes
the saved snapshot's selected Work rows and their source references, its copied
coverage, both fixed plan documents, and the exact selected binding records. The
existing shared `pm_source_lineage.require_document` barrier checks these inputs
before comparison; subject authorization also checks every copied reference from
original operation targets, fixed packages and exact issue revisions. Mutable
Attempt projections and unselected Work lineage are not substituted for these
fixed inputs. Existing snapshot, plan and subject readers keep their checks. The
complete result retains its final shared lineage guard as defense in depth; no
response reference relies on that final guard alone. Every other
derived refusal, including comparison errors, ambiguous/contradictory proof and
subject/byte bounds, follows authorization of the entire selected source closure.
Exhausting discovery before that closure is established returns the same generic
404 as source denial, regardless of discovery order or readability so far:
"The selected impact sources are unavailable or cannot be established within the
read's bounds; select fewer bindings". Only overflows established after closure
authorization return `PM_IMPACT_TOO_LARGE` (413). No truncated or paginated
matrix is returned. A person may explicitly select a smaller subset.

Coverage reports `work: selected_bindings_only`, `project_complete:false`,
`unselected_work:not_scanned`, `history:retained_events_only`, the original
snapshot coverage, `undiscoverable_history:creation_boundary_unproven`,
`semantic_impact:unassessed`, `acceptance_verification:not_performed`,
`delivery_verification:not_performed`, `network_checked:false`, and
`cross_scope_issue_publication:unsupported`. No hidden-source counts are returned.
The result always has `execution_authorized:false` and `work_update_authorized:false`.

## Authority and capability

Require `observe` and current workspace access, preserving the existing explicit
authenticated administrator policy. Authorize the entire saved snapshot, both
revision source closures, and every joined package, Attempt, execution and issue
through their existing source readers. A package includes predecessor sources;
an issue mapping includes its original package history; an Attempt includes its
original start and children. Pending intents use the existing operation reader.
Do not borrow the creator's grant or return a partially redacted matrix.

Any unreadable selected source fails the entire read with a generic
`PM_IMPACT_SOURCE_UNAVAILABLE` (404), exposing no protected title, identity,
digest, rationale or count. Missing retained evidence is an unknown boundary,
not an authorization failure. Authorization is checked again on each independent
read, including after membership/source withdrawal. Readers and contributors can
inspect; this read grants neither owner decision authority nor write scopes.

Global `features.pm_workspaces.plan_impact` and workspace `plan_impact` advertise:

```json
{"version":1,"read_only":true,"read":"pm_plan_impact_preview",
 "required_scopes":["observe"],"max_bindings":50,"max_subjects":100,
 "max_candidate_rows":1000,"max_bytes":153600}
```

This advertises only the implemented read. There is no model action, governance
write capability, adoption gate or frontend feature flag. Later governance
matrix/review fields and a shared inspector must receive their own explicit
capability when implemented, with original-key replay and whole-source closure.
Existing plan comparison and all existing domain read responses remain unchanged.
