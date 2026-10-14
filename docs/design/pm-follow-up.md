# Fixed Follow-up preparation

This increment makes one part of the v1.2 professional PM harness executable:
prepare a bounded, sourced discussion agenda before asking people to repeat
information. It does not invoke a model, run a meeting, dispatch work, change a
discussion mode, adopt scope, send notifications, or start maintenance scheduling.

The stored intake is a deterministic management projection, not a semantic PM
assessment. Its eight knowledge domains are governance, scope, schedule, finance,
stakeholders, resources, risk, and quality. A missing domain stays unknown and
does not create a question. Candidate ideas, assumptions, observed repository
claims, conflicting statements, and explicitly confirmed inputs retain their
original status.

## Preparing an intake

Registered action: `pm.follow_up.prepare`, scope `manage`, plus `observe` and
current workspace writer membership. Owners and contributors can prepare a
discussion; preparation grants neither decision nor execution authority.

```json
{
  "action": "pm.follow_up.prepare",
  "target": {"workspace_id": "…"},
  "params": {
    "snapshot_id": "pmsnap_…",
    "thread_id": "pmt_…",
    "governance_revision_ids": ["pmgrev_…"],
    "meeting_follow_up_refs": [
      {
        "completion_id": "…",
        "follow_up_id": "check_quality",
        "result_event_id": "…"
      }
    ],
    "previous_intake_id": "pmfu_…"
  },
  "preconditions": {
    "expected_version": 0,
    "expected_plan_version": 3,
    "expected_thread_version": 7
  },
  "idempotency_key": "the-original-request-key"
}
```

Only `snapshot_id` is required in params. Thread, selected governance revisions,
selected meeting follow-ups, and previous comparison are optional. Omitting a
meeting result selects only the original completed assignment; it never selects
the latest result implicitly. At most twenty governance revisions and twenty
meeting follow-ups may be selected. One revision per governance record and one
result per original completed follow-up are allowed.

`expected_version` is this workspace's Follow-up head, initially zero.
`expected_plan_version` is the current plan head read by the caller. A selected
thread additionally requires `expected_thread_version`; otherwise that field is
invalid. Head and membership checks run at admission and in the atomic effect.
Concurrent preparations from one head cannot overwrite one another. A historical
snapshot with an adopted baseline is valid; its accepted document is never
silently replaced with the current plan.

The result contains `workspace_id`, `intake_id`, the new workspace `version`,
`document_digest`, `agenda_count`, and `execution_authorized: false`. Original-key
replay returns this original result even after the plan or thread advances,
subject to current membership and original source visibility. Internal recovery
reads the committed local effect receipt and does not create a second intake.

## Frozen document

Each append-only `pm_follow_up_intakes` row records its operation, actor, time,
workspace version, global insertion sequence, canonical document and digest.
No schema data step or `user_version` is allocated.

The document contains:

- `as_of` and `journal_cursor`: preparation time and central journal position.
- `snapshot_ref`: exact snapshot ID, digest, snapshot time and journal position.
- `plan_ref`: that snapshot's accepted document revision, scope revision,
  document digest and original plan head version.
- `thread_ref`: selected thread ID and exact version, or null. The latest 100
  current InputRecords are selected by durable insertion sequence. Truncation is
  explicit; messages are not interpreted or presented as answers.
- `selected_governance_refs`: original record identity, kind/root cause, exact
  revision/version/digest/document, original context, actor, time and operation.
- `selected_meeting_follow_up_refs`: exact original completion and assignment,
  completion digest, original context snapshot, recorded decisions and optional
  exact result event with its independent snapshot.
- `knowledge`: eight domains containing fixed `baseline_inputs`, separately
  attributed `supplemental_inputs`, `answer_links`, and original baseline
  `coverage`. Unknown slots remain in `unmapped_inputs` and are not assigned a
  domain by prose similarity.
- `issues`, `agenda`, `changes`, original `access_refs`, and explicit `coverage`.

The snapshot's original work binding revisions and captures remain referenced
through that immutable snapshot. Preparation does not capture new task state,
refresh remote sources, or treat different capture times as one current state.
When the snapshot includes several historical states of the same execution, one
issue lists all selected observations and says `multiple_captured_states`.

### Reusing answers without rewriting history

Only explicit `InputRecord.supersedes_input_id` chains establish input continuity.
A successor that is `user_confirmed` with an actual `confirmed_by` establishes an
`explicit_user_confirmed_supersedes` answer link. Reading a plausible answer in a
message, seeing the same slot twice, or receiving a repo claim establishes no
such link. Traversal is bounded; missing/truncated ancestry is marked incomplete.

An accepted unresolved input remains unresolved in `baseline_inputs`. Its later
confirmed successor appears separately; the original issue can say
`answer_recorded` and leave the agenda. The link always states
`accepted_plan_changed: false`. Neither confirmation nor Follow-up preparation
adopts a revised plan.

A selected governance direction decision is a recorded answer for its own stable
change record. `adopted_direction` is not plan adoption. Any original adoption
receipt remains separately attributed. Completed meeting decisions provide
discussion context; they do not automatically answer or verify a follow-up.
`reported_done` and `reported_resolved` remain unverified reports.

### Stable issues and bounded agenda

Issue identity is derived from workspace plus explicit source family:

- Input issue: the earliest reachable immutable supersedes ancestor.
- Governance issue: the stable governance record, which preserves its explicit
  root-cause identity. Separate justified root causes sharing evidence stay separate.
- Meeting follow-up: original completion plus assigned follow-up ID.
- Execution observation: original source kind and source ID. All fixed fact
  variants selected in this snapshot are retained.

Issue fingerprints include the selected source versions and reported status.
Comparing an explicitly selected prior intake produces `added`, `changed`,
`unchanged`, and `no_longer_selected` IDs. Absence is not resolution. Re-analysis
of unchanged sources preserves identities and fingerprints; it does not create
new reminder objects. No notifications exist in this slice.

At most three agenda entries reference issues. Deterministic order is:

1. An explicitly recorded governance `review_at` has been reached.
2. A selected central state explicitly needs attention, or a meeting result
   explicitly reports blocked.
3. An explicitly conflicting InputRecord.
4. An explicitly unresolved InputRecord.
5. An explicitly reported completion/resolution with verification unassessed.

Stable issue IDs break ties. Each agenda entry exposes `ordering_basis`, source
references and `is_user_question: false`. This is a mechanical discussion order,
not an invented importance score, business priority, deadline or assessment of
financial impact. Business priority remains `unassessed`; no extra questions are
invented to fill three slots. Recorded direction decisions and confirmed answer
links are retained in issues but do not re-enter the agenda as unresolved inputs.

The remaining professional PM work—semantic root-cause analysis, scope impact,
tradeoff options, stakeholder-specific questions, quality assessment and
prioritized recommendations—still needs an authorized reviewer/runtime using
these fixed sources. This slice does not advertise those capabilities as done.

## Reads and current visibility

```python
head(db, workspace_id)  # internal {version, intake_id}
get_intake(ops, principal, workspace_id, intake_id)
latest_intake(ops, principal, workspace_id)
list_intakes(ops, principal, workspace_id, after=0, limit=50)
```

Get/latest return `{version: 1, workspace_id, head_version, intake}`. The intake
contains `sequence`, `intake_id`, `workspace_id`, its own `version`,
`created_at`, `created_by`, `operation_id`, `document_digest`, and `document`.
An empty workspace returns `intake: null`, `head_version: 0`. Latest means durable
insertion order, even after clock rollback. An inaccessible latest intake is
unavailable; an older one is not substituted silently.

List returns `{version: 1, workspace_id, head_version, intakes, next_cursor,
coverage}`. It contains metadata only, never repeated full documents. Limits are
1–100 and nonnegative sequence cursors. Hidden entries are omitted while the
cursor advances over them; coverage states visibility filtering and scan count.

All reads require observe and current membership. Each read rechecks the original
snapshot evidence, the selected governance operation's original context, and
both the meeting completion's original context and selected result's snapshot.
It cannot reuse the author's former access or a newer public context. Prior
comparison access is flattened into the new intake (maximum 200 references),
without recursive live head traversal. If any original source is unavailable,
the whole derived intake is withheld because text could quote that source.

The integrator must connect `operation_visible(db, principal, operation)` before
exposing action results/params through shared operation reads, replay, RPC or
feeds, and `event_visible(db, principal, event)` for `pm_follow_up` events.
The hooks reload the complete original operation and use its immutable admission
source binding. This also protects admitted operations before an intake exists.
Internal effect recovery remains separate from public source visibility.

The complete GET wire wrapper must fit **2,000,000 UTF-8 bytes**, measured with
the actual JSON separators and escaping, not character count or compact stored
JSON. Admission to storage reserves maximal integer widths. Oversized selections
fail atomically, with no partially truncated agenda or intake. Bounded thread
selection retains coverage and original IDs rather than claiming complete knowledge.

## Shared service entry points

The service advertises `features.pm_workspaces.follow_up_prepare` when the
`pm.follow_up.prepare` action is registered. This is fixed agenda preparation;
the Follow-up PhaseProfile remains unavailable as a discussion runtime mode.

| HTTP GET within `/pm/workspaces/{workspace_id}` | RPC / MCP read |
| --- | --- |
| `/follow-ups?after=0&limit=50` | `pm_follow_up_list(workspace_id, after=0, limit=50)` |
| `/follow-ups/latest` | `pm_follow_up_latest_get(workspace_id)` |
| `/follow-ups/{intake_id}` | `pm_follow_up_get(workspace_id, intake_id)` |

Every entry requires observe and current membership. Only the list accepts
`after` and `limit`; empty, repeated and unknown query parameters are rejected.
The native bridge accepts these same bounded read routes. Preparation goes
through the existing central operations endpoint and original idempotency key.
There is no separate Follow-up queue or native execution path.

Shared operation reads/lists and events delegate to the domain's original
admission hooks, including admitted operations without a completed intake.
Withdrawal of a selected governance or meeting source therefore withholds the
derived intake and original operation through HTTP, RPC, MCP and event feeds.
Internal effect receipts are retained for recovery; public replay remains
subject to current membership and those original source references.

## Client source withdrawal and partial coverage

An unavailable latest intake is not an empty history and is not replaced by an
older intake. For the specific `PM_FOLLOW_UP_SOURCE_UNAVAILABLE` response, the
client clears the previous facts, rechecks the workspace and the signed-in
actor's current membership, then rereads bounded history metadata and the plan
head. It can show that readable history and allow an authorized writer to prepare
a new intake from readable sources. A selected historical intake that loses its
sources stays selected with an unavailable message until the user changes it.
No stale document is restored by the metadata read.

Unknown errors, generic 403/404 responses, failed membership rechecks and malformed
scope responses keep the complete view unavailable. A late response cannot
restore an older read after a newer failure. Draft selections and any ambiguous
operation request/key remain unchanged; an unsent draft requires explicit CAS
rebase when its saved head differs, while recovery checks the original request.

The client displays snapshot `projects_truncated`, `work_truncated`,
`evidence_truncated`, and `bytes_truncated` warnings next to the agenda, together
with the selected thread's truncation warning. An empty agenda still discloses
that omitted source records were not assessed. These are the fixed preparation's
saved coverage flags; rendering does not query newer facts or infer complete
coverage when a flag is absent.

## 共用次要工具呈現

後續追蹤只展開有輸入的知識面向，其餘以一行說明尚無基準輸入的面向數；不把未知顯示成零。議程附近保留部分涵蓋狀態，省略來源的原因、固定版本、摘要值與比較身分收在詳細資訊。準備與分析表單按需開啟，沿用原操作、草稿及請求恢復。兩处掛載的組合調整仍待後續討論介面工作。

準備討論以單一按鈕開啟表單並聚焦快照選單；取消只收起表單。已選來源及未完成的原操作在重載後仍展開，僅自動綁定基準版本不算使用者草稿。唯讀時隱藏準備動作，權限說明放在同列左側，重新讀取按鈕保持靠右；尚無基準輸入的面向依原領域順序列出名稱。
