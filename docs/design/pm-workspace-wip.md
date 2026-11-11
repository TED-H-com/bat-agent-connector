# Workspace WIP policy (advisory v1)

Python Connector / Task Service owns this policy and its independent read. This
increment provides visibility only. It does not change any existing action's
admission, request, idempotency hash, events, preconditions or response. There are
no reservations, admission warnings stored in the journal, release actions, or
new scheduling hooks. UI placement in Kanban and readiness is planned for a later
increment. Enforcement is not available; `enforced` is unsupported.

## Unit and evidence

`work_commitments_v1` counts distinct Work IDs in one PM workspace: `M ∪ C`.
`M` uses the existing Kanban management flow, across all current workspace
bindings, independent of pagination, filters or card rendering. `C` contains
unresolved original executions and accepted selections, including before a child
or Work binding exists. Multiple Attempts, wrappers, links and selections of the
same Work consume one unit. Covered admissions keep their original workspace and evidence regardless of
rebinding, link removal or archiving. Only currently active explicit links
(`removed_at IS NULL`) contribute linked activity; removed links are outside scope.

The fixed first-match breakdown order is `management_wip`,
`unresolved_execution`, `accepted_selection`. Each Work belongs to exactly one
reason; the three distinct-Work counts sum to `used`. The breakdown is not a count
of sessions, Attempts, operations or batch parallel slots.

| Recorded event/state | Inclusion / release |
| --- | --- |
| Never-started `todo`, no selection | Excluded. Draft scope, package preparation and unselected backlog do not count. |
| `doing`, `waiting`, pending approval; previously started then returned to `todo` | Management WIP under the existing flow rules. |
| Content approval, archived Work/project, GitHub closed source lane | Management closure only; unresolved execution/selection still counts. |
| `work.start` accepted | Its Work counts, including legacy Attempts and pre-child acceptance. |
| Batch / adopt-and-start accepted | Every distinct selected Work counts at parent acceptance, including entries beyond current parallel/start limits. Parent and children are not extra units. |
| Direct PM `repository.continue` | Its original fixed package identifies the Work and workspace. |
| Active explicit execution/task/session link (including a task’s send commands) | Linked execution holds its associated Work. Removing the link excludes that linked activity; covered admissions remain independently counted. Uncorrelated active execution remains held. |
| Accepted, running, waiting, needs-attention, uncertain | Retain the hold. ACK, successful dispatch, idle, gone, process-ended display and elapsed time do not release it. |
| Completion evidence | Execution release requires management closure AND original correlated terminal evidence AND no later queued/sent/unresolved input. |
| Failed/cancelled before sending | Release only with terminal original operation/child evidence proving no send or uncertain external effect. Management WIP remains independent. |
| Stopped parent / invalidated execution epoch | Unadmitted selections may release when original gates prohibit further children. Existing uncertain children retain their holds. Reopening does not renew the old epoch. |
| Reopen / content edit / restore | Re-evaluate current management evidence and original execution claims; no historical authority is recreated. |

Reuse original Work Attempt/session-result identity, source-generation and send
marker evidence and follow-up receipt predicates. A receipt for another turn or
generation cannot release capacity; later queued input keeps the hold. Before
Attempt release (including unsent-original release), enumerate every operation associated with the original execution
in `pm_source_lineage`, authorize each source and charge both lineage rows and
original operations to the record budget. Source authorization also applies while
management is open or reopened, even when its WIP already occupies the same unit. Reuse the existing settled-lineage
predicate, including its conservative 256-descendant settlement limit. This scans
all action kinds, not just `work_inputs` or a `session.send` filter. Hidden sources
and record overflow use the same generic unknown response.

Session input/control kinds are `session.send`, `session.answer` (both answers and
permissions) and `session.interrupt`. Later sends, including direct host/session
sends, require terminal original send receipts and correlated terminal result
receipts for their exact markers and original generation. Accepted or uncertain
sends retain the hold; a cancelled-before-execution input can release on original
unsent evidence. Answer/interrupt operations without independently correlated
turn evidence remain held conservatively; a terminal ACK does not invent that
missing contract. Other associated actions use the existing settlement predicate.
For batch
execution reuse original runtime completion and settled-lineage predicates;
fixed verification alone is insufficient. Unsupported/older completion evidence
keeps an identified commitment held. This read neither gathers new evidence nor
broadens the result collector or creates a completion authority.

## Complete or unknown

Read one database snapshot; never probe BAT, providers, sessions or files. Count
only original central records. Bounds are 10,000 distinct Works and 50,000
contributing records, including history, selections, execution/input references
and their original receipts. Stop on overflow. Queries start with workspace or
source identity indexes; work performed must not grow with unrelated workspace
history. Schema creation and index creation happen only at installation.

Coverage `complete` means the named central commitment scope was enumerated and
inclusion was determined, not that all execution outcomes are known. An identified
uncertain execution is a known held unit. Missing enumeration/inclusion evidence
makes coverage `unknown`. Unknown has `used: null`, `remaining: null`,
`over_limit: null`, and **no breakdown or known_used**. Never substitute zero.

If any contributing source is unreadable, or any bound is exceeded, return exactly
one generic reason: `coverage_unavailable`. Do not disclose which condition,
source, identifier, partial count or per-reason count caused it. Reasons derived
solely from readable sources may be specific (`missing_history`,
`inconsistent_history`, `clock_uncertain`). A specific reason must never mask a
later hidden-source or overflow condition. Conservative retained holds do not
need a completion guess.

Always return `outside_scope: ["unassociated_external_work",
"removed_link_activity", "host_resource_capacity", "model_cost"]`. Unassociated external/manual activity
is not inferred from paths, host names or sessions. Workload remains its separate
maintenance safety signal. The Kanban page `wip` remains `returned_page` with
`limit: null`. Only when a saved policy exists does Kanban's `wip_limit` capability
point to this independent read; unconfigured responses remain unchanged.

## Policy and authority

`pm.wip_policy.save` uses the existing operation submission/replay transport.
Target is exactly `{workspace_id}`. Params are exactly `{policy, rationale}`;
rationale is 1–4000 characters. Preconditions are exactly
`{expected_policy_revision, expected_policy_digest}`. First save expects `0,null`;
later saves compare both against the current head **inside the local commit
transaction**. Stale state returns `PM_WIP_POLICY_CHANGED` (409). Same original
operation key replays the original receipt; different bytes under that key use
the existing idempotency conflict. No implicit override exists.

Policy document (exact fields):

```json
{"schema_version":1,"mode":"advisory","limit":5,
 "counting_basis":"work_commitments_v1",
 "basis":{"assumptions":"Two implementers and one reviewer this week.","review_on":null}}
```

`mode` is `advisory` or `disabled`; `enforced` returns
`PM_WIP_MODE_UNSUPPORTED` (422). Advisory limit is an integer 1–1000 (not boolean);
disabled requires null. Assumptions are nonempty plain text, 1–2000 characters;
review_on is null or exact ISO date and is advisory, never an expiry. Canonical
policy is at most 16 KiB. No installation default or automatic numeric conversion.
A limit below usage is valid and reports over-limit without cancelling/blocking.

Append-only `pm_workspace_wip_policies` stores revision, previous digest, canonical
document/digest, actor, original operation ID and time. UPDATE/DELETE triggers
protect history; UNIQUE(workspace_id, revision) and UNIQUE(operation_id) protect
CAS/replay. DDL is idempotent, allocates no user_version and needs no data step or
backfill. This is the only new table. At most four new indexes are authorized.
The implementation uses `pm_wip_operations_workspace`, `pm_wip_management_work`,
`pm_wip_commands_task` for linked task sends, and the neutral
`pm_run_state_session` for the existing visibility predicate.
Active links use the existing `work_item_links_active` partial index. Each
contributing query needs a query-plan test.

Read requires `observe` and current workspace membership. Save requires
`observe` + `manage` and workspace owner (`decide`), with shared explicit
administrator semantics unchanged. Contributor/reader cannot save. Counts never
grant source access. The policy action is a record action, so policy history and
recovery remain available while investment is paused/closed. Existing stop,
cleanup, result collection and original-key reconciliation retain their authority.

## Read and capability

`pm_workspace_wip_get(workspace_id, policy_revision?)` maps to
`GET /api/v1/pm/workspaces/{workspace_id}/wip?policy_revision=N`, daemon RPC,
strict MCP tool and exact native GET bridge. Reject unknown/duplicate query keys,
bodies and nonpositive/noninteger revisions. Historical revision selects the
policy shown, never a historical count; report selected and effective revisions
separately. Omitted revision selects the latest policy; absent policy is null.

```json
{"version":1,"scope":"workspace_commitments","workspace_id":"…",
 "counting_basis":"work_commitments_v1","policy":null,
 "selected_policy_revision":0,"effective_policy_revision":0,
 "observed_at":0,"journal_cursor":0,
 "coverage":{"status":"complete","reasons":[],
   "outside_scope":["unassociated_external_work","removed_link_activity","host_resource_capacity","model_cost"]},
 "used":0,"breakdown":{"management_wip":0,"unresolved_execution":0,"accepted_selection":0},
 "remaining":null,"over_limit":null,"admission_checked":false}
```

Policy includes its immutable record and document digest for CAS. Remaining and
over-limit compare current usage with the selected advisory policy; disabled or
absent policy leaves both null. Two admissions racing for one advisory slot may
both succeed and the next read shows their union; the policy does not cap them.
Two policy saves with the same expected head serialize: one revision commits,
the other conflicts. A restart sees either the committed immutable revision and
local effect receipt or neither; no admission reservation exists to recover.

Advertise `workspace_wip` only when its action and schema are installed:

```json
{"version":1,"read":"pm_workspace_wip_get","action":"pm.wip_policy.save",
 "modes":["advisory","disabled"],"enforcement":false,
 "counting_basis":"work_commitments_v1",
 "bounds":{"works":10000,"contributing_records":50000}}
```

HTTP, daemon, MCP and native bridge share the same authority, success and refusal
shapes. Canonical skill/adapters and pinned inventories describe advisory behavior
only. Planned UI uses the independent read in existing Kanban/readiness views for
client-side warnings; this backend does not add frontend behavior or an admission
check. Enforcement would require a separate product decision and contract.
