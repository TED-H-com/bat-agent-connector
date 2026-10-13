# BAT Dashboard design v2

The Web and Tauri clients share `src/`. Python Connector / Task Service remains the
only authority for permissions, project bindings, lifecycle, operations and evidence.
This foundation changes presentation and navigation; individual view projections
continue to follow [the PM contract](../docs/design/pm-project-control-center.md).

## Principles

Answer the management question first. Keep chrome quiet, use one accent, and put
sources and technical identifiers in Details. Preserve one workspace context across
management views, discussions and bound work. A form submission is not evidence of
completion. Unknown, partial, stale and withdrawn states must remain distinguishable.
Never substitute zero for missing data or count a loaded page as a complete inventory.

Keep existing drafts, stable IDs, operation keys, receipt recovery and read-only
manual resources. A source withdrawal removes its facts, counts and inspector;
late responses cannot restore them. View changes do not start another event reader.
Work, session and delivery events refresh the live projections together without
locking the workspace Refresh control; a held projection read can still be superseded
by a newer withdrawal read. PM record events retain the complete workspace refresh.

## Tokens and type

`src/ui/tokens.css` is the colour and type source of truth. Use semantic variables,
including their light/dark variants; do not add literal colours in module CSS.
The system theme applies by default. `data-theme="light"` and `data-theme="dark"`
on the root explicitly override it. Legacy `--panel`, `--muted`, `--chip`,
`--readonly`, `--type-*` and `--radius-*` remain aliases for existing modules.

| Tokens | Purpose |
| --- | --- |
| `--bg`, `--surface`, `--surface-2`, `--surface-3` | Canvas, cards, subtle rows, pressed neutral |
| `--line`, `--line-strong` | Hairlines and input borders |
| `--text`, `--text-2`, `--text-3` | Primary, supporting, meta text |
| `--accent`, `--accent-hover`, `--accent-soft`, `--accent-text` / `--on-accent` | Action, hover, selection, action ink |
| `--ok`, `--warn`, `--bad`, `--info`, `--review`, `--neutral` | Evidence states; each has a `-soft` background |
| `--space-1/2/3/4/5/6/8/10/12` | 4, 8, 12, 16, 20, 24, 32, 40, 48 px |
| `--r-xs/sm/md/lg/pill` | 4, 6, 8, 10, 999 px |
| `--shadow-overlay` | Menus, drawers and dialogs only |

Light `--text-3` is `#646d80` (revised from `#6b7487`): at least 4.67:1
on canvas, surface and surface-2. Dark `#8a93a4` remains at least 5.22:1
on those surfaces. Action ink uses `--on-accent` (alias of `--accent-text`);
the dark ink is `#0b0e14`, not white. Computed contrast is covered by shell tests.

Use the system UI / Segoe UI / Noto Sans TC stack. Body is 14px/1.5. Caption is
12px/500; small text 13px; reading text 15px/1.6; section title 15px/600;
page title 20px/600/1.3; metric 26px/600. Use at most four visible sizes per screen.
`.mono` and `code` use the monospace stack at 12.5px and wrap anywhere. `.tabular`
and `.numeric` use tabular numbers; numeric columns align right.

Visible keyboard focus is a 2px accent ring with 2px offset. Motion is 140ms ease-out
for local transitions. Reduced motion removes animation and transitions; no looping
animation claims work is running. Inputs are 32px on desktop and 16px text on mobile.
Touch actions have at least 44px targets.

## Shared components

Import helpers from `src/ui/components.js`; they return DOM nodes. Append them with
`h()` from `ui/dom.js` or the app's native-link wrapper. Text is always a text node.
Icons come from `ui/icons.js`: locally vendored Lucide SVG geometry, 24×24 viewBox,
1.75 stroke, current colour. No provider markup, inline stylesheets or HTML strings.

| Helper | API and behavior |
| --- | --- |
| `button` | `{variant, size, icon, label, onClick, ...attributes}`; primary/secondary/ghost/danger, md/sm |
| `iconButton` | `{icon, label, onClick}`; accessible name and tooltip required |
| `segmented`, `tabs` | `{options: [{value,label,disabled,panel}], value, onChange, label}`; radio/tab semantics, roving focus, arrows/Home/End; `setValue()` updates silently |
| `statusPill` | Status key; exported `statusVocabulary` maps each key to both labels and its tone |
| `pageHeader` | `{title,pill,meta,actions,overflow}`; one H1, one optional meta line |
| `sectionCard` | `{title,actions,body}`; neutral surface, hairline, 16px padding |
| `metric` | `{label,value,context,bar}`; missing/nonfinite value is a dash and Unknown pill; bar contains sourced `{tone,value}` segments |
| `emptyState` | `{icon,title,text,action}`; one short sentence and next action |
| `skeleton` | Number of static rows, with an accessible loading state |
| `disclosure` | `{label,body,open,key}`; bounded in-memory open-state preference; namespace the key |
| `inlineError` | `{error,retry}`; adjacent error and recovery action |
| `contextChip` | `{icon,text,onRemove}`; optional named removal control |
| `agentMark` | CLI name; local initials for Codex, Claude, Grok, Gemini, BAT, PM |
| `menu` | `{button,items:[{label,icon,href,onSelect,disabled,key}]}`; arrows, Escape, outside click, focus return; dispose on unmount |
| `inspector` | `{title,onClose,returnFocus}`; append returned node, then append content to `.body`; `.close()`/`.dispose()` release listeners and restore focus |
| `dataTable` | `{columns:[{key,label,numeric,render}],rows,rowKey,selected,onSelect,expandable}`; sticky headers, 44px rows, keyboard row navigation and optional expansion |

The inspector occupies 400px at ≥1200px, overlays from the right at 800–1199px,
and becomes a full-screen sheet with Back below 800px. Overlay mode traps focus
and makes background content inert. `returnFocus` accepts an element or a callback
resolved at close. It focuses a connected, rendered target outside hidden/inert
ancestors, falling back to the creation-time opener under the same checks.

Canonical evidence labels: Not started, In progress, Waiting, In review, Accepted,
Blocked, Done, Merged, Deployed, Unknown, Not assessed, Partial, Stale, Read-only,
Draft, Adopted, Historical. Done describes content only; it does not mean delivered.

## Shell and information architecture

The topbar is 48px. At ≥1000px it shows the product mark/name, a plain workspace
link to Overview and one current-view breadcrumb; only the connection pill is on
the right. The workspace switcher exists once, in the sidebar. At smaller widths,
the topbar shows the drawer control, workspace/view name (also opens the drawer),
a named Pending inbox icon with count, and a connection dot. The drawer retains its
footer; opening it hides the duplicate topbar Pending control. Desktop Pending and
Settings live only in the sidebar footer. Loaded Pending counts carry a partial
marker and stay hidden at zero or unknown.

Connection details live in the tooltip; the pill opens Connection. A successful
poll stays Connected while listeners or a view mount are pending. Retrying a failed
poll or waiting for the first poll is Reconnecting; a disconnected client is Offline.
Source refresh failures still pause mutations and event acknowledgment independently
of transport health, with the pause explained in the tooltip.

One persistent sidebar is 248px (232px at 1000–1199px), with its own scroll and an
optional saved resize preference. Below 1000px it is a drawer with scrim, focus trap
and Escape/focus return. The sidebar contains workspace name/central phase, Project
views, explicitly bound Work, and Pending/Tools/Settings. Capability-absent views
are omitted. With no selected PM workspace, the existing global tree remains.

Work uses central workspace/project bindings, never matching names, paths or repos.
A bound session opens the existing work/session view without changing the selected
PM workspace. All projects is a quiet right-aligned link in the Work label row and
opens the global tree. Manual sessions remain read-only. Tree names occupy one line
with ellipsis; the complete name and state stay in the tooltip and accessible name.
A fixed status column shows a dot and short label, or just the dot in a narrow rail.
Rows are 36px on desktop and 44px on touch; children begin at the parent text column.
The global Projects block starts at the same top padding as the workspace block;
New workspace remains available through Tools when no workspace is selected.
The rail uses a shared icon/text grid. Missing capability and empty binding lists
add no permanent message. Failed reads show a compact error with Retry and expandable
details; scope withdrawal clears rows. A search distinguishes unavailable data from
no matches. Only a successfully read empty binding list means no links. A partial
binding resolution explicitly says some linked projects are unavailable; a cleared
403/404 tree never claims its old rows were retained.

| View | Existing content and secondary tools |
| --- | --- |
| Overview | Charter; Report menu for fixed snapshots, comparison, print and export; phase and workspace settings in header overflow |
| Discussions | Thread list, messages, inputs, planning, proposals, bundles, runs and analysis; meetings |
| Requirements | RTM; quality reviews and execution verification, evidence, specs, work packages (including deliveries), handoff and issue publication |
| Schedule | Roadmap baseline/forecast/observation; sprints |
| Dependencies | Maintenance policy, checks, workload and release baselines |
| Issues & changes | Register of risks, issues, actions, changes and decisions when `pm_register` is available; otherwise governance stays primary. Repairs, review, follow-ups and governance remain collapsed disclosures |
| Agents | Resource observations; AHP |
| Board | Kanban; work batches |

Secondary tools use collapsed section disclosures. They keep their original forms
and refresh/withdrawal behavior. Do not rebuild view contents just to move sections.

Routes are `#/pm/<workspace>/<view>` and
`#/pm/<workspace>/discussions/<thread>`. A workspace-only route restores its last
view (Overview where available). Old direct thread routes are replaced in history.
Workspace/view preferences are bounded and keyed by connection namespace, including
principal. View switches retain mounted controls, filters, expansions and selection;
no fetched records are stored in preference maps. Back/forward follows view URLs.
Unavailable views replace the URL with the view actually shown. A 403/404 forgets
the remembered workspace so the PM entry does not reopen withdrawn authority.
Opening a thread immediately remembers Discussions. Returning from Settings rereads
selected report, requirement and resource records before presenting their facts.

`src/pm-discuss-link.js` shares the Discuss with PM URL contract:
`#/pm/<encoded-workspace>/discussions/new?ref=…&ref=…&title=…&from=…&report=…`.
Each `ref` is a JSON context object encoded by `URLSearchParams`; the receiving
`parseDiscussRefs()` keeps up to eight distinct, backend-valid identities in order.
Optional title (trimmed to 160 code points), project-view origin and snapshot report
use the same validation in both directions; empty values are omitted and refs with
extra fields (including display metadata) are rejected.

## Global screens

Pending, Operations, Delivery, Settings, Projects, cleanup, artifact review and the signed-out card share one page header (`.ui-page-header`): header actions centre on the title row, with 16px before the next control. A meta line under the title does not move the actions. Below 800px the action row stays on that same line and scrolls inside the header when the buttons are wider than the column, so the title and the meta lines keep their width. Marks beside the title stay on the title line as well; the title shrinks and wraps inside that line instead of letting the marks wrap underneath it. Pending is an inbox hosted like Operations: the groups and the Details disclosure stay in one column, and the inspector is the column on the right at 1200px and up (the same sheet below that). The Needs you / Active operations / Unread switch is the shared segmented control and keeps focus on the checked option after the view rebuilds. Each group is one surface list: a title and count, hairline rows, and one secondary action. The row selects; the inspector is what opens the next page. A manual or read-only session shows Open and the Read-only pill, never Answer or Review. The row title is not underlined until hover or focus. The status pill sits beside the meta, not at the far edge. A chevron opens the inspector only when that is different from the row action. Needs attention (unknown outcomes, not failures) offers a quiet View all link in the group header. “N shown · Load more” appears only while another page exists. A group hides only after a fresh, successful, empty load. A failed read stays visible as Not updated · Retry, and an error does not turn the Pending badge into zero. Each tab has its own empty state and at most one link: Approvals only when there is something to approve and the inbox is empty, otherwise inside that empty state. Explanations sit once in a Details disclosure after the groups and name only the tabs that are offered. Operations is a dense table (action, target, status, actor, created, duration). Duration is the elapsed time for an active operation and `updated_at − created_at` once it is terminal. The shared segmented filter and the shared search field stay. Selecting a row opens an inspector whose first action is Open operation; Actor, Created, Duration and Attempts sit above Details. Refresh stays disabled while a load is in progress. In the browser, the settings sub-nav (Connection, Desktop, Models, Fleet, Updates, About) shows only the selected section, remembers that section for the namespace, and stacks under 800px. Native settings keep mounted sections visible together and use links with `aria-current`, not tabs. Forms stay within 640px. Connection shows the endpoint, a live state pill, the live last-event time, access as Access · actor · scopes, and a secondary Disconnect. In the browser, the Tailscale note is one muted line inside that card, with no controls and no local calls. Fleet without configuration uses the shared empty state. About shows the product name, the version when one is known, and the setup guide. The signed-out screen is one centred card: the product mark, one sentence, the connect form with the setup guide on the connect row, and the same Tailscale note in the card footer. A rejected token says Token not accepted / 權杖無效 in an alert, with no raw status code. Sidebar names keep the row width minus a 16px mark: circle-alert for Needs you and Asking you, circle-x for Failed, an info dot for Running, and no mark for neutral states. The mark follows the work entry’s own status before the session’s activity. A numeric unread count sits after the name when the entry has one. The status word stays in the tooltip and accessible name. The Pending count badge stays. At 800px and below, row actions, row chevrons and View all keep a 44px hit area. Composed metadata uses `label-text.js`, so a missing target does not leave a separator. Short status words come from the shared vocabulary; precise wording and raw codes stay in Details. Registered operation names have an English and Traditional Chinese label; an unknown name is humanised rather than called Operation.

## Headers, copy and acceptance

One page title; do not repeat the workspace name above it or repeat the view name
below it. At most one primary action, an overflow menu and icon actions. Reading
content has a 760px measure; forms 640px; tables and boards fill up to 1600px.
English uses sentence case. Every new string has natural Traditional Chinese in
`ui/ui-strings.js`, merged into the existing i18n mechanism. No raw keys, undefined,
null, emoji icons or unexplained disabled navigation.
Projection reasons share one frozen map and `reasonLabel(t, reason)` using the app
translator. Unknown codes say Not available / 無法取得; raw codes stay in Details.
Composed labels use `label-text.js` to omit missing segments. A Brief section without
a recorded status says Unknown / 未知. Issue labels prefer the repository name stored
on the mapping, then the published issue name, and otherwise show only the number.

Verify both locales at 390, 768 and 1440px, plus dark mode. Inspect screenshots for
alignment, contrast, wrapped Chinese labels and document overflow. Test keyboard
selection, drawer/menu/inspector Escape and focus return, history, state restoration,
original-key recovery, zero unauthorized writes and source withdrawal. Regenerate
the browser assets from source; Web and Tauri must use the same implementation.
`tests/ui-components.spec.ts` exercises the DOM primitives in a CSP-constrained
source harness. `tests/shell.spec.ts` covers workspace context, navigation history,
withdrawal, namespace isolation, responsive focus behavior and theme overrides.

Withdrawal assertions must select each affected view and open its disclosure, then
check the source element's own `hidden` property and the visible main content.
An ancestor-hidden assertion alone is insufficient. The management fixture covers
Board, Meetings and Sprints before and after late reads; the shell fixture withdraws
bindings through both `pm_evidence` and `project` events without a reload. Temporary
mutations that suppress section hiding or omit PM navigation event triggers must fail.

Computed contrast acceptance (normal enabled controls; light / dark): primary
5.90 / 7.32, danger outline 5.15 / 7.38; pill text by tone: ok 4.77 / 6.11,
warn 4.93 / 6.30, bad 4.83 / 5.55, info 5.23 / 5.61, review 5.34 / 5.91,
neutral 5.19 / 5.44. All meet 4.5:1. The icon legal comment must remain in both
browser and Tauri web bundles; `THIRD_PARTY_NOTICES.md` is a desktop bundle resource.

## Overview

Event pages coalesce reads that have not started. Session, task and command events
do not refresh Overview; work, operation and delivery events refresh its projection
without rebuilding workspace sections. ACK waits for a post-event read, and an older
held read cannot block a newer withdrawal.

Overview defaults to the central `pm_overview` live projection. The shared header
shows the plan title, adoption date, live/report mode and visible lifecycle stage;
the plan journal sequence is never displayed as a version. Four equal status
columns answer goal, accepted requirements, blockers and decisions. Null remains a
dash, incomplete classification keeps only its known denominator with Partial, and
complete coverage describes the scan rather than project completion. Unknown
reasons share the localized reason map; omitted lifecycle operations appear only
in Details. Delivery evidence keeps accepted, merged, deployed and rolled-back
states separate.

Attention retains the central severity/age ordering, with exact context identities
passed to `discussHref`. Reading rows keep their DOM node and focus during refresh; replaced links recover focus
by identity within their own part. Cached ages advance with the projection timestamp.
Overview records its scroll position before a view switch hides it and restores it after
the destination header has finished laying out. Repeated selection
of the active view does not scroll. Related inspectors return focus to the destination
heading; requirement and work links reveal their selection on narrow screens. Requirements and
work open the existing requirements selection; governance opens its existing Issues
selection. Milestone links select the Schedule projection row only when its milestone and baseline
revision match. Attention without a governance record opens its exact central context
in an Issues inspector; no new management record is inferred. The charter follows the live adopted document in six compact cards,
with three bullets before expansion and a labeled Sources disclosure on each card.
Report controls use the same report terminology and shared disclosure chevrons;
the saved history arrows remain together. Narrow attention rows keep age and Discuss
together in their metadata. Details lists localized source coverage, with raw JSON
behind a separate Raw coverage disclosure.
Full plan uses the shared inspector and restores focus on Escape. At 768px the
charter has two columns; at 390px all regions stack in reading order.

Print includes the status strip and full charter; the saved-charter fallback prints
inside its Overview region as well. Interactive controls and live attention are omitted.

Report tools retain the existing durable snapshot operation, original-key recovery,
authorized comparison, history pagination, print and export. Report history loads on
demand and rereads every loaded page after a refresh. Selecting a report
replaces the status and charter with that report's fixed content; live attention,
milestones, delivery rows and phase are absent. Historical snapshots do not contain
central acceptance counts, so those metrics stay unknown with the requirement
denominator on its own line. Restricted, filtered or truncated reports show Partial
in both the saved bar and strip; unfinished report history stays explicit. Choosing
a report shows a loading state. Live decisions distinguish unknown counts from no
items and link to unlisted decisions. Epoch timestamps use local calendar dates;
date-only milestone values retain their calendar date. Back to live rereads the
projection. Source withdrawal invalidates the affected facts and closes their reader;
late responses cannot restore a prior generation. Preferences store only UI state,
scoped to connection/principal and workspace. Older servers retain the saved-charter
view when `pm_overview` is absent.

## Discussions

Discussions uses a 280px searchable thread list, a conversation with its composer
pinned below the scrolling transcript, and a Context / Inputs / Plan panel. Below
1200px the panel opens in the shared drawer; below 800px it becomes a sheet with
Escape and focus return. A quiet origin link sits above the text title; its pencil
opens inline Save / Cancel controls (Enter saves, Escape cancels) using the central
rename action. Cancel discards unsubmitted title edits; a successful Save restores
focus to the pencil after recovery controls unlock. Recovery remains attached to the
original rename intent. Tabs associate their panels for assistive technology.
Context chips use current labels and put differing admission labels in tooltips;
withdrawn refs show only their kind and Unavailable, including in the thread list.
Chips stay on one line with full accessible labels: up to three in the header, then
+N opens Context; one per list row. The selected row has the table accent inset.
The composer has one status/actions row with Send and Ask PM kept together. Failed
and pending note explanations stay visible. Search shares the list header with an
icon-only New discussion button. A list-opened thread returns to Discussions.

The shared `/discussions/new` link opens an editable draft and focuses the composer.
Its first Send creates the bound thread and then appends the note. Both operations
retain their own original keys. The first thread mount transfers the note into the
composer draft and intent, consumes the handoff, and submits it once. A non-empty
composer is never overwritten. Later mounts only recover the original request; they
never auto-send. A new launch always keeps its own route and context. Completed
launch draft keys are removed; a bounded index retains the last 40 return origins.
Opening or reloading an unsent draft performs no write. Thread origins retain the project-view route and fixed report ID through draft reloads; returning
to a report restores both its ID and Overview's saved mode. Source views retain their own
preference state.

Structured analysis stays on the existing capability-checked native/BAT proposal
path. A Proposed update card groups candidates with include toggles, at most three
inline answers, and one owner adoption review. Notes and answers remain central
messages. Only central `adoptable` / `adoptable_reason` determine proposal freshness; the
client does not compare source IDs or thread versions. Renaming alone leaves an
adoptable proposal intact. Known reasons use shared short labels; future reasons
and central VERSION_CONFLICT show Out of date and Ask PM again while retaining
review drafts. An adopted proposal uses the central adopted reason and revision ID
to show Adopted and open the accepted revision in Plan, without another adoption or
Ask PM again action. Raw reason codes stay in Details.
The clock runs only during actual operation checks. Exhausted polling shows Status
not checked; refresh and PM events re-read the known original operation ID and reset
the backoff. Only label changes update the live region; elapsed time is hidden from
announcements. Original-key recovery is retained. Saved proposals, runtime options,
planning inputs, immutable revisions,
context packages, meetings, follow-ups and Brief remain in the thread panel.
Each proposal refresh reads one list and its selected document; picker titles and
status come directly from list rows. Proposal titles replace raw IDs in the picker;
plan name, revision and status replace
commit digests in the reading surface. Identifiers and copyable digests are in Details.
All thread tools use shared chevron disclosures, including retained lazy controls.
Thread disclosures have connection/principal/workspace/thread keys and keep the
reader’s choices across live reads and Refresh. A pending original request opens
Inputs once when the thread is first shown, including after reload, and later
reads never re-expand a reader-closed disclosure.
The Plan tab lists revision rows with centrally read Accepted / Draft / Historical
pills and a Compare action; revision detail reads wait until Plan opens. Meetings
and Follow-up read only while their Context disclosure is open or on their own
events. The Brief uses the same borderless presentation as these sibling sections;
explanatory planning copy is inside Details.

## Schedule

Schedule uses the central `pm_schedule` projection. Day / Week / Month zoom, Today,
and a shared legend sit above a sticky tree and time axis. The tree follows explicit
`parent_id` relationships; there are no phase rows. Undated ancestors retain the tree
structure, and the Unscheduled disclosure includes every loaded undated item.
Baseline is dashed, forecast outlined with a solid stroke, and actual filled. An actual
without end evidence has a short fading marker labelled Open, never a finish at today.
Only a `blocked` status produces hatching. Milestone delta compares recorded dates.

Reads follow opaque cursors sequentially, at most ten pages per refresh. The chart
range uses all loaded rows; any partial/truncated page keeps a Partial pill and a
reason in Details. Unknown reasons use the shared reason vocabulary with a neutral
fallback. Counts describe loaded items only. Source withdrawal clears the chart,
counts and inspector together and invalidates pending reads.

The inspector keeps the Requirements section order and a narrow
`pm-schedule-inspector.render(row, context)` interface for shared-inspector integration.
Session attempts have no dates or bars; their read-only session links and attempts
remain available in the inspector and Unscheduled. Propose change uses `discussHref`
with the exact context identity. Missing result and delivery evidence stays unavailable.
Roadmap and Sprints keep their original durable forms in the header overflow drawer.
Zoom, selection, expansion and scroll preferences are bounded and connection/workspace
keyed. Below 800px, rows become a list with date ranges and independently scrolling
mini timelines; the document never scrolls horizontally.

## Dependencies

The question on this view is which checks have to be run again if one dependency changes.
It reads the current dependency projection, its revisions, and one impact query. Capture,
declare, and retract stay the durable dependency actions, using the projection version,
the workspace version, and the source binding digest. Capture again is the header action.
It is primary only when this member can run it. Otherwise it is a secondary button with the
blocking reason beside it. Maintenance policy, release baselines, maintenance checks, and
workload observations stay in the secondary cards.

The header names the capture commit and its UTC date. A Stale pill is shown only when
the projection says `stale` is true. Unknown freshness stays out of the header and is
stated once under Details. A Partial pill and the omitted total describe the returned
capture, not a repository-wide total. Relation counts do the same, and they are omitted
when the projection has no count object.

The matrix lists every captured component. More than twelve rows start with the first
twelve and Show all (M). Columns group dependencies by kind. A capture of twelve
columns or fewer shows every dependency, so a second component is not an empty row.
More than twelve columns start as the focused component's direct dependencies, with
Show transitive. A cell mark is
R, B, T, C, or Ref. A legend above the matrix names those marks and the evidence styles:
solid verified, outline declared, dashed inferred. The accessible name is the dependency,
relation, version, and evidence. At 800px and below the same rows become a list. The graph
uses the width of the view and centres a drawing that is narrower than that width. It is
labelled Loaded and is not a fleet-wide graph. Selecting a node keeps its upstream and
downstream visible.

The inspector shows a pinned version only when this capture resolved one, plus the
allowed range, the evidence location, and the maintenance owner or the recorded reason.
Tests to re-run come from the capture's verification hints. Affected components come
from the impact read. A bounded prefix says how many components were loaded. An impact
miss says why the dependency is outside this projection, which is not the same as no
impact. Check impact opens a discussion with no context ref; the dependency name and
the impact summary are in the title. Declare and retract stay owner actions in a
disclosure. Details lists every manifest and unreadable path. Unknown reason codes use
the shared wording, and the raw code appears only there.

## Issues & changes

When `features.pm_register.version` is 1, the view reads `GET /pm/workspaces/{id}/register`
and `GET …/register/{row_id}`. Type counts appear in the header only when
`coverage.complete` is true and no type filter is set. A type filter with complete
coverage says how many records match. Otherwise the header meta is the number of
rows shown. An empty blocking-reason list says that no reason field was recorded.
The summary is one 120px strip of three blocks. At 800px and below it collapses
into a Trends disclosure directly under the toolbar, closed until opened, and the
rows become a list (title, one meta line, next step) instead of the wide table.
The list is a list, a list item, and a button. The selected button sets
`aria-current` and `aria-selected`.

A refresh keeps the pages already loaded, up to twenty, and keeps a selected row
until that row's own detail read says it is gone. Overlapping refreshes of the
same filters share one trailing list read. A changed filter starts its own read
and drops the one still in flight. The open row is read again when its list row
changed or it was kept beyond the loaded pages. At 1200px and above, returning
to the view restores the side panel and reads that row. Below 1200px a refresh
or a return keeps the selection and leaves the sheet closed until the row is
opened again, and a return moves focus to that row. A detail read in that case
still drops the row when it is gone, without opening the sheet. Leaving the view cancels register reads that are
still in flight and closes the inspector.
Search and owner accept at most 200 characters.

The table shows the type word; Source stays in the inspector facts. A finding with
no title is named from its kind and its impact or priority. Long owner text
ellipsizes. Related requirement and work chips use a loaded name when one is known.
Filters are one toolbar: type, status, exact owner and title search, with accessible
names and no visible field labels. Pages follow `next_cursor`; row ids and cursors
are passed through unchanged.

The inspector shows the processing timeline in the order the server sent. Status
wording covers the operation, decision and direction states the register emits. An
operation that is accepted for processing does not use the inspected-result word
“Accepted”. An unrecognised status uses the generic wording, and the raw code is
only in Details. Discuss and Propose change use the shared discussion link. A
finding binds the finding ref on its action when that object is a context ref.
Requirement and work links open the Requirements selection. Below 1200px, a row
action closes the sheet, keeps the selection, and moves focus to the form. Those
actions navigate with the view URL.

A 403 or 404 on the list drops the rows, counts and inspector together. A 403 or
404 on one row drops that row and clears the counts until the list is read again.
Governance, repairs, review and follow-up keep their durable forms in collapsed
disclosures, opened from the header overflow. Header actions and capture hints
belong to their active view; an empty overflow is hidden.

## Agents

Agent resources uses the same compact navigation, table and inspector. Separate
source-reported models from requested model context, and observed tokens from current
quota windows. Missing counters stay unknown. Source failures withdraw numbers and
model details together. Filters and a selected inspector keep their place during
background reads; the toolbar can pause view updates without stopping collection.
When the resources projection includes `buckets`, the usage trend is one stacked bar
per local day and CLI. A day that is not `complete`, and any null metric, stays unknown.
The resources read includes `buckets` only when the request sets `buckets=day`.
Omit the chart when the projection has no buckets.
