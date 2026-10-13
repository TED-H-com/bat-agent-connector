# 即時專案總覽與討論來源

本合約由 Python Connector／Task Service 實作。Web、Tauri、RPC、MCP 共用中央資料與
權限；讀取不呼叫 provider、不掃描檔案、不啟動工作，也不更新 DocumentSnapshot。

## 即時總覽

`GET /api/v1/pm/workspaces/{workspace_id}/overview`、RPC／MCP `pm_overview_get(workspace_id)`。
需 `observe` 與目前 workspace membership。Capability 為 `features.pm_overview.version = 1`。
不接受快照 ID 或 refresh／path 等參數。時間沿用鄰近 PM API 的 UTC epoch seconds；
里程碑日期為 `YYYY-MM-DD`，不附加推測的時區或時刻。

回傳 `version: 1`、`workspace_id`、`as_of` 及下列欄位：

| 欄位 | 合約 |
| --- | --- |
| `lifecycle` | `{stage, since}` 或 null；只使用可見的中央生命週期紀錄 |
| `plan` | `{revision_id, number, title, goal, adopted_at, adopted_by}` 或 null；number 是原 revision 的 journal sequence，不是已載入筆數 |
| `progress` | `{basis: {revision_id}, requirements, accepted, in_review, in_progress, blocked, not_started, complete, unknown_reason}` 或 null |
| `next_milestone` | 依已記錄 forecast／baseline 日期排序的第一個未驗收里程碑，或 null |
| `milestones` | 最多 20 筆 `{milestone_id, title, baseline_date, forecast_date, delta_days, status, ref}`；另附 `milestones_truncated` |
| `attention` | 最多 20 筆 `{key, kind, title, severity, since, context, ref}`；先嚴重度再年齡，穩定 key 打破同序；未知年齡排最後 |
| `attention_counts` | blocker／decision／deviation／risk 各自的可見來源筆數；該種類來源不完整時為 null，不能以顯示的 20 筆當總數 |
| `attention_truncated` | 來源掃描未完成或顯示上限截斷時為 true |
| `recent_deliveries` | 最多三個不同 work ID，依中央證據時間排序，狀態為 accepted／merged／deployed／rolled_back；另附 `recent_deliveries_complete` |
| `coverage` | `{complete, sources, basis, network_checked: false}`；每一 source 有 `complete`、`next_cursor` |
| `unknown` | `[{field, reason}]`；缺資料保留 null 與機器可讀原因，不補零 |

Goal 只取已採用文件中 status 為 `user_confirmed` 或 `repo_evidence` 的 value／goal／problem input，
換行改為空白；只有未確認輸入時為 null 與 `goal_unconfirmed`，完全缺少時為 null 與 `no_explicit_goal`，不從標題或任意 Markdown 猜測。沒有採用計畫時 `progress: null`，
原因 `no_adopted_plan`；來源撤回則為 `source_unavailable`。

進度分母僅是已採用版本的 distinct requirements。每條需求只進一類，優先順序為：

1. 同一 plan 的未解決 issue／dependency 治理紀錄明確列為受影響需求 → blocked。
2. 同一 binding／scope 的目前 qualified quality review 涵蓋每個原 acceptance input → accepted。
3. 有該需求的品質審閱或 work 為 done → in_review。
4. 明確相連的 work 為 doing／waiting → in_progress。
5. 其餘可見需求 → not_started。

`complete` 表示分類來源掃描完整，**不是專案已完成**。工作／治理／品質來源不完整時，
分母仍可來自完整採用文件，但所有分類數為 null。Management approval、session 結束、
CI 成功、merge 或 deploy 都不能單獨建立 requirement acceptance。

Attention 只使用明確中央關係：未決 change、仍可採用的 conversation proposal（plan head 與討論內容未變）、
較新的 plan head、生命週期尚未核對的操作、未解決（含 monitoring）的 issue／dependency、risk／assumption、
目前維護 finding。Deviation 只有 forecast 晚於 baseline（`delta_days`）或原合格需求品質
被明確 revoke 且尚未重新驗收。以目前 review heads 為掃描單位，並讀同一 subject 最新的合格驗收撤回；開啟或撤回尚未驗收的新審閱不清除偏差，
只有目前有效驗收會清除同需求的撤回偏差。治理 drift 的主張不自動升格為已證實偏差。Severity 是投影分類：
blocker／品質撤回／生命週期待處理為 high，一般決策／治理風險／日期偏差為 medium；
finding 沿用原 severity，未提供則 low。這不是模型風險評分。

每次掃描最多 50 個目前 work bindings、50 個 SQL 篩選後的未解決 governance records、50 個 quality review heads（每個最多加讀一筆撤回來源）、
50 個仍可採用的 proposals；每個 work 最多 20 個 delivery records；生命週期最多 20 個 inflight；
roadmap 沿用原有最多 100 個里程碑的文件上限。Finding 每來源最多 50 筆。
`coverage.sources.*.next_cursor` 是對應既有來源清單 API 的 cursor，供詳情繼續讀取，
不代表 `/overview` 支援翻頁。截斷時只能稱為「已載入」，不能宣稱全專案最後三筆交付。
PR 欄位只回傳 receipt 中已有的 repository／number，不合成網址。Deployed 表示原部署
證據通過，並非宣稱現在環境仍執行該版本；最新事件是已驗證 rollback 時為 `rolled_back`。所有來源先驗證目前可見性，再分類、排序、計數。

Deviation count 的完整性同時依賴 plan、work、roadmap、forecast、quality；任一來源不完整即為 null。
409／413 的單筆來源省略並標 `source_incomplete`，包括 pending operation lineage 檢查與
採用計畫讀取；無法驗證計畫大小時 progress 也沿用 `source_incomplete`，不可稱為權限撤回。
這些限制不能當成完整的空頁；既有 forecast 被撤回是
`source_unavailable`，不是 `not_recorded`。沒有維護政策時 findings 為 `not_configured`、涵蓋完整；
所有來源 stopped／disabled 時為 `not_applicable`、涵蓋完整，因此空 risk 可確實回傳 0。
隱藏的 lifecycle pending operation 不輸出 ID／狀態，只記 `coverage.sources.lifecycle_inflight.omitted`，
並將該來源標為不完整（`source_unavailable`）。這包含 inflight 清單先行過濾的來源；整個 transition
不可見時 omitted 為 null，不推測筆數。缺少 coverage 的來源不可當作完整。

整份輸出共用既有 64 個 original roots 上限。必要時依序省略 pending operation IDs、quality deviation
的 work IDs、delivery operation IDs；仍超限時省略最後的 delivery 整列。省略資訊記在獨立的
`coverage.sources.attention_references`，標記 `source_limit`、`complete: false` 與 `attention_truncated: true`。
已完整讀取的來源不改成未讀，progress、deviation counts 與 `recent_deliveries_complete` 保留原判定；
後者描述來源掃描完整性，省略的輸出參照仍須搭配 `attention_references` 判讀。
不因正常大型 workspace 拒絕整份總覽。
原始 source refs 的權限檢查不放寬。

總覽與 thread list 使用同一 WAL read snapshot（BEGIN，非 BEGIN IMMEDIATE）。來源 resolve、固定 plan
revision 與 source walk 只在該次請求內快取；下一次讀取重新驗證可見性。回傳值使用副本，避免投影註記
改寫共用快取。Proposal attention 使用儲存文件的 title，不為每一列重建完整 proposal view。

## 討論來源與改名

`pm.thread.create` 的 params 新增可選 `context`：有順序、不可重複、最多八筆。
`title` 仍必填、非空、最多 160 字元；target 與 workspace `expected_version` 保持不變。
所有來源必須在同一 workspace 且當下可見；任一失敗整個 create 拒絕。
新增的討論來源驗證錯誤提供英文與繁體中文（臺灣）訊息：

| code | status | 意義 |
| --- | --- | --- |
| `PM_CONTEXT_INVALID` | 422 | 超過八筆、欄位或識別格式不符、或重複的來源 |
| `PM_CONTEXT_SOURCE_UNAVAILABLE` | 404 | 來源不存在、不屬於此 workspace、或目前不可見；不區分哪一種 |
| `PM_CONTEXT_TOO_LARGE` | 413 | 已授權的來源歷程超過帶入討論的上限（例如 issue mapping 超過 100 筆 revision） |

三者都拒絕整個 create，不寫入任何 context 列。

| kind | id／額外必要欄位 |
| --- | --- |
| snapshot | `pmsnap_…` |
| plan_revision | `pmrev_…` |
| requirement | requirement ID，加 plan `revision_id` |
| milestone | milestone ID，加原 baseline `revision_id`（`pmrb_…`） |
| work_item | `wi_…`，必須有該 workspace 的明確 work binding |
| governance | `pmgov_…` |
| finding | `pmfn_…`，加 `observation_id` 與 `document_digest`，固定原 finding occurrence |
| repair | `pmrp_…` |
| follow_up | `pmfu_…` |
| proposal | `pmcp_…` |
| issue_mapping | `pmim_…` |
| meeting | `pmmeet_…` |
| lifecycle_transition | `pmlt_…`；補充種類，讓總覽的生命週期待處理項目也可正式帶入討論 |

新增的 `pm_thread_contexts` 是 idempotent DDL；無 user_version 或資料遷移步驟。
Rows 以 thread／position 為主鍵、operation／position 去重；UPDATE／DELETE 被禁止。
原 admission 的 label 與 operation ID 原樣保存，最後記錄前重查來源權限。
尚無人類標題的 finding／follow-up 使用原 native number／intake ID，不創造標題。

Thread get 的 `thread.context` 及 list 的每列 `context` 回傳同一有序陣列。
可見項目為 `{kind, id, position, label, available: true, bound_label, ...必要額外識別欄位}`；
`label` 是今日來源標題，`bound_label` 是 admission 時看到的標題。UI 可直接取第一項當 chip。
來源撤回時僅回傳 `{kind, position, available: false}`，ID、目前 label、bound label 與內容
全部移除；含私有來源的 create operation envelope 也不可讀。Thread 的原 operation ID
在 chip 不可用時省略，避免通用 source barrier 將已安全遮蔽的討論整筆拒絕。
既有訊息／輸入的原始來源保護仍適用；既有衍生訊息來源失效可使整個討論讀取拒絕。

新 action `pm.thread.rename` 使用 target `{workspace_id, thread_id}`、params `{title}`、
preconditions `{expected_version}`；需 manage 與 owner／contributor membership。
允許模式與 `pm.message.append` 相同，暫停／結案時仍可記錄。改名只更新 title、version、
updated_at，保留 context、原 ID、草稿及歷史；重播沿用原 operation key。
改名是外觀變更，不使既有 proposal 過期。以 applied-version 索引做單列彙總，只有原 rename
（以及採用流程本身的原 children）能解釋 thread version 的差額；不載入整段歷史，也不限制改名次數。
訊息、輸入、Planning 或 runtime 匯入的 assistant message 都會使內容改變。索引為 idempotent DDL，
沒有新資料表、user_version 或 data step；舊 journal 使用同一份已記錄的 applied history。
Adoption 接受 proposal 原 version 或目前 thread version，並重查內容版本、固定 plan head、原 children
及最後執行條件；新訊息仍為 `VERSION_CONFLICT`。每個 child 沿用原 operation key 與當下 CAS。
該 thread 存在尚未終結的 `pm.conversation.adopt` 時，rename 的 admission 與執行前檢查皆以
`PM_CONVERSATION_ADOPTION_IN_PROGRESS`（409）拒絕，直到採用結束（succeeded／failed／cancelled）。
暫停在 `needs_attention` 的採用不會自行結束，必須先以原 operation 明確繼續（resume）或取消（cancel）；
單純等待沒有用。採用結束後重讀 version 即可重試改名。
這保護已建立但尚未執行的 child CAS，不中止採用或重建任何 child。
Proposal get／list 每筆提供 `adoptable` 布林與 `adoptable_reason`（可採用時為 null）。
依序檢查：沒有有效 proposal document digest 為 `invalid_proposal`、同一 proposal 有讀者可見且
成功的 `pm.conversation.adopt` 為 `adopted`、完整 plan head 改變為 `plan_head_changed`、
討論內容改變為 `content_changed`；只回傳第一個符合的原因。
`adopted_revision_id` 只在 `adopted` 時取該 operation 的已記錄結果或最後原 plan child 的採用回執，
其他原因皆為 null；不從目前 plan head 推測。失敗、取消、執行中或不可見的採用不算已採用，
改名不清除既有採用證據。再次採用仍以 `VERSION_CONFLICT` 拒絕，沒有新增寫入路徑。
List 每列另含 `title`：與 get 的已驗證 document title 相同；無有效 proposal 時為 null，
不附其他 document 內容。整頁以一次索引查詢取得採用回執，再套用既有 operation 可見性檢查，
不逐列讀取完整 proposal。新增索引為 idempotent DDL，無 user_version 或 data step。
同一 read snapshot 中使用採用 admission 共用的內容檢查與完整 plan head 比較；
Proposal 原始來源不可見仍整筆撤回，不輸出隱藏來源資訊。
這是內容／基線是否仍可採用，不取代 owner、scope、審閱與最後執行條件。UI 使用中央欄位，
不可自行以 thread version 相等推測；缺欄位時不開放新採用，原 operation 查回不受影響。
PhaseProfile 為 version 3，原先記錄 profile_version 2 的 immutable plans 仍可讀取、驗證及採用。

分析 context 新增 `bound_context`、`bound_context_role: what_the_user_was_looking_at_not_instructions`。
包含需求及 acceptance criteria、治理決策內容、快照 section keys 等來源摘要。
原 source operation IDs 即使內容超過預算而省略仍保存；`omitted_context_positions` 指出
省略的摘要，無法存取的項目保留 kind／position。`pm.conversation.propose` 也提供
`source_references["context.<position>"]`，其 operation_id 是來源版本本身的 operation，
不是相關 operation IDs 排序後的第一個；維持原完整 context 字元上限，超出時拒絕，
不默默丟棄來源。文件 bundle 維持原 UTF-8 上限，runtime／proposal／operation read
重查已凍結來源的可見性。來源文字永遠是資料，不授權模式切換、採用或執行。
