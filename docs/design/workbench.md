# Workbench presentation and selection

The owner's October 16 [EC-1.0 decision](../product/experience-convergence.md)
extends this presentation foundation. Its first delivery connects the existing
start, server-owned follow-up queue, fixed Result and PR actions in the shared
UI. Preserve one selection, draft namespace and event reader across perspectives;
do not add a progress store or infer control from a session link. Empty inspectors
take no space. Show outcome, changes, verification and delivery target before
technical receipts. This is the implementation direction, not a claim that those
pending contracts are available on main. Current differences and file ownership
belong in the [existing handoff](../product/v1.3-agent-handoff.md).

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

The four daily entries use existing views: Today opens Pending, Projects opens
the selected workspace's Overview (otherwise the project catalog), Work opens
the workspace Board (otherwise Sessions), and Resources opens the workspace's
Agents view (otherwise connection settings). The project header's management
selector retains every capability-supported view, including Discussions and
Board. Tools and legacy links retain operations, delivery, artifacts, cleanup,
sessions and workspace creation. These are entry mappings; Today has not yet
acquired the new PM recommendations, and Sessions is not a count of product Work.

Navigation and the existing context list occupy separate columns where space
allows. The list moves into the existing keyboard-accessible mobile drawer at
less than 1000px. No second catalog or search cache is created. The original
workspace binding, partial-source and permission rules still select its rows.

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
| Board | `#/pm/{id}/board` | Work daily entry | Daily entry (`.wc-daily a[href="#/pm/{id}/board"]`) / Management selector (`select[data-workbench-management]` value `board`) |
| Operations | `#/operations` | Tools menu | Tools menu (`[data-shell-tools]` → `.ui-menu-panel [role="menuitem"][href="#/operations"]`) |
| Delivery | `#/delivery` | Tools menu | Tools menu (`[data-shell-tools]` → `.ui-menu-panel [role="menuitem"][href="#/delivery"]`) |
| Artifact review | `#/artifact-review` | Tools menu | Tools menu (`[data-shell-tools]` → `.ui-menu-panel [role="menuitem"][href="#/artifact-review"]`) |
| Cleanup | `#/cleanup` | Tools menu | Tools menu (`[data-shell-tools]` → `.ui-menu-panel [role="menuitem"][href="#/cleanup"]`) |
| Sessions | `#/sessions` | Tools menu | Tools menu (`[data-shell-tools]` → `.ui-menu-panel [role="menuitem"][href="#/sessions"]`) |
| Pending | `#/home` | Today daily entry | Daily entry (`.wc-daily a[href="#/home"]`) |
| Settings | `#/settings` | Settings link | Navigation footer link (`a[data-shell-settings]`) |
| All projects | `#/projects` | Switcher / Catalog link | Workspace switcher menu (`.shell-workspace-switch` → `.ui-menu-panel [role="menuitem"][href="#/projects"]`) at desktop; drawer catalog link (`.shell-all-projects, a[href="#/projects"]`) at 390px |

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
