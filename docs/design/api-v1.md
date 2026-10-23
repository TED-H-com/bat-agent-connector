# /api/v1：操作底座、資源目錄與事件

日期：2026-10-07。對應《Better Agent Dashboard／Connector 計畫》v1.0 的 §09–§11，工作包 W01（contract、identity、capabilities、OperationService）與 W03（inventory、pagination、事件游標）。人工 session 的唯讀規則見 [resource-policy.md](resource-policy.md)。

## 一個程序、一個 owner

`/api/v1` 由既有的 `batc serve` task daemon 提供，與 `/rpc` 共用同一個 loopback listener、同一個 SQLite journal（`task_journal.py`）與同一把 owner 鎖。沒有第二個程序或第二個帳本。Dashboard、MCP、CLI 都進入同一個 `OperationService`：

- HTTP：`/api/v1/*`（`api_v1.py`）。
- MCP：`operation_submit`、`operation_get`、`operations_list`、`inventory_sessions`、`inventory_hosts`、`events_list`、`capabilities_get`，經 daemon 的 `/rpc`。
- CLI：`batc api-token`、`batc op`。

遠端使用一律經 SSH forward 到 daemon 的 loopback 埠；daemon 不綁非 loopback 位址。

Legacy session controls：MCP `session_send`／`session_continue`／`session_answer`／`session_interrupt`／`session_set_permissions`
與對應 CLI 經 RPC 呼叫 `session.send`／`session.answer`／`session.interrupt`／`session.permissions` actions。
可選 key／control_version、完整結果與 operation ID/status/code 保留；prefix 固定成 exact session ID，
answer 省略 tool_use_id 時在 admission 固定唯一的 pending prompt。這些相容入口可省 key；
每次建立獨立 operation，storage sentinel 在所有 operation 讀取中投影為
`idem_key=null`、`idempotency_key=null`、`idempotency_enabled=false`。named keys 原值保留，
`batc:nokey:` 為保留前綴；HTTP／`op_submit` 仍須明確 key。細節與尚未轉接入口見
[operations-unification.md](operations-unification.md)。

`session.permissions` 的 target 為 `{host, session_id}`、params 為 `{mode: default|allow_all}`，
使用 operate scope 與既有 host／confinement／task gates；可含 `control_version` precondition。
Claude mode 與 Codex sandbox／approval 分別保存 frame intent／receipt。Claude turn 進行中拒絕，
不新增 deferred flag；idle 後需新 key 表達新的決定。歷史 deferred flags 也不以新 actor／incarnation
自動執行；legacy bulk approval apply 在任何回答／raise 前以 `LEGACY_PERMISSION_RAISE_DISABLED` 拒絕，
dry-run 預覽保留。完整錯誤與 recovery 合約見 [session-permissions.md](session-permissions.md)。

## 身分與權限

| 主體 | 取得方式 | 權限 |
|---|---|---|
| `local-admin` | daemon 的 `task-admin.token`（0600） | 全部 scopes；不接管其他 controller 資源 |
| API token | `batc api-token issue --actor operator-dashboard --scope observe --scope operate` | 發行時指定的 scopes |

Scopes：`observe`（讀目錄、操作、事件、政策）、`operate`（驅動 managed session）、`start`（開新的 managed agent session，例如 `checkpoint.continue`）、`manage`（專案、工作項目與連結）、`approve`（確認工作項目完成；與 `manage` 分開，回報完成的 agent 不能自己簽核，見 [work-items.md](work-items.md)）、`merge`、`deploy`、`integrate`（PR metadata 更新與把成果推送到 head 分支；兩個 action 分開，metadata 另需 repo allow_pr_update，見 [delivery.md](delivery.md)／[integration.md](integration.md)）、`cleanup`（reviewed resource cleanup／release）、`cleanup_discard`（只丟棄未提交內容；person-controlled，agents 不要求，Hermes／Grokbot tokens 不給）。Journal 只存 token 的 SHA-256。操作的 actor 一律取自 token；request body 或 MCP 參數自報的名字沒有授權效果。冪等鍵的唯一性以 actor 為範圍：同 actor、同 key、同內容回原 operation；同 key 不同內容回 409。

Controller 由持久 `server_id` 與已認證 actor 產生；同 actor 的多 client、換 token 與重啟維持身分。Session reservation 保存 `controller_authority` 與 `controller_incarnation`；BAT `workspace_id` 和可選 `pm_workspace_id` 分開。新操作受理及最後 BAT frame 都核對原建立者，背景 task／reviewer／failover／清理沿用持久 authority。缺少建立證據的舊資源維持唯讀；已送出但不明的舊效果仍可回查，不重新認領或重送。`features.controller_isolation` 宣告此契約，inventory 的 `api_access` 依目前 caller 計算，`provenance` 保留客觀來源。

瀏覽器防護：Host 必須是 loopback（擋 DNS rebinding）；有 Origin 時須是 loopback 或 `[api] allowed_origins`。API token 透過 bearer 驗證；managed browser session 僅接受 cookie 路徑，並核對來源、fetch metadata 與 CSRF，不接受作為 HTTP 或 `/rpc` bearer。既有 cookie session、拒絕後的連線恢復與同 host 不同埠限制見 [Managed installation](managed-installation.md#credentials-and-browser-connections)。只有列在 `allowed_origins` 的 Origin 會收到 CORS 標頭（含 `OPTIONS` preflight）；Dashboard 與 API 同源，不需要它。

MCP 的 `operation_submit`、`operation_cancel`、`operation_resume` 要 `confirm=true`，而且只以 `BATC_API_TOKEN` 的主體執行，不退回本機 admin token：這個 client 能做什麼由它自己的 token scopes 決定，不是由 MCP server 啟動時的主機 tier 決定。PM 及共用 operation／event 讀取也要求 `BATC_API_TOKEN`；legacy Fleet 觀察保留原本唯讀方式。

## Operation

HTTP `POST /operations?wait=…`、RPC `op_submit(wait_s=…)` 與 MCP
`operation_submit` 等待後，必須以目前 caller 再讀原 operation；等待期間的
PM membership／原始來源撤權會拒絕回傳 params、result 與 external refs。
拒絕揭露不取消、刪除或重送已受理的操作，原始 key 與 receipt 保留。
HTTP 的 `admission_refused=true` 只用於確認該 actor／key namespace 沒有
已受理紀錄的拒絕；不可用 filtered read 的查無結果推斷尚未受理。既存隱藏
操作的 replay 與等待後拒絕不帶此旗標，避免 client 丟棄原 key 建立重複工作。
PM key namespace 包含 workspace；另一 actor 的同 key 不影響這個判斷。

| 狀態 | 意思 |
|---|---|
| `accepted` | 意圖已持久保存，尚未開始 |
| `running` | 執行中；daemon 重啟後從已保存的步驟接續 |
| `waiting_checks`、`waiting_external` | 等外部條件（checks、queue、workflow），到時間再跑 |
| `uncertain` | 某一步送出後沒有確定結果；以回查判斷，不重送 |
| `needs_attention` | 需要人處理（含回查多次仍無法證明） |
| `succeeded`、`failed`、`cancelled` | 終態 |

每個外部呼叫是一個具名步驟（`operation_steps`）。步驟意圖在呼叫前先 commit。逾時、連線中斷或之後的本機記錄失敗（`OSError`）時步驟記為 `uncertain`，之後由該步驟的 `reconcile` 讀回外部狀態：證明已發生就補記成功，無法證明（包括回查本身連不上）就維持 `uncertain` 並退避重試回查，五次後轉 `needs_attention`。回查次數用自己的計數（`uncertain_tries`），等待 checks／workflow 的輪詢不會用掉它。已完成的步驟在重跑時直接回傳保存的結果。

取消只在下一步之前生效，而且不跳過回查：`uncertain` 的操作收到取消時先立刻回查，證明那一步已發生就照常完成，所以 `cancelled` 不會掩蓋已送出的動作。`needs_attention` 可以取消（原因會註明哪一步從未證明），也可以 `resume`：已完成的步驟不重做，未證明的步驟再回查一次，不重送。資源操作的 replay／cancel／resume 另外要求原 controller；admin 或相同 scope 不授予接管權。Task capability 僅可依原任務授權，system cleanup 僅限原 task lifecycle。其他管理與 GitHub 交付操作沿用各 action 的授權；事件記在實際操作的人名下。`delivery.merge_and_deploy` resume 另外要求目前 principal 同時有 merge/deploy（admin 沿用全權），與新建一致；缺少任一 scope 回 FORBIDDEN，連原 actor 也不能繞過。唯讀與背景 reconciliation 不依賴使用者在線或 resume 授權。

`NeedsAttention` 從外部 `ctx.step` 傳出時，operation 停在 `needs_attention`，該 step 保留 `uncertain`。重啟或 resume 都先執行原 reconcile；沒有 reconcile 或回 None 時只讀回等待，不能再次呼叫 fn。回傳已知結果就補記成功；只有 reconcile 明確回 `RERUN`、證明原呼叫未生效時才可重新派送。這與 `ctx.effect` 的本地交易回執不同：effect 與 journal 變更在同一交易內，回滾後可安全重做本地變更。

第一批 action：

| Action | Scope | 外部呼叫 | 回查依據 |
|---|---|---|---|
| `session.send` | operate | `claude:send-message`，`clientMessageId = batc-<operation_id>` | BAT 接受後寫入的 turn 紀錄；沒有紀錄時讀 BAT 的對話，找 id 等於 `clientMessageId` 的 user 訊息。Codex 不保留這個 id，回覆遺失的 Codex 送字無法證明，會停在 `uncertain`／`needs_attention` |
| `session.answer` | operate | `claude:resolve-ask-user`／`resolve-permission`；必須帶 `tool_use_id` | 那個 tool use 已不在 pending |
| `session.interrupt` | operate | `claude:interrupt-turn`／`abort-session` | session 已不在 streaming |
| `session.permissions` | operate | `claude:set-permission-mode` 或分開的 `set-codex-sandbox-mode`／`set-codex-approval-policy` | 每個設定的已保存正面回執；只看到相符 metadata 不能證明 native reconfiguration 成功，未知 frame 不重送 |

Session action 在建立時先用 registry 與目錄判斷來源，人工、unknown、未啟用 write tier 的主機直接回 403，不寫入 operation；執行時 `service.py` 再做一次資源政策的即時檢查。新 action 以 `ActionDef` 註冊（`api_actions.py`），之後的 GitHub merge、部署、checkpoint 都用同一套步驟與回查規則。

Part A delivery actions 仍由 `POST /api/v1/operations` 受理：`github.pr.update`（integrate，repo opt-in）帶 `params={title?,body?}`、`expected_metadata_digest`；`github.pr.merge` 帶 `params={preview_id,method}`、`preconditions={expected_head_sha,expected_base_sha,preview_digest}`。`delivery.merge_and_deploy` 使用同 merge envelope，加 recipe 與 deploy scope；只部署經驗證的 actual merged SHA。舊 head-only 請求回 PRECONDITION_REQUIRED，點名 preview read。MCP `github_pr_update`／`github_pr_merge` 是 caller-token operation wrappers；`github_merge_preview_get` 為 observe read。新 CLI mutation 也要求 BATC_API_TOKEN。Contract_version 保持 ISO date `2026-10-08`（既有 contract 和本次變更同一天）；capabilities 宣告 metadata_update／merge_scope_preview 與 per-repo allow_pr_update。Part B 宣告 deployment_history／environment_generation／runtime_check／rollback_readiness；缺 verification 的 recipe readiness 點名缺少設定，deploy feature=false（history 仍可讀）。


Part B 的 `deployment.start` 帶 `{source_sha, retry_of?}`（40 hex、optional retry_of 只取 saved identity 且 SHA 相符）；`deployment.rollback` 帶 `{deployment_id}`。兩者及 combined 都需 `{expected_environment_generation, expected_recipe_digest}`，先讀 `deployment_preview`（`GET /api/v1/deployments/preview?recipe=NAME`）。舊 start 缺這兩欄回 DEPLOY_PREVIEW_REQUIRED，message 點名該 read。Generation CAS 在第一個 local deploy.select step，同交易保存 deployment／desired／event，stale operation 異步 failed／ENVIRONMENT_CHANGED；不改 core admission。Combined preview.base_ref 與 recipe.ref 不同為 DEPLOY_SOURCE_NOT_ON_REF（422、zero PUT／POST）；start／retry／SHA rollback 在 dispatch 前以 ref...sha identical／behind 證明可達，否則同 code、zero POST。

MCP `deployment_start`／`deployment_retry`／`deployment_rollback` 為 caller-token operation wrappers，confirm=true、BATC_API_TOKEN 必填，read-only server 不註冊。Retry 從 `deployment_status` 的 saved identity 建新的 deployment.start，只換新 key／generation／digest，不重新 merge 或換 latest。讀取工具 `deployment_preview`／`deployment_status`／`deployments_list`／`deployment_environment_get` 需 observe。CLI `delivery preview NAME`、`history NAME --cursor CURSOR --limit 50`、`show DEP_ID` 讀同一 journal；`deploy NAME --sha SHA`、`rollback NAME DEP_ID`、`retry NAME DEP_ID` mutation 另必填 `--generation N --recipe-digest DIGEST --key KEY`。`delivery merge --recipe NAME`／MCP github_pr_merge 的 combined 也傳同一對 preconditions，不隱式代選最新。

Deployment success 必須原固定 run attempt＋指定 job completed/success＋目標 environment 無 pending＋recipe runtime 的版本及／或健康證據（至少一種）。API 明示 version_checked／health_checked；job success 不替代 readback。Verifier 只存白名單 response fields；HTTPS（loopback HTTP 僅測試）、無 redirect、timeout／max_bytes 有界，Bearer 只用後端 token_ref。Dispatch 429 是 refusal：依 Retry-After 在 wait_max_s 內等待，每次再送前精確查 token，找到即採用；lost write 查不到仍 uncertain，不重新 POST。

Cancel 不取消 provider run／queue：provider 未終態時仍持有環境 slot 與 recipe deploy lock，包含 cancelled combined merge、dispatch 與尚待定位的 on_merge。Combined on_merge 取消後若 merge 才落地，核對 reviewed head／實際 merge SHA 可達 recipe ref，再追 exact push run 至終態；legacy 未終態亦占用 repository（不分大小寫）／environment 的跨 recipe slot。Settled history 不再回查 provider／runtime；current run／runtime 與 unresolved locate 依 `[github] deployment_reconcile_interval_s`（預設 300、範圍 60–86400 秒）節流，cadence 跨 restart，相同證據不增 row version。Stopped provider run／legacy run／未決 merge 使用 per-deployment poll_due，初始 15 秒，provider state 不變每次倍增至設定上限，改變回 15 秒；interval／digest／checked_at 跨 restart。成功 read 清除 reconciliation_error；非預期單列例外記 RECONCILE_FAILED 並繼續下一列，只在內容變動時寫入。只讀 reconcile_deployments 以原 snapshot 查 provider facts，終態才釋放，不 dispatch／resume／改 cancelled operation。晚到舊代不升 current，記 superseded；runtime 舊版使 current=null、ENVIRONMENT_VERSION_DRIFT，保留 last_verified，無自動重派。Rollback 是同 recipe/environment saved verified SHA／artifact 的新 generation／operation／run；API 列 not_undone，刪／inactive record 不回退。

Deployment tables 是每次 open 的冪等 DDL，不佔 user_version。Reviewer 分配 history data step 3：僅 version=2 時一交易回填舊 operations results／refs、設 3；crash rollback、重開 no-op。Old successes 為 unverified，never current／rollback；舊非終態已 dispatch 僅查原 run，未 dispatch 停 DEPLOY_PREVIEW_REQUIRED。Observation step 2 由 #35 提供，本包不代做。

Metadata uncertain write 從 step.started_at 起滿 600 秒，GET 仍為 before 就在 delivery 表保存 not_applied／PR_METADATA_NOT_APPLIED，釋放同 PR 的 admission lock 並停止背景 GET，永不重 PATCH。Cancelled 保留 cancelled；UNCERTAIN_UNRESOLVED 保留原 needs_attention audit，resume 只讀 settlement 並以 PR_METADATA_NOT_APPLIED failed。新操作仍比較 metadata digest，晚到的舊寫入會在下次寫前被 PR_METADATA_CHANGED 擋下；GitHub 無 CAS 的窗口仍依 [delivery.md](delivery.md) 說明。

未過期且同 repository／PR／method／digest 的 merge_preview 重用 ID，原 expiry 不延長；保存時清除 expired 超過 24 小時、未被非 terminal merge／combined operation 引用的 rows。Files 只回 filename／status／additions／deletions／changes／previous_filename，不回 patch／blob／contents URLs。Dashboard 的事件重載使用 from_event=true，仍讀 PR／checks，同 head／base 的完整 scope 每 60 秒最多一次；手動讀取重新預覽。等待 checks 只核對 head／base，完整 scope 在首次 submit step 前重新核對；暫時 scope read failure 可 resume，不建立 failed submit step。merge.verify 受影響 PR 的 merged_after=true／independent=true 代表它在本次 merge 之後獨立落地，允許通過；最近 PR 列表按 updated desc，只讀到 admission 時間，已保存 candidates 仍全部驗證。

## 資源目錄

`inventory.py` 用自己的 read-only `Fleet` 輪詢每台主機：client 核心拒絕所有寫入 channel，所以觀測不可能改動 BAT。結果寫入 `hosts_observed` 與 `sessions_observed`。

- 主機連不上時保留最後一次的列，標 `stale: true, stale_reason: host_unreachable`，不刪除。
- 一列要連續兩次成功列舉都沒出現，才標 `gone`。`workspace:load` 回 null 或格式不對時算失敗，不當成空清單。
- 一次輪詢沒觀測到的欄位沿用上次的值：讀 meta 失敗的 session 保留上一列；這次沒重讀 state（`auto` 只在 session 執行中才讀）時保留上次的 pending；活動時間只會往後。
- 每台主機各自排程輪詢，慢的主機不會拖住其他主機。已從設定移除的主機不出現在列表。
- Inventory 的 host row 與 session `state.evidence.connection` 新增 `valid_until`（上次成功觀察 + `stale_after_s`）；`state.evidence.activity.valid_until` 使用活動軸自己的成功觀察時間。未知觀察為 null；到期（含邊界）或既有 stale flag 都不得宣稱 current。欄位為 additive，不增加 probe 或 timestamp-only 事件；GET 只讀取快取。
- Host stale 在讀取時推導，不向 sessions 發送 host flap 事件；只有 session-specific 缺席／gone／scope change 記 stale/fresh。
- 首次觀測寫 `session.added`；值或非時間欄位 freshness 改變寫 `session.updated`，gone 後重現寫 `session.reappeared`。三者的 body 都帶 `fields_stale` 與 `field_evidence`；digest 與 `changed_fields` 包含這兩欄，即使沿用的 loaded／streaming 值相同，meta 失敗及恢復也會通知。`field_observed_at`、`last_activity_ms` 等時間單獨變動不寫 update；連續相同失敗／成功不寫事件。
- 欄位 freshness 與 `session.stale`／`session.fresh` 的單一 specific reason 分開，可同時 stale。成功列舉缺席的 `session.gone` 規則沿用上文。
- `order=activity` 的分頁鍵是活動時間；翻頁期間活動時間變動的列可能重複或漏掉，要完整清單用 `order=id`。
- `GET /api/v1/sessions` 用 keyset 分頁（`order=activity` 或 `id`）。游標綁定篩選條件；第一頁回 `as_of`（當下事件游標），之後以 `/api/v1/events?after=as_of` 追變化，不必反覆重列。

設定：

```toml
[api]
inventory_interval_s = 60   # 每台主機的輪詢間隔；失敗時指數退避到 600 秒
stale_after_s = 180         # 超過這個時間沒成功觀測就標 stale
activity_every = 5          # 每 N 次才查 transcript／archive 的活動時間（最慢的部分）
allowed_origins = []        # 額外允許的瀏覽器 Origin（loopback 已允許）
```

## 事件

`api_events` 是單一、持久、單調的游標，涵蓋 task、operation、session、host。Task 事件在同一個交易中寫入 `events` 與 `api_events`；舊 journal 開啟時以 `PRAGMA user_version` 一次性回填。`work_events`（Hermes 的里程碑 feed）與 webhook 推播仍用原本的 `events.event_id`，不受影響。

- 所有事件增加 provenance context；`history.backfilled` 預設不進 events／SSE，明確 `kind=history.backfilled` 才顯示，next_cursor 仍跨過隱藏 seq。`related_resource_type/id` 查資源索引，原 `resource_type/id` 仍是直接主體。完整契約見 [observation.md](observation.md)。
- `GET /api/v1/events?after=N&limit=M` → `{events, next_cursor, head_cursor, has_more}`。
- `GET /api/v1/events/stream`：SSE，支援 `Last-Event-ID`，15 秒 keepalive，最多 16 條同時連線（每個 actor 最多 8 條），單條最長 30 分鐘。串流每 5 秒重新驗一次 token，撤銷或過期後就結束。瀏覽器 `EventSource` 不能帶 Authorization，Dashboard 以 `fetch` 讀串流。

第二版 §14 的續接契約見 [dashboard-sync.md](dashboard-sync.md)：observe-only bootstrap 在 snapshot
讀取前保存 signed checkpoint，回 journal authority／principal identity。Events 帶 checkpoint 才增加 sync
metadata，整頁處理後一起保存 next cursor/token；舊事件回應 shape 不變。超前、retention 缺口或 anchor
改變回 409 EVENT_CURSOR_RESET（resnapshot=true、preserve_drafts=true）；SSE 使用 sync.checkpoint／
sync.reset control events。沒有新增 retention job，api_head 保留 allocator high-water。
Checkpoint 續接限未篩選 feed；帶 checkpoint 同時指定任一 kind／直接或 related resource 篩選，
JSON 與 SSE 均在事件讀取及 stream headers 前回 422 INVALID_REQUEST，不能簽發跳過非 matching 事件的 token。

資源 `…/history` 使用安全的遞迴摘要：reason／previous_reason 只保留已知固定 enum 或 null；caller 的 request_ted／task result／command conflict／operation diagnostic prose 移除，保留原已記錄的機器 reason_code／error_code。所有 title、status_reason、git_author claim、scalar body/request/response/evidence 等 prose 入口排除，含 history.resource 及 saved_snapshot；來源 evidence 只留結構化表／ID／enum／hash。Scalar source 只允許固定來源 enum，ref／external_ref 只允許無空白的 ID/Git ref/URL token；原 journal 與既有 work_events 的文字不改。完整 producer/value/shape 稽核見 [observation.md](observation.md)。

History 的 since／until 是 inclusive UTC epoch seconds，按 occurrence 篩選，排序仍按 seq。Context 缺少 occurred_at_epoch 才以 api_events.created_at fallback；明確 JSON null 是未知發生時間，不符合任何單邊／雙邊界線。無界線仍顯示該 fact 且 occurred_at 為 null。Coverage.unknown_occurrence_times_excluded 在有時間界線時為 true（表示排除規則，非筆數）；無界線為 false。Coverage.first_recorded_at 仍是最早 journal 記錄時間，可為 migration 時間，不能當發生時間。

History／relations 的 opaque cursor 在任何 journal read 前驗證 version、filter hash、as_of 型別與 key；之後才讀 head 驗上界並查資源／結果。History key 為 int，relations key 為恰兩元素的 [int, str]；bool 不是 int，null 不是有效 key 或元素。錯 key 一律 `INVALID_CURSOR`／422，包含資源沒有 rows 或 execution_id／include_closed 篩掉全部 rows 的情況；合法游標的分頁與 as_of 不變。

## 路由

| 方法與路徑 | Scope | 說明 |
|---|---|---|
| `POST /api/v1/artifacts` | manage | artifact.upload intent；body 為 target／params／preconditions，Idempotency-Key，?wait |
| `POST /api/v1/artifact-capture-previews` | observe | host/session_id/relative_path；唯讀人工單檔 evidence，10 分鐘 credential-bound signed preview，不建永久 preview rows |
| `POST /api/v1/artifacts/uploads/{op_id}/content` | manage＋同 actor 或 admin | body 前驗證 action／state／Content-Length；只收 application/octet-stream，拒絕 chunked；operation 自己的 staging |
| `GET /api/v1/artifacts?limit=&cursor=` | observe | 分頁 ID 與 latest ready 的展示 metadata；輸入選擇仍須精確 ref |
| `GET /api/v1/artifacts/{art_id}/revisions/{revision}` | observe | immutable metadata 與 materialization evidence |
| `GET /api/v1/artifacts/{art_id}/revisions/{revision}/content` | observe | attachment download，nosniff／no-store；無 inline preview |
| `GET /api/v1/version` | 無 | connector、api_version、contract_version |
| `GET /api/v1/capabilities` | observe | actor、scopes、主機 tiers、actions 與是否允許 |
| `GET /api/v1/bootstrap` | observe | journal／principal identity、snapshot 讀取前的 checkpoint、既有 read models 首頁；非跨頁 atomic snapshot |
| `GET /api/v1/hosts` | observe | 主機可達性、stale、最近 discovery；host/discovery/after/limit 可讀 scope |
| `GET /api/v1/hosts/{host}/discovery` | observe | 每 profile 最新 scope、authority、outside_scan 及 discovery.changed 事件分頁 |
| `GET /api/v1/sessions` | observe | Keyset；host/provenance/access/attention/include_gone/order/cursor/limit，加 profile_id/project_id（可多值）/work_item_id/execution_id/provider/has_tab/loaded/streaming/lifecycle/stale/relation_scope（current/history） |
| `GET /api/v1/sessions/{host}/{id}` | observe | 一列與連到它的工作項目；`live=true` 另附即時資源政策判定 |
| `GET /api/v1/sessions/{host}/{id}/history`、`…/relations` | observe | Journal-only 分頁 timeline／使用區間；history 固定 as_of、actor evidence、版本 |
| `GET /api/v1/worktrees/{wt_id}`、`…/history`、`…/relations` | observe | 只讀已知 creation intent 身分；不掃主機／Git，沒有 ownership grant |
| `GET /api/v1/tasks/{task_id}/sessions`、`…/history` | observe | Execution 的 lead/reviewer/follow-up 關係分頁；不以最新 session pointer 取代歷史 |
| `GET /api/v1/sessions/{host}/{id}/messages` | observe | 經 read-only fleet 讀對話；`reading` 隨當次讀取附 `incomplete_reasons`（`archive_unconfirmed`／`archive_budget`／`message_ids`）與可選 `unindexed_count`，見 [session-reading.md](session-reading.md) |
| `GET /api/v1/policy` | observe | mutation 清單與各主機設定 |
| `GET /api/v1/connections?installation_ref=…&after=…&limit=…` | observe | 僅目前 principal 與 installation 的投影；limit 1–200，預設 50，`next_after` 分頁 |
| `POST /api/v1/connections/preview` | observe；解析來源或 setup 另需 manage 與原 personal identity | 唯讀連線 before／after 與外層 review_digest；setup.connection.apply 必須附 native_plan 並原樣回顯，缺少回 NATIVE_REVIEW_REQUIRED；來源沿用 discovery 的 resolver／validator 及拒絕碼，不 probe、不寫入、不建立 operation |
| `POST /api/v1/connections/receipt` | manage 與原 personal identity | 固定 v1 四產物 native 收據；native_review_digest、phase、code、observation 與結果矩陣依 connection_contract.json。保留部分／缺少／不可讀及放棄證據；僅完成原 native_effects，不受理新意圖 |
| `POST /api/v1/connections/handoff` | manage 與原 personal identity | exact body `{contract_version,installation_ref,operation_id,mode}`；claim 記錄 fence；readback 需原 sent fence，終態／過期仍回傳原 native_handoff 與 read_only:true，不續期、不派送；缺少／不支援版本回 CONNECTION_CONTRACT_UNSUPPORTED |
| `GET/POST /api/v1/operations` | observe／依 action | 列表；建立（`Idempotency-Key` 標頭或 `idempotency_key`，`?wait=0-60` 秒，格式錯誤時在保存前回 422） |
| `GET /api/v1/operations/{id}`、`POST …/cancel`、`POST …/resume` | observe／actor 或 action scope | 含步驟；取消見上；resume 只用於 `needs_attention` |
| `GET /api/v1/events`、`/events/stream` | observe | 事件分頁、SSE |
| `GET /api/v1/tasks/{task_id}` | observe | 既有 `work_status` |
| `GET /api/v1/repositories/{owner}/{repo}/pulls/{number}?method=&from_event=true` | observe | PR title/body 與 metadata_digest；重用／保存 immutable mpv merge_preview（完整 commits、檔案摘要、stacks／chains／indirect PRs、blocking）；事件 reload 60 秒 scope 節流，SHA 變立即刷新，省略 from_event 為手動預覽 |
| `GET /api/v1/delivery/previews/{mpv_id}` | observe | 讀保存的 merge scope／digest／expiry，不刷新來源 |
| `GET /api/v1/deployments/preview?recipe=NAME` | observe | repository ID、recipe digest／readiness、generation、desired／current／last_verified／observed、ordering／rollback limits |
| `GET /api/v1/deployments?recipe=NAME&cursor=&limit=` | observe | keyset history（created_at／dep ID，預設 50、上限 200），offline 可讀，identity／evidence／rollback eligibility |
| `GET /api/v1/deployments/{dep_id}` | observe | fixed identity、run／attempt、operation／provider URLs、state／is_current／evidence／已過濾 runtime_evidence／reconciliation_error；missing DEPLOYMENT_NOT_FOUND |
| `GET /api/v1/deployment-environments/history?recipe=NAME&cursor=&limit=` | observe | 同 repository／environment 的跨 recipe keyset history，含 legacy；同既有 status／cursor，offline 不呼叫 GitHub，Dashboard 每頁 5 筆；invalid cursor 回 422 `INVALID_PARAMS` |
| `GET /api/v1/deployment-environments?recipe=NAME` | observe | desired generation／current／last_verified／observed_at／slot／attention；已移除 recipe 仍可讀保存資料 |
| `GET /api/v1/integrations/candidates?host=` | observe | 可放進 PR 的 agent 成果與 checkpoint，及送過的 PR |
| `GET /api/v1/integrations/previews/{ipv_id}` | observe | 預覽文件與是否過期 |
| `GET /api/v1/integrations?repository=&pull_number=`、`/integrations/{op_id}` | observe | 一個 PR 的整合紀錄、一次整合與各來源 receipts |
| `POST /api/v1/cleanup-previews` | observe；discard 另需 cleanup_discard | target 四類與 per-item choices，純讀 signed preview；TTL **15 分鐘**（integration 一小時） |
| `POST /api/v1/operations` action=cleanup.apply | cleanup；discard 另需 cleanup_discard | preview_token／preview_id／preview_fingerprint；PREVIEW_MISMATCH／EXPIRED／BLOCKED／STALE 必須重新 preview |
| `GET /api/v1/cleanup-retained` | observe | 實際可讀 refs／objects 與 unavailable；host/resource_id/query/limit/cursor；Part A 無 restore |
| `GET /api/v1/cleanup-tombstones`、`/{resource_id}` | observe | query/original_id/host/work_item_id/kind/limit/cursor；永久原 ID aliases、位置、原因、PR、receipts |
| `GET /api/v1/projects`、`/projects/{prj_id}` | observe | 專案樹與統計；一個專案與它的工作項目樹（[work-items.md](work-items.md)） |
| `GET /api/v1/work-items`、`/work-items/{wi_id}` | observe | 跨專案工作項目（`pending=true`：等人決定；`unread=true`：此 principal 尚未標記的工作版本）；詳情含獨立的 completion 與 reading，見 [閱讀合約](work-item-reading.md) |

錯誤格式為 `{"error": {"code", "message"}}`：401 未驗證、403 權限或資源唯讀（代碼同 resource-policy）、404、405、409 冪等衝突、422 參數錯誤、502 BAT 錯誤。

## 附件（Part A）

`capabilities.artifacts` 公開 file／selection／store／MCP／upload window limits，以及 host helper 配置與已觀測 readiness。B1 增加 capture.manual_single_file、snapshot=false、preview_ttl_s；MCP 增加 artifact_capture_preview／artifact_capture，沿用 observe＋manage 與同 credential 的 signed preview。`artifact.capture` action target={preview_id}、params={preview_token}、preconditions={expected_fingerprint}，成功回既有 ArtifactRef；不接受 client source lineage。Confirmation 經 operation_submit 的 checkpoint.continue.revalidate。沒有 materialize action、store delete 或新 BAT channel。完整參數與錯誤見 [artifacts.md](artifacts.md)。

## 執行限制證據（A10）

既有 session reads 帶 `write_scope`、`confinement` creation snapshot 與 `current_verification`；`/hosts` 及 `capabilities.hosts[].confinement` 帶 account check 與 agent 可達選項。Cached 舊列補 unknown evidence，不改資料庫；stale 不宣稱目前已 verified。`/tasks/{id}` 的 `session_confinement` 顯示 Task Service 相容 gap。Checkpoint preview、continue／repair operation refs 與結果帶同一證據；無新增 route 或 MCP tools。權限與批准的穩定拒絕見 [confinement](confinement.md)，A10 尚待 W12 live run。

`GET /api/v1/capabilities` 的 `hosts[].confinement.host_account.start_effect` 描述新 start 的帳號查核流程，與 reason 一起回傳。MCP `capabilities_get`、CLI／MCP host reads 用同一後端 projection；GET 不觸發 live check。`start_account()` 在真正 start 時跑 live check，並用同一 `account_start_effect()` 規則決定拒絕。

Host-account verdict 另含 `checked_uid` 與 `channel`：status、method=sudo_exec、ssh_alias、auditor_uid、bat_uid、bat_account、closure（schema_version/status/interpreter/roots/entries_remaining）、ptrace_scope（不可讀為 null）與 preflight evidence。Verified 須符合操作者宣告的 check_ssh_alias／check_uid／bat_account／expected_uid，auditor 與 BAT 的 UID 必須不同。無可信 alias 直接回 unknown／check_channel_untrusted，不跑 BAT 帳號的登入命令；此 reason 和其他 hardening gaps 一樣為 fallback_default，受限 Claude 用 default，不啟用 acceptEdits。Verified 另需完整 pre-interpreter closure proof；unknown／check_executable_untrusted 包含未知 layout 或不完整 gate，仍 fallback_default。同 UID hostile process 不在此證明範圍。舊同帳號／directory-only cache 不沿用，GET 仍只讀。

| start_effect | 意義 |
|---|---|
| `verified` | 帳號已查核。新的受限 Claude 可用 acceptEdits，啟動前仍會再查；不是既有 session 的升級。 |
| `recheck` | 宣告帳號但尚無 fresh evidence，reason=unchecked_or_stale。啟動時再查；通過、支援的加固 fallback 或拒絕由 live 結果決定。 |
| `fallback_default` | Unknown 的 reason 是支援的環境加固缺口；受限 Claude 用 plain default，不啟用 acceptEdits。未宣告帳號也用此 value，跳過帳號 check；一般 operator／Task policy 不變。 |
| `refused` | Mismatch 或其他 unknown；HOST_ACCOUNT_UNVERIFIED 拒絕 Claude 與 Codex 的新 start。 |

Dashboard 依 start_effect 選啟動提示，不把所有非 verified status 當成 blocked、不在 JS 複製 reason 清單。Fallback／refusal 保留 reason code；Codex 保留原 sandbox 提示，refused 時先顯示拒絕。

觀測 Part A 使用同 journal 的讀服務：MCP 只新增 inventory_session、inventory_worktree、resource_history、resource_relations 四個 tools；discovery 是 inventory_hosts 的參數。CLI 為 batc inventory/history/relations。History、relations、scope 的 GET 不呼叫 host、不寫入 journal；inventory 只保存 latest rows，沒有每 poll revisions。Dashboard 的跨專案歷史、scope 卡及 reopen/SSE 去重是 [observation.md](observation.md) 的 Part B。

## 整理合約（Part A）

見 [cleanup.md](cleanup.md)：preview 只讀、一份 snapshot、每 host 序列化並限制讀取時間，500 resources 上限。
Host target 接受 configured host 或有 resource history 的原 host。Host 移除後，各來源的 session／worktree／
branch／carrier ID 保持不變，全部 retained／OBSERVATION_UNAVAILABLE，不送 BAT／SSH；同 preview 的其他 host 正常 apply。
失敗 integration.preview 的 durable prepare intent 也算 creation history，不必先有 integration_previews row。
Apply 只執行同一 reviewed fingerprint；16 KiB signed token，15 分鐘到期。release_undelivered 保留 commits 與
branch，不需 cleanup_discard；只有 discard_uncommitted 摧毀內容。Accepted actor/scopes/choices 固定，resume
沿用原 OperationService 規則，不再檢查 discard scope；回執記錄 resumer。保留設定 keep/forever/false。
Resumed run 在沒有任何 operation_steps row 時若 expiry／mismatch／early refusal，先釋放全部 own reserved
guards／session markers、pending／running 回執改 failed 並保存 refusal code；已有 step 不走此 release。
完全無 step、只因 read-only failure 留下的 uncertain 回執也在 refusal 時結清為 failed。
Item status=already_absent 是獨立 definitive receipt，result.items 與 summary.already_absent 分別列出，
不算 retained。它只保存經全 plan 驗證的 absence／original IDs，沒有 per-item host call、tombstone／aliases
或 registry cleaned mark；不是 cleanup 移除的證據。Dependencies 接受 succeeded 或 already_absent。
沒有 reclaim item 但有可釋放 cap 的 already_absent active 非 task session（無自己的 worktree 或 carrier
同樣 already_absent）時，preview.ready=true；同一 cleanup.apply 只結算本機 retirement／receipt。
Preview 本身不改 registry；retained carrier、未決或已退休 row 不會單獨開啟 apply。
Confirmed planner stop 只把 matching active row 改 registry status=stopped，已不占 host cap；ACK／read-back 未確認時不改。
Already-absent session 的 worktree 本次 succeeded／already_absent，或沒有自己的 worktree時，matching active、非 task row 的 status 改為
absent_at_cleanup，retirement 記 actor／operation_id／carrier_resource_id。回執 after_state 有
capacity_released=true、registry_status、carrier_resource_id、stopped_by_cleanup=false；不建 session tombstone。
其他 row 不改，capacity_released=false，capacity_reason=not_counted／generation_changed／task_owned／start_unsettled／
carrier_retained／registry_refused／registry_io_failed；success／同 retirement replay 的 reason=null。Registry refusal／I/O
只列 capacity_error={code}，不影響 cleanup succeeded／tombstone，也不轉 uncertain。Starting／uncertain sessions 及
carrier 以 COMMAND_UNRESOLVED retained，不是 already_absent。Confirmed planner stop 若 capacity 拒絕，stopped 仍 true。
Worktree retained 時 absent session 的 active slot 留著供 resume。已退休的 ID 的 drive／client-resume／
same-ID start／registry recovery 回 SESSION_RETIRED (409)；人可用新 ID 經原 cap reserve，ownership 仍 connector_managed。
GET /operations/{id} 的 cleanup_receipts／tombstone 回執包含 completed_phases=[{resource_id,phase,effect,step,result}]、
refused_phases（同形但 error）及 cancel_requested。它們投影 durable steps／operation，含 approved DAG 的
prerequisites，不因後續失敗消失。result.items 是最後一次 progress snapshot；cancel 後以 live cleanup_receipts
或 operation.cancel_requested 判斷取消，不以舊 result.items 的 flag 判斷。effect=additive（preserve）／runtime（stop）／destructive（discard、remove.*）。
Gate 通過後的 process／transport／decode／schema failure 是 uncertain，保留 reservation、只回查。
已完成 runtime／destructive phase 後 refusal，item=uncertain、error.code=CLEANUP_PARTIAL_STATE，
另記 refused_phase／refused_code，operation=needs_attention；解除 blocker 後 resume 以新的 .aN attempt
重核原 preconditions，完成步驟不重做。Discard after snapshot 存在 succeeded step，resume 不採納新內容。
Cancel 已有 runtime／destructive partial 的 item 仍 uncertain、cancel_requested=true、reservation 保留；
cancelled parent 不表示內容保留，也不能 resume。需人工檢視，本包無強制解鎖／takeover。只有未送出或
pure additive、全部已結清的 item 可 cancelled／釋放 guard，回執仍列已建立的 pins。
Legacy batc cleanup／session_cleanup 只評估，apply 回 LEGACY_CLEANUP_DISABLED (409)，指向 resource-cleanup；
auto_cleanup deprecated，只保留解析，不啟用任何 writes。Fanout planner 只 stop，worktree 留給 reviewed cleanup。
Restore、reviewed task cleanup、TaskDaemon tombstone backfill 在 Part B；clones/areas 退休與 refs/batc/* 刪除不在本包。
## Task Service operations（2026-10-08，Part A）

依[統一操作規格](operations-unification.md)的 Part A，以下能力經既有 `POST /api/v1/operations`／`/rpc op_submit`，不新增 task 寫入 URL。舊 RPC／MCP 保留原結果，增加 `operation_id`、`operation_status`；operation succeeded 只表示該次控制完成，不表示 task done。

| Action | target | params | Scope／身分 | 舊入口 |
|---|---|---|---|---|
| `task.submit` | host、workspace | project、original_words 與原 work_submit 選項 | start | work_submit |
| `task.pause` | task_id | abort_current、actor、source_message_id | operate | work_pause |
| `task.resume` | task_id | actor、source_message_id | operate | work_resume |
| `task.mark_stage` | task_id | stage、ref、actor | manage；verified done task | work_mark_stage |
| `session.send` | task_id | text、step_id | 原 task capability／本機 admin 相容路徑；不授予一般 operate token task-scoped 權限 | task_send |
| `task.verify` | task_id | 空物件；不能自填 argv、exit code 或 evidence | 原 task capability／本機 admin 相容路徑 | task_run_verification |
| `task.request_ted` | task_id | reason | 同上 | task_request_ted |
| `task.command.reconcile` | task_id、command_id | 原 outcome、actor、source、evidence、observed_result、turn_ref、candidate／tree、next_prompt | 原一次性 command capability；admin 不能代替 | work_reconcile、task-reconcile |

`preconditions.control_version` 可要求目前版本。舊 task tools 新增可選 `idempotency_key`／`control_version`，CLI `task-reconcile` 新增 `--key`／`--control-version` 並遵守 `--read-only`。key 以驗證 actor 為範圍；相同 key／params 回原 operation，不同內容 409 `IDEMPOTENCY_CONFLICT`。Goose 的原 `step_id` 在未指定 key 時仍是 task send 的重試身分。其他無 key 的舊 task controls 每次使用獨立 request identity，不跨次去重；no-key sentinel／null 投影留在 Part B。舊 work_submit 的 201–256 字 key 由相容 adapter 保存原字串、以 SHA-256 映射到既有 200 字 operation key 上限；通用 HTTP 上限不變。

Task-bound operation 受理時固定 task `control_version`；session target 另固定 host／session／role，存於 `external_refs.admission_binding`（server admission binding，不是 caller precondition）。省略 `preconditions.control_version` 也不能跨 pause／resume 或換 session 執行：第一個 effect 前以 `CONTROL_VERSION_CONFLICT`／`TASK_BINDING_MISMATCH` failed，零 frame／command／task write。request hash 與 caller preconditions 不變；同 key 仍重讀原成功或拒絕。pause／resume 不覆寫較新 incarnation；已成功 receipt／未知 frame readback 沿用原恢復。升級前無 binding 的 operation 保留原 execution-time binding。詳見[盤點與儲存規則](operations-unification.md)。

原 task／continuation／pause／resume／stage／observed verification／request-user／reconcile 的 local effect 與 operation step receipt 同交易提交，不另建 task 狀態表。pause 先保存 paused／control_version，abort 再記獨立 step；等待 task lock 或 verifier 不延後 pause 的持久化。RPC 最多等 30 秒；未完成時以 operation ID 回查，pause 可回已保存的 task snapshot。reconcile capability 只存 hash，消耗與回執原子提交；已消耗的 capability 只能用原 key 重讀自己的 operation，不能建立新控制。

task command receipt succeeded 時，`external_refs.task_id`／`command_id`／`control_version` 已與 receipt 同交易保存，版本來自 command 的 dispatch binding；prepared operator command 也適用。舊缺 refs 的 receipt 在 operation 恢復／讀回前修復 link，不重跑 effect 或派送 frame；standalone operation 不寫 task refs。

CLI `task-reconcile --key` 把 key 同時交原 admin-only capability issuer，依既有 admin secret 綁定 task／command／key，使跨次重試保留 actor。沒有 key 時維持原新發一次性 capability；發行本身只是 connector credential data，不執行 task command，也不授予一般 token 對帳權限。

task-owned session 的 send／answer／interrupt／permissions 都先經 resource policy，再進 daemon 原 coordinator；舊直接 service tools 也經相同 gate。鎖順序為 task → session → host write lock → BAT semaphore；client-resume、answer、abort 與每個 permission channel 在 frame 前重查 journal 的版本與 ownership。任意 before_invoke callback 不授予插隊權限。standalone managed session 沿用原行為。

背景 trusted verification 期間的 pause／版本改變為控制取消：task 保留 verifying／paused，取消的 run 不寫 evidence，不升級 needs_ted／uncertain。resume 後下一個 tick 從頭跑，包括 dependency retry；paused task 不計 verification_deadline。lease lost／binding mismatch 保留目前 state，交既有 owner／修復 binding 後再 resume；真正 verifier error 仍是 verification_error／needs_ted。沒有新增 event 或錯誤碼。

| 409 task code | 處理 |
|---|---|
| `TASK_OWNER_UNAVAILABLE` | pointer／journal／row／owner lease 不可用；連既有 owner，不啟動第二個 daemon |
| `TASK_BINDING_MISMATCH` | registry 與 task ownership 不一致；停止並查 work_status |
| `CONTROL_VERSION_CONFLICT` | 控制版本已改；讀狀態，不自動換成新版重試 |
| `TASK_PAUSED`／`TASK_VERIFYING` | task 暫停／驗證中；不可用 queue、force、continue、approve-pending 或 relay 插隊 |
| `TASK_SEND_NOT_DISPATCHED`／`NOT_ACCEPTED`（task send） | operation 沒有已接受／settled 的原 send command；failed 結果沿用原 key，新的派送用新 key |
| `TASK_COMMAND_PENDING`／`TASK_RECONCILIATION_REQUIRED` | 原 command 結果未證明；由 coordinator 回查／command capability 對帳，不重送 |
| `TASK_STATE_BLOCKED` | current lead／task state 不允許該控制；waiting_permission 不接受 send |
| `TASK_OWNED_CONTROL_REQUIRED` | 低階 failover／worktree／外部 verification 不管理 task-owned session；cleanup 保持 KEEP |

policy admission 仍 403、無 operation row；執行期拒絕保留 failed operation 與原 code。`OWNER_CONFLICT` 是 `batc serve` 啟動拒絕，附既有 owner_id、db_path、pid、endpoint、lease_path；不同 `--db` 也不能建立第二個 fleet authority。

## 尚未涵蓋

Agent MCP 安裝使用 `--principal-only`：只註冊 central daemon reads、operations 與 task adapters，
所有 calls 必須帶 `BATC_API_TOKEN`，不 fallback 本機 admin token／task capability。
`work_status`／`work_result`／`work_events` 的 RPC 讀取亦要求 `observe`；沿用既有回傳格式。
再加 `--read-only` 可完全隱藏寫入工具。預設 operator profile 保留舊 direct Fleet 工具，
不代表 API principal scopes 可約束這些舊入口；agent 不使用它。詳見 [agent bundles](../agent-skills.md)。

- Artifact 的 managed capture 與 accept（Part B2）、跨主機接續（Part C），見 [artifacts.md](artifacts.md)。人工單檔 B1 不提供 dirty snapshot。

- 既有 MCP 寫入工具（`session_send` 等）仍直接呼叫 service；它們受同一套資源政策約束，但不留 operation 紀錄。之後改為經 `operation_submit`。


Deployment Part B 的 observation adapter 使用同一 `/events` feed：
`deployment.selected/updated/verified/superseded/backfilled`、`deployment.drift`，只含保存的 IDs、generation、
state、SHA、run/attempt、固定 error code 與去除 query/fragment 的 HTTPS provider_url。
透過明確 operation/source refs 索引 history，無 binding 時不猜 session/worktree。Step 3 接在 observation
step 2 之後；backfilled snapshot 的 occurred_at=null，原事件不改寫。History capability 列 `delivery_part_b`。

- Part B：其餘 legacy writes 的 operations、no-key sentinel／讀取投影、完整結果與外部 steps 拆分。Task controls 與 A07 共用 gate 已在 Part A 完成。
- `import-bat --output PATH --force` 是 owner 啟動前的本機 config 安裝指令，僅寫指定設定檔，保留現有路徑。
- operation cancel/resume 已授權且 evented，不新增其 control operations；api-token issue/revoke 只改 connector 資料、不觸及 BAT/Git/provider，也不新增 operation。

## Managed setup：連線預覽與受理

| 路徑／欄位 | 已實作契約 |
| --- | --- |
| `GET /api/v1/managed/setup` | 原安裝 personal identity 與 `manage`；新增 `preview_contract_version:1`，其餘 setup state 保留。未宣告此版本時，新 UI 要求先更新 Connector，不自動退回舊式套用。[managed_setup.py:207](../../src/bat_agent_connector/managed_setup.py#L207)、[managed-setup.js:521](../../desktop/src/managed-setup.js#L521) |
| `POST /api/v1/managed/setup/discover` | 明確唯讀查詢，與 preview 相同的 `manage`／personal installation 邊界，固定 route、無 query。輸入 `{revision, import_profile_id, profile_id?}` 或 `{revision, url, fingerprint, secret_ref, profile_id?}`，只能選一種來源。回傳 `{source, profiles:[{id,name}], profile_id:string或null, workspaces:[{workspace_id,name}]}`。`source` 是 [D3 固定 descriptor v1](fleet-configuration.md#d3-source-descriptor-v1固定契約)。只呼叫與 Apply 共用的 `_read_host`，pinned connect、10 秒 ping、既有 channel deadlines；discovery 共用 25 秒總期限（含收尾），每一步使用剩餘 budget，逾時回 `409 SETUP_DISCOVERY_TIMEOUT`；Apply 的 per-step timeouts 保留。profiles/workspaces 各最多 200。唯一有效 profile 可預選，多個則待選擇後再次明確查詢該 profile 的 workspaces。不建立 operation／journal、不消耗 staging、不寫設定，兩個 write gates 都關閉。 |
| `POST /api/v1/managed/setup/preview` | 同一 route table／認證邊界，scope 為 `manage`；無 query，只接受 `{action,target,params,preconditions}`，action 必須為 `setup.host`，target 為 `{host}`，params 沿用候選 builder 的 allowlist，preconditions 只能含 `{config_revision}`。原安裝／personal identity、busy、revision、來源、型別與 parser roundtrip 均先驗；不建立 operation／step、不寫設定、不連 BAT／SSH／Git。[api_v1.py:255](../../src/bat_agent_connector/api_v1.py#L255)、[api_v1.py:1427](../../src/bat_agent_connector/api_v1.py#L1427)、[managed_setup.py:287](../../src/bat_agent_connector/managed_setup.py#L287)、[managed_setup.py:415](../../src/bat_agent_connector/managed_setup.py#L415) |
| 成功回應 | `{preview:{contract_version:1,action,target,config_revision,after_revision,review_digest,changes,source,warnings,effects}}`。changes 是 allowlist 的 `{field,before,after}`，source 是 `{kind,reference,available}`；kind 為 `bat_profile`、`staged_secret` 或 `existing_connection`。不含原始 TOML、秘密本文或解析後的 `file:` 憑證路徑；正式形狀見 [producer golden](../../tests/fixtures/managed-setup-preview.json)。[managed_setup.py:444](../../src/bat_agent_connector/managed_setup.py#L444)、[test_managed_setup_preview_golden.py:16](../../tests/test_managed_setup_preview_golden.py#L16) |
| `review_digest` | SHA-256 綁版本、server／principal／actor、literal action／target／params（只有不透明秘密引用）及 before／after revisions。不是操作授權或加密簽章；Apply 仍重跑原安裝／scope／來源／busy guards。來源或請求改變須重新預覽。[managed_setup.py:437](../../src/bat_agent_connector/managed_setup.py#L437)、[managed_setup.py:482](../../src/bat_agent_connector/managed_setup.py#L482) |
| `POST /api/v1/operations` 的 `setup.host` | 新 UI 帶原 target／params、原 `config_revision` 與 `review_digest`；不符回 `409 SETUP_REVIEW_CHANGED`，在建立 operation 與 probe 前拒絕。沿用 `manage`、既有 action／journal／step；舊客戶端僅帶 `config_revision` 仍受支援，`setup.repository`／`setup.verification` 不接受 digest。已受理的重送仍先依原 key／request hash 查回，不能改成新意圖。[managed_setup.py:482](../../src/bat_agent_connector/managed_setup.py#L482)、[operations.py:566](../../src/bat_agent_connector/operations.py#L566)、[managed_setup.py:671](../../src/bat_agent_connector/managed_setup.py#L671) |
| `connection.reference.apply`／`setup.connection.apply` | 既有 observe／manage scopes；target 為 installation_ref 與 optional existing connection_id，preconditions 為 expected_revision／review_digest。Create ID 由 accepted operation 決定，既有 endpoint／pin／remote profile 不重綁。setup.connection.apply 必須有產生器 native_plan；reference action 拒絕 native_plan。固定交接 JSON、雙摘要、JSON 詞彙矩陣、readback、放棄、Windows-only 發布與恢復規則見 [Durable connection operations](fleet-configuration.md#durable-connection-operations)。`GET /managed/setup` 與 capabilities 皆宣告 connection_contract_version:1。 |
| 秘密與取消 | 已存 BAT profile 的預覽不 staging；貼上秘密可先走既有 `POST /managed/setup/secrets`，畫面必須顯示已發生的私密暫存。取消不建立 operation、不存連線、不 probe／啟動服務，未用秘密仍按既有到期清理規則處理。預覽只在記憶體；新 Apply 才保存原 request／key。已知 operation ID 恢復只 GET，尚未取得 ID 才以原 key／request 重送查回。[managed_setup.py:232](../../src/bat_agent_connector/managed_setup.py#L232)、[managed-setup.js:604](../../desktop/src/managed-setup.js#L604)、[managed-setup.js:633](../../desktop/src/managed-setup.js#L633)、[managed-setup.js:647](../../desktop/src/managed-setup.js#L647) |

此處只實作既有 managed setup 的連線預覽；角色選擇、四檔協調與 native apply 仍依
[Fleet 共用契約](fleet-configuration.md#加入機器共用契約) 的已定／尚未實作界線，不能視為實機驗收。

Discovery 的 capability 為 `GET /managed/setup` 的 `discovery_contract_version:1`。
`/capabilities` 沒有 HTTP route 清單，因此不增加另一份 route advertisement。
WebView 透過既有 `connector_request` 的固定 HTTP allowlist 存取 `/managed/setup/discover`；沒有新增 IPC。
新版 UI 將選定的伺服器 `profile_id` 與 `source_digest` 放進既有 `setup.host` preview params，
import 來源也可以攜帶這兩欄；未帶 discovery snapshot 的舊請求維持原有欄位限制。
既有 review digest 繼續綁定 params、來源及候選設定；preview、admission、執行與寫檔前均重新依 D3
讀來源快照，不一致或 staging 到期時走 `SETUP_REVIEW_CHANGED`。首次 discovery 到期則為 `INVALID_SECRET`；
不符合 admission 的 import 為 `PROFILE_UNAVAILABLE`。唯讀 discovery 的 operation refusals 帶 `admission_refused:true`，證明沒有建立 durable intent。
Preview error 保留原 `{error:{code,message}}` 形狀，不增加此欄位；Apply admission 繼續使用既有證明。
來源 snapshot 參數型別／格式錯誤為 `INVALID_PARAMS`；格式有效但來源已變動／不可用則為 `SETUP_REVIEW_CHANGED`。
Endpoint 在 digest 前共用同一個 canonical validator；空 query／fragment 與加尾斜線後超過 512 字元均為 `INVALID_HOST`。
`GET /managed/setup` 與 preview 仍不執行 BAT／SSH 探測。

既有設定中 endpoint／pin／BAT profile 相同且憑證可用時，UI 可以直接 Review／Apply 管理選項，
不送 discovery 或重貼 token。要探索或改選既有連線的 profile，必須另貼 BAT token；
禁用按鈕旁以雙語說明原因。本輪不以儲存的憑證建立 server-side discovery 路徑。
既有 `HOST_IDENTITY_CHANGED` 拒絕與新 host name 要求仍保留。


### Operation list continuation / 操作清單續頁

`GET /api/v1/operations` orders by `(created_at DESC, operation_id DESC)`. Follow
`next_before` together with `next_before_id` as query `before` plus `before_id` to
include equal-timestamp operations on subsequent pages. Both continuation fields
come from the raw bounded page before visibility filtering; even an empty visible
page can have a continuation. Stop only when the cursor is null. A timestamp-only
`before` keeps the existing strict `created_at < before` behavior. `before_id` needs
a timestamp and a bounded operation ID; invalid pairs return `422 INVALID_FILTER`.

繁體中文：清單以時間、操作 ID 遞減排序；請成對使用兩個續頁值，避免同時刻的操作
漏頁。可見清單為空時仍須依 cursor 續頁；單獨的時間 cursor 維持原本嚴格小於行為。
這只修正既有讀取，不新增操作、意圖帳本或執行權限。
