# Fixed professional meeting records

W16 builds Planning, Sprint Review and Retrospective records over the W15 Sprint
commitments and fixed DocumentSnapshot sources. `install(ops)` registers the
local journal actions below. Public API/MCP routes use the shared API service.
Install Sprint and snapshot actions through their existing
hooks when those commands are needed; meetings do not create those artifacts as
side effects.

The implementation has no provider calls, scheduler, Git engine, work dispatch or
session control. There is no new schema `user_version` or data migration step.
All immutable records have stable operation-derived IDs, insertion sequences,
server-recorded actors and timestamps. Mutable pointers only select the current
meeting context, draft and explicit completion.

## A fixed basis for each discussion

The shared Meetings card keeps its title and Refresh on the first row. A successful
read with no meetings shows one short sentence in that card, in English or zh-TW,
without a nested empty-state card. A failed read retains the existing error, source
withdrawal and write gates; it never becomes an empty list.

Creating a meeting requires an explicit Sprint ID, its expected current version
and a saved snapshot ID in the same workspace. The context freezes:

- Sprint Goal revision and text, selected work revision and Developer How.
- Sprint roles, period, state, Product Goal, Product Backlog and DoD revisions.
- Snapshot identity, original digest, capture time and source journal cursor.
- The central cursor at which the meeting context was prepared.

The fixed Sprint and snapshot need not represent identical capture times. Both
times/cursors remain visible; the service does not assert that an older snapshot
contains the current Sprint results. An incomplete snapshot stays incomplete.
Requirement acceptance and InputRecord source statuses remain in the snapshot's
original plan. Shared DoD does not replace requirement acceptance.

`context.revise` explicitly creates another immutable context revision. It clears
the current draft pointer; old contexts, drafts and attributed notes remain in
history. It cannot silently apply a new Goal, reselect work or replace previously
recorded evidence. Completing the new context requires a note recorded in that
context. For Planning, changed Goal/selection pointers must be refreshed before
completion. Later Product Goal or DoD heads do not rewrite the Sprint's originals.

Review and Retro require a Sprint that actually started. They may prepare a
discussion during an active Sprint or record it after closure/cancellation.
Elapsed time does not manufacture a meeting. A completed Review does not close
the Sprint, and Sprint close does not imply that Review or Retro happened.

## Different purposes and outputs

All drafts are discussion proposals, regardless of the author's model or role.
Drafting a summary, generating a presentation or saving an empty meeting never
marks a discussion complete. The declared meeting kind selects a strict output
schema; Review fields cannot masquerade as Planning or Retro fields.

| Kind | Draft output fields | Derived context and completion requirement |
| --- | --- | --- |
| `planning` | `why`, `delivery_approach`, `capacity_discussion`, `dependency_notes: string[]` | `what` and `how` come from the fixed Sprint selection. Completion needs at least one selected item and a nonempty How recorded through the existing Sprint authority rules for each selected Developer. |
| `review` | `goal_review`, `outcomes: [{binding_id,observation}]`, `feedback: string[]`, `environment_changes: string[]`, `next_steps: string[]` | Outcomes may name only selected bindings. Each is an attributed discussion claim, `dod: unassessed`, `counts_as_done: false`. Completion needs feedback and an explicit next direction, which may be to retain the current direction and observe. No new work is required. |
| `retro` | `observations: string[]`, `previous_improvement_review`, optional `no_improvement_rationale` | Record collaboration, process, tool or quality reflection. Completion needs at most five owned improvements, or an explicit reason to adopt no new improvement. Neither a blank form nor invented work is required. |

Review identifies selected bindings with no recorded outcome separately. It does
not infer a qualified Increment, evaluate DoD, promote a work claim to verification
or gate release/deployment. A textual claim such as "everything is done" remains
a claim. This first meeting slice does not implement Daily Scrum, automated
quality assessment or establish complete Scrum conformance.

## Notes, decisions and follow-up

`note` stores the requester's actual text and references. It has no client-supplied
actor field. References may name the exact snapshot, a visible evidence ID in that
snapshot, the fixed Sprint Goal/selection, Product Goal/Backlog/DoD, or a note in
the same current context. Unknown kinds, foreign IDs, duplicate references and
references to a different context are rejected. Quoted command text stays text.

Drafts may contain up to ten decision proposals with a stable local `id`, `text`,
`rationale`, optional `alternatives` and `source_refs`. Completion takes an explicit
`decision_ids` subset. Only these receive `recorded_discussion_decision` and the
responsible actor; other proposals remain proposals. The original draft is never
rewritten. A recorded discussion decision has `scope_applied: false`: adopted
plans, scope revisions, backlog changes and execution continue to use their
existing commands and authority. Completing a meeting does not dispatch them.

Follow-ups contain `id`, `text`, `owner_actor`, `expected_effect`, `verification`
and optional source references. Owners must be current workspace writers at draft
and completion time. Completed records preserve the original assignment and
verification method. The maximum is five for Retro and ten for other meetings.
An assignment is an owner-recorded discussion outcome; it is not evidence that
the assignee accepted or executed a task. No WorkItem or schedule is created.

`follow_up.record` appends an attributed result with status `open`, `blocked` or
`reported_done`. Only the assigned current writer or the designated current
Sprint owner can report it. `reported_done` remains `verification_status:
unassessed`; it does not prove the expected effect. Results can explicitly cite
a newer saved `snapshot_id`, whose digest is frozen for that result. Omitting it
uses the original meeting snapshot. There is no implicit source refresh.

A follow-up is identified by workspace, meeting ID and its local follow-up ID;
the immutable completion fixes its originating draft. All result events remain
available in sequence order for the next review. This is responsibility and
result tracking, with no retries, dispatch queue, automatic rollover or new
maintenance scheduler.

## Authority and completion

Reads require `observe` and current workspace membership. Every write requires
both `observe` and `manage` plus current write membership. Readers and outsider
actors cannot draft or record notes. Contributors can prepare discussion material.

Only the Sprint's designated actor, still an actual workspace owner, may complete
the meeting. Administrator credentials do not impersonate that responsibility.
There is no inferred facilitator privilege. Completion requires an exact current
draft ID, the meeting version, a nonempty discussion summary, rationale and a
saved note in the current context. The completion basis is explicitly
`explicit_designated_owner_record`, with `attendance_verified: false`; the service
does not independently prove an external conversation or invent participants.

After completion, notes, drafts and context cannot be rewritten. Later follow-up
events are separate immutable facts and advance the management version. The
original completion, selected decisions and owner responsibilities are retained.

Reading fixed references still rechecks source authority. The snapshot service
authorizes its original evidence for the current reader. If any referenced source
is filtered, the meeting read is unavailable rather than exposing a summary that
may quote revoked information. Paged history checks each returned record's original
context; follow-up results also check their own later snapshot. Meeting listings
omit currently inaccessible entries while advancing the underlying page cursor.
No read substitutes newly captured evidence or creates an audit mutation.

## Actions

All actions require a stable operation key and nonempty bounded `rationale`.
Unrecognized fields are rejected. IDs, lists and text are bounded, and a draft
may contain at most 200,000 UTF-8 bytes. `target` and `preconditions` are exact.

| Action suffix (`pm.meeting.`) | Target | Params besides rationale | Preconditions |
| --- | --- | --- | --- |
| `create` | `workspace_id`, `sprint_id` | `kind`, `title`, `snapshot_id` | `expected_sprint_version` |
| `context.revise` | `workspace_id`, `meeting_id` | `snapshot_id` | `expected_version`, `expected_sprint_version` |
| `note` | same meeting | `content`, optional `source_refs` | `expected_version` |
| `draft` | same meeting | `output`, optional `source_refs`, `decisions`, `follow_ups`, `unresolved_questions` | `expected_version` |
| `complete` | same meeting | `draft_revision_id`, `discussion_summary`, optional `decision_ids` | `expected_version` |
| `follow_up.record` | same meeting | `follow_up_id`, `status`, `result`, optional `snapshot_id`, `source_refs` | `expected_version` |

The operation effect receipt, immutable record, mutable pointer and central event
commit in one journal transaction. Execution rechecks authority, membership,
versions and current source visibility. Transaction failure rolls back all of
them. Replaying the original committed receipt never duplicates a meeting or
decision, and internal receipt recovery can finish bookkeeping after authority
loss; fresh commands and public access still enforce current permissions.

## Read integration

`get_meeting(ops, principal, workspace_id, meeting_id, history_limit=50)` returns
the meeting state, fixed context, currently authorized snapshot, current draft,
original completion, bounded notes and follow-up events. It includes next cursors
for partial history and never silently treats a partial page as complete.

`list_meetings(..., sprint_id=None, after=0, limit=50)` lists same-workspace meeting
metadata. `list_history(..., kind, after=0, limit=50)` pages `contexts`, `notes`,
`drafts` or `follow_ups`. Limits are 1–100; non-boolean nonnegative integer insertion
sequences provide stable ordering despite clock rollback. Meeting records do not
assign writer control to sessions.

Public reads under `/api/v1/pm/workspaces/{workspace_id}` are `/meetings`
(`sprint_id`, `after`, `limit`), `/meetings/{meeting_id}` (`history_limit`) and
`/meetings/{meeting_id}/history/{kind}` (`after`, `limit`). Kind is one of
`contexts`, `notes`, `drafts`, `follow_ups`. MCP/RPC use `pm_meetings_list`,
`pm_meeting_get` and `pm_meeting_history_list` through the same read service.
Tauri permits exactly these paths and selectors; no caller identity overrides
or extra filters are accepted.


All meeting read payloads have the shared 2,000,000-byte UTF-8 JSON budget,
including HTTP's ordinary JSON separator spaces and coverage metadata. This is
the domain payload size; RPC/MCP envelopes add their own transport framing.
`get_meeting` first reserves the complete current context, authorized snapshot,
draft and original completion. It never shortens their text, source references,
criteria or claims. If those mandatory facts cannot fit, it returns
`PM_MEETING_READ_TOO_LARGE` (413), including when the snapshot itself is large.

Notes and follow-up histories then fill whole-record prefixes in that order under
one shared budget. Additive `coverage: {max_bytes, histories: {notes, follow_ups}}`
records `after`, `returned`, `has_more` and `byte_limited` for each stream. An empty
initial history may have cursor `0`; this means request its standalone history
from the beginning, not that no records exist. History pages expose the same
coverage fields at the top level and distinguish byte limits from count limits.
An oversized first standalone record returns 413 instead of repeating its cursor.
Every returned history record still checks its original context's source access;
follow-up records additionally check their own later snapshot. Lightweight
sequence rows are batched, while full documents load only until the first record
that does not fit. The byte limit does not permit bypassing source visibility.

Meeting metadata lists are similarly bounded and report `scanned` and
`visibility_filtered` in coverage. Inaccessible entries retain the prior filtering
policy and advance the underlying sequence without exposing their IDs. A visible
record that cannot fit stays first on the next page; it is never skipped. A page
must consume at least one underlying record, finish at the end, or explicitly
return 413. Reads have no journal mutations or new evidence captures.


## Original-source visibility on shared operation feeds

Meeting source checks also apply to shared operation GET/list/RPC/MCP reads and
both operation and meeting event streams. Workspace membership alone does not
permit access to an operation's quoted notes, draft output, completion summary or
follow-up results after losing the original source. New admissions save the
original `meeting_context_revision_id` in server-owned admission metadata, never
in public target, params or preconditions. Original caller requests and operation
keys therefore remain unchanged for durable form recovery.

Existing committed operations derive their original context from their immutable
operation-linked meeting records. Context revision operations check both the old
and new context; follow-up operations additionally check their own later snapshot.
Replacing the current context with a public snapshot cannot reauthorize old
private quotes. Legacy operations without either saved admission context or an
immutable result stay unavailable publicly until their source can be established;
the service never substitutes the current head. Replay/control use the same
original-source check. Internal original-effect receipt recovery is unchanged,
and source revocation never triggers another write or dispatch.

## 共用次要工具呈現

會議在討論的脈絡欄維持原位置，以類型、狀態、衝刺目標與工作名稱標籤呈現。歷史與表單按需展開；技術識別碼收在詳細資訊，窄欄可橫向捲動完整識別碼。登記後續動作依 meeting_id 與 required.follow_up_id 選取原會議與交辦項目，開啟並聚焦結果表單；導覽不會送出操作。決策文字相容 text 與 title。

建立討論以單一按鈕開啟原位置表單並聚焦第一欄；取消只收起表單，重載仍展開既有草稿或原操作。唯讀時隱藏新增動作，權限說明放在同列左側，重新讀取按鈕保持靠右。會議選單只顯示標題，類型與狀態由下方標記呈現，通用角色以本地化名稱顯示。

唯讀時不提供記錄及建立表單入口；完成討論只向符合原指定擁有者條件的人提供，後續結果依原負責人及成員權限判斷。權限降低時將入口及表單移出畫面，保留草稿與原請求，權限恢復後還原；基準過期及原操作鎖定的行為維持不變。
