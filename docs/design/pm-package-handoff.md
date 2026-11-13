# Fixed engineering package handoff (W19 / W38)

This bounded slice extends `repository.continue`; it does not create another
queue, task recipe, agent session controller, or delivery engine. It assigns one
complete immutable engineering package to one existing Connector execution.
The contract is `pm-engineering-handoff/v1`.

## Explicit admission

The existing target, source preview, WorkItem/project preconditions, scopes and
controller authority remain mandatory. Add these optional parameters together:

```json
{
  "pm_package_ref": {
    "workspace_id": "ws_<32hex>",
    "package_id": "pmwp_<32hex>",
    "document_digest": "<64hex>"
  },
  "pm_code_basis": {
    "mode": "implement_fixed_spec_from_selected_base",
    "rationale": "Why this fixed published code baseline is selected"
  }
}
```

The existing linked-work `start` and `manage` scopes, `observe`, current workspace
write membership, original sources and current adopted scope are checked. The
engineering base choice does not acquire scope adoption authority or require a
second owner approval. It belongs to this same explicit dispatch request.

The package's WorkItem, WorkBinding, requirement identities, original work
fingerprint, project and configured repository must match the selected execution.
The numeric repository identity, published ref, full code SHA and binding digest
must match exactly. There is no `latest` resolution and no live PM file lookup.

Specification commits and the implementation starting commit remain distinct.
Existing package readiness calls different SHAs unassessed because it has no
alignment decision. Different SHAs are not evidence of incompatibility. This
request records a caller-selected implementation base with
`compatibility: unverified`; it authorizes implementing the fixed requirements
from that base, not a claim that the base already satisfies the specification.
All other unresolved readiness checks continue to block admission.

The complete original package, source bytes, source identities, requirements,
acceptance conditions, task relationships, design, proposed verification and
delivery descriptions are preserved in the original admission binding and first
prompt. The contract includes its canonical UTF-8 SHA-256. The first prompt must
fit both the existing 20,000-character BAT limit and a 64,000-byte UTF-8 bound,
including user instructions and ordinary artifact instructions. Oversized
contracts are refused whole. Truncating requirements or replacing them with a
mutable filename is not supported.

## Execution and original reconciliation

`source.resolve`, fixed fetch, owned carrier preparation, `session.start` and
`send` remain the existing durable steps. No person's checkout is changed. The
session ID is the existing operation-derived UUID. Its source association is
stored in the operation INSERT transaction before any external effect.

Every newly admitted or proven-unsent retry checks current source access. The
published handoff additionally checks adopted scope, WorkBinding, work
fingerprint and configuration immediately before each unsent BAT frame and owned
Git mutation. The existing client grant/controller/confinement checks remain.
The generic BAT frame hook identifies the original worker; it grants no write.

Lifecycle epochs remain governed by `ctx.step` admission and RERUN restart.
Already admitted bounded steps may finish after a lifecycle stop. The final
frame source check does not change that lifecycle contract.

An uncertain original start/send is reconciled using its original session,
message ID, receipts and transcript. Source withdrawal does not require resending
or preventing internal read-back of an already sent effect. Original local
receipts may be recorded internally while public reads become unavailable. A
successful start is not a verified requirement, Done, merge or deployment.

Readiness keeps `ready_for: engineering_handoff_review`, `execution_available:
false` and `execution_authorized: false`: that read performs no execution.
`dispatch_readiness: separate_fixed_handoff_admission` names the separate command
boundary, with its action, versioned contract, required code-basis decision and
`bounded_full_prompt` materialization disclosed in `handoff` metadata.

## Original-source read inventory

`pm_source_lineage` is an append-only association of central identities to the
original handoff operation. It does not allocate a schema data-step version.
The original fixed package's access policy is evaluated for the current caller;
the original creator's identity is never borrowed for a public read.

| Surface | Authoritative identity and gate |
| --- | --- |
| Operations: get/list, same-key replay, cancel/resume, waited results | Original operation association; shared `pm_access` and final HTTP/RPC response gate |
| API event pages, SSE, Dashboard bootstrap | Event resource and original operation associations; whole inaccessible rows are omitted without resetting scan cursors |
| Session inventory, details/live reads, reading state and instructions | Exact pre-bound `host/session_id`; prefix observations cannot bypass an exact protected session |
| Session read/wait | Source check before observation and again before returning its result, including delayed responses |
| Observation resource/history/relations and worktree views | Exact session/worktree relationship records; no folder/name inference |
| WorkItem, project work lists, result sources and execution candidates | Bound WorkItem and original execution references; inherited source rows are filtered or singular reads refused |
| Integration/checkpoint/repair/cleanup read projections | Explicit original operations, session/relationship identities and named source references |
| Artifact metadata/list/content and native download | Original capture operation through each artifact revision; the whole artifact identity retains all prior source roots |
| Capture previews and captured artifacts | Exact managed creation/accepted execution evidence plus current original-source access, rechecked after awaited reads |
| Artifact acceptance/materialization receipts | Inherited original operation and artifact identities; ordinary upload/download does not create a PM bypass |
| PM evidence of an execution/session | The same source gate in the existing evidence collector, before downstream projections |

Source associations are independent of mutable display names and filesystem
paths. Named central identifier fields and existing typed source selectors are
the only relationships used by the final shared response barrier. It does not
parse prose or infer ownership from UUID-looking text. Only established
collection fields may omit whole rows; an unknown or singular shape containing
hidden sources is refused whole. Fixed PM documents are never silently rewritten
into partial specifications. Whole-row filtering keeps the upstream pagination
cursor and explicitly marks when source visibility filtered the response.

The WorkItem used for an execution becomes a source-bound management projection;
it can no longer expose the derived execution's work summary to callers who
cannot read its original package. Unrelated WorkItems, ordinary artifacts and
legacy sessions keep their previous rules.

### Independent page rows and the final response barrier

HTTP JSON reads, `TaskDaemon.call_api` (including RPC/MCP), and WorkItem result
reads use `pm_source_lineage.read_response`. A registered page's independent
rows do not share the 64-original-source document limit. Each complete row keeps
its own 64-root limit and current-source check; the rest of the response is one
separate document with its own 64-root limit. A hidden row is omitted. A hidden
reference anywhere in the remainder refuses the whole response with the existing
404, even if there are no rows. All reference forms, resource traversal limits,
error codes/messages/statuses, request checks and write admission remain unchanged.

The row inherits `host` through the actual surrounding objects, including the
bootstrap and its `snapshot` object. Retained rows have their document depth
checked from the root of the original response (maximum depth 32), rather than
starting a new depth allowance at each row. As before, a row already omitted for
source visibility does not participate in the final response's depth check.
Nested lists inside a row remain part of that single document; they are not
recursively treated as pages. Filtering preserves order, complete row contents,
cursors and existing metadata. A change sets `source_visibility_filtered`; an
existing `count` follows `filter_page`'s existing collection-group counting rule.
Bootstrap parts preserve their own flags/counts. No byte serialization changes
are made to responses that already pass.

Only these shapes are registered:

- Established top-level list fields: `sessions`, `operations`, `artifacts`,
  `work_items`, `work`, `children`, `links`, `result_artifacts`, `checkpoints`,
  `candidates`, `integrations`, `events`, `relations`, `retained`, `tombstones`,
  `known_sessions`, `instructions`, `archived`.
- Deployment `items`: a `next_cursor` field, an `items` list, and every item a
  dictionary containing `deployment_id`, preserving the existing shape test.
- PM runs: string `workspace_id`, `next_cursor`, `coverage` with integer
  `scanned` and boolean `visibility_filtered`, and a `runs` list whose rows have
  string `run_id` and `operation_id` and the same `workspace_id`. Maintenance
  check runs and arbitrary dictionaries with a `runs` key do not match.
- Bootstrap: dictionary `snapshot`, `sync`, and `capabilities`, plus `pagination`.
  Dictionary snapshot parts recursively use the same page rules. Only their
  registered row lists are excluded from the outer document check; all other
  snapshot metadata and non-dictionary parts remain in the checked remainder.

A single row with more than 64 sources still refuses the **whole page** with 409;
`filter_page` does not catch a lineage limit and silently drop that row. The
remainder at 65 sources also refuses the whole page. Scalars, top-level lists,
single records, write receipts, unknown shapes and lists inside single records
retain the whole-document rule. These are deliberate limits, not partial-result
or cursor signals.

There is no new scope, cache or source budget shared across rows. For N rows returned
by handlers before filtering, across every registered list and bootstrap part, the barrier makes at most one
independent source-visibility check per row and one check of the remainder.
Each keeps the existing maximum of 64 original roots, 2,048 source nodes and
source depth 64; root lookup retains its 256-identity recursion bound. Thus the
source-node allowance scales with N, at most `(N + 1) * 2048`; it is not a new
page-wide allowance. Reference parsing makes at most two passes over each
retained row (one over omitted rows), and one over the assembled remainder,
plus the registered bootstrap-container partition walk. Its cost still depends
on document size/breadth and the existing producer limits and resource lookups.
These are work bounds, not a latency promise or a new global byte limit.

With 66 independently readable adapter selections, real HTTP and daemon reads
perform 132 original-root checks both before and after for operations and events.
The bootstrap fixture distributes those 66 roots across individually bounded
snapshot parts: its checks fall from 156 to 90. PM runs newly gain a second
per-row pass: 66 distinct-source runs cost 66 checks in the handler before the
old union refuses, and 132 total after this fix. For 64 run summaries sharing one
source, the barrier alone changes from 1 union check to 64 independent checks.
The final pass is necessary: handler-time authorization cannot authorize a source
withdrawn before the response barrier. An additional bootstrap fixture places
66 roots in its first source-bearing part: the old barrier aborts before later
parts and performs 89 checks, whereas completing the corrected response takes
178 checks. The cost of a refusal is not a budget for a returned page.

The cost policy has three rules:

1. Every response obeys the per-row-plus-remainder bound above, including responses
   that are ultimately refused. Each check retains its existing limits.
2. For an already registered shape that the old barrier returns (established
   collections, deployment items and bootstrap), original-root checks do not
   increase except for inherited-only references of individual rows. The allowance
   is the sum, over handler-returned rows, of `document_roots(row, inherited_host)`
   minus `document_roots(row)`; no root outside that difference adds an allowance.
   For 64 operation rows sharing one source the barrier costs 65 → 64 checks.
   A returned bootstrap with two 32-row parts spanning 64 distinct sources costs
   192 → 64. With a page `host` and 64 rows sharing only one readable `session_id`,
   it costs 1 → 64: the inherited-only roots explain this exception. Newly
   registered PM runs retain the separate 66 → 132 and 1 → 64 rules above.
3. A response refused by the old barrier may cost more, whether it is now returned
   or still refused. The old barrier may stop at the first failing part; now all
   parts' rows are checked before the remainder. The 89 → 178 bootstrap is the
   returned example. No scope, cache or traversal budget is shared across rows to
   hide that additional work; rule 1 still applies.

A future optimization may use one synchronous barrier snapshot with independent
per-record budgets (the `pm_source_reads.record` mechanism proposed separately, absent from this baseline).
A barrier-wide scope without independent budgets would share the traversal
allowance and could hide readable rows. It is intentionally not implemented here,
and this change has no dependency on that mechanism or roadmap-report work.

### Other response shapes: inventory and known limits

The following inventory follows the HTTP dispatcher, daemon read branches and
`pm_reads` registry. `>64` means the response's row references can collectively
exceed 64 original roots; it does not assert that a typical fixture does so.
`Independent` describes the row records, not aggregate counts in their envelope.
`Filtered` means the producer already performs source visibility filtering, not
merely a workspace-membership or request-scope check. Except for the explicitly registered PM runs row, shapes in this table
remain **unregistered** and retain the whole-document 64-root limit. Eligibility
alone does not establish a new shape contract: this change registers only the
PM runs projection above. Other eligible pages need their own explicit shape,
transport/withdrawal tests and envelope-metadata decision before registration.

| Producer / response lists | >64 | Independent | Filtered | Known limit / reason to retain the document rule |
| --- | --- | --- | --- | --- |
| `pm_runs.list_runs`: `{workspace_id,runs,next_cursor,coverage}` | Yes | Yes | Yes | **Registered** by the complete shape above; handler calls `source_visible` on each run |
| `pm_discussions._list_threads`: `threads` | Yes | Yes | Yes | Also carries context, capabilities and catalog metadata; unregistered |
| `pm_discussions.get_thread` / context: `messages`, inputs and context groups | Yes | No | Yes | One discussion/context document, with its own coverage and ordering |
| `pm_plans.list_plans`: `revisions` | No | Yes | Yes | Summary SELECT omits operation/resource references; revision IDs are not lineage selectors |
| `pm_evidence.list_projects`: `bindings` | Yes | Yes | No | Membership only; source checks cannot be inferred |
| `pm_evidence.list_work`: `bindings` | Yes | Yes | No | Work-binding projection has no per-row source filter |
| `pm_evidence.list_evidence`: `evidence` | Yes | Yes | Yes | Eligible; preserve its inaccessible-source coverage until registered |
| `pm_snapshots.list_snapshots`: `snapshots` | Yes | Yes | Yes | Eligible but unregistered |
| `pm_roadmap_report_get`: Markdown/HTML/SVG report wrapper (HTTP, RPC, MCP) | No row collection | No | No | Fixed report; whole original closure is authorized, never row-filtered. Content is a string; identity/digest/coverage metadata retains the whole-document rule |
| Capabilities: `features.pm_workspaces.roadmap_report` | No | No | No | Nested format/locale/version lists are capability metadata, not source records; no new top-level collection |
| Bootstrap capability addition: `capabilities.features.pm_workspaces.roadmap_report` | No new row collection | No | No | Existing bootstrap shape still matches intentionally; this metadata remains in its remainder, and snapshot page shapes are unchanged |
| `pm_roadmap.list_revisions`: `revisions` | Yes | Yes | No | Includes a shared head and unfiltered revision records |
| `pm_governance.list_records`: `records` | Yes | Yes | Yes | Eligible; coverage and selected context remain whole |
| Governance history: `revisions` | Yes | No | No | One record's history and shared original context; parent access gate |
| `pm_meetings.list_meetings`: `meetings` | Yes | Yes | Yes | Eligible but unregistered |
| Meeting history: `records` | Yes | No | No | One selected meeting's history and coverage |
| Sprint artifact revisions / sprints: `revisions`, `sprints` | Yes | Yes | No | `_list_page` enforces byte coverage, not per-row source filtering |
| Sprint history: `records` and quality/context data | Yes | No | No | Selected sprint/history document; shared quality access gate |
| `pm_issue_mappings.list_mappings`: `mappings` | Yes | Yes | Yes | Eligible; returned/scanned/source-incomplete coverage is not rewritten |
| `pm_work_packages.list_packages`: `packages` | Yes | Yes | Yes | Eligible; returned/has-more coverage remains a document |
| `pm_quality_reviews.list_reviews` / `for_work`: `reviews` | Yes | Yes | Yes | Eligible; selected Work applicability/context remains checked whole |
| `pm_work_batches.list_batches`: `batches` | Yes | Yes | Yes | Eligible but unregistered |
| `pm_work_start.list_attempts`: `attempts` | Yes | Yes | Yes | Eligible; Work parent remains part of the document |
| `pm_work_follow_up.list_inputs`: `items` | Yes | Yes | Yes | Not deployment items; latest-attempt and Work metadata remain whole |
| `pm_spec_materialization.list_materializations`: `materializations` | Yes | Yes | Yes | Eligible; spec receipts, not a roadmap-report API |
| `pm_maintenance_repairs.list_repairs`: `repairs` | Yes | Yes | Yes | Eligible; `next_after` shape remains unregistered |
| `pm_maintenance_checks.list_runs`: `runs` | Yes | Yes | Yes | Distinct from PM runtime runs; no matching coverage shape |
| `pm_maintenance_review.list_reviews`: `reviews` | Yes | Yes | Yes | Eligible but distinct from quality reviews |
| `pm_maintenance_policy.list_policies`: `policies` | Yes | Yes | Yes | Eligible but unregistered |
| `pm_release_baselines.list_baselines`: `baselines` | Yes | Yes | Yes | Eligible but unregistered |
| AHP / GitHub observation lists: `observations` | Yes | Yes | Yes | Eligible, distinct producer contracts |
| `pm_workload.list_snapshots`: `snapshots` | Yes | Yes | Yes | Eligible but distinct from document snapshots |
| `pm_conversation_proposals.list_proposals`: `proposals` | Yes | Yes | Yes | Eligible; coverage retained whole |
| `pm_follow_up.list_intakes`: `intakes` | Yes | Yes | Yes | Eligible; original-snapshot visibility retained |
| Follow-up / overview assessments: `assessments` | Yes | Yes | Yes | Different cursors, freshness and partial metadata; unregistered |
| Lifecycle transitions / inflight: `transitions`, `inflight` | Yes | Yes | Yes | Eligible; transition identity and omitted counts remain whole |
| Dependency revision summaries | No | Yes | No | Capture metadata only, without operation/resource references; one selected-source access check |
| Dependency graph / impact | Yes | No | No | One selected revision/context graph, not an independently filtered record page |
| Register / Schedule rows | No | No | Yes | Producers already stop the row union at 64 roots; retain totals, date ranges, grouping and cursors |
| Overview reference lists | No | No | Yes | Producer trims references to its whole-document lineage bound and reports `source_limit` |
| Kanban / WIP | Yes | No | Yes | Joined views with totals, coverage, dependencies or grouped rows; no generic row splitting |
| Resource usage rows, quota pools and day buckets | No | No | No | Usage source IDs/aggregates, not lineage record references; withdrawal follows collector grants |
| Work delivery projections: `deliveries` | Yes | Yes | Yes | Eligible receipts; Work identity and original-package envelope remain whole |
| Work profiles: `profiles`, `default_profile_id`, `count` | Yes | Yes | No | Independent profile revisions with shared default/total metadata; membership is not source filtering |
| External-history source `rows` | No | Yes | Yes | Current source grants select the catalog; external source IDs are not original lineage references |
| Selected external-history `entries` | No | No | No | One granted transcript and coverage document; not per-entry original-source filtering |
| Release-source `bindings` and provider `releases` | No | Yes | No | Only project/repository choices or release ID/tag/publication metadata; no original operation references |
| Release baseline deployment choices: `deployments` | Yes | Yes | Yes | Eligible; source/eligibility flags and project selection stay whole |
| Hosts, discovery, connections, preferences, policies, skill catalogs, PM workspaces, profiles/capabilities, API tokens | No | Yes | No | Configuration/identity catalogs; existing authority gates, no inferred lineage pages |
| Ordinary projects | No | Yes | No | Project metadata; associated Work pages use their own established collection |
| GitHub `pulls` list | No | Yes | No | Returns number/title/state/draft only, without a provider/repository identity or operation refs |
| Provider, integration, approval and spec-source previews / file selections | Yes | No | No | Whole selected preview or source document; only established top-level candidate lists are split |
| Session/task/worktree history and related metadata outside established `events`/`relations`/`sessions` lists | Yes | No | No | Resource-specific read document; only the already established lists are split |
| Work status/result task lists and task-control receipts | Yes | No | No | One task/work projection, not a registered independently filtered page |
| Raw top-level lists, nested lists in detail/receipt/preview payloads, any unknown extension | Yes | No | No | Whole-document fallback, including the original 64-root limit |

The registered collections still cover maintenance review `events`, external
history associations/cleanup `links`, ordinary Work/result links, deployment environment history `items`, retained/tombstone pages and existing
session/observation lists wherever their established top-level fields appear.
The merge of `e35a71a3` was audited against `447da212`: the only new domain read
is `pm_roadmap_report_get`, shared by HTTP, RPC and MCP. Across all six outputs,
its top-level keys match none of `_COLLECTIONS`, deployment `items`, PM runs or
bootstrap. The existing capabilities read and bootstrap only gain the nested
capability metadata listed above; no new shape is registered accidentally.
This inventory does not grant a new capability or authorize any write.

## Supported derivatives and limits

Existing session send/answer/interrupt and Connector metadata operations inherit
the original source association. They retain the original session controller and
incarnation checks. `session.read` and `session.position` only save personal
observation state, so trusted source-bound requests are lifecycle records and
remain usable while paused. Original source checks still apply; an external step
cannot use this local record exception. Managed artifact capture/acceptance and local WorkItem
metadata similarly keep the original source roots. New publication, integration,
checkpoint continuation, cross-work links, repair or cleanup writes without a
complete fixed PM handoff contract are refused with
`PM_DERIVED_ACTION_UNAVAILABLE`. Their existence in the older product does not
make this first PM dispatch slice a complete M4 delivery implementation.

Normal PM-domain commands keep their own adopted-scope/source contracts; when
they refer to a source-bound execution they also inherit its read policy and must
belong to the same PM workspace. A caller cannot supply trusted lineage fields.

Next slices still need owned immutable specification materialization for larger
packages, explicit provenance across integration/delivery derivatives, and an
owned published specification carrier for later commits. W36 currently reads the
PM carrier's original source commit. This handoff does not rebase or overwrite
that carrier, fetch missing latest specs, or relabel a PM plan commit as code.

Tests use real disk Git fixtures and mock BAT/provider APIs. No live hosts,
production GitHub writes, model calls or native OS acceptance are implied.
