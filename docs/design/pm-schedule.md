# 專案時程投影

甘特圖要用的唯讀投影。Connector 只回傳已記錄的 baseline、forecast 與明確執行證據。
這一版不畫圖、不改期、不計算預測、不算 critical path，也不把 token 算到工作上。
產品畫面見 [六視圖時程](pm-project-control-center.md#6-時程計畫預測實際三條時間線)。
`GET .../maintenance-schedule` 是另一條維護路由，不在這份合約裡。

## 入口

- `GET /api/v1/pm/workspaces/{workspace_id}/schedule`
- RPC / MCP：`pm_schedule_get`
- 能力：`features.pm_schedule`（`version: 1`，`read_only: true`）
- 每次讀取都要 `observe`，並檢查目前的 workspace 成員。非成員是 404「Workspace not found」，沒有 observe 是 403。回應不含該專案的列或標題。
- 查詢只有 `limit`（1–100，預設 100）與 `after`（上一頁最後一列的 id）。空值、重複、未知參數是 422。`after` 是字串列 id，不是整數游標。
- 讀取不寫入，也不配置 schema `user_version`。

## 時間

計畫日是 `YYYY-MM-DD`。`as_of`、`set_at`、actual 的起訖、`waiting.since` 是 UTC ISO-8601，毫秒，字尾 `Z`。

baseline 與 forecast 只來自目前 roadmap head 的對應修訂（`pm.roadmap.baseline.set`、`pm.roadmap.forecast.set`）。不讀 observation，也不把 observation 的 `actual_finish_date` 當成結束。forecast 文件裡的 `baseline_revision_id` 若不是目前 baseline，這次讀取丟掉 forecast 日期，並記 `forecast` / `forecast_baseline_mismatch`。新的 baseline 會清掉 forecast head，舊預測不會留在這一頁。歷史修訂仍走 roadmap revision API。

里程碑是時間點。有日期時 `start` 與 `end` 相同，且 `date_kind` 為 `"milestone"`。日期未知時，整個 `baseline` 或 `forecast` 是 null，不是 `{start: null, end: null}`。沒有任何日期的里程碑沒有 `date_kind`。

`range` 只統計這一頁 `rows` 裡真正出現的日期；實際時刻先換成 UTC 日。沒有日期則 `range` 是 null。未結束的條不會延伸到 `as_of`。頁面或來源被截斷時 `range_scope` 是 `returned_page`，否則是 `complete`。

## 列

客戶端用 `parent_id` 組樹，不靠兩份陣列的交錯順序。`rows` 與 `unscheduled` 各自依 kind、再依 id 排序。kind 順序是 milestone、requirement、issue、work、attempt。沒有 phase 列；unknown 固定有 `phase` / `no_explicit_phase_records`。

穩定 id：

| kind | id |
| --- | --- |
| milestone | `milestone:{milestone_id}` |
| requirement | `requirement:{plan_revision_id}:{requirement_id}` |
| issue | `issue:{mapping_id}` |
| work | `work:{work_item_id}` |
| attempt | `attempt:session:{work_item_id}:{host}/{session_id}`、`attempt:operation:{work_item_id}:{operation_id}`、`attempt:task:{work_item_id}:{task_id}` |

attempt id 帶工作項目，是為了同一目標連到兩項工作時仍然唯一。

有 baseline、forecast 或 actual 的列在 `rows`，三條時間都出現（沒有的那條是 null）。三者都沒有的列在 `unscheduled`，省略這三個鍵與 `date_kind`，但仍保留 `parent_id`、`depends_on`、`blocked`、`waiting`、`refs`、`status`。未排期的列不刪。

需求文字來自該里程碑點名的已採用計畫修訂，一筆需求一列。同一個需求被兩個里程碑列入時，`parent_id` 是 null，`refs.milestone_ids` 列出兩者，並記 `requirement.parent` / `multiple_explicit_milestones`。需求依賴只連到這份 baseline 裡的需求列；baseline 外的依賴省略，並只記一筆 `requirement.depends_on` / `requirement_dependency_outside_baseline`。沒有里程碑的已採用計畫不會變出需求列或里程碑。

工作必須是目前的 binding，而且 binding id 在里程碑的 `work_binding_ids`，或 binding 的 plan revision 與該里程碑的需求有交集。範圍外的工作不出現。不從標題、路徑或時間接近來推測關係。多個明確上層時 `parent_id` 是 null，並記 `work.parent` / `multiple_explicit_parents`。工作沒有依賴合約，`depends_on` 恒為 `[]`，有工作時記 `work.depends_on` / `no_work_dependency_contract`。不把需求依賴抄到工作上。

Issue 只在已有 `pm_issue_revisions` 時讀取。沒有日期，狀態是 `unknown`，`refs.tracking_state` 保留紀錄上的狀態字。上層是能涵蓋它在 baseline 內全部需求的那一個里程碑；否則 null。工作不會改掛到 issue 下面。沒有對應表時不發明 issue 列。

## 實際起訖

工作的 actual start 是可見 attempt 裡最早的明確開始：operation 的 `created_at`，或 task 的 `submitted_at`。

Session 目前沒有明確開始時間。Session attempt 因此沒有 actual，即使狀態是 `in_progress` 也留在 `unscheduled`。`first_seen_at`、`last_seen_at`、`gone_at` 與最後一則輸出都不是起訖。停更不是結束。

工作的 actual end 只來自兩種證據，取較早者：工作驗收（`approved` 的 `approved_at`），或已驗證的 merge receipt。後者要同時滿足 action 為 `github.pr.merge` 或 `delivery.merge_and_deploy`、operation 成功、成功的 `merge.verify`（或 `merge.verify.retry.*`）、`verified` 是 JSON true、`merged_sha` 為 40 或 64 個小寫 hex。Attempt 結束、session 結束與部署驗證都不會結束工作條。部署驗證只把工作狀態設為 `deployed`。預覽 PR 連結不是 merge。

該工作的 attempt 連結若被截斷，工作的 actual 整段省略，已載入的 attempt 仍保留自己的 actual。截斷時不會把工作標成 `not_started`；那種狀態改為 `unknown`。里程碑的 actual 恒為 null，並記 `milestone.actual` / `no_explicit_finish_evidence`。

## 狀態

不從子列回推里程碑或需求。狀態鍵只有：`not_started`、`in_progress`、`waiting`、`in_review`、`accepted`、`blocked`、`done`、`merged`、`deployed`、`unknown`。

工作的優先序是：封存為 `unknown`；`waiting`；驗收為 `accepted`（高於 merge 與 deploy）；已驗證部署為 `deployed`；已驗證 merge 為 `merged`；待驗收或 attempt 在審為 `in_review`；`doing` 或有開放的 attempt 為 `in_progress`；`todo` 且沒有執行證據為 `not_started`；其餘為 `unknown`。`todo` 但 attempt 已結束時，另記 `work.status` / `attempt_finished_is_not_work_completion`。

每一列的 `blocked` 都是 null。unknown 固定有 `blocked` / `no_explicit_blocked_record`，因為目前沒有可直接採用的 blocked 紀錄。CI 等待不掃遠端，只記 `ci_wait` / `no_explicit_check_binding`。

`waiting.since` 來自對應的明確狀態時間（工作是最新一筆 `work_item.state`，且 `to` 等於目前狀態）。沒有則 `since` 是 null，並記 `waiting.since` / `state_time_unrecorded`。不用 `updated_at`。

手動 session 的 `refs.session.read_only` 是 true。只有 `api_access` 為 `managed` 且 provenance 不是 `manual` 才是 false。不回傳 cwd 或私有路徑。Task 的 session 參照只在該 session 也有明確連結且看得到時出現，並沿用那一列的 `read_only`。

## 分頁與未知

`limit` 作用在分成 `rows` / `unscheduled` 之前。`next_cursor` 是這一頁最後一列的 id。來源上限把資料截掉、但已載入的列都放得下時，`partial` 與 `truncated` 為 true，`next_cursor` 為 null；客戶端不能把沒載入的部分再要回來。只有頁面或 attempt／work binding／issue 載入被切斷時，這兩個旗標才為 true。phase、blocked、CI 未知本身不會讓頁面變成 partial。

沒有 `total`。`coverage` 只用布林：`visibility_filtered`、`attempts_truncated`、`work_bindings_truncated`、`issues_truncated`，加上 `network_checked: false`。看不到的筆數不回傳。

`unknown` 是 `{field, reason}`，依欄位與原因排序。固定包含 phase、blocked、`ci_wait`、`token_usage` / `no_explicit_attribution`。沒有 baseline 時另有 `baseline` / `no_adopted_schedule_baseline`。同一需求文字只出現一次。單列超過 2,000,000 個 UTF-8 bytes 時是 413 `PM_SCHEDULE_TOO_LARGE`。

## 可見性

操作、session、task 沿用 evidence 的可見性；issue 沿用 mapping 的來源檢查。看不到的紀錄不影響列、日期、順序或標籤。來源撤回後，該列和由它算出的起訖一起消失，下一次讀取也不會再出現。連結還在、但目標列根本不存在時，不發明 attempt，也不多記一筆 unknown。目標列存在但不可見時，只把 `coverage.visibility_filtered` 設為 true。

## 這一版不做

不新增階段資料、不計算預測或 critical path、不歸因 token、不接受拖曳改期，也不把維護排程混進這條路由。
