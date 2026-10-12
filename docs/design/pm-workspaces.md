# PM workspaces and durable discussions

This contract describes the first local-data increment of the adopted
[PM v1.2 plan](../product/pm-v1.2-plan.md). The [implementation status](../product/pm-v1.2-status.md)
tracks the remaining product work. Implementation lives in `pm_workspace.py`,
`pm_discussions.py`, `pm_profiles.py`, and the shared operation/read adapters.

## Authority and identity

Python TaskDaemon and its existing journal remain the authority. PM workspaces reuse
the journal's `api_sync_metadata.server_id`, also reported by Dashboard bootstrap.
Clients connected to that central read the same records. A workspace is an explicit
namespace with its own ID, name, repository strings, membership and version; two
workspaces naming the same repository remain independent.

These additive tables do not assign legacy projects, tasks, sessions or BAT runtime
workspaces to a PM workspace. They do not consume a `user_version` data step or infer
ownership from a name, repository or path. The existing local managed central and
advanced external-central connection remain the installation paths. This increment
does not relocate a journal or provision a workspace server on another host.

Workspace IDs are `ws_` plus 32 hexadecimal characters. A controller ID is generated
by the server from its stable server namespace and the authenticated actor; credentials,
clients and role changes do not change it. Request fields cannot supply the acting
principal, an administrator flag or a claimed controller. Membership's `actor` names
the member being changed, not the caller performing the change.

The same server/actor identity now binds execution reservations and operation
authority; see [resource policy](resource-policy.md). Each session also has an
immutable creation incarnation, checked again before a BAT write. Membership or an
administrator scope does not transfer another controller's session. Legacy resources
without original controller evidence remain read-only. No PM discussion action here
sends to, resumes, interrupts or closes a BAT session; the PM runner remains separate
unfinished work.

## Membership and mutation authority

| Role | Read discussions | Append messages and record inputs | Confirm inputs, select Planning, manage membership |
| --- | --- | --- | --- |
| Reader | Yes, with `observe` | No | No |
| Contributor | Yes, with `observe` | Yes, with `manage` | No |
| Owner | Yes, with `observe` | Yes, with `manage` | Yes, with `manage` |

An explicitly authenticated administrator retains administrative authority. Creating
a workspace requires `manage` and makes its caller the initial owner. Changing or
removing membership requires the current workspace version; removing or demoting the
last owner is refused. Only owners/administrators receive the full member list.
Outsiders receive no workspace detail, including through PM operation and event reads.

Recording `user_confirmed`, confirming an existing input, or correcting a previously
confirmed input requires owner/administrator decision authority. Ordinary contributors
cannot turn their own assumptions into confirmed requirements. The recorded confirmer
comes from the authenticated operation, never from input metadata supplied by a client.

All mutations use registered `ActionDef`s and the existing OperationService. Authority
and versions are checked at admission and again in the transaction applying a new
local effect. A member removed after admission cannot perform an unapplied mutation;
a demoted owner cannot perform a pending decision. Replay/control checks use current
membership and the required `manage` scope.

The accepted operation captures authenticated actor, scopes and administrator status,
without credential values or hashes. Revoking its credential prevents future requests;
it does not retract an already accepted intent. Current membership still gates an
unapplied effect. After a local effect commits, internal recovery reads its saved
receipt first, even if membership subsequently changes. It must not report a committed
write as failed or apply it twice. Public result access remains membership protected.

## Shared API and MCP reads

All reads below require `observe`; workspace content also requires current membership.
New PM MCP tools and shared `operation_get`, `operations_list`, `events_list` require
the caller's `BATC_API_TOKEN`, including in the legacy MCP profile, and do not fall
back to the local administrator. Existing direct Fleet reads retain their previous
behavior. The same named methods are
available through the existing authenticated daemon RPC.

| HTTP GET | MCP / RPC method | Result |
| --- | --- | --- |
| `/api/v1/pm/workspaces` | `pm_workspaces_list` | `{workspaces: [...]}` for this caller |
| `/api/v1/pm/workspaces/{workspace_id}` | `pm_workspace_get` | `{workspace: {...}}` |
| `/api/v1/pm/profiles` | `pm_profiles_get` | Versioned profile catalog and available modes |
| `/api/v1/pm/workspaces/{workspace_id}/threads` | `pm_threads_list` | Threads, cursor and caller capabilities |
| `/api/v1/pm/workspaces/{workspace_id}/threads/{thread_id}` | `pm_thread_get` | Messages, inputs/history, profile and initial draft |
| `/api/v1/pm/workspaces/{workspace_id}/threads/{thread_id}/context` | `pm_context_get` | Bounded, attributed discussion context |

Workspace documents include `workspace_id`, `server_id`, `name`, `repositories`,
`version`, `created_by`, timestamps and the caller's `membership`
(`actor`, `role`, `controller_id`). Detail adds `members` only for owners/administrators.
Workspace listing currently returns all visible workspaces without a pagination cursor.

Thread lists accept `limit` (1–100, default 50) and `after` (the returned
`next_cursor`). Thread detail accepts `limit`, `before_message` and `before_input`,
with separate `next_message_cursor` and `next_input_cursor`. The current-input view
contains at most 100 entries; coverage flags expose truncation. An initial draft
returns its selected sources even when they are outside that current-input page;
unselected candidate content is capped and marked when truncated.

Context accepts `max_chars` (2,000–64,000, default 32,000), prioritizes selected inputs,
groups inputs by status and includes recent messages. The budget covers serialized
input/message content, **not the entire response envelope/profile**. Omitted IDs,
selected IDs, missing loaded slots and coverage flags remain visible. It is context
data, not execution authority or proof of complete project history.

PM membership filtering also covers global operation get/list, bootstrap, HTTP/RPC
events and SSE. Hidden events retain the underlying cursor progression. Legacy
work-item links cannot attach PM operations as a way around workspace visibility.
Existing non-PM resource visibility remains unchanged.

## Durable commands

Submit through `POST /api/v1/operations`, MCP `operation_submit`, or the existing
operation RPC/CLI. Use the shared `action`, `target`, `params`, `preconditions` and
stable idempotency-key envelope. MCP writes retain the established explicit
`confirm=true` tool argument and caller-token requirement; this is not an extra
interactive confirmation round.

Every action below requires `manage` plus the membership/decision rules above.
`W` denotes `target: {workspace_id}`; `T` denotes
`target: {workspace_id, thread_id}`.

| Action | Target | Params | `expected_version` |
| --- | --- | --- | --- |
| `pm.workspace.create` | `{}` | `name`, optional `repositories` | None; empty preconditions |
| `pm.workspace.member.set` | W | `actor`, `role` (`owner`, `contributor`, `reader`, or `null` to remove) | Workspace |
| `pm.thread.create` | W | `title` | Workspace |
| `pm.message.append` | T | `content` | Thread |
| `pm.input.record` | T | `slot`, `value`, `status`, `source_ref`; optional `applicable_revision`, `supersedes_input_id` | Thread |
| `pm.input.confirm` | T | `input_id`, `rationale` | Thread |
| `pm.thread.plan` | T | `selected_input_ids` (1–50 current distinct IDs), `rationale`, optional `title` | Thread |
| `pm.thread.plan.revise` | T | `selected_input_ids` (1–50 current distinct IDs), `rationale`, optional `title` | Thread, plus exact `expected_draft_id` |

In Planning, an owner/administrator can explicitly revise the selected source set
with `pm.thread.plan.revise`. Both the current thread version and current draft ID
must match at admission and at the journal transaction. Each revision gets a new
`draft_id` and records `previous_draft_id`, rationale, author, original operation ID
and the exact source versions. The original `pm_planning_drafts` rows stay intact;
the additive immutable `pm_planning_draft_revisions` table stores subsequent
selections. The thread points to its latest selection, and unselected current
inputs remain candidates. Omitting `title` preserves the previous draft title.

Selected sources retain their original status and attribution. A corrected or
confirmed input must be selected by its new current ID; older selections and
existing plan documents retain their historical sources. Revising the selection
does not alter any accepted plan or scope, create a plan document automatically,
or authorize execution. A later `pm.plan.draft` uses the newly selected sources.
Profile version 2 adds this explicit Planning command and describes adoption via
the workspace service; frozen run profiles and contexts are never rewritten.

Workspace names allow 120 characters; repository bindings allow 20 explicit strings
of up to 300 characters each. Workspace action results use `{workspace: {...}}`.
Discussion receipts identify the workspace, thread, version and any new message,
input or draft ID.

New PM keys are scoped by workspace and authenticated actor; workspace creation uses
the server-level PM namespace. Storage encodes that scope under the reserved
`batc:nokey:pm:` prefix; public responses retain the caller's literal key. Legacy key
semantics and records are unchanged. Same scoped key and payload return the original
operation after current authorization, without reapplying stale initial preconditions;
a different payload conflicts. Timeout callers keep the same key and read the original
operation. A new key represents a new reviewed intent, not a transport retry.

## Professional PM profiles and source memory

Profiles cover governance/value, scope, schedule, finance, stakeholders, resources,
risk and quality. Each versioned profile defines purpose, context selection, input
slots, question policy, permitted commands, outputs and exit intentions. They prefer
existing context and consequential gaps, permit zero questions, and bound a question
round to three. The profile is a contract supplied to a caller, not a running model.

| Profile | Purpose | This increment |
| --- | --- | --- |
| `idea` | Explore problems, value, alternatives and uncertainty without committing scope | Local discussion commands and explicit initial Planning transition |
| `planning` | Assemble selected direction, evidence, constraints and consequential decisions | Local discussion/input commands around a fixed initial draft |
| `refinement` | Prepare verifiable near-term engineering work | Contract only |
| `sprint_planning` | Agree iteration goal, selection, capacity and Definition of Done | Contract only |
| `follow_up` | Compare actual evidence with commitments, costs and blockers | Contract only |
| `sprint_review` | Inspect useful outcomes and stakeholder feedback | Contract only |
| `retrospective` | Own and evaluate improvements to collaboration, process and quality | Contract only |
| `maintenance_review` | Review a release baseline, findings and bounded maintenance policy | Contract only |
| `closure_reopen` | Record management intent and account for retained/in-flight work | Contract only |

Only Idea and Planning are listed in `executable_modes`, meaning local commands;
`runtime_available` remains false. All nine profiles distinguish assumptions and gaps
from decisions. Input statuses are `user_confirmed`, `repo_evidence`,
`agent_assumption`, `unresolved`, `conflicting` and `not_applicable`.

Messages preserve source words. Inputs carry source references, recorder, observation
time, applicable revision and confirmation/correction lineage. Corrections and
confirmations append new immutable records. A `message` source must exist in that
same discussion. Repository, external and statement references remain attributed
claims: `repo_evidence` is not independent repository verification, and confirming an
input does not prove its external source. Read responses expose that distinction.

`pm.thread.plan` freezes the selected input revisions and leaves the unselected
candidates recorded as Idea material. Assumptions and unresolved inputs retain their
status. There is one fixed initial Planning draft per specific thread; continuing
messages and inputs do not rewrite it. Full plan editing, multiple adopted revisions,
Git-backed PM worktrees, a PM runner, baseline adoption and dispatch remain later work.

## 使用方式

使用包含本批功能的 Connector 與共用前端，登入後從主導覽的「PM 討論」進入。
入口依後端宣告的 capability 顯示；舊版服務不會因更新前端而自動取得 PM 功能。
建立與編輯需要 `observe`、`manage` scope，既有 workspace 另依上述 membership 判斷。

先建立明確的 PM workspace，再新增 Idea 討論，保存原話、來源與尚未確認的假設。
Owner 可確認資訊，並明確選取方向產生該討論的第一份 Planning 草稿；未選內容仍保留。
確認資訊會建立新版本。若先前勾選的是舊版本，畫面會保留提示，讓使用者明確取消
舊選取並檢視新版本；不會悄悄把草稿來源換掉。
這份草稿沒有採用計畫或派工的效力，也不會接管原本的 session。中斷後先用原 key
查回操作；資料不完整時依游標補讀，不把有限上下文當成完整歷史。

目前畫面保存討論筆記，沒有連接 AI 參與者；不會自行研究 repo 或產生回覆。
外部 agent 可使用相同的 MCP 與已授權 operations 讀寫資料。

## Fixed discussion packages and PM worktree preparation

`pm.run.prepare` freezes one Idea/Planning discussion using target
`{workspace_id, thread_id}`, params `{purpose, max_chars?}` and the current thread
`expected_version`. It requires `manage` and contributor/owner membership.
The durable package retains the exact profile, purpose, message/input IDs, source
statuses, included/omitted coverage and context digest. Later notes do not rewrite
it. Preparing a package does not append an assistant message or change thread mode.

Optional `repository_binding` contains the existing repository preview's
`repository`, `host`, BAT `workspace_id`, `source_ref`, `source_sha`, `repository_id`
and `binding_digest`. This additionally requires `start`, a repository listed in
the PM workspace, and an independently configured server binding. Before any host
effect, the original operation records the PM-owned controller, workspace writer
generation and fixed worktree binding. See [PM worktrees](pm-worktrees.md).
Without this selection, preparation saves a journal-only context package.

The writer is shared by all discussions and clients of the workspace; it does not
expire merely because a process or network is quiet. Local completion releases the
writer as `prepared_manual`. Unknown external effects retain the original operation
and its recovery path. The internal PM actor is distinct from the requesting member;
an actor name alone cannot acquire the writer. New effects recheck the original
requester's membership and current generation.

| HTTP GET (under `/api/v1/pm/workspaces/{workspace_id}`) | MCP / RPC | Result |
| --- | --- | --- |
| `/runs?thread_id=…&limit=20&after=…` | `pm_runs_list` | Bounded summaries and `next_cursor` |
| `/runs/{run_id}` | `pm_run_get` | Original fixed context and receipts |
| `/capabilities` | `pm_capabilities_get` | Configured worktree destinations and runtime evidence limits |

All package reads require `observe` and current workspace membership. The shared
operation/event filters apply to preparation as well. Web and Tauri show saved
context beside the discussion and reuse the original key after a lost reply.

The current BAT adapter reports `external_tool_policy_unverified`: filesystem
read-only options do not restrict externally configured MCP tools. It therefore
offers context preparation without automatic Idea/Planning execution. Internal
native-event import and replay checks are implemented for the bounded runner;
they do not advertise a usable autonomous adapter. See [runtime evidence](pm-runtime.md).

### Initial shared-view readiness

Durable PM forms remain disabled until the complete initial workspace read has
finished, including its registered section reads. Rendering a discussion form is
not sufficient readiness for the router's POST gate. Existing drafts and uncertain
original intents remain intact during that read; recovery then uses the same
original key or acknowledged operation ID under current authority.
