# Changelog

## Next release (unreleased)

- PM background updates no longer disable a pressed Save button and lose a plan
  revision submission. Explicit refreshes, lifecycle changes and source withdrawal
  still close admission gates, with late-response protection preserved.

- Unsigned macOS desktop packaging logs `bundle_dmg.sh` with `--verbose` and retries the same
  build once after detaching only this job's leftover `/Volumes` images.

- Sessions show a status pill in the list, rail and header only when they need
  attention or are active (`Waiting for you` / `等待處理`, `Running` / `執行中`,
  `Out of sync` / `未同步`, or managed failure/attention). Unloaded tabs with
  unknown activity stay quiet in the list and note "Not open in BAT" /
  "BAT 中未開啟" in Details; loaded tabs without a reported streaming flag note
  "BAT did not report activity" / "BAT 未回報活動狀態" in Details.
  `GET /api/v1/sessions/{host}/{id}/messages` adds `reading.incomplete_reasons`
  (`archive_unconfirmed`, `archive_budget`, `message_ids`) and optional
  `reading.unindexed_count`, and the conversation header replaces the generic
  "Partial history" warning with reason-specific quiet notes and `N+` unread
  lower bounds, including alongside archive budget and unconfirmed-history notes.
  Session details label the Session ID correctly, translate Workspace as 工作區 in
  zh-TW, and show the formatted observed time once without a duplicate raw ISO code tag.

- Keep Board recovery controls and keyboard focus stable across repeated read
  failures, and reuse the initial activation read during workspace loading.

- First-run BAT setup accepts a masked Connection URL paste, confirms the address
  and abbreviated fingerprint without exposing credentials, and validates fields
  before staging. Only proven admission refusals release the request; uncertain
  replies retain its original key. English and zh-TW errors and connection terms
  are readable, with technical facts in Details. Connected actions stay together,
  completed steps collapse with even spacing, and one Settings Refresh preserves
  page-only credentials and drafts. IME Escape keeps inspectors and menus open.

- Reserve the returning Issues inspector before its reads finish, so related
  chips do not disappear under the pointer when the side panel arrives.

- Give Discussions room with an on-demand thread panel, readable source rows,
  composer-side receipts and title/context search. Proposal scope opens for review
  with one primary action; IME renames preserve drafts. English and zh-TW explain
  saved messages without a connected PM, and meetings appear only in meeting threads.
  Keep proposal answer actions compact, hide unavailable report saves while retaining
  original-request recovery, and give empty states a short title and supporting line.
  Keep the phone thread list separate from unsent drafts, name exclusions before review,
  and show meeting prerequisites without empty selectors or repeated messages.

- Schedule at phone width fits every bar to the shared date range, with a range
  caption and the Today line, and does not offer Day zoom there. Week ticks label
  week starts only, so a partial opening week is not crowded under the sticky column.
  Redrawing the board while it is scrolled does not drop those labels. Open ends
  stay Open in English and zh-TW.

- Governance, Meetings and Follow-ups use shared cards, compact context, localized
  details and date-time editing. Register handoffs select and focus the requested
  durable form without submitting it; original drafts and recovery keys remain intact.
  Creation has one entry with focus and Cancel, read-only access explains its permission
  requirement, and missing follow-up baseline areas are named in both languages.
  Read-only reasons stay beside Refresh; unselected register governance shows a
  selection hint, and finding toggles retain their label when selected.
  Unavailable write entry points are hidden using each action's existing permissions;
  drafts and original requests return when permission is restored.

- PM panel failures show one localized sentence, preserving specific fixed-source
  mismatch explanations, with the original code and server
  message in Details. Withdrawal, read gates, Retry and original request recovery
  remain in place. Meetings explains the Sprint prerequisite to writers without a
  Sprint; readers see one empty message. Its card keeps the title
  and Refresh on the first row in English and Traditional Chinese.

- Reviewed handoff facts no longer print empty placeholders. The issues register
  names `format_valid` as "Format checked" / "格式已核對" (structure and cited
  sources only; conclusions stay unaccepted). zh-TW operation, agent and provider
  words use the existing label tables.

- Worktree and execution pages now read as a name, an owner, and the latest
  result. Record IDs, the raw record, and cleanup limits stay in Details.
  A delivery row keeps one review action; other links sit in More actions
  or in Details. Manual and unknown worktrees stay read-only.
  Untitled sessions retain a readable label, and dispatch appears only when
  paused. Read-only accounts do not see pause or project-creation controls.
  Project withdrawal clears rows, counts and editors while retaining drafts;
  unknown counts and unavailable project lists no longer appear as zero.
  Unavailable projects explain the read failure with a return link and transient
  failures offer Retry. Readers have no execution control card; resource history
  uses plain disclosures, and project overflow/reorder controls use shared icons.
  Unreadable project lists hide the view-only notice and offer Connection settings
  when forbidden; error codes stay in Details. Move/pin icons explain disabled
  moves, and execution facts distinguish ownership kind from a named lead agent.

- Sprints, AHP observations, work packages, package handoff, work batches and
  delivery receipts open on the answer: a status pill, one main action, and
  identifiers in Details. zh-TW uses 衝刺, 工作階段, 完成定義, 推送, 合併 and
  工作流程執行. A saved merge, a deployment and an acceptance stay separate facts.

- Those work tools keep Refresh on the first line, at the top right of the
  selector or status. In a narrow row, including the inspector, a secondary
  action such as New batch wraps under the selector. A new batch, result
  evidence and every sprint form except the one next action stay closed until
  asked. A failed read shows the error without an empty
  catalog or an empty Sprint selector. Sprint dates are both absolute. A
  delivery state is a pill, `PR #12` stays with its repository, and handoff
  names the permission in words.

- Name maintenance findings from the structured `finding` identity in the issues
  register, Overview attention and discussion chips, in English and zh-TW.
  Servers that omit `finding` keep the previous fallback. A withdrawn chip stays
  unavailable and shows no identity. A row named from `finding` does not also
  list a missing title. A neutral record says it is listed as an issue because
  it has no type of its own. Disclosure chevrons snap to the open mark instead
  of turning through a corner.

- Add a repeatable real-central PM decision walkthrough in English and zh-TW,
  with scripted native analysis, temporary Git, explicit issue resolution,
  original-key adoption and resolution recovery, and step screenshots. Keep hidden
  lifecycle admission fresh when events supersede Refresh, and pause original-key
  retry controls while Central sending is paused;
  see [walkthrough status](docs/design/pm-decision-walkthrough.md).

- Refresh the English and Traditional Chinese README, PM first-use navigation and public
  website around the project-to-decision journey. Replace the gallery with eight bilingual
  scenes from one synthetic project, pinned to merged main `aba02180` with capture hashes.
  Record which PRs are merged, and separate branch, installation and live evidence, in the
  [October 14 progress report](docs/product/progress-2026-10-14.md).

- Add pinned-commit dependency inventories with lockfile evidence, sourced declarations and
  reverse impact reads via `pm_dependencies_get`, `pm_dependencies_revisions_list` and
  `pm_dependencies_impact_get`. Unknown stays unknown; counts cover only captured data,
  and withdrawn sources lose rows and counts together.
  See the [dependency contract](docs/design/pm-dependencies.md).

- Report tasks open separately and focus their own controls. Change phase starts at
  Choose phase / 選擇階段 and requires an explicit choice. Workspace settings resolve
  plan and requirement labels from the bound revision, revalidate them on refresh,
  and keep their exact IDs in Details. Schedule, Agents and Dependencies show an
  unavailable state when their projection is unsupported, with secondary tools
  collapsed below it. Empty and unavailable shell tasks use short titles with muted
  explanations; Settings distinguishes missing evidence from unreadable projects
  and clears withdrawn project details. Native save receipts use localized punctuation.
  Discussion tools overlay without
  narrowing the thread; docked inspectors fill the viewport with independent scrolling.
  View headers, lists and secondary sections share one edge beside docked inspectors.
  Fixed dock reservations leave Discussions' own panel column intact, with one scroller.
  Overview grids follow their available column width beside a reader; milestone
  date ranges wrap only at the arrow.
  Unreadable Evidence plans keep one message and their diagnostics in the row's Details.
  Shell and lifecycle views share bilingual project phase names; Export report opens
  directly to its choices and download action. Phase empty states fit fallback fonts.
  Lifecycle copy consistently says phase, Settings shows one access reason, report
  controls fit their selected text, and breadcrumbs separate only existing crumbs.

- Add live project status via `pm_overview_get`: adopted-requirement progress, blockers,
  decisions, deviations and recent deliveries, retaining confirmed goals and recent
  decided history and unresolved requirement blockers across plan revisions,
  separate from fixed saved reports.
  Unknown or incomplete source counts stay null; hidden evidence never supplies totals.
  See the [overview contract](docs/design/pm-overview-discussion-context.md).

- Reuse completed PM source and operation visibility reads during synchronous
  page, step and final-frame validation. Discard memos on writes and before awaits;
  preserve current authority, withdrawal and original-receipt recovery checks.
  Reuse completed visibility without reopening source savepoints; still reject
  invalid borrowed source scopes before serving a cached decision. Reload canonical
  spec admission evidence for compact shared-operation read projections.
  Retain bounded proofs within their exact live guard context and copy decoded
  canonical operation/plan metadata without repeating its parsing.

- Bound repeated original-source traversal in GitHub workload observation checks.
  Share only completed historical decisions within each synchronous policy read,
  consume the immediately verified parent budget before each GET, and refuse a page
  whose deadline expired during validation. Keep the original 60-second bound,
  receipt recovery and independent post-read/source-withdrawal checks.

- Integrate the M4 lifecycle evidence and original recovery candidate with the
  Charter, requirements traceability, compact board and local CLI resource workbench.
  Preserve original runtime receipts and manual observation controls in board details,
  including source withdrawal, original-key recovery and localized idle-session
  evidence. Candidate versions remain
  core 0.2.6 and desktop 0.1.2, upgrading from the fixed core 0.2.5 fixture.

- Apply the same worker diagnostics and failure cleanup to joined maintenance's
  captured attempt waves, without changing the 60-second bound or durable receipts.

- Overview distinguishes unread milestone, delivery and charter sources from known
  empty records in English and Traditional Chinese. Live updates keep the attention
  row being read at its viewport position and preserve Progress Details focus and
  open state. Attention stays in Central's severity/age order during passive viewing
  and on return to Overview; focus, hover or text selection holds the existing order.
  Passive anchoring starts after scrolling into the list; new top rows otherwise
  appear in place without moving the page.
  A text selection anchors its own row when rows above it change height.
  Withdrawn rows and their counts still disappear immediately.

- Make compact Requirements rows hide their table header, size report controls for
  touch, and compare only changed statements with unchanged context collapsed.
  Requirement inspectors show acceptance criteria once and identify the qualifying
  review. Overview keeps decision text clear and explains delivered work awaiting
  this round's acceptance, with Requirements and sourced PM discussion links in
  English and Traditional Chinese.

- Keep Requirements rows and Board agent options stable when identical delivery
  reads finish in a different order.

- Let Escape cancel a restored Discussion rename draft while initial reads are
  pending, preserving IME composition, Save gates and original-request recovery.

- Keep release registration focus and keyboard input when source previews arrive
  during rationale entry, so Web and Tauri submit the reviewed request once.

- Reuse source validation within one fixed maintenance-repair read traversal so
  delivery does not exhaust its budget repeatedly checking the same graph.
  Each new frame still checks current sources; uncertain dispatches retain their
  original keys and receipts and are never sent again.

- Add source-bound discussions (`pm.thread.create`) and durable titles (`pm.thread.rename`).
  `pm_conversation_proposal_get` exposes adoptability that survives title-only renames;
  proposal reads include visible adoption receipts and list titles.
  Withdrawn context is redacted and access to analysis derived from it remains protected.
  The PM profile catalog uses version 3; version 2 plans still read and adopt.
  See the [discussion contract](docs/design/pm-overview-discussion-context.md).

- Issues & changes filters by shared status, open age and opening week. An age bar
  or a week point filters the loaded rows and shows a removable chip. Status choices are the
  shared groups, and an unknown status stays under Other. Counts say how many rows
  are shown unless the catalogue is complete; a loaded count appears once in the
  header. The type, title, status and next step
  stay visible as the table narrows. A mapping row without a title uses its source
  name. Board lanes fill the width in whole columns, keep each count, and ellipsize
  the lane name. The board toolbar wraps whole controls. Filtered empty results
  offer Clear filters; genuinely empty and incomplete reads have distinct states.
  Board counts append the number shown while filtered, and horizontal lanes share
  the tallest content height. Both panels keep Retry beside the read error, with
  Board error Details inside the full-width banner. Empty filtered register results
  hide trends until cleared, omit zero type counts and say None shown.
  Register rows, inspector facts and timelines retain exact decision and operation
  statuses such as Adopted and Reported done; status groups only filter rows.
  Board cards and work inspectors retain Content approved without implying Done
  or a passing completion definition.

- Add a read-only issues, actions and changes register via `pm_register_list` and
  `pm_register_get`, with source identities, recorded statuses and linked detail timelines.
  Totals and summaries require complete relevant sources; hidden or withdrawn records
  are omitted from rows and aggregates together, while other readable records remain.
  See the [register contract](docs/design/pm-register.md).

- Expose a structured `finding` identity on maintenance findings in `pm_register_list`,
  `pm_register_get`, overview attention and discussion context chips. `title` stays null.
  Missing or unproven facts are omitted and listed in `unknowns`. A withdrawn source
  removes the row and the identity together.
  See the [register contract](docs/design/pm-register.md).

- Maintenance repairs, maintenance review, maintenance policy and release baselines
  open on the current state, in English and zh-TW. Status uses a shared pill.
  Record identifiers, digests and the limit note stay in Details, and each durable
  form stays behind one action. "Turn into work" binds the finding and its matching
  rule. "Record evidence" selects that saved repair. Nothing is written until submit,
  and an unknown result still recovers with the original operation key.
  The state sits beside Refresh. A repair limit reads as "4 per 24 h". Counts from
  a source that was not observed stay Unknown. Each panel has one Details disclosure,
  and review keeps one primary action with the rest under More actions.
  Policy and release baselines keep that state on the Refresh row, with one divider.
  A baseline's channel pills sit on the next line, under the selection.

- Add the read-only `pm_schedule_get` projection of recorded baseline, forecast and actual
  dates, including unscheduled work and execution attempts for a future schedule view.
  Partial pages never claim totals; withdrawn sources lose rows and derived dates together,
  and missing dates stay unknown. Manual sessions remain read-only.
  See the [schedule contract](docs/design/pm-schedule.md).

- Add opt-in daily usage through `pm_resources_get` with `buckets=day` and an IANA `tz`,
  covering up to 92 local dates for usage charts. Complete days are settled, not frozen.
  Missing counters stay null; withdrawn sources lose buckets and totals together.
  See the [resource contract](docs/design/agent-resource-observation.md).

- Keep PM discussion disclosures as the reader left them during live updates and
  Refresh. A pending original request still opens Inputs when the thread is first
  shown, including after a reload, and does not expand that section again later.

- Add the shared Discuss with PM link builder and parser for exact context identities,
  ordered deduplication and bounded Unicode titles across project views.

- Keep each shell destination in one place per breakpoint; put All projects in the
  Work header, truncate tree names accessibly and disclose partial project bindings.
- Keep healthy connections Connected during view/source refreshes, canonicalize
  unavailable PM view links and forget workspace preferences after access withdrawal.
- Strengthen PM withdrawal tests across visible views; preserve icon license notices
  in generated clients and desktop bundle resources, and improve muted text contrast.

- Align sidebar rows on one icon/text grid; show Work read failures with compact
  details and Retry, while unsupported or empty Work sources stay quiet.

- Add shared UX v2 light/dark tokens, accessible UI primitives and local Lucide icons.
  Unify headers, disclosures, Discuss links, search fields, filter chips, empty/loading
  states and Refresh icons. Keep full accessible titles in narrow columns, identifiers
  intact, compact chips with invisible mobile touch targets, and workspace chevrons after
  their labels. Use bilingual action and operation-status labels across lifecycle,
  work, capture and delivery flows; complete counts omit loaded markers, and missing
  facts retain their unknown state.
  Share compact bilingual timestamps, timezone tooltips, calendar-safe dates and one
  visibility-aware minute ticker that retains selected text. Missing message times stay
  empty after refreshes. Search and filters fit their columns, with a measured select
  fallback for segmented controls. Narrow tables hide secondary columns beside
  inspectors, Operations uses mobile rows, and the selected rail extends through long pages.
- Coalesce event reads for the visible view and open tools across authenticated routes,
  preserving parent authorization, drafts and original-key recovery. Mark hidden sources
  stale and mask their facts until fresh activation reads settle. Ordinary work events
  do not reload the sidebar catalog.
- Unify the Web/Tauri workspace shell with compact navigation, explicit bound work,
  a mobile drawer, canonical PM view routes and namespace-scoped view preferences.
- Organize existing PM tools into Overview, Discussions, Requirements, Schedule,
  Dependencies, Issues & changes, Agents and Board. Keep secondary tools in disclosures;
  expose reports and lifecycle through header menus without changing durable actions.
- Track durable PM forms by connection, releasing discarded forms and receipt subscribers
  and restoring current gates immediately after refresh or inspector reattachment. Explain a gate that
  closes during submission or receipt checking while preserving drafts and original keys.
- Show live project status and what needs attention first in Overview, with compact charter cards
  and saved reports on demand. Unknown values stay explicit and saved reports never mix in
  live data. Open Overview issue and risk
  attention items in the Issues & changes inspector, retaining the exact source and a meaningful
  title for Discuss with PM. Label the pending count Open decisions (待決事項), show Recently decided
  with visible completed decision titles, times and actors, linking their exact register
  inspectors. An adopted discussion offers an authorized, editable Resolve issue form;
  reported resolution preserves the original finding and remains unassessed. Leave not-started
  progress as empty track, retain colored states at their true proportions, and omit repeated
  plan provenance from the live Goal card. Completed decisions say just now or use “ago”;
  attention keeps compact waiting ages, and both advance on live refresh.
- Polish Discussions with inline renaming, compact context and thread rows, and a paired composer
  toolbar. Proposal freshness follows central adoptability, an adopted proposal links its accepted
  revision, and first-message drafts and original-key recovery survive reloads. Keep unsent PM
  discussions focused on their draft; defer thread tools until the central discussion is created,
  even when all PM capabilities are available. Reveal and focus the accepted PM revision when its
  link opens the mobile Plan sheet, after the sheet establishes its focus boundary. Preserve the
  central Adopted status and accepted-revision link immediately after a PM proposal adoption,
  including recovery of its original receipt. Keep checking acknowledged PM operations while central
  waits for child operations or checks, using the original receipt and bounded backoff. Name
  every discussion context from central records in draft and saved threads, retaining
  identity-only request params and the source's actual kind (including Issue / 問題).
  Keep chips on one line with complete labels, use 決策 for decision records, retain
  local drafts and prevent sending when a source becomes unavailable. Keep saved-context
  captions, ages and analysis status in one readable text flow in the thread panel.
- Show acceptance, PR merge and deployment evidence for each requirement in a shared work inspector,
  with list rows on narrow screens and readable columns beside the docked inspector. Board adds
  filters, keyboard navigation, one-line cards and mobile lanes, and keeps existing PM tools in
  drawers. Requirements distinguishes accepted, unassessed and unknown quality coverage, marks
  partial delivery evidence, and shows the requirement traceability chain. Auxiliary failures stay
  local; live inspector actions retain focus and open menus. Board cards expose their recorded facts
  to assistive technology, retain selected delivery pages across refreshes, and restore selection
  and focus on return. Empty Boards show one empty state; paged lanes label counts as loaded.
  Requirement inspectors show saved verification evidence when no qualifying review is available.
  Withdrawal clears selection and returns focus to the view heading; snapshot conflicts expose raw
  codes only in Details and offer a local retry. Live refresh of a Requirements milestone selection
  preserves search and group-heading focus. Requirements work links open authorized unassigned work
  even before a plan is adopted. Keep Board card metadata on one centred line with wider fallback
  fonts, truncating flexible labels while preserving their full accessible names and delivery
  evidence. Keep title columns readable beside inspectors, preserve search focus and row
  expansion keys, and fit complete acceptance labels with wider fallback fonts. Verify
  background preservation after the initial detail read settles.
- Add the Schedule view: Day, Week and Month zoom over the central schedule projection, with dashed
  baselines, outlined forecasts, filled actuals and an Open marker for an actual without end
  evidence. Partial reads keep a Partial pill, and narrow screens get list rows with mini timelines.
  Schedule shows "End time not recorded" for a terminal attempt with no end time, and "Open" only
  when the projection marks the span open. Unscheduled omits rows already in the tree. The tree
  keeps one tab stop. A hidden Schedule does not fetch. Day labels and the Today mark do not
  collide, and narrow layouts share one timeline origin. Month ticks use short month names;
  attempt actions and interval tooltips use shared localized labels and calendar dates.
  Keep bar and delta labels above Today and beneath sticky titles, and fill the grid
  without stretching its date scale.
- Add the Dependencies view, which answers which checks to re-run when a dependency changes: a
  component matrix with evidence styles, a bounded graph, and an inspector with the pinned version,
  owner, tests to re-run and affected components. Capture again stays the durable header action.
  Preserve distinct dependency declarations alongside captured relations and hide empty view hosts
  after failed reads. Move Dependencies capture reasons below header actions; improve form layout,
  touch targets, dark chip contrast, graph spacing and selected-cell outlines. Apply Dependencies
  graph filters to cross-repository links and bound large matrix, graph and narrow list views with
  explicit shown-versus-loaded counts and legends. Clear Dependencies form options and hide source
  surfaces on failed reads, while retaining drafts and original operation keys. Refresh open impact
  inspectors and restore inferred relation drafts in both languages. Count owner declarations
  throughout Dependencies and disclose bounded impact, catalog gaps and declared-only results.
  Component inspectors name their dependencies; Discuss with PM titles include the selected item and
  captured commit. Preserve Dependencies keyboard focus across activation, live reads and inspector
  close in the matrix, graph and narrow list; expose valid selection semantics.
  Dim edges outside a selection, give a component from another repository its own
  node and legend entry, and state partial impact, omitted tests and contract,
  reference or service versions in plain language in English and zh-TW.
- Show the central register in Issues & changes: type counts and the summary appear only when
  coverage is complete, pagination stays honest, and a processing timeline offers Discuss and
  Propose change. A selected row survives refreshes, and states read in the reader's language.
- Show sourced token totals, a daily usage trend per CLI aligned to local days, and quota per pool
  and window in Agents. The trend is requested only when the server offers daily buckets, and an
  unavailable or withdrawn source leaves the totals, chart and inspector together.
  Paused source snapshots remain fixed during event refreshes. Localized chart dates
  keep a measured gap on narrow screens without dropping daily observations.
- Keep Agents headings, totals, rows, inspectors and daily usage in one consistent
  window when returning or receiving new usage. Refresh mismatched cached trends
  together with totals, preserving immediate withdrawal. Use human tool names and
  translated roles for sources and Discuss drafts, shared Unknown pills for missing
  facts, and complete the Traditional Chinese usage labels. Reuse trends while
  readable row facts are unchanged, including idle and partially observed sources.
- Give the global screens (Pending, Operations, Delivery, Settings, first run and Projects) the
  shared page header, inbox groups, operations table and inspector. Pending opens each operation
  from its inspector and reports a failed read instead of an empty inbox, and every registered
  action has an English and Traditional Chinese label. A failed Pending refresh shows no count, with
  one Retry when every group fails; operation durations use a compact day and hour form.
- Lay out the session transcript on one message grid with an inline agent chip. Tool events, labels
  and checkpoints share the inspector's disclosures, and a linked work item reads Work · title. Keep
  Session reply controls visible before focusing the input and after resizing. Complete the
  Traditional Chinese execution-limit explanations. Preserve Session return locations through reload
  and discussion drafts. Project inspector links carry their view, and Discuss returns to the exact
  session. Make Session checkpoints quiet at rest, distinguish capable hosts and preserve disclosure
  choices per connection. Continue forms expand below their actions and link to the new session only
  when the central result identifies it. Withdraw unavailable Sessions as one inline state, retain
  drafts and original retry keys, back off failed reads, and render message failures inside the
  transcript. Validate and group Session Delivery receipts without clearing them during refresh;
  distinguish transient failures, withdrawn sources and partial pages. Keep Session approval and
  question actions visible without keyboard scrolling; cap prompt content, preserve transcript space
  and use the shared Results drawer widths. Simplify manual-session copy and distinguish unknown
  titles, agents and disclosures. Keep Session transcript position and tool disclosures stable
  during live updates and resizing; restore focus on Back to latest and follow successful replies.
  Show received message counts, consistent agent marks, compact durations and Person on manual
  turns. Give mobile Session titles the full header width so wider fonts leave room for a whole
  pending answer input. Keep Info and Compose distinct on narrow screens, age Sent
  receipts from central completion times, and retain the shared inspector width and
  heading hierarchy.

- Add durable pinned-commit dependency inventories through the existing PM Git carrier, with
  lockfile evidence, Build/Test classification, CI verification hints, sourced owner declarations,
  immutable history and bounded reverse impact reads. HTTP, MCP/RPC and native read routes share
  current membership and source withdrawal; capture never executes repository code. Lockfile
  versions count as verified only for components the lockfile covers (never by name alone),
  unsupported fields make a capture partial, and freshness stays unknown until a repository head
  is observed. Optional extras and tool-specific dev groups keep their roles; an oversized
  manifest is stored as a deterministic partial capture with its omission count.

- Add the central live PM overview (`pm_overview_get` / workspace `/overview`),
  with adopted-requirement progress, explicit blockers, decisions, schedule/quality
  deviations that retain revoked acceptance until reacceptance, current review/proposal
  state, rollback evidence and bounded source
  coverage. Hidden lifecycle transitions and filtered pending operations remain
  incomplete. Hidden or oversized sources stay partial with distinct unavailability
  and size-limit reasons; source references respect
  the whole document's limit without withdrawing complete source counts. Reads share a snapshot and request-local source cache.
  Saved reports retain their fixed snapshot semantics.
- Bind up to eight original context items when creating a PM discussion and rename
  threads with durable version checks while preserving title-only proposal adoption.
  Renaming waits for any in-flight adoption to finish, protecting its original children.
  Proposal views publish the same adoptability rule used by admission; the shared
  UI uses it after a rename. Version 3 profiles retain compatibility with version 2 plan documents. Bound
  analysis references use each original revision's operation. Source withdrawal redacts chips and
  protects analysis derived from their frozen context; English and Traditional
  Chinese validation messages accompany the new context errors.

- Add a read-only issues, actions and changes register (`pm_register_list`, `pm_register_get`).
  Each row keeps its source record's identity and canonical status. Totals, open-item age, the
  last 12 weeks and blocking reasons are included only when every source that matches the active
  type filter was read completely. One skipped 409 or 413 record stays out of that source and
  marks it partial, so the totals and the summary are null; the other readable records stay
  listed. A withdrawn source leaves the remaining rows and the counts together. Detail
  timelines follow each stored foreign key one hop in both directions. A hidden target is
  skipped. A target that returns 409 or 413 stays off the timeline and marks that source
  partial. A lineage-hidden repair does not remove a visible finding's repair action.
  A meeting read that starts after a follow-up sequence reports that cutoff in
  `coverage.histories.follow_ups.after`. Adopted plan rows are ordered by the
  immutable plan revision sequence.
  A hidden row is
  omitted from rows, counts and summary together and does not fail the page. Row ids are
  path-safe (`governance.pmgov_…`) and cursors are opaque sequence tokens. The projection adds
  no write actions.

- Add a read-only project schedule projection (`GET /api/v1/pm/workspaces/{workspace_id}/schedule`,
  `pm_schedule_get`). Baseline and forecast dates come only from the current roadmap revisions.
  Actual intervals come only from explicit execution evidence. Manual sessions stay read-only.
  The read does not compute a forecast, a critical path, phase rows, or per-work token use.
  A visible session link with no observation stays an unknown read-only attempt. A linked
  operation or task with no record is omitted, marked partial, and does not publish the id.
  The page is cut by encoded size and by a 64-root lineage budget. `next_cursor` is an
  opaque base64url keyset, so the next page resumes without gaps. Requirement text is kept
  verbatim. Withdrawn work items, runs, operations, sessions, and tasks leave together with
  the dates derived from them, and their ids do not appear in rows or cursors.
  A hidden run does not take a session's run slot or remove the visible `run_id`. The
  per-session scan stops at a second visible run, or after 64 rows when that choice is
  still open, and then omits `run_id`. A hidden issue mapping does not consume the
  200-row issue cap or mark the page partial. A link to a hidden operation, task,
  or session does not consume the attempt-link caps or withhold a visible attempt.
  A task's `waiting.since` uses the latest transition into the current state,
  including named task events. Every schedule cursor the server issues is accepted
  back, including a row id that contains a control character. A plan or one issue
  mapping that lineage refuses is hidden; it does not fail the schedule read.
  A cancelled or failed operation ends at the matching status event, including a
  failure that never recorded a failed step. An attempt whose own lineage does not
  fit is omitted before its work's dates and status are derived.
  See `docs/design/pm-schedule.md`.

- Add opt-in daily usage buckets to the existing PM resources read. `pm_resources_get`
  returns per-source local days only when `buckets=day` is set; `tz` is then an exact IANA
  name and defaults to UTC. The window is still at most 366 days. Daily buckets additionally
  cover at most 92 local dates. A day is complete only when the collector's read started at
  or after that local day's end plus a settle margin (`max(15 minutes, 3 × interval)`), the
  stored cursor had reached EOF, and the window contains the whole day. A later Claude
  refinement can still land on its first-fragment day, so `complete` is settled coverage
  rather than a frozen total. Missing counters and uncovered days stay null, and withdrawn
  sources omit buckets together with their totals.

- Windows desktop: give the read-only Fleet contract process its own bounded PowerShell
  startup phase (at most 60 seconds) before the unchanged 10-second request deadline.
  Installed-app UI tests retry transient UI Automation reads within the existing
  readiness deadline and keep bounded, text-free attempt diagnostics.

- PM labels no longer print a missing value. A Brief section without a recorded
  status says Unknown / 未知. Issue tracking uses the repository name stored on
  the mapping, or the name on the published issue when the mapping omits it,
  and otherwise shows only the issue number.

- Diagnose durable-operation test timeouts with worker/step details and join failed
  test workers before journal teardown, preserving the existing deadline and original-key
  recovery.
- The cleanup integration test keeps reading the same operation until its status is terminal
  before the existing success checks, so early item receipts no longer fail a still-running cleanup.

- Introduced in [PR #81](https://github.com/teddashh/bat-agent-connector/pull/81):
  bundle the matching Python central runtime and prepare private installation data,
  stable personal identity, authenticated browser handoff and installation-owned login startup.
  Windows uses native file locks and private storage; safe forward upgrades preserve the
  journal and refuse busy work or downgrades. Validation records are linked from the PR checks.
- Add guided BAT/profile access, exact GitHub repository/host/workspace bindings and
  Task Service verification command settings. Configuration is revision-bound and journaled;
  saving probes access without dispatching, pushing or executing a saved verification command.
- Add adjustable project navigation, readable conversation/send receipts, unread and model
  preferences, reviewed skill selections, result links and repair work. Skill selections pin
  source digests and remain unapplied; historical queued receipts do not imply a current BAT
  queue position or support per-message cancellation.
- Formal signing, Mac notarization, production update channels and Linux persistent native
  credential enrollment are outside this delivery. Real-environment acceptance is user-owned;
  these exclusions do not claim corresponding features or tests are complete.

- Backport the official GLib iterator fix for RUSTSEC-2024-0429 into the Linux
  desktop dependency graph. Preserve the original crate version/license and exact
  source inventory; require resolved-source verification, an optimized iterator
  regression, and a packaged release WebView smoke test in desktop CI.

- Date/time inputs are validated the same way on every supported Python version; an
  hour of 24 is rejected.

- Add published-version starts through explicit repository/host/workspace bindings. Preview the
  exact branch head, fetch its fixed SHA into a fresh managed carrier, and recover the original
  operation after lost replies. Shared browser/Tauri UI, CLI, MCP and canonical workflow `.9`
  preserve the same request; human checkouts and remote refs remain unchanged.

- Windows Fleet core tests give PowerShell fixtures their own bounded startup phase before the unchanged work
  deadline, and start them one at a time.

- Add central durable `session.start`, exact workspace discovery and a shared start form. Save
  the original request before submission; reconcile unknown start/send outcomes and preserve retained
  worktrees for reviewed cleanup. Publish the same workflow in canonical agent skill `.8`.
- Add fixed-selection batch approval and managed artifact capture/review to the shared UI. Preserve
  each original request, operation and immutable revision across reloads and partial outcomes.
- Keep engineering handoff prerequisite selectors disabled while their sources are read. A slow read
  can no longer drop a selected assessment, and batch preflight refuses unavailable sources.
- Add Task Service reviewed cleanup and automatic eligible lifecycle cleanup using the same central
  ownership, control-version and live-consumer checks. Retain dirty/unique content and task history.
- Add native file picker/drop uploads, bounded literal text/static PNG preview, and Save As for an
  exact artifact revision. Rust owns local file handles, credentials and transfer recovery.
- Prepare the Rust Fleet core: strict configuration and preference snapshots, Windows held-process
  ownership and shared locks, plus bounded pinned-TLS/BAT and observe-only Connector probes. Desktop
  runtime wiring, migration and installed Windows acceptance remain separate work.

- Add durable `session.permissions` through HTTP, MCP, CLI and the shared browser/Tauri session controls.
  Preserve the caller, fixed session/task binding and operation key; record each Claude/Codex setting
  separately so partial or unknown outcomes cannot replay completed writes. Refuse a running Claude
  turn without queuing an unbound deferred change; historical deferred flags cannot apply automatically.
  Close legacy bulk-approval apply before any answer/raise; its dry-run preview remains available.

- Route legacy send, continue and answer through the central durable actions with the caller's
  authenticated authority, exact session/prompt binding, optional operation keys and complete receipts.
  Preserve client message IDs, queue and permission-answer behavior; lost acknowledgements read back
  the original effect. Remaining bulk-approval/start/orchestration adapters are tracked separately.

- Add shared browser/Tauri manual-file preview and capture. Review a remote relative path, source,
  size and digest, preserve the same capture after a lost reply or reload, then explicitly add its
  immutable revision to an attachment draft. The native bridge accepts only the fixed typed preview
  route. Actual-central fixtures now run in desktop CI alongside browser/native-transport UI tests.

- Refuse legacy `worktree_remove` / `remove-worktree` with `LEGACY_WORKTREE_REMOVE_DISABLED`
  after the existing ownership checks. An idle predecessor can share its directory with an active
  successor, so the legacy single-session check cannot authorize deletion. Use reviewed cleanup's
  consumer checks, retention and receipts; discard/branch overrides cannot reopen the old path.
  Read/status tools and canonical cleanup remain available.

- Add local Desktop Fleet connection controls through the existing Kit's fixed PowerShell facade.
  Restricted native IPC keeps configuration and process startup outside the webview, bounds replies and
  deadlines, and uses only the OS PowerShell's system modules. Revision-bound selection drafts survive
  uncertain replies; other-login monitors remain read-only. Shared confinement and cleanup views remain
  available alongside Fleet settings. Installed Windows ownership/lifecycle acceptance and Rust Fleet
  parity remain pending ([desktop Fleet adapter](docs/design/desktop-fleet.md)).

- Capture one reviewed file from a positively classified manual BAT session as an immutable artifact.
  Idempotent replay and resume/cancel keep the original credential and combined scopes without re-expiring accepted previews.
  HTTP, MCP and CLI use a short-lived credential-bound preview and the existing operation/store quota,
  staging and publication receipts. Reject symlinks, hardlinks, nonregular files, source changes and rebinding;
  preserve the manual checkout and recover completed captures without rereading or republishing their source.
  This is single-file continuation data; managed-result acceptance and complete dirty snapshots remain separate. The shared capture UI is described above.

- Route legacy MCP/CLI interrupt through the central durable `session.interrupt` action. Preserve
  fixed session/task bindings, complete results and final-frame checks; lost replies only read back.
  Optional keys explicitly report whether retries deduplicate; internal no-key sentinels never appear
  in operation reads. CLI read-only now guards daemon-backed mutations before dispatch as well.

- Keep session subscriptions after an initial observation/message read failure. Retry failed reads,
  disable session actions until recovery, and preserve pending answers and the original operation key.

- Present central session state evidence and discovery coverage, plus fixed-snapshot history and relations
  for sessions, known worktrees and executions. Retain loaded inventory pages using stable-ID order and
  identity-scoped dependencies. Safety reads keep parent archive restrictions current while drafts defer
  ordinary renders. Real central/MockBat and browser/native fixtures cover the shared UI; live M1 acceptance
  remains pending.

- Refresh mounted session questions, permissions and linked work-item evidence without losing drafts.
  Recheck pending identity before answering, preserve failed-read checkpoints, and disable archived-parent
  actions while an edit stays open. History/relations and full M1 acceptance remain tracked separately.

- Reject malformed deployment history cursors before querying, including integer overflow and
  non-finite timestamps, while preserving valid integer ordering and saved page cursors.

- Join in-flight deployment history reads before event acknowledgment, wait for every sibling read,
  and retain the selected history page through failed refreshes. Open reviewed confirmations keep their
  original generation while the event checkpoint waits for the form to close.

- Integrate delivery history after observation data step 2: run deployment step 3 in the same journal open,
  preserve original events, and append one saved snapshot per deployment. State changes publish bounded,
  operation-linked facts without verifier bodies or configuration; unchanged reads stay quiet.

- Reject malformed artifact IDs and attachment roles at HTTP admission with a structured error,
  before recording an operation or reserving upload storage.

- Add `--principal-only` for agent MCP installations: require the agent token for all calls,
  expose central operations and task adapters, and omit direct Fleet tools. Task status/result/events
  now accept principals with `observe`; missing agent credentials never borrow local admin authority.

- Generate Hermes and Grokbot skill bundles from the packaged canonical workflow, with
  source digest/version metadata and a CI drift check ([guide](docs/agent-skills.md)).
  Remove the Hermes cron-cleanup policy override; discover capabilities and preserve
  original operations/keys on reconnect or lost replies. Fleet pins and live installs are unchanged.


- Integrate immutable artifact inputs with reviewed cleanup: exempt only verified exact replicas with a
  readable store original. Recheck accepted evidence under the host mutation gate, including resumed
  cleanup after refs were preserved; missing or corrupt originals retain the surviving worktree copy.

- Prepare a read-only projection of exact verified artifact replicas for reviewed cleanup.
  Bind replicas to checkpoint creation and transfer intents; retain ordinary content when the
  immutable original is unavailable. Cleanup wiring awaits the integrated base; correct the
  artifact spec's data-step allocation and remove its obsolete Hub-import dependency.

- Artifact attachments, Part A ([design](docs/design/artifacts.md), plan §08/§12/§13, W05b, B04): immutable
  Connector-owned revisions, quota reservations and operation staging; binary HTTP upload authenticates before
  reading content. Typed work item/checkpoint refs, restricted Python 3.9+ SSH materialization inside the continuation
  worktree, independent digest read-back and a fresh source/input guard before dispatch. Source confirmation resumes
  the same parent; start/send lost replies use existing reconcile. HTTP, CLI, three MCP tools and bilingual Dashboard
  pickers/drafts/materialization status. No store deletion or new BAT channel; manual/result capture and artifact.accept
  (Part B, A03 attachments) and cross-host commit fetch (Part C) remain deferred.
- Artifact schema setup runs idempotently after numbered migrations without reading or advancing `user_version`,
  preserving delivery and observation data migrations ([design](docs/design/artifacts.md), plan §08/§13).
- Artifact scratch reaping retries independently of task ticks, with filesystem deletion off the event loop.
  Cleanup failures keep reservations and cannot fail a committed cancel, upload receive or unrelated admission
  ([design](docs/design/artifacts.md), plan §09/§13, B04).
- Attachment continuations reject oversized prompt manifests before recording an operation. Host readiness reports
  Git versions and requires Git 2.31+; managed clones safely create missing `info/exclude` with no-follow checks
  ([design](docs/design/artifacts.md), plan §06/§13/§28, B04).



- Duplicate registry identities discovered during capacity bookkeeping now retain the confirmed stop or reclaim
  result and report `REGISTRY_DUPLICATE_SESSION` as a capacity refusal, without changing the invalid registry.
- Cleanup uses Task Service's shared ownership lookup for preview, locked mutation checks and capacity retirement.
  Historical task start/branch bindings remain protected when a registry tag is missing. Cleanup settlement tests
  now wait for persisted operation outcomes; existing atomic cleanup transactions and early-refusal release rules
  remain in place after the operations integration.
- Cleanup reservations now arbitrate with process-held start claims under the shared registry flock. A live
  start refuses cleanup with START_IN_PROGRESS; a reserved or cleaned resource refuses new starts and recovery.
  Cleanup writes and retirement enforce unique host/session identities, and releasing a reservation keeps the
  same session ID on other hosts reserved.
- Cleanup and observation now share registry worktree creation identities across failover, reuse and reviewer
  carriers regardless of registry order. Session reads preserve cleanup tombstones across all transports,
  including historical sessions on removed hosts.
- Reviewed resource cleanup Part A ([design](docs/design/cleanup.md), plan §23/§10/§19, E01/E02): pure previews
  for work items (including children), checkpoints, integrations and hosts; 15-minute signed plans; scoped apply
  with retained refs before non-force removal, exact delivery receipt coverage, CAS local branch deletion,
  item receipts, permanent tombstones/aliases/search and actual retained content. Shared registry guards protect
  legacy tools too. HTTP/MCP/CLI and the English/zh-TW Dashboard share the contract. New cleanup scope;
  cleanup_discard gates only uncommitted discard, release_undelivered keeps commits and branches. Legacy cleanup
  only evaluates (LEGACY_CLEANUP_DISABLED on apply); auto_cleanup is deprecated; fanout stops its planner and
  keeps its worktree. Clones/areas/all pins stay. TaskDaemon cleanup remains unchanged; reviewed task cleanup and
  restore are Part B.
- Cleanup review fixes ([design](docs/design/cleanup.md), plan §23, E01/E02): idempotent journal DDL runs on
  every open without claiming a data migration version, preserving delivery and observation migration ordering.
  Attachment replicas are exempt only with exact materialization evidence (path, size, SHA-256, regular file,
  single link); edited, extra or missing content and unexpected directories require reviewed discard authority.
  Until the artifacts adapter supplies that evidence, all `.batc-inputs/` content follows ordinary retention rules.
  An HTTP regression test pins server-recorded acceptance authority and retries of the same public request.
  Joint worktree/branch cleanup is covered by succeeded receipts for both items. A branch moved after its worktree
  removal now returns PREVIEW_STALE before writing its retained ref.
  Integration cleanup targets resolve apply and handoff operations back to their preview using the snapshot's
  operation rows, so preview/apply/handoff targets list the same sources, areas, pins, repairs and sessions.
  Preview, apply checks and the final pre-stop read share the complete waiting-field set; a session that becomes
  waiting returns SESSION_WAITING and keeps its worktree.
  All terminals, including registered terminals, now supply live cwd consumer evidence using path components.
  Missing or failed live reads retain resources with OBSERVATION_UNAVAILABLE. Apply repeats consumer checks
  while holding the host directory flock before stop and each Git phase; a relocated or unknown consumer blocks
  mutation and keeps the worktree.
  Locked checks explicitly close host stdin on refusal or timeout, including on Python 3.10, so a failed read
  aborts immediately instead of leaving the helper waiting for its deadline.
  Fan-out stops its planner only with caller confirmation and every planned task successfully started. Refused
  confirmation, a failed start or an incomplete loop keeps the planner and its plan for retry, with a reason and
  a resource-cleanup next action; its worktree always stays.
  The legacy write-path audit also removed implicit worktree rehydration from cleanup evaluation. Missing BAT
  worktree state now escalates without a registration frame or registry changes, even with auto_cleanup enabled.
  Standalone BAT worktrees now project their clone from registry creation evidence, without needing a checkpoint,
  integration or task carrier record. Managed-root/layout evidence and live repository/branch binding are required;
  mismatches and origins outside managed roots stay listed and retained. Their delivered local BAT branches use
  the same preserve-before-remove and CAS path, while the clone remains the retained content store.
  Per-item apply snapshots observe only the item's host, including a branch's host derived from its creation slot.
  Initial previews and whole-plan validation still check every selected host; unavailable hosts retain resources
  with OBSERVATION_UNAVAILABLE without adding their read deadline to each healthy-host item.
  Host errors after a mutating call starts now report an uncertain outcome with exact call evidence and keep
  the cleanup guard. Read-back settles completed phases, completes unchanged temporary remnants only after
  rechecking their original manifest and every retained commit, or reports CLEANUP_PARTIAL_STATE with removed,
  changed and remaining content. Refusals before a mutating call remain definitive; partial states never count
  as success or release their reservation.
  Cleanup previews keep historical resources on removed hosts visible and read-only with
  OBSERVATION_UNAVAILABLE. Initial and per-item observations skip unconfigured hosts; BAT, SSH, apply and retained
  reads check configuration before client/runner lookup. Configured-host items still plan normally, and registry
  reservations and tombstones survive host removal.
  All host mutations require the locked consumer gate. Process, transport, decoding and malformed-reply failures
  after permission now remain uncertain with their reservation kept and settle by read-back; failures before
  permission remain definitive because the host helper aborts on EOF or refusal without writing.
  Cleanup receipts and tombstones now expose cumulative completed and refused phases, including prerequisite
  session stops and worktree removals. Later refusals keep runtime/destructive partial items reserved with
  CLEANUP_PARTIAL_STATE; resuming validates the recorded post-discard state and retries only refused phases under
  a new attempt. Cancel keeps partial effects and reservations visible; additive-only settled pins may release
  their guard and stay listed. Stop's read-only gate now completes before its BAT write, so a lost stop reply
  is uncertain too. The API contract and both skills document the receipt fields and cancellation rule.
  Resumed cleanup refusals before any durable external step now release all of that operation's reservations
  and settle pending/running receipts with the refusal code, including expiry and token mismatch. Runs with
  steps keep their existing reconcile rules. Already-absent resources get their own receipt and summary count,
  satisfy dependencies, and make no per-item host call or false tombstone/registry cleaned mark.
  Confirmed fan-out planner stops now retire the runtime as stopped after acknowledgement/read-back, releasing
  its host slot while keeping creation evidence and the reclaimable worktree. Reviewed absence releases capacity
  as absent_at_cleanup only when its carrier is removed/already absent (or it has no own worktree); retained
  worktrees keep their resume slot. Retired IDs refuse drive/resume/same-ID start with SESSION_RETIRED.
  Legacy worktree removal records its result while preserving an already-retired runtime status.
  An all-absent host can now apply its reviewed preview to release eligible active session capacity even when
  nothing remains to stop or remove. The local-only settlement leaves host resources and tombstones unchanged;
  retained carriers and already-retired rows cannot enable an apply (docs/design/cleanup.md, plan §23, E01/E02).
  Removed hosts now keep every recorded checkpoint, task, repair and registry worktree identity, including its
  local branch and clone/area carrier. Branch identities are projected before live reads, so host removal keeps
  their IDs stable and retains them with OBSERVATION_UNAVAILABLE. Historical host targets remain inspectable;
  mixed-host apply still reclaims healthy-host items without any removed-host BAT or SSH call. Branch re-checks
  read the live ref after worktree removal and normalize only dependencies with succeeded worktree receipts.
  Historical host targets also recognize a failed integration prepare's durable creation facts before a preview
  row exists, keeping its area and temporary identities visible without host calls (plan §23, E01/E02).
  Capacity retirement follow-up (v2 plan §19/§22 R08/§24, E01/E02) is non-failing bookkeeping: it changes only
  matching active, non-task registry rows.
  Non-counted history remains unchanged; session receipts explain generation changes, task/start ownership,
  retained carriers and registry refusals/I/O failures without degrading a completed reclaim. Starting/uncertain
  sessions and carriers remain retained with COMMAND_UNRESOLVED. Confirmed planner stops stay reported as stopped
  even when capacity retirement fails; crash/resume replays the same retirement without rewriting it.
- Preserve observation history/discovery alongside confinement evidence. Removed-host historical sessions
  remain readable with unknown current account verification; live detail reads report option drift without
  rewriting creation evidence. Additive host-check DDL preserves observation, delivery and future data versions.

- Verify an unsent start's worktree is absent after a rollback success reply before clearing its durable path
  ([design](docs/design/confinement.md), A10). BAT can acknowledge a no-op removal after losing its in-memory
  mapping. A remaining carrier, unavailable read-back or cancellation now retains identity; repeated same-ID
  recovery refuses to create a replacement until the original carrier can be reconciled.

- Reject Python import-path overrides before account-check interpreters run ([design](docs/design/confinement.md),
  A10). The shared shell gate now refuses executable/shared-library `._pth` files and build markers, including
  libpython symlink targets and standard multiarch directories. These can redirect startup imports despite
  `-I -S`; even root-owned overrides are outside the supported system-package layout. Earlier closure caches
  expire. Custom Python builds/loader paths and hostile same-UID processes remain outside the trust claim.

- Keep unsent same-ID starts retryable after a confirmed worktree rollback ([design](docs/design/confinement.md),
  計畫 §06/§12/§28, A10; v2 A06). Clear the removed carrier's path/branch, restore the origin cwd and retain its
  rollback audit, so retry creates a new worktree. Failed, cancelled or unconfirmed removal keeps the original
  identity across repeated recovery attempts; only a matching read-back permits reuse. Task lead recovery uses
  the same rule without changing its normal retain policy or task transitions. Sent starts remain fenced.
  A10 still awaits W12 live acceptance.

- Prove the full system Python closure before account-check interpreters execute ([design](docs/design/confinement.md),
  計畫 §06/§07/§12, A10). A bounded absolute-tool gate checks every stdlib/platform-stdlib entry, bytecode and
  extension, symlink hops/targets, zip and venv parents. Unknown layouts or incomplete scans report
  check_executable_untrusted and confined Claude falls back to default. Gate and rechecks share one definition
  and budget (default 50000 entries). Programs use -c argv with updated sudoers examples; ptrace_scope is recorded
  and the no-hostile-same-UID trust assumption is explicit. Directory-only caches expire. W12 still must prove
  real-host Debian/Ubuntu/RHEL behavior and A10.

- Registry session identity is unique per host/session ID ([design](docs/design/confinement.md), 計畫 §06/§12, A10).
  Sent starts, including BAT invoke-error replies and legacy failed/sent rows, are fenced from same-ID reservation
  retries. The pinned Codex start path can retain a session after an error, so the connector keeps its reservation
  and worktree for read-back; Task lead commands/tasks remain uncertain until recovery. Failover keeps the reserved
  successor rather than releasing it. Shared registry read/write validation rejects duplicates explicitly with
  REGISTRY_DUPLICATE_SESSION before writing; recovery and warm claims update the existing row. Cap and supersede
  handoff rules remain intact; legacy failed/sent successors use the same failover binding for read-back recovery.
  A10 still awaits W12.

- Host-account verification now requires an operator-declared trusted auditor SSH channel ([design](docs/design/confinement.md),
  計畫 §06/§07/§12, A10). The auditor proves its different identity and protected login/bootstrap paths before
  directly executing isolated Python as the BAT account through a narrow sudo rule. Returned UID/channel facts
  must match config; same-account login output can never certify a boundary. Without a trusted alias, no in-band
  check runs: unknown/check_channel_untrusted gives fallback_default and confined Claude uses default, never
  acceptEdits. Old checker caches are invalidated. Existing integrity/process/root scans remain defense in depth.
  Trusted-channel verification on real hosts and A10 still await W12.

- Task Service reviewer starts send one start frame per reservation ([design](docs/design/confinement.md),
  計畫 §06/§12/§28, A10). Lost or unconfirmed replies, including `ok: false` or a different session ID, retry only
  metadata reads with bounded backoff. Unproven starts keep their reservation and leave the command and task
  uncertain with `CONFINEMENT_START_UNSETTLED`; a later tick settles by read-back without another dispatch.
  Readable identity/permission mismatches still refuse, pre-transport failures still release, and valid ACKs retain
  best-effort evidence reads. The lead retry loop and other start/recovery paths retain their transport fences.
  A10 still awaits the W12 live run.

- Dashboard start notes now follow the server's `host_account.start_effect` ([design](docs/design/confinement.md),
  計畫 §06/§10/§12, A10). Unchecked or stale evidence requires a live recheck at start; supported hardening gaps
  and undeclared accounts use confined Claude's plain default fallback. Only refusals show the blocked note and
  reason, including before Codex's sandbox note. Capabilities GET remains read-only, and the projection shares its
  rule with the start gate. English and zh-TW notes and both skills explain the four values. A10 still awaits W12.

- The A10 Linux account-check fixture isolates its fake `pathlib` import, keeping pytest's real `Path` intact on
  Python 3.10 and 3.11; the product's read-only check is unchanged.
- Share artifact attachments across browser and desktop work-item/continuation forms. Preserve exact
  identity-scoped operation intents through lost replies and source-head changes. Native binary upload
  uses capped raw IPC to one operation-bound central route, with no credential or URL supplied by JavaScript.
  Temporary real Git/MockBat fixtures verify immutable bytes and materialization; native file-picker and
  complete live attachment acceptance remain pending.

- Port confinement evidence and reviewed cleanup into the shared desktop/browser source. Retain cleanup
  requests within the original account across uncertain replies, preserve agent/form choices during updates,
  and validate native cleanup routes and typed previews. Real temporary Git/MockBat cleanup and UI fixtures
  verify the central flow; live confinement, restoration and full observation history UI remain pending.
- Refuse filtered Dashboard checkpoint replay before reading events or opening SSE. Shared signed checkpoints
  acknowledge only the complete public feed, so an empty or partial filtered page cannot skip other updates.
  Legacy filtered event requests keep their existing response shape and numeric cursors.

- Keep failed checkpoint-preview refreshes inside the event acknowledgment barrier. Preserve the original
  commit selection and note, refuse stale checkpoint creation, and resume after a successful read; a failed
  preview cannot silently change the request into a checkpoint of HEAD.

- Wait for asynchronous Dashboard view refreshes before persisting event checkpoints. Failed or deferred
  refreshes retain the original cursor and drafts. Failed reads pause mutations; deferred renders keep
  version-checked form saves available. Delayed work cannot cross accounts or
  mounted views. Existing pending-control and linked-history presentation gaps remain tracked under R04.
- Port Delivery environment history, fixed rollback/retry confirmations and translations into the shared
  browser/Tauri frontend. Keep deployment previews and writes on the central API, add the narrow native read
  allowlist, and preserve identity-scoped operation keys across reconnects and lost replies. Delivery events
  refresh cards through shared polling; open confirmations retain the selected version.

- Add the shared Vite/TypeScript Dashboard source and initial packaged Tauri 2 shell with restricted native
  central transport, native-memory credentials, tray hiding and same-session instance handoff. Browser and
  desktop share bounded checkpoint polling, isolated draft/operation storage and offline write blocking;
  account changes abort pending submissions. Generated assets and unsigned packaging are checked in CI.
  Linux native and combined central fixtures pass; Windows, Fleet, protected credential enrollment and R04
  per-view freshness remain pending ([desktop foundation](docs/design/desktop.md)).

- Record the [Tauri v2 product scope](docs/product/realignment-v2.md) and
  [integration status](docs/product/implementation-status.md) (R00). Keep the central Python backend and
  share the browser/desktop UI; exclude Hub import and redefine B05 as Connector data preservation.
  Desktop, Fleet parity and live acceptance remain tracked work, not completed capabilities.

- Add an authenticated bootstrap checkpoint before persisted Dashboard snapshot reads, stable journal/principal
  cache identities, and signed replay checkpoints refreshed after each event page. Cursor rollback, missing
  retained history and changed event anchors explicitly require resnapshot while preserving drafts. SSE supports
  checkpoint/reset control events; legacy event page shapes remain unchanged. Versionless metadata shares the
  existing journal and adds no retention job (v2 §14, R04, B02/B05/T11; docs/design/dashboard-sync.md).

- Validate the complete session inventory cursor before reading session rows: versioned payload schema,
  exact SQLite-compatible key types, and a non-coerced event boundary between zero and the journal head.
  Malformed cursors consistently return 422 for empty, filtered and populated inventories; previously issued
  unversioned cursors remain valid, including paging after a restart and catching up from the original boundary.

- Bind task-effect observation history to the executing operation's persisted actor, entry point and ID.
  An unrelated RPC caller waking the shared scheduler cannot relabel other users' task events; observation
  context carries no authorization grants. Keep command/frame checks and transactional receipts unchanged.

- Refuse BAT worktree mutations for legacy reviewers whose shared creation root is unproven in the registry,
  including paths under managed roots. Preserve proven carrier behavior and observation identity resolution;
  raw CLI policy does not require a task daemon or guess a journal location.

- Observation cursor key validation ([design](docs/design/observation.md), plan v2 §14 (former §10/§11), B01/B02):
  validate history integer keys and relation [integer, string] keys in the shared decoder before any journal read.
  Reject booleans, nulls and malformed shapes with INVALID_CURSOR even for empty or fully filtered results;
  verify HTTP/MCP/CLI parity, audit inventory/discovery/event cursors, and preserve valid snapshot paging.

- Observation creation-root carriers ([design](docs/design/observation.md), plan §06/§08/§11, B01/B03/D05):
  follow explicit shares_worktree_with, equal-path legacy failovers and proven reviewer carriers through one
  parent rule. Remove the unwritten sharing key; non-sharing successors keep their own root and never inherit
  old worktree identity/history, live or in step-2 replay. Require task lead/path evidence for pathless reviewers;
  refuse BAT worktree actions without a recorded worktree even in managed roots. No new data step.

- Observation worktree maker agreement ([design](docs/design/observation.md), plan §06/§08/§11, B01/B03/D05):
  share the connector creation predicate and registry root walk with the ownership classifier, including legacy
  batc/ branches. Retain journaled connector slots or parent slots instead of minting BAT registry IDs; missing
  slots remain unknown. Refuse BAT worktree actions when successor/reviewer rows lose root markers, preserve
  existing refusals, and verify live identity/history/relations and data-step-2 replay without a new migration.

- Observation history privacy and occurrence bounds ([design](docs/design/observation.md), plan §08/§10/§11, B03):
  keep reason/previous_reason only as fixed enums; omit task and operation diagnostics, titles, prose containers
  and free-form refs recursively from history and saved snapshots while preserving recorded codes and identities.
  Explicit unknown occurrence times never match since/until; only absent metadata uses event record time.
  Report the exclusion rule in coverage, preserve unknown facts in unbounded reads, and document every producer
  in the spec, API contract and both skills. Keep source journal rows and data step 2 unchanged.

- Observation field freshness events ([design](docs/design/observation.md), plan §10/§11/§19, B02/B03):
  emit session updates when fields_stale or fixed field_evidence values change, including meta failure/recovery
  with unchanged retained values. Keep observation/activity timestamps and repeated identical polls quiet;
  preserve freshness in HTTP/MCP/CLI history without error text. Document catch-up in the API and both skills;
  the Dashboard Sessions list already reloads on these events. Keep session-specific stale/fresh reasons separate.

- Observation operation ref positions ([design](docs/design/observation.md), plan §08/§10/§11, B01/B02/B03):
  bound operation refs by both event and link sequence; saved facts read strictly before their historical position,
  while live and replayed events include their own sequence. Exclude later checkpoint runs and mutable operation
  refs from early facts, preserve unbounded catalogue membership, and bound related-event links by the captured
  feed head. Backfill stays in data step 2 with projection failures isolated.

- Observation relation closure bodies ([design](docs/design/observation.md), plan §08/§10/§11, B01/B03):
  emit the complete final command boundary and one close timestamp in the same body stored by the relation
  and its revision. Open/bind/close events match their revisions across replacement lifecycles; version-1
  replay reconstructs the final linked command while keeping unknown close times null and saved events intact.

- Observation saved-fact placement ([design](docs/design/observation.md), plan §08/§10/§11, B01/B03):
  position eventless snapshots using their own timestamps and the original journal boundary, recovering
  historical task/session/worktree links without attaching later participants. Task-source snapshots retain
  execution links even when no relation was open or time is unknown; completed backfill retries write nothing.

- Observation relation event links ([design](docs/design/observation.md), plan §08/§10/§11, B01/B03):
  attach opened/bound/closed facts only to their named relation and session, including version-1 replay;
  retain malformed-event evidence without guessed links. Task milestones and task-source projections use
  relations proven open at the event sequence, excluding former participants and preserving execution paging.

- Observation history roles ([design](docs/design/observation.md), plan §08/§10/§11/§15/§16, B01/B03):
  retain lead/reviewer and other fixed roles in relation events and nested version/backfill summaries.
  Audit the recursive whitelist to preserve bounded IDs, sequences, SHAs and boolean/state evidence, including
  delivery head-repository identity and write acknowledgement; keep prompts, PR text and commit messages excluded.

- Observation worktree relation snapshots ([design](docs/design/observation.md), plan §08/§10/§11, B01/B03):
  retain sequenced session/worktree binding intervals instead of filtering by the current worktree pointer.
  Late bindings stay outside existing `as_of` cursors; moves preserve earlier participation, with scoped ranges
  and command lists across pages. Data step 2 seeds proven original sequences or the saved binding's backfill
  link sequence once; projection failures keep the core event and expose the gap.

- Observation settlement history ([design](docs/design/observation.md), plan §08/§09/§10/§11/§15, B03/C07):
  all three metadata-settlement writers share one insert/event transaction, including acknowledged PATCH conflicts.
  Only the first inserted receipt emits history, preserving its code without PR text; legacy receipts use the
  same sanitized backfill. Delivery DDL checks use the journal's latest allocated data-step constant.

- Keep deployment rollback/retry keys stable across drawer close, live refresh and page reload for the same
  reviewed request. Let explicit PR loading proceed while environment details remain open, render only HTTPS
  provider links, and translate the new deployment labels in zh-TW. Browser regressions use a local daemon
  with synthetic providers; no production deployment is implied.

- Require both current merge and deploy scopes when resuming a combined delivery operation (Tauri v2 §18,
  C07), including its original actor. Refused resumes preserve receipts and do not contact the provider;
  authorized recovery keeps the admitted merged SHA and never repeats completed writes.

- Dashboard Delivery now groups configured recipes into repository/environment cards: selected, observed and last
  verified identities, cursor-paged history, rollback readiness and not_undone limits, fixed-identity deploy retry,
  superseded links and drift attention. Confirmations bind preview preconditions, refuse stale selections without
  automatic resubmission and survive SSE; scope-disabled buttons explain why. Receipts expand separately. A thin
  observe-only environment history HTTP read reuses saved history and cursors without provider calls. English and
  zh-TW browser flows pass at 390/768/1440 px ([design](docs/design/delivery.md), plan §09/§10/§17/§18, C07/D02–D06).

- Stopped deployment provider reads use persistent 15-second exponential backoff, capped at the configured reconcile
  interval and reset by changed provider evidence. Successful reads clear stale errors; a bad row records
  RECONCILE_FAILED and cannot starve later rows ([design](docs/design/delivery.md), plan §09/§17, D03/D05).

- Deployment reconciliation skips settled history, polls each current run/runtime and unresolved run lookup at
  `[github] deployment_reconcile_interval_s = 300` (60–86400 seconds), and preserves row versions for unchanged
  evidence. Run lookup narrows by saved send time; the environment cadence survives restart and current-version changes.

- Cancelled combined on_merge operations, including legacy history, bind the reviewed merge result on the recipe
  ref and retain the environment slot until its exact push run is terminal; reconciliation never merges or dispatches.

- Delivery legacy recovery preserves failed/cancelled outcomes and releases unsent terminal operations; only old
  successes are unverified. In-flight legacy runs hold the real repository/environment slot across recipe aliases,
  with recorded configuration fallbacks for missing environment and mode.

- Delivery Part B backend ([design](docs/design/delivery.md), plan §09/§10/§17/§18/§28, D01–D06): fixed deployment
  identities, environment generations and provider slots; saved attempts/jobs/pending-environment plus runtime evidence;
  history and saved SHA/artifact rollback through the same recipe; caller-token HTTP/MCP/CLI start/retry/rollback reads
  and writes. A read-only reconciliation loop handles cancelled runs, external reruns and version drift, without
  dispatching or resuming. Superseded generations never become current. History backfill is allocated data step 3
  after observation step 2; DDL takes no user_version. The Dashboard environment card is included in this release.
- **Breaking deployment contract:** recipes without `verification` now refuse deploys with
  DEPLOY_VERIFICATION_REQUIRED; history remains readable. Add this exact setting to `[[deploy.recipes]]`, replacing
  the reserved example URL with your configured runtime endpoint (returns repository_id/environment and required
  source_sha/healthy fields). For health-only evidence set version_required=false; at least one check is required:

  ```toml
  verification = { kind = "http_json", url = "https://deployment.example/version", version_required = true, health_required = true }
  ```

  Start, retry, rollback and combined merge now require deployment_preview's expected_environment_generation and
  expected_recipe_digest; old start callers get DEPLOY_PREVIEW_REQUIRED naming that read. Contract date stays
  2026-10-08 (same UTC day); capabilities add deployment_history/environment_generation/runtime_check/rollback_readiness.
- Issue #32 deployments: on_merge run association includes recipe branch/repository/workflow/push/SHA; start/retry/SHA
  rollback verify source reachable from the ref; combined admission rejects another base with DEPLOY_SOURCE_NOT_ON_REF.
  Cancel retains provider locks until terminal evidence. Dispatch input mapping requires source_sha and operation_id;
  a dispatch 429 is a refusal with bounded Retry-After and exact token lookup before another POST.

- Delivery acknowledged metadata conflicts ([design](docs/design/delivery.md), plan §09/§10/§15, C07): save a
  conflict settlement when an acknowledged PATCH reads back differently, releasing the PR for a fresh-digest update
  while retaining needs_attention and its audit. Resume and reconciliation use the saved conclusion without GitHub
  calls; refused readback keeps verification pending and the update lock until a successful read.

- Observation history after Delivery Part A ([design](docs/design/observation.md), plan §08/§10/§11/§15/§16,
  B01/B03): index immutable merge previews, `merge.verify` receipts and first metadata settlements using explicit
  operation resource refs. History keeps numbers, SHAs, states and codes without PR titles/bodies; preview reuse
  and repeated reconciliation add no events. Late links respect `as_of`, and data step 2 backfills saved delivery
  facts once without changing their documents. Repository/PR refs alone imply no session or worktree link.

- Delivery merge method ([design](docs/design/delivery.md), plan §09/§16, C05): execution, recorded steps and
  verification use the admitted preview's method across checks waits and restarts; current policy can block a PUT
  with MERGE_DISABLED/INVALID_PARAMS, but changing the default never changes the reviewed method.

- Delivery journal DDL ([design](docs/design/delivery.md), plan §09/§28, C04/C07 storage): create the preview,
  metadata-settlement and scope-read tables and preview index on every open with idempotent DDL, preserving
  user_version and saved previews. Version numbers belong to allocated one-time data steps; Part B follows this rule.

- Delivery metadata conflict settlement ([design](docs/design/delivery.md), plan §09/§10/§15, C07): an unresolved
  cancelled or UNCERTAIN_UNRESOLVED metadata write that still shows a third value after ten minutes now saves a
  conflict receipt and before/intended/observed refs. This releases the PR for a fresh-digest update and stops
  background reads without another PATCH or undo; resuming the original operation retains PR_METADATA_CONFLICT.

- Delivery native-stack verification ([design](docs/design/delivery.md), plan §09/§16, C05): a native stack
  created after the final scope check now stops merge verification even when its other members remain open.
  The merge receipt keeps every member's current state, head SHA and base ref; combined delivery does not dispatch.
  Membership already dissolved by GitHub remains an observation limit; unrelated open-PR head changes are not proof.

- Merge-async 400 recovery (#32 low item; [design](docs/design/delivery.md), plan §16, C04/C05): re-read the PR
  before recording refusal. An already merged reviewed head goes through normal result verification without
  claiming this operation merged it; all other states retain PR_NOT_MERGEABLE. Refused readback stays resumable,
  and neither recovery path sends a second PUT.

- Delivery Part A after #33 ([design](docs/design/delivery.md), plan §09/§10/§15/§16, C04/C05/C07): refused GitHub
  reads after merge.submit or a metadata PATCH retain needs_attention and resume with fresh readonly verification,
  including comparison and recent-PR pages. Readonly plan/verify steps do not count as sent writes; pre-write
  refusals still fail fast. Repeated resumes do not reuse a stale refusal receipt or resend the accepted write.

- Delivery operations no longer end as `failed` while GitHub is still merging or deploying (#32). A refused GitHub
  read after a merge request or dispatch was sent (an expired token's 401, a 403, a 404) now waits for a person
  (`needs_attention`) and keeps the recipe's deploy lock; before any write it still fails. A rate-limited read (403),
  a reply cut short or an unreadable body counts as no answer, and an unanswered run lookup after a 204 dispatch
  keeps looking. The GitHub token is resolved for every request, so a rotated or expiring token works without a restart.

- Managed execution confinement ([design](docs/design/confinement.md), 計畫 §06/§07/§12, A10): preserve general
  `default`/`allow_all`, add opt-in `confined`, record immutable creation evidence and separate current verification,
  and refuse confined raises, persistent approvals and mode-widening ExitPlanMode answers. Claude uses default unless
  a Linux read-only account check supports acceptEdits (BAT's acceptEdits file callback has no path check); Codex's
  sandbox reaches at most options_confirmed. Planner is read-only/never; successors inherit limits. Task Service
  behavior stays unchanged with its gap visible. Reads, bilingual forms and both skills explain the evidence.
  Failover read-back now completes the reserved successor row and sends its unsent handoff with the same message ID,
  journal hash and guards. A durable frame fence permits recovery before sending and prevents resends after a possible
  frame attempt, including crashes between a normally confirmed start and its handoff.
  Reserved-start recovery also checks normalized cwd before promotion; a different folder or a recorded permission
  mismatch stays terminal, keeps its reservation and evidence, and never dispatches a handoff on a later matching read.
  Checkpoint continuation and repair handoff remain successful when a display-only metadata read fails after their
  first instruction was accepted; creation evidence stays intact and current verification reports unknown/readback_failed.
  Reviewer starts now refuse readable permission mismatches after an ACK or during identity polling, keep terminal
  evidence and the reservation, and leave the task and start command uncertain without a review prompt. Headless
  recovery, warm reuse and Task Service failover guards also reject readable permission drift; unreadable reviewer
  start metadata remains best-effort, and engine/recipe policy is unchanged.
  Host-account checks now use a separate SSH command with a clean environment, fixed cwd, isolated absolute Python
  and absolute find. Verification requires trusted root-owned checking executables/stdlib/parents and a hardened
  passwd-derived login environment. Hosts missing these hardening preconditions report unknown: confined Claude
  starts fall back to plain default, never acceptEdits; root/process failures still refuse starts. Old checker caches
  are invalidated. Clean startup files must be installed from trusted copies before hardening; the same-UID check
  cannot detect a payload planted before those files became protected.
  Start-frame transport evidence now distinguishes pre-frame cancellation from an unsettled sent start. Cancellation
  propagates without asynchronous rollback. Checkpoint/repair, failover and Task Service recover proven-unsent starts
  under their reserved IDs; a later new-start retry cannot overwrite sent evidence with false. Retained BAT worktrees are checked and reused, and Task command evidence covers early
  preparation. Starts already handed to transport remain uncertain and are read back without another start frame.
  Cancellation tests check the original exception inside the coroutine, covering Python 3.10's loss of the message
  when awaiting a cancelled task without weakening the propagation check.
  Starting reservations now hold per-session OS flock claims through the start call. Only an abandoned unsent row
  can be reclaimed; a live process or coroutine returns START_IN_PROGRESS without changing the row, worktree or
  frame. Claims cover same-ID checkpoint/repair, Task Service and failover recovery, survive until return or
  exception, and are released by the OS on a crash. Read back later instead of blindly retrying this refusal;
  sent starts still use CONFINEMENT_START_UNSETTLED and read-back recovery.
  **A10 is not proven until the W12 live run**; no sandbox evidence-file import is included here.

- Delivery Part A ([design](docs/design/delivery.md), plan §09/§10/§15/§16/§18, C04/C05/C07):
  `github.pr.update` edits title/body with existing integrate scope and per-repository allow_pr_update opt-in,
  read-compare-write-readback and recorded conflicts; unknown PATCH replies are never resent. Immutable merge
  previews pin head/base/method, complete paginated commit ranges and affected PRs; unsupported native stacks,
  branch chains and indirect merges are refused. Merge verifies actual results, accepts normal base movement after
  submission and reports the extra commits; combined deploy uses the actual verified merged SHA. HTTP, MCP
  github_pr_update/github_pr_merge, caller-token delivery CLI, Dashboard metadata drawer/scope preview and both
  skills are aligned. Contract version remains the ISO change date 2026-10-08. The Dashboard environment card is delivered in Part B.
- Delivery Part A review fixes ([design](docs/design/delivery.md), plan §10/§15/§16, C04/C05/C07):
  uncertain metadata writes still unchanged after ten minutes settle as not applied,
  releasing the PR without another PATCH. Merge previews reuse identical documents, prune expired unreferenced
  rows and throttle event reloads for sixty seconds. Checks waits use cheap head/base reads; the final scope check
  runs before the submit step so transient read failures can resume. Verification accepts related PRs merged later
  and stops updated PR pagination at admission time.

- Observation Part A ([design](docs/design/observation.md), plan §08/§10/§11/§19, W03 remainder, B01/B02 server/B03):
  journal-only session/worktree history with a fixed sequence bound, task/session relation ranges that survive warm
  reuse, actor and version evidence, distinct unknown/loading/tab/activity states, and latest discovery scope per
  host/profile. Polls update bounded rows; host staleness is derived without session fan-out; migration facts stay
  out of the default events/SSE feed while its cursor advances. Shared worktree creation IDs match cleanup's contract.
  New HTTP routes, four MCP reads (`inventory_session`, `inventory_worktree`, `resource_history`, `resource_relations`),
  discovery via `inventory_hosts`, and CLI `inventory/history/relations`. No background Git probing. Dashboard
  timeline, filters, scope card and browser reconnect checks remain Part B.

- Operations unification Part A ([design](docs/design/operations-unification.md), plan §09/§10/§24,
  A05/A07/A09): task submit/pause/resume/stage, scoped send/verification/request-user and command reconciliation
  use OperationService with receipts committed alongside the original journal effects. Legacy task tools keep
  their results and add operation ID/status, with optional keys and control versions. Shared coordinator gates
  cover legacy session writes, client-resume, permission channels, approval/deferred raises and relay; paused,
  verifying or unreconciled tasks cannot be bypassed. The canonical fleet owner lock is acquired before journal,
  token or provider initialization; conflicts report the existing owner. No schema migration. Remaining legacy
  operations, no-key sentinel, null effect projections and A01/A05/A08 all-entry-point coverage remain Part B.
  Task sends that lose a pre-frame race to pause now fail with `TASK_PAUSED` and replay that refusal; resume
  requires a new send key. Success requires the operation's accepted/settled command receipt (A05/A07, §09/§10).
  Recovery after a cancelled/rejected command commits preserves the original refusal or local task outcome,
  without inventing uncertainty, commands or frames; legacy coordinator sends and ticks use the same rule.
  Locked verification, request-user and stage actions now recheck their state rules before the first effect,
  preserving state refusal codes and succeeded receipt replay (A05/A07). Task-bound operations now persist
  `external_refs.admission_binding` atomically with the operation: the admitted task version and targeted session
  role remain fixed even when callers omit control_version. Stale execution, including pause/resume and task
  continuation, fails CONTROL_VERSION_CONFLICT / TASK_BINDING_MISMATCH before any effect. Caller preconditions,
  request hashes and same-key replay are unchanged; old unbound operations retain their behaviour (A05/A07,
  §09/§10). Legacy task-owned answers without a prompt ID now resolve and journal the ask-user or permission
  ID before dispatch, then pass it to the existing service check. Lost replies settle through the original
  coordinator read-back after restart; a changed prompt is rejected before any frame, and a missing prompt
  creates no command. Explicit IDs, caller params, hashes and result shapes are unchanged (A07, §09/§10).
  Preliminary client-resume frames now check the full task guard without counting as a send command's effect.
  Resume failures and later pre-send refusals reject the unsent command without making the task uncertain;
  operations fail definitively with the existing code. Coordinator sends without an operation handle pre-frame
  failures inside the tick: pause/version changes cancel the command; otherwise it is rejected and the task
  stops at needs_ted with the code in its result/event, retaining the initial-lead disappearance rule. The next
  daemon tick does not resend the rejected command. Lost send replies still use the original read-back,
  including Task Service adapter sends; operation step semantics are unchanged (A05/A07, §09/§10).
  Recovery now honors a failed task_dispatch receipt even if the process stopped before recording the command
  rejection: it records rejected and replays the saved failure without a BAT read-back or task mutation. A
  coordinator tick that runs first observes the same receipt. Saved successful dispatch replies use the original
  result rules, and task/result receipts remain atomic. Unexpected errors after the prompt frame, including
  malformed replies, stay uncertain until the original read-back proves the outcome; explicit BAT refusals are
  failed steps with rejected commands (A05/A07/A08, §09/§10).
  Command receipts now commit their task_id, command_id and dispatch control_version refs in the same journal
  transaction, including prepared operator commands. Receipt replay repairs older missing links before outer
  read-back or early result/refusal returns without repeating an effect or frame. The same mechanism protects
  task submission, continuation and reconciliation reservation links; admission bindings and API shapes are
  unchanged (A05/A07, §09/§10).

- Trusted verification now treats a task pause, a control-version change, owner loss or a changed session binding
  as cancellation. Cancelled runs write no evidence and keep the task's current control instead of escalating to
  needs_ted or uncertain. Resume starts verification again, including a cancelled dependency retry; genuine
  verifier errors retain the existing needs_ted path. Dependency and start handlers preserve control refusals,
  and paused tasks retain their deadline exemption ([operations unification](docs/design/operations-unification.md),
  計畫 §09/§10, A07).

- Task-owned failover now requires an internal authority issued by the owning coordinator from its journaled
  successor and handoff reservation, with all identity and frame callbacks. A public task ID, an unissued object
  or missing callbacks cannot bypass TASK_OWNED_CONTROL_REQUIRED. Ownership and control are checked again after
  waiting for the writer lock and before the start frame. Standalone MCP/CLI failover and the existing handoff
  proof are unchanged; automatic mid-task failover remains disabled
  ([operations unification](docs/design/operations-unification.md), v2 計畫 §02/§10–12, A07).

- BAT's worktree actions (`worktree:rehydrate`, `worktree:merge`, `worktree:remove`) are refused for worktrees the
  connector made over SSH (checkpoint, conflict repair, Task Service; `NOT_A_BAT_WORKTREE`), and `batc cleanup` keeps
  those sessions. BAT has no record of them: `batc remove-worktree` on a checkpoint session re-registered the
  worktree under the session's workspace folder (a person's checkout in real use, copying its env files in) and
  removed it from there, pruning that repository. New sessions record `worktree_made_by: "connector"`; older rows
  are recognised by their `batc/` branch.
- Projects and work items (docs/design/work-items.md, plan §05/§08/§10/§19/§20, W05): connector-owned projects (tree,
  repositories, Task Service project) and work items (goal, request verbatim, acceptance, steps, state, parent,
  `derived_from`) with stable IDs, per-parent order with pins, archive and restore that keep an entry's slot, and
  links to sessions, checkpoints, operations, tasks and PRs (removals kept as history). An agent's "done" is a claim;
  `work_item.approve` accepts it against a fingerprint of the content read, editing the content asks again, and
  `work_item.continue` sends it back. New scope `approve` (separate from `manage`). Each change is one transaction
  recorded with its operation, so a re-run never applies twice. Routes `/api/v1/projects`, `/api/v1/work-items`;
  `work_items` on session and operation reads; MCP `projects_list`, `project_get`, `work_items_list`,
  `work_item_get`; CLI `batc project`, `batc item`; Dashboard Projects, project and work item screens, items waiting
  for a decision on Pending, and starting agent work from a linked checkpoint. Rules ported from Project Hub v4.68.2
  (MIT, THIRD_PARTY_NOTICES.md).
- Integration into an existing PR (docs/design/integration.md, plan §14, C01-C03): `integration.preview` pins the
  PR head and chosen results (a person's checkpoint, an agent's checkpoint run, a GitHub branch) by SHA in a bare,
  identity-checked area under the host's first managed root, lists every commit and file that would enter, and
  predicts the result; `integration.apply` composes exactly that (fast-forward, merge commit or picked commits) with
  git plumbing only, checks that nothing else entered, and pushes one exact SHA to the PR's head branch with a normal
  push using the host's git credentials. Per-source receipts; a lost push reply is read back, never resent; a
  conflict stops with nothing pushed. New scope `integrate`, config `integrate = {hosts, remote_url}` on
  `[[github.repos]]`, routes `/api/v1/integrations/...`, MCP `integration_candidates`, `integration_get`,
  `integrations_list`, CLI `batc integrate`, and an "Update PR results" panel in the Dashboard's Delivery view.
  Merging is refused while an integration of the same PR is open, and the other way round.
- Integration review fixes: a cancel requested while a step is being read back now stops before the step is sent
  again (OperationService, all actions); a lost push reply waits for a push still running on the host and re-sends
  only when GitHub says the composed commit does not exist (`PUSH_UNPROVEN` otherwise); once a push may have
  happened, a closed PR or a GitHub error no longer ends the apply as "nothing pushed"; GitHub lag after a proven push
  is a warning, not a wait; receipts of an unproven push read `unknown`; exact ref matching for `ls-remote`; no links
  inside the integration area; the preview no longer runs `git status` in an agent's folder (its config could run
  commands).
- `integration.handoff`: an apply stopped at a conflict gets a repair worktree in the integration area and a confined
  managed session that resolves it; Resume waits while the session works, accepts exactly one merge commit of the two
  sides with no uncommitted changes or conflict markers, pins it by SHA, and continues without composing earlier
  sources again (`RESOLUTION_INCOMPLETE`, `RESOLUTION_INVALID`). CLI `batc integrate handoff`; the Dashboard offers
  "Ask an agent to resolve". Shared `checkpoints.start_in_worktree` starts both checkpoint and repair sessions.

- Checkpoints no longer call BAT's `git:status` on the person's checkout: BAT runs a plain `git status`, which can
  rewrite `.git/index`, and answers `[]` on failure. Uncommitted changes are counted over SSH with
  `git --no-optional-locks status`, or reported as not observed (`dirty: null`); `dirty` is a count, not a bool.
  Source reads go through the inventory's read-only fleet. A lost `session.start` reply is only started again when
  no registry reservation exists (a null session meta no longer counts as proof), and a lost first instruction to
  a Codex session is settled from its transcript. New: `GET /api/v1/sessions/{host}/{id}/checkpoint-preview`,
  `checkpoints/{id}?live=true` (`source.advanced`), `started_from` on the session read, MCP `checkpoint_preview`,
  `checkpoint_create`, `work_continue_from_checkpoint`, CLI `batc checkpoint`, and a commit picker and note in the
  Dashboard. The Dashboard no longer requests `/favicon.ico`.
- Checkpoint continuation (review fixes): BAT's start-point check is now the `verify.start` step before the
  session starts, so a replay after the agent committed no longer ends in a false `START_MISMATCH`. Host scripts
  for one clone are serialized with `flock`, so a re-run after a lost SSH reply waits instead of racing. The clone
  keeps the source's origin only when it is a network URL, with credentials stripped. The clone and worktree
  paths are checked against the managed roots before any write (`checkpoint.managed_worktree` in the mutation
  table). A checkpoint taken inside a managed clone continues in that clone. A session that is only in the
  registry keeps its workspace, and a checkpoint without one is refused up front (`NO_WORKSPACE`).
- Checkpoint sessions are confined whatever the host's `default_permission_mode` (plan §06, A10): Claude starts in
  `default` (or `acceptEdits` with a verified host account) and Codex in the `workspace-write` sandbox with `on-request` approval. The registry records
  `write_scope: "confined"` and the permission fields from the reservation on, so a start proven by read-back, a
  resume and a Codex failover successor keep them. `session_set_permissions` refuses allow-all for these sessions
  and `approve_pending` skips them. The source conversation in the first instruction is marked as background.
- `session_worktree_status` no longer reads a checkpoint session's recorded main checkout (the person's folder)
  with BAT's `git:status`, which could rewrite their index; it says the folder is not read instead.
- New API scope `start` for starting agent sessions; `checkpoint.continue` needs it. An `operate` token (send,
  answer, interrupt) no longer starts sessions. Reissue the Dashboard token with `--scope start`; the Dashboard
  disables "Start agent work" and says why when the token lacks it.
- Dashboard: an idempotency key is reused only while the operation it created is unfinished, so the same draft
  sent again later is a new request instead of a silent replay; a session page that finishes loading after you
  navigated away no longer adds its Checkpoints panel to the next page.

- Checkpoints (docs/design/checkpoints.md): `checkpoint.create` records any session's commit, branch, uncommitted
  change count and a fixed conversation excerpt through read channels only; `checkpoint.continue` builds a
  connector-owned clone under the first managed root (its origin is the source's origin, never the person's
  folder), adds a worktree and branch at that commit over the host's SSH alias, starts a managed session there,
  checks BAT sees that commit, and only then sends the first instruction. New reads `GET /api/v1/checkpoints`,
  `GET /api/v1/checkpoints/{id}` and MCP `checkpoints_list`; the Dashboard's session page records checkpoints and
  starts work from them.
- Browser Dashboard at `/dashboard/` on the task daemon's loopback port (docs/design/dashboard.md): pending items,
  the session inventory with provenance and staleness, managed-session send (queued behind a running turn),
  interrupt and answer, a read-only view for sessions a person created in BAT, PR merge and merge-and-deploy
  buttons, and the operation log with deploy retry from the merged commit. Four static files with no build step,
  a strict CSP, and a client of `/api/v1` only.
- GitHub delivery operations (docs/design/delivery.md): `github.pr.merge` merges at the reviewed head SHA through
  the asynchronous merge API (waits on pending checks and merge queues, adopts a matching pending request,
  records the real merged SHA by reading the PR back), `deployment.start` runs a configured `[[deploy.recipes]]`
  workflow or tracks the run a merge started and only reports deployed when the recipe's deploy job succeeded,
  and `delivery.merge_and_deploy` keeps the merge when the deploy fails so only the deploy is retried. Lost
  replies are settled by reading GitHub back. New read `GET /api/v1/repositories/{owner}/{repo}/pulls/{n}` and
  MCP `github_pr_preview`.
- `/api/v1` on the task daemon (`batc serve`), sharing its loopback listener, journal and owner lock
  (docs/design/api-v1.md). Durable operations (`session.send`, `session.answer`, `session.interrupt`) with
  actor-scoped idempotency keys, steps whose intent is committed before the BAT call, and `uncertain` outcomes
  settled by read-back instead of resending (a lost send reply is found in BAT's transcript; a cancel waits for the
  read-back; `needs_attention` operations can be resumed with `POST …/resume`, `operation_resume` or
  `batc op ID --resume`). API tokens per actor and scope (`batc api-token`); the actor always
  comes from the token. A persisted session inventory refreshed through a read-only fleet, with keyset paging,
  `stale` rows for unreachable hosts and `gone` after two misses. One persistent event cursor (`api_events`) for
  tasks, operations, sessions and hosts, with SSE and `Last-Event-ID`. New MCP tools `operation_submit`,
  `operation_get`, `operations_list`, `operation_cancel`, `inventory_sessions`, `inventory_hosts`,
  `events_list`, `capabilities_get` and CLI `batc op`. MCP operation writes need `confirm=true` and the client's own
  `BATC_API_TOKEN`; they never run as the local admin.
- Sessions a person created in BAT are read-only through every tool. A new resource policy
  (`resource_policy.py`, docs/design/resource-policy.md) classifies sessions as `manual`, `connector_managed` or
  `unknown`, checks that a managed session's folder is one the connector owns (a `managed_roots` folder or a
  worktree it created) and re-reads BAT's session folder, worktree and git root before each write. The client core
  refuses any write frame without a policy grant, so send, continue, answer, interrupt, resume, permissions,
  approve_pending, relay, failover, stop, rehydrate, merge, remove and cleanup are all covered; override flags do
  not bypass it. Refusals carry a code (`MANUAL_READ_ONLY`, `WORKDIR_NOT_MANAGED`, `BINDING_MISMATCH`,
  `DESTINATION_MANUAL`, ...).
- Behaviour changes: `session_relay` targets the workspace's most recent connector-managed session and, with
  `start_if_missing`, starts a new worktree session instead of writing to a person's session; failover only continues
  connector-managed sessions (`all_exhausted` lists the others under `skipped_read_only`, outside the per-call cap);
  `worktree_merge` and cleanup merges only target the main checkout recorded at start, inside `managed_roots`
  (otherwise `DESTINATION_MANUAL` / `BINDING_MISMATCH` / ESCALATE); the fan-out planner runs in its own worktree;
  `session_start` refuses `use_worktree=false` outside a managed root and any managed-root destination whose git
  root resolves elsewhere; cleanup keeps (never stops) BAT sessions and connector sessions left in a human checkout.
- New host settings `managed_roots` and `shared_clone_worktrees` (default true: worktrees may still be created
  inside a human clone, which shares its refs). New read tool `session_policy` / `batc policy`; `sessions_list`,
  `sessions_triage` and `worktree_status` rows carry `provenance` and `api_access`.
- Every task is one Goose session on Opus 5.5 (switch off by default). The `goose-session` recipe
  prompt tells Goose to split once, aim for Grok 4.7 : Codex : Opus 5.5 = 4:2:1, and give no new work
  to a model at or below 15% weekly remaining; that is prompt guidance, not something the service
  enforces or measures. The service no longer routes per step, starts an independent reviewer, or
  fails over mid-task. Trusted-test failures return to the same session for bounded rework. A
  continuation does not create a task. Jev runs only when an orchestrator passes `executor_model`
  for already-split work.
- Routing decisions now start the BAT agent they name. Sessions use the host's actually available
  agents in the configured order: Claude Opus 5.5 (pinned `claude-opus-5-5:auto-compact-300k`, offered only
  while the host usage snapshot is fresh and under 85%/90%), then Codex. The reviewer is the other
  model family from the lead when possible; a reviewer that hits its usage limit falls back to the
  next provider instead of counting as a review. `provider_usage` records real session starts and
  quota hits. AGY `claude-opus-4-6-thinking` is not a BAT runtime and is not started.
- Warm session reuse requires the current HEAD to equal the previous task's verified commit and is limited to
  follow-ups in the same workstream (`parent_task_id`, or `continuation=true` in the same origin thread);
  independent requests start fresh from base.
- Status separates `verified` / `adopted` / `merged` / `deployed` (`delivery` block); the last three come only
  from `work_mark_stage` records. `work_submit` stores optional `context_refs` (attachments, previous message
  id, plan, commit). New read-only `python -m bat_agent_connector.gate_eval` calibration table for the minimal
  Jev gate; gate reservations now record diff size and paths. The 0.50 threshold is unchanged.
- No model calls where code already knows the answer: `work_status`/`work_result` are plain journal reads;
  PM provider choice is explicit rules by step type and stakes (no Jev classification or confidence); the
  advisory candidate pre-screen is removed; minimal submit uses rules directly when it is the only runnable
  engine (Goose live gate closed) and asks Jev only when both engines can run.
- Trusted verification runs under one deadline (start, drain, exit) in its own process group; a
  timeout kills the group and, over SSH, the remote `setsid` group, and must confirm it is gone.
- Verification has two clocks: last meaningful progress (idle budget per recipe) and an absolute cap
  (3x) from the start of the verifying phase; `updated_at` heartbeats no longer extend it.
- A trusted-test code failure goes back to the lead with a redacted output tail for bounded rework;
  missing dependencies get one lockfile install and a retry; environment problems require operator action.
- The task service no longer knows about chat delivery. The Discord publisher, its per-event outbox columns,
  the board table and `batc task-delivery` / `work_delivery_*` are removed; opening an older journal drops the
  outbox (`events.discord_status`, `events.discord_message_id`, `board`) so the old backlog can never be posted.
  New read-only milestone feed: MCP `work_events(since_cursor, limit)`, RPC `work_events`, CLI `batc task-events`.
  Transitions into `needs_ted`/`failed` now store the reason on the event.
- Milestones are pushed to an optional generic loopback JSON webhook (`[task_service.event_webhook]`), signed with
  HMAC-SHA256, in order, retried with backoff; `work_events` stays as catch-up.
- Task review verdicts are structured: only the reviewer's final message counts, and it must carry
  `{"verdict","candidate_commit","tree_hash","findings"}` for the exact candidate. Missing, invalid,
  conflicting or mismatched verdicts, and PASS with high-severity findings, become bounded rework.
  Lead `BAT-STATUS` is read from the last marker of the final agent message only.
- The direct-send fence reads the running daemon's actual journal (`task-service.json` pointer) and
  refuses sends/answers to task-owned sessions whose state cannot be read.
- The minimal task path, with a typed Jev candidate review gate, can be made the daemon's default by
  setting `BATC_TASK_DEFAULT_PATH=minimal`; without it the default stays `standard`. An explicit
  `task_path` on `work_submit` (`"standard"` for the full-review path) overrides either default.
- Claude turn markers now use BAT's exact `clientMessageId` echo (`batc-<uuid>`), with an explicit
  timestamp cursor and conservative queued-turn phases. Codex is labeled as a timestamp fallback
  because BAT does not echo that ID; Codex sends are not automatically retried after a disconnect.
- Failover successor reservation is atomic per source session and worktree. The old session is
  cleaned only after the new handoff is acknowledged.
- Automatic cleanup requires an explicit passing verification record for the current clean commit.
  `session_record_verification` / `batc record-verification` capture command, exit code, environment
  and log reference. Jev and `BAT-STATUS` remain completion claims, not test evidence.
- In-process writes now serialize per host. The remaining GUI workspace-save race and cross-process
  ownership limits are documented in `docs/design/next-gen-connector.md`.
- Split CI into pytest shards and separate frontend, browser and central UI jobs.
  Balance files by measured times, verify full coverage and preserve native checks.

- Overview now shows live project status and attention first, with compact charter cards and saved reports
  on demand. Unknown values remain explicit, and saved reports never mix in live data.
- Added a bilingual Schedule timeline with recorded baseline, forecast and actual evidence, accessible
  inspection, bounded paging, and preserved roadmap and sprint actions.

- Dependencies view: technology matrix, layered graph and impact for a captured inventory, with durable
  capture, declare and retract, and honest partial, stale and omitted states in English and zh-TW.

- Discussions open from any 'Discuss with PM' link with the record already attached, an automatic title and
  the composer focused. Threads support inline renaming, compact context chips and a paired Send / Ask PM
  composer, and proposal adoption follows the central adoptability state while preserving drafts and
  original-key recovery.

- Global screens (Pending, Operations, Delivery, Settings, first run, Projects) share the page header,
  inbox groups, an operations table with an inspector, and settings sections. Pending uses one segmented
  switch and one list per group, browser Settings shows one section at a time, and rail rows show a status
  mark instead of a status word.

- Issues & changes reads the central register: type counts and the summary appear only when coverage is
  complete, with honest pagination, a compact summary strip, list rows on narrow screens, and a processing
  timeline with Discuss and Propose change.

## 0.2.4 (unreleased)

- Turn markers: `session_send` and `session_relay` return `turn_marker` / `after_ms` (the host-side id and
  timestamp of the message just sent; fallback: the newest host timestamp before the send). `session_wait` and
  `session_read` take `after=<turn_marker>` (CLI `--after`). With it, `session_wait` returns only once the
  session has replied after that send (`done` / `event`, `turn_done=true`): a stale idle state, or the end of an
  older turn that the send was queued behind, no longer counts, and a timeout reports whether the turn started.
  `session_read` shows only messages newer than the marker and hides the live streaming tail until the new turn
  has produced output, with a note not to report older messages as the result. Callers that read right after
  relaying could otherwise pick up the previous task's last reply.

## 0.2.3 (unreleased)

- Relay mode: `session_relay` (CLI `batc relay`) forwards the human's message verbatim, followed by an optional
  brief clearly labeled as the relaying assistant's interpretation (goal, context, constraints, acceptance
  criteria). The session is told that the original is the source of truth, to fix unclear or suboptimal asks using
  its own judgment and the project plan, to state its interpretation in one line, and to ask only when the ambiguity
  is genuine and consequential. Earlier thread messages can be included verbatim. `[client] human_name` /
  `relay_name` set the names used. With no session in the workspace it reports `no_session`, or starts a Codex
  session in the main checkout when `start_if_missing=true`.
- Session-planned fan-out: `session_relay(request_fanout=N)` asks the session for a ```` ```bat-fanout ```` JSON
  plan; `fanout_plan_session` starts a read-only Codex planner in the main checkout when the main session is busy
  or quota-stopped; `fanout_from_plan` starts one worktree per planned task with the prompt unchanged and cleans up
  the planner. The relaying assistant never writes the plan.
- Status markers: sessions are asked to end every stop with `BAT-STATUS: MILESTONE <name>`, `CONTINUE <next step>`
  or `NEED-<HUMAN> <reason>`. Triage and cleanup prefer the marker over heuristics (MILESTONE counts as a completion
  claim, CONTINUE keeps the session, NEED escalates).

## 0.2.2 (unreleased)

- Merge gate is language-independent: Jev's "claims completion" question says the final output may be in any
  language (e.g. Traditional Chinese) and that `task` may be empty, and a deterministic backstop lifts the score to
  0.9 when the final output has a done/committed phrase (en or zh-TW/zh-CN, e.g. 已完成 / 已提交 / 全部通過), names a
  commit SHA and reports no blocker (尚未 / 需要你 / should I ...). Jev's diff verdict is still required.
- The final output used by the gate skips BAT's own system notices.
- Cleanup rebuilds an empty branch diff commit by commit and file by file (new read channel `git:diff-files`).
  BAT's `worktree:status` / `git:diff` return an empty diff when git prints more than a pipe buffer (~64 KB),
  because the host reads the child's stdout only after it exits; large branches then reached Jev with no diff.
- The diff secret check ignores obvious placeholders (alphabet/digit runs, `example`, `your-...`, repeated chars),
  e.g. fake webhook tokens in unit tests.

## 0.2.1 (unreleased)

- Cleanup decision `ESCALATE_TO_TED` is now `ESCALATE` (the old name is accepted as an alias).
- Per-host `codex_model`: default model for Codex sessions started or failed over on that host.
- `session_failover(instructions=..., archive_only=...)`: custom handoff steps, and successors whose branch cleanup
  keeps but never merges (to preserve superseded work).
- Fix: `worktree_remove` (and cleanup) re-registers the worktree with BAT before removing it and verifies the folder
  is gone. BAT's `worktree:remove` reports success without doing anything when its worktree manager has no record
  for the session (e.g. a failover session that reused an existing worktree).
- Cleanup treats a host timeout as KEEP (retry on the next sweep) instead of ESCALATE; `worktree:status` gets 90 s.

## 0.2.0 (unreleased)

- **Triage**: `sessions_triage` / `quota_sessions` / `batc triage` / `batc quota` classify sessions (quota exhausted,
  transient rate limit, waiting for permission/answer, working, done, error) with evidence and reset time.
- **Quota failover**: `session_failover` / `batc failover` continue a quota-stopped Claude session in Codex, in the
  same worktree when there is one, with a handoff prompt. Idempotent and capped.
- **Permissions**: per-host `default_permission_mode` (`default` | `allow_all`); `session_start` and failover apply it;
  `session_set_permissions`, `approve_pending`, and `session_answer(dont_ask_again)`. Claude sessions are never
  switched mid-turn.
- **Automatic cleanup**: `session_cleanup` / `batc cleanup` with MERGE_AND_CLEAN / CLEAN_ONLY / KEEP /
  ESCALATE_TO_TED decisions behind hard gates, per-host `auto_cleanup`, branches always kept, one escalation summary.
- **Optional Jev judgment** (`[jev]`, off without `TYPESAFE_API_KEY`): refines ambiguous states and gates merges;
  fails open for triage and safe (escalate) for cleanup. Credential-looking strings are redacted from anything sent.
- Newly allowed channels: `git:log`, `git:diff` (read); the three permission-mode setters (write);
  `claude:stop-session` (orchestrate, used only by cleanup on idle sessions).

## 0.1.0 (unreleased)

- First version. It has three tiers:
  - **read**: hosts, workspaces, sessions, reading, waiting, and worktree status. It is always on.
  - **write**: send, continue, interrupt, and answer. It is off by default and turned on per host.
  - **orchestrate**: start a worktree session, merge, and remove a worktree. It is off by default, turned on per host, and needs the write tier.
- Connections pin TLS to the host certificate and use v2 authentication only.
- An audit log records every write, and writes are rate-limited.
- Includes the `batc` CLI, the `bat-agent-connector-mcp` MCP server (stdio or loopback HTTP), and an agent skill.
