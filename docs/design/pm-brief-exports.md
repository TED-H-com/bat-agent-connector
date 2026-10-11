# Fixed Project Brief exports

This is the first W39/W40 output slice from [PM v1.2 section 10.8](../product/pm-v1.2-plan.md).
It renders **Project Brief only**, using one explicit existing
[DocumentSnapshot](pm-snapshots.md). Roadmap, Kanban, Sprint and governance output
views, editable presentations, PDF/PPTX, delivery workflows and shared links are
not implemented by this module.

## Read contract

```python
pm_brief_exports.render_brief(
    ops, principal, workspace_id, snapshot_id,
    format="markdown",  # markdown | html | svg
    locale="en",        # en | zh-TW
)
```

The function calls `pm_snapshots.get_snapshot` with the requesting principal and
exact workspace/snapshot IDs. It requires current `observe` scope and workspace
membership, and rechecks each external evidence source through the existing
snapshot policy. It never borrows the snapshot creator's access. Wrong-workspace,
unknown and inaccessible snapshot requests retain the existing read errors.
Unsupported format/locale produces `INVALID_PARAMS` (422), without fallback.
Only snapshot template version 1 has a renderer; unsupported versions produce
`PM_EXPORT_TEMPLATE_UNAVAILABLE` (409).

There is no new action, schema, operation, file, ordinary artifact, Git write,
publication or model call. Selecting a report format does not create a newer
snapshot, refresh evidence or complete a Review. An eventual UI/API should offer
these bytes for an explicitly selected snapshot using this read contract; it must
not silently substitute `latest` or reuse an earlier caller's authorized output.

The return value is:

```text
version: 1
workspace_id, snapshot_id
snapshot_digest, view_digest
template_version, renderer_version
locale, format, media_type
content                    UTF-8 text
byte_length                exact encoded content length
output_digest              SHA-256 of exactly those UTF-8 bytes
page_count                 SVG panel count, otherwise null
coverage:
  snapshot                 saved context coverage including current filtering
  visibility               current source visibility
  projection: project_brief_only
  other_management_views: not_rendered
  referenced_sources: fixed_authorized_view
  unreferenced_evidence: not_expanded
  content_truncated: false
execution_authorized: false
review_completed: false
published: false
```

Media types are `text/markdown`, `text/html` and `image/svg+xml`, each with
`charset=utf-8`. Renderer version 1 makes formatting changes identifiable.
Repeated renders of the same authorized view, format, locale and renderer are
byte-identical. No current clock value enters the report.

## One snapshot, five factual sections

Goals, delivery, blockers, deviations and decisions retain the snapshot's exact
item statuses and source references. Accepted inputs preserve user confirmation,
repository claims, assumptions, unresolved and conflicting meanings. A candidate
document is identified separately as not the current commitment. The report does
not include later discussion inputs, the latest plan or any independently fetched
Roadmap, Sprint or governance state.

Labels and deterministic evidence descriptions support English and Traditional
Chinese. User/source-authored goals and rationale remain in their original
language; they are not translated or rewritten by a model. A source appendix
contains stable references, facts, digests, source observation/capture times,
journal cursors, source-input provenance, accepted adoption identity and selected
work/binding/capture versions. Displayed selection counts mean only the visible
saved selection; they are explicitly not whole-project totals. Evidence that does
not contribute to the five-point projection is not expanded into a separate
management conclusion.

These meanings are explicit in every format:

- Assembly time is not a new observation of each source. Each original source
  time remains separate; the central journal cursor is not a remote freshness
  guarantee.
- Work content approval and task claims are not Definition of Done. A test result
  applies to its recorded candidate commit, merge is not deployment, and historical
  deployment evidence does not establish current environment health.
- Quality, completion ratio, qualified-deliverable count, costs and drift remain
  unassessed or unknown where the source says so. An older plan binding is not by
  itself a drift finding. No items is not proof of completion or resolution.
- Partial selected context and later source filtering remain visible. No global
  activity count is used as a substitute for missing coverage.
- Report generation prepares discussion; it does not constitute Review attendance,
  feedback, a decision, accepted scope, execution authorization or publication.

## Current access and stable history

`snapshot_digest` continues to identify the original immutable frozen source
document. `view_digest` identifies the currently readable snapshot projection.
When original source access is withdrawn, the existing snapshot reader removes
the inaccessible fact and all derived Brief entries/source links, and records
filtering. The renderer uses only this filtered result, displays the restriction,
and generates a new `output_digest`; it does not describe the filtered report as
the complete original snapshot. Stored snapshot rows remain unchanged.

Exported bytes already saved by an authorized user are ordinary offline files;
future access changes cannot retroactively revoke those files. Every new call
rechecks access. There is no cross-principal output cache in this implementation.

## Literal portable output and bounds

All three formats share one deterministic content projection. Source text is data:

- Markdown puts literal content in dynamically sized code spans/fenced blocks,
  preventing source HTML, Markdown delimiters and autolink URLs from becoming
  active content. Headings remain standard Markdown headings.
- Standalone HTML escapes every inserted literal and contains only local style
  rules and passive report elements. It has a restrictive content security policy;
  no scripts, frames, forms, links, external assets or network fonts are emitted.
- SVG contains only vector panels, text and basic shapes; there is no script,
  `foreignObject`, image or URL-bearing attribute. Long CJK text and unbroken IDs
  wrap conservatively without ellipses. Long reports continue in numbered vertical
  page panels; no source text is discarded to fit a panel.

Illegal XML controls and bidi direction overrides are shown as visible Unicode
escape notation. HTML/SVG markup is escaped; URLs remain literal text. The report
uses the shared `desktop/DESIGN.md` neutral palette, 15px body scale, readable
14px supporting facts, system fonts and restrained hierarchy. HTML is responsive
and has print rules; SVG uses fixed 960×1280 vector panels separated by 24 units.
This is a portable vector report, not a five-view diagram or presentation deck.

Content is limited to **1,500,000 actual UTF-8 bytes**, and the complete serialized
JSON response to **2,000,000 actual UTF-8 bytes**. SVG also has a **32-panel** limit.
Any exceeded limit raises `PM_EXPORT_TOO_LARGE` (413) without returning a partial
export. The renderer never silently truncates text, references or sections. A
snapshot whose own source coverage is partial can still be exported, with its
original partial-coverage flags preserved; that differs from truncating an export.

## Shared read entrypoints

`GET /api/v1/pm/workspaces/{workspace_id}/snapshots/{snapshot_id}/brief-export`
returns the complete renderer JSON through the existing central API. Optional
query fields are `format=markdown|html|svg` and `locale=en|zh-TW`; omitted values
default to `markdown` and `en`. Blank, repeated, unknown or invalid selectors are
rejected. The path requires exact workspace and snapshot IDs; there is no
`latest/brief-export` alias, content URL, filename, filesystem path or caller
supplied content parameter.

RPC and MCP expose the same `pm_brief_export_get(workspace_id, snapshot_id,
format="markdown", locale="en")` contract with `observe` scope. Each invocation
rechecks current membership and filters the original snapshot through its existing
source reader. Tauri's fixed GET allowlist admits only these paths and enum
queries, with no operation key or request body. All entrypoints preserve complete
JSON or the renderer's explicit 413; none return a shortened report.

`features.pm_workspaces.brief_export` advertises the supported formats, locales
and renderer version. It means that the read renderer is available; it does not
claim that a local save dialog, presentation editor or all five view exports
exist. The canonical skill and its generated agent bundles describe this same
fixed read and output boundary.

This entrypoint does not use ordinary artifact storage or grants. It creates no
durable operations, files, share links or publication. A future user-initiated
download can validate `byte_length` and `output_digest` against the UTF-8 content,
then save the bytes through an explicit local destination workflow. Report text
must never become app HTML, an executable instruction or a URL to open.

## Validation

Focused tests use real central-journal snapshots and mock source records to check
all three formats/locales, fixed revisions despite later plan/work changes,
current membership and source withdrawal, no writes, candidate/unknown meanings,
malicious literals, precise UTF-8 bounds, and long SVG pagination. The tests do not
call a live provider or claim a professional PM analysis or Review took place.
