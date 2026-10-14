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
Events coalesce reads for the visible view and tools mounted in its drawer. Hidden
sections become stale and stay masked until a fresh read settles when shown. A held
projection read can still be superseded by a newer withdrawal read. PM record events
follow the same visibility rule; workspace authority and lifecycle admission have
their own scoped reads even while the Lifecycle drawer is closed. A newer event
read must restore lifecycle gates withdrawn by a superseded manual Refresh.

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

## Shared time stamps

`ui/time.js` is the formatter family: under 24 hours use compact past/future labels;
older instants use month, day and local time. Include the year only outside the current
year. Calendar dates preserve their written day in every timezone. `ui/stamps.js`
uses the app locale and renders `<time datetime>` with a complete localized timezone
tooltip. One document ticker updates only stamp text every minute, retains text selections
inside stamps, pauses while hidden, and never rerenders a view or requests data.
Missing message timestamps lose their datetime, tooltip and ticker marker together.
Native option labels use the same text
formatter because an option cannot contain a `<time>` element. ISO request parameters,
exports and source documents retain their machine representation.

## Shared components

Import helpers from `src/ui/components.js`; they return DOM nodes. Append them with
`h()` from `ui/dom.js` or the app's native-link wrapper. Text is always a text node.
`h()` skips `null`, `undefined` and `false` children. Native `replaceChildren`,
`append`, `prepend`, `before`, `after` and `replaceWith` do not: each empty marker
becomes visible text. Pass those calls through `place(node, method, ...children)`
in `ui/dom.js`, which drops only those three markers and keeps empty strings and numbers.
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
| `inspector` | `{title,onClose,returnFocus,dock}`; append returned node, then append content to `.body`; `.close()` restores focus; ordinary panels dispose by closing; docked panels retain mounting on narrow close and destroy on dispose |
| `dataTable` | `{columns:[{key,label,numeric,render}],rows,rowKey,selected,onSelect,expandable}`; sticky headers, 44px rows, keyboard row navigation and optional expansion |

The inspector occupies 400px at ≥1200px, overlays from the right at 800–1199px,
and becomes a full-screen sheet with Back below 800px. Overlay mode traps focus
and makes background content inert. `returnFocus` accepts an element or a callback
resolved at close. It focuses a connected, rendered target outside hidden/inert
ancestors, falling back to the creation-time opener under the same checks.

Context chips keep a 24px visual height, one ellipsized line, a full tooltip and
accessible label. Interactive chips use an invisible 44px touch area without moving
neighbours. Discussion chips resolve every launch kind through central reads. Saved context uses the same display resolver and its authorized labels. URLs retain only context identities; their title never labels a chip. Missing sources show Kind · Unavailable and block a draft Send. Governance records use their own type, and plans include the title and revision number; IDs remain in Details.

All `details > summary` elements, including retained legacy forms, share the same
chevron, 13px/500 label and 90-degree open rotation through `ui/components.css`.
Native summary markers are hidden without rebuilding their contents.

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
Row layouts must hold with wider fallback fonts (DejaVu Sans, Segoe UI, Microsoft
JhengHei); flexible text ellipsizes with its full accessible name. Time assertions
pin a timezone.
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

Secondary tools use collapsed disclosures or the view's tool drawer. They keep their
original forms and refresh/withdrawal behavior. Do not rebuild contents to move sections.

Durable PM forms register only while connected. A hidden autonomous child tracks
connection without changing the ordinary form's layout, selectors or hidden state.
Detachment releases the registry entry, receipt subscription and polling timer;
reattachment immediately derives the current workspace, lifecycle, scope and action
gates, including forms rebuilt when a stale hidden view is reopened. Discarded forms
receive no further registry updates or state callbacks. A gate that
closes during submission or receipt checking shows an inline unavailable status;
the draft, original request and recovery key remain intact. Submit controls follow
the shared transport send gate. An unknown admission waits for sending to resume
before an explicit retry with its original key; an acknowledged operation ID
remains recoverable by GET without another POST. Reconnection never reposts an
unknown admission automatically.

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

Pending, Operations, Delivery, Settings, Projects, cleanup, artifact review and the signed-out card share one page header (`.ui-page-header`): header actions centre on the title row, with 16px before the next control. A meta line under the title does not move the actions. Below 800px actions occupy their own wrapping row under the full-width title and metadata. Titles clamp to two lines with the complete title in the tooltip and accessible name; they wrap at words and use pretty text wrapping. Actions retain their intrinsic width so labels stay on one line. Marks beside the title stay on the title line as well; the title shrinks and wraps inside that line instead of letting the marks wrap underneath it. Pending is an inbox hosted like Operations: the groups and the Details disclosure stay in one column, and the inspector is the column on the right at 1200px and up (the same sheet below that). The Needs you / Active operations / Unread switch is the shared segmented control and keeps focus on the checked option after the view rebuilds. Each group is one surface list: a title and count, hairline rows, and one secondary action. The row selects; the inspector is what opens the next page, and its first action is that row’s Answer, Review or Open link. When the session read already includes the pending question, that question is the first paragraph. A manual or read-only session shows Open and the Read-only pill, never Answer or Review. The row title is not underlined until hover or focus. The status pill sits beside the meta, not at the far edge. A chevron opens the inspector only when that is different from the row action. Needs attention (unknown outcomes, not failures) offers a quiet View all link in the group header. “N shown · Load more” appears only while another page exists. A group hides only after a fresh, successful, empty load. A failed read keeps earlier rows, shows no count, and stays marked Not updated · Retry. When every group fails in the same refresh, one alert and one Retry sit at the top instead of a bar on each group. An error does not turn the Pending badge into zero. Each tab has its own empty state and at most one link: Approvals only when there is something to approve and the inbox is empty, otherwise inside that empty state. Explanations sit once in a Details disclosure after the groups and name only the tabs that are offered. Operations is a dense table (action, target, status, actor, created, duration). Duration is the elapsed time for an active operation and `updated_at − created_at` once it is terminal, written as seconds, minutes, hours and minutes, or days and hours, with a zero remainder omitted. The shared segmented filter and the shared search field stay. Selecting a row opens an inspector whose first text is the localized status and whose first action is Open operation. The meta line is only the target. Actor, Created, Duration and Attempts sit above Details, and a server reason is a Reason row inside Details. Refresh stays disabled while a load is in progress. In the browser, the settings sub-nav (Connection, Desktop, Models, Fleet, Updates, About) shows only the selected section, remembers that section for the namespace, and stacks under 800px. Native settings keep mounted sections visible together and use links with `aria-current`, not tabs. Forms stay within 640px. Connection shows the endpoint, a live state pill, the live last-event time, access as Access · actor · scopes, and a secondary Disconnect. In the browser, the Tailscale note is one muted line inside that card, with no controls and no local calls. Fleet without configuration uses the shared empty state. About shows the product name, the version when one is known, and the setup guide. The signed-out screen is one centred card: the product mark, one sentence, the connect form with the setup guide on the connect row, and the same Tailscale note in the card footer. A rejected token says Token not accepted / 權杖無效 in an alert, with no raw status code. Sidebar names keep the row width minus a 16px mark: circle-alert for Needs you and Asking you, circle-x for Failed, an info dot for Running, and no mark for neutral states. The mark follows the work entry’s own status before the session’s activity. A numeric unread count sits after the name when the entry has one. The status word stays in the tooltip and accessible name. The Pending count badge stays. At 800px and below, row actions, row chevrons and View all keep a 44px hit area. Composed metadata uses `label-text.js`, so a missing target does not leave a separator. Short status words come from the shared vocabulary; precise wording and raw codes stay in Details. Registered operation names have an English and Traditional Chinese label; an unknown name is humanised rather than called Operation.

## Shared actions and links

Discuss is one shared anchor built with `discussHref`: secondary small button,
message icon and Discuss / 討論. Inspector action rows put it first. Schedule retains
its distinct Propose change action. Links have no underline at rest and underline
on hover or visible focus; back links include a left chevron. Meta lines sit directly
under the title with the shared gap. Pills never receive textual separators.

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
columns answer goal, accepted requirements, blockers and open decisions. The live
Goal card shows only the goal or its existing unknown/unconfirmed state; the header
supplies plan title and adoption date. Saved reports retain their fixed baseline
provenance. Not-started requirements leave empty progress track in both themes;
accepted, review, in-progress and blocked shares keep their true proportions. Null remains a
dash, incomplete classification keeps only its known denominator with Partial, and
complete coverage describes the scan rather than project completion. Unknown
reasons share the localized reason map; omitted lifecycle operations appear only
in Details. Delivery evidence keeps accepted, merged, deployed and rolled-back
states separate.

Empty milestone and delivery lists say None recorded / 尚無紀錄 only with complete
roadmap or recent-delivery coverage, including any delivery output-reference limit.
Otherwise they show Unknown and the recorded localized reason. Empty charter cards
require a readable complete plan (or an unrestricted complete saved report); open
decisions require a complete known zero.
Missing coverage never establishes absence.

The Open decisions card (待決事項) retains the central pending count and adds Recently decided:
up to five visible completed decisions, with their title, relative time and actor.
Each row opens the exact Issues & changes register inspector. Missing history is
Unknown; incomplete or capped history is Partial and describes only loaded rows.
It never substitutes the history total for the pending count. Saved reports do
not borrow live decision history.

Completed decisions use past-event ages: just now / 剛剛 below one minute, then
5m ago / 5 分鐘前, 3h ago / 3 小時前, or 2d ago / 2 天前. Attention retains compact
waiting ages, with <1m / 不到 1 分鐘 below a minute. Live projection refreshes
update both kinds of cached ages without replacing their rows.

An adopted discussion with an open sourced issue offers one secondary Resolve
issue action to its assigned writer or explicit workspace owner, with the action's
observe/manage scopes. The durable form prefills an editable rationale naming the
fixed adopted plan title and revision number. It preserves the original finding,
affected IDs, sources and version precondition; it creates no snapshot. Original-key
recovery settles the receipt before rereading the finding and Overview. Reported
resolved remains Not assessed; adoption alone never resolves an issue.

Selecting a governance attention item or completed plan decision opens its exact Issues & changes inspector,
where Discuss retains the record identity and title. Without the register capability,
the existing governance tool remains the destination. The destination list settles
before selection; withdrawn or late record reads cannot reopen an inspector.

Attention retains the central severity/age ordering, with exact context identities
passed to `discussHref`. A maintenance finding shows the shared finding name
instead of the provider number, and a severity word when `finding.severity` is
present. Its Discuss link uses that same name. A server that omits `finding`
keeps the provider number and does not invent a severity word. Reading rows keep their DOM node and focus during refresh; replaced links recover focus
by identity within their own part, including the Progress Details summary and its open state.
Before updating any Overview region, a focused or hovered attention row anchors
the viewport. Next in priority is the row containing a non-collapsed selection's
focus node, falling back to its anchor node. Without interaction, the first
visible row anchors only after the list's top has scrolled above its scroller's
visible area; while the list's top is in view, new rows push the rest down without
compensating scroll. Focus, hover or a non-collapsed text selection
inside the attention list preserves its existing order and appends new rows. Passive
visibility uses Central's severity/age order; returning to Overview re-sorts even
unchanged data. Overview compensates its actual scrolling container after layout,
with native CSS anchoring disabled so Chromium and WebKit have the same behavior.
A withdrawn anchor is removed immediately
and receives no scroll compensation. Cached ages advance with the projection timestamp.
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
the saved history arrows remain together. Attention rows keep Discuss beside the title at every width. Details lists localized source coverage, with raw JSON
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
A finding chip uses the shared finding name when `finding` is present, including
the composer's pre-bind label from the finding's native kind, number and proven
facts. A withdrawn finding chip stays unavailable and shows none of that identity.
A server that omits `finding` keeps Kind · stored label.
Chips stay on one line with full accessible labels: up to three in the header, then
+N opens Context; one per list row. The selected row has the table accent inset.
The composer has one status/actions row with Send and Ask PM kept together. Failed
and pending note explanations stay visible. Search shares the list header with an
icon-only New discussion button. A list-opened thread returns to Discussions.

Saved context disclosure captions, compact ages and analysis status share one text flow
so the side panel wraps words instead of squeezing metadata into separate flex columns.
The shared `/discussions/new` link opens an editable draft and focuses the composer.
Before a central thread exists, it mounts only the local discussion draft and
list, deferring meetings, follow-ups, evidence and specification tools until Send.
Its title can be renamed locally once the initial permission read completes; title
controls follow the durable create form's gates and lock with its original intent.
The draft title survives reload and becomes the central thread-create title.
Governance context chips in an unsent draft read the current record title from
central. A 403/404 removes the source label and ID and disables sending while
retaining the local draft; a superseded read cannot restore withdrawn context.
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
Successful receipt recovery refreshes this same central adopted view; a local
success message must not replace its accepted-revision link.
On mobile, that link focuses and reveals the accepted revision after the Plan
sheet establishes its focus boundary.
The clock runs only during actual operation checks. Exhausted polling shows Status
not checked; refresh and PM events re-read the known original operation ID and reset
the backoff. Only label changes update the live region; elapsed time is hidden from
announcements. Original-key recovery is retained.
Receipt checks include the central `waiting_external` and `waiting_checks` states;
these keep the bounded backoff and read only the acknowledged operation ID.
Saved proposals, runtime options, planning inputs, immutable revisions,
context packages, meetings, follow-ups and Brief remain in the thread panel.
Each proposal refresh reads one list and its selected document; picker titles and
status come directly from list rows. Proposal titles replace raw IDs in the picker;
plan name, revision and status replace
commit digests in the reading surface. Identifiers and copyable digests are in Details.
All thread tools use shared chevron disclosures, including retained lazy controls.
Thread disclosures have connection/principal/workspace/thread keys and keep the
reader’s choices across live reads and Refresh. A pending original request opens
Inputs once when the thread is first shown, including its drawer/sheet after reload, and later
reads never re-expand a reader-closed disclosure.
The Plan tab lists revision rows with centrally read Accepted / Draft / Historical
pills and a Compare action; revision detail reads wait until Plan opens. Meetings
and Follow-up read only while their Context disclosure is open or on their own
events. The Brief uses the same borderless presentation as these sibling sections;
explanatory planning copy is inside Details.

## Requirements

Requirements is a delivery truth table: Requirement, Milestone, Work, PR,
Acceptance, Deploy and Next blocker. Its shared table chevrons reveal criteria,
explicitly bound work, agent marks, PR receipts and visible attempt counts.
Milestone/phase grouping does not infer missing associations. Unassigned work and
issue mappings remain in a final disclosure. Search includes exact issue numbers
and omits missing label fragments. Header totals require complete catalogues;
partial reads say “loaded”. Content approval, merge, deployment and requirement
acceptance are separate states. Acceptance is Accepted when an exact qualifying review
exists, Not assessed only after a complete unfiltered quality catalogue, and Unknown
otherwise. Missing delivery capability or failed reads stays Unknown; paged or filtered
receipts stay Partial in rows, compact summaries, inspectors and Board chips.
An unknown blocker is never counted as zero.

Below 800px the shared table's optional `compactRow` renders each requirement as
one expandable summary target: two-line title, labelled acceptance/deployment pills with gaps,
work/PR counts and milestone, followed only by recorded blockers. Enter/Space or a
tap toggles it; Left/Right collapse/expand. The expanded content retains criteria,
stacked work rows and an Open requirement action. Desktop selection stays unchanged.
With the inspector docked, Milestone and Next blocker move out of the table; both
remain in the inspector. Explicit column tracks reclaim that width, milestones
ellipsis with full titles, PR pills wrap, and Work remains numeric and right-aligned.

`pm-work-inspector.js` supplies both views (and the future Schedule caller) with
six ordered work sections: Requirements served; Status & ownership; Latest result;
Blocker & next step; Delivery; Details. Discuss uses exact context objects through
`discussHref`. Managed and manual session links use central session identity and
access metadata; manual sessions carry Read-only. Missing attempt start/end times
and CI results remain unknown; observation or send-acceptance time does not imply
execution duration. Technical records and source times appear in Details. A requirement instead shows
Criteria, Bound work, Latest result, Decisions and changes, Delivery, then Details.
Criterion results use the exact current plan, digest, scope and input identity; a recorded
rejection stays visible even when no review qualifies the overall requirement as Accepted.
Auxiliary source failures clear only their own facts and offer Retry; plan and work
catalogue withdrawal clears the view. Inspector actions retain their nodes across live
age updates, and closing returns to the current selection.
Milestone destinations apply once for the current route. Live refresh retains the
reader's search and restores group-heading focus by central milestone identity.
An authorized unassigned-work read can resolve a work destination without an adopted plan.

Existing quality, evidence, specs, package, handoff and publication controls move
intact into drawers reached from the Requirements overflow and work inspector;
Board's overflow opens Batches. Drafts, original request keys and recovery controls
remain owned by their existing modules. Both work inspectors expose the same
Requirements tools; Batches stays in Board's header overflow. The shell provides the single refresh.
`requirements?select=<work_item_id|requirement_id>` expands and scrolls to the
selection, clearing a previous search when following an explicit selection link.
Overview and Issues links preserve the exact revision, expand the destination row with
`aria-current`, and focus its visible target. Work links focus the shared sheet title
on narrow screens. Explicit destinations cancel queued scroll restoration; revisiting
a view restores its own scroll. Unassigned work remains selectable before plan adoption. Preferences are bounded by connection/principal and workspace; fetched
facts are reauthorized, never stored in the preference map.

The package entry preserves the existing read-only catalogue. This foundation has
no package creation editor; creation remains the registered `pm.work_package.create`
API action. Adding that editor requires the fixed source/admission workflow rather
than an inferred package assembled from a selected row.

## Schedule

Schedule uses the central `pm_schedule` projection. Day / Week / Month zoom, Today,
and a shared legend sit above a sticky tree and time axis. The tree follows explicit
`parent_id` relationships; there are no phase rows. Undated ancestors retain the tree
structure. Unscheduled lists only loaded rows that are not already in that tree, and
the disclosure is omitted when none remain. Requirement rows do not show a status pill.
Baseline is dashed, forecast outlined with a solid stroke, and actual filled. An actual
the projection marks open has a short fading marker labelled Open, never a finish at
today. A terminal attempt with no recorded end says End time not recorded and is not
drawn as open. Waiting and blocked stay visible on an open bar. Only a `blocked`
status produces hatching. Milestone delta compares recorded dates. Recorded baseline,
forecast and actual dates stay visible in the inspector. Week ticks label UTC
Mondays only; a range that starts mid-week leaves that partial interval unlabelled.
Month ticks label the 1st only. Day, Week and Month then drop a label whose
left edge falls before the start of the track, or that would sit closer than its
own width to the next one. Both decisions use the track's own coordinates, so
scrolling the board does not remove labels. The Today mark sits under the axis text. Month ticks show only
the localized short month; Day and Week retain month and day. Scale labels use the
shared time module without becoming event timestamps. Bar tooltips use the same
calendar-date formatter as visible dates. Operation attempts use the shared action
labels in the tree, inspector and tooltips; their raw action id appears only as code
in Details.
The grid fills the available chart width while retaining its date scale. Bar and
milestone-delta text has an opaque row-colored background above the Today line in
both themes; selected rows keep their selection color beneath the labels.
Each track isolates that paint order beneath the sticky title column during scrolling.

Reads follow opaque cursors sequentially, at most ten pages per refresh. The chart
range uses all loaded rows; any partial/truncated page keeps a Partial pill and a
reason in Details. Unknown reasons use the shared reason vocabulary with a neutral
fallback. Complete reads show exact counts; partial reads show loaded counts only. Source withdrawal clears the chart,
counts and inspector together and invalidates pending reads.

The inspector keeps the Requirements section order and a narrow
`pm-schedule-inspector.render(row, context)` interface for shared-inspector integration.
Session attempts have no dates or bars; their read-only session links and attempts
remain available in the inspector and Unscheduled. Propose change uses `discussHref`
with the exact context identity and a title of Change: {first line}. A milestone ref
is sent only with the baseline revision that milestone belongs to. Missing result and
delivery evidence stays unavailable. Roadmap and Sprints keep their original durable
forms in the header overflow drawer. A live refresh does not open the schedule
inspector beside that drawer. Zoom, selection, expansion and scroll preferences are
bounded and connection/workspace keyed. A hidden Schedule does not fetch until the
view is active; live updates keep focus in Unscheduled and Details. A response that
exceeds the size limit says so. The schedule region does not nest a second vertical
scroller. Below 800px, rows become a list: title, recorded dates and status stay in
the row, and each bar track uses one shared date range fitted to the row width. A
caption names that plotted range (for example Sep 25 – Nov 7) and each track keeps the Today
line. Bar labels and milestone-delta text are not drawn on the tracks; Open and the
recorded delta stay in the row. Day zoom is not offered at that width; a saved Day
choice returns above 800px. Week and Month remain. Nothing in a row is clipped, and
the document does not scroll horizontally.

## Dependencies

The relation form uses a bounded two-column grid, one column on phones, 44px touch
controls and labelled source commits. Graph fills the available layer width and
keeps its legend beside 32px zoom controls (44px on touch). Active chips use the
existing primary text token for contrast in both themes; selected matrix cells
centre their marks with a separate inset selection ring.

Distinct edge and declaration IDs retain their evidence even when they share a
component, dependency and relation. Failed reads hide both view hosts as well as
their rows, forms and inspector; late impact reads cannot restore withdrawn data.

Matrix and list selection buttons use pressed state. Graph nodes do the same, with
a distinct selected fill. Home/End follow the graph's visual column/row order.
Activation, live reads and inspector close resolve focus by stable item key in the
currently visible matrix, list or graph; inspectors receive a return-focus callback.

The question on this view is which checks have to be run again if one dependency changes.
It reads the current dependency projection, its revisions, and one impact query. Capture,
declare, and retract stay the durable dependency actions, using the projection version,
the workspace version, and the source binding digest. Capture again is the header action.
It is primary only when this member can run it. Otherwise it is a secondary button with the blocking reason below the header,
where it wraps without displacing Refresh. Maintenance policy, release baselines, maintenance checks, and
workload observations stay in the secondary cards.

The header names the capture commit and its UTC date. A Stale pill is shown only when
the projection says `stale` is true. Unknown freshness stays out of the header and is
stated once under Details. A Partial pill and the omitted total describe the returned
capture, not a repository-wide total. Relation/evidence counts include captured edges and separate owner declarations;
they are omitted without a capture or a count object. An uncaptured workspace has
no Partial pill or freshness claim. Details includes parser/catalog reasons, omitted
collections, cross-repository selection gaps and unreadable parent paths.

Matrix and narrow list start with twelve loaded components. Show all (M) expands
those rows. Matrix columns group by kind and start with at most twelve dependencies,
covering each shown component before filling the budget; All columns (N) reveals the
rest. These controls describe direct columns, not transitive impact. View limits say
how many of the loaded components/dependencies are shown and offer search/filtering.
Hidden desktop lists are not populated. Graph uses the same relation, evidence and
search predicates for local and cross-repository links; external nodes appear only
with matching links. It starts with the same row prefix and always caps drawing at
sixty nodes, retaining the focused key where present. Its limit describes shown nodes
out of matching loaded nodes, never a complete graph total. Relation/evidence legends
are visible in both matrix and graph, with an entry for a component from another
repository when a matching cross-repository link is shown. That component uses the
review tokens and names its repository, or “Other repository” when the projection
has no repository name. Matrix column headers and narrow list rows show the same
peer and do not count the link as another dependency. Selecting a node keeps the
nodes on its own edges at full strength and dims every other node and edge the
same way. Keyboard focus alone does not change that treatment, and dimmed labels
stay readable.

The inspector title is the dependency or component name. A loaded or partial count
stays in Affected components. The version field is labelled by kind: a contract or
API schema is a contract version, a reference is a reference version, a service is
a service version, and a library or toolchain requirement stays an allowed range.
A bare number is never shown without that label. The inspector shows a pinned
version only when this capture resolved one, plus that version field, the evidence
location, and the maintenance owner or the recorded reason.
Component selections show component facts and a Dependencies list whose rows name
the dependency, relation, version, evidence and joined source references. Tests to
re-run come from the capture's verification hints. When some are listed and the
capture omitted others, the list ends with “More were omitted.” When none are
listed and omitted hints or a dropped hint collection mean more may exist, it says
“None listed in this capture. More may exist.” A complete capture with no matching
hint says none were recorded. Affected components come
from the impact read; owner declarations are listed separately from captured paths. Bounded/partial impact has a Partial pill and one sentence that combines the
loaded-graph, peer-selection and manifest reasons. Each machine reason stays in a
disclosure. Positive counts describe returned components only; an empty
bounded prefix never says “0 loaded”. Declared-only reads list the declarations. An impact
miss says why the dependency is outside this projection, which is not the same as no
impact. Discuss with PM opens a discussion for dependency and component selections without
a context ref (the backend has no dependency context kind). Its title names the item
and captured commit. Declare and retract stay owner actions in a
disclosure on the main page, never reparented into an inspector. Inferred drafts
restore all five translated relations. The source commit and declaration select
have labels; retract is secondary. Projection refresh rereads an open impact and
retains focus on its action. Failed reads remove options, disable mutation forms,
and hide the toolbar, Details and record disclosure with the rows and inspector;
drafts and original operation keys remain intact. Details lists every manifest and unreadable path. Unknown reason codes use
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
to the view reserves the side panel immediately, then reads that row into its
loading body. The table must not first expose related chips and hide them when
the delayed inspector arrives. Below 1200px a refresh
or a return keeps the selection and leaves the sheet closed until the row is
opened again, and a return moves focus to that row. A detail read in that case
still drops the row when it is gone, without opening the sheet. Leaving the view cancels register reads that are
still in flight and closes the inspector.
Search and owner accept at most 200 characters.

The table shows the type word; Source stays in the inspector facts. A maintenance
finding with no title is named from its structured `finding` when the server sends
one: a GitHub issue or Dependabot pull request uses its kind and number, and a
security alert uses its package, or its number when the package is absent. The
same name is used in the list, the table and the inspector title. A server that
omits `finding` keeps the older register name: the finding kind plus its impact
or priority. The inspector adds only identity facts that are present — severity
with its word and colour, package, ecosystem, labels and manifests — and puts
the GHSA ID in Details. Absent identity facts are not filled in as “Not recorded”;
`finding.*` unknowns stay out of that list. When the row is named from `finding`,
Details also leaves out the missing-title line; a fallback name still lists it.
A neutral type basis says the record has no type of its own, so the register
lists it as an issue. Long owner text
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
quota windows. Missing counters stay unknown. Source failures withdraw numbers, chart
contributions and model details together. The source filter sits with the list and
narrows that list only; metrics, the chart and quota keep describing every row.
Rows, filters, inspector titles and Discuss drafts use the CLI's human name and
translated role. An inaccessible or CLI-less source uses its translated role;
missing identity uses Unnamed source. The source ID stays in inspector Details as
mono. Missing models, token parts and counters use the shared Unknown pill;
observed subtotals keep Partial and an unobserved total stays an em dash.
Below 800px each source is a tabbable button in a list, marked with `aria-current`.
The toolbar can pause view updates without stopping collection. While paused the meta
line shows the absolute collection time ("Paused · data from 5:00 PM") and the button
label changes between "Pause updates" and "Resume updates", with no pressed state.
Events do not refresh or repaint a paused view.

The chart is drawn only when `features.pm_resources.buckets.granularity` is `day`.
Otherwise the read sends neither `buckets` nor `tz`. A supported read uses one window
for every period: `since` is local midnight of today − N + 1 in the request zone, and
`until` is now, so "1d" is today only. A rejected zone is retried once as UTC. If both
attempts are rejected, later reads omit buckets until the view is opened again. The
plot is a summary image. Exact values is the keyboard path to each day's numbers; the
bars themselves are not focused. Date labels use the same local day format as the meta
line in that table, and a short month/day form on the axis. Axis labels keep a measured gap
when localized text or a narrow plot needs fewer ticks; every day remains in the
bars, tooltips and exact-values table. The y-axis is one step
above the peak (1, 2, 2.5 or 5 × 10ⁿ) in compact numerals. Open days keep full colour,
with a hatch and the in-progress or settling marker. An incomplete day shows its observed tokens; null metrics stay unknown. A day that starts before the
window is "Partial day", never Settling. A null total is a dashed stub in that CLI's
stack position. A covered zero is a baseline tick.

The heading, usage metric, rows, inspector and chart show one consistent read.
A cached trend is reusable only for the same since instant, until day, zone and
period, when each readable source has the same fingerprint as in the bucket read:
source ID, status, observation count, total value/observed/covered and last sample
time. Idle row totals stay unknown even when complete days have covered zeros;
partial row subtotals can include samples in a day whose total is unknown. These
differences alone do not invalidate the cache. A totals-only read that differs
requests buckets immediately and keeps the previous consistent pair until they
arrive, without flashing the chart away. Source withdrawal still removes that
source's numbers, chart contributions and inspector facts immediately. The
heading uses the window and request zone of the read being shown.

A tick asks for buckets again when the capability is on and the trend is missing,
the readable row fingerprints or window changed, or the trend is older than
15 minutes. Until that read returns, the chart draws only series that are still readable.
A tick waits out a user-initiated read. The Partial pill follows
unreadable, pending, catching-up, stale or binding-changed rows, and totals that are
null or observed-only. Today's open bar does not turn the pill on by itself. Quota is
one block per pool and window, titled by the CLI or source and the window length, as
used percent, and is never added across pools. A whole number of days reads as an
n-day window. The pool id stays in that pool's details. An unconfirmed pool has no
percent. The page-header refresh is the only reload control. A 403 or 404 withdraws
rows, counts, the chart and the inspector together.


## Board

Board uses neutral lanes and compact cards with explicit requirement/session
associations. Search, requirement/agent filters, grouping and collapsed lanes are
view preferences. Below 800px lanes stack; wider boards scroll horizontally inside
the board. Arrow keys navigate cards and lanes; Enter opens the inspector. Live
updates defer lane movement while that lane is hovered or focused, but withdrawal
removes inaccessible facts immediately. Local transitions are 140ms and respect
reduced motion.

Card requirement chips are single-line with full titles. Unknown agents and ages
leave no placeholder. One metadata row holds known agent marks, PR chips, blocker
flags and a right-aligned age; the status pill appears only outside status grouping.
PR chip titles retain merge evidence, with full receipts in the inspector. A subtle
edge fade signals clipped desktop lanes. Opening a card or navigating with arrows
scrolls it into view inside the board. Inspector facts state unknown/absent values
once; a recorded blocker retains both its status and reason.

Each lane is a list with an H2 heading. Cards are list items with an H3 title
button, described by their requirement, status and recorded delivery/blocker/age
facts. Visually hidden status text stays inside the card’s bounds; blocker flags are not tab stops. Paged lanes show loaded counts and one
visible partial marker; an empty Board hides the lanes and shows one empty state.
Opening the inspector reveals the selected card after docking. Closing restores
focus to the latest selection, falling back to the view heading after withdrawal.
An unchanged read failure keeps its status, Details and Retry nodes, including
keyboard focus, while another read is pending and when the same error returns.
Initial activation and the workspace load share that first projection read;
later events still start their own reads and invalidate older responses.
Selections use `?select=` in both views and reopen after returning from another
view. Current requirement chips link to Requirements; earlier-plan text is read
from its bound revision and never replaced by a raw ID in a chip or filter.

## Session workspace

Opened from Work or a project tree. The page header shows the session title and agent
mark, one status pill, and one quiet meta line: project, host, and a compact observed
time (relative within a day, otherwise a short local time with no seconds). Send stays
the only primary action, on the composer's queue row. Interrupt is a danger button.
Discuss, copy title, copy ID, Open in BAT, and the send shortcut sit in the overflow
menu. Below 600px the session caption and metadata keep the full header width, with
actions on the following row. An absent title says Untitled session, and an
unreported agent uses Unknown.
A manual or unconfirmed session shows Read-only and has no send, answer, or
interrupt control.
On mobile, the Session title and metadata use the full header width, with actions
on the next row. Wider fonts must not turn the title into a tall narrow column
that squeezes the pending question scroller below a whole answer input.

The conversation uses one grid for every message: a 24px mark, an 8px gap, and one
body column for the name, compact time, chip, text, and disclosure. Agent marks use the same vocabulary as the header; tools and system rows use icons.
The model chip adds only the model when the name already identifies the agent.
Manual-session user turns say Person. Durations use seconds or minutes. Copy original is an
icon at the row's right edge. The window caption counts received messages and says Latest only for a partial window.
An empty transcript uses the shared empty state; an empty toolbar takes no space. A tool event is one row
(name, compact time, status pill) and its command is a disclosure. New messages
while the reader is scrolled up show New messages and do not move the visible row.
Scrolling up without new content shows Back to latest. Activating either restores
transcript focus. A pinned reader follows layout changes and a successful own send.
Tool disclosures preserve the reader's toggle until the tool changes state.
Approval prompts sit above the composer and keep their original operation keys.
Their preview is a wrapping block, separate from transcript rows. The composer
occupies at most half the viewport; questions scroll inside it while Answer and
Send remain reachable. On mobile, a pending prompt uses a compact reply field so
the question scroller can show a whole answer input. Without a pending prompt, the
reply composer keeps its natural height so Send stays visible before input focus.
The transcript has a 160px minimum and the main Session region scrolls as a safety
net. A manual session has one plain read-only line. The
last send is one short line; the operation id stays in Details. On a narrow window
the info and composer collapse icons sit on the caption row and the queue row.

Results stay in the docked inspector at 1200px and wider, and in a drawer or sheet
below that. Changes (only when it has its own content), Checkpoints, Delivery, and
Result sources sit in that one panel, separated by hairlines, with one title style.
Checkpoints say when this host cannot record a version, and keep the longer note in
Details. Permissions, labels, and observation history use the same disclosure. Labels
show one state line; the reason, and Edit labels when saving is possible, stay in
Details. A linked work item reads Work · title. Delivery pills come only from saved
receipts, and pull-request links use the normal link. Identifiers and raw timestamps
stay in Details. From a project view, the shell breadcrumb is Workspace / Work /
Session and the page offers Back to that view. Discuss uses the shared discussion
link, binding only exact work item refs.

搜尋欄統一使用 `searchField()`，保留輸入節點與草稿，32px 高、前置搜尋圖示，提示為 Search／搜尋。工具列篩選器使用相同 32px 邊框樣式，空間不足時在自己的欄內換行。`segmented()` 以共享 ResizeObserver 測量容器與選項寬度；不足時改為保留相同選項與狀態的 select，寬度恢復後還原按鈕與鍵盤焦點。

共享 `dataTable()` 以 `secondary: true` 標記次要欄。`ui-table` 容器小於 900px 時收起，詳細資料仍保留在 inspector；標題與識別碼不在字中斷行，完整欄位內容置於 title。Operations 在 800px 以下改為單一互動列。全寬 sheet 不留左側邊線，rail 背景延伸至文件底，頁尾目的地以 aria-current 顯示目前頁面。

事件頁的多筆通知共用尚未開始的讀取；一旦開始便釋放共用位置，較晚事件仍需自己的讀取，ACK 仍等候結果。`cross-view-events.spec.ts` 用 20 筆混合事件固定請求預算：PM 一般頁 28、Requirements 48、Session 24、Pending 18、Delivery 12、Operations／Settings／Projects 8，另驗證最後事件後有讀取且不重載 workspace／thread。Projects 頁和 rail 各自讀一次專案清單。尚未登入的首次設定頁不訂閱事件。

空白區域使用 `emptyState()`（24px 圖示、15px／600 標題、至多一句說明及一個動作）；讀取中使用 `skeleton()`，特定狀態名稱放於 aria-label，保留原有狀態及錯誤容器。完整清單顯示精確數量，只有部分清單使用 loaded／shown；Schedule、Sessions 與導覽計數明確區分兩者。Operations 與工作協調的步驟及子操作回執使用共享操作狀態翻譯，步驟名稱與錯誤碼維持原始識別碼。

Session inspector subsections use level-three headings under Results; there is
one Messages landmark. Label details, Send record and Session details have distinct
disclosure names. Session drawers inherit the shared 400px width, and mobile sheets
fill the viewport.

A session GET returning 403, 404 or 410 withdraws the whole Session workspace:
clear transcript, title facts, Delivery and inspector, hide the composer, preserve
the stored draft, and show one inline state with Back. Pending reads cannot repaint.
Withdrawal stops timers; an explicit later session event may establish new evidence.
Transient read failures use exponential retries from 1 to 30 seconds; replayed events
obey the same delay. Message failures show a transcript state and Retry; oversized
windows retry once with ten messages. Known error codes have localized recovery
actions, with the code retained only in Error details.

Session Delivery reuses the work inspector's contract validation and PR grouping.
Keep validated rows during reads, replace only changed content, show Unknown on
a first transient failure and Not updated with Retry on a later one. A source
withdrawal removes cached receipts and their derived counts. A partial window states
the number shown and more; filtered sources stay Partial. Provider origins determine
PR links; merging, verified current deployment and acceptance remain separate.

Checkpoints keep the commit picker closed until Record this version is requested.
Start from here precedes its form; the confinement caveat is in Execution limits.
Checkpoint notes report missing host capability only when absent, and exact config
keys remain in Details. Continue shows the central operation state and, when the
result identifies a new session, Open the new session. The operation ID is in
Operation details. Checkpoint disclosure state includes the central namespace.

Session return navigation is cosmetic and includes a validated from query. The
history entry retains the workspace ID only within the exact central namespace
and session identity, so a reload can return without treating a name or path as a
binding. Discuss accepts a bounded Session hash as a return origin and returns to
that session; Requirements/Board and Schedule session links carry their origin.
The session-local navigation helper performs no writes or authority inference.

Inspector Escape ignores editable fields outside the inspector. Table expanders stay outside the Tab order; rows expand with Left/Right. Mobile Session controls use distinct Info and Compose icons. The unsized rail adapts to 232px at 1000–1199px; a saved user resize remains explicit. Deep links derive their initial breadcrumb from the route.

Requirements uses its own table width: below 1100px it hides Milestone and Next blocker; below 720px it uses the same compact row interaction as mobile. The title track has a 14rem minimum whenever columns are visible.

A successful Session send uses the central operation updated_at in the shared time ticker. A receipt without that timestamp says Sent without fabricating an instant.

The rail refreshes the project catalog only for project, workspace and binding events. Other resource events refresh expanded work and invalidate cached activity counts without refetching the catalog. `visible-view-events.spec.ts` covers both 10- and 20-event PM/live pages: Overview 4, Discussions 4, Requirements 24, Board 22, Schedule 3, Dependencies 4, Issues 4 and Agents 8 GETs, including the rail. A later event page must start another read. Hidden withdrawal is tested with a held forbidden response, before any old card can be painted.

A PM event page still revalidates the selected workspace's parent authorization.
A failed parent read hides every source projection, closes source inspectors, and
masks hidden stale views before navigation can expose them. Recovery rereads the
visible view and restores an open tool with its unsent draft. An Overview related
inspector keeps its owning Overview projection subscribed while it is mounted in
Issues. Closed discussion tools wait for their next visible disclosure.

Agents event reads respect Pause updates. A later event can supersede an older
in-flight source read while updates are running; only explicit Refresh bypasses
the pause. Parent authorization remains checked while the projection is paused.

Lifecycle and delivery detail summaries use the shared action and operation-status
labels. PM writer states reuse nativeRunState; step names remain identifiers.
