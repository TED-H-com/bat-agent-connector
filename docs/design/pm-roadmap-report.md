# Fixed scope and roadmap report

This is one W13/W39/W40 renderer, alongside [Project Brief](pm-brief-exports.md).
It uses the existing [Roadmap observation](pm-roadmap.md) and its original
[DocumentSnapshot](pm-snapshots.md). It does not capture new evidence, adopt scope,
change a forecast, materialize Git files, publish, or write native files.
Other management-view renderers and an actual-date authority remain separate work.

## Identity and fixed sources

The anchor is `workspace_id` plus an exact `observation_revision_id` (`pmro_…`).
A snapshot ID alone does **not** identify this report: two observations may select
that snapshot with different scheduling baselines or forecasts. Template 1 and
renderer 1 preserve their output rules; future renderers must be selected explicitly.

A guarded journal read selects only the immutable observation, its exact baseline,
nullable forecast, exact snapshot, and original plan documents. It never selects
current heads or latest records. The snapshot fixes the accepted document and
candidate metadata; candidates are labelled separately and contribute no accepted
requirements. Milestone requirements retain their exact plan revision and IDs,
including a historically adopted plan different from the snapshot's accepted plan.
The first recorded adoption matching that historical revision and scope proves its
adoption; later readoption cannot replace that proof. Work, capture, evidence,
criterion and quality references stay those originally selected.

All accepted requirements, original scope inputs/statuses, baseline milestones,
dependencies, original/selected baseline dates, forecast dates, quality and coverage
are rendered. Shared work and repeated milestones do not multiply deliverables.
Quality uses the existing original-assessment projection; missing or truncated
coverage never becomes qualified. Legacy observations remain unassessed. No live
Work dependency graph, current risk register, model narrative or critical path is
added. The source appendix expands only observation-selected evidence, but **all**
original snapshot evidence remains an authorization dependency.

Actual finish dates are always unknown in renderer 1: there is no actual-date
record yet. Commit, merge, review, acceptance and snapshot times never supply an
actual date. This limitation appears in the legend in both languages. Forecasts
are explicitly those selected by the observation, not today's forecast. Later
adoption, rebaseline, forecast or review revisions cannot change an old report.

## Authority and refusal order

Every read requires `observe`, current workspace membership (reader, contributor
or owner, with the existing authenticated administrator override), and current
access to the entire original source closure. Authority lookups may check current
grants; they never supply replacement report facts or inherit creator authority.

The loader first establishes membership and an exact observation identity. It
then authorizes roadmap operations, snapshot/plan/adoption lineage, every saved
evidence source (including quality proofs and evidence not expanded), original
maintenance ancestors, and selected historical plans. Existing source traversal
and request-snapshot caches are reused; no permission cache crosses requests.
Only after **all** sources are authorized does it check digest/relationship proof,
source/output byte limits, template compatibility, and project the report.

- Bad selectors return `INVALID_PARAMS`/422; absent same-workspace observation is
  `PM_ROADMAP_NOT_FOUND`/404. Missing scope is `FORBIDDEN`/403; workspace access uses
  the existing concealed `NOT_FOUND`/404.
- Hidden, malformed or unprovable source authority, including exhausted traversal
  budgets, returns only `PM_REPORT_SOURCE_UNAVAILABLE`/404 with generic wording.
  It reveals no source-specific size, identity, coverage or mismatch diagnostic.
- Once the whole closure is authorized, inconsistent fixed identity/digest is
  `PM_REPORT_SOURCE_CHANGED`/409; unsupported template/renderer is
  `PM_EXPORT_TEMPLATE_UNAVAILABLE`/409; excessive report size is
  `PM_EXPORT_TOO_LARGE`/413. No successful partial export is returned.

The report's own authorization budget is 2,000 records/operation references and
16,000,000 UTF-8 bytes of charged source data (including raw and decoded records).
Existing lower source depth/node limits still apply. Budget exhaustion means
unproven authorization, **not** a 413. An inaccessible oversized closure is
indistinguishable from an ordinary hidden source. After authorization the pure
source projection is limited to 2,000,000 canonical UTF-8 bytes.

Withdrawal refuses the whole report; this differs deliberately from Brief's
filtered successful view. New read/export attempts cannot disclose old bytes.
Already saved offline copies cannot be retroactively revoked. No new Dashboard,
Tauri route or native-save support is delivered by this backend increment.

## Deterministic output and Unicode

User-facing titles are **Scope and roadmap report** and **範圍與路線圖報告**.
One ordered literal block stream produces Markdown, standalone HTML and paginated
SVG, with English or zh-TW labels. Authored text retains its original language;
there is no model translation. Meaningful stored list order is retained, map keys
and source-reference sets are sorted by Unicode scalar order. JSON uses UTF-8,
sorted keys, `ensure_ascii=False`, `allow_nan=False`; digests use compact separators.

Report text has LF newlines, tabs expanded at four-column stops, no BOM and no
NFC/NFKC normalization. Illegal XML controls, surrogate/noncharacters and bidi
controls are shown as visible code-point escapes, not discarded. Markdown uses
literal variable-length fences; HTML/SVG escape inserted content. HTML uses the
Brief passive markup/CSP/style helpers. Brief renderers 1–4 and their existing
wrapper are unchanged; the new report never calls that Unicode-dependent wrapper.
No script, external asset/font, URL-bearing link, foreignObject or executable
preview is generated.

`pm_roadmap_unicode.py` is generated by
`uv run --python 3.13 python scripts/generate_roadmap_unicode.py`; `--check` verifies
it without writing. The generator requires Python 3.13's Unicode **15.1.0** data.
It records assigned East Asian wide/fullwidth ranges, combining marks, variation
selectors, ZWJ, regional indicators and emoji modifiers. Only the generator uses
`unicodedata`. Runtime layout uses the committed range table on Python 3.10–3.13.

The documented simple cluster rule attaches marks/selectors/modifiers and
ZWJ-linked code points, pairs regional indicators, and preserves LF boundaries.
It is not full UAX #29. Width is the widest constituent: wide/fullwidth and
regional indicators count two units, others one. Clusters over 32 code points
become complete visible escapes in **all** formats. Fixed conservative wrapping
uses one em per width unit and never asks a browser/font for measurements.

SVG uses 960×1280 panels, 24-unit gaps, 40-unit side margins, fixed integer
positions and 20/16/15/14px hierarchy. Its fixed font stack is
`system-ui, Segoe UI, Noto Sans TC, sans-serif`. OS fonts may change pixels but
never bytes. Every source text slice records block/start/end offsets; concatenated
SVG source text reconstructs the complete normalized blocks, including blank lines
and escaped controls, independently of page headers/footers. No ellipses or clipped
content. HTML is responsive/printable with no fixed-height content clipping.

No current clock, random identity, locale date formatting, platform newline,
current state, font measurement or model output enters rendering. Original epoch
times use UTC whole seconds; date-only values retain their ISO calendar dates.
Content is limited to 1,500,000 actual UTF-8 bytes, complete serialized JSON to
2,000,000 bytes, and SVG to 32 panels. Crossing a limit fails wholly with 413.
Original capture truncation remains a labelled source-coverage limitation, not
permission to truncate this export.

## Digests and read surfaces

- `snapshot_digest`: unchanged canonical stored DocumentSnapshot digest.
- `baseline_digest`, `forecast_digest`, `observation_digest`: original canonical
  roadmap documents; absent forecast has null identity/digest.
- Plan `document_digest` and structured-scope digest retain their existing file-set
  and scope definitions; they are not replaced by a new hashing convention.
- `source_set_digest`: canonical sorted source manifest and `roadmap_report_v1`
  profile, covering exact snapshot/roadmap/plan/adoption identities and digests.
  The snapshot digest transitively covers its evidence, accepted scope and lineage
  references. Permission state and format/locale do not enter this digest.
- `view_digest`: canonical fully authorized pure report projection before its own
  digest field is appended. `snapshot_view_digest` separately names the original
  snapshot's full authorized view. Never compare these as the same digest.
- `output_digest`: SHA-256 of exact content UTF-8 bytes, including final newline.

```text
GET /api/v1/pm/workspaces/{workspace_id}/roadmap/revisions/{pmro_id}/report
    ?format=markdown&locale=en&renderer_version=1

pm_roadmap_report_get(workspace_id, observation_revision_id,
    format="markdown", locale="en", renderer_version=1)
```

HTTP, daemon RPC and MCP call the same read. Formats are `markdown|html|svg` and
locales `en|zh-TW`. Renderer version is an integer from 1 to 999,999,999 (default 1); unsupported
positive versions refuse after source authorization. HTTP parses its bounded
integer query; RPC/MCP require an integer, never bool or a coerced string. Empty,
repeated, unknown and invalid selectors are refused. MCP opts into
`_strict_tool_arguments`. There is no latest alias, caller content/path/URL, action,
new table, operation, ordinary artifact, Git effect or native write.

Response `version:1` includes workspace/observation/snapshot/baseline/forecast IDs,
all named digests, template/renderer version, format, locale, media type, content,
byte length, output digest and SVG page count (null otherwise). Coverage contains
snapshot/observation coverage, `visibility.filtered:false`,
`source_policy:whole_original_closure`, `projection:roadmap_report` and
`content_truncated:false`. `execution_authorized`, `review_completed` and
`published` are false. Identical fixed sources/version/format/locale produce
identical bytes on every authorized read; withdrawal produces refusal instead.

`features.pm_workspaces.roadmap_report` advertises version, exact read, required
scope, template/renderer versions, formats/locales, source/withdrawal policy,
byte/page bounds and Unicode version. `materialization:false` and
`native_save:false` distinguish renderer availability from those later features.
No `roadmap_report_materialization` capability is added here.

## Validation and remaining work

Synthetic fixtures cover all six golden byte files, Python 3.10/3.13, Unicode 15
characters, hash seeds/timezones, later adoption/forecast changes, whole-source
withdrawal, authorization-bound concealment before size/mismatch diagnostics,
original quality and maintenance ancestors, literal hostile content, complete
Unicode pagination, 32/33-panel and exact UTF-8/JSON boundaries, transport/scopes
and no writes. Existing Brief/roadmap/snapshot/source tests remain applicable.
CI is not real PM judgment, native dialog or installed-device acceptance.

PR B must separately define explicit `pm.roadmap.report.materialize`, typed carrier
kind `pm-roadmap-report`, `pmrpt_` receipts, original-key reconciliation and the
unchanged carrier limits. Before effects it must specify where frozen bytes live,
prove operation/event/summary/receipt reads cannot expose them, and document
retention/cleanup, following spec materialization. PR A supplies no byte-storage
or materialization authority. Dashboard/native work remains a subsequent PR.
