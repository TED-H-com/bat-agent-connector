# Session permissions：逐 frame durable 合約

基底 main `da66c545120c1b5fb2703277ae6c0e180c309c46`；追蹤 [#57](https://github.com/TED-H-com/bat-agent-connector/issues/57)。
這是[同一產品](../product/realignment-v2.md)的 R01 接續，不是新 scheduler／產品。
沿用[mutation 盤點](legacy-mutation-audit.md)、[operations](operations-unification.md)、
[驗收矩陣](../product/acceptance-v2.md) A01/A05/A07/A08/A10 與 T07/T09–T11。

## 入口、身分與固定意圖

Canonical `session.permissions` 需 `operate`；target 是 `{host, session_id}`（完整 ID），
params 只接受明確的 `mode: default|allow_all`；preconditions 可含非負整數 `control_version`。
MCP `session_set_permissions` 與 CLI `permissions` 保留原 mode default、confirm/read-only/tier，
新增可選 operation key/control-version，以原 principal 呼叫中央。不 autostart、不改走直接 BAT。
Named key 先 replay 原 literal intent；新的 legacy 要求才解析完整 ID／唯一 prefix 並保存 binding。
無 key 每次獨立，不用 BAT message ID 代替 key。既有 cleanup admission enrichment 與 capture
existing-operation credential authorization 不變。HTTP body 不能指定私有 binding／step callbacks。

第一個效果前保存不可變 plan：host／完整 SID、agent kind、原 registry/runtime identity、mode、
固定 options 與排序後 channel/params。Task-owned 只綁原 lead/incarnation/control-version 與同一
permissions command。Claude 一個 mode frame；Codex sandbox、approval 各一個 step。
每個 step 在 transport 前保存 intent，只有 literal `true` ACK 保存成功 receipt；completed receipts
優先重播。未知 frame 永不重送；一個 ACK 不代表 Codex 整組完成。原 operation ID、actor、command
與 partial receipts 保留，沒有另一個 queue 或資料 migration step。

## 最後 frame 與恢復

每個尚未送出的 frame 都重查 host write/allow-all policy、manual/unknown/retired/cleanup policy、
原 session/runtime binding、confinement、task pause/version/owner。使用既有 guard_read 在 client
semaphore 內讀 identity，然後同步執行最後 gates；`on_transport` 才記錄真的送達 transport 的界線。
Claude 必須有正面 idle evidence；streaming 回 `PERMISSIONS_STREAMING`，不保存 deferred raise。
不把缺少 streaming 欄位視為 idle，不暴露 force。未知 agent preset 回 PERMISSIONS_AGENT_UNSUPPORTED，
不套用 Codex setter；admission 沒有 task owner 也必須保持沒有，不能在執行時自動採用新 owner。
Audit 以原 actor/operation ID 記錄各實際 dispatch 的 attempt/result；同一輪 Codex batch 只檢查 rate 一次。
恢復時，已 ACK 的 frames 不重記 attempt；新的未送出 frame 再查 hourly budget，同 operation 的先前
frame 不構成另一個 request 的 interval，但仍計入 hourly budget。

已保存 ACK 的恢復不重讀 live mode，也不因後來 pause／policy 變動抹去 receipt。全部 ACK 後，
允許以固定 plan／原 command 身分完成該 command 的本機 receipt，即使其後要求 cancel 也不抹去已接受結果。
只在精確匹配此 command 的 uncertain marker（task/version/updated_at、原 state、沒有其他 pending command）
時還原其自己的 uncertain 狀態；任何後來 pause／incarnation／state 變更都保留。
Registry projection 使用原 creation/binding/options 的 CAS；若有後來的 owner/policy，記錄 skipped，
不得覆蓋。沒有 ACK 的 step 保持 uncertain；操作 resume 只延續同一 plan／steps，不挑新 session。
尚未送出的後續 frame 仍受原 task gate 約束，不能用成功前綴繞過 pause/version。
若全部精確 channel/params 的 true ACK 已存在，但 local bookkeeping 多次失敗導致 needs_attention，
cancel 只排程原 operation 的 receipt completion；partial/unknown 維持一般 cancel 的未證明 outcome 證據。
Admission host policy／confinement refusal 均為帶原明確 code 的 HTTP 403，且不建立 operation。
目前 worker 在 connect／guard_read 失敗且 on_transport 未觸發時有明確 no-send 證據，
保存 PERMISSIONS_NOT_SENT failed receipt，可在排除連線問題後提交新 key；若已有 ACK 前綴，
原 command 保留 partial/uncertain 證據。Process interruption 只留下 intent 時沒有這項證據，仍不可重送。

## BAT 證據與成功的界線

固定 BAT commit `b7419892fbc9946799b64cca24c2ec8c7fa15c42`，另參照[協定筆記](../PROTOCOL.md)：

- [Claude setter](https://github.com/tony1223/better-agent-terminal/blob/b7419892fbc9946799b64cca24c2ec8c7fa15c42/node-sidecar/src/handlers/claude-session.mjs) 先保存 permissionMode；SDK control 失敗會關 query，仍可回 true。
- [Codex setters/reconfigure](https://github.com/tony1223/better-agent-terminal/blob/b7419892fbc9946799b64cca24c2ec8c7fa15c42/src-tauri/src/codex_app_server.rs) 先改本機 sandbox/approval 欄位，thread/resume 後續可能失敗。
- [Remote routing](https://github.com/tony1223/better-agent-terminal/blob/b7419892fbc9946799b64cca24c2ec8c7fa15c42/src-tauri/src/remote_server.rs) 依序呼叫 setter/reconfigure；sent invoke-error 不能當作沒有改動。

因此 true ACK 只證明 **BAT 接受 permission configuration**；不是當前 SDK／OS 已強制執行的證明。
False/null/object 不是成功 ACK。Lost/error ACK 不可用相同 metadata 補證，更不可重新送 setter。
Task permissions 現有 readback 也無此證據；缺 ACK 保持 uncertain／needs_attention。
Result 保留 host/session_id/agent_kind/mode/calls/note；mode 表示要求的設定，不是推定目前 mode。
Operation detail 的逐 step status/error 與 external_refs.permission_frames、command refs 顯示部分結果；
完整 channel/params/ACK receipt 保存在同一 operation_steps，不另設 authority。

## Legacy bulk/deferred 相容界線

舊內部 `lifecycle.approve_pending` apply 若 `raise_to_allow_all=true`，在回答任何 prompt 前回
`LEGACY_PERMISSION_RAISE_DISABLED`，指向逐項 `session.answer`／`session.permissions`。
此內部路徑的 dry-run 保留，明確 answer-only false 不變。公開 MCP/CLI 的 `approve_pending`
現接[固定 bulk preview／child operations](bulk-approval.md)；apply 必須有原 preview 與明確 selection，
不再呼叫這條舊 raise 路徑。Parent 使用 operate + observe，各 permission child 仍受本合約約束。
歷史 `permission_raise_pending` 只提供待處理診斷；`_raise_deferred` 永不重新採用舊 actor/version
或偷偷產生新 operation，不 auto-apply；回同一 refusal code。新 permissions 不再寫入這種旗標。

## 尚未涵蓋

本合約不定義 bulk prompt plan（另見 bulk 合約）、Goose/Task Service 新引擎、raw channel／任意 options、跨 host permission
交易、native enforcement 或另一個持久化權威。外部 BAT GUI 不參與 Connector 鎖；每個 frame 前
的讀取不能宣稱跨系統原子隔離。Windows installed/live、實際 host confinement 與完整 46 項同 RC
驗收仍需分別留證；mock/CI 不代表正式安裝或真 provider 寫入已驗。
