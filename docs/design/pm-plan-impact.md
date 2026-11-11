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
subjects deduplicate by original operation/type. Bounds are enforced before
returning anything, with `PM_IMPACT_TOO_LARGE` (413). No truncated or paginated
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
