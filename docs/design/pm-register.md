# 問題、行動與變更登記

唯讀投影。中央 Python Connector 把既有紀錄編成穩定列與處理鏈，不新增資料表、不配置
`user_version`、不新增寫入動作。畫面由後續工作實作。

一次請求是各來源各自開啟交易後的盡力時間點（`as_of` 為 ISO-8601 UTC 毫秒，偏移 `+00:00`），
不是跨模組的單一快照隔離。鄰近紀錄裡的 REAL epoch 秒會轉成這個格式；沒有時區的字串維持未知，
不假設 UTC。

## 讀取

- `GET /api/v1/pm/workspaces/{workspace_id}/register?type=&status=&owner=&q=&after=&limit=`
- `GET …/register/{row_id}`
- MCP／RPC：`pm_register_list`、`pm_register_get`，範圍 `observe`，並檢查 workspace 成員。
- 能力：`features.pm_register = {version: 1}`。

`row_id` 是 `source.kind + ":" + source.id`。`source.kind` 使用討論脈絡的共用詞彙：
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
| 會議 follow-up | `meeting` | `{meeting_id}~follow_up~{id}` | `action`，`explicit` | 完成時 `open`，其後事件 `open`／`blocked`／`reported_done` |
| 會議已記錄決策（`recorded_discussion_decision`） | `meeting` | `{meeting_id}~decision~{id}` | `decision`，`explicit` | disposition 原值 |
| 會議草稿裡 disposition 仍是 `proposal` 的項目 | 不列 |  |  | 尚未記錄，不猜成變更 |
| 維護審查完成方向 | `meeting` | `pmrevw_…` | `decision`，`explicit` | `direction` 原值 |
| 維護審查 follow-up | `follow_up` | `{review_id}~follow_up~{id}` | `action`，`explicit` | 完成時 `open`，其後事件同上 |
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

- 治理：只為被選取的那一列讀 `list_history`。決策與採用／提案版本各是一步。採用與提案指向同一
  `revision_id` 時只留採用那一步，狀態 `adopted`。
- repair 的 `finding_id`、`work_item_id`。
- follow-up intake 的 `selected_governance_refs`、`selected_meeting_follow_up_refs`。
- 審查 follow-up／candidate 的 `review_id`。
- 計畫採用結果或 source lineage 能對上、且目前看得到的 `proposal_id`。看不到的提案 id 不出現。
- 治理 `disambiguation.record_ids`。

不從標題、路徑、時間接近或相似名稱推論。需求 id 放在 `related.requirement_ids`，不是時間線步驟。
證據步驟只在紀錄明確存了 evidence id 時出現。

## 合計與分頁

篩選後、分頁前，若掃描完整：

- `total_by_type` 五個類型都有，包含篩選後的 0。
- `summary.open_by_age`：`0-7d`、`8-30d`、`31-90d`、`90d+`。年齡用 `created_at` 到 `as_of` 的日數。
  未來或缺少的 `created_at` 不進桶，計入 `coverage.open_without_known_age`。
- `summary.weekly`：以 `as_of` 那週往回 12 個週一（`YYYY-MM-DD`）。`opened` 用建立時間，`resolved` 用狀態被記錄的時間。
- `summary.blocking_reasons` 只計算欄位名就是 `reason` 或 `blocking_reason` 的字串。空陣列表示沒有這種欄位，不是「零個阻塞」。

未決狀態：`open`、`monitoring`、`observed_open`、`blocked`、`tracking`、`needs_review`、`proposed`、
`in_progress`、`draft`。已解決：`reported_resolved`、`reported_done`、`adopted`、`rejected`、
`cancelled`、`succeeded`。`failed` 兩者都不是。`null` 狀態不進年齡桶。其他狀態計入
`coverage.unclassified_status`，不計入摘要。

任一來源超出掃描上限、明細上限，或 finding 來源不是完整且可見的目前觀察，則 `coverage.complete`
為 false，`total_by_type` 與 `summary` 為 `null`。缺表代表該功能未安裝，是完整的空來源，不是部分資料。
Finding 只讀目前這一筆完整觀察；歷史出現在被截斷的觀察裡時，整份登記標為部分，不把抄到的 finding 當成總數。
看不到的紀錄（含來源撤回）整筆略過，不影響順序、計數或標籤。治理與 repair 能依 id 直接讀取；
該讀取得到 404 時，即使其他來源被截斷，單筆結果仍是 404，不會把已證明的撤回說成「還不確定」。
會議、提案、對照與 follow-up 只存在於有界掃描裡。掃描不完整又對不到列時回 409，避免把截斷誤報成不存在。
維護 repair 的 proof 文件沒有 evidence id，所以不做成證據步驟。

`limit` 預設 50、最大 100。`after` 是 `{updated_at}~{row_id}`，排序為 `updated_at` 降序、`row_id` 升序。
`q` 只比對標題，大小寫不敏感；標題為 null 不相符。`owner` 是 actor 全字匹配。

同一列裡的需求 id 與 PR `{repository, number}` 只保留一次。Finding 的既有 PR 若只有 number、沒有
repository 全名，不放進 `related.prs`。不把 GitHub issue 的 number 當成 PR。

## 可用動作

`available_actions` 只列出已經註冊、而且這列確實適用的動作：`action`、`target`、`required`。
`required` 是已知道、但不在 target 裡的參數值。不新增 Discuss 或 Propose；那由畫面另開討論。

- 治理：`pm.governance.revise`。`change` 且尚未決定時加 `pm.governance.decide`。已採方向、有提案版本、
  尚未連結採用時加 `pm.governance.adoption.link`。
- finding：只有 `issues`、沒有既有 PR／工作／repair，且某個已採用 `repair_rules.labels_all` 是標籤子集時，
  才給 `pm.maintenance.repair`，並帶上該 `rule_key` 與 `finding_ref`。對不上就不給，不發明規則。
- repair 尚未終態：`pm.maintenance.repair.evidence.record`。
- issue mapping：`tracking` 給 `pm.issue.cancel`；未 `cancelled` 給 `pm.issue.publish`。
- 已完成的會議或審查 follow-up：對應的 `follow_up.record`，`required.follow_up_id` 為該項 id。
