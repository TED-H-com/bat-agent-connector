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
| `--accent`, `--accent-hover`, `--accent-soft`, `--accent-text` | Action, hover, selection, action ink |
| `--ok`, `--warn`, `--bad`, `--info`, `--review`, `--neutral` | Evidence states; each has a `-soft` background |
| `--space-1/2/3/4/5/6/8/10/12` | 4, 8, 12, 16, 20, 24, 32, 40, 48 px |
| `--r-xs/sm/md/lg/pill` | 4, 6, 8, 10, 999 px |
| `--shadow-overlay` | Menus, drawers and dialogs only |

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
| `inspector` | `{title,onClose}`; append returned node, then append content to `.body`; `.close()`/`.dispose()` release listeners and restore focus |
| `dataTable` | `{columns:[{key,label,numeric,render}],rows,rowKey,selected,onSelect,expandable}`; sticky headers, 44px rows, keyboard row navigation and optional expansion |

The inspector occupies 400px at ≥1200px, overlays from the right at 800–1199px,
and becomes a full-screen sheet with Back below 800px. Overlay mode traps focus
and makes background content inert. Escape returns focus to the opener.

Canonical evidence labels: Not started, In progress, Waiting, In review, Accepted,
Blocked, Done, Merged, Deployed, Unknown, Not assessed, Partial, Stale, Read-only,
Draft, Adopted, Historical. Done describes content only; it does not mean delivered.

## Shell and information architecture

The topbar is 48px: product mark, workspace switcher and breadcrumb; Pending,
connection state and Settings on the right. Connection details live in the tooltip;
the control opens Connection. Pending counts reuse loaded attention data, with a
partial marker, and remain absent when unknown. Mobile shows navigation, workspace
and a named connection dot.

One persistent sidebar is 248px (232px at 1000–1199px), with its own scroll and an
optional saved resize preference. Below 1000px it is a drawer with scrim, focus trap
and Escape/focus return. The sidebar contains workspace name/central phase, Project
views, explicitly bound Work, and Pending/Tools/Settings. Capability-absent views
are omitted. With no selected PM workspace, the existing global tree remains.

Work uses central workspace/project bindings, never matching names, paths or repos.
A bound session opens the existing work/session view without changing the selected
PM workspace. All projects opens the global tree. Manual sessions remain read-only.
Missing binding capability, withdrawn bindings and inaccessible bound projects show
an unavailable state. Only a successfully read empty binding list means no links.

| View | Existing content and secondary tools |
| --- | --- |
| Overview | Charter; Report menu for fixed snapshots, comparison, print and export; phase and workspace settings in header overflow |
| Discussions | Thread list, messages, inputs, planning, proposals, bundles, runs and analysis; meetings |
| Requirements | RTM; quality reviews and execution verification, evidence, deliveries, specs, work packages, handoff and issue publication |
| Schedule | Roadmap baseline/forecast/observation; sprints |
| Dependencies | Maintenance policy, checks, workload and release baselines |
| Issues & changes | Governance and findings; repairs, review and follow-ups |
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
Opening a thread immediately remembers Discussions. Returning from Settings rereads
selected report, requirement and resource records before presenting their facts.

## Headers, copy and acceptance

One page title; do not repeat the workspace name above it or repeat the view name
below it. At most one primary action, an overflow menu and icon actions. Reading
content has a 760px measure; forms 640px; tables and boards fill up to 1600px.
English uses sentence case. Every new string has natural Traditional Chinese in
`ui/ui-strings.js`, merged into the existing i18n mechanism. No raw keys, undefined,
null, emoji icons or unexplained disabled navigation.
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
