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
Every adoption row matching that historical plan revision and scope is included,
authorized with its original commit operation, charged against the traversal budget,
and listed in canonical manifest order. The current adoption CAS produces one match;
legacy duplicates are all required. Physical rowids and rebuild order are never
selection criteria. The snapshot's accepted-adoption decision is also resolved
exactly, including when the scheduling baseline has no milestones. Compared with
reports before that existence proof, an ordinary single-adoption/empty-baseline report
now includes the accepted decision in its manifest. Its source-set, view and output
digests and source appendix intentionally change; no source authority fix is rolled back.
Work, capture, evidence, criterion and quality references stay those originally selected.

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
then requires existence of each named operation before its existing lineage gate,
and authorizes roadmap operations, snapshot/plan/adoption lineage, every saved
evidence source (including quality proofs and evidence not expanded), original
maintenance ancestors, and selected historical plans. Existing source traversal
and request-snapshot caches are reused; no permission cache crosses requests.
Collection validates containers and reference types needed to enumerate authority
at each source load. Unenumerable authority is unavailable, even for a future template.
Only after **all** sources are authorized does it check identity/digest proofs, then
snapshot template compatibility, before any version-1-specific proof or projection.
Version 1 proves observation evidence is a subset of authorized snapshot evidence,
milestone IDs equal the baseline, selected quality contexts agree, and adoption
results agree with their rows. It never silently filters an inconsistent observation.
Source/output bounds and rendering follow these proofs.

`pm_roadmap_report_schema.py` is the single consumed-field inventory. Immediately
after the template check, its version-1 validator covers observation/baseline/forecast,
the snapshot (accepted/candidate plan, work, capture, evidence and coverage), plan
inputs/sections/requirements, adoption results, and each consumed evidence/quality
variant, including criterion matrices. Container elements are validated too. Capture
coverage uses original boolean flags and nonnegative integer source counters. Optional
legacy quality context may be absent; a present incompatible context refuses. Fields
only passed through canonical JSON are explicitly opaque JSON leaves, not silently
assumed mappings. Required map references are proven before shared projections index
them. Unknown extra fields retain their original canonical representation.

Before complete authorization, Connector-owned fixed IDs and digests passed by
this loader to SQL or a reader remain nonempty UTF-8 strings of at most 512 bytes.
External session/resource IDs and externally sourced kind/type strings instead
accept nonempty UTF-8 scalars within the existing source budget. Work-link `ref`
uses its producer's 300-character bound, not a 512-byte bound. The committed
reference-domain inventory classifies all 168 paths; producer admission is unchanged; maintenance kinds and
optional basis values must belong to their declared sets. Shared readers are unchanged.
The collection boundary alone conceals OperationError, KeyError, TypeError, ValueError,
RecursionError, AttributeError, IndexError, sqlite3.ProgrammingError,
sqlite3.InterfaceError and OverflowError. Other SQLite failures (including locked,
I/O and corruption errors) retain their existing behavior. No catch surrounds the
v1 validator, proofs, projection or renderer. Digest-consistent malformed v1 structures
refuse with PM_REPORT_SOURCE_CHANGED/409 after authorization, never a transport 422.
Immediately after the template check, an iterative whole-document walk includes
opaque JSON leaves and unknown members. The downstream safety bound is the native
JSON budget of **127 nested containers**, matching `connection_sources._native_json_values`
and serde_json. Each complete document's root dict/list counts as container one;
each nested dict/list adds one; scalars add zero. Journal row envelopes do not count.
The 128th container gives the same controlled 409 after authorization. The production
walk is iterative and checks depth only; it does not call the recursive native checker.
This generous safety bound is not a contract on current producer shapes. The real
maintenance repair fixture currently reaches 12 containers, leaving 115 containers
of margin. Its test requires producer depth to remain at most one quarter of the
budget, so producer growth is detected long before the read boundary. It prints the
deepest path (currently the repair child's confinement/evidence/host_check/protected_roots).
No interpreter recursion limit or post-authorization catch changes. Collection's
existing concealment still applies if decoding or authorization cannot complete.
The separate content-byte and SVG-page limits still apply to authorized documents
within the depth budget; depth acceptance is not a promise of unbounded output.

Field enumeration alone does not prove downstream safety. Test-only recording
mappings audit actual projection and all renderer accesses for ordinary, legacy,
quality matrix, maintenance, forecast and empty-baseline closures. Explicit opaque
metadata is only copied to serialized JSON. Boundary tests measure stack headroom
through the service, shared read, daemon, HTTP, RPC and MCP paths, including deepcopy,
canonical JSON and all six outputs.
The peak must leave at least half the default recursion budget free; over-depth input
never reaches projection. Producer literal enumeration drift is checked separately.

Schema-generated tests enumerate every field and container element using all eight
required mutation types, plus empty and over-bound Unicode strings. Type-compatible
values (for example a legitimately nullable field) are distinguished from malformed
ones; invalid authority shapes are compared as raw HTTP bytes in ordinary, oversized
and hidden-later contexts.

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
NFC/NFKC normalization. After newline/tab normalization, the complete escape set
is C0 U+0000–U+001F
except LF, DEL U+007F, C1 U+0080–U+009F, bidi controls U+061C/U+200E/U+200F/
U+202A–U+202E/U+2066–U+2069, all 66 noncharacters (U+FDD0–U+FDEF and
U+nFFFE/U+nFFFF for planes 0–16), and lone surrogates U+D800–U+DFFF.
They are shown as visible code-point escapes, not discarded. These sets are pinned
in the generated table. Public operation admission rejects decoded JSON lone
surrogates before persistence; original canonical digests are unchanged. A malformed
stored authority document that cannot be encoded is concealed during collection.
Markdown uses literal variable-length fences; HTML/SVG escape inserted content. HTML uses the
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
It is omitted unless all existing `roadmap`, `brief_snapshots`, and `plan_drafting`
availability flags are true; no additional availability predicate is introduced.
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
