# Versioned Sprint commitments and management state

W15's first domain slice records explicit Product Goal, Product Backlog, Definition
of Done (DoD), Sprint Goal, selected work and a fixed period in one workspace. It
adds management state to the existing Connector operation service. It does not
start, stop, take over or modify sessions, worktrees, work items, attempts or work
execution. An explicit selection is a management choice, not dispatch authority.

This is an additive schema with no `user_version` allocation. `install(ops)` is the
integration hook. The shared API layer installs the domain alongside its HTTP/RPC/MCP reads and client controls.

## Commitments and fixed references

Product Goal, DoD and ordered Product Backlog are independently versioned immutable
artifacts. Each workspace has a head per artifact kind, advanced with an explicit
`expected_revision_id` compare-and-set. Sprint Goal is a separate revision stream.
Changing the current Product Goal or DoD does not rewrite an existing Sprint.

Product Goal and DoD reference a fixed, historically adopted plan revision,
including its document digest and adopted scope revision. A Product Backlog
references a fixed Product Goal and ordered `pm_work_bindings`. Each selected work
binding must itself reference a historically adopted plan. Requirement acceptance
criteria remain part of those original requirement sources; they are not replaced
by shared DoD criteria.

The first slice covers explicitly bound work only. It does not pretend that every
future idea is already a Product Backlog item. Its ordered subset may be empty.
Changing plan heads does not silently rebind that work. A new backlog/selection
must use the current binding, and start checks selected bindings again. Historical
Sprint selections continue to show their original binding after a later rebind.
The binding remains a PM association (`execution_spec: not_assigned`), not an
engine-enforced coding instruction.

## Roles, modes and time

Creation records a fixed direction owner (`owner_actor`) who is an actual workspace
owner, a nonempty set of Developers with current write membership, and optionally
a Scrum Master with write membership. A creator with direction authority can
name a different existing owner; that does not authorize the creator to make later
Sprint decisions in that owner's place. Owner reassignment is not available in
this slice. A later feature must make such reassignment explicit and historical.

Only the designated actor, still an actual workspace owner, may revise a planned
Sprint Goal, start, close or cancel. Administrator credentials do not bypass this
check. Workspace administration retains its existing authority for artifact
revisions and creation; it does not silently acquire the designated Sprint role.
All mutations also require `observe`, `manage` and current workspace write access.
Read operations require `observe` and current membership under the existing
workspace policy. Missing membership follows its non-disclosing not-found rule.

Two explicit modes exist:

- `personal_iteration`: a lightweight iteration using these artifacts.
- `scrum`: **Scrum artifacts (conformance not established)**. The read model reports
  `scrum_conformance: not_established`; setting this value does not claim complete
  Scrum implementation. Planning, Review and Retrospective records are provided by
  the meeting service; Daily Scrum remains separate work. Fixed acceptance and
  DoD assessment use the [quality review service](pm-quality-review.md).
  Historical definitions retain their originally saved label.

Both modes use an immutable positive period of at most one calendar month.
Timestamps require an explicit UTC offset. The month boundary preserves the UTC
clock and clamps a missing day to the next month's last day (January 31 to
February 28/29). Start is allowed at or after the start and strictly before the end.
There is at most one active Sprint per workspace, enforced in both the transaction
and a partial unique index. Elapsed time alone does not mutate state or claim a
meeting occurred.

## Selected work and the implementation plan

Sprint creation freezes the Product Backlog and DoD revision references, roles,
capacity note and period. Selection chooses a subset of that fixed backlog with a
Developer assignee and a textual `how`. Every selection change appends a new
revision, including an empty selection before start. An active Sprint requires at
least one item; its Goal stays fixed.

The designated owner may select or remove work and assign other Developers, but
must leave a newly assigned other Developer's How empty. The owner cannot rewrite
another Developer's existing How. A Developer can select, remove and explain only
their own work; other Developers' entries and relative order must be preserved.
The original selection at start is retained separately from subsequent explicit
adaptations. Every adaptation records a rationale. No change mutates any person's
checkout or engine attempt.

Starting requires a nonempty explicit selection and current work bindings,
projects/work items and selected Developers. An optional Scrum Master must still
have write membership. An active Goal cannot be rewritten; if obsolete, its
owner cancels it with a reason. Cross-backlog expansion, role reassignment and
period changes are not available as implicit adjustments.
Once the fixed period elapses, active selection is preserved until close or
cancel; late edits cannot rewrite the ended period's final work selection.

## End of period and cancellation

`close` requires an active Sprint whose end time has arrived. Early termination
uses `cancel`, by the designated current owner, with a nonempty rationale.
Cancellation can also close an unstarted plan. Both terminal states retain the
original Goal, its started revision, selected-work history, DoD reference, actor
and reason. Optional `work_notes` may explain currently selected work only.

Close and cancel may explicitly select `quality_refs` by selected work item ID.
Each reference must resolve to the original assessment for that work binding,
adopted scope and this Sprint's fixed DoD revision. The terminal event freezes
the selected assessment and its source references. A qualified assessment yields
`quality: qualified`, `counts_as_done: true` and `qualified_increment`; an
assessment that does not qualify yields `not_qualified` and backlog reconsideration.
Without an assessment, an item remains `unassessed`, `counts_as_done: false`, with
disposition `requires_product_backlog_consideration`. This does **not** assert
that implementation or testing failed. Later assessments cannot rewrite the
original terminal event; current source access still governs its public reads.
A work item's Done claim or human implementation approval cannot stand in for DoD
and item acceptance evidence. Closing the management period cannot imply Review,
Retrospective, deployment or product completion.

Unassessed/unfinished work remains available for explicit Product Backlog
reconsideration. The service does not silently reorder that backlog, move work to
a new Sprint or modify attempts. A subsequent Sprint begins with no selected
work. Removed selections remain in immutable history.

## Actions and reads

All mutations require a bounded, nonempty `rationale` and a stable operation key.
Unrecognized parameter fields are rejected.

| Action | Target | Parameters in addition to rationale | Preconditions |
| --- | --- | --- | --- |
| `pm.product_goal.revise` | workspace | `plan_revision_id`, `text` | `expected_revision_id` (null for first) |
| `pm.dod.revise` | workspace | `plan_revision_id`, `criteria: [{id,text}]` | `expected_revision_id` |
| `pm.backlog.revise` | workspace | `product_goal_revision_id`, ordered `binding_ids` | `expected_revision_id` |
| `pm.sprint.create` | workspace | `title`, `mode`, `owner_actor`, `developers`, optional `scrum_master`, `backlog_revision_id`, `dod_revision_id`, `goal`, `starts_at`, `ends_at`, `capacity_note` | `expected_workspace_version` |
| `pm.sprint.goal.revise` | workspace + Sprint | `goal` | `expected_version` |
| `pm.sprint.select` | workspace + Sprint | `items: [{binding_id,assignee,how}]` | `expected_version` |
| `pm.sprint.start` | workspace + Sprint | none | `expected_version` |
| `pm.sprint.close` | workspace + Sprint | optional `work_notes: {work_item_id: reason}`, `quality_refs: {work_item_id: original_assessment_ref}` | `expected_version` |
| `pm.sprint.cancel` | workspace + Sprint | optional `work_notes`, `quality_refs` | `expected_version` |

Read APIs are `get_artifacts`, `get_artifact`, `list_revisions`, `get_sprint`,
`list_sprints`, and `list_history(kind=goals|selections|events)`. Paged lists use
insertion sequences (`after`, `limit` 1–100), so clock rollback does not change
history order. `get_sprint(history_limit=50)` includes the current Goal and selected
items alongside bounded initial histories and `history_next_cursors`. Clients
must follow those cursors rather than treating a partial page as complete history.
Every domain read has a 2,000,000-byte UTF-8 JSON limit, including ordinary HTTP
JSON spacing and response metadata. Artifact getters return the complete artifact
or an explicit `PM_SPRINT_READ_TOO_LARGE` (413). The limit applies to the domain
payload; RPC/MCP transport envelopes have their own additional framing.

History, artifact revision and Sprint lists return whole-record prefixes with
`coverage: {max_bytes, after, returned, has_more, byte_limited}`. `byte_limited`
distinguishes the byte boundary from a requested record-count boundary. No work
item, criterion, Goal, How, rationale or claim is shortened. A continuation uses
the last included insertion sequence. A standalone list whose first requested
record cannot fit returns the same explicit error instead of an empty page with a
non-advancing cursor. Only lightweight identifiers/sequences are fetched as a
batch; full history documents are materialized one at a time until the first
record that cannot fit.

`get_sprint` reserves the complete current definition, Goal and selected items
first. If those mandatory facts and coverage cannot fit, it returns the explicit
error. Otherwise it fills complete history prefixes in the deterministic order
Goals, selections, events under one shared byte budget. Its additive
`coverage: {max_bytes, histories: {goals, selections, events}}` reports the same
per-history fields (without repeating `max_bytes`). A history with no room for
its first record has an empty array and continuation `0`; clients can request the
standalone history from `after=0`. It is not complete or absent history. Each
history keeps its own continuation, so an earlier large record cannot hide a
later record within that same stream.

Reads do not refresh evidence, dispatch work or write audit events.

## Durability and boundaries

Every mutation rechecks current authority and compare-and-set predicates in the
same journal transaction as its state, immutable revision/event and the
`pm.sprint.apply` local-effect receipt. If the transaction fails, it all rolls
back. A replay retrieves the original receipt and stable IDs; it does not create a
second Sprint or repeat a management decision. A saved fact can be recovered
internally after the original actor loses authority, while fresh requests and
public reads still enforce current access. This local receipt model contains no
external sent-effect uncertainty or retry dispatch.

Immutable revision, Sprint-definition and event tables reject UPDATE/DELETE.
Mutable heads/current state are derived management pointers. These actions do not
alter accepted plans, PM messages, writer slots, work claims, quality assessments,
Git/PR facts or deployment evidence. They consume explicitly selected quality
assessments; they do not create assessments or grant execution authority. Current Sprint
reads advertise Review/Retro preparation when the meeting service is installed,
the Sprint actually started and the caller has current write membership and
manage scope. Preparing a meeting still requires an explicit authorized snapshot;
these flags do not claim that a meeting occurred or the Increment was verified.

## Shared reads and client

Under `/api/v1/pm/workspaces/{workspace_id}`, GET paths are:

- `/sprint-artifacts`, `/sprint-artifacts/{kind}?after=0&limit=50`, and
  `/sprint-artifacts/{kind}/{revision_id}`, for kind `product_goal|dod|backlog`.
- `/sprints?after=0&limit=50` and `/sprints/{sprint_id}?history_limit=50`.
- `/sprints/{sprint_id}/history/{kind}?after=0&limit=50`, for `goals|selections|events`.

The corresponding MCP/RPC names are `pm_sprint_artifacts_get`,
`pm_sprint_artifact_revisions_list`, `pm_sprint_artifact_get`, `pm_sprints_list`,
`pm_sprint_get` and `pm_sprint_history_list`. Query keys are exact, limits are
bounded, and current workspace membership is checked on every read. Tauri's
central bridge allows the same specific paths and query parameters.

The shared workspace home places Sprint Goal, selected work, period and DoD
together. Editors and original history expand in place. Existing durable forms
preserve unsent drafts and original uncertain operation contents/keys. Fresh
submission uses the reviewed baseline; reading changed versions alone does not
adopt them into a pending request or modify an active Sprint.
