# Roadmap report materialization core

This contract covers `pm.roadmap.report.materialize` and its source-authorized
receipt reads over HTTP, RPC and MCP. The daemon installs the action and its
existing idempotent ledger. The authorized read-only
[report contract](pm-roadmap-report.md) still applies to rendering.

Submit the registered action through the ordinary OperationService with target
`{workspace_id, observation_revision_id}`, params `{format, locale,
renderer_version}`, and preconditions `{expected_source_set_digest,
expected_view_digest, expected_output_digest}` from that exact rendered read.
Use one stable idempotency key. The action remains an investment, requires the
existing contributor/owner role and all three scopes, and creates no new
confirmation or transport channel.

Receipt reads require `observe` and current workspace/source access:

- HTTP `GET /api/v1/pm/workspaces/{workspace_id}/roadmap-report-materializations`
  accepts only `after` and `limit`; append `/{materialization_id}` for one receipt,
  with no query selectors.
- RPC/MCP `pm_roadmap_report_materializations_list(workspace_id, after=0, limit=20)`
  and `pm_roadmap_report_materialization_get(workspace_id, materialization_id)`
  share the exact central readers. The signed cursor is 0 through `2**63 - 1`.
  Unknown, repeated, blank and wrongly typed selectors are refused; no coercion
  of booleans, strings or floats to cursor/limit integers in MCP or RPC.

`ACTION in ops.actions` is the only registration authority. It supplies both
`features.pm_workspaces.roadmap_report.materialization` and the presence of
`features.pm_workspaces.roadmap_report_materialization`; capability metadata
adds no independently configurable enabled flag. Per-principal `actions[].allowed`
also requires observe/manage/start. Membership, lifecycle, carrier, writer and
current source facts remain admission-time gates, not installation promises.
Native save, Dashboard save and publication remain outside this surface.

A contributor or owner with `observe`, `manage` and `start` selects an exact
workspace observation, format (`markdown`, `html`, `svg`), locale (`en`, `zh-TW`),
renderer version and expected source-set, view and output digests. Existing
administrator, controller, source-lineage, writer and investment-lifecycle gates
remain authoritative. `load_fixed` authorizes the complete original source closure;
the writer does not implement its own source loader. The carrier's original run
selects the thread. No current thread, plan, forecast or report head substitutes
for a selected historical source.

The `pm.roadmap.report.freeze` local effect stores canonical JSON in
`operation_steps.response`, with an empty request. It freezes report metadata,
source manifest, carrier binding, original run/thread/generation, expected parent,
and the exact path-to-content map. The rendered UTF-8 bytes are unchanged.
The file builder adds a canonical JSON manifest with provenance in a separate file.
There are exactly two new files:

- `docs/pm/reports/pmrpt_<operation hex>/report.<md|html|svg>`
- `docs/pm/reports/pmrpt_<operation hex>/manifest.json`

The carrier kind is `pm-roadmap-report`. No caller can supply a path, ID or body.
The service and host validate the same typed paths, manifest and exact receipt.
The summed file bound is 512,000 bytes. Saving verifies every carrier receipt in the host's `_REVISIONS` file (`batc-pm-revisions.json`), bounded by `rev_load`'s `pm_read(..., maximum=4_000_000)` limit of 4,000,000 bytes; this history verification belongs to saving/reconciliation and never runs on report or receipt read paths. Both mutation and readback encodings must
fit the existing 120,000-byte host-command limit. Neither bound is enlarged.
The complete, unconditional carrier helper travels as plain normalized source in
`python3 -c <source> <request>`; only request data is encoded. Assembly shortens
indentation outside string literals and empties comment-only lines, preserving
literal bytes, docstrings, syntax and line numbers. Its shell-quoted source has a
fixed budget of 41,419 bytes (the original helper overhead): the current normalized
source uses 39,579 UTF-8 bytes, 39,585 quoted bytes, leaving 1,834 bytes spare.
Growth beyond that budget requires equal savings elsewhere in the helper or a
separate transport decision. It must not consume previously valid request capacity,
select code by revision kind, change frozen intent or enlarge the command limit.
Normalized block indentation uses tabs, with irregular indentation preserved.
If tokenization raises `TokenError` or `SyntaxError` (including indentation and
tab errors), the complete source is returned unchanged on every service version.
The helper executes on the host's own `python3`, not the central service's venv.
Host execution and normalization are tested on Python 3.10 and 3.13.
The wider Python 3.7+ evidence is syntax only, not runtime certification or a new
host requirement.
One cached normalized text is keyed by its exact assembled source; this caches
immutable code only, never request data or authority.
Existing report directories, accepted specs, `roadmap.yaml`, arbitrary paths and
generic file writes remain refused. The commit changes neither adopted scope nor
roadmap heads. No artifact upload or model execution occurs.

The append-only `pm_roadmap_report_materializations` table stores metadata and the
verified Git receipt. DDL is idempotent, adds no migration data step and does not
allocate `user_version`. `pm.roadmap.report.record` atomically inserts the receipt,
completes the service run, releases its writer and emits a metadata event. A Git
receipt is proof of that original Git fact, not publication, review or acceptance.

Plan, spec and report writers use `pm_plans.latest_git_receipt` to prove one chain
from parent links. A workspace without report receipts retains its prior parent
selection behavior, including the legacy fallback without a spec table. An earlier
unknown effect keeps the original writer reserved and blocks another writer.
A standalone plan-only journal without the report table has no report receipts;
an explicit schema lookup preserves that same fallback. Normal daemon startup
continues to install the report ledger unconditionally.

Recovery first returns a completed local receipt. Otherwise the saved freeze is
reused. Started or uncertain Git steps read back the original intent. A fully
completed Git transaction may be recorded after withdrawal, cancellation or a
lifecycle stop. Partial intent resumes only with current authority and the same
original epoch, writer, bytes and operation ID. Proven zero effect may rerun the
original step. Unproven effects stay unresolved. A scoped-key replay returns the
same operation; changed intent conflicts only after original visibility checks.

Operation detail/list, events/summaries, run detail/list and thread reads never
serialize step requests or responses. Receipt projections contain metadata and
file hashes, never report bytes, manifest contents, source manifest or file content
maps. Every report-derived field is concealed when any original source is no
longer readable. Known receipt reads return generic `NOT_FOUND`; lists omit the
whole report row. The existing traversal metadata remains, including opaque cursor
IDs, event head/scan cursors, run scan counts and visibility-filtered flags. This
contract does not promise to conceal that a record exists. Existing thread content
keeps its own authorization; materialization adds no thread message or report title.

Each receipt record has `kind: "roadmap_report_materialization"`. Receipt lists
default to 20 records and permit at most 50, following spec materialization;
this is the accepted deviation from DESIGN-B's proposed 50/100. Get and list
bound the complete UTF-8 JSON read document, including the envelope and cursor,
at 2,000,000 bytes. Source access is decided before size diagnostics. Pagination
reserves the largest signed 64-bit cursor. A visible row that does not fit is
not consumed and is encountered again on the next page; if the first examined
row cannot fit, `PM_REPORT_MATERIALIZATION_READ_TOO_LARGE` returns status 413.
Hidden rows advance the scan cursor without exposing their receipt metadata.

Visibility uses the existing source-lineage hook and request-scoped read cache.
Within one synchronous snapshot, the same observation's full `load_fixed` closure
is authorized at most once for a principal. For a plain collection request over HTTP,
RPC or MCP, one observation shared by every returned row costs at most two full
loader calls: one in the handler and one in the fresh final barrier. A request
with an original-source selector costs at most three, including `read_request`
before the handler. Completed proofs are shared only within each synchronous
snapshot, never across these phases or across an await. Withdrawal between the
handler and the final barrier is therefore still enforced.
No permission cache persists between
requests, writes, transactions, tasks or event-loop turns. Cycle and budget
refusals retain the existing guarded-cache rules. Collection readers start an
independent traversal budget and recursion state for each top-level record while
sharing their snapshot and completed memo results. A nested read inside an active
traversal keeps its budget through the whole synchronous scope chain, including
principal changes and closed frames retained by a copied context. A new scope
finds and validates the nearest still-open ancestor; once every ancestor closes,
the same copied context permits independent records again. Only the live-traversal
fact and budget are inherited, never
another principal's grants or partial answers. Every independently visible row
remains visible on the page, and every visible row has complete source
authorization under the unchanged rules. Completed proofs can save traversal
work: a row refused alone only by the 2,048-node computational guard may become
visible with a warm page cache. Universal equality of computational refusals is
not required.

Operations, events, runs and receipt lists count only report-closure work in the
original loader, including hidden and failed traversals. At record boundaries
they stop after 20 newly examined observation identities, 2,000 charged source
records, or 16,000,000 charged source bytes. A record is atomic: its complete
authorization, including nested lists, finishes before another boundary tests
the thresholds. There is no new per-record refusal. Completed same-observation
cache hits add no loader work. A response that examines no report closure keeps
the original reader's exact fields, rows and cursor semantics.

A work stop returns a successful shorter page, possibly empty, with continuation
after the last fully examined record. SQL-prefetched but unexamined rows are not
consumed, authorized, cached, or marked hidden. Existing paired operation cursors,
event seq/head/has-more, run coverage and receipt sequence cursors remain the
only continuation mechanisms; clients must follow an empty continuing page.
Byte-deferred receipts remain unconsumed even though their source proof finished.

For ordinary records selecting one new observation each, and closures within
their individual loader limits, one response authorizes at most 20 observations,
fewer than 4,000 source records and fewer than 32,000,000 source bytes. The last
atomic record may cross a threshold, including the first row. A multi-root record
can cost the threshold plus up to 2,049 full closures (2,048 guarded roots plus
a direct selection); this conservative envelope is not a time bound. Charges
include the failed 2,001st loader record and the bytes of a rejected value.
An arbitrarily corrupt oversized stored value is not bounded by the ordinary
within-limit byte envelope. No universal latency bound follows.

For proof-entry accounting, R counts every independently allocated record budget
at every nesting level, not only outer-page rows. Nesting inside an active
traversal allocates no additional budget; an ordinary nested list under an idle
outer record may allocate its own record budgets. With D counting every direct
report-proof entry, including nested lists, `2,048 × R + D` is a conservative
proof-entry envelope. It does not bound cache lookups, SQL, graph edges,
serialization or arbitrary callback work. Callbacks can invoke arbitrarily many
lists, so no finite universal execution-time or VM-instruction bound follows.

The private freeze stays for the journal's lifetime, following spec materialization.
Frozen bytes can exist without a receipt, including after a failed or uncertain
effect. Withdrawal stops future disclosure and new effects; it neither erases the
private freeze nor rewrites Git. No cleanup removes a freeze beneath an uncertain
operation. Finite retention or erasure is a later owner policy question.

## Known limits: other registered pages carrying report roots

The four budgeted readers above are not the whole public read surface. A report
operation is an original lineage root; existing named operation/resource references
can carry it into another registered page. This does not authorize a new derivative
write: `pm_source_lineage.inherit` still applies the existing action and workspace
gates (`src/bat_agent_connector/pm_source_lineage.py:330`). The save itself creates
an operation, event, run and receipt, not an artifact or session. The following
inventory describes reference-bearing shapes, including inherited references,
not a claim that a report save directly creates every resource listed.

All paths below are under `src/bat_agent_connector/`. Registration is the complete
`pm_source_lineage.py:520` collection set and the additional tests at `:559`,
`:564`, `:575` and `:585`; reference extraction is at `:136`, including nested
named IDs and inherited host identity. The final barrier walks only registered
rows, never arbitrary nested dictionaries as independent pages.

| Registered shape outside the four budgeted lists | How a report root can reach its rows; producer anchors | What bounds work here |
| --- | --- | --- |
| `sessions`, `known_sessions`, `work` | Session/resource lineage or operation IDs in execution projections; `api_v1.py:1594`, `execution_sources.py:161`, `:193` | Producer-selected rows (project work defaults to 50; worktree session bindings select at most 100), then the per-row bounds below. No report-work early stop. |
| `work_items`, `children`, `archived` | Work lineage and nested child/source references; `work_items.py:1125`, `:1227`, `:1061`; `work_item_results.py:104` | Ordinary Work lists clamp to 200; archived lists use `LIST_MAX=200`; child/result projections keep their own limits. Tree/detail projections do not acquire a new aggregate report budget. Pure project metadata has no report root. |
| `artifacts`, `result_artifacts`, `checkpoints` | Historical capture/operation references and resource lineage; `artifacts.py:156`, `work_item_results.py:74`, `checkpoints.py:179`; root expansion at `pm_source_lineage.py:115` | Artifact pages at most 200; result sources at most 20; checkpoint producer limits remain unchanged. Entire nested row content counts toward that row's source limit. |
| `links`, `relations` | Named operation/owner/source/resource references; `work_item_results.py:58`, `pm_external_history_links.py:148`, `:388`, `observation.py:1061` | Existing producer pagination/truncation only; ordinary result links cap at 20. No report-aware work continuation is added to these handlers. A source-free external-history association adds no report closure. |
| `candidates`, `integrations` | Candidate resources, saved operation and receipt references; `integration.py:1422`, `:1431`, `pm_maintenance_review.py:200` | Integration lists clamp to 100, integration source candidates to 200; fixed review/preview documents retain their existing byte bounds and whole-envelope check. |
| `retained`, `tombstones` | Preserved resource/operation identities; `cleanup.py:2167`, `:2195` | Existing pages clamp to 200. A bare string/ref without a recognized identity causes no report authorization. |
| `instructions` | Session instruction operation lineage; `session_instructions.py:77` | At most 100 selected instruction receipts, then per-row authorization. No report-specific work stop. |
| `events` outside the global event reader, and embedded `operations` pages | History/review operation references; `pm_maintenance_review.py:575`, `observation.py:977` history projections; registered by `pm_source_lineage.py:520` | These field names do not imply use of the four budgeted handlers. Their own producer row/byte limits apply, followed by the same final barrier. |
| Deployment `items` with `next_cursor` and a `deployment_id` in every row | Deployment operation, recipe and delivery ancestry; `deployment.py:692`, `pm_source_lineage.py:109`, `:559` | At most 200 deployment rows; exact shape test and per-row source bound. No new cursor-aware report budget. |
| Bootstrap `{snapshot,sync,capabilities,pagination}` | Registered pages inside snapshot parts; `api_v1.py:1586`, `pm_source_lineage.py:585` | Each producer keeps its own limits; the barrier shares completed proofs across all parts and checks the combined non-row envelope whole. Only a part actually using a budgeted reader gets that handler's work stop. There is no new bootstrap-wide report budget. |

For these other shapes, `filter_page` (`pm_source_lineage.py:498`) starts a fresh
source record for every row. `document_roots` (`:184`) enforces 64 original roots
per row, not per response; root expansion has its existing 256-resource guard
(`:75`) and document references retain depth 32 (`:138`). Recursive authorization
retains depth 64 and 2,048 charged nodes per independently allocated record
(`pm_source_reads.py:22`, `:243`). Each report closure retains its own 2,000-record
and 16,000,000-byte guards (`pm_roadmap_report_sources.py:30`, `:75`). Completed
proofs are reused within this barrier's fresh snapshot; incomplete work is not.

There is no 20-observation response cap for these other handlers. Their aggregate
work scales with the rows supplied by their producers, distinct reachable roots,
and any independently allocated nested records, using the R/D accounting above.
These guards do not bound SQL scans, edges, serialization, or the bytes of an
arbitrarily corrupt stored value. They imply no universal response-time bound.
A hidden row is removed without changing the producer's cursor; no work stop or
permission decision is invented by the barrier. Singular/unknown shapes and the
non-row envelope remain whole-document checks, including the 64-root limit.
