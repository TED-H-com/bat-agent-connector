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
- 查詢只有 `limit`（1–100，預設 100）與 `after`。`after` 必須是上一頁的 `next_cursor`：不透明的 base64url（`A–Z`、`a–z`、`0–9`、`_`、`-`，沒有 `+`、`/`、`=`），解出來是該列的穩定 id。空值、重複、未知參數，或不是這種游標，是 422。它不是整數游標，也不能把列 id 直接放進查詢。回應裡的 `id` 仍是明碼。伺服器發出的游標都收得回來，列 id 裡的控制字元不會讓 `after` 變成 422。
- 一次讀取用一個 deferred read snapshot，不開寫入鎖，也不配置 schema `user_version`。同一次請求裡重複的 lineage、計畫與可見性查詢會重用；下一頁是另一次讀取。

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

需求文字來自該里程碑點名的已採用計畫修訂，一筆需求一列。文字是規劃輸入，原樣保留：換行、tab、開頭的 `/` 都留著，由前端截斷顯示。真正沒有文字時仍列出該列，標題改用 requirement id，並記 `requirement` / `requirement_text_unavailable`，讓 `parent_id` 與 `depends_on` 繼續指向已列出的列。同一個需求被兩個里程碑列入時，`parent_id` 是 null，`refs.milestone_ids` 列出兩者，並記 `requirement.parent` / `multiple_explicit_milestones`。需求依賴只連到這次實際列出的需求列；baseline 外或沒有列出的依賴省略，並只記一筆 `requirement.depends_on` / `requirement_dependency_outside_baseline`。沒有里程碑的已採用計畫不會變出需求列或里程碑。計畫本身不可見時不列該需求。

工作必須是目前的 binding，而且 binding id 在里程碑的 `work_binding_ids`，或 binding 的 plan revision 與該里程碑的需求有交集。範圍與可見性都在 200 筆上限之前套用。範圍外或隱藏的 binding 不佔名額；較新的範圍內工作不會被範圍外的 binding 擋住。超過 200 筆範圍內且可見的 binding 才設 `work_bindings_truncated`，順序仍是 sequence 由舊到新。不從標題、路徑或時間接近來推測關係。多個明確上層時 `parent_id` 是 null，並記 `work.parent` / `multiple_explicit_parents`。工作沒有依賴合約，`depends_on` 恒為 `[]`，有工作時記 `work.depends_on` / `no_work_dependency_contract`。不把需求依賴抄到工作上。

Issue 只在已有 `pm_issue_revisions` 時讀取。沒有日期，狀態是 `unknown`，`refs.tracking_state` 保留紀錄上的狀態字，包含 `cancelled`。標題用非空白檢查：這一版的 `public_title`，否則先前已發布的最後一個非空白 `public_title`，否則 `repository#number`。`/` 與換行保留。三者都沒有時不列，並記 `issue.title` / `issue_title_unavailable`。上層必須同時符合需求 id 與該 binding 的 plan revision，而且那個里程碑涵蓋它在 baseline 內的全部需求；不能只靠需求 id 跨修訂對上。否則 `parent_id` 是 null。工作不會改掛到 issue 下面。沒有對應表時不發明 issue 列。工作項目隱藏時，issue 列一起不出現。只計會列出的列：隱藏工作的對應，以及套件存取拒絕的對應，都不佔 200 筆名額。持續翻頁直到列出 200 筆，或已經檢查 1000 筆對應而後面還有資料；只有這時設 `issues_truncated`。

## 實際起訖

工作的 actual start 是可見 attempt 裡最早的明確開始：operation 的 `created_at`，或 task 的 `submitted_at`。

Session 目前沒有明確開始時間。Session attempt 因此沒有 actual，即使狀態是 `in_progress` 也留在 `unscheduled`。`first_seen_at`、`last_seen_at`、`gone_at` 與最後一則輸出都不是起訖。停更不是結束。

工作的 actual end 只來自兩種證據，取較早者：工作驗收（`approved` 的 `approved_at`），或已驗證的 merge receipt。後者要同時滿足 action 為 `github.pr.merge` 或 `delivery.merge_and_deploy`、operation 成功、成功的 `merge.verify`（或 `merge.verify.retry.*`）、`verified` 是 JSON true、`merged_sha` 為 40 或 64 個小寫 hex。Attempt 結束、session 結束與部署驗證都不會結束工作條。部署驗證只把工作狀態設為 `deployed`。預覽 PR 連結不是 merge。

Operation attempt 的結束時刻：`succeeded`、`failed`、`cancelled` 都用最新一筆 `api_events` kind `operation.{status}`，而且 body 的 `to` 等於該狀態。不用步驟的 `finished_at`，也不用更早另一個終態的事件。`uncertain` 不算結束。終態卻沒有對得上的事件時，end 是 null，並記 `actual.end` / `terminal_time_unrecorded`。

若算出的工作 end 早於 start，保留 start，把 end 改為 null，`open` 為 true，並記 `work.actual` / `inconsistent_dates`。

該工作的 attempt 連結若被截斷，或連結只指向缺少的紀錄，工作的 actual 整段省略，已載入的 attempt 仍保留自己的 actual。這兩種情況都不會把工作標成 `not_started`；那種狀態改為 `unknown`。只有缺少紀錄才設 `attempts_unavailable`。連結先依 `link_id` 判斷可見性，再套每個工作 50 筆、合計 200 筆的上限。只計可見紀錄與缺列；隱藏的連結不占名額、不改可見列、也不設 `attempts_truncated`。這一批連結最多檢查 1000 筆；檢查上限停下且後面還有連結時，剩下的工作才算被截斷。里程碑的 actual 恒為 null，並記 `milestone.actual` / `no_explicit_finish_evidence`。

## 狀態

不從子列回推里程碑或需求。狀態鍵只有：`not_started`、`in_progress`、`waiting`、`in_review`、`accepted`、`blocked`、`done`、`merged`、`deployed`、`unknown`。

工作的優先序是：封存為 `unknown`；`waiting`；驗收為 `accepted`（高於 merge 與 deploy）；已驗證部署為 `deployed`；已驗證 merge 為 `merged`；待驗收或 attempt 在審為 `in_review`；`doing` 或有開放的 attempt 為 `in_progress`；`todo` 且沒有執行證據、也沒有被截斷或缺少的連結為 `not_started`；其餘為 `unknown`。`todo` 且某個 attempt 有終態結束時間時，才記 `work.status` / `attempt_finished_is_not_work_completion`。沒有結束時間的 attempt 不記這筆。

每一列的 `blocked` 都是 null。unknown 固定有 `blocked` / `no_explicit_blocked_record`，因為目前沒有可直接採用的 blocked 紀錄。CI 等待不掃遠端，只記 `ci_wait` / `no_explicit_check_binding`。

`waiting.since` 來自最新一筆對得上目前狀態的狀態事件，不是篩掉其他狀態後留下的舊事件。工作是最新的 `work_item.state`。Task 不限 `events.kind`：body 的 `from` 與 `to` 都是 task 狀態的事件都算轉換，取 `event_id` 最新且 `to` 等於目前狀態的那一筆。同一種事件也用來決定 done／failed 的結束時刻。等待中的 operation 是最新的 `api_events` kind `operation.{status}`。只有 body 的 `to` 等於目前狀態時才用該事件的 `created_at`。對不上則 `since` 是 null，並記 `waiting.since` / `state_time_unrecorded`。不用 `updated_at`。Task 的 `verifying` 是 `in_review`，沒有 `waiting` 物件；`verifying_started_at` 不是等待起點。

手動 session 的 `refs.session.read_only` 是 true。只有 `api_access` 為 `managed` 且 provenance 不是 `manual` 才是 false。不回傳 cwd 或私有路徑。Task 的 session 參照只在該 session 也有明確連結且看得到時出現，並沿用那一列的 `read_only`。`refs.run_id` 只在這個已觀察 session 恰好對上一個可見的 run 時出現。掃描依建立順序，只數可見的 run：看到第二筆可見 run 就停，不挑選，並記 `attempt.run_id` / `multiple_explicit_runs`。隱藏的 run 不占名額、不拿掉可見的那一筆，也不記這筆 unknown。每個 session 最多檢查 64 筆（含隱藏的）；若這個上限停下時還沒決定是一筆還是多筆，不回 `run_id`，並記 `attempt.run_id` / `run_lookup_truncated`。不靠 workspace 全域上限。

## 分頁與未知

`limit` 作用在分成 `rows` / `unscheduled` 之前。`next_cursor` 是這一頁最後一列 id 的不透明 base64url，排序與整頁相同（kind，再 id），不是 `rows` 或 `unscheduled` 陣列的最後一筆。客戶端原樣帶回 `after`。來源上限把資料截掉、但已載入的列都放得下時，`partial` 與 `truncated` 為 true，`next_cursor` 為 null；客戶端不能把沒載入的部分再要回來。頁面被切斷、attempt／work binding／issue 載入被切斷，或 `attempts_unavailable` 為 true 時，這兩個旗標才為 true。phase、blocked、CI 未知本身不會讓頁面變成 partial。

沒有 `total`。`coverage` 只用布林：`visibility_filtered`、`attempts_truncated`、`attempts_unavailable`、`work_bindings_truncated`、`issues_truncated`，加上 `network_checked: false`。看不到的筆數不回傳，缺列的筆數也不回傳。

位元上限是 2,000,000 個 UTF-8 bytes。每一列的公開 JSON 只編碼一次。信封用最壞情況估算：`more` 為 true、剩餘列裡最長的編碼游標、全部 `unknown`、以及最大的 `range` 物件。列的編碼長度（含陣列裡的逗號與空白）累加，超過上限就停在前一列。同一輪也累計這一頁公開列引用的 lineage roots 聯集；聯集即將超過 64 時同樣停在前一列。因此被位元組或 roots 截斷時 `more` 為 true，`next_cursor` 是這一頁依排序最後留下的列；下一頁用 `after` 接上，不重複、不漏列。單列自己的 roots 超過 64，或查詢丟出 `OperationError` 時，該列視為隱藏，不放進游標，也不讓整次讀取 409。這個判斷在算出工作的 actual 與狀態之前做；被隱藏的 attempt 不計入所屬工作的起訖或狀態。單列加上信封仍超過上限是 413 `PM_SCHEDULE_TOO_LARGE`。回傳本文再編碼後也不超過該上限。多位元組標題以 UTF-8 位元組長度計。

`unknown` 是 `{field, reason}`，依欄位與原因排序。固定包含 phase、blocked、`ci_wait`、`token_usage` / `no_explicit_attribution`。沒有 baseline 時另有 `baseline` / `no_adopted_schedule_baseline`。同一需求文字只出現一次。

## 可見性

操作、session、task 沿用 evidence 的可見性；issue 沿用 mapping 的來源檢查。工作項目在計入上限之前先過 `pm_source_lineage.visible`；409 也算隱藏。計畫讀取的 409 與單筆 issue 對應的 lineage 409 同樣只隱藏那一筆，不讓整頁失敗。看不到的紀錄不影響列、日期、順序或標籤。來源撤回後，該列和由它算出的起訖一起消失，下一次讀取也不會再出現。隱藏的工作、它的 attempt 與掛在它上面的 issue 一起不出現。隱藏工作的 id 不出現在列 id、`parent_id`、attempt id 或游標。預設讀取與帶 `after` 的讀取都是 200；不能用狀態碼判斷某個 id 在不在。Session 或 task 的 lineage 查詢若丟出 `OperationError`，只隱藏那一筆，整頁仍然回來。

Session 連結還在、`sessions_observed` 沒有該列、而且 `pm_evidence._session_visible` 為真時，仍列出 attempt。狀態是 `unknown`，標題是 session id，`refs.session` 是 `{host, session_id, read_only: true}`，沒有 baseline、forecast 或 actual，並記 `attempt.session` / `session_not_observed`。沒有觀察列就沒有 provenance，所以不能標成 managed。不可見的 session 不列，只把 `visibility_filtered` 設為 true；先查可見性，不靠觀察列是否存在。

Operation 或 task 的連結還在、但那一列不存在時，不回傳該 id。`coverage.attempts_unavailable` 為 true，因此這一頁是 `partial`，並記 `attempt.operation` / `operation_record_missing` 或 `attempt.task` / `task_record_missing`。沒有那一列就不能證明可見性，所以這不是 `visibility_filtered`。列存在但不可見時，只設 `visibility_filtered`。

## 這一版不做

不新增階段資料、不計算預測或 critical path、不歸因 token、不接受拖曳改期，也不把維護排程混進這條路由。

## 共用前端

時程樹標題保留完整的無障礙名稱，滑鼠停留與鍵盤聚焦時顯示完整原文提示（含換行）。
樹列只使用這一個自訂提示，不附原生 `title`；同列內移動滑鼠不重新量測，聚焦捲動時重新定位。
日期全文保留在日期的補充無障礙描述，列描述維持精簡日期。
寬螢幕依文字、縮排及狀態膠囊量測共用標題欄寬，保留至少 480px 的時間軌道空間；
空間不足時仍可用提示閱讀全文。列與時間軌道共用同一列高與欄寬，不各自排版。

`desktop/src/pm-schedule.js` 在 Schedule 檢視呈現此投影；每次依序讀取最多十頁，
原樣傳回游標，並以所有已載入列計算圖表日期範圍。任何一頁標示 partial／truncated
都保留「部分」狀態，原因收在詳細資訊；不聲稱隱藏筆數或完整總數。

圖表不提供拖曳或推算預測。後端標成 `open: true` 的實際區間只畫短的「未結束」標記，不延伸到今天。
終態卻沒有結束時間（`open: false`）顯示「尚無結束時間紀錄」，不當成未結束。
已在時程樹裡的列不重複列入未排期；未排期為 0 時不顯示該摺疊。需求列不顯示狀態膠囊。
Session attempts 維持未排期；人工工作階段只連到既有唯讀介面。側欄的「提議變更」
使用共用討論連結，標題取第一行並加上「變更：」。里程碑只在它屬於目前 baseline
時綁定該 revision；需求綁定 plan revision。時程檢視隱藏時不抓取。窄於 800px 時各列的條狀軌道拉滿列寬，共用同一個日期範圍，
並以一行範圍標題（例如 9月25日至11月7日）與今天標記對齊；條上不放文字。日刻度在這個寬度不提供，
已選的日刻度會保留，寬螢幕再恢復。週刻度只標週一，月刻度只標月初；開頭不足一週或一個月的區間不標。
左緣落在軌道起點之前的標，或與下一標重疊的標，以軌道自己的座標去掉；捲動後重畫不會少標。
路線圖與 Sprint 的原有表單移至頁首更多操作的抽屜，固定 key 與操作查回邏輯不變。

`session_not_observed` 目前是整頁的 unknown 原因，沒有 row id。完整投影只有一個
符合未觀察形狀的候選時，前端才可標為「尚未觀察到」；多個候選或部分資料維持
「未知」，不把其他已觀察但沒有 runtime/model 的 session 誤標為未觀察。
