# Selected external context and a published code basis

This read previews one association: retained external conversation context selected
by the requester, and an independently verified published code basis. It does **not**
prove that the external CLI produced that commit, and the entry is not a verified
Result. 中文：由請求者選取的保留外部內容，搭配獨立驗證的已發布程式碼基礎；
這不代表外部 CLI 產生該 commit，也不將歷史項目視為已驗證的 Result。

The Python Connector is the only authority. The preview creates no Work, Attempt,
package, relationship, operation or receipt. It writes no journal or table rows,
runs no Git command, clones nothing, collects no history and calls no model.
It reuses `repository_sync.source_preview` unchanged: it makes one read-only
`workspace:load` request through the read-only Fleet client, in the same way as
the existing published continuation preview. The shared client may resend that
read-only frame after a busy reply, up to its existing bound of three attempts
(`client.py`, `_invoke_checked`). Only `workspace:load` invoke frames are sent.
The client records transport failures in the Connector's own error log: the log
file and the lock file the logger keeps next to it. No other file is created or
changed by this read. Its retry and logging behavior are unchanged. 中文：預覽發出
一次唯讀請求；共用 client 遇到 busy 回覆可在既有三次嘗試上限內重送同一種唯讀
frame，並記錄傳輸錯誤日誌（包含日誌檔及旁邊的 lock 檔）；不變更其他檔案。
The exact workspace folder remains part of the existing binding digest;
there is no cache, caller assertion or reduced digest. Manual and foreign sessions
and worktrees remain read-only.

## Read surface

- HTTP: `POST /api/v1/pm/workspaces/{workspace_id}/history-continuation-preview`.
  No query parameters.
- Daemon RPC and MCP: `pm_history_continuation_preview(workspace_id, body)`.
  Exact outer fields; no action envelope, operation key or confirmation.
- Tauri: the same POST path and body through the existing central API bridge.
  The bridge rejects extra fields, wrong methods, queries and operation keys;
  Python resolves grants, retained content and the published basis.

MCP exposes `workspace_id` and an opaque object `body`; it does not publish a
typed schema for the nested body fields. The central Python validator is their
only authority. Before that call, the MCP adapter may parse a JSON object string
or refuse a non-object body with its own English validation text, which may repeat
the caller's input. Those adapter errors are not the central bilingual catalog.
中文：MCP 的 body 是不透明物件，內層欄位由中央驗證；adapter 的前置英文錯誤
可能重複呼叫者輸入，不屬於中央雙語 reason catalog。

The existing HTTP API request-body limit is 200,000 bytes; an oversized body
receives HTTP 413 / `TOO_LARGE`. The daemon RPC envelope limit is 100,000 bytes,
including `method` and `params`; an oversized envelope is rejected before the
ordinary RPC error envelope and callers can receive a connection reset. MCP uses
that RPC transport and inherits its limit. A body within every field's character
limit can still exceed either byte limit, especially with escaped Unicode JSON.
These transport limits and the RPC reset behavior are unchanged by this read.
中文：HTTP body 上限為 200,000 bytes（超限回 413 / `TOO_LARGE`）；RPC envelope
含 method/params 上限為 100,000 bytes，超限可能直接中斷連線，MCP 亦受此限制。
符合各欄位字數限制，不代表序列化後符合傳輸位元組上限。

The caller needs `observe`, explicit current PM workspace membership and the
current named reader grant for the selected history source. Administrator status
substitutes for neither membership nor the reader grant. An unavailable source
returns the generic `PM_HISTORY_SOURCE_UNAVAILABLE` before repository or project
details are read or returned. The grant generation and original retained reference
are rechecked after the asynchronous reads, including on failed provider replies.
Withdrawal followed by regrant invalidates an in-flight preview. Known BAT/GitHub
read transport failures return bilingual `REPOSITORY_SOURCE_UNAVAILABLE`; a withdrawn
grant still takes precedence over that proof failure.

The exact body has these fields (only `model` is optional):

| Field | Contract |
|---|---|
| `context_ref` | Complete `external_history_ref/v1`, as returned by the existing fixed excerpt preview. |
| `code_target` | Exactly `repository`, `host`, BAT `workspace_id`, `source_ref`; each at most 256 UTF-8 bytes. An explicit configured binding and a `refs/heads/` branch (at most 200 ASCII characters after the prefix) are required. No inferred target when bindings are ambiguous. |
| `expected_source_sha` | Explicit reviewed full lowercase 40-hex branch head SHA. A changed head is refused, never substituted. |
| `project_id` | Existing active project explicitly bound to this PM workspace, including the selected repository. |
| `requirement_ids` | 1–50 distinct IDs from the existing adopted scope, using its actual `pm_plans.ITEM_ID` grammar: `[a-z][a-z0-9_.-]{0,79}` (at most 80 ASCII characters). |
| `new_work` | Exactly `title`, `goal`, `acceptance`, with existing Work field validation (title up to 120 characters; goal and acceptance up to 20,000). No Work is inserted. |
| `instruction` | Nonempty instruction up to 4,000 characters, separate from the retained excerpt. |
| `agent` | Explicit BAT agent `claude` or `codex`; no native alias or adapter fallback. |
| `delivery` | `pull_request` or `reviewable_changes`. |
| `model` | If present, nonempty string up to 256 characters; absence uses the existing BAT selection semantics. |

All text must be valid Unicode scalar text, without unpaired surrogates. Character
bounds count codepoints, not UTF-16 units. Blank means empty or entirely Python's
Unicode whitespace set: U+0009–000D, U+001C–001F, U+0020, U+0085, U+00A0, U+1680,
U+2000–200A, U+2028–2029, U+202F, U+205F and U+3000. The bridge uses the same set.
`server_id` (128 codepoints), `model` and all target fields refuse blank values,
C0/C1 controls (U+0000–001F and U+007F–009F), and U+2028/U+2029 line separators.
Goal and acceptance retain existing Work text rules: empty text is allowed,
CRLF/CR is counted after conversion to LF, and C0 controls other than tab/LF/CR
and DEL are refused. Instruction also uses those Work text rules, requires
nonblank text and has a 4,000-codepoint raw bound. Title follows the Work title rule:
the raw input must be nonblank and at most 120 codepoints, then Python whitespace is
stripped from both ends and the remaining 1–120 codepoints must not contain C0,
U+007F, U+2028 or U+2029; it is the one single-line field that accepts C1 controls,
because Work accepts them. 中文：title 先檢查原始輸入非空白且至多 120 個 codepoint，
再去除兩端 Python 空白；剩餘 1–120 個 codepoint 不得含 C0、U+007F、U+2028 或
U+2029；依 Work 規則，title 是唯一接受 C1 控制字元的單行欄位。Retained context
is never normalized by these request-field checks.

URLs, cwd, paths, credentials, native session IDs, checkpoint IDs, ownership or
producer claims, extra nested fields and alternate schemas are refused. Text in a
retained entry that looks like a SHA, path or checkpoint is never used as proof.
Only the two currently supported JSONL profiles can supply retained context;
inherited/fork/subagent identities remain subject to the existing collector's
fail-closed identity checks.

## Fixed descriptor

`external_history_published/v1` has exactly:

```json
{
  "schema": "external_history_published/v1",
  "server_id": "11111111-1111-4111-8111-111111111111",
  "workspace_id": "ws_11111111111111111111111111111111",
  "context_ref": {
    "schema": "external_history_ref/v1",
    "server_id": "11111111-1111-4111-8111-111111111111",
    "workspace_id": "ws_11111111111111111111111111111111",
    "source_id": "synthetic-history",
    "entry_id": "shi_22222222222222222222222222222222",
    "source_digest": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "content_digest": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "selection": {"kind": "entry"},
    "excerpt_digest": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
  },
  "basis": {
    "repository": "example/project",
    "repository_id": 123,
    "host": "example-worker",
    "workspace_id": "example-workspace",
    "source_ref": "refs/heads/main",
    "source_sha": "dddddddddddddddddddddddddddddddddddddddd",
    "binding_digest": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
  },
  "association": "requester_selected_context_and_basis"
}
```

The IDs/digests above are illustrative synthetic values, not proof. Both outer
identities must match the context reference and the current Connector/workspace.
The exact retained reference resolver verifies source binding, entry/content and
excerpt digests and selection. Selection is either `{kind:"entry"}` or exactly
`{kind:"text",part_index,start_codepoint,end_codepoint}` with half-open Unicode
codepoint offsets. Text is not normalized: CRLF, combining characters, emoji and
whitespace remain exact. Existing 16,000-codepoint, 128-part and 128-KiB escaped
excerpt bounds apply. At least one nonempty text part is required; marker-only
and empty entries are refused. Clipping and omissions remain explicit.

The basis uses the existing published proof: positive numeric repository identity,
exact configured repository/host/BAT workspace binding, exact branch head SHA and
the existing config/workspace-folder/repository-ID digest. Older ancestor commits,
checkpoint descriptors and free-standing execution contexts are unsupported.
The resolver re-proves an existing descriptor and refuses a changed SHA or binding;
it never edits it to follow the source's head.

`descriptor_digest` is SHA-256 of sorted-key compact JSON, encoded as UTF-8 with
`ensure_ascii=False`, no NaN and no Unicode normalization (the existing history
canonicalization). It detects changes; it is not a signature or an authorization.

## Response and readiness

The response contains `descriptor`, `descriptor_digest`, exact `excerpt`, explicit
`target` (repository, execution host, BAT workspace), `history_complete:false`,
`execution_status:"unknown"`, `producer_attribution:"unproven"`, `association:"requester_selected_context_and_basis"`, bilingual
`meaning`, `readiness`, `preconditions` and a bilingual `notice` that continuation
is not yet available. Old Connector/PM workspace/source/entry identities remain in
`context_ref`; the separately verified execution basis remains in `basis`.

Readiness is advisory: `ready` means the selected requirements pass the existing
pure thin-package checks and the requester is a workspace owner with `manage` and
`start`. Otherwise it is `blocked` with stable bilingual reasons. Checks contain
stable check names and passed/blocked status. They do not reserve capacity or
claim runtime, provider, delivery or owner acceptance. The current project version
and adopted scope revision are returned as preconditions. They, the original source
grant and the repository configuration are reread after asynchronous proof.
No dummy Work is inserted: `compose_thin_package` and `document_readiness_checks`
are reused without modifying Work-start behavior or receipts.

Repeating a lost preview read obtains proof again and has no receipt to recover.
A later changed branch head fails against the original requested SHA. There is no
schema migration, new DDL, action or executable capability flag;
`relationships.derive_ready` remains false.

A continuation operation is planned and is **not yet available**. This contract
implements only descriptor validation, resolution and the preview read.

## Bilingual reason catalog

Reasons in successful responses have `{code,message:{en,"zh-TW"}}`. Refusals retain
the existing API/RPC error envelope, stable code and HTTP status, with both language
messages. Source-unavailable precedence also applies when a provider read fails.
The catalog is maintained in `pm_history_continuation.REASONS`.

| Code | English | 繁體中文 |
|---|---|---|
| `PM_EXTERNAL_CONTINUATION_UNSUPPORTED` | Select retained context and an explicit published branch head. | 請選取保留的內容及明確已發布的分支頂端。 |
| `PM_HISTORY_SOURCE_UNAVAILABLE` | History source is unavailable. | 歷史來源無法使用。 |
| `PM_HISTORY_REFERENCE_INVALID` | Select one bounded retained entry or text range. | 請選取一則範圍有限的保留項目或文字範圍。 |
| `PM_HISTORY_REFERENCE_CHANGED` | The fixed history reference differs. | 固定的歷史參照已不符。 |
| `PM_EXTERNAL_CONTEXT_UNUSABLE` | Select context containing nonempty text. | 請選取包含非空文字的內容。 |
| `PM_EXTERNAL_CONTINUATION_CHANGED` | Preview the current project, scope and binding again. | 請重新預覽目前的專案、範圍及綁定。 |
| `PM_EXTERNAL_CONTINUATION_OWNER_REQUIRED` | A workspace owner with manage and start is required for new Work. | 建立新 Work 需要具備 manage 與 start 權限的工作區擁有者。 |
| `PM_EXTERNAL_CONTINUATION_NOT_AVAILABLE` | Continuation is planned and is not yet available. | 延續操作已規劃，目前尚未提供。 |
| `PM_EXTERNAL_CONTEXT_ASSOCIATION` | Requester-selected context and independently verified published code basis; producer attribution is unproven. | 由請求者選取的內容與獨立驗證的已發布程式碼基礎；尚未證明產生者歸屬。 |
| `PM_WORK_START_NOT_READY` | The selected requirements are not ready for a thin work package. | 所選需求尚未準備好建立精簡工作套件。 |
| `PM_PROJECT_BINDING_REQUIRED` | Select an active project explicitly bound to this workspace. | 請選取明確綁定此工作區的有效專案。 |
| `PM_ADOPTED_PLAN_REQUIRED` | Adopt a scope before previewing continuation. | 請先採納範圍，再預覽延續。 |
| `PROJECT_REPOSITORY_CHANGED` | The selected repository does not belong to this project. | 所選儲存庫不屬於此專案。 |
| `REPOSITORY_NOT_BOUND` | Select an explicitly bound repository, host and workspace. | 請選取明確綁定的儲存庫、主機及工作區。 |
| `REPOSITORY_HOST_UNAVAILABLE` | The configured execution host or read client is unavailable. | 設定的執行主機或讀取用戶端無法使用。 |
| `REPOSITORY_WORKSPACE_CHANGED` | The exact bound workspace is unavailable. | 確切綁定的工作區無法使用。 |
| `REPOSITORY_SOURCE_UNAVAILABLE` | The exact published branch head could not be proved. | 無法證明確切的已發布分支頂端。 |
| `REPOSITORY_ID_CHANGED` | The published repository identity differs. | 已發布儲存庫的身分不符。 |
| `REPOSITORY_REF_CHANGED` | The branch head differs from the selected SHA; review the original selection. | 分支頂端與所選 SHA 不符；請檢查原始選取。 |
| `REPOSITORY_BINDING_CHANGED` | The repository or workspace binding differs. | 儲存庫或工作區綁定不符。 |
| `INVALID_REF` | Select an explicit refs/heads/ branch. | 請選取明確的 refs/heads/ 分支。 |
| `INVALID_PARAMS` | Supply the exact bounded preview fields. | 請提供確切且範圍有限的預覽欄位。 |
| `FORBIDDEN` | Preview requires observe and explicit workspace membership. | 預覽需要 observe 權限及明確的工作區成員資格。 |
| `PROJECT_NOT_FOUND` | The selected project is unavailable. | 所選專案無法使用。 |
| `PROJECT_ARCHIVED` | The selected project is archived. | 所選專案已封存。 |
