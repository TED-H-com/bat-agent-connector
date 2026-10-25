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
| `attention` | 最多 20 筆 `{key, kind, title, severity, since, context, ref}`；維護 finding 另附 `finding`，只有事實缺失或無法證明時才附 `unknowns`。先嚴重度再年齡，穩定 key 打破同序；未知年齡排最後 |
| `attention_counts` | blocker／decision／deviation／risk 各自的可見來源筆數；該種類來源不完整時為 null，不能以顯示的 20 筆當總數 |
| `attention_truncated` | 來源掃描未完成或顯示上限截斷時為 true |
| `recent_decisions` | 最多五筆 `{key, title, decided_at, actor, status, ref, operation_id}`，依決策時間由新到舊；key 與 register row_id 相同 |
| `recent_decisions_count` | 完整掃描後的可見已決來源數；來源或輸出參照不完整時為 null，不以顯示筆數當總數 |
| `recent_decisions_complete` | 已決來源及輸出參照的涵蓋是否完整；不是所有決策都已產生預期效果 |
| `recent_decisions_truncated` | 可見來源超過五筆、掃描未完成或輸出參照被省略時為 true |
| `recent_deliveries` | 最多三個不同 work ID，依中央證據時間排序，狀態為 accepted／merged／deployed／rolled_back；另附 `recent_deliveries_complete` |
| `coverage` | `{complete, sources, basis, network_checked: false}`；每一 source 有 `complete`、`next_cursor` |
| `unknown` | `[{field, reason}]`；缺資料保留 null 與機器可讀原因，不補零 |

已驗收的 recent delivery 可附原始 `assessment_ref`（review、operation、version、
document digest、candidate commit）。共用 Web／Tauri 介面以英文與繁體中文展開
該引用，提供原操作與固定 quality_assessment 討論來源；不另取最新 review、不推導
新驗收，也不以引用補齊未知涵蓋。舊服務未提供時不顯示該區塊。重新整理以完整原始
引用維持焦點／展開狀態，換一筆評估時不沿用舊狀態；來源撤回移除內容，固定報告
不使用即時交付引用。單純展開不新增讀取或寫入。

Goal 的唯一取值規則：只使用已採用文件的 `selected_inputs`，依固定優先序 **`governance.goal` →
`goal` → `value` → `problem`** 取值；每個 slot 只考慮 status 為 `user_confirmed` 或 `repo_evidence` 的 input。
同一 slot 有多筆時，保留文件 `selected_inputs` 的原順序，取第一筆符合狀態者。
審閱後的 governance.goal 即使排在舊 value／goal／problem 後面、文字與原目標不同，也優先呈現；
未確認的 governance.goal 不遮蔽已確認的舊 slot。舊 slot 的固定優先序也不受選入順序影響。
`pm.conversation.adopt` 把審閱後的 candidate 記為 `domain.key`；governance domain 的 goal candidate
因此是 `governance.goal`。同一 input 保留在 `selected_inputs` 與 `sections.governance`，採用文件沒有丟失目標；
總覽讀取這個明確 slot，不改 adoption、input 身分或 immutable revision。
既有 profile version 2 的 accepted revisions 使用相同規則，不需資料重寫。
不接受任意 `.goal` 後綴、其他 domain 或自由文字；未選入與已排除的 input 不參與。
換行改為空白；只有未確認的符合 slot 輸入時為 null 與 `goal_unconfirmed`，完全缺少時為 null 與 `no_explicit_goal`，不從標題或任意 Markdown 猜測。沒有採用計畫時 `progress: null`，
原因 `no_adopted_plan`；來源撤回則為 `source_unavailable`。
例如 selected_inputs 依序保留 value「原本的帳號存取目標」與 user_confirmed governance.goal
「讓會員安全恢復帳號存取」，plan.goal 回傳後者；兩個 input 的原 ID、文字、狀態與選入順序皆不改寫。

進度分母僅是已採用版本的 distinct requirements。每條需求只進一類，優先順序為：

1. 可見的未解決 issue／dependency 明確列出的受影響 ID 仍存在於目前採用計畫 → blocked；不要求來源 revision 與目前 revision 相同。
2. 同一 binding／scope 的目前 qualified quality review 涵蓋每個原 acceptance input → accepted。
3. 有該需求的品質審閱或 work 為 done → in_review。
4. 明確相連的 work 為 doing／waiting → in_progress。
5. 其餘可見需求 → not_started。

`complete` 表示分類來源掃描完整，**不是專案已完成**。工作／治理／品質來源不完整時，
分母仍可來自完整採用文件，但所有分類數為 null。Management approval、session 結束、
CI 成功、merge 或 deploy 都不能單獨建立 requirement acceptance。

採用新版不會自動解決原 issue／dependency。Blocker attention 的 `context.requirement_ids` 保留原受影響
ID、`context.revision_id` 保留最初記錄的計畫版本；另外提供 `current_revision_id`、
`blocked_requirement_ids`（仍在目前採用計畫的受影響 ID）與 `absent_requirement_ids`（目前計畫已無此 ID）。
兩組保留原 ID 順序，合起來涵蓋原受影響 ID，不從文字或新名稱推測替代需求。
無可讀採用計畫時，三個目前關係欄位皆為 null，不能把未知當成全部已移除。
Blockers 的數字計算未解決紀錄；progress.blocked 計算目前需求的 distinct ID，因此多個紀錄可阻擋同一需求。
例如 N 記錄的 issue 影響 login／recovery，N+1 保留兩個 ID 時，原 blocker 仍計為一筆，兩條需求皆 blocked；
N+1 移除 recovery 時，仍有一筆 blocker、login 為 blocked，recovery 明列於 absent_requirement_ids，不進目前分母。
全部受影響需求已移除時，未解決紀錄仍須處理，row 與 count 保留、blocked_requirement_ids 為空，progress 不新增不存在的需求。
新版採用後可用既有 `pm.governance.revise` 將原 issue 記為 `reported_resolved`，不需先建立新快照；
需重讀治理 record.version、沿用原快照的完整管理欄位與來源，並沿用本次解決操作的原 key 等待中央結果後重讀總覽。
解決紀錄才移除 blocker row／count／目前 blocked ID，不以計畫採用自動推測已解決；驗收仍須自己的品質證據。
完整步驟見 [治理合約](pm-governance.md#registered-actions)。

Attention 只使用明確中央關係：未決 change、仍可採用的 conversation proposal（plan head 與討論內容未變）、
較新的 plan head、生命週期尚未核對的操作、未解決（含 monitoring）的 issue／dependency、risk／assumption、
目前維護 finding。Deviation 只有 forecast 晚於 baseline（`delta_days`）或原合格需求品質
被明確 revoke 且尚未重新驗收。以目前 review heads 為掃描單位，並讀同一 subject 最新的合格驗收撤回；開啟或撤回尚未驗收的新審閱不清除偏差，
只有目前有效驗收會清除同需求的撤回偏差。治理 drift 的主張不自動升格為已證實偏差。Severity 是投影分類：
blocker／品質撤回／生命週期待處理為 high，一般決策／治理風險／日期偏差為 medium；
finding 沿用原 severity，未提供或不在 `low`／`medium`／`high`／`critical` 則分類為 low。這不是模型風險評分。
目前 finding 的 `title` 是已證明的 provider number 十進位字串；number 無法證明時為 null，不把無效值轉成標題。
同一筆另附結構化 `finding`（欄位與 [登記合約](pm-register.md#finding-身分) 相同）。`unknowns` 只在有事實
缺失或無法證明時出現。來源撤回，或 `visibility_filtered` 的 payload 仍列出 finding 時，不產生 attention 列，
該來源不算完整的零筆，`attention_counts.risk` 因此為 null。

`attention_counts.decision` 與 decision attention 仍只表示待處理決策。採用後的提案從 pending 移除，
不以已完成的決策補回 attention 或增加 pending count。
`recent_decisions` 另外讀取原 `pm_plan_adoptions` receipt 與 `pm.governance.decide` revision：
每個 register identity 保留最近一次已記錄決策，包含已被較新 plan 取代的 adopted revision，以及其後 revise
清除目前 decision 的治理紀錄。重複決定同一治理紀錄不增加來源數；完整決策歷史仍在 register inspector。
ref 分別是 `{kind: "plan_revision", id: "pmrev_…"}` 與 `{kind: "governance", id: "pmgov_…"}`，
開啟 `pm_register_get(workspace_id, key)` 的同一 inspector。採用的 proposal／discussion 依原外鍵從 inspector 查回，
不從最新 head、時間接近或標題推測。這個欄位的來源範圍是計畫採用與治理決策，不是所有會議的摘要。
`decided_at` 是原採用 receipt／decide revision 的 UTC epoch seconds，actor 使用原 adopted_by／recorded_by；
status 是 `adopted`、`adopted_direction` 或 `rejected`。採方向不代表 scope 已採用，採用計畫也不代表工作已執行或驗收。

兩個來源各最多掃描 50 個最近已決 identity；以記錄的決策時間降序掃描，最終同時刻以 key 排序。
先重查當下來源與原決策 operation 的可見性，再排序、計數與套用五筆顯示上限。
隱藏／撤回來源的列、actor、時間、ref、operation_id 與計數貢獻一起移除；下一次請求重新驗證，不沿用上次可見結果。
來源名稱為 `coverage.sources.decided_plans`／`decided_governance`；缺表為 `source_unavailable`，
409／413 為 `source_incomplete`。超過 50 筆為 `scan_limit`，complete 為 false，總數為 null。
這個反向時間掃描沒有可交給既有順向來源清單的 cursor，next_cursor 維持 null；不代表來源完整。
已決歷史不得擠掉原 attention／delivery 參照；超過整份輸出的 64 roots 時略過該歷史列，
以 `coverage.sources.decided_references` 記錄 `source_limit`，已決總數為 null、truncated 為 true。
pending decision counts／attention_truncated 與原進度、交付涵蓋判定保持獨立。

例如使用者保留目標「會員可以登入並重設密碼」，採用「重設連結效期為 15 分鐘」後，
plan.goal 仍是保留目標，pending decision count 可降為 0，已決欄位則包含（下列為合約示例）：

```json
{
  "recent_decisions": [{
    "key": "plan_revision.pmrev_11111111111111111111111111111111",
    "title": "重設連結效期為 15 分鐘",
    "decided_at": 1791892800.0,
    "actor": "project-owner",
    "status": "adopted",
    "ref": {"kind": "plan_revision", "id": "pmrev_11111111111111111111111111111111"},
    "operation_id": "op_22222222222222222222222222222222"
  }],
  "recent_decisions_count": 1,
  "recent_decisions_complete": true,
  "recent_decisions_truncated": false
}
```

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
可見的 finding chip 另附與登記相同的 `finding`；`unknowns` 只在事實缺失或無法證明時出現。
這個物件在讀取時計算，不寫進 `pm_thread_contexts`。

Thread get 的 `thread.context` 及 list 的每列 `context` 回傳同一有序陣列。
可見項目為 `{kind, id, position, label, available: true, bound_label, ...必要額外識別欄位}`；
`label` 是今日來源標題，`bound_label` 是 admission 時看到的標題。UI 可直接取第一項當 chip。
來源撤回時僅回傳 `{kind, position, available: false}`，ID、目前 label、bound label、`finding` 與內容
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
使用者訊息為「採用完成前無法改名；已暫停的採用請先繼續或取消。」；不顯示原始狀態碼。
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
List 每頁只讀一次 plan head、以一次查詢取得該頁的 thread 列，同一 (thread, version) 的內容檢查只做一次，
結果不跨頁或跨請求保留。逐列的來源可見性檢查不變。
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

## 共用前端總覽

`desktop/src/pm-overview.js` 以本投影作為即時狀態的唯一來源，並讀取同一
`plan.revision_id` 的固定文件來呈現章程。不以 snapshot 補即時數值，也不以
journal sequence 顯示版本。未知值保留破折號；來源掃描不完整時，只顯示已知
需求分母及「部分」。所有缺漏原因透過共用 reason map 本地化；未識別的代碼
只在詳細資訊中顯示。Delivery 回傳的欄位名稱為 `state`，回復狀態單獨標示。

選取報告時，狀態與章程只讀取該 snapshot；沒有保存的驗收數量不回推。
報告模式隱藏即時關注清單、里程碑與交付列。回到即時總覽會重新讀取投影。
討論連結沿用 `pm-discuss-link.js`，逐筆傳遞原 attention ref；報告模式綁定
報告及其固定計畫版本。快照建立、比較雙方的權限重查、原 operation key
回復與匯出流程仍由原本的 charter／brief export 模組負責。
## 共用前端討論入口

重新載入時，尚未送出的改名草稿在初次讀取完成前保持可聚焦、唯讀；儲存仍受原授權
與初次讀取門檻控制。一般 Escape 可立即捨棄草稿，讀取完成後將焦點還給改名按鈕；
輸入法組字中的 Escape（`isComposing` 或 `keyCode` 229）保留草稿。晚到讀取不重新
開啟已捨棄的草稿；已送出或儲存資料損壞的原請求仍鎖定，不能透過 Escape 清除。

總覽的治理關注項目在中央支援登記讀取時，開啟「問題與變更」的原紀錄詳情，
由詳情的討論入口帶入同一紀錄與標題。沒有登記能力才使用原治理工具。
先完成目的頁的清單讀取，再選取固定來源；撤回與晚到回覆沿用原序號檢查。

`desktop/src/pm-discuss-link.js` 解析 new 討論 URL；開啟時只建立本機草稿，第一次
傳送才依序呼叫原 `pm.thread.create` 與 `pm.message.append`。草稿頁只呈現討論草稿
與清單；會議、後續準備、交付證據與規格工具等中央 thread 建立後才掛載，避免完整
中央能力讓尚未傳送的草稿被次要工具擠出畫面。建立回執後保存 thread
ID 與待傳訊息，重新載入不重建討論；結果不明時保留原 key，只有已知 operation ID
可自動查回。來源 chip 使用本次讀取的 label，舊 bound_label 只放提示；不可用的來源
不保留 ID、標題或快取內容。右側脈絡／資訊／計畫分頁保留原操作與草稿。
尚未傳送的治理來源草稿也先讀取中央紀錄，讓 chip 顯示目前標題。403/404 時
移除來源標題與 ID，停用傳送並保留本機草稿；被更新讀取取代的晚到回覆不得恢復來源。

提案顯示建議更新、納入選項及單次採用審閱。介面只依中央提案讀取回傳的
`adoptable` 與 `adoptable_reason` 判斷是否仍可採用，不比較訊息、輸入、草稿 ID
或 thread version。已知原因使用共用短標籤；未知原因仍顯示「已過期」，原始
代碼只放在詳細資料。純改名保留中央可採用狀態。VERSION_CONFLICT 顯示
「已過期／再次詢問 PM」，不清除審閱文字，也不以另一個 key 重送原採用。
已知原操作進入 `waiting_external` 或 `waiting_checks` 時仍以原 ID 做有限次回執檢查，
讓等待子操作的採用完成後能更新介面；不重建請求或重送開始。
成功查回採用回執後仍顯示中央的已採用狀態與已採用版本連結，不以本機成功提示取代。
行動版先完成計畫面板的焦點邊界，再把焦點移到已採用版本，讓目標紀錄進入可視範圍。


Overview review corrections: event pages coalesce post-event projection reads without
rebuilding workspace sections. Restricted, filtered and truncated saved reports remain
explicitly Partial, including in print. Coverage uses localized source labels with raw
records on demand; a missing decision list never implies a known zero.
