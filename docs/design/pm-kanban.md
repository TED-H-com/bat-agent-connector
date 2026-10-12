# Workspace Kanban read projection

This is the W14 foundation for v1.2 §§7.2–7.3 and 10.4. It reads the same
Connector WorkItems, command journal and evidence used elsewhere. It adds no
schema, action, assignment, dispatch queue or mutable progress record. The
[work-item contract](work-items.md), [explicit PM associations](pm-evidence.md)
and existing operation authority continue to apply.

## Shared presentation (October 12 owner feedback)

The default view groups compact cards by the central `column`: Backlog, In
progress, Waiting, Review / verify and Content approved. Archived and unknown
states remain separate when present. Column counts describe loaded cards only;
pagination remains explicit. This is the workspace board, not an inferred Sprint
membership filter. A refreshed card moves only when its central stage changes.

Cards show the title, management state, a concise quality result, known elapsed
time and a short incomplete-source indicator when applicable. Execution counts,
original snapshots, source IDs and timing uncertainty expand on demand and still
recheck access. Page WIP accounting and definitions live under Board details;
the default view does not repeat them on each card. Content approval is never
renamed Done or treated as a passing DoD. Dragging does not write state.

Planning uses readable text-only documents and optional source/compare sections.
Unresolved original requests expand their recovery controls after reload, even
when the thread has moved to Planning. Sprint work uses compact title/owner/How
rows and retains the fixed DoD. Project dispatch places recurring reference help
in Advanced settings while keeping invalid branches, read failures, permission
problems and original uncertain outcomes next to their actions. See
[shared design rules](../../desktop/DESIGN.md).

## Reads and scope

`pm_kanban.get_board(ops, principal, workspace_id, *, project_id=None, after=0,
limit=50)` returns:

```text
{version: 1, workspace_id, project_id, observed_at, journal_cursor,
 cards, next_cursor, coverage, wip, capabilities}
```

`pm_kanban.get_card(ops, principal, workspace_id, work_item_id)` returns
`{version: 1, workspace_id, observed_at, journal_cursor, card}`. Both require
`observe` and current workspace membership; a reader can use them. No owner or
writer permission is obtained by reading. An unassociated work ID returns 404.
There is no mutation or original-key replay contract in this module.

Each request reads within one central Journal transaction. `journal_cursor` is
the central API event cursor; `observed_at` is the projection time, not a claim
that any host or provider was checked then. Source timestamps retain their own
meaning. The board selects the latest **explicit** workspace binding for each
WorkItem and returns one card per work. It never selects work by repository name,
task project label, session count or parent/child inference.

Pagination uses the current binding's durable insertion sequence, with strict
integer `after >= 0` and `1 <= limit <= 100`. It is a current read, not a frozen
multi-request pagination session: concurrent rebinding may move a work to a
later page. Consumers deduplicate by WorkItem ID and replace the binding on
refresh. WIP counters apply to the returned page, with unknown counts explicit;
they are not workspace totals. Both board and standalone card UTF-8 JSON payloads
are bounded at 2,000,000 bytes, including ordinary HTTP JSON spacing, escaping,
coverage and WIP counters. RPC/MCP transport envelopes add their own framing.
If a board page is shortened, the cursor precedes the first omitted work; whole
cards are returned and no oversized middle card is skipped. A single oversized
card or envelope fails explicitly with `PM_KANBAN_TOO_LARGE` (413). Source
visibility is still checked before size errors; the limit never authorizes
access or trims evidence within an otherwise returned card.

## Cards and management stages

A card contains:

| Field | Meaning |
| --- | --- |
| `work_item_id`, `work` | Current title, state, version, pin/archive flags and source timestamps |
| `binding` | Exact immutable binding ID/sequence, source work version, project, fixed plan/requirement IDs, previous binding and creating operation |
| `column`, `completion` | Projection of the existing WorkItem state and exact-content approval |
| `flow` | Sourced management lifecycle times, current WIP, reopening count and history coverage |
| `execution` | Separately observed tasks, operations, sessions and canonical send attempts |
| `quality` | DoD and requirement acceptance remain `unassessed`; verification/deployment point to evidence |
| `latest_capture` | Original latest saved capture and currently visible evidence summaries |
| `capture_matches_binding`, `capture_matches_work_version` | Whether that saved capture still describes this exact binding/work version |
| `dependencies`, `assignment` | Explicit unknowns; existing model does not store these PM concepts |

Column mapping is `todo → backlog`, `doing → in_progress`, `waiting → waiting`.
A pending completion claim, including checked steps, goes to `review`; a valid
existing content approval goes to `accepted`. The label `accepted` means only
that a principal with the existing `approve` scope accepted the exact work
content. It does not establish DoD, testing, merged PRs or deployment. Neither a
successful operation nor terminal task changes the WorkItem column.

Archived work or work in an archived project is displayed as `archived` and
excluded from active page WIP. Archive is not cancellation and does not cancel
execution. Restoration preserves the recorded work history and elapsed age.

The saved capture is never rebound or refreshed implicitly. Its evidence IDs,
source versions, timestamps and coverage remain fixed. Current WorkItem state
and current execution observations are displayed separately from that snapshot.
Consumers must not combine old capture facts with a new binding as if a new
capture had been taken.

## WIP and trustworthy time

The management boundary is **started work until recorded content approval**.
This intentionally measures the existing work management contract; it does not
measure quality-approved delivery throughput. A completion claim stays WIP.
Returning started work to `todo` does not erase its active age. A claim expressed
by checked steps can establish pending WIP without establishing a start time.

The reader replays up to 500 relevant work events in durable sequence order,
checking original operation visibility and the atomic `management_applied`
receipt. Available original management receipts also detect a pruned intermediate
cycle whose remaining state transitions would otherwise appear consistent.
Missing/inaccessible/pruned/truncated/inconsistent history, timestamp reversal,
or a future event gives unknown timing. `updated_at` is never substituted for a
state entry time. Clock uncertainty is not converted to a zero duration.

An explicit entry into `doing` or `waiting` supplies the initial `started_at`.
Direct `todo → done` is a completion claim with unknown start/age. Rejecting that
claim with `continue` can establish the first explicit start. Rejecting a claim
after work had already started retains that earlier start and age. Reopening a
previously accepted cycle starts a new management cycle; editing accepted content
can invalidate approval and thus reopen it. A single operation emitting both
content-update and state events counts as one reopening. The current cycle has
`closed_at` and `cycle_seconds` only if approval and start times are available.

`waiting_since`/`waiting_seconds` describe the current WorkItem waiting interval.
`total_waiting_seconds` sums recorded waiting intervals across the retained work
history, including time spent waiting before approval and after reopening. Active
age includes waiting. The module does not infer a blocker or a blocking reason
from a generic waiting state; these remain unknown. Task-specific state names
remain visible separately, but their waiting/blocked duration is unknown in this
slice. Raw event notes, prompts and operation params are not returned.

`flow.sources` contains safe event sequence/original operation references.
Reopening and archive do not invent a cancellation event. Cycle time is labelled
with its management boundary; this slice does not calculate delivery throughput
or velocity.

## Execution identities and visibility

Execution reads inspect at most 50 active work links and 100 send identities per
card. Removed links and unlinked activity are not scanned. Coverage describes
unavailable/unsupported/truncated sources without exposing their private IDs.

- A linked Task Service task retains its original ID/state. Each original
  `commands.kind='send'` ID is an attempt identity, including repeated sends to
  the same session. `command_accepted` or `command_settled` is evidence that the
  send was accepted; an intent/uncertain status alone does not prove acceptance.
- An explicitly linked execution operation in the existing execution-action
  allowlist has its original operation ID. A matching succeeded `send` receipt
  with `accepted=true` and the same nonempty message ID proves acceptance. The
  overall operation may still be uncertain or failed after an accepted effect.
- An operation wrapper with an original `task_id`/`command_id` references that
  canonical command, so linking both the wrapper and task does not count twice.
  Missing/inaccessible original commands are unavailable; they do not become
  invented standalone attempts.
- Operation reconciliation/retry counts, multiple sessions, task continuation
  counters and unrelated successful operations never add execution attempts.
- Saved session observation is separate from sends. An unavailable stream cannot
  establish failure or completion and does not erase independently saved Git or
  verification evidence. Session reads grant no control.

`execution.accepted_attempts` counts only returned, visible accepted send
identities. Its coverage is bounded; zero is not proof that the work has never
executed. Attempt `completion` remains `unassessed`: accepted/settled send,
terminal task, successful operation, PR merge and quality acceptance differ.

Every task/command/operation/session and saved evidence source reuses the
evidence layer's current source permission checks, including controller and
private PM workspace boundaries. A snapshot author's visibility does not grant
another member access. No private prompt, request payload, controller secret,
reason note or hidden source ID is copied into the board.

## Deliberate remaining capabilities

Shared HTTP reads are `/api/v1/pm/workspaces/{workspace_id}/kanban` with optional
`project_id`, `after`, `limit`, and `/kanban/{work_item_id}` without selectors.
MCP/RPC expose `pm_kanban_get` and `pm_kanban_card_get`. All use the same read
projection and current workspace/source checks. Tauri accepts only these exact
routes and query fields; reads accept no operation key or actor override.

This foundation advertises read-only projection. Ready admission, assignments,
work dependencies, configurable WIP limits/resource capacity, drag mutations,
dispatch, quality assessment and throughput remain unavailable here. Parent IDs
are hierarchy, not dependencies; creators/execution actors are not assignees.
Missing capacity/cost is unknown, not zero. Additional W14 work must extend the
existing authoritative contracts instead of creating a second progress engine.
