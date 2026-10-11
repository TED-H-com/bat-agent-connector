# Fixed five-view snapshots and portable exports

Implementation checkpoint (2026-10-11): this W39 branch is parked while the
conversation-to-review/adoption loop takes priority. Capture/source filtering has
focused regression evidence; the renderer, new routes, native extension and
preview below are work in progress. Browser build and Ruff are sanity checks,
not complete API/native/UI acceptance. Do not advertise this slice as delivered.

W39 completes the four views beside Project Brief: Roadmap (scope and milestones),
Kanban, Sprint and discussions, and risk/change/decisions. They reuse the existing
PM domains. Saving or exporting a view never adopts a plan, dispatches work,
completes a meeting, publishes a file or evaluates missing quality evidence.

## Snapshot extension

`pm.snapshot.create.params.include_management_views: true` explicitly adds a
version-one `management_views` extension. Omitting it preserves the original
snapshot document shape and behavior. Existing stored documents/digests are never
updated. The snapshot's original transaction, plan CAS, lifecycle/membership and
original-operation receipt rules apply. All four views use that one transaction,
`as_of`, journal cursor and selected accepted scope. Capturing makes no network
request and does not refresh saved evidence.

The extension contains bounded original Roadmap revisions, Kanban cards, Sprint
commitments/results and meeting records, and governance revisions. Dates, age,
assignment/coverage unknowns and conclusions are frozen. Original Sprint DoD and
terminal quality assessments are retained; newer goals or review conclusions
cannot rewrite them. Management approval, verification and semantic acceptance
remain different facts. Shared requirement/deliverable counts come from the same
Brief quality projection, never from attempts or card totals.

Records carry flat original access references. Snapshot references include their
original immutable identity plus flattened source authority; no renderer traverses
live heads or expands recursively nested snapshots. Every read authorizes those
original sources for the current reader without replacing their frozen data.
Source withdrawal removes the whole dependent card, finding or discussion and its
links/counts. Coverage marks filtering without revealing omitted identities.
The original snapshot digest remains unchanged; the authorized view digest changes.

Each view contains at most 100 records and each record at most 512 distinct access
references. The complete extended snapshot remains within the existing 2,000,000
UTF-8 response-byte bound (ordinary JSON wire spacing included). Deterministic
whole-record prefixes disclose partial coverage. A first record that cannot fit
is omitted with explicit byte-limited coverage; no source text is sliced. Empty,
unavailable, partial and source-filtered views remain distinct. Existing source
history limits stay visible. No project-wide completeness or zero-cost claim is
inferred from an empty selection.

## Fixed reads and exports

An exact selected snapshot supports `roadmap`, `kanban`, `sprint`, and `governance`
view reads and `svg` / `html` exports, in `en` / `zh-TW`. The existing Brief read,
Markdown/HTML/SVG route and old snapshot renderer stay compatible. A snapshot
without the extension returns an explicit unavailable-view error; it is never
backfilled from today's state. The fixed-view preview and both output formats
consume the same deterministic projection and original references.

All outputs retain snapshot/view/output digests, source times, original revisions,
coverage, unknowns and historical markers. SVG is passive vector/text with bounded
pages; HTML is passive standalone markup with print rules. Literal user text is
escaped, URLs stay text, and XML controls/bidi overrides are visibly escaped.
Limits are 1,500,000 content bytes, 2,000,000 complete JSON bytes and 32 SVG panels.
An oversized export fails wholly with 413; it never silently truncates text.
No render-time clock, model, provider call, durable write or output cache exists.

The shared UI keeps its live editors and adds a compact historical preview/export
selector under the saved Brief. Browser downloads verify exact UTF-8 bytes and
SHA-256. Native Save As accepts only typed fixed references and expected digests,
rereads central after destination selection, and uses the existing create-new
writer. Source changes, identity changes and delayed responses cannot save stale
bytes. Older clients retain Brief reading and explicitly lack new save capability.

## Validation

Tests exercise actual domain-created sources, same-transaction capture, later
head changes, source withdrawal including original quality and quoted ancestors,
old snapshot compatibility, exact bytes/digests, partial bounds, long CJK and
literal hostile text, API/RPC/MCP/native parity and mounted browser/IPC exports.
Screenshots check both locales at 390, 768 and 1440 pixels. CI is not live BAT or
native OS dialog acceptance.
