# Roadmap report materialization core

This contract covers the private core for `pm.roadmap.report.materialize`. The
action installer is used explicitly by tests only. Every daemon installs the empty
receipt table, index and immutability triggers through `schema`; this does not
register the action. There is no public action installation,
receipt route or capability advertisement in this increment. The authorized
read-only [report contract](pm-roadmap-report.md) still applies to rendering.

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
The summed file bound is 512,000 bytes. Both mutation and readback encodings must
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

Visibility uses the existing source-lineage hook and request-scoped read cache.
Within one synchronous snapshot, the same observation's full `load_fixed` closure
is authorized at most once for a principal. No permission cache persists between
requests, writes, transactions, tasks or event-loop turns. Cycle and budget
refusals retain the existing guarded-cache rules. Collection readers start an
independent traversal budget and recursion state for each top-level record while
sharing their snapshot and completed memo results. A nested read inside an active
traversal keeps its budget through every open scope, including intervening
principal changes; only the live-traversal fact and budget are inherited, never
another principal's grants or partial answers. Every independently visible row
remains visible on the page, and every visible row has complete source
authorization under the unchanged rules. Completed proofs can save traversal
work: a row refused alone only by the 2,048-node computational guard may become
visible with a warm page cache. Universal equality of computational refusals is
not required. Response-wide work limits and transport
finalization are still part of the pending public integration.

The private freeze stays for the journal's lifetime, following spec materialization.
Frozen bytes can exist without a receipt, including after a failed or uncertain
effect. Withdrawal stops future disclosure and new effects; it neither erases the
private freeze nor rewrites Git. No cleanup removes a freeze beneath an uncertain
operation. Finite retention or erasure is a later owner policy question.
