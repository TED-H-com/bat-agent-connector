# Workbench presentation and selection

The owner's October 17 [EC-1.1 decision](../product/experience-convergence.md)
extends this presentation foundation. Its first delivery connects the existing
start, server-owned follow-up queue, fixed Result and PR actions in the shared
UI. Preserve one selection, draft namespace and event reader across perspectives;
do not add a progress store or infer control from a session link. Empty inspectors
take no space. Show outcome, changes, verification and delivery target before
technical receipts. This is the implementation direction, not a claim that those
pending contracts are available on main. Current differences and file ownership
belong in the [existing handoff](../product/v1.3-agent-handoff.md).

When the selected Work's Result is available, the shared Workbench keeps that
Result and its review first, with existing work/session tools and history in one
disclosure. Work record controls, checkpoint actions, delivery/source readers, permissions, labels and
history remain the same mounted components. Opening this disclosure performs no
write. Its open state belongs to the selected Attempt, and an active field is not
hidden if a Result becomes available while it is being edited. If the Result is
unavailable, the existing tools remain directly accessible. Ordinary session and
classic-layout paths keep their existing presentation and authority.

The owner's [October 15 supplement](../product/workbench-ux-plan.md) changes the
v1.3 information architecture. Its [48 acceptance cases](../product/workbench-ux-acceptance.md)
remain separate from E00–E11 and R0–R5. Private reference images were reviewed
locally; no image, logo or source code from that interface is distributed here.

Baseline: `ea33d5ccca33eabcc014c9f46bc735958c0a1394`, including #190's synthetic
flow, #191's shared read-only demo, and #192's real ordinary PM request path.
The owner authorized continued implementation while personal testing is pending.
That authorization does not record R0 or real BAT acceptance.

## Candidate rollout

Tools → Workbench preview opens the same route with `?workbench=1`. Tools →
Classic layout removes its effect. The query survives hash navigation and is
available in Web, Tauri's shared source and the read-only demo. No schema change,
ID migration, copied progress, new listener or new mutation is introduced.
Legacy layout remains the default during this first review increment.

`workbench-mode.js` captures one immutable layout decision per page load, with
`layout`, `source`, `status` and a fallback `reason`. Its pure `decideLayout`
accepts an optional policy that can disable the Workbench preview; without policy,
only the first `workbench=1` query value enables it and Classic stays the default.
Status remains `preview`, never acceptance. Switching either way preserves the
storage namespace, all drafts, operation keys, selected target, checkpoint and
displayed history. Accepted routes, rollout defaults and Classic retirement remain
owner decisions.

The four daily entries use existing views: Today opens Pending, Projects opens
the selected workspace's Overview (otherwise the project catalog), Work opens
the workspace Board (otherwise Sessions) on desktop and the shared full-screen
Work list below 1000px, and Resources opens the workspace's
Agents view (otherwise connection settings). The project header's management
selector retains every capability-supported view, including Discussions and
Board. Tools and legacy links retain operations, delivery, artifacts, cleanup,
sessions and workspace creation. These are entry mappings; Today has not yet
acquired the new PM recommendations, and Sessions is not a count of product Work.

Navigation and the existing context list occupy separate columns where space
allows. On smaller screens `#/work-list` gives those same list/search nodes a
full-screen location, separate from the workspace drawer. Board remains in the
project management selector and has a link from the workspace's list. The selected
Work header returns to that list; browser Back/Forward follows the same routes.
Search, the selected row and list-page scroll survive the round trip. The desktop
column has its own scroll so rotation cannot overwrite the list-page position.
No second catalog or search cache is created. The original
workspace binding, partial-source and permission rules still select its rows.

The desktop context column can be resized with its labelled separator. It reuses
the Classic sidebar's pointer, keyboard, Escape/capture-loss cancellation and
storage-failure handling. Width is a browser preference (`batc.workbench.list.width`),
separate from Classic; it does not belong to project state or authorize an action.
Bounds reserve 360px for the main view after navigation and the separator, with
a 200–500px list. A narrow window clamps the rendered width without losing the
saved preference. Defaults/reset are 256px, or 220px below 1280px. The separator
is absent from mobile and full-screen list navigation, has a 44px coarse-pointer
target, and is disposed with the shell. Reflow moves no composer or conversation
nodes and retains the existing search, selection, drafts and list scroll. The
Work heading's actions wrap with available content width so widening the list
does not squeeze the title into a few characters per line.

The preview project list joins an execution to its existing Work only when the
project response supplies a caller-visible `work_identity`. This projection checks
both recorded start-child links, the Attempt, the exact session and source visibility;
titles, paths and ordinary user-added associations cannot establish it. It reads the
existing journal without collecting Result/actions or creating progress records.
HTTP and MCP/RPC return the same optional association. It grants no control.

A joined Work has one row, retains its canonical title and pending decision state,
and carries the selected Attempt's existing session link (otherwise the newest
visible execution in this bounded project read). Old execution links still select
the same Work. Attempts and source records remain available through their original
views and links. Unproven, unavailable or unloaded Work associations stay separate,
including sources with identical titles. A fresh withdrawal removes the join;
keyboard focus stays on the still-visible Work when its link changes.

Discuss from a validated Work keeps that Work first in the bounded PM context
list and preserves the exact workspace, Work and Attempt in its return link.
The originating session link remains visible on wide screens as well as mobile;
the discussion list cannot restore that execution selection. Reloading the PM
draft and returning keeps the existing next-instruction draft. The destination
reads the public Work and Attempt again before offering any controls, even if
another Attempt became latest in the meantime. A legacy session link does not
acquire Work identity from the last workspace or an earlier route.

### Classic → preview location

| Classic destination | Route | Preview location | Reached via |
| --- | --- | --- | --- |
| Overview | `#/pm/{id}/overview` | Projects daily entry | Daily entry (`.wc-daily a[href="#/pm/{id}/overview"]`) / Management selector (`select[data-workbench-management]` value `overview`) |
| Discussions | `#/pm/{id}/discussions` | Project management selector | Management selector (`select[data-workbench-management]` value `discussions`) |
| Requirements | `#/pm/{id}/requirements` | Project management selector | Management selector (`select[data-workbench-management]` value `requirements`) |
| Board | `#/pm/{id}/board` | Work list / project management | Desktop Work daily entry; mobile Work-list header; Management selector (`select[data-workbench-management]` value `board`) |
| Operations | `#/operations` | Tools menu | Tools menu (`[data-shell-tools]` → `.ui-menu-panel [role="menuitem"][href="#/operations"]`) |
| Delivery | `#/delivery` | Tools menu | Tools menu (`[data-shell-tools]` → `.ui-menu-panel [role="menuitem"][href="#/delivery"]`) |
| Artifact review | `#/artifact-review` | Tools menu | Tools menu (`[data-shell-tools]` → `.ui-menu-panel [role="menuitem"][href="#/artifact-review"]`) |
| Cleanup | `#/cleanup` | Tools menu | Tools menu (`[data-shell-tools]` → `.ui-menu-panel [role="menuitem"][href="#/cleanup"]`) |
| Sessions | `#/sessions` | Tools menu | Tools menu (`[data-shell-tools]` → `.ui-menu-panel [role="menuitem"][href="#/sessions"]`) |
| Pending | `#/home` | Today daily entry | Daily entry (`.wc-daily a[href="#/home"]`) |
| Settings | `#/settings` | Settings link | Navigation footer link (`a[data-shell-settings]`) |
| All projects | `#/projects` | Workspace switcher | Workspace switcher menu (`.shell-workspace-switch` → `.ui-menu-panel [role="menuitem"][href="#/projects"]`); on mobile open the workspace drawer first |

## Source mapping

| Presentation | Existing authority | Current limit |
| --- | --- | --- |
| Work identity, title and purpose | Explicit `/work-items/{id}` for an item route | An unbound session remains a session; it is not silently promoted to Work |
| Attempt and conversation | `/sessions/{host}/{session}` and its messages | Existing latest-window/coverage rules; no native-reader support is invented |
| Activity in header and Live | Same authorized session envelope | Streaming means output; idle or start-operation success does not mean completion |
| Related work | `work_items` in session envelope | A link is not a fulfills/dependency edge |
| Fixed source | `started_from` commit and original session | Read-only link, no implicit continuation |
| Execution record | `relations_summary` | No inferred scheduling dependency |
| Work derivation | Explicit `derived_from` in work-item envelope | Branch ancestry is not execution ordering |
| Results and decisions | Original result/receipt/work-item components | New cross-CLI Result and PM assessment composition remain E04/E09 |
| Next | No current queue projection available here | Historical queued receipts cannot become a live queue position |

`workbench-projection.js` is a pure presentation mapping. It does not allocate
IDs or persist domain records. Lineage currently provides a bounded, keyboard
operable relationship list, not the complete proposed graph. It displays at most
30 recorded relationships and explicitly marks limited coverage. There are no
fabricated empty requirements, completion percentages, forecasts or dependency
arrows. The original full relation tools remain in Results.

Item-backed session reads revalidate the item and its exact session link. A removed
link cannot keep the old work title or authorize controls through that route.
An item without a linked session still uses the existing work record page; adding
three views and a composer to that state remains part of UX03/E02.

## Selection, updates and permissions

Work, Lineage and Live retain the original conversation and composer nodes.
Changing perspective does not navigate, fetch or execute. The bounded UI memory
stores only the chosen perspective and scroll offsets, keyed by connection/
principal, workspace and explicit item or session identity. Existing composer
draft and original-operation keys remain unchanged. Multi-attempt input needs
E04's Work/Attempt contract before drafts can be migrated to a new Work namespace.

Inspecting a relation opens one named inspector while the original title remains.
Only Open record changes the primary route. Escape restores the relation control.
Results reuses the same mounted components in an optional inspector. Interrupt
remains the original gated action, now in More. A manual or foreign session gains
no new controller capability from any of these presentations.

The original event reader supplies all perspectives. Withdrawal clears header
facts, relations, Live and the relation inspector along with conversation data.
Generation checks prevent a late read from reviving that source. A failed refresh
clears the new projections and explicitly marks activity and relationships unknown;
Work retains the original retry and draft. Stale observations cannot appear in Now
even if their last streaming flag was true. Queue coverage remains unknown.

## Delivery accounting

This increment advances UX01/02/03/05/06/09; none is declared fully accepted.
Fixture coverage addresses parts of UX-A01/02/03/04/05/07/28/31/37/38/43/46.
Tests exercise the real shared components via mock HTTP and IPC, with no live BAT,
model, MCP or GitHub writes. P evidence is not I/L acceptance.

The following images are our shared product rendered with the existing synthetic
workspace fixture, not the owner's private reference images. Captured October 15
with `workbench.spec.ts`; these show layout and reading behavior, not live execution.

| View | Classic | Candidate |
| --- | --- | --- |
| Desktop, zh-TW, 1440px | [Before](workbench-evidence/classic-desktop-zh-TW.png) | [Work](workbench-evidence/work-desktop-zh-TW.png) |
| Phone, zh-TW, 390px | [Before](workbench-evidence/classic-phone-zh-TW.png) | [Work](workbench-evidence/work-phone-zh-TW.png) |
| Recorded relation, EN, 1280px | — | [Lineage and keyboard focus](workbench-evidence/lineage-desktop-en.png) |
| Selected session, EN, 1440px | — | [Live with unknown queue](workbench-evidence/live-desktop-en.png) |

Local validation: 167 unit cases; 129 related browser regression cases followed
by 36 final workbench/demo cases (including six project-selector locale/size
combinations); two HTTP/IPC checks after the focus-preservation fix, then three
submission/link-withdrawal cases. The latter verify the existing original-key
`session.send` request, queue flag, receipt and newer draft through perspective
changes, and block the old work route after its session link is removed. The first
focus test assertion expected a non-normalized timestamp; it was corrected to
the existing ISO timestamp contract. Background updates keep inspector and Live
keyboard focus. A final 23-case workbench run also covers tab-arrow focus and
returning from Live to Work: closing an already hidden Results panel must not
redirect keyboard focus. Full Python and exact-commit CI results belong in the PR status.

Still required: real adopted-plan → work-package → start composition; reliable
next-turn progression; Work/Attempt/Result identity across adapters; full typed
Lineage and live queue sources; assessment decisions at all entrances; unbound
work states, source withdrawal across every new projection; the complete visual,
200-work/stream load and installed/live acceptance matrix. V0–V4 and all 48 cases
remain open at the scope defined by the owner's package.

## EC-1.1 mobile and continuity increment

The [adopted delta](../product/experience-convergence-delta.md) adds full-screen list →
Work/discussion → on-demand sheet navigation on the shared frontend. Workspace settings
and the selected Work's details have separate entries. Global Today/Projects/Work/Resources
remain separate from the selected Work's Work/Lineage/Live views.

Back closes the nearest sheet before returning to the original list, restoring query,
selection and scroll. Reflow and touch landscape preserve the same composer, draft and
single send handler. Visual viewport/safe-area handling distinguishes zoom from keyboard
changes; IME, voice input and multiline paste cannot trigger accidental send. The mobile
product target is 44 CSS px. A local draft is never presented as a server-accepted input.
Foreground/login recovery reads the original key and rechecks source/permission before
showing actionable state; changing account/workspace cannot replay the previous draft.

List summaries, selected details and incremental source refresh use existing collectors,
IDs, scopes and events. New data keeps the reader's focus and position. Measure source
reads, payload/minute and selection-to-content separately from model time. No per-client
collector, mobile command layer or second completion store is introduced.

The first implemented increment covers the shared Work list, Work return link,
and Results sheet browser history. Back closes Results before leaving the Work;
Forward can reopen the still-mounted readable Results. Withdrawal prevents that
reopening, and route/connection teardown discards the sheet registration. History
contains only an opaque presentation marker, never a draft or command. Other
inspectors and the workspace drawer retain their existing close behavior.

`workbench-mobile.spec.ts` exercises English/zh-TW at 390/768/1440px, 844px
landscape reflow, original composer identity, one send after reflow, IME key
suppression, draft reload and HTTP/IPC source withdrawal. These are browser
fixtures, not physical keyboard, lock-screen, Safari or installed Tauri evidence.

`foreground-device-continuity.spec.ts` combines reflow, visibility recovery,
`pagehide`/persisted `pageshow`, browser-engine offline/online, IME suppression and
a newer Attempt in English/zh-TW at 390×844 and 844×390 over HTTP and simulated
IPC. It checks composer identity, focus, selection, draft bytes, reading/textarea
scroll positions, settled event/source gates and one explicit send to the original
Attempt with its persisted key. IPC uses a real same-origin network probe before
the simulated bridge; lifecycle events are synthetic. iPhone Safari, Android Chrome
candidate Enter, the real lock screen, Wi-Fi↔cellular and installed Tauri still
require the owner's physical-device evidence.

Review images use synthetic shared-component fixtures. Before/after Work captures
use identical data, draft, viewport and locale; list/Results captures add rows to
exercise scrolling. They contain no live Fleet or account data.

| View | Before | Candidate |
| --- | --- | --- |
| Work, zh-TW, 390×900 | [Before](workbench-evidence/ec11-mobile/before-phone-zh-TW.png) | [After](workbench-evidence/ec11-mobile/work-phone-zh-TW.png) |
| Work, English, 1440×900 | [Before](workbench-evidence/ec11-mobile/before-desktop-en.png) | [After](workbench-evidence/ec11-mobile/work-desktop-en.png) |
| List, zh-TW, 390×844 | — | [Full-screen list](workbench-evidence/ec11-mobile/list-phone-zh-TW.png) |
| Results, English, 768×844 | — | [Full-width sheet](workbench-evidence/ec11-mobile/results-tablet-en.png) |

Discussion sheet Back/Forward is merged in #287. Foreground recovery is tracked
in #288. Physical-device acceptance remains open.
See [K extensions](../product/workbench-ux-acceptance.md#ec-11-追加情境沿原-k-編號).

### Keyboard viewport evidence

The shared viewport observer constrains Work, Session and Discussion input to the
visible area without replacing their composers. Queue receipts and transcript
content remain scrollable. Phone landscape keeps Discussion list/detail navigation
through 999px. Pinch zoom and desktop resize restore ordinary layout.

| View | Before (foreground branch `775201bf`) | Current source |
| --- | --- | --- |
| Discussion, English desktop 1440×844 | [Before](workbench-evidence/ec11-keyboard/discussion-before-en-1440.png) | [After](workbench-evidence/ec11-keyboard/discussion-after-en-1440.png) |
| Discussion, zh-TW phone 390×844 | [Before](workbench-evidence/ec11-keyboard/discussion-before-zh-TW-390.png) | [450px visual viewport, 36px offset](workbench-evidence/ec11-keyboard/discussion-keyboard-zh-TW-390.png) |
| Work, zh-TW landscape 844×390 | — | [260px visual viewport](workbench-evidence/ec11-keyboard/work-keyboard-zh-TW-844.png) |

All images use synthetic fixtures. The blank lower region is outside the simulated
visual viewport; no OS keyboard is pictured. Chromium viewport events, Back, resize,
unchanged input nodes, queued receipts and zero unintended writes are checked.
Actual iPhone Safari, Android keyboard/IME, lockscreen and Tauri acceptance remain
separate. Desktop sidebar and existing operation/source authority are unchanged.

## Shared session runtime facets

`state/sessions.js::sessionFacets` separates observed output activity, attention,
local staleness axes (runtime evidence, gone and missing) and lifecycle. The
existing `sessionStatus` order remains the only single-pill and Live-lane rule:
pending interaction, managed attention/failure, stale/gone/missing, then current
streaming. Waiting attention stays in Waiting even when its observation is stale;
stale output never enters Now or claims that output is still arriving. Cards show
these independent facts and the observation time. A stale pending prompt is an
observation of a request, not proof it is still pending now and not action authority.

Result-source observations adapt their availability status and object/count pending
shape into the same facets. Unknown output stays unknown and pending is retained
alongside the stale marker and observation time. Diagnostics uses `runtimeStale`
for its stale count, preserving unknown values and the existing scrubber. These
presentation changes do not alter send gates, operations or pending actions.

The producer golden is generated by real ApiV1 session list/detail reads with
MockBat, with result-link observations from `work_item_results._observation`.
Current inventory emits `runtime_status=blocked/exited` but no typed managed
attention `status`, and projects exited lifecycle as `unknown`. It also emits
nested activity staleness alongside `fields_stale`. The golden keeps these actual
shapes; existing managed status/ended compatibility inputs and nested-only stale
inputs are pinned separately in pure tests. No missing wire fields are invented.
Choosing a different single-pill/lane precedence remains an owner decision.
