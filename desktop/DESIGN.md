# Shared dashboard design

This is an operational console: compact, factual and legible. Preserve the existing
browser and native shared source in `src/`; do not introduce a parallel visual theme.

## Palette and type

Use the semantic variables in `src/app.css`, including its dark theme. Light values:
background `--bg: #f6f7f9`, panel `--panel: #fff`, text `--text: #1d2330`, secondary
`--muted: #5d6676`, border `--line: #dfe3ea`, action `--accent: #2f5bd3`, action text
`--accent-text: #fff`. Reuse `--ok`, `--warn`, `--bad`, `--info` for evidence states.
System UI / Segoe UI / Noto Sans TC is the shared stack; body is 15px/1.5,
page headings 20px and section headings 16px. Supporting orchestration text is 14px.

## Components and layout

Reuse `.panel` (14px padding, 10px radius, 1px border), `.capture-fields` (8px gap,
wrapping 220px field basis), `.actions` (8px gap) and the primary/secondary/danger
button variants (6px radius, 6px 12px padding; 36px desktop / 44px mobile height).
Secondary buttons use neutral text and borders; reserve the action fill for the
primary submission. One primary submission per form.
Authenticated views use a persistent 260px project/work sidebar (230px below 1200px)
and a flexible content area. The selected session puts conversation and reply first,
with a 280px work/results inspector beside it at 1200px and above. Narrower screens
place the inspector below the conversation; below 800px a button expands the tree.
Connection-only screens retain the 1100px container; long forms may narrow to 820px.
No new shadows or floating layers. Place status and recovery beside the original
request. Exact IDs and literal inputs must wrap, never disappear behind ellipsis.

## Interaction and responsive behavior

At 640px, forms wrap to one column, action targets are at least 44px and editable
controls are 16px to avoid mobile zoom. Preserve focused drafts during event reads.
Show disabled controls with a concrete scope or evidence explanation. Mark partial
inventory and unknown receipts honestly. Text, icons and color must not imply a
successful effect without central evidence. Never render agent-provided markup.

## Agent guide

The owner's October 12 references prioritize a column board, compact work lists
and readable plans. Default views show work, state and the next action. Put
source IDs, timestamps, accounting definitions and general instructions in
labelled disclosures. Keep actionable errors, missing permission and uncertain
outcomes beside their controls. Do not repeat implementation caveats on every
row or add a help sidebar. Planning sources and version comparisons expand on
demand; a checklist is not evidence that acceptance criteria passed. Board
columns follow central state, and content approval is not renamed delivery Done.

Extract existing CSS tokens before adding UI. Use a compact selection panel and
instruction section for a new action; use receipt links for multi-effect outcomes.
Avoid decorative cards, oversized headings, extra primary buttons or a second font.
Check both locales and 390/768/1440 widths with real screenshots before delivery.

The October 12 visual review keeps light and dark as complete system themes.
Public tours use light consistently across scenes. The work board sits directly
on the page, with quiet lane surfaces and white cards, without an enclosing panel.
PM navigation stays visible while reading a long plan on desktop. Dispatch and
plan reading use an 820px maximum width; buttons and disclosure links should not
compete visually with work titles.

The project workbench defaults to a six-section Charter when plan and snapshot
capabilities are present. Compact project navigation separates report reading,
traceability, work flow and existing editing tools. Summary sequence and adopted
plan identity remain distinct. Report sources and full plan text expand on demand.
RTM uses an expandable table with a work inspector; source IDs wrap below titles.
Keep requirement order, expanded rows and filters stable during source refreshes.
Discussion disclosures on a PM thread keep the open or closed state the reader
chose. Live updates and Refresh do not collapse or expand them. A pending
original request opens Inputs when that thread is first shown, including after
a reload, and a later refresh leaves a reader-closed section closed.
Remove withdrawn source facts from rows, counts and the inspector together.
Composed labels drop a missing segment. A Brief section without a recorded status
says Unknown, and an issue link uses the repository name on the mapping or, when
that field is absent, the name copied onto the published issue. It never prints
a missing value as text.

Agent resources uses the same compact navigation, table and inspector. Separate
source-reported models from requested model context, and observed tokens from current
quota windows. Missing counters stay unknown. Source failures withdraw numbers and
model details together. Filters and a selected inspector keep their place during
background reads; the toolbar can pause view updates without stopping collection.
When the resources projection includes `buckets`, the usage trend is one stacked bar
per local day and CLI. A day that is not `complete`, and any null metric, stays unknown.
The resources read includes `buckets` only when the request sets `buckets=day`.
Omit the chart when the projection has no buckets.
