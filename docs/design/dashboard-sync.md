# Dashboard bootstrap 與中央事件續接

日期：2026-10-08。固定基準 `afa54a8`（Observation Part A）。對應第二版計畫 §10、§14、R04，
驗收 B02、B05、T11。只使用既有 `api_events` 與 persisted read models；沒有第二個 event store、
Task Service 或 BAT channel。Frontend 的草稿與呈現規則由共用 Dashboard 實作。

## 合約

`GET /api/v1/bootstrap` 需要 observe，回傳：

```json
{
  "sync": {
    "version": 1,
    "server_id": "<journal authority UUID>",
    "principal_id": "<actor/scopes/admin digest>",
    "checkpoint": {"cursor": 120, "token": "<signed opaque token>"},
    "head_cursor": 120,
    "retained_after": 0
  },
  "capabilities": {},
  "snapshot": {"hosts": {}, "sessions": {}, "projects": {}, "work_items": {}, "operations": {}},
  "pagination": {"atomic": false, "session_order": "id"}
}
```

先保存 checkpoint，才讀取各 snapshot pages。Snapshot 值沿用既有 endpoint 的文件與分頁欄位：
sessions 使用 order=id、include_gone=true、limit=50；projects／work_items 含 archived，work_items／
operations 首頁 limit=50。只讀本機 persisted state，不刷新 BAT、查 live policy 或執行 operation。
Capabilities 增加 identity={server_id,principal_id} 與 features.dashboard_sync，原欄位不變。

這不是跨 endpoint 或跨頁的 atomic snapshot。Client 以 stable IDs 合併已讀 pages；完整 sessions 巡覽
沿 order=id，其他 read models 保留自己的分頁規則。Snapshot 期間的變更由 checkpoint **之後**的事件
補回，即使 snapshot 已包含變更仍可重複收到，必須按 seq 去重並刷新受影響實體。History 舊頁保留各自
固定 as_of，不能改用 bootstrap cursor 假稱固定快照。

## Client 順序與事件介面

1. 以受信任 configured endpoint 連中央，用自己的 bearer principal 取得 bootstrap。
2. Cache、cursor/token 與 drafts 用 endpoint + server_id + principal_id 分區。切換身份不得顯示舊區資料。
3. 讀需要的 snapshot pages；從 bootstrap checkpoint 開始讀未篩選的中央 events。
4. `GET /api/v1/events?after=N&checkpoint=TOKEN` 保留既有 events／next_cursor／head_cursor／has_more，
   增加同形 sync。TOKEN 的 signed cursor 必須等於 N。處理整頁全部事件／invalidations 後，才一起保存
   sync.checkpoint.cursor 與 token；不能收到 head 就先寫 cursor。新 token 綁該頁 next_cursor 的 anchor。
5. 中斷時保留已處理 checkpoint、drafts、已受理 operation IDs；重連先回查，不自動重送 mutation。

未帶 checkpoint 的 events 回應維持原形狀，但 cursor 超前／失去 retention 仍明確 reset。
完整中央 store 不得用 filtered feed 的 cursor 代替完整 feed。Hidden history.backfilled seq 仍由
next_cursor 跨過，不會停在 migration fact；沒有查詢另一份 event log。
帶 checkpoint 的 JSON／SSE 請求不可同時帶 kind、resource_type、resource_id、related_resource_type
或 related_resource_id 篩選；回 422 INVALID_REQUEST，驗證先於事件交易／metadata 查詢及 SSE headers。
即使篩選結果為空、全部符合，或 token 來自前一頁，也不簽發可跳過其他事件的新 checkpoint。
未帶 checkpoint 的既有篩選介面與 numeric cursor 保持原合約；該 cursor 不能與完整 feed 的 token 混用。

`GET /api/v1/events/stream` 接相同 after／checkpoint，原 numeric id／event／data frames 不變。
每批事件後送無 id 的 `event: sync.checkpoint`，data 為 sync；client 處理完前面的 batch 才保存。
若使用 Last-Event-ID，必須與 token cursor 一致；只有舊 numeric ID 而沒有對應 token 時不能猜 anchor。
可先使用分頁 polling。Stream 每輪重核 continuation；中途失效送 `event: sync.reset` 然後結束。

## 身份、持久化與恢復

`api_sync_metadata` 是 singleton DDL，包含隨機 server_id、HMAC signing_key、retained_after，
在既有 journal 開啟時冪等建立，不讀寫 user_version。Signing key 永不出 API。server_id 識別 journal
authority：重啟與該 journal 的備份保留 UUID，新 journal 使用新 UUID。它不是機器身份證明；完整複製
同一 journal 的兩個端點可能有同一 UUID，因此 client 仍須使用已驗證 configured endpoint 作分區。
principal_id 綁 actor、scopes、admin，不綁 bearer secret；同一 actor 的不同權限不能共用 cache。

PM workspace membership 的變動另外綁在 checkpoint 的 `access_id` digest，不改
`server_id`／`principal_id` 或本地草稿 namespace。每個 actor 的 membership INSERT、role UPDATE、
DELETE 在同一交易更新 `api_sync_pm_access` generation；generation 保留在移除會員之後，也不依賴
事件 retention 或牆上時間。即使兩次輪詢間先撤權再授權，舊 checkpoint 仍須重新取得快照。
Digest 同時涵蓋該 actor 所有 workspace 的目前 ID／role，包含來源 workspace，兼顧既有會員資料的
升級及來源可見性。Token 只帶 digest，不包含 workspace ID、角色列表或撤權事件內容。

Access 不符回 `EVENT_CURSOR_RESET`／`access_changed`，JSON polling 與已開啟 SSE 使用相同檢查。
新授權也需要重新快照，不能假設 grant 前的舊事件仍被保留。撤權後的普通事件與 operation 仍照
目前來源權限過濾，不因通知需求放行。升級前沒有 `access_id` 的合法 token 會安全地要求一次快照。
其他 actor 的 membership 變更與無效／回滾的修改，不讓該使用者的 checkpoint 無限重設。

Client 收到 access reset 時必須立即隱藏舊來源與暫停操作；不能因仍在輸入而延後撤權處理。
重新讀取後，沿用同一身份的未提交草稿、原 request／idempotency key 與 operation IDs，依最新授權
顯示可讀資料。不把保留草稿視為仍能使用被撤銷的來源或繼續原操作的授權。

Token 用該 journal 的 key 驗 HMAC，只證明續接資料，不授予 observe 或其他權限。每次請求仍先驗
bearer。Token 包含 identity、cursor 與該位置最近 event 的內容 digest；每一頁更新 anchor，所以還原
舊 DB 造成的 head regression，或重新用同一 seq 寫入不同 event，都必須重新 bootstrap。若還原後仍
保留完全相同的已處理 prefix，則可以安全讀後續事件。這不是整份資料庫或整段歷史的 cryptographic audit。

目前不做 event pruning／GC，也沒有新增 retention job。Head 使用 api_events 的 SQLite durable
allocator high-water，刪除保留列不讓 head 倒退。DELETE trigger 只記最高失去的 seq，對任何 replay
跨過已刪內容都保守 reset；舊資料缺少首段或全部清空時，同時由現有 rows／allocator 推導缺口。
Validation、event page 與新 token 在同一短交易讀取，不能在另一程序刪除事件後仍回成功頁。

HTTP 在串流 headers 之前的 reset 回 409：

```json
{"error":{"code":"EVENT_CURSOR_RESET","reason":"cursor_expired","resnapshot":true,"preserve_drafts":true,"message":"..."}}
```

| reason | 意義 |
|---|---|
| server_changed／principal_changed | journal authority 或 effective principal 不符 |
| access_changed | PM membership 可見範圍／角色改變，或舊 token 未綁存取版本；身份與草稿分區不變 |
| cursor_ahead／cursor_expired | cursor 超過目前 head，或缺少必須 replay 的歷史 |
| history_changed | checkpoint 的 event anchor 已不存在或內容改變 |
| checkpoint_invalid／checkpoint_cursor_mismatch | token 格式／signature 錯誤，或 after 與 token 不成對 |
| identity_unavailable | 服務沒有可證明的 identity metadata |

Client reset 必須清掉該區觀測 cache、重新 bootstrap，保留該身份區的未提交 drafts 與 operation IDs。
負數／格式錯誤 cursor 仍為 422 INVALID_REQUEST。沒有 token 回 401，沒有 observe 回 403。

## 測試與修改範圍

`dashboard_sync.py` 管 checkpoint／continuity；Journal 僅安裝 metadata 並維持 durable head；ApiV1
增加 bootstrap、capability identity 與 event wrappers。不改 BAT protocol、auth grants 或 event body。

| 驗收 | 測試 |
|---|---|
| B05 | test_b05_sync_identity_survives_restart_without_consuming_a_data_step |
| T11 | test_t11_sync_checkpoint_is_bound_to_journal_and_effective_principal；test_t11_bootstrap_auth_identity_and_checkpoint_before_snapshot_reads |
| B02 | test_b02_restored_journal_detects_regression_and_divergent_reused_sequence；test_b02_retention_keeps_head_and_explicitly_resets_expired_cursor |
| B02 | test_b02_pages_refresh_checkpoint_and_hidden_events_advance_without_shape_change；test_b02_http_cursor_ahead_and_expired_are_explicit_resets；test_b02_sse_delivers_page_checkpoint_then_explicit_reset |
| B02 | test_b02_filtered_checkpoint_refused_before_any_journal_read；test_b02_filtered_page_cannot_mint_or_reuse_a_complete_feed_checkpoint；test_b02_http_and_sse_reject_filtered_checkpoint_before_replay：bootstrap／前頁 token、所有 filter 欄位、空與非空 filtered page 均不跨過未處理事件 |

PM access 回歸另見 `test_dashboard_sync_pm_access.py`：grant／revoke／角色降升、撤銷後重授權、
重啟／retention／升級、rollback；兩個真正 HTTP principals 修改會員後 polling／已開啟 SSE 都
安全 reset，原撤權事件不放行，principal identity 保持一致。

## 尚未涵蓋

- Event retention policy／pruning／GC 不在本包；此合約能辨識缺口但不刪 history。
- 跨頁 snapshot isolation 不提供；舊 dynamic sorts 不升格成固定 snapshot。
- Desktop／browser 的草稿持久化、批次 invalidate 與身份切換 UX 由 R02/R04 frontend 接此合約。
- 同 journal 的完整複本不能靠 journal UUID 區分實體機器；endpoint／transport 身份驗證仍必要。
