# 問題、行動與變更登記

唯讀投影。中央 Python Connector 把既有紀錄編成穩定列與處理鏈，不新增資料表、不配置
`user_version`、不新增寫入動作。畫面由後續工作實作。

一次登記讀取共用一個延遲的讀取交易（deferred `BEGIN`），所以各來源讀到同一個已提交點。
`as_of` 仍是請求開始時的 ISO-8601 UTC 毫秒時間戳（偏移 `+00:00`），本身不是快照 id。
這只承諾 SQLite 這次延遲讀取交易所見的已提交內容。鄰近紀錄裡的 REAL epoch 秒會轉成這個格式；
沒有時區的字串維持未知，不假設 UTC。

## 讀取

- `GET /api/v1/pm/workspaces/{workspace_id}/register?type=&status=&owner=&q=&after=&limit=`
- `GET …/register/{row_id}`
- MCP／RPC：`pm_register_list`、`pm_register_get`，範圍 `observe`，並檢查 workspace 成員。
- 能力：`features.pm_register = {version: 1}`。

`row_id` 是 `source.kind + "." + source.id`，只含 `[A-Za-z0-9._-]`，例如 `governance.pmgov_…`，
放進路徑時不必再編碼。`source.kind` 使用討論脈絡的共用詞彙：
`governance`、`finding`、`repair`、`follow_up`、`proposal`、`issue_mapping`、`meeting`、
`plan_revision`、`requirement`、`work_item`。列的類型只會是 `risk`、`issue`、`action`、
`change`、`decision`。紀錄保留自己的身分；投影不會把一列改成另一種類型來抹掉歷史。

`type_basis` 為 `explicit` 或 `neutral`。類型有歧義時只用紀錄自己的明確分類；沒有分類就用
最中性的 `issue`，並標 `neutral`。不從標題或內文猜測。

未知欄位是 `null`，原因在 `unknowns`（例如 `not_recorded`、`not_on_current_revision`、
`repository_name_not_stored`）。不把未知顯示成 0。

## 對照

| 既有紀錄 | source.kind | source.id | type | status |
| --- | --- | --- | --- | --- |
| 治理 `risk`／`issue`／`change` | `governance` | `pmgov_…` | 與 kind 相同，`explicit` | `open`／`monitoring`／`reported_resolved` |
| 治理 `assumption`／`dependency`／`drift` | `governance` | `pmgov_…` | `issue`，`neutral`（kind 不在登記枚舉） | 同上 |
| 維護 finding | `finding` | `finding_id` | `issue`，`neutral`（紀錄沒有登記類型） | `state`，目前為 `observed_open` |
| 維護 repair | `repair` | `pmrp_…` | `action`，`explicit` | 操作 `operation_status` |
| Follow-up intake（`pm.follow_up.prepare`） | `follow_up` | `pmfu_…` | `action`，`explicit` | 無狀態欄位，`null` |
| 會議 follow-up | `meeting` | `{meeting_id}.follow_up.{id}` | `action`，`explicit` | 完成時 `open`，其後事件 `open`／`blocked`／`reported_done` |
| 會議已記錄決策（`recorded_discussion_decision`） | `meeting` | `{meeting_id}.decision.{id}` | `decision`，`explicit` | disposition 原值 |
| 會議草稿裡 disposition 仍是 `proposal` 的項目 | 不列 |  |  | 尚未記錄，不猜成變更 |
| 維護審查完成方向 | `meeting` | `pmrevw_…` | `decision`，`explicit` | `direction` 原值 |
| 維護審查 follow-up | `follow_up` | `{review_id}.follow_up.{id}` | `action`，`explicit` | 完成時 `open`，其後事件同上 |
| 維護審查 candidate，disposition `proposal` | `proposal` | `pmrve_…` | `change`，`explicit` | `proposed` |
| 對話提案 | `proposal` | `pmcp_…` | `change`，`explicit`（提案不是登記類型） | `validation.status` 原值 |
| 計畫採用 | `plan_revision` | `pmrev_…` | `decision`，`explicit` | `adopted` |
| GitHub issue mapping | `issue_mapping` | `pmim_…` | `issue`，`explicit` | `tracking`／`cancelled` |

審查完成的 `source.kind` 用 `meeting`，因為完成文件明確寫下 `meeting_completed`，而且詞彙沒有
`review`。id 前綴是 `pmrevw_`，與 Sprint 會議 `pmmeet_` 不同。這是詞彙內的折衷，不是把審查改寫成治理紀錄。

審查 candidate 的 `source.kind` 用 `proposal`，id 是事件 `pmrve_`，與對話提案 `pmcp_` 不同。
disposition 已明確是 `proposal`，所以類型是 `change`。

產品問題（治理 `issue`）與 GitHub issue mapping 是兩筆身分。只有 finding 上儲存的
`existing_work.mapping_id`，或 mapping 的 `relation`，才會連在一起。

會議 follow-up 的 `source_refs`（snapshot、evidence、sprint 等）不在登記詞彙內，不做成時間線步驟。
同一場會議裡的決策與 follow-up 不因時間接近而互相連結。

治理紀錄寫下的 `context.plan_revision_id` 只是記錄當下的基線，不是處理鏈。時間線不使用它。

## 時間線

`pm_register_get` 才附 `timeline`。步驟只來自行與行之間已儲存的外鍵，以及該列自己的歷史：

- 治理：只讀這筆紀錄自己的修訂，不讀整個工作區的歷史。超過上限時保留最新的一段，再由舊到新重放。
  決策不重複：只有動作是 `pm.governance.decide`，或 disposition 改變時才記一步。採用出現一次。
  採用指向的 `revision_id` 上，被取代的提案步驟不列入。步驟只連到這位讀者讀得到的 id；
  讀不到的計畫版本不放進時間線。
- repair 的 `finding_id`、`work_item_id`。
- follow-up intake 的 `selected_governance_refs`、`selected_meeting_follow_up_refs`。
- 審查 follow-up／candidate 的 `review_id`。
- 計畫採用結果或 source lineage 能對上、且目前看得到的 `proposal_id`。看不到的提案 id 不出現。
- 治理 `disambiguation.record_ids`。

這些外鍵從兩邊讀。選到的列載入它存下的對方；對方那一列也載入指回這裡的紀錄。
只加對方自己的一步，不跟著對方的外鍵再走。隱藏的對方略過。對方 409／413 時不放進時間線，
並把該來源標成 `source_incomplete`。清單不為計畫列讀提案；提案 id 只在這筆計畫的明細上解析。

不從標題、路徑、時間接近或相似名稱推論。需求 id 放在 `related.requirement_ids`，不是時間線步驟。
證據步驟只在紀錄明確存了 evidence id 時出現。

## 合計與分頁

隱藏列在計算合計、計數與摘要之前整筆略過。一頁最多 64 個不同的 lineage root
（`pm_source_lineage.MAX_ROOTS`，呼叫當下讀取）。下一列會超過時這一頁就結束，
`next_cursor` 從被排除的第一列繼續，下一頁重新累計。沒有 lineage 的列不占這個名額。
明細的時間線步驟用同一套檢查：隱藏的步驟略過；再加一步會超過上限時，時間線在那之前結束，
並把該來源標成部分。清單、審查事件與單筆治理歷史遇到 409／413 時，先用同一個 `after`
以一筆重試；這一筆仍失敗，就用該來源自己的序號 stub 讀出這筆的序號，略過它，保留已經
建立的列與步驟，再從它之後繼續。被略過的一筆把該來源標成 `source_incomplete`，所以合計
與摘要是 null；其餘可讀的紀錄仍列出。它不會讓整頁失敗。

篩選後、分頁前，若這次篩選用到的來源都完整：

- `total_by_type` 五個類型都有，包含篩選後的 0。
- `summary.open_by_age`：`0-7d`、`8-30d`、`31-90d`、`90d+`。年齡用 `created_at` 到 `as_of` 的日數。
  小於 8 天是 `0-7d`，小於 31 天是 `8-30d`，小於 91 天是 `31-90d`（第 90 天仍在這一桶），其餘是 `90d+`。
  未來或缺少的 `created_at` 不進桶，計入 `coverage.open_without_known_age`。
- `summary.weekly`：以 `as_of` 那週往回 12 個週一（`YYYY-MM-DD`）。`opened` 用建立時間，`resolved` 用狀態被記錄的時間。
- `summary.blocking_reasons` 取來源記下的原因：欄位名是 `reason` 或 `blocking_reason` 的非空字串，
  以及 follow-up 在 `blocked` 時寫進事件 `document.result` 的說明。空陣列表示沒有記下原因。
  `coverage.blocked_without_reason` 計算狀態是 `blocked`、但沒有上述原因的列數；涵蓋不完整時是 `null`。

未決狀態：`open`、`monitoring`、`observed_open`、`blocked`、`tracking`、`needs_review`、`proposed`、
`in_progress`、`draft`。已解決：`reported_resolved`、`reported_done`、`adopted`、`rejected`、
`cancelled`、`succeeded`。`failed` 兩者都不是。`null` 狀態不進年齡桶。其他狀態計入
`coverage.unclassified_status`，不計入摘要。

`coverage.complete` 只看目前 `type` 會用到的來源。未篩選時，任一不完整的來源都會讓合計為 null。
會議超過上限不會讓 `type=risk` 變成部分。不相關的部分來源不出現在 `coverage.sources`。
`partial_reason` 在相關來源有 409／413 略過時是 `source_incomplete`，否則是 `incomplete`。
缺表代表該功能未安裝，是完整的空來源，不是部分資料。

掃描保留最新的可讀紀錄，不是最舊的。略過的 409／413 不進入清單，但該來源是部分，
合計與摘要是 null。隱藏列不占名額，也不因此標成部分：issue mapping 與 repair 在進入名額之前
就先過 lineage 檢查。issue mapping 清單頁回報 `coverage.source_incomplete` 時，等同略過一筆
409，該來源標成 `source_incomplete`。上限在呼叫當下讀取：
治理、repair、issue mapping 與單筆治理歷史是 200；會議、審查、intake、提案與計畫採用是 40；
會議 follow-up 歷史是 50；審查事件是 20。

登記讀取會以 `follow_up_after` 選取會議最近的 follow-up 歷史；若省略較早事件，會議來源為部分。
底層 meeting get 的 `coverage.histories.follow_ups.after` 回報實際使用的起始序號；未指定時為 0。
這是會議歷史的涵蓋欄位，不是登記頁的 `after` 游標。

Finding 只讀目前這一筆觀察。觀察不存在，或存在但這位讀者看不到（`visibility_filtered`），
都是未知，不是「完整、零筆 finding」。歷史出現在被截斷的觀察裡時，finding 來源標為部分，
不把抄到的 finding 當成總數。看不到的紀錄（含來源撤回）整筆略過，其列、合計貢獻與摘要一起移除；
其餘可讀列保留，合計與摘要仍須符合完整涵蓋條件。隱藏紀錄不影響可見列的順序、可用動作或標籤。
若撤回後會議清單仍列著該會議，但明細已不可讀，該會議的登記列移除，會議來源標為部分。

格式不合法的 `row_id` 在 HTTP、RPC 與 MCP 都是 422。格式合法但缺少或隱藏的列都是 404，
即使其他來源不完整也一樣。治理與 repair 依 id 直接讀取，不重掃整份清單。
Finding 的直接讀取仍會掃描 repair，以便知道哪些 finding 已被可見的 repair 佔用。
血緣上隱藏的 repair 不佔用，也不把來源標成不完整；repair 來源若被截斷或 409／413，動作維持未知。
維護 repair 的 proof 文件沒有 evidence id，所以不做成證據步驟。

`limit` 預設 50、最大 100。排序是首次寫入的序號降序、`row_id` 升序，不用 `updated_at`。
計畫列的序號是 `pm_plan_revisions.sequence`，不是 adoption 表的 rowid。
列在兩頁之間被修訂時仍留在原位，顯示的是目前內容。`after` 是不透明的 base64url
（沒有 `+`、`/`、`=`），內容是序號與下一列的 `row_id`，在 Python 3.10 與 3.14 的解析相同。
`q` 只比對標題，大小寫不敏感；標題為 null 不相符。空的 `q` 在 HTTP 查詢層是 `INVALID_REQUEST`，
在 RPC 是 `INVALID_PARAMS`。`owner` 是 actor 全字匹配。

同一列裡的需求 id 與 PR `{repository, number}` 只保留一次。Finding 的既有 PR 若只有 number、沒有
repository 全名，不放進 `related.prs`。不把 GitHub issue 的 number 當成 PR。

## Finding 身分

維護 finding 的 `title` 維持 `null`，`unknowns.title` 是 `not_recorded`。伺服器不組英文名稱。
`pm_register_list` 與 `pm_register_get` 的 finding 列另附 `finding`，讓客戶端自己寫本地化名稱。
其他列沒有這個鍵。這是讀取時從既有觀察投影的物件，不入庫，也不新增 schema 或 `user_version`。

只複製觀察裡已經驗證過的事實。缺欄位或明確的 null 省略，並在列的 `unknowns` 寫
`finding.<field> = not_recorded`。有值但不符合當初的觀察檢查時整筆省略，原因是 `unproven`。
不截斷、不挑出清單裡合法的一部分、不把別的型別轉成字串或數字。`source` 無法證明時，
不列出該種類的專屬欄位，也不用外層來源名稱補上種類。

| 欄位 | 條件 |
| --- | --- |
| `source` | `native.kind`，只會是 `issues`、`dependabot_pull_requests`、`dependabot_alerts` |
| `number` | `native.number`，大於 0 且小於 2⁶³ 的整數 |
| `labels` | 僅 issues。最多 100 個、各最多 100 字、不重複的標籤。空清單若已存下則保留 |
| `manifests` | 僅 Dependabot pull request。最多 100 條已驗證的相對路徑 |
| `package` | 僅 Dependabot alert。非空字串，最多 512 字 |
| `ecosystem`、`ghsa_id` | 僅 Dependabot alert。非空字串，最多 100 字 |
| `severity` | 僅 Dependabot alert。只會是 `low`、`medium`、`high`、`critical` |

`base_branch`、`manifest_path`、`first_patched_version` 留在觀察裡，不複製進 `finding`。
列上的 `impact` 仍是 `facts.severity` 的原字串（有存下任何字串時），`priority` 仍是
`facts.priority` 的原字串。它們不是 `finding` 的欄位，也不代表那份值已通過 alert 檢查。

看不到的觀察（403／404，或 `visibility_filtered`）不回傳該列，也不回傳 `finding`。
過濾後的 payload 即使仍列出 finding，下一次讀取也不會把身分補回來。血緣上隱藏的 finding
在清單裡整筆略過；單筆讀取在時間線的來源邊界回 404。其餘可讀列與它們的形狀不變。

## 可用動作

`available_actions` 只列出已經註冊、而且這列確實適用的動作：`action`、`target`、`required`。
`required` 是已知道、但不在 target 裡的參數值。不新增 Discuss 或 Propose；那由畫面另開討論。

- 治理：`pm.governance.revise`。`change` 且尚未決定時加 `pm.governance.decide`。已採方向、有提案版本、
  尚未連結採用時加 `pm.governance.adoption.link`。
- finding：只有 `issues`、沒有既有 PR／工作／可見的 repair，且某個已採用 `repair_rules.labels_all` 是標籤子集時，
  才給 `pm.maintenance.repair`，並帶上該 `rule_key` 與 `finding_ref`。對不上就不給，不發明規則。
- repair 尚未終態：`pm.maintenance.repair.evidence.record`。
- issue mapping：`tracking` 給 `pm.issue.cancel`；未 `cancelled` 給 `pm.issue.publish`。
- 已完成的會議或審查 follow-up：對應的 `follow_up.record`，`required.follow_up_id` 為該項 id。

## 共用前端呈現

從總覽開啟指定紀錄時，詳情與已載入列使用相同身分；成功讀取詳情後，若列表已有
該列，也同步更新顯示。紀錄不在已載入或篩選後的列表時，詳情以一行提示，不插入
額外列、不增加已載入筆數。已解決或關閉的紀錄不把原 response 當作目前下一步，
不更動治理寫入內容。未結案紀錄以「討論」為主要操作；「提議變更」收在更多操作。
治理、維護修復、維護審閱與後續追蹤只從頁首選單開啟，沿用原表單與查回流程。
