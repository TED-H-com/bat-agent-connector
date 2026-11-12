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
management is open or reopened, even when its WIP already occupies the same unit. Classify authorized lineage operations explicitly; non-input read, metadata and
review actions never occupy capacity. Unknown action kinds give generic unknown.
The Attempt path has no 256-descendant cap: its enumeration uses the global record budget. This scans
all action kinds, not just `work_inputs` or a `session.send` filter. Hidden sources
and record overflow use the same generic unknown response.

Session input/control kinds are `session.send`, `session.answer` (both answers and
permissions) and `session.interrupt`. Later sends, including direct host/session
sends, require terminal original send receipts and correlated terminal result
receipts for their exact markers and original generation. Accepted or uncertain
sends retain the hold; a cancelled-before-execution input can release on original
unsent evidence. Answer/interrupt never create or extend a hold. An answer continues an input
that is already held: the collector cannot record its terminal boundary while
a question is pending. An interrupt only ends work. Neither control ACK nor
a refused answer creates a new commitment. Other associated actions do not extend an Attempt hold.
A proven-unsent original has no Attempt turn and is not served by the result
collector (which requires a succeeded handoff). A later direct input therefore
does not recreate that execution commitment. This is a known under-count of
external/direct activity; management WIP still applies. All its lineage sources
remain authorized, classified and charged before returning a count.
For batch
execution reuse original runtime completion and settled-lineage predicates,
including the inherited 256-descendant known-hold limit shared with Workload
(not a WIP enumeration bound);
fixed verification alone is insufficient. Unsupported/older completion evidence
keeps an identified commitment held. This read neither gathers new evidence nor creates a completion authority.
The existing result collector has one narrow extension for exact direct markers,
as specified in [E04 Result](v1.3-e04-result.md).

Linked task sends use the TaskCoordinator's original cancelled/rejected-before-
dispatch or exact-command `not_delivered` reconciliation evidence. Task closure
(`done`/`failed`) and management closure are both required. Read and charge the
original command history, reconciliation and any bound operation's dispatch steps.
Accepted markers, historical ACKs, running/uncertain dispatch fences and unresolved
sends retain the hold; a later cancelled status cannot erase those originals.
A terminal task alone is never delivery or completion evidence. Task history is
read through its existing task index in index order and stops at the remaining
record budget plus one, using the same generic unknown on overflow.

## Complete or unknown

Read one database snapshot; never probe BAT, providers, sessions or files. Count
only original central records. Bounds are 10,000 distinct Works and 50,000
contributing records, including history, selections, execution/input references
and their original receipts. The reused Kanban per-Work history bound is 500
for both flow events and retained management receipts; fetch at most 501 to detect
overflow, then return the generic unknown without replaying that history.

Current bindings use the existing `(workspace_id,work_item_id,sequence)` index:
seek in descending Work ID and sequence order, landing directly on the next
Work’s latest binding. Each Work needs one seek; obsolete versions are not visited
or charged. At most 10,001 Work identities are examined to detect Work overflow;
current binding records also consume the global record budget. Active links are
enumerated in `(kind,ref)` index order, with no temporary sort. Every enumerated
active link consumes the record budget, even if its kind does not hold capacity.
Stop after the remaining record budget plus one row on overflow. Queries start with workspace or
source identity indexes; work performed must not grow with unrelated workspace
history. Schema creation and index creation happen only at installation.

Coverage `complete` means the named central commitment scope was enumerated and
inclusion was determined, not that all execution outcomes are known. An identified
uncertain execution is a known held unit. Missing enumeration/inclusion evidence
makes coverage `unknown`. Unknown has `used: null`, `remaining: null`,
`over_limit: null`, and **no breakdown or known_used**. Never substitute zero.

If any contributing source is unreadable, or any bound is exceeded, return exactly
one generic reason: `coverage_unavailable`. Do not disclose which condition,
source, identifier, partial count or per-reason count caused it. A source from a
foreign workspace that is not readable within this read's source authority gives
that same generic unknown, even if the principal can read it elsewhere; it is not
silently omitted or treated as zero. Policy visibility is independent of count
coverage. Reasons derived
solely from readable sources may be specific (`missing_history`,
`inconsistent_history`, `clock_uncertain`). A specific reason must never mask a
later hidden-source or overflow condition. Conservative retained holds do not
need a completion guess.

Always return `outside_scope: ["unassociated_external_work",
"removed_link_activity", "host_resource_capacity", "model_cost"]`. Unassociated external/manual activity
is not inferred from paths, host names or sessions. Workload remains its separate
maintenance safety signal. The Kanban page `wip` remains `returned_page` with
`limit: null`. Only when a saved policy exists does Kanban's `wip_limit` capability
point to this independent read; unconfigured Kanban responses remain unchanged.
Bootstrap and availability responses change only through additive `workspace_wip`
capability and `pm.wip_policy.save` action entries; universal golden byte identity
is not claimed.

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
 "bounds":{"works":10000,"contributing_records":50000,"per_work_history":500,"per_execution_direct_inputs":500}}
```

HTTP, daemon, MCP and native bridge share the same authority, success and refusal
shapes. Canonical skill/adapters and pinned inventories describe advisory behavior
only. Planned UI uses the independent read in existing Kanban/readiness views for
client-side warnings; this backend does not add frontend behavior or an admission
check. Enforcement would require a separate product decision and contract.

## Attempt lineage action inventory

Admission sources are `pm_source_lineage.DERIVED_ACTIONS`,
`pm_delivery_lineage.ACTIONS`, all registered `pm.*` actions, and
`pm_source_lineage.HANDOFF_ACTIONS` (currently `repository.continue`) with `pm_handoff`. `LINEAGE_ACTIONS` is an explicit table;
its inventory test derives the expected set from those four admission sources.
Every source is authorized and charged regardless of its classification.

Input: `session.send`. Controls: `session.answer`, `session.interrupt`.
No-hold actions:

`artifact.accept`, `artifact.capture.managed`, `delivery.merge_and_deploy`, `deployment.rollback`, `deployment.start`.
`github.pr.merge`, `github.pr.publish`, `integration.apply`, `integration.handoff`, `integration.preview`.
`pm.ahp.observe`, `pm.backlog.revise`, `pm.continuation.prepare`, `pm.conversation.adopt`, `pm.conversation.propose`.
`pm.dependencies.capture`, `pm.dependencies.declare`, `pm.dependencies.retract`, `pm.dod.revise`, `pm.evidence.capture`.
`pm.execution.runtime.observe`, `pm.execution.verify`, `pm.follow_up.analyze`, `pm.follow_up.prepare`, `pm.github.import`.
`pm.github.observe`, `pm.governance.adoption.link`, `pm.governance.decide`, `pm.governance.record`, `pm.governance.revise`.
`pm.history.link`, `pm.history.unlink`, `pm.input.confirm`, `pm.input.record`, `pm.issue.cancel`.
`pm.issue.publish`, `pm.lifecycle.close`, `pm.lifecycle.pause`, `pm.lifecycle.reopen`, `pm.lifecycle.retain_inflight`.
`pm.lifecycle.set_stage`, `pm.maintenance.check`, `pm.maintenance.repair`, `pm.maintenance.repair.evidence.record`, `pm.maintenance.repair.runtime.observe`.
`pm.maintenance_policy.adopt`, `pm.maintenance_review.candidate.record`, `pm.maintenance_review.complete`, `pm.maintenance_review.follow_up.record`, `pm.maintenance_review.note`.
`pm.maintenance_review.prepare`, `pm.meeting.complete`, `pm.meeting.context.revise`, `pm.meeting.create`, `pm.meeting.draft`.
`pm.meeting.follow_up.record`, `pm.meeting.note`, `pm.message.append`, `pm.overview.assess`, `pm.plan.adopt`.
`pm.plan.draft`, `pm.product_goal.revise`, `pm.project.bind`, `pm.quality.review.create`, `pm.quality.review.record`.
`pm.quality.review.revoke`, `pm.release_baseline.register`, `pm.roadmap.baseline.set`, `pm.roadmap.forecast.set`, `pm.roadmap.observe`.
`pm.run.analyze`, `pm.run.bat.analyze`, `pm.run.prepare`, `pm.session.quiescence.observe`, `pm.snapshot.create`.
`pm.spec.materialize`, `pm.sprint.cancel`, `pm.sprint.close`, `pm.sprint.create`, `pm.sprint.goal.revise`.
`pm.sprint.select`, `pm.sprint.start`, `pm.thread.create`, `pm.thread.plan`, `pm.thread.plan.revise`.
`pm.thread.rename`, `pm.wip_policy.save`, `pm.work.bind`, `pm.work_batch.evidence.record`, `pm.work_batch.start`.
`pm.work_input.defer`, `pm.work_package.create`, `pm.work_profile.save`, `pm.workload.capture`, `pm.workspace.adapter.select`.
`pm.workspace.create`, `pm.workspace.member.set`, `repository.continue`.
`session.labels.set`, `session.position`, `session.read`, `session.record_verification`.
`work.adopt_and_start`, `work.start`, `work_item.approve`, `work_item.continue`, `work_item.order`.
`work_item.pin`, `work_item.update`.

A send is a Work input only when its stored target is exactly the workspace/Work,
its existing `work_inputs` row and immutable admission binding name this
Attempt/original execution, and its resolved session is this Attempt's session.
It retains the existing Work-input marker path. A send whose stored target is
exactly the Attempt host/session is a direct input. Anything else is generic
unknown, including a mismatched resolved target; targets are never rewritten.

## Direct inputs and reachable release

Only new agent-work inputs create or extend a hold. `pm_direct_inputs.eligible`
is shared by the collector and the WIP read: original handoff lineage, exact
host/session stored target, terminal/non-uncertain operation, succeeded send
step, equal request/response message IDs and accepted=true are all required.
The original external-step uncertainty predicate is preserved. The WIP read
still enumerates, authorizes and charges **every** lineage source, including
non-inputs and controls; hidden sources and any budget overflow remain the same
generic unknown. The eligible lookup is charged too; it performs no provider read.

Required receipts are the existing initial/latest Work-input marker plus every
eligible direct marker. Each must match the original source key and generation
and the existing terminal-boundary predicate (`turn_done` or `agent_error`, not
`process_ended`). Attribution is `correlated`, or `correlated_after_prior_turn`
only for a direct marker whose original send response says queued. ACK alone
keeps the hold. The collector can now produce each such receipt through its
ordinary original-marker read and append-only receipt path; no release action
or new completion authority is introduced.

The WIP bound `per_execution_direct_inputs: 500` covers all later direct inputs
for one execution, including accepted/pending inputs. At 501 return generic
unknown, without partial counts. Collector eligibility separately fetches at
most 501 eligible inputs; overflow skips only its additive direct step and records
`direct_input_limit` in the separate direct check. The existing step still runs. The shared lookup starts at the original's lineage index
and uses existing operation/step indexes, never a global history scan. Only the
bounded rows are sorted in memory by `operations.rowid` admission order, then
operation ID. No fifth index, schema/data step or admission change is needed.


Each collector pass completes the existing initial/latest Work-input step first,
including its original memo, check and queue wake behavior. Only an Attempt that
survives that memo short-circuit, has an existing terminal marker receipt and has
remaining pass time may receive one additive direct step. That step never wakes
the Work queue and never reads or writes the existing memo entry; its check reason
is stored separately. Thus a queued direct marker cannot delay the existing
prior-turn queue wake. An eligible pass performs at most one additional session read.


### Direct-send outcome table

This is direct-specific; the original handoff `unsent()` predicate is unchanged.
Every row still requires lineage authorization and budget charging.

| Original durable state | WIP outcome |
| --- | --- |
| Accepted/running/waiting/needs_attention without a proven terminal outcome | Hold while the original operation can settle. |
| Terminal with no send step (including cancellation before dispatch) | No execution hold. |
| Terminal, failed send step with durable `external_refs.direct_send_dispatch:false` | No execution hold: the original transport fence proves the frame was not written. |
| Terminal, failed send step with `REFUSED`, without a false transport fence | Generic `coverage_unavailable`: result-phase audit failures can raise `WriteRefused` after transport. Pre-fence history is not guessed. |
| Terminal with original send response `accepted:false` | No execution hold (including NOT_ACCEPTED followed by cancellation). |
| Terminal, certain, succeeded send step with `accepted:true` and exact request/response message ID | Hold until its exact terminal receipt. Shared eligibility accepts succeeded, failed or cancelled terminal bookkeeping, so every such ACK has a producer path. |
| Any unresolved external step, including lost ACK or exhausted reconciliation | Hold until existing reconciliation proves the original outcome; cancellation never proves release. |
| Any other terminal evidence, including a generic failed send or malformed/mismatched ACK | Generic `coverage_unavailable`, no counts. |

Direct sends persist `external_refs.direct_send_dispatch:false` before the BAT
call, and `true` synchronously through the existing `on_transport` hook before
WebSocket send. The refs merge commits before the hook returns; no await separates
the hook and send. A crash after that commit but before the frame leaves true:
**true means possibly sent, never proof of delivery**. A true fence without a
certain ACK is not release evidence.

This direct-specific key deliberately does not match `<step>_sent`. The generic
Wait handler, reconciliation, Work-input queue and operation-loop projections do
not interpret it. In particular, a direct Wait still follows the original generic
failure path, including after the direct fence becomes true. Work/task send fences
are unchanged. Only WIP uses this additional dispatch evidence. HTTP, RPC and MCP
operation reads expose it as additive external_refs metadata; no request hash,
status, retry or step-error mapping is changed. Older rows without the key, including
rows with only the earlier send_sent metadata, are not guessed or backfilled.

Ordinary cancellation after ACK leaves a succeeded operation unchanged; it does
not create a cancelled accepted send. Failed/cancelled bookkeeping with a certain
ACK is conservatively supported by the shared predicate and tested as synthetic
history, not claimed as an ordinary action outcome. Claude reconciliation can
recover an exact transcript echo or its durable accepted-turn registry record
without resending. An unobserved lost ACK (notably Codex without that registry
record) can exhaust retries without proof; this remains an inherited unresolved
hold, not a new release authority. Unknown evidence never becomes zero.


### Receipt work is charged once

The original follow-up terminal-boundary predicate is shared at row level.
`_has_turn_boundary_receipt` retains its behavior (either stored position or
receipt-document marker, terminal `turn_done`/`agent_error`). WIP applies that
predicate once to its already authorized, record-budget-charged receipt rows;
it never performs an uncharged receipt query per required marker. The additive
collector step likewise reads the Attempt receipt range once, builds its terminal
marker set, then chooses the earliest eligible missing marker. Existing selection,
queue wake and source/generation/attribution requirements do not change.

### Shared direct / Work-input markers

The direct step reserves an existing Work-input marker for its own collector step,
including queued inputs, except for either of these terminal owners:

- Failed/cancelled with a non-true original Work `send_sent` fence and a
  failed/cancelled `receipt_state`, following the existing Work-input authority.
- Failed with BAT's explicit `NOT_ACCEPTED` classification, using the same
  `rejected_turn` predicate as `latest_sent_turn`. This owner cannot resume or
  produce an own Work turn, even though its rejected frame crossed transport.

A never-entered send step has no fence; an entered current step writes false
before any frame. An absent fence follows `receipt_state`'s compatibility
semantics, not physical proof of no transport on every historical build.
The intermediate `ce53d7ed` admitted Work-target sends without the fenced body;
`ae30f2e1` connected that body. Available release tags containing the former all
also contain the latter (13 tags, from `v0.2.6-preview.20261017` through `v0.2.18`).
No release tag in that inventory exposes this intermediate unfenced Work producer;
this does not establish what untagged builds were installed.

Uncertain, possibly dispatched, accepted or succeeded owners remain reserved,
including true-fence `REFUSED` and all failures other than the explicit terminal
BAT rejection. Missing owners and all nonterminal owners remain reserved: false
alone can still take the existing `unsent()` → `RERUN` path. No new release
authority or history mutation is added.

The indexed message-ID lookup is scoped to workspace and Attempt and left-joins
the original operation and its unique send step. Missing owner evidence retains
the reservation. At most 500 single-row lookups, each with at most three existing
unique-index seeks, are added per direct pass; no input-history scan is introduced.
The initial marker already has its own terminal receipt before a direct step runs.
Both normal send orders keep the own Result, and WIP accepts the same terminal
evidence for both holds. Once a skipped owner becomes terminal-unsent or terminal-rejected, a later
ordinary eligible pass records the direct receipt under the direct operation;
that receipt remains excluded from the own Result. Cancellation before/after the
direct ACK and a pre-frame reused-ID refusal require neither resend nor deletion.

Malformed unrelated receipt boundaries do not affect follow-up admission: the
original marker relevance check still precedes boundary interpretation. The
single-marker predicate retains its existing error on an unhashable relevant
boundary; the all-marker WIP/collector projection ignores non-string boundaries
as no terminal proof. Neither path invents release evidence from malformed rows.
