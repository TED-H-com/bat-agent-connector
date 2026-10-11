# Workspace work associations and evidence

This is the initial W05/W06 foundation for [PM v1.2](../product/pm-v1.2-plan.md).
`pm_evidence.py` records explicit associations to existing Connector work and
immutable, bounded snapshots of the central journal. It does not start sessions,
change work state, query a remote provider, assign an execution specification, or
evaluate Definition of Done.

## Explicit associations

An owner first binds a Connector project to a PM workspace, then selects existing
work items. Matching repository names, paths, titles, or sessions never creates an
association. Project binding does not import every work item. The same source work
may have explicit associations in more than one workspace; each workspace retains
its own plan association, capture history, membership, and evidence IDs.

All actions use the registered `OperationService`, require `observe` and `manage`,
and recheck current membership and preconditions inside the journal transaction.
Project/work binding requires owner or administrator decision authority. Capture
allows contributors. A reader can inspect accessible evidence but cannot capture.

| Action | Exact target | Parameters | Preconditions |
| --- | --- | --- | --- |
| `pm.project.bind` | `workspace_id`, `project_id` | `rationale` | `expected_workspace_version`, `expected_project_version` |
| `pm.work.bind` | `workspace_id`, `work_item_id` | `rationale`, optional `plan_revision_id`, optional `requirement_ids` | `expected_work_version`, `expected_binding_id` |
| `pm.evidence.capture` | `workspace_id`, `work_item_id` | Empty object | `expected_binding_id` |

`expected_binding_id` is explicitly `null` only for the first work association.
Project binding is immutable and unique per workspace/project. Revising a work
association appends a new `pmwb_…` binding, records its previous ID, and preserves
earlier bindings. A work item moved to another project must be explicitly
reassociated before capture. Archived projects/work cannot acquire new bindings;
existing historical captures remain readable.

An optional `plan_revision_id` must have been adopted in this same workspace. It
may be an older adopted revision. Requirement IDs must occur in that exact
document. Moving the workspace's accepted-plan pointer never migrates work
associations. These are owner-recorded associations made at binding time:
`execution_spec: "not_assigned"`, not a claim that past agents executed that plan.
No action grants execution authority or changes the existing work engine.

Bindings, capture, evidence insertion, the PM event, and an applied-operation
receipt are committed together. A lost reply or restart reuses the original
operation/key and returns the original IDs and receipt. Replaying a public action
still requires current workspace membership. The module allocates no schema
`user_version` or historical data step.

## Source facts and their meaning

Capture starts from the selected work's active `work_item_links`, using exact
stored source IDs. Only normalized fields from these existing central sources are
published. Prompts, command arguments/output, raw operation parameters/results,
session logs, checkout paths, deployment recipes and credentials are omitted.

| Evidence kind | Central source | Meaning and limits |
| --- | --- | --- |
| `work_claim` | `work_items` | Recorded state, completion claim, approval fingerprint and work version. Human approval and `done` remain distinct from quality verification. |
| `work_link` | `work_item_links` | An explicit active source association and its recording operation; no inference from content. |
| `operation_status` | `operations` | Saved operation status and controller identity. Success alone proves no merge, test pass, deployment or DoD. |
| `execution_state`, `execution_command` | `tasks`, `commands` | Saved Task Service state and command identity. Completion/idle is not quality acceptance. |
| `observed_verification` | `observed_verifications` | Central runner result bound to candidate commit, tree, command digest, exit code and output digest. A pass applies only to that recorded commit and command. External `session.record_verification` testimony is not this evidence. |
| `session_observation` | `sessions_observed` | Last saved activity/readiness, including unknown values and staleness. No live connection is made; unknown or quiet never means complete. |
| `resource_relation` | `api_event_resources`, `relation_revisions`, `session_worktree_bindings` | Existing explicit execution/session/worktree IDs and relation sequence. No path/name ownership inference or control transfer. |
| `pr_observation` | `pr_merge_previews` | Saved PR head/base observation with its preview identity, digest and expiration. It is not a current GitHub read or merge result. |
| `pr_merge` | Successful central `merge.verify` steps | Verified merged SHA and reviewed head/base when retained. Missing preview leaves reviewed-head coverage unknown. A merge is not deployment or DoD. |
| `integration_delivery` | `integration_receipts` | Fixed execution/task-command source and delivered SHA. `delivered` means pushed into the integration target, not PR merged or deployed. |
| `deployment` | `deployments` and saved verification evidence | Historical verification of a fixed source/artifact identity. Provider success without verification evidence stays unverified. Current environment health is not assessed by this capture. |

The initial projection does not expand checkpoint links, checkpoint/branch
integration sources, artifacts, arbitrary unlinked activity, or removed links.
It reports unsupported/unavailable sources, rather than silently claiming complete
coverage. Resource projection is partial; observed verification is limited to
linked Task Service runner records, and deployments to linked operations.
Additional source adapters can extend this contract without changing work IDs.

Every fact and capture reports `dod: "unassessed"`; fact/capture coverage reports
cost as unknown. Acceptance criteria and DoD need a future explicit quality
evaluation, including the applicable baseline. No task count or PR state substitutes
for that evaluation.

## Identity, immutability and time

An evidence ID is a deterministic UUID derived from server ID, PM workspace ID,
source kind, original source ID and the digest of normalized facts. Repeated reads
of the same fact, including association with another work item in the workspace,
reuse one evidence row. A changed source version/result gets a new evidence ID.
Changing only a poll timestamp does not invent a new fact. Capture identity derives
from its original operation ID; each new capture operation records a separate
snapshot and points to the deduplicated facts.

`source_observed_at` is the source's recorded timestamp when available.
`captured_at` on evidence is its first capture time; the capture document has its
own capture time. Neither is a claim of current remote freshness. `journal_cursor`
is this Connector server's durable API event head observed by the transaction; it
is not a BAT replay epoch, source event sequence, or synchronization guarantee.
Capture also freezes source work version and exact work/plan/requirement binding.

Rows are append-only, protected against update/delete by SQLite triggers. Latest
capture uses durable insertion `sequence`, never wall-clock order, so clock rollback
cannot resurrect an older snapshot. Later source changes or removed links do not
rewrite earlier captures. Current membership and source visibility still filter
what a caller can read from that preserved history.

Capture processes at most 50 active links, 20 rows per related-source query, 100
facts, and 16,000 UTF-8 bytes per normalized fact. Coverage flags truncation and
records unavailable/unsupported counts, scope limitations and `network_checked:
false`. These bounds are not complete-project totals. The capture's cursor and
coverage travel with its facts so views cannot substitute all historical evidence
for the latest snapshot.

## Source visibility

Workspace membership is required for every read. Referenced operations pass their
existing read policy and validated controller authority. Task/command authorities
and PM session ownership are checked separately. Malformed authority or a different
server identity makes the source unavailable. Observation never grants controller
mutation rights.

A PM-private source from another workspace is not copied into this workspace even
when the capturing user belongs to both. Corrupt or legacy work links cannot bypass
this rule. PM discussion/run operations are not delivery evidence. Integration
facts require visibility of both the integration operation and the original
execution/task command. Unavailable facts produce coverage information without
copying inaccessible source IDs, parameters or contents.

Saved evidence is checked again on list/get/capture read. If a source becomes
inaccessible, its fact and ID are omitted from the response; the immutable stored
history is retained. Pagination keeps its original sequence cursor even if a page
contains only filtered facts. Coverage's `inaccessible_sources` indicates this
filtering without disclosing the source.

## Read contract

All functions take `(ops, principal, workspace_id, …)`; lists accept integer
`after=0` and `limit=50` with a maximum of 100. They return `version: 1`.

- `list_projects`: `bindings`, `next_cursor` for explicit project bindings.
- `list_work`: current `bindings`, `next_cursor`; optional `project_id` filter.
  Bindings include fixed plan/requirement IDs, previous binding, recording actor,
  operation and time. They declare `association_basis: "explicit_owner_association"`
  and `execution_spec: "not_assigned"`.
- `list_evidence`: summaries in `evidence`, `coverage`, `next_cursor`; optional
  `work_item_id` filters the complete captured history for that work. Summaries
  contain IDs, digest, source/capture times, cursor and scalar assessment only.
- `get_evidence(…, evidence_id)`: `evidence`, including `document` with `fact_type`,
  bounded normalized `facts`, DoD and cost status.
- `latest_capture(…, work_item_id)`: `capture` or `null`.
- `get_capture(…, capture_id)`: that immutable capture or 404.

Capture responses contain `capture_id`, `sequence`, actor, original operation,
server/workspace/work IDs, work binding, fixed plan/requirement IDs, source work
version, cursor, capture time, coverage and `execution_authorized: false`.
`evidence_ids` and `evidence` summaries have identical order and visibility filtering.
Views should render these snapshot summaries, then retrieve a selected evidence
detail; they must not mix the work's whole historical evidence list into its latest
capture. This foundation supplies shared facts for later Project Brief, five PM
views and quality evaluation without creating another progress authority.
