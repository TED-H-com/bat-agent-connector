# Sessions 與歷史：觀測、關係與來源證據

日期：2026-10-08。對應《Better Agent Dashboard／Connector 計畫》v1.0 的 §08、§10、§11、§19「Sessions 與歷史」、§28「02 observation」，工作包 W03 剩餘部分，驗收 B01、B02、B03。

Phase 1 規格已審查。Phase 2 分兩步：Part A（本次）實作伺服器、journal、HTTP/MCP/CLI、skills、文件及伺服器驗收；Part B 在後續分支實作 Dashboard。下列 Dashboard 畫面與瀏覽器 reopen／catch-up／SSE 去重驗收都屬 Part B。共用操作、資源政策、checkpoint 與管理資料分別沿用 [api-v1.md](api-v1.md)、[resource-policy.md](resource-policy.md)、[checkpoints.md](checkpoints.md)、[work-items.md](work-items.md)。

與 Task Service operations 整合後，執行中的 task effects 以 operation 已保存的 actor、entry 與
operation ID 建立 observation context；喚醒 scheduler 的另一位 RPC 使用者不會成為這些事件的
actor。事件證據來源為 `operations.actor`，不是繼承當下 RPC 的驗證證據。Context 只供歷史歸屬，
不提供 scopes、admin 權限或跳過既有 admission／frame gate；各 operation 的 context 互相隔離。

## 固定來源版本

| 來源 | 固定版本與本規格使用處 |
|---|---|
| Connector | `566eba6b63e49eeca38383f4029a3a7abec98801`，本工作開始時的 `HEAD` 與 `origin/main`；branch 為 `feat/observation`。套件版本仍為 `0.2.4`。本文件中的「現有」均指這個 commit。 |
| Delivery Part A adapter | Rebase 基準 `208dd13d75f48e77915dda212ae84819ceef508e`（#34）。已接讀 `pr_merge_previews`、`merge.verify` step receipt 與 `pr_metadata_settlements`；其 DDL 不占資料步驟編號。 |
| Delivery acknowledged-conflict adapter | 本輪 rebase 基準 `021b5969e0c1090db15b35b9a461055d4519be4a`（#40）。接讀 acknowledged PATCH 的 conflict settlement，原 metadata control flow 不變。 |
| Worktree relations 修正 | `619cb2e`（#35 的審查基準）。補 seq binding intervals，固定 relations 的 as_of；不改已核准的 Part A/B、worktree ID 或資料步驟編號。 |
| History role 摘要修正 | `bb02099`（#35 的審查基準）。補遞迴摘要的 role 與下列有限 metadata；不改寫 journal 事實或資料步驟。 |
| Relation event links 修正 | `a23f503`（#35 的審查基準）。具名 relation 事件只掛自己，execution fan-out 取原 seq 的有效 relation；live／版本 1 replay 使用同一規則。 |
| Saved fact time 修正 | `79b04fe`（#35 的審查基準）。沒有原 event seq 的回填事實以自己的時間定位，保留 task execution link；不改 live projection，不 rebase。 |
| Relation closure body 修正 | `79b04fe`（#35 的審查基準），接續 saved-fact 修正 `44f6ad5`。新 relation 事件與同 seq revision 使用同一完整 body；legacy closure 重建 command 終點，不猜關閉時間。 |
| Operation refs position 修正 | `3841959`（#35 的審查基準）。Operation event seq 與 resource linked_at_seq 都受 caller 的位置限制；checkpoint runs 也需要當時已存在的證據。不 rebase、不新增資料步驟。 |
| Field freshness 事件修正 | `a133f9c`（#35 的審查基準）。Session 值相同但 meta freshness 改變仍寫 update；不 rebase、不新增 event kind 或資料步驟。 |
| Summary／unknown occurrence 修正 | `2e404ee`（#35 的審查基準）。遞迴摘要限制 reason 為固定 enum、移除其他 prose 入口；顯式 unknown occurrence 不以 migration 時間符合查詢。不 rebase、不新增資料步驟。 |
| Worktree maker 一致性修正 | `8efd987`（#35 的審查基準）。Registry 身分與 ownership classifier 共用 connector predicate 與 creation-root walk，涵蓋 legacy batc/ branch；不 rebase、不新增資料步驟。 |
| Creation-root carrier 修正 | `fbfb75a`（#35 的審查基準）。Parent 只有在 child 共用 carrier 時才回溯；fallback 與 policy 共用同一 parent rule，main-checkout successor 不繼承舊 worktree。不 rebase、不新增資料步驟。 |
| Cursor key 修正 | `4e80b47`（#35 的審查基準）。History／relations 共用 cursor decoder，任何 journal read 前先驗 key；空結果與全部被 filters 排除時也回相同 INVALID_CURSOR。不 rebase、不新增資料步驟。 |
| 計畫 | 本輪以 Tauri 校正計畫 v2.0（2026-10-08）及 product/realignment-v2 為準；cursor 修正對應 v2 §14、B01/B02。原 Part A 的 v1 章節引用保留為歷史對照，不複製私有計畫。中央 Python backend 保留，Hub importer 不在範圍，也不是依賴。 |
| BAT | `b7419892fbc9946799b64cca24c2ec8c7fa15c42`；不代表每台主機都已安裝此版，實際 `serverVersion` 另存於掃描證據。 |

BAT 協定判讀依據如下；Phase 2 開始時重新比對 main 的來源版本及 orchestrator 配發的資料步驟編號，不追逐上游最新版。

| BAT 原始碼（上述固定 commit） | 確認的邊界 |
|---|---|
| [node-sidecar/src/handlers/claude-session.mjs](https://github.com/tony1223/better-agent-terminal/blob/b7419892fbc9946799b64cca24c2ec8c7fa15c42/node-sidecar/src/handlers/claude-session.mjs) 的 `claude.getSessionMeta`、`claude.getSessionState`、`claude.clientResume`、`claude.stopSession` | Meta 可回 null；Claude state 查詢遇缺少 cwd 的記錄會刪除 runtime map entry。`clientResume` 在 session 不存在時會走 resume。Stop 移除 runtime 記錄，不證明對話／資源永久刪除。 |
| [node-sidecar/src/lib/state.mjs](https://github.com/tony1223/better-agent-terminal/blob/b7419892fbc9946799b64cca24c2ec8c7fa15c42/node-sidecar/src/lib/state.mjs) 的 `buildSessionMeta` | 提供 `sdkSessionId`、cwd、`runtimeStatus`；`isStreaming` 也包含 runtime 啟動中的狀態，不能直接解讀為模型已輸出文字。 |
| [src-tauri/src/commands/workspace.rs](https://github.com/tony1223/better-agent-terminal/blob/b7419892fbc9946799b64cca24c2ec8c7fa15c42/src-tauri/src/commands/workspace.rs) 的 `workspace_load` | Workspace 文件可能不存在；null 不能當成完整而成功的空列舉。Connector 實際傳送 profile 的入口見 `service._workspace`。 |
| [src-tauri/src/commands/git.rs](https://github.com/tony1223/better-agent-terminal/blob/b7419892fbc9946799b64cca24c2ec8c7fa15c42/src-tauri/src/commands/git.rs) 的 `git_get_status_native` | 呼叫一般 `git status --porcelain --untracked-files=all`，失敗回空陣列。觀測不走這條 dirty 查詢；沿用 `checkpoints.source_state_script` 的 `git --no-optional-locks`。 |

## 現有與新增行為差異

| 能力 | 現有程式與限制 | Phase 2 行為 |
|---|---|---|
| 目錄 | `inventory.Inventory` 使用 read-only `Fleet`；`hosts_observed`、`sessions_observed` 保存最新觀測，主機失敗保留舊列。`list_sessions` 有 keyset 分頁。 | 保留這套目錄；補 profile、掃描涵蓋範圍、欄位觀測時間、跨專案篩選及關係摘要。 |
| 發現來源 | `service._host_sessions` 聯集 workspace agent tabs 與 active registry entries。Transcript 查詢只補已知 Claude session 的活動時間，不發現所有歷史或人工無 tab sessions。 | 聯集 journal 已知的 session IDs、既有觀測及上述來源，無 tab／歷史 session 可獨立閱讀；明列未掃範圍。 |
| 歷史 | `api_events.seq` 已是單一持久游標，`Journal._event` 同交易投影 task events。尚無 session／worktree timeline；用 `resource_id` 篩事件只會找到直接以它為主體的事件。 | 加上事件對資源的索引與證據 envelope；每個資源的歷史包含相關 operation、command、checkpoint、run、receipt 及連結。GET 只讀 journal。 |
| 步驟 | `operations.OperationService._step_start/_step_restart/_step_done/_step_status` 更新 `operation_steps`，沒有步驟事件。 | 在這些既有交易中追加步驟事實；不改步驟執行、reconcile 或寫入權限。 |
| 關係 | `branches`、`commands` 保留多個 task 與 session；`tasks.session_id`／`reviewer_session_id` 是當前指標。`registry.claim_warm` 轉移最新 owner，另留 `warm_from_task_id`。 | 提供時間及 command 範圍的歷史關係，當前 owner 與歷史參與分開；不把 registry 的最新 `task_id` 套到舊事件。 |
| 狀態 | 已有 loaded、streaming、has_tab、gone、stale。但 null meta 時 `_host_sessions` 填 `streaming=false`；stale 主要於 `Inventory._session_out`／`_host_stale` 讀取時計算。 | 新增有證據的多軸狀態，未觀測為 null／unknown；主機 freshness 在讀取時推導，只記 session 自身的 stale 轉換。Gone 只表示不再列舉。 |
| 來源 | `operations.actor/entry`、checkpoint actor、work item links actor 已有；task events 的 `api_events.actor` 通常是 null。`command_reconciliations.actor`、`ted_actions` 有 caller 自報證據。 | 每事件回相同 provenance 欄位；驗證主體、觀測者及自報 actor 分開。缺資料明示 unknown。 |
| Dashboard | `viewSessions` 只有 host/access 篩選；`viewSession` 有訊息、`started_from`、工作項目及 checkpoint。`streamEvents` 重連續讀，但不先排除重複 seq；`start` 先取 head，沒有完整 reopen 驗收。 | 加跨專案目錄、關係、timeline 與掃描證據；Part B 補 baseline／catch-up／SSE 交接及重複防護測試。 |

操作統一、delivery、cleanup 是平行工作包。本包不實作 Task Service actions／共用 gate、PR metadata／merge preview／新的 deployment history、tombstone／retained refs／restore。Phase 2 只接讀當時已在 main 的正式 journal 契約；未合併的 branch 不是來源。

Delivery Part A 已於上述 rebase 基準合併。本包只加既有 preview／settlement 寫入點的事件與讀模型 adapter，不改其 admission、merge、metadata reconciliation 或背景排程。Delivery 的正式 operation steps 沿用本包的既有 step 投影。

## 資源身分與關係模型

### Session 與 worktree

Session 的 `resource_id` 沿用 `host/session_id`，`session_id` 是完整 BAT ID；provider 名稱或 provider native ID 都不是列表 key。HTTP 的既有 `{host}/{id}` 即此 ID 的兩段，接受 URL encoding，不用前綴匹配。保留 `profile_id`、`workspace_id`、provider 及 `provider_native_id`（目前可從 tab／meta 的 `sdkSessionId` 取得；未提供則 null），移動 tab 或改標題不換資源 ID。

現有設定每個 host alias 只有一個 `HostConfig.profile_id`。掃描 scope 固定這個 alias 的 remote binding 與 profile；journal 保存 binding 的非敏感識別及版本。不同 profile 可用不同設定 alias，不能把相同 provider 的列合併。若同一 alias 改指別的 remote binding／profile，停止覆寫舊身分，回 `DISCOVERY_SCOPE_CHANGED`、保留舊觀測並標 stale；使用新 alias 掃新範圍。本包不替 host alias 改綁建立自動合併機制。舊 journal 未記 profile 的歷史標 `profile_id: null`、`identity_evidence: legacy`，不得把現在的設定追認為當年的 profile。

Worktree 只有在 journal 或可信 registry 有明確建立 binding 時才建立讀模型。使用共享 `resource_ids.worktree_id(host, intent_type, intent_id, slot)`：將 `["worktree", host, intent_type, intent_id, slot]` 以 `json.dumps(sort_keys=True, ensure_ascii=False, separators=(",", ":"))` 編碼，再回 `wt_` 加 SHA-256 前 32 hex。Cleanup 使用同一個函式，先合併的包提供模組，另一包原樣引用。

BAT-made worktree 的建立根節點由共用純函式 `resource_ids.registry_worktree_root(entries, host, session_id, lead_of=None)` 解析。每一跳先用 `registry_worktree_parent(entry, by_id, lead_of=None)` 確認共用 carrier，不能只憑 failover／reviewer 祖先就掛舊 worktree。`by_id` 限同 host；比對記錄的完整 path 字串，不用 cwd／branch 或相同目錄猜測。Registry 順序不影響不同 session 的回溯；缺 parent 停在最後已知 entry，cycle 為 unknown。`registry_worktree_intent()` 與 `resource_policy.worktree_maker()` 共用這個 walk；`registry_bindings()` 的 connector slot fallback 也用同一 parent rule，而非自己的 key list。不發 host 呼叫。

| Parent 證據（依此順序） | 何時回溯 |
|---|---|
| `shares_worktree_with` | 明確共用的 carrier，直接找同 host 的 parent。若該 parent 缺失就停止，不改猜別的祖先。 |
| `failover_of` | shares_worktree_with=failover_of 時已由上一列處理；否則只有 child 的非空 worktree_path 等於 parent 的 worktree_path 才回溯，兼容缺 sharing marker 的 row。Path 不同或 child 無 path 時，child 是自己的 root；不繼續改猜 lead。 |
| `lead_session_id`／reviewer 的 task lead | 非空 child path 與 lead path 相等才回溯。Child 沒 path 時，必須由 task 記錄同時確認 lead session ID 與該 lead 的非空 path。Child 明存不同 path 就不回溯，即使 task path 符合也不覆蓋它。 |

`lead_of(task_id)` 保持相容 ID-only callback；child 自有 path 時可用該 ID 找 lead 再比對 path。缺 child path 的例外需要 callback 回 `{session_id, worktree_path}`，且兩欄都吻合 parent。Observation 只讀同 host 的 tasks.session_id／external_worktree_path，沒有保存 task carrier 時保持 unknown，不把今天的 parent path 追認成 task 當年的 path。Production reviewer writer 本來會複製 lead path，這個例外只處理 task 已保存 carrier 的不完整 row。

Raw CLI 的 policy classifier 不讀猜測的 journal 路徑，也不依賴 task daemon。若 registry root walk 停在沒有 connector marker 的 reviewer，無法僅由 reviewer 的 session 建立紀錄證明 worktree 由 BAT 建立：`worktree_made_by` 回 `unknown`，三個 BAT worktree action 一律 `NOT_A_BAT_WORKTREE`，即使 path 位於 managed root。這涵蓋缺 lead_session_id、只有 task_id 的舊 reviewer；observation 仍可用自己的 journal 查 lead，保留原 identity／history，身分不等於 mutation grant。已證明的 explicit lead／sharing／等 path failover 照原 resolver 回溯，session actions 不受此 worktree 限制影響；不新增資料步驟。

Git history 的 `bcf9745`（0.2.0）建立 lifecycle failover writer 時即加入 shares_worktree_with；此 repo 無更早「failover 已存在但尚未寫 marker」的 writer 證據，等 path 規則是對缺 marker 保存列的保守相容。已檢查全部可見 history：舊規格的非正式 parent key 只出現在 observation resolver／tests，沒有 production writer，因此移除這個 key，不再宣稱它是 legacy registry 契約。

判定 maker 的唯一 predicate 是 `resource_ids.connector_made(entry)`：worktree_made_by=connector、checkpoint_id、integration_operation_id 任一存在，或 branch 以 batc/ 開頭，就屬 connector-made。兩個函式都在 creation root 判定，不能只讀 successor／reviewer 自己的新 row。若目前 row 另有 connector 證據但與 root 矛盾，也保留 connector 的保守判定：policy 不放寬既有 NOT_A_BAT_WORKTREE refusal，identity 不鑄 BAT ID。`resource_ids` 只 import 標準函式庫，不能 import registry／policy／其他套件模組，避免循環依賴。身分仍不是 ownership grant。

| Registry 寫入形狀 | Marker 實際保留情形與規則 |
|---|---|
| `orchestrate.session_start` external worktree；`checkpoints.start_in_worktree` | Root 記 worktree_made_by=connector 與 branch；checkpoint／integration 再補各自 ID。 |
| `lifecycle.session_failover` | same_worktree=true 記 failover_of、shares_worktree_with，確認 start 後複製 worktree_path／branch；不複製 maker／checkpoint／integration ID。只有共用 carrier 才回溯 root。same_worktree=false 記 failover_of 但 sharing marker／worktree_path／branch 為 null，cwd 是 origin；沒有舊 worktree 身分。 |
| `BatTaskAdapter.start` reviewer；`_restore_headless_lookup` | 複製 lead 的 worktree_path／branch，記 lead_session_id；未保證複製三個 explicit maker markers。缺明存 lead 的 reviewer 以 lead_of 找 lead，仍驗證 carrier。缺 path 時需要 task 的 lead/path 雙重證據。 |
| `registry.claim_warm` | 更新 task_id／title／warm_from_task_id／updated_at；同 row 的 created_at、path、branch 與全部 maker markers 保留。 |
| `shares_worktree_with` | 真正的 registry sharing key；lifecycle 寫入，registry／task_bat 驗證，folder_owner 也讀取它。身分解析使用同一 carrier 指標。 |

修正前的兩種不一致：legacy batc/ root 的 policy 判為 connector，identity 卻鑄 registry ID；另有 explicit connector marker、branch 非 batc/ 的 root，其 markerless successor／reviewer 可被 policy 判為 BAT，而 identity 已排除 registry intent。修正後，已證明、有 created_at／worktree_path 且無 cycle 的 root 及上述後繼形狀都用同一 maker 證據。缺 created_at／path 或 cycle 不提供 BAT creation intent，不能把這個 None 當 connector ownership 證明。

根節點屬 connector-made 或缺 created_at／path 時，`registry_worktree_intent()` 回 None；created_at 使用載入 JSON 值的 str()，不重新格式化。Legacy 只有 batc/ branch 的 row 也不鑄 registry ID。`registry_bindings()` 保留其已 journaled 的 checkpoint／integration／task slot，或繼承 parent 的 slot；沒有 slot 就沒有 wt_ ID，保持 unknown，不冒充 BAT 建立。Observation／cleanup 共用純函式解析，其他建立 intent 沿用既有 journal binding。

不共用 carrier 的 child 使用自己的 creation root；有自己的 worktree_path／created_at 時可有自己的 registry intent，沒有 path 就沒有 worktree ID。政策 maker 同樣不繼承舊 carrier 的 maker；其既有預設 bat 值不是 BAT worktree 或寫入權限的證明。BAT worktree 三種 action 在沒有記錄 worktree_path 時明確回 NOT_A_BAT_WORKTREE，包含 cwd 位於 managed root 的非共用 successor；人工 checkout 仍先回 WORKDIR_NOT_MANAGED。這比修正前更嚴，沒有新的 rehydrate／merge／remove grant，也不限制原本可在 managed root 執行的 session actions。

| 建立來源 | intent_type | intent_id | slot |
|---|---|---|---|
| Checkpoint continue | `checkpoint.continue` | continue operation_id | `worktree` |
| Integration repair | `integration.handoff` | operation_id | `repair` |
| Task external worktree | `task` | task_id | `external_worktree` |
| Connector session 的 BAT-made worktree | `registry` | `session_id@created_at`（created_at 完全沿用 registry 儲存值） | `worktree` |

Warm reuse、共用 carrier 的 reviewer／failover successor 或後續 continue 指回首次建立的 slot，取得相同 ID；同 path 的新 creation intent 取得新 ID。Worktree 身分表保存這個 ID 及 binding/path/branch 證據。只有 cwd／branch 或無法證明建立 slot 時不猜 ID，不發送 rehydrate；這是觀測身分，不是 ownership grant。

未被 inventory 看過但已有 command／checkpoint／run 證據的 session 也可取得詳情、關係及 history：`observation: unknown`、`has_tab: null`，並保留真實 ID。未知完整 ID 回 404；已知而 gone 的 ID 仍回 200。Registry 所知但尚未成功列舉者也保留 journal identity，未觀測的欄位維持 null。人工無 tab session 若從未被 BAT 可讀介面或 journal 記錄過，列入 outside scan，不能宣稱已發現。

### Execution 與 session 的使用區間

Execution 沿用 Task Service `task_id`，API 補 `execution_id = task_id`，不另建 tasks database。既有 `branches.branch_id` 是派工分支識別，不是 Git branch。

| Relation 欄位 | 定義 |
|---|---|
| `relation_id`、`execution_id`、`session_resource_id` | 一段使用關係的穩定 ID、原 task ID、資源 ID。`relation_id` 由 execution/session/role 與起始 command ID 衍生；舊資料沒有 command 才用 branch ID 作 anchor。Pending → bound 保持同 ID，後續取得 branch ID 只補關聯。 |
| `branch_id`、`role`、`reason` | 沿用 lead／reviewer 等真實角色與 `start`、`warm_reuse`、replacement／failover 原因。PM branch 沒有 BAT session 時不製造 session 列。 |
| `follow_up_of_execution_id`、`parent_relation_id` | 只來自明確 `tasks.parent_task_id`／continuation 與 journal replacement 證據。Follow-up 的 lead 仍是 lead，以此欄標示後續工作，不因 provider 相同就推斷接續。 |
| `start_seq`、`end_seq`、`started_at`、`ended_at` | 半開範圍 `[start_seq, end_seq)`；時間為 UTC。未證明終點為 null；不拿 task 的最新 `updated_at` 當終點。 |
| `start_command_id`、`end_command_id`、`command_ids` | 穩定 command IDs；每 command 明確連到 relation。UUID 不作數值大小比較；範圍依 intent／binding 的 journal seq。摘要不冒充完整清單，完整 command 事實由 history 的 task command kinds 分頁閱讀。 |
| `status`、`evidence` | `pending`（意圖）、`bound`（已綁定）、`closed`；含依據表／ID 及不完整邊界說明。關係不是資源寫入授權。 |

`Journal.warm_candidates`、`BatTaskAdapter.find_warm/_warm_identity/start` 與 `TaskCoordinator._start` 已有同 workstream、乾淨且 HEAD 為已驗證版本的 reuse 檢查。`commands.payload.warm_session_id`、新 task 的 `branches.reason=warm_reuse`、`task_capabilities_revoked`、`warm_base_recorded` 均已記錄；舊 task 的 branches／commands 不會被 registry owner 轉移刪掉。但目前沒有明確舊／新 execution 的完整區間及 binding event，`command_bind_session` 也沒有事件。

Phase 2 在既有 journal 寫入點補 `relation.opened`、`relation.closed`、`relation.bound`；同交易保存舊／新 task、role、session、binding command 及來源證據。Start intent 是 pending，只有原流程確認身分後才 bound。Warm claim 與 SQLite 不共用交易：以已持久化的 start command 連接 claim 結果；當中斷時保留 pending／unknown，由既有 Task Service recovery 確認後補記事實。讀 history 不執行 recovery、claim、start 或新派工。

`relation.opened/bound/closed` 必須明存 `relation_id` 與 `session_resource_id`。其 resource refs 只保留事件的 execution 與該 session；context.relation_ids 只含該 relation。Worktree link 只從該 session 在事件 seq 的 binding 衍生。不能從 command、branch、execution 的其他 relations、目前 task worktree 或 caller context 加掛其他 session。`relation()` 與 `close_relations()` 都送完整 IDs；沒有任一 ID 的事件屬 malformed，核心事件照常保留在全域 feed，log 與 context.evidence 記 `MALFORMED_RELATION_EVENT`，不寫任何 resource link，也不猜 relation 身分。此規則同時適用 legacy=True。

新寫入的三種 relation 事件，body 必須逐欄等於該事件 seq 的 `relation_revisions.body`。先完成所有欄位，再送事件；不能在事件之後才補 branch、parent relation 或 command 邊界。Closure 先從 `command_relations` JOIN `commands` 按 created_at DESC 查最後一個已連結 command；沒有 command 時 end_command_id=null。一次建好 status=closed、end_seq、end_command_id 與單一 ended_at，再以同一 body 送 relation.closed、更新 observation_relations、保存 event seq 的 revision。Relations endpoint 在這份 body 上另加 command_ids；history 摘要保留上述邊界。`relation()` 的 open/bind 也在 emission 前完成 branch/reason/parent 欄位。

Task 的共同里程碑仍可掛當時所有有效 relations：只限沒有 command／branch 的 `task.*` 事件，例如 state、paused、resumed，不含 relation.*。用 `relation_revisions` 在該 seq 以前（含該 seq）的最後一份 revision，而非目前 mutable body；必須已在事件前出現，status 不是 closed，已知 start_seq < event.seq，且 end_seq 為 null 或 >= event.seq。Terminal task 的事件先掛當時仍開啟的 relations，再各自送具名 closure；之前已關閉的 session 不再收到後續里程碑。Legacy start_seq=null 不代表一直存在，以較早的 revision seq 證明當時已有這段關係。

同一 seq 規則也用於 `_refs(kind="task")` 的 live 寫入查詢：operation 的 task sources、integration preview sources／receipts、work-item task links/unlinks；不能把已結束的參與者加回新事件。Live projection 維持 `79b04fe` 的規則。Inventory 的 `_memberships()` 是目錄查詢，仍保留曾參與 task 的 sessions 作 current/history work-item membership；它不寫事件，也不承諾 as_of snapshot，與 projection 的事件歸屬不同。

沒有原 event seq 的 snapshot 以該列自己的時間定位，不使用 migration 當時的 active relations。欄位如下；沒有可用的指定欄位時，不以最新 updated_at 或其他時間補猜。

| Snapshot 來源 | 使用時間 |
|---|---|
| `work_item_links` | `linked_at`，不是 removed_at |
| `integration_receipts`、`operations`、`commands`、`checkpoint_runs`、`branches` | `created_at` |
| `operation_steps` | `started_at` |
| `checkpoints` | `captured_at` |
| `sessions_observed` | `last_seen_at` |
| `pr_merge_previews`、`pr_metadata_settlements` | `created_at`、`settled_at`（各自原欄位） |

取 backfill 開始前的 journal boundary（原 api_events head + 1），在原 events 中找 created_at 嚴格晚於事實時間的第一個 seq；沒有更晚事件時才用該 boundary。後來產生的 backfill rows 不參與定位。Context.fact_at_seq 保存此位置，occurred_at/occurred_at_epoch 使用同一事實時間；event 排序、cursor/as_of 與 linked_at_seq 仍使用新 fact event 的 seq，不能把它冒充原本就有的 event。

Snapshot 的位置在第一個更晚 event **之前**，所以取 revision.seq < fact_at_seq 的最後 revision，再套上述 open/start/end 條件；更晚才 closed 的參與者仍保留，之後才 opened 的 replacement 不加入。衍生 worktree 也讀這個位置前的 binding，不拿 session 目前的 worktree。Absent/null/空值、非數字、布林、非有限或超出可表示範圍的時間均為 unknown；上述表的 snapshot 不建立 session links，即使 operation target 明存 session 也不補猜，指定時間欄位與 context 時間為 null。明確身分及 binding 的原 IDs/證據仍保留在 snapshot/目錄；先前定義的目前 binding seed 與 tasks.external_worktree 身分事實不經 task relation 推算，維持其原 link 規則。

`_refs()` 的共用位置契約：live event 與 legacy event replay 傳自己的 seq=S，`before=False`，讀 <=S 的證據；沒有 event 的 snapshot 經 `snapshot_refs()` 傳 fact_at_seq=P，`before=True`，讀 <P（inclusive bound=P-1）的證據。`seq=None` 只表示目錄 membership，維持不設限的原行為。Snapshot 沒有可用位置時不將它當目錄查詢：operation/checkpoint_run 不推算 session 或 worktree；task 仍保留明確 execution。所有 branch 與 caller 使用以下規則，不因 backfill event 新 seq 已到 head 就放寬。

| Ref branch／caller | 位置與不變事實 |
|---|---|
| `session` | 直接指定的穩定 resource ID，沒有「全部目前 session」推算；unknown 時間的 snapshot 仍依上文移除 session link。 |
| `task` | 使用原位置的 open_relations/revisions，保留前述已出現、未 closed/end 的條件；snapshot 用 before=True。目錄才包含過去所有 relations。 |
| `operation` | 只 join 該 operation 的原 api_events，要求 event.seq 與 api_event_resources.linked_at_seq **兩者** <= inclusive bound。晚 event 與晚 resource.bound 回掛舊 event 的 link 都不進早 snapshot；seq=None 才含全部。 |
| `checkpoint` 的 source | 捕捉時固定的 host/source_session_id，不隨後來 run 改變，屬指定 checkpoint 的位置無關身分。沒有 checkpoint row 就不猜。 |
| `checkpoint(include_runs=True)`、`checkpoint_run` | Run 的 host/session 是固定 receipt 身分，但 receipt 不一定已在當時存在。優先用具名 checkpoint.continued 的原 event seq；沒有該事件時，用 run.created_at 後第一個非 backfill journal event 的 seq 作保守已知位置。時間不能用或沒有更晚原 event 時，只能從該 run 的 history.backfilled event seq 起確認。Known seq 必須 <= inclusive bound；checkpoint_run 未存在或尚未證明時，fallback 到同 bound 的 operation refs。目錄不限制 run。 |
| `record_event()` 原事件 | Operation 的 sources/preview sources、target.operation_id、integration sources/receipts、work-item refs 均傳原 event seq；operation 也繼承該 seq 已知的自己的 refs，使 live／legacy replay 一致。Legacy 不讀目前 external_refs，resource.bound 只讀原 body 的 refs；live writer 的 external_refs 是同交易當時的值。 |
| `record_event()` 的 history.backfilled、`backfill()` | Saved steps、receipts、links、eventless operations、delivery previews/settlements 的 operation refs 均傳自己的 P，包括 op_id、params.sources、target.operation_id。不能從目前 operations.external_refs 的 session/worktree_path 補掛；row 自己的 request/response 明存 IDs 仍可作該 row 證據。Checkpoint/run 的建立 slot 與目前 binding 的身分 seed 維持各自正式來源。 |
| 其他 resource index reads | History membership、earliest coverage 與 projection_error gap lookup 已以 reader.as_of 限制 linked_at_seq；gap 的來源 seq 也不得晚於失敗 event。Gap 與普通 history 一樣允許已在 as_of 前確認的晚 link，不假裝原投影成功。全域 /events 的 related-resource filter 另以本次捕捉的 head 限制 linked_at_seq。resource.bound／delivery preview 的 index 寫入仍明記晚 linked_at_seq，不能抹平。Inventory._memberships() 是不承諾 snapshot 的目錄查詢，使用 seq=None。 |

凡 snapshot 的 source/reference 明存 task，`snapshot_refs()` 一律連該 execution；當時沒有 open relation 或時間 unknown 時仍可從 execution history 讀取。涵蓋 work-item task links、integration receipt task sources、eventless operation 的 params.sources／integration preview sources，以及 params.kind/ref 的 task reference；operation step/checkpoint/run 等經 operation 解析來源的 snapshots 也使用自己的時間。單一 execution 時 context.execution_id 同步保留，不能因當前 task 已 closed 使事實孤立。

Session/worktree binding 另存 `session_worktree_bindings(session_resource_id, worktree_id, start_seq, end_seq, linked_at_seq, evidence_ref)`；PK 為 session/start_seq，只有 bind/move 改變才寫入。區間為 [start_seq, end_seq)，end_seq=null 表示尚未見到替換。首次 registry 證據發 `session.worktree_bound`；同一 binding 的重複 poll 不追加事件。原 event 的 projection、checkpoint/run/task 建立事實可直接使用其 seq；移動關閉舊 binding 並開新 binding，不能刪掉舊列。`observation_resources.worktree_id` 只作目前身分摘要，不用來篩選 snapshot 的 relations。

Worktree relations 固定同一 as_of：binding 的 start_seq 與 linked_at_seq 都必須 <= as_of；end_seq 若在 as_of 之後，該 snapshot 視為尚未結束。Relation 仍取 as_of 前最後 revision，只收其時間範圍與 binding 有交集的列。已離開 worktree 的 session 保留當時交集；移動後才開始的其他 relations 不回掛舊 worktree。跨越移動的同一 relation 可在 A/B 都出現，但回 `worktree_ranges` 列各自交集與 binding 證據，command_ids 只含落在該交集且已 linked 的 command。原 relation_id/start_seq 保持不變，分頁 key 仍是原 start_seq/relation_id；重返同一 worktree 以多個 ranges 顯示，不重複 relation row。未知 legacy 起點仍為 null，交集不宣稱可證明更早的起點。

Execution 終態／明確 replacement 可關閉該已確認使用區間；不能由 session idle、host 離線或 registry 最新 owner 推斷終點。Task 結束後又有明確帳本活動時另開區間。舊資料依每 task 的 branches、commands、events 回填，確定的 command 歸屬保留；只有開始下限而沒有結束事實就保留 null。Reviewer 歷史即使當前 `reviewer_session_id` 清掉仍可見。

工作項目的直接 link 沿用 `work_item_links`，unlink 的 actor、時間與 operation 保留。Timeline 對 event 的 work item 歸屬固定在當時的 link 區間；後來 unlink 不抹去舊關係，後來新增 link 不回寫舊事件。目錄的「曾經關聯」可經 task／operation／checkpoint 的明確 link 找到 session，必須標 `via`；單靠同 project／repo 不建立隱含連結。

## 狀態分類

`state` 分軸回傳；舊扁平欄位保留相容，新畫面與工具以 `state` 及其 evidence 為準。Bool 欄位允許 null，不把未觀測填成 false。各軸有 `observed_at`、`source_ref`、`stale`，一次讀 meta 失敗不能刷新上一個 streaming／pending 事實的時間。

| 軸與機器值 | 顯示／可用證據 | 不可推斷 |
|---|---|---|
| `connection: connected / not_connected / unknown` | 最近一次有效 host 掃描成功／失敗／未掃；不以 inventory idle socket 已關閉當斷線。 | 斷線不等於 session 結束。 |
| `loading: loaded / not_loaded / unknown` | Meta 有有效物件／成功回 null／查詢失敗或未查。Null 只證明這次沒有載入記錄。 | 未 loaded 不等於刪除、可安全重新啟動。 |
| `tab: present / no_tab / unknown` | 成功且完整的設定 profile workspace 文件有／沒有該 ID；或尚未取得文件。 | 無 tab 不等於無 session；無 tab 也不授予管理權。 |
| `activity: streaming / starting / not_streaming / unknown` | 有效 meta 的 streaming 與 runtime status，保留原始機器值；未提供或失敗為 unknown。 | `not_streaming` 不等於工作完成；`starting` 不寫成已開始輸出。 |
| `lifecycle: active / ended / unknown` | Active 需有效 runtime 證據；ended 需明確、可連到此 runtime／資源的終止回執或具有已驗證語意的 provider 終態，附 `end_scope: runtime / resource`。 | Turn 完成、task done、meta null、registry cleaned、gone 都不證明 session 結束或永久資源刪除。 |
| `enumeration: present / missing / gone / unknown` | 沿用連續兩次成功且完整列舉缺席才 gone；失敗／部分掃描不累加 misses。 | Gone 顯示「不再列舉」，不顯示「已刪除」。 |
| `freshness: fresh / stale` | 保留 `stale_reason`：`never_observed`、`host_unreachable`、`host_not_refreshed`、`not_enumerated`、`gone`，新增 scope／partial 原因。 | Stale 不覆寫最後已知狀態、actor 或 identity。 |
| `provenance`、`api_access`、`isolation` | 沿用 `resource_policy`，來源、可寫能力、隔離各自呈現。 | 新 relation、人工 actor 標籤、Git author、history 完整度均不提升寫入能力。 |

目前 main 沒有正式 journal stop 回執；interrupt／abort 是 turn interruption，不是 session 終止。未來 stop 若證明卸載 runtime，仍可能保留 transcript／tab，才可顯示「已證實 runtime 結束（可保留對話）」。目前 main 沒有保證覆蓋所有人工 session 的 resource 終態來源，無證據時 lifecycle 保持 unknown，不能為了填 ended 而呼叫 stop 或 resume。若後續 main 有正式 cleanup tombstone，它證明哪個實體被整理就顯示哪個 scope，不宣稱整段歷史消失。

## Discovery scope 的可見證據

延伸 `Inventory.refresh_host`，同 journal 的 `discovery_latest` 只保存每 `(host, profile_id)` 最近掃描；poll 覆寫 status、times、methods、coverage、outside_scan，不累積每輪 rows。只有 status 或 coverage 改變才追加 `discovery.changed`；`/hosts/{host}/discovery` 回最新 scope 與從事件分頁讀出的近期 transitions。HTTP inventory 讀取不觸發 poll。

| 欄位 | 契約 |
|---|---|
| `scan_id`、`host`、`profile_id`、`binding_version` | 固定掃描範圍；DISCOVERY_SCOPE_CHANGED 時 binding_version 沿用 journal 的 binding 欄位，拒絕改綁後的設定，不呈現為目前已掃範圍。Journal ID 不含 token／真實指紋；API 不暴露 bearer 或 credential path。 |
| `attempted_binding_version` | 僅 DISCOVERY_SCOPE_CHANGED 時出現，記錄被拒絕的設定 digest；其餘成功／失敗不帶此欄位。同 profile 改 URL／fingerprint 後，每次 refresh 都保持拒絕、零 BAT frames，原 binding／身分／歷史保留。 |
| `started_at`、`finished_at`、`last_success_at` | UTC；成功與嘗試時間分開。單 session 另回 `first_seen_at`、`last_seen_at`、`observed_at`，沿用原 timestamp，不把重新讀取時間當觀測時間。 |
| `observer`、`authority` | 已知服務主體 `inventory`、`authority.kind=bat_authenticated_read`、非敏感 credential reference ID／設定版本、BAT 回覆的 protocol／capabilities。只有實際回覆的 authority 才算 verified；scope observe 是讀 Connector，並不證明 BAT token 能掃所有 profiles。 |
| `methods`、`coverage` | 每來源的 attempted／succeeded／failed／skipped、workspace IDs、筆數、activity/pending 是否本輪查過。來源包括 `workspace:load`、meta、安全 state、archive、限定 cwd 的 Claude transcript、registry、journal。 |
| `status`、`complete_enumeration`、`errors` | `succeeded / partial / failed / never_scanned`；redacted 穩定錯誤及欄位失敗。完整列舉只描述本次授權集合，不宣稱掃完整磁碟。 |
| `outside_scan` | 其他 profiles／未配置 hosts、從未列舉或記錄的人工無 tab sessions、任意路徑 transcript、未掃 Codex rollouts、未取得的 Git 狀態、connector 記錄開始之前的歷史，附原因。 |

Cheap poll 沒重讀 archive／pending 時明列 skipped，保留舊值及欄位時間。首次 meta 失敗仍可保存 tab 身分，但 loading/activity 為 unknown；後續失敗沿用舊事實並標欄位 stale。Null／壞 workspace 文件一律不是完整列舉，即使 registry 剛好提供非空 rows 也不增加 missing_count。成功文件僅有部分 enrichment 失敗時，已確認的 tab 列舉仍可成功，相關欄位及來源保持 partial。

欄位 freshness 採既有 session 事件：`Inventory.MATERIAL`、digest、`changed_fields` 納入 `fields_stale` 與 `field_evidence` 的值；`session.added`／`session.updated`／`session.reappeared` 的 body 都帶兩欄。Meta 失敗改為 `fields_stale: true`、loaded/streaming 的 `previous_session_meta`；恢復改回 false 與本輪的 `session_meta`／`not_observed`。Evidence 的其他固定值為 `meta_failed`（首次失敗）、`workspace_document`（tab）。即使 loaded／streaming 保留相同值，兩向轉換與單獨 evidence 改變都寫 update；兩個 session 互換失敗時，即使 discovery 的 partial／coverage 不變，也能從 inventory.as_of 之後的 events catch-up。`field_observed_at`、`last_activity_ms` 不加入 digest 或 changed_fields；相同 evidence 的連續失敗／成功不寫事件。比較舊 body 時使用同一組現行 keys，舊版本 cached digest 的格式變更不算 freshness 轉換。

欄位 freshness 不使用 `session.stale`／`session.fresh`；後者只描述 specific_stale_reason，兩種 stale 可同時存在。History 的遞迴 SUMMARY_FIELDS 已保留 `fields_stale`、`field_evidence` 及其中 loaded／streaming／has_tab；本輪不放寬 whitelist，只傳固定 enum 與布林，meta_error、error、note 不進摘要。MCP resource_history 與 CLI history 直接回相同 server body。現有 Dashboard 列表依 session resource_type 事件重新讀目錄，session 頁依自身 resource_id 更新訊息；沒有套用 delta 或 kind 白名單，因此沿用 session.updated 即可通知。Dashboard 多軸狀態／timeline／reconnect 消費仍屬 Part B。

Journal／registry 候選在失敗 poll 前已知的身分仍保留；不讀 registry 充當成功 BAT 枚舉。每台 host 的排程與退避沿用 `Inventory.loop`，慢 host 不阻擋其他 hosts。Host 從設定移除時沿用一般列表不列它的行為；journal 歷史保留，已知 ID 的純歷史讀取回 `scope_status: outside_current_config`，不呼叫已移除 host。

Host reachability 沿用 `host.reachable`／`host.unreachable`，host_unreachable／host_not_refreshed／never_observed 的 session freshness 在讀取時由 host row 推導，不為每個 session 寫事件或索引 host flap。只對 session-specific 的 `not_enumerated`、`gone`、scope change 記 `session.stale`／`session.fresh`；相同 reason／觀測版本只記一次，GET 不寫事件。Daemon 停機期間不虛構 poll；host_not_refreshed 在讀取時推導，重啟不補寫每 session 的逾期事件。

## Timeline 的 journal 來源與事件種類

只使用 journal 已保存的事實，不在 GET 時呼叫 BAT、SSH Git、GitHub、registry 或模型，也不由最新 row 拼出過去每一步。保留 `api_events` 為唯一事件序列；新增 `api_event_context`（seq 為 PK，證據 envelope）與 `api_event_resources`（`seq, resource_type, resource_id, linked_at_seq, evidence_ref`，PK 為前三欄，依資源與 seq 建索引）。Relation IDs 在 context 中保存。同一事件可連多個 session／worktree，但某資源的 timeline 用 DISTINCT seq 只回一次。

Operation intent 當下尚不知道新 session／worktree 時，其 context 保持未知；確認 binding 後，在 `resource.bound` 的交易中索引該 operation 先前的事件，`linked_at_seq` 為 binding 事實的 seq。History 同時要求 event seq 及 linked_at_seq 不超過 as_of，已開的 snapshot 不變。`resource.bound` 帶先前事件 refs，提醒 client 重讀該資源的 history，而不是把早期事件重送成新發生；關聯補全不改原 actor／版本／狀態。Task 歷史歸屬依確定的 command／range 處理，不因目前 owner 更換追掛全部 task 事件。

下表「已有」是現有 api event；「補記」是在既有事實寫入交易中加上事件或完整 fields。所有 rows 的順序／分頁鍵均是**全域 `api_events.seq`**，不使用表內 command UUID、step seq 或 timestamp 作次序。Event ID 回 `event_id = seq`，SSE `id` 也用此 seq。

| Event kind | Journal 來源／既有寫入點 | 事件固定 fields 與關聯 | 狀態 |
|---|---|---|---|
| `session.added` | `sessions_observed`；`Inventory._record_success` | resource、scan、first_seen、material snapshot、fields_stale 布林及固定 field_evidence；只表示第一次被 Connector 看見。 | 已有；補 context |
| `session.updated` | 同上 | material snapshot、changed_fields、fields_stale、field_evidence；freshness 的兩向轉換即使保留值相同仍記錄，連續相同結果不記。Field timestamps 不參與 digest，timeline context 取當輪已知來源；活動時間單獨改變不發事件。 | 已有；補 diff/context |
| `session.worktree_bound` | registry 的首次／變更 binding；`registry_bindings/bind_worktree` | session resource ID、worktree ID、previous worktree ID；用原 seq 保存 start/end 與 linked_at_seq。已知 creation intent 由原 operation/task/checkpoint event 投影，不另發一筆。同一 registry binding 重複 poll 不追加事件。 | 補記；projection 與 index 共用 savepoint |
| `session.gone`、`session.reappeared` | `sessions_observed.missing_count/gone_at`；成功列舉交易 | misses、scan、最後 seen、原身分；重現清除 gone，不重建 ID。現有 reappear 是 `session.updated`，Phase 2 改發專用事件且不雙發。 | gone 已有；reappeared 補記 |
| `session.stale`、`session.fresh` | 保存的 host／session freshness transition | reason、依據 scan/觀測版本、過期與記錄時間。 | 補記；GET 不產生 |
| `host.reachable`、`host.unreachable`、`discovery.changed` | `hosts_observed`、`discovery_latest`；`_record_success/_record_failure` | host/profile、scope、完成度／redacted error；host 層事件不向 sessions fan-out。 | host 已有；scope transition 補記 |
| `operation.accepted/running/waiting_checks/waiting_external/uncertain/needs_attention/succeeded/failed/cancelled` | `operations`；`OperationService.create/_transition` | action、from/to、error code、event actor、entry、target、當時版本與 external refs；結果中有真實 session/worktree 時追加 binding 事實。 | 已有；補 context |
| `operation.step.started/restarted/succeeded/failed/uncertain` | `operation_steps`；既有 `_step_*` | operation ID、step name/seq、保存的 status、摘要或 hash、external ref、reconciled、已知來源／結果 SHA。 | 補記，不變更執行機制 |
| `task.command_intent`、`task.command_accepted/running/settled/uncertain/rejected/cancelled`、`task.command_reconciled`、`task.failover_operator_reconciled` | `commands`、`command_reconciliations`、對應 `events`；`Journal.command/command_status/resolve_send/resolve_failover` | task/command、當時 session/relation、kind/status、message/turn refs、prompt hash；reconciliation 的 caller claim 與可驗證證據分開。 | 已有；補 context |
| `task.command_bound`、`relation.opened/bound/closed` | `branches`、`commands`、關係區間；`add_branch/command_bind_session/change` 與已確認的 reuse/replacement 記錄點 | task、session、role、range、binding command、previous/next execution、reason。 | 補記 |
| `task.task_branch`、`task.state`、`task.paused/resumed`、其他實際存在的 `task.*` | `events` 經 `Journal._event` 同交易投影；`tasks`、`branches`、`observed_verifications`、routing 等作證據 | 原 kind 的結構化摘要、明確 session refs 或當時有效 relation；task 的非 session 里程碑標 execution context，不宣稱每個 session 都做過該動作。 | 已有；完整列出，不受 `work_status` 最近 10 筆限制 |
| `checkpoint.created` | `checkpoints`；`checkpoints._run_create` | checkpoint/operation、source session、擷取版本與當時 HEAD、dirty 三值、excerpt digest、capture actor；不回聊天全文。 | 已有；補 source worktree/context |
| `checkpoint.continued` | `checkpoint_runs`；`checkpoints._run_continue` | checkpoint、operation、新 session/worktree、source session、固定起點、實際 branch；在來源與新資源 timeline 均可見。 | 已有；補 context |
| `resource.bound` | `operations.external_refs`、成功或 uncertain step 中已知的真實 binding | operation、資源、step、checkpoint source、可信度；送字前中斷尚無 `checkpoint_runs` 也能看見預留／已建立資源。 | 補記；intent 不說成啟動成功 |
| `integration.previewed/composed/conflict/resolved/delivered/updated/handoff_started` | `integration_previews`、`integration_receipts`、`api_events`；`integration._receipt_update/_push/_finish` 等 | operation、receipt 的 `(operation_id, seq)`、source kind/id/host、pinned/base/integrated/resolution/delivered SHA、resolver session/worktree、PR 身分；依 checkpoint/run 的明確來源連資源。 | 已有；補當時 fields |
| `delivery.merge_previewed` | `pr_merge_previews`；`pr_delivery.save_preview` 新增 immutable preview 的短交易 | preview ID、repository、PR number、method、head/base/merge-base SHA、commits 的 SHA/parents、affected PR numbers/states、blocking codes；PR title/body、commit message 不入摘要。同一 preview 重讀不追加事件。 | #34 合併後接讀；preview 的 seq 使用全域 api_events.seq |
| `operation.step.*`（`merge.verify` 及其 retry） | `operation_steps`；`pr_delivery.verify_merge` 經既有 `_step_*` | verified、merged_sha、merged_onto_base_sha、base_moved、other_commits_count、額外 commits SHA、affected PR number/state；source_versions 接 expected_head_sha/expected_base_sha，result_versions 保存當時的 merge SHA。 | 既有 step 投影涵蓋，不另造 receipt 事件 |
| `delivery.metadata_settled` | `pr_metadata_settlements`；`save_metadata_settlement` 統一 `settle_not_applied/run_update/reconcile_metadata` 三個寫入點，首次 INSERT 成功的同一短交易 | operation ID、status、code、settled_at；同一 operation 只有首次入帳發事件。含 acknowledged PATCH 的 conflict settlement。觀測者 delivery-service，未證實背景呼叫者時 actor 為 unknown；不回 observed PR title/body。 | #34/#40 合併後接讀；重複 insert/reconciliation 不重複發事件或覆寫原結論 |
| `work_item.linked`、`work_item.unlinked` | `work_item_links`、`work_items._run_link/_event` | link ID、work item、kind/ref、actor、時間、link/remove operation；間接關係附 via。 | 已有；補 link ID/context |
| `task.external_worktree_retained`、`task.initial_session_vanished` | `events`；`Journal.complete_external_cleanup/mark_initial_session_vanished` | 原 worktree path/branch/ref/commit 或消失的 ID、command evidence；保存原事件語意。 | 已有；不改造成 §23 tombstone |
| `history.backfilled`（預設 live feed 隱藏） | migration 的舊 journal 事實投影 | 原表、PK、可證明的 snapshot、原 timestamp、`backfilled=true`、缺少的歷史；只在沒有對應 api event 時補一筆。 | 補記；不虛構過去 transitions |

以下是**條件式事件種類**；只有對應 writer/schema 已在 Phase 2 開始時的 main 才提供 adapter 及 fixture。表內名稱是 observation 的種類分類，不強迫其他包改名；使用其正式 event kind，無來源就不產生、不宣告 capability。

| 未來事實種類 | 來源及 fields | 本包邊界 |
|---|---|---|
| Task Service operation／step 關聯 | operations unification 的正式 operation-command binding，含 operation、task、command、actor、entry。 | 接讀已合併的契約；本包不建立 task actions 或第二套 gate。 |
| 後續 deployment history | delivery 後續正式 journal facts，含 repository/PR、workflow/run/environment、來源／結果版本；只有明確連到 session/worktree 的才入該 timeline。 | Part A 的 preview／settlement／merge receipt 已按上表接讀；後續 provider 契約未合併時不宣告支援。 |
| `cleanup.tombstone`、retained ref、restore | cleanup 正式 tombstone/receipt，含 resource ID、operation、actor、移除範圍、保留 ref/commit、restore 的新實體關係。 | 不掃磁碟補墓碑；不實作 cleanup、refs 寫入、restore；保留歷史身分。 |

Delivery 的 merge envelope 目前只含 repository/PR，沒有 session/worktree refs；一般 merge 因此不出現在任意 session/worktree timeline。不依 PR number、相同 SHA 或路徑推斷來源。只有 operation target／external_refs 明確指向 session，或 external_refs.worktree_id 指向 journal 已知的 worktree，才連結其 steps/receipts。Operation.params.preview_id 可將該 preview 的既有事件回連資源；索引的 linked_at_seq 記實際連結時點，原 event context/actor 不回寫，既有 as_of 分頁不會突然多出舊 preview。

### 固定排序與分頁

Timeline 預設新到舊，`ORDER BY seq DESC`；`order=asc` 可逐筆回放。第一頁在 daemon 單一 journal owner 的同步讀取中捕捉 `as_of = api_head()`，查詢只含 `seq <= as_of`。Next cursor 為 versioned opaque token，內容為 `{v, f, a, k}`；f 是 resource/filters/order 的 hash，a 是 as_of，k 是 last_seq。DESC 下一頁 `seq < last_seq`，ASC 下一頁 `seq > last_seq`，一律限制 `seq <= as_of`。預設 limit 50，上限 200，非法值回 422；換 resource／kind／時間／順序不能沿用游標。`next_cursor=null` 表示這個 snapshot 讀完；回 `has_more`、`count`、`head_cursor`。

`kind` 為精確種類集合，`since`／`until` 是 UTC epoch seconds，inclusive，以 occurrence 篩選，排序仍按 seq。只有 context 完全缺少 occurred_at_epoch（含舊 live／projection gap）才用 api_events.created_at；明確 JSON null 代表 unknown，有任一時間界線時永不匹配。不帶界線仍列出這些 facts，context.occurred_at／occurred_at_epoch 保持 null。Coverage 固定回 unknown_occurrence_times_excluded：有 since 或 until 為 true，無界線為 false；此 flag 表示排除規則生效，不是漏掉筆數。歷史時間可以相同或晚補；晚補的舊事實按新 seq 顯示並標記原時間，不重排已讀頁面。Coverage.first_recorded_at 是 linked/as_of 範圍內最早 api_events.created_at 的記錄時間，可保持 migration 時間，不能當 occurrence。GET 不新增事件。

本輪稽核 occurred_at_epoch 的全部使用處：record_event 只從已知 occurred_at 建 epoch，saved_fact 的 fact_time 缺少／無效值產生 null；fact_position 用原 row 時間，無證據不推測 session。History 篩選使用上列缺 key／null 區別，keyset 及排序一律 seq，不以 occurrence 或 migration 時間補排序。現有 Part A 沒有 Dashboard timeline 分組；Part B 分組須保留 unknown，不以 recorded_at 假代發生時間。

Relations 的 key 為 `(start_seq, relation_id)`，預設 ASC；未知開始的 legacy row 用排序值 0，但輸出 start_seq 仍是 null。Cursor 帶 resource/execution、filters、as_of 與 last key。關閉或綁定的新事實按 seq 保存 revision，因此下一頁仍返回 as_of 當時的 relation，重啟不改頁面邊界。

History／relations 的 `cursor_read` 共用同一驗證流程，endpoint 傳入各自的 key validator。收到非空 cursor 時，先解碼並驗證 version、filter hash、as_of 的整數型別與非負範圍，以及 key；此時尚未讀 journal，包含 head 與資源存在性。History key 必須是 int（bool 不算）；relations key 必須恰為兩元素 list，第一個是 int（bool 不算），第二個是 str。整個 key 為 null、list 內為 null、缺元素或錯型別均回 `INVALID_CURSOR`／422。驗證成功後才讀 head 確認 as_of 不超前，再查資源與結果；沒有 cursor 才開新 snapshot。驗證不依賴任何 row、execution_id／include_closed 篩選或 worktree ranges，不能因空結果而放行錯誤 key。

本輪完整 cursor 稽核如下；只有 history／relations 呼叫 `cursor_read`，已移除 relations loop 內的驗證。其他 observation 分頁的檢查也不依結果列數決定。

| 入口／檢查位置 | 游標契約與是否依賴 rows |
|---|---|
| `Observation.history` | `history_cursor_key` 在 decoder 內先驗 int，再讀 head；事件、索引、coverage SQL 都在驗證後。空 history 仍回相同 422。 |
| `Observation.relations` | `relations_cursor_key` 在 decoder 內先驗兩元素 list，再讀 head；resource、revision、worktree ranges、commands SQL 及所有 row filters 都在驗證後。 |
| `Inventory.list_sessions` | 獨立 decoder 先驗完整 object schema：新游標恰為 `{v:1,f,a,k}`，相容舊 `{f,a,k}`；未知版本、額外／缺少欄位均拒絕。id key 恰為 `[host,session_id]`（UTF-8 字串），activity 恰為 `[sort_key,host,session_id]`，sort_key 為 SQLite signed 64-bit int（schema 與 writer 均為 INTEGER；bool／float／非有限值不接受）。as_of 必須為非 bool 整數且 `0 <= a <= api_head()`，不可 coercion；key、schema、型別驗證先於所有 SQL，head 範圍驗證先於 sessions／resource query。空目錄、排光的 filters、有結果均回既有 `INVALID_REQUEST`／422。保留動態 keyset 與事件 catch-up 契約，不新增 snapshot。 |
| `Inventory.discovery`／`hosts_document(discovery=true)` | 無 opaque key；transitions 的 after／next_cursor 是全域整數 seq。委派 `Journal.api_events` 在事件 query 前驗非 bool、非負及不超過 head；latest scope 的讀取不參與 cursor 判定。 |
| `Journal.api_events`、HTTP events 與 SSE | after 在事件 query 前驗型別／範圍；HTTP adapter 先解析整數，SSE 在送串流 headers 前用 api_events(after, 0) 驗證。是否有 matching events 不影響效力。 |

全域 `/events?after=` 與 SSE 沿用向前的 seq，與 history 的 opaque page cursor 不互換。`events_list` 增加明確的 `related_resource_type/id` 篩選，查 `api_event_resources`；原 `resource_type/id` 仍表示直接主體，不偷偷換語意。Filtered feed 固定本頁 head，掃至該 head 並回最後掃描 seq；即使沒有符合事件也能前進游標，避免反覆讀空頁。Client 必須處理完事件才保存 next_cursor；不把較大的 head_cursor 當作已處理游標。

目錄分頁維持既有 keyset，不增加 observation_revisions 或 snapshot rows。`order=id` 的 key 為 `(host, session_id)`，穩定集合每列一次；巡覽期間新加入的列可能出現或不出現，filter membership 改變透過 events 補追。`order=activity` 的 key 是 `(-last_activity_ms, host, session_id)`，保留動態排序可能漏列／重列的限制。as_of 是第一頁的事件補追起點，不是目錄隔離快照。

Backfill 事件只在 resource history 或明確 `kind=history.backfilled` 的 events 查詢顯示，預設 `/events`／SSE 排除；next_cursor 仍跨過隱藏 seq，不反覆掃描或在升級後重播大量舊事實。

## Provenance 與 actor evidence

每個 event 回以下 `context`；鍵必須存在，不知道的單值用 null 並在 `unknown_fields` 列原因，陣列用空陣列。Dashboard 顯示「unknown／來源未證實」與現有證據，不能把 null 默認成 操作者 或 Hermes。事件發生者與看見事件的人分開。

| 欄位 | 來源與規則 |
|---|---|
| `actor`、`actor_basis`、`actor_evidence` | 驗證 token 的 Principal 或可信服務主體；basis 為 `authenticated_principal / service / unknown`。保留既有 event 的 actor，而非用 operation 原始 actor 覆蓋 cancel/resume 的操作者。 |
| `claimed_actor`、`observer`、`evidence` | `ted_actions`、reconciliation、legacy body 的自報人名放 claim；observer 指 inventory／Task Service 等記錄者。證據用表／ID、protocol/version、turn/message/hash／receipt ref；不存 token 或原始 prompt。 |
| `entry_point`、`operation_entry_point`、`client`、`operation_id`、`command_id`、`step` | entry_point 是當次事件的可信 http/mcp/cli／daemon 入口，由 adapter 傳入 writer；operation_entry_point 才是建立操作時的 `operations.entry`。Cancel/resume 沒記當次入口時為 unknown，不借用原入口。Legacy task RPC 若只有 local-admin 驗證，actor 是 local-admin，human identity unknown；不由 body 自報推斷實際人。 |
| `work_item_ids`、`execution_id`、`relation_ids`、`project_ids` | 當時有效的 journal 關係，可多 work items；不以最新 owner 或今天的專案樹回寫舊事件。 |
| `host`、`profile_id`、`workspace_id`、`session_resource_ids`、`worktree_ids` | 實際 binding；跨 host receipt 分 source/destination；沒有明確 binding 就保持未知。 |
| `source_versions`、`result_versions` | `{kind, repository_id, sha/ref/digest, role, evidence_ref}` 的陣列；如 checkpoint commit、task base/verified commit、integration pinned/integrated/delivered SHA。Branch／latest 不是固定版本。Git author 若已有證據，只是版本 metadata，不是 actor。 |
| `occurred_at`、`recorded_at`、`backfilled`、`unknown_fields` | UTC；recorded_at 是 durable 寫入時間。Host timestamp 附來源，不拿時鐘偏差改 seq。回填區分原來源時間與新增投影時間。 |

Operation／checkpoint／work item 新事件在現有交易中寫完整 context；舊 operation 的建立者／原入口／target 可由不可變欄位還原，變動過的 result/version 不拿最新值補舊 status。Step、receipt、command 的每次轉換保存當時 fields，HTTP/MCP/CLI 回同一份 envelope。History 使用明確 allowlist 回結構化摘要、hash 及 reference；舊 task body 裡的需求原文或原始 prompt 不整段複製到 timeline。聊天內容仍用既有 messages 讀取路徑，不擴大 event feed 的敏感內容。

`summary()` 對 dict 與 list 遞迴使用同一 SUMMARY_FIELDS；relation.opened/bound/closed 的 body.role、source_versions/result_versions 各項的 role、history.backfilled.saved_snapshot 的 role 都保留。Role 是固定機器角色，不能用備註或 prompt 補值。原 context 的版本角色也保留；Git author 仍不是 API actor。新增欄位只接受現有 writer 契約中屬於 enum、ID、seq、SHA 或布林的欄位，不因巢狀位置放寬文字內容。以下為本輪稽核新增的完整清單：

| 類型 | 新增 SUMMARY_FIELDS |
|---|---|
| Enum | `role`、`agent`、`source_provenance`、`effect`、`outcome`、`verdict`、`write_scope` |
| ID／來源 reference | `head_repo_id`、`resolver_operation_id`、`resolver_session_id`、`apply_operation_id`、`integration_operation_id`、`parent_id`、`verification_id`、`route_id`、`old_session_id`、`handoff_command_id`、`operator_followup`、`source_message_id`、`evidence_ref` |
| Journal seq | `integration_seq`、`linked_at_seq` |
| Git SHA／SHA-256 | `expected_head_sha`、`expected_base_sha`、`preview_digest`、`request_hash`、`excerpt_sha256`、`before_digest`、`after_digest`、`candidate_commit`、`diff_sha256`、`start_commit`、`old_head`、`new_head`、`pushed_sha`、`push_old_sha`、`composed_tree`、`predicted_tree`、`approved_fingerprint` |
| 布林（journal flags 可為 0/1） | `cancel_requested`、`would_merge`、`files_may_be_truncated`、`write_acknowledged`、`observed_intent`、`read_refused`、`conflict_before_write`、`conflict_after_write`、`pushed`、`local_checkouts_changed`、`advisory_only`、`abort_current`、`ready`、`stale`、`attention` |

稽核範圍包括 relation.*、session.worktree_bound、delivery.merge_previewed/metadata_settled、operation steps/refs、task/checkpoint/integration/work-item/inventory 事件與回填表的 snapshots。Session/worktree binding 的 worktree_id/previous_worktree_id 等欄位原已保留。原有 allowlist 欄位不擴大文字契約。Prompt／需求／聊天的 `prompt/words/original_words/instructions/payload/excerpt/text/messages/note`，以及 `message/subject/summary/description/warnings/lines/error` 等自由文字仍排除。`title` 現在於全部摘要排除，包含 tab／work-item 標題與 history.resource；scalar `body/request/response` 不接受，PR title/body 不進事件。

### Reason 與其他字串的安全摘要稽核

採 value enum 驗證：任何深度的 reason／previous_reason 只保留 SUMMARY_REASONS 的精確固定值或 null，不能只因 key 在 SUMMARY_FIELDS 就保留字串。未知值（含 caller／agent prose、例外訊息、dict/list）移除，不編造替代 code。原已記錄的 reason_code／error_code／code／verification_error／read_only_code 保留為最多 128 字元的機器 code（字母開頭，後續僅字母、數字、底線、點、冒號、連字號）；不接受 prose。讀取時對既有 events 和 saved_snapshot 都套規則，不重寫舊 facts，不新增 data step。所有 writer 的原始 core rows、relation body/revision 同一性及控制流程保持原契約。

| reason／previous_reason producer | 類型與摘要處理 |
|---|---|
| Journal.request_ted → task.ted_requested | caller reason[:1000] 是 prose；不符合固定 enum 就移除。 |
| Journal.change → task.state（needs_ted／failed）及自訂 transition event | result 轉字串[:1000] 是 diagnostic prose；移除，保留 from/to 等機器狀態，已有 code 才顯示 code。 |
| Journal.finish_minimal_review → task.minimal_review_decision | Journal 接受 caller reason，但 ModelRouter.MinimalReviewGate 只產生 diff_unavailable、diff_too_large、sensitive_path、jev_unavailable_or_invalid、jev_pass、low_confidence、jev_fail/risk/unsure；只保留這些精確 enum，其他 prose 移除。 |
| Journal.submit → task.engine_decision | MinimalTaskRouter 的 jev_choice／jev_unavailable_or_invalid 保留；daemon 的 presplit_<provider> 是任意配置名稱組合，不追認 enum，移除。 |
| Journal.command_operator_only → task.command_identity_conflict | caller diagnostic 是 prose；移除。Command reconciliation 的 source 是 caller attestation，不是 reason enum，按下表移除 scalar source。 |
| Journal.add_branch → task.task_branch，Observation.relation/close_relations → relation.* | role 是機器角色；reason 常為 start、warm_reuse、vanished_replacement、replacement（既有 replacement 記錄）、recovered_start、recovered_failover、quota_failover、initial 或 PM fallback 的 quota_error/rate_limited/auth_error。這些保留；add_branch 未驗證的任意 reason 不因 role 有效而放行。Pending 的 null 保留，close 沿用同一 body。 |
| Journal.revoke_task_capabilities → task.task_capabilities_revoked | 固定 warm_session_transfer 保留。 |
| TaskService dependency_install_result（TaskVerifier／TaskBAT） | 保留 no_supported_lockfile、candidate_not_clean、installed、install_failed、install_dirtied_worktree、unsupported、worktree_unavailable；adapter 例外類名或任意 result.reason 不符合 set 就移除。 |
| Inventory._specific_stale／_record_success → session.stale／session.fresh | 固定 not_enumerated、gone、scope_changed，含 previous_reason；保留，不套 host flap。 |
| Inventory._discovery → discovery.changed.outside_scan | 固定 not_configured、not_enumerable、only_known_claude_cwd、scan_cost、no_background_git_probing、journal_facts_only；保留。 |
| OperationService._transition → operation.*，step response/resource.bound | cancellation、resumption、waiting、NeedsAttention／OperationError／例外 reason 和 status_reason 是 prose；reason 不符合 set 就移除，status_reason 全部排除；既有 error_code 保留。 |
| PR delivery scope → delivery.merge_previewed 及 step snapshots | native_stack、branch_chain、indirect_merge 是固定 enum，保留；blocking.message／warnings／PR title/body 排除。Metadata settlement 已用 code，保留。 |
| Integration push read-back，以及 checkpoint/lifecycle/orchestrate 的舊回覆傳入 operation step | remote_rejected/remote_moved 的 reason 來自 Git stderr；snapshot／workspace／merge 等說明句也是 prose，移除；固定 outcome、status、code 保留。 |
| Journal.route、work_events 的 summary/reason/reason_code record | route 把 reason 留在 routing 表，model_route event 沒有 reason；work_events 是另一個既有讀 feed，不插入 api_events。summary/title 為 prose，不納入本 history；若舊 fact 已記固定 reason_code，摘要保留 code，不從 summary 反推。 |
| Journal.note／api_event 的通用入口及 history.backfilled | 任意 caller body 不擴大契約；每層套相同 value/shape 規則，saved_snapshot 不能繞過。 |

其他所有可能為字串的 SUMMARY_FIELDS 依 producer 契約稽核如下；欄位長度有限並不等於 enum，會承接 prose 的 key 要移除或檢查值／結構：

| 字串欄位／群組 | 來源與界線 |
|---|---|
| title、status_reason、git_author | title 可來自 BAT tab、work-item caller 或 task 需求第一行；status_reason 為 operation diagnostic；git_author 沒有正式 writer，generic note 可自報任意 prose。全部排除，含 resource header 與 snapshots。 |
| body、request、response | 原資料可為 prompt／PR prose；只接受 dict（null 可保留），遞迴移除不安全 fields。Dict 不是放行文字的理由。 |
| evidence、errors、blocking | evidence 可能是人工 attestation；errors 可為 redacted exception。只接受 dict 或 list 中的 dict，移除 scalar／list text；保留 table/ID、code、SHA、enum，移除 error/message/note。 |
| source | reconciliation 的 caller source 可帶 prose。Scalar 僅保留 observed_runner、workspace:load、enrichment、journal；其他只接受 dict，遞迴同一規則。Source_kind/source_id 等具名資源欄位仍是種類／ID。 |
| ref、external_ref | mark_stage 只檢查長度，可帶 deployment prose；summary 的 scalar ref 限無 whitespace／控制字元、最多 512 字元的 ID/Git ref/URL token。合法 PR URL／SHA／資源 ID 保留；例如操作描述句只在原 journal，不進 history。 |
| host/profile_id/workspace/workspace_id、cwd/path/worktree_path/clone_path/repo_root、branch/worktree_branch/retained_ref、repository | 配置 alias、實際 workspace／路徑、Git ref 或 owner/repo 身分，來自 enumeration／creation intent／Git facts；不是 task 原文或 commit-message 欄位。路徑名稱可有空白，保留身分原值，不能把它當 reason 或 actor。 |
| *_id、resource_id/resource_type、id、intent_type/intent_id/slot、anchor_id、source_key/source_table/table、channel、scan_id、binding_version/attempted_binding_version、credential_ref、evidence_ref、parent_id、operator_followup | Registry/journal/BAT/GitHub 資源 ID、建立 slot、固定表／channel 名、掃描或 binding digest；source_key 為來源表與 primary key 的序列化，不是 prompt。泛用容器不把 prompt 改名成 ID。 |
| sha、head、commit、pin、*_sha/*_sha256、*_commit、*_head、*_tree、tree_hash、digest/hash/sha256、request_hash/preview_digest、approved_fingerprint | Checkpoint／Git step／receipt 的固定版本或內容 hash；不含 commit subject/message、PR title/body、prompt 原文。Code 群組依上文驗證。 |
| kind/status/action/step/from/to/state、role、agent/agent_kind/agent_preset/model、runtime/runtime_status、entry/actor/observer、server_version/version、mode/method/location_class、source_kind/source_provenance、effect/outcome/verdict/write_scope、scope/scope_status、identity_evidence/provenance/api_access/isolation/end_scope、loading/activity/tab/enumeration/lifecycle/freshness | 正式 writer 的固定狀態、角色、action/step 或配置／BAT 的機器標籤；不用 diagnostic result 填值。API actor 是 Principal 識別，原 claim 仍獨立且不授權。Field_evidence 的 loaded/streaming/has_tab 值由 service 的固定 enum 建立，不取 meta_error。 |
| author | 唯一正式 producer 為 integration 的 Git log parser，取一行 author header，subject 另欄且排除；僅為版本 identity metadata，不是 API actor 或 prompt 證據。Generic git_author claim 排除。 |
| first_seen_at/last_seen_at/observed_at/gone_at、*_at | Inventory／journal 已保存的 UTC record/observation 時間；不是聊天文字。Saved fact 的無效 occurrence 先正規化為 null，不改成 migration 時間。 |
| before/after、target/refs/saved_snapshot、pending、field_evidence/field_observed_at、coverage/methods/authority/capabilities、source_versions/result_versions、files/commits/parents/other_commits、affected_prs/stacks/members、merge_receipt/metadata_settlement/metadata_reconciliation | 已有 writer 以 dict/list 保存版本、ID、路徑、scope、固定狀態或布林；遞迴同一 allowlist/value 規則。before/after 的 PR title/body 被移除；files 只留檔案身分，commits 留 SHA/parent/author，不留 subject/message。Object 欄位拒絕 scalar prose；source_versions/result_versions/affected_prs/stacks 的 list 只留 dict，pending 等混合狀態沿用 writer 的 bool/dict，files/parents/members/capabilities 的 primitive 僅為路徑／SHA／PR number／協定 tag；新增 producer 不能改此結構契約。 |
| changed_fields | Inventory.MATERIAL 固定 key 名稱的陣列，不是 caller 的變更敘述。其餘 SUMMARY_FIELDS 是 seq/count/boolean/nullable 值，writer 不以 prose 代替數值。 |

`provider` 同時可能是 provider 名稱與 GitHub API URL，不能全域列為 enum。`head_ref/base_ref/html_url/remote_url/name/idem_key/purpose/step_type/field/fingerprint` 可帶任意名稱、位址或 caller 文字，保持排除；已明確命名的 ID/SHA 才新增。`old/new/tree` 的通用名稱及 `params/preconditions/result/external_refs/observed/reviewed/intended/picked/conflict_files/remerge_stat` 等混合容器不放寬，使用已保存的具名版本與 context。`dirty/missing_count/attempts/uncertain_tries/diff_chars/confidence/tests_ok` 是數量或評分，並非布林/ID/seq；新 timestamps 也不在本輪新增範圍。回填缺少的資料仍 unknown，不增加資料步驟重寫已保存的摘要。

`resource_policy` 是可寫判斷唯一來源。B03 的 unknown actor/provenance 不會產生 grant，不回填 managed creation intent，也不增加配額；觀測 journal 的 relation/index 不能被 mutation admission 當作擁有權證據。

## 輸入／輸出與介面

所有新增讀取須通過既有 `api_auth` 的 observe scope，HTTP／MCP／CLI 經同 daemon、同 journal 的讀服務。沒有新增 mutation action；需要記 checkpoint、link 或派工時仍使用已註冊 `ActionDef`／OperationService，權限保持原契約。

| HTTP 路由（Phase 2） | MCP（RPC 為同一讀服務） | CLI | 輸入與輸出 |
|---|---|---|---|
| `GET /api/v1/sessions`（延伸） | `inventory_sessions` | `batc inventory sessions` | 保留原 host/provenance/access/attention/include_gone/order/cursor/limit，新增 profile_id、project_id（多值 OR）、work_item_id、execution_id、provider、has_tab、loaded、streaming、lifecycle、stale、`relation_scope=current/history`（預設 history）。其他不同 filters 為 AND；回 sessions/count/next_cursor/as_of/hosts，加 relation summary 與 coverage。 |
| `GET /api/v1/sessions/{host}/{id}`（延伸） | `inventory_session`（新增） | `batc inventory session HOST SID` | 回 session、現有 started_from/work_items，新增 state、discovery、relations_summary、history_available；不為 journal-only session 呼叫 host。既有 `live=true` 明確為另外的 host 讀取，history 不支援 live。 |
| `GET /api/v1/sessions/{host}/{id}/history` | `resource_history(resource_type="session", resource_id="host/id")` | `batc history session HOST SID` | cursor/limit/order/kind/since/until；回 resource、events、count、as_of、head_cursor、next_cursor、has_more、coverage。計畫 `/sessions/{id}/history` 的 ID 在現有 API 分成 host/id。 |
| `GET /api/v1/sessions/{host}/{id}/relations` | `resource_relations(resource_type="session", resource_id="host/id")` | `batc relations session HOST SID` | cursor/limit、execution_id、include_closed（預設 true）；回 relations、count、as_of、next_cursor，完整 ranges／evidence。 |
| `GET /api/v1/tasks/{task_id}/sessions`、`…/history`；`GET /tasks/{task_id}` 加摘要 | `resource_relations(resource_type="execution", resource_id=task_id)`；保留 `work_status` | `batc relations execution TASK` | execution_id 就是 task_id；history 同 resource_history(type=execution)，relations 以同 relation 服務分頁列 lead/reviewer/歷史 replacements，附 follow-up 關係與 worktree；不依最後五個 commands 充當完整列表。 |
| `GET /api/v1/worktrees/{worktree_id}`、`…/history`、`…/relations` | `inventory_worktree`、`resource_history`／`resource_relations`（type=worktree） | `batc inventory worktree ID`、`batc history worktree ID`、`batc relations worktree ID` | 只讀 connector 已知 binding；history/relations 分頁與 session 相同。沒有 binding 回 404，不掃主機猜 path。 |
| `GET /api/v1/hosts`（延伸）、`GET /api/v1/hosts/{host}/discovery` | `inventory_hosts(host, discovery=true)` | `batc inventory hosts`、`batc inventory discovery HOST` | hosts 含每 configured profile 最近 scope/status/times；discovery 回最新 scan/outside_scan，近期 transitions 以 seq cursor 分頁。 |
| `GET /api/v1/events`（延伸）；既有 `/events/stream` | `events_list` | `batc inventory events --after N` | 保留原契約；新增 related_resource 篩選與同 context。SSE 沿用全域 feed，不另建 timeline streamer。 |

CLI 保留現有 `batc sessions/read/hosts` 的直接 BAT 行為，新增 `inventory/history/relations` 清楚表示中央持久讀模型。新命令 `--json` 結果與 MCP/HTTP 等價，不因 daemon 未啟動而退回實機 scan；回確切連線錯誤。MCP `inventory_sessions` 補現有 HTTP 的 order 參數，完整巡覽用 order=id。分頁 params／返回 keys 以契約測試比對，避免同名參數各入口不同意思。

`capabilities.features` 增 `session_history`、`resource_relations`、`discovery_scope`，`worktree_history` 說明只限已知 binding；附支援 kinds、history 起始／backfill 限制及 optional providers。能力須反映已在 main 的實作；缺少 optional writer 回 unsupported/未取得原因，不造空的成功資料。

本次整合的 history.optional_adapters 列 `delivery_part_a`、`delivery_part_b`。這只表示已接讀正式 journal facts，不表示有 GitHub credential、可寫 PR 或有未知的 session/worktree 關係。

| 錯誤 | HTTP | 恢復 |
|---|---|---|
| `UNAUTHORIZED`、`FORBIDDEN` | 401／403 | 沿用既有驗證與代碼；讀工具不升權。 |
| `NOT_FOUND` | 404 | Journal 從未有該 ID／worktree binding；不隱含 live scan。 |
| `INVALID_PARAMS`、`INVALID_CURSOR` | 422 | 無效型別、範圍、順序、時間或游標與 filters 不合；保留 filters，重新第一頁。 |
| `HISTORY_CURSOR_UNAVAILABLE` | 409 | Cursor 的 snapshot／schema 版本不可讀；重取 baseline 再 catch-up，不能默默跳 head。 |
| `DISCOVERY_SCOPE_CHANGED` | 掃描結果的 error code | 舊 alias 的 binding/profile 變動；保留舊資料並標 outside/stale，配置新 alias。History 仍可讀。 |
| `SOURCE_UNAVAILABLE` | 資料的 reason，GET 仍 200 | Host/field/optional source 沒取得；回舊觀測、證據時間與 scan error，不把失敗當空 inventory。 |

### Part B（第二步）：Dashboard「Sessions 與歷史」

列表預設跨專案，加入多專案、工作項目、execution、host/profile、provider、來源、各狀態及「包含不再列舉的歷史」篩選；最後者在此畫面預設開。清楚列資源 ID、API 唯讀、各時間與 scan scope。選過的 filters 保存在既有 sessionStorage；無關聯資源可單獨篩出，不因沒有工作項目而消失。

Session 詳情加分頁 timeline 與 relations；工作項目、execution、operation、checkpoint、已知 worktree 皆可跳轉。Timeline 每列顯示時間、kind、actor 或 unknown、來源／結果版本、狀態及證據；較長技術 ID 收在可展開細節。Execution 顯示 lead、reviewer、follow-up 的 ranges；worktree 讀頁只呈現同一模型的資訊。Scope 卡顯示「掃了哪個 profile、何時、依何 authority、未掃哪些」，離線仍可讀舊 timeline。

重開與即時更新流程：

1. 從 journal 讀第一頁 baseline，保留 `as_of`；以 order=id 讀所需頁面，以 resource ID 合併 rows，不把跨頁排序變動當新資源。首次載入不使用先查 head 再載 view 的方式聲稱已補漏。
2. 向前分頁讀 `/events?after=as_of` 補到一次捕捉的 head；同時到達的 SSE 暫存，處理完補漏後接續。只按 seq 遞增套用，已處理的 seq 直接忽略；同一事件同時關聯多個資源，也只消費一次全域游標。
3. SSE 斷線後由最後已處理 seq 重連；frame 被截斷不前進 cursor。重複 frame、跨 chunk、多筆積壓、catch-up／SSE 重疊均不重複 timeline 卡片。沒有有效目錄 baseline cache 的 reopen 重取 baseline；不能只恢復游標而留下空畫面。
4. Token／daemon 身分改變時丟棄舊 cache/cursor。現有 journal 不 prune api_events；若未來有 retention 或 cursor 超過當前 head，明示 gap 並重建 baseline，不靜默略過。
5. History 第一頁 as_of 固定；新事件用 seq map 加到頂端，舊頁 cursor 繼續讀原 snapshot。Filters 改變重開 snapshot。渲染沿用 `fill()`、`liveReload` 與 typing／編輯抽屜 hold，保存載入過的頁面及草稿，不在打字下方重畫。

兩種語言在 `i18n.js` 同步，390 px 可讀。CSP 禁止 inline style attributes；需要動態樣式用既有 class 或 `el.style.setProperty`。本包不增加 managed 控制動作；既有控制按鈕仍依 backend capability 及資源政策。

### Resource detail presentation

The shared Web/Tauri resource summary reads the existing worktree/task response only.
Worktree titles use the first returned work title, then the branch; executions use
the returned title or first request line, then the branch. Ownership labels follow
recorded creation kinds. Manual, missing and unfamiliar ownership stays read-only.
No requirement association is inferred or fetched for these summaries.

The latest worktree result uses returned work and delivery receipt times. A recorded
PR receipt is distinct from result verification; dispatch acceptance cannot establish
completion. Task delivery pills use the journal's recorded delivery stage, independently
of dispatch state. Missing evidence is Unknown. Related sessions use their observed
title or the shared untitled label, with host metadata on its own line and
activity pills only for fresh running/waiting observations. The dispatch row
appears only while paused; a reason is shown only when the returned pause event
contains one. Accounts without the existing control grant see a plain read-only
explanation instead of pause/resume entry points; original-operation readback
and frozen requests remain available under their existing rules.

One collapsed Details holds identity, path, source/receipt commits, navigation links
and cleanup limits; Raw record is separately collapsed within it. Summary actions
are existing session, artifact-review and gated cleanup-preview routes. Work rows
keep Review result visible and place other destinations in the shared menu or Details.
Details preferences are scoped to the connection namespace and resource identity.
A withdrawn read clears summary rows and Details together; view/request guards
reject late responses. Existing task controls, durable requests and recovery keys
remain owned by their original controller module.

The project detail page removes work rows, counts and editors when its source
read fails, including during editing. Request/view guards reject older responses;
private project, child-item and creation drafts survive source recovery. Project
IDs do not repeat above the creation form. Read-only accounts do not see creation,
dispatch or edit entry points. Counts require complete, numeric source evidence;
an absent or partial total has no completion chip. The archive checkbox sits on
one line with its label inside the work-item card, which also owns its empty state.
The sidebar requires a successful project list to claim an empty catalog. A
missing list remains unknown without treating unrelated task reads as failed;
API failures retain their existing refresh/checkpoint error handling.

## 必要前置條件、實際副作用與唯讀邊界

Daemon 必須持有既有 owner lock，journal migration 完成；API observe 主體及 host 的 BAT credential 由既有配置提供。沒有 SSH alias 的 host 仍可讀 inventory/history，Git dirty 等欄位標未取得，不能為觀測要求開 write/orchestrate tier。

讀 HTTP/MCP/CLI 只作 journal 的 SELECT，不寫 registry、不取得 host mutation lock、managed worktree flock 或 Git index lock。Background observation 的實際副作用只在 Connector 自己的 journal 覆寫 latest discovery、最新觀測、關係投影與事件；read-only Fleet 會連線／驗證 BAT 並讀 workspace/meta/安全 state/archive。它不送 start、resume、client-resume、rehydrate、workspace save，也不執行 TaskCoordinator.tick 或 write grant。

沿用 `service._state_safe`：Claude meta 沒有 cwd 就不讀 state；不以完整度要求繞過。不新增任何背景 Git 探測。Git 欄位只取 journal 已存的 checkpoint、step、receipt 或既有 on-demand reads；後者沿用 `checkpoints.source_state_script` 的 `git --no-optional-locks`。Outside scan 明列未背景掃描 Git 狀態；禁用 BAT `git:status` 及未驗證會刷新 index 的 worktree status 路徑。不能為補資料 stash、fetch 到人工 repo、checkout、commit、prune 或 rehydrate。讀取不到只回 unknown 與原因。

新增 journal writes 在原 writer 的短交易內，外部呼叫仍在 commit 之後。API 只讀不因觀察人工資源建立 managed registry entry，不改配額、不恢復未知 ownership。直接 messages/live 讀取是另有 host I/O 的既有能力，與純 journal history 分開。

Registry identity 合併結果以 canonical JSON 比較，未變更不執行 upsert。相同 BAT／registry 的第二輪 poll 不改 observation identity／relation／event tables；latest discovery 仍更新本輪掃描時間。

## Migration 與失敗恢復

沿用 `task_journal.Journal`；新增 tables、columns、indexes 採 idempotent DDL，每次開啟都執行，不讀寫 `PRAGMA user_version`，也不占資料步驟編號。新增事件 context/resource 索引、discovery latest、已知 worktree identity、relation segments 及欄位 evidence；既有 tasks、commands、events、work_item_links、checkpoint IDs 不改編。Relation segments 唯一鍵為 `(execution_id, session_resource_id, role, anchor_id)`，command-relation mapping 以 `(command_id, relation_id)` 為 key，保存 linked_at_seq；rebinding 保留舊 link，使 as_of 的 command_ids 不受最新 binding 影響；各 revision 留 seq，只有 SQLite 原 writer 寫入。

回填在 daemon owner 內、背景 loops 開始前完成，是 orchestrator 配發的一次性資料步驟 2；backfill 與 `user_version=2` 在同一交易提交。版本 1 的 journal 執行一次，版本已為 2 時不再執行；新 journal 也以 2 結束。DDL 使用 IF NOT EXISTS，位於資料步驟的版本 gate 之外。來源 key（表名／PK／事實類型）唯一，重啟重做不重複。已經有 api event 的 task／checkpoint／link／receipt 只補資源索引與可信 context，不追加同一件事的第二個 event。當舊 mutable step／receipt 只剩最後快照時，`history.backfilled` 記錄「目前保存的結果」及原 timestamp，不捏造 started → uncertain → succeeded 全序列；`history_coverage` 明列開始時間、缺少的 transitions。舊 profile／actor／range 不明保持 unknown。

Worktree maker 修正也沿用資料步驟 2：binding seed 重播同一套 connector slot／session binding 證據；後續 registry 掃描使用同一純 predicate／root walk，legacy batc/ 不覆寫 seed 的 connector slot。沒有 slot 不補 registry intent。#35 尚未合併，沒有已部署 journal 保存本輪修正前的錯誤 BAT IDs，因此不新增資料步驟、不重跑已完成的 backfill；正式 worktree_id hash 保持逐字不變。

Carrier 修正同樣不占新的資料步驟：registry binding 的 live writes／fallback 先通過共用 parent rule；步驟 2 以同一 binding 投影重播已證明的 carrier 事實，之後的 registry refresh 也不為非共用 successor 補掛舊 ID。版本 1 regression 包含 BAT root 與 connector slot root；child 無 path 時，重播後仍無舊 worktree binding／history／relations。Branch 尚未合併，不需要修補已部署的錯誤 seed；projection failure 的 savepoint／gap flag 規則不變。

Journal 的 `LATEST_DATA_STEP` 記整套 journal 最新配發的資料步驟，現在為 2；新增資料步驟時更新這個常數，個別 backfill 保持自己的版本 gate。整體 journal 開啟結果為 max(原版本, LATEST_DATA_STEP)，不降低較新版本；delivery DDL 的相容性測試使用這個常數判斷整體版本，不把其他包的資料步驟誤判為 delivery DDL 改版本。

版本 1 journal 已有 delivery 的 DDL 與資料時，資料步驟 2 也涵蓋 pr_merge_previews／pr_metadata_settlements；沒有對應事件才補 history.backfilled，以表名/PK 作唯一來源 key。保留原 preview.created_at／settlement.settled_at、摘要及正式 operation refs；原 delivery tables/documents 不改寫。Scope-read throttle 表不是 immutable 歷史，不把 pr_merge_scope_reads 的覆寫列偽裝為每次讀取事件。回填仍不進預設 live feed。

Binding table/index 為每次開啟執行的 idempotent DDL，不讀寫 user_version。既有 binding 的 seed 屬本包資料步驟 2，不另占步驟 3：重播有明確 session/worktree 對的 binding/建立事件，使用最早可證明的原 seq；只有保存的目前 binding 而沒有更早證據時，用既有 backfill 為該 session/link 配發的 history.backfilled seq 作已知起點。不得由今天的 mutable worktree_id 將更早事件或已結束 relation 回掛目前 worktree；無證據的更早歸屬保持 unknown。Reopen 不再執行資料步驟或補 poll rows。Projection 失敗仍只回滾 savepoint，core event 保留 projection_error；不留下半個 binding move。

步驟 2 重播已保存的具名 relation 事件時，保留其明確 IDs 及 status/end 的 revision，讓後續事件不再掛已關閉的 session；原 legacy start_seq/started_at 的 unknown 保持 null。Legacy closure 及具名 relation.closed 的 replay 都用同一 command query 重建 end_command_id，避免舊 event 的 null 抹掉已證明的最後 command；無 command 仍為 null。關閉時間未知，replayed revision 的 ended_at 保持 null。這是舊資料的重建規則：原 api_events.body 不改寫，與新 writer 的逐欄相等 invariant 區分；重建不把舊 body 缺失的值當已知事實。Malformed relation 只保存上述證據，不建立 links。此修正不新增 DDL 或資料步驟，也不重跑已完成的步驟 2；投影若例外仍由既有 savepoint 保護核心寫入。

Eventless facts 的時間定位與 execution links 仍屬本包資料步驟 2。完成後直接重呼 backfill 或 reopen 都不寫入；guard 只用於這個一次性資料步驟，DDL 仍每次開啟獨立執行，不占 user_version。

Operation refs 的位置修正也只套在原資料步驟 2 與之後的 live 投影；不新增 DDL 或 user_version，不重跑已完成 backfill。保存的 facts 早於 operation/resource binding 時，不能再從目前 refs 取得後來的 session/worktree；仍可從明確指定 kind 的全域 backfill feed 讀取其 metadata／來源證據。既有 projection savepoint 與 projection_error 保留。

快照來源為 tasks.external_worktree、checkpoint_runs、保存的 operation refs/steps 或 session 的目前 binding，snapshot 明存 session/worktree 對。僅目前 binding 的 fallback 來源 key 為 `session_worktree_bindings:<session resource ID>`；它的 backfill event seq 同時是 binding.start_seq、linked_at_seq 及該 event 的 resource link seq。已知舊 creation 的重複 receipt/snapshot 不把已移走的 session 移回去。Legacy relation 的開始時間仍可 unknown，但確定的 command participation 與 live projection 相同；舊 command 在 binding 的證據之前時不宣稱它屬於該 worktree。已完成步驟 2 的 journal 只安裝 DDL，不再 seed；新 binding 由之後的 writer 事實建立，未被保存的過去 binding 不推測。

as_of 稽核：history 的 membership/coverage 只查 api_event_resources 的 seq/linked_at_seq，context/occurred_at 固定保存；resource envelope 為目前身分摘要，不是 membership filter。Session/execution relations 用不可變 identity columns 與 revision/command link seq；worktree relations 改用上述 binding intervals。Inventory 的 as_of 只供 events catch-up，目錄本來就不承諾 snapshot：目前 host config、provenance/access、gone/state/freshness、project/work-item/execution memberships 都可在 traversal 中變動；order=id 保證穩定 key 前進，新增列/篩選 membership 變動由 events 補讀，order=activity 仍保留動態排序限制。這些目前值不能套進 history/relations 的 snapshot membership。

已確認綁定的 operation 可能在 runs 表入帳之前中斷；讀 `external_refs`、已持久 step 及 commands 保留其身分與 uncertainty，不執行啟動補償。Context／index 在原交易內用獨立 savepoint 寫入；投影例外只回滾該投影，核心事實照常提交，context 留 `projection_error` 的例外類名並記 log，不保存訊息。History 由原寫入資源或此前已證實的資源連結顯示該事件及缺口；不留下部分 index／relation rows，也不對外宣告 journal 未提交的事件。Poll 失敗保存 failed scan，舊欄位及其他 hosts 不受影響；meta 失敗只影響該 session 的相關欄位。

Reconcile 所補的是新證據與新 seq，先前 uncertain 事件留著；重新讀 timeline 不重送任何命令。讀到未知未來 kind 仍回原 kind、摘要及 evidence，Dashboard 用一般事件列呈現，不因版本差把歷史丟掉。

## 預計修改檔案

Phase 2 的新增 `observation.py` 與共用 `resource_ids.py`；前者只集中純 journal history／relations／event-context 查詢與投影，不取代 inventory 或 OperationService。

| 檔案 | 修改 |
|---|---|
| `src/bat_agent_connector/task_journal.py`、新增 `observation.py` | Additive migration、事件 envelope／資源索引、回填、ranges、分頁與讀模型。 |
| `resource_ids.py`、`resource_policy.py` | 共用純 connector predicate／creation-root walk，含 legacy batc/；classifier 沿 root 判 maker，既有 BAT action refusal 不放寬。 |
| `inventory.py`、`service.py`、必要的 `config.py` | Scope/scan/field evidence、安全候選聯集、三值狀態、stale 排程；不擴大可寫 channel。 |
| `operations.py`、`task_core.py`、`task_bat.py` | 在既有 journal 寫入點補 step/binding/relation 事實；不實作 operations-unification 的 action/gate。 |
| `pr_delivery.py` | 只在既有 preview/settlement 入帳交易追加摘要事件；保留 delivery 的 cache、retention、step/reconcile/control flow。 |
| `checkpoints.py`、`integration.py`、`work_items.py` | 同交易保存當時 versions／多資源來源及 link ID；沿用已有 writer。 |
| `api_v1.py`、`task_daemon.py`、`mcp_server.py`、`cli.py` | Observe 路由/RPC/tools/命令、params 契約、feature capabilities。 |
| `dashboard/app.js`、`app.css`、`i18n.js`（Part B） | Sessions/歷史/relations/scope、filters、baseline/SSE、兩語言及手機。 |
| `tests/test_observation.py`（新增）、`test_api_v1.py`、`test_task_service.py`、`test_mcp_and_config.py`、`test_work_items.py`、`test_checkpoints.py` | 下節驗收；沿用 MockBat、LocalRunner/RealGitLog、temp git repos，必要時 FakeGitHub。新增可重跑 Dashboard harness／測試，不只依手動截圖。 |
| `skills/bat-agent-connector/SKILL.md`、`skills/hermes/bat-agent-connector/SKILL.md` | 同步教 agent 用持久 inventory 的完整分頁、session/worktree history、relation range、unknown evidence、events 游標；區分訊息分頁與 journal 歷史，禁止因斷線而重開工作。 |
| `README.md`、`README.zh-TW.md`、`CHANGELOG.md`、`docs/design/api-v1.md` | 各 README 加短說明與本文件連結；Next release 引用計畫章節、B01–B03；路由表／MCP/CLI/能力同步。 |
| 本文件與相關 design 的 `尚未涵蓋` | Phase 2 完成後才改為實際結果；`api-v1.md` 更新 observation 路由及限制；`dashboard.md` 的畫面待辦留給 Part B，diff/檔案仍待辦。 |

## 測試計畫與驗收對照

下表為 Phase 2 測試計畫；標 Part B 的瀏覽器驗收不在本次 Part A。每個測試名稱或 docstring 引用 B01/B02/B03；現有測試只證明其已涵蓋的部分，不能把 skipped 舊 reviewer 測試算作驗收。

| 驗收／計畫 | 新測試名稱與必要斷言 |
|---|---|
| B01；§08、§11、§19 | `test_b01_id_paging_complete_and_filter_changes_via_events`、`test_b01_relation_scope_and_cross_project_link_history`：205 筆 ID keyset 完整巡覽；filter changes 經 events；跨 project/work item 的 current/history 及 via 證據。 |
| B01；§08 | `test_b01_warm_reuse_reviewer_followup_and_command_ranges`、`test_b01_pending_bind_and_snapshot_relations`、`test_b01_warm_binding_closes_reserved_intent_without_overwriting`：兩 task 共用 session 的 ranges、舊 reviewer/replacement、follow-up、pending/bound/closed、固定 revision 分頁；原 reserved intent 正確關閉。 |
| B01；§08、§10 | `test_b01_worktree_shared_creation_identity_and_reuse`、`test_b01_legacy_task_external_creation_keeps_shared_identity`：共享 registry 建立 slot、reviewer/failover/reuse 同 ID、同 path 新 intent 不合併；使用 cleanup 共用 hash 函式。 |
| B01、B03；§08、§10、§11 | `test_b01_worktree_relations_exclude_late_bindings_from_existing_cursor`、`test_b01_worktree_moves_preserve_relation_ranges_across_pages`：舊 cursor 不受晚 binding 或後續 move 影響；fresh read 看新 binding；A 保留舊 relations/commands，B 只收 move 後範圍，跨頁無重複。`test_b03_worktree_binding_backfill_matches_live_and_reopens_without_writes`：版本 1 以資料步驟 2 seed，明確原 seq 與 live projection 相同；重開不改 rows/head/version。`test_b03_worktree_binding_projection_failure_preserves_core_event`：move 投影失敗回滾新列與舊 end，core row/錯誤 flag 保留。 |
| B03；§08、§11 | `test_b03_saved_worktree_binding_uses_backfill_link_seq_without_inventing_earlier_range`：僅有目前 binding 時，以同一 backfill event/link seq 作 start；排除其前的 closed relation/commands，不推測舊事件歸屬，重開零寫入。 |
| B01；§08 | `tests/test_resource_ids.py`：共用 root resolver 的 failover chain、reviewer lookup、warm reuse、逆序 registry、缺 parent、cycle、其他建立 intent 及原 JSON created_at 格式。 |
| B01；§06、§08 | `test_b01_registry_identity_and_policy_agree_on_creation_root`：BAT、maker flag、checkpoint、integration、legacy batc/ 五種 root，各配 explicit-sharing failover、缺 marker 的等 path legacy failover、非共用 failover、warm reuse、reviewer（明存 lead 或 lead_of）、shares_worktree_with row，身分與 maker 使用同一有效 root。無 path 的非共用 child 為自己的 root，沒有 intent，maker 不繼承舊值。`test_b01_warm_claim_keeps_creation_markers_and_identity`：真正 claim_warm 保留全部 markers／created_at／path 與 intent。 |
| B01、B03；§08、§11 | `test_b01_b03_legacy_connector_branch_keeps_journaled_slot_and_history`：legacy batc/ lead／reviewer 保留 task 或 checkpoint slot，session history／relations 接同 worktree；live、版本 1 step 2 replay、重開／同資料 poll 零 writes。`test_b01_legacy_connector_branch_without_slot_has_no_worktree_identity`：兩條路徑均不鑄 BAT ID。 |
| B01、D05；§06、§08 | `test_connector_creation_root_refuses_bat_actions_when_child_loses_markers`：markerless failover／reviewer／shared row 仍回 NOT_A_BAT_WORKTREE，無 BAT write frames。`test_b01_current_connector_evidence_preserves_policy_refusal_and_has_no_bat_identity`：目前 row 與 root 證據矛盾仍不放寬。既有 policy classification 與 checkpoint BAT action refusal 測試全部保留。 |
| B01、B03；§08、§11 | `test_b01_b03_nonsharing_failover_never_links_old_worktree`：main-checkout successor 的 failover_of 保留，但無 sharing marker／path；BAT／connector root 各驗證 live 與版本 1 step 2 replay，無舊 binding、自己的事件不進舊 worktree history／relations，重開零 writes。`test_b01_nonsharing_failover_has_only_its_own_creation_intent`：不同 path 的 child 用自己的 root／intent；無 path 時不猜 ID。 |
| B01；§08 | `test_b01_reviewer_root_requires_matching_carrier_or_recorded_task_path`、`test_b01_reviewer_task_path_must_name_the_same_lead`、`test_b01_registry_reviewer_without_path_requires_recorded_task_carrier`：明存／lookup lead、相等／不同／缺 path，僅 task 保存同 lead 同 path 才補缺 path 的 reviewer；ID-only callback 不足以補 path。純 walk 與 connector-slot fallback 一致。 |
| B01、D05；§06、§08 | `test_nonsharing_failover_without_worktree_never_gets_bat_worktree_grant`：BAT／connector root 後的非共用 successor，即使 origin 在 managed root、force options 全開也拒 BAT actions；沒有 rehydrate／merge／remove frame。 |
| B01、D05；§06、§08；v2 R01/R04 | `test_legacy_reviewer_worktree_needs_a_proven_registry_carrier`：真 classifier／policy 與 merge/remove 入口，缺 registry lead 的 reviewer 在 managed root 仍回 NOT_A_BAT_WORKTREE、零寫入 frame；帶 explicit lead／sharing／等 path failover 時保留 BAT／connector maker 與既有 grant 行為。 |
| B01、B03；§08、§11 | `test_b01_b03_checkpoint_source_run_steps_and_worktree_history`、`test_b01_b03_receipt_versions_are_fixed_at_the_writer_transition`：真實 temp Git checkpoint/run 及 steps 的多資源 timeline 去重；receipt 在既有 writer 轉換時保存各版本，不以新結果改舊事件。 |
| B01、B02；§10、§11 | `test_b01_history_as_of_late_binding_and_invalid_cursors`：history as_of 同時限制 event/linked_at；晚 binding 不進已開 snapshot，kind/order/resource/time 游標契約。 |
| B01、B02；v2 §14（原 §10、§11） | `test_b01_cursor_keys_are_rejected_before_any_journal_read`：SQL trace 證明 history／relations 錯 key 在任何 journal read 前即回 INVALID_CURSOR／422。`test_b01_b02_relation_cursor_key_contract_is_independent_of_rows`：HTTP／MCP／CLI 對空 session／execution／worktree、execution_id 排光、include_closed=false 排光及有 rows 的資源，同樣拒絕字串、短 list、bool、錯型別與 null key；空 history 也拒絕，合法分頁沿用原測試。 |
| B01、B02；v2 §14 | `tests/test_inventory_cursors.py`：同 filter 的惡意 session token（object／null／bool／非有限值／超過 SQLite 整數範圍／無效 UTF-8／未知 version／負數或超前 as_of）在 session query 前被拒絕；HTTP／MCP／CLI 對空目錄、filter 排光、有 rows 一致回 cursor 錯誤。id／activity 的新舊合法游標重開 journal 後仍完整分頁、無重複、保留原 as_of 並捕捉後續事件。 |
| B02；§11 | `test_b02_two_hosts_one_offline_and_scope_change`、`test_b02_null_workspace_preserves_missing_counts_even_with_registry`：另一 host 持續成功、離線保留舊值；scope 改綁不再 host I/O；移除 host 仍可讀 history；壞 workspace 不算完整列舉。 |
| B02；§11 | `test_b02_scope_change_stays_blocked_on_later_refreshes`：同 profile 改 URL／fingerprint，連續兩輪零 BAT frames；binding 與 binding_version 固定原值，attempted_binding_version 記被拒絕值；原身分及 history 不變。 |
| B02；§11 | `test_b02_discovery_latest_no_poll_rows_and_no_host_fanout`、`test_b02_session_specific_stale_gone_fresh_and_get_no_writes`：每 profile 一筆 latest、相同 poll 不寫事件；host flap 不 fan-out；missing/gone/reappear/fresh 與 GET 零 writes。 |
| B02；§11 | `test_b02_unchanged_poll_does_not_rewrite_observation_identities`：第二輪 registry 投影的 total_changes 不變；相同 BAT／registry 不重寫 observation tables。 |
| B02、B03；§11、§19 | `test_b02_b03_field_freshness_swap_catches_up_through_events`：兩 sessions 的 meta 失敗互換，discovery 持續 partial／相同 coverage；inventory.as_of 後 HTTP events 分別通知恢復／失敗，最終 state evidence 一致。HTTP/MCP/CLI history 保留每次 freshness，原 error 與巢狀 note 被移除；零 BAT writes。 |
| B03；§08、§10、§11 | `test_b03_history_drops_task_prose_and_keeps_codes_over_http_mcp_cli`：request_ted、needs_ted/failed result、minimal review 及 command conflict 的 distinctive prose 不在 HTTP/MCP/CLI execution history；原 core rows 不變，固定 code、relation reason 及 session stale/fresh enum 保留。Live 與版本 1 replay 都驗證。 |
| B03；§08、§11 | `test_b03_history_summary_filters_prose_recursively_in_bodies_snapshots_and_resource`：title/status_reason/git_author、scalar body/request/response/source/evidence/errors/blocking、nested reason/code/ref 的 prose 全部移除；固定 code、enum、structured evidence 與合法 PR ref 保留。包含真實 mark_stage 的 free-form ref 及 history.resource header。 |
| B03；§10、§11 | `test_b03_unknown_occurrence_times_never_match_bounds_and_live_absence_falls_back`：缺少／無效 saved timestamp 不符合 migration time 的 since/until（單邊與雙邊），unbounded 仍列出 null occurrence；valid fact 以原時間篩選，live 缺 key／整個 context 時才用 created_at。Coverage flag 表示排除規則，first_recorded_at 保持記錄時間。 |
| B02；§11 | `test_b02_unchanged_field_freshness_polls_emit_no_events`：連續相同失敗／成功、僅 field_observed_at／last_activity_ms 前進都不寫事件。`test_b02_field_evidence_only_change_and_old_digest_upgrade`：僅 evidence enum 改變寫 update，fields_stale 保持 false；舊 digest 首輪相同 poll 不誤發。 |
| B02；§10、§11 | `test_b02_cursor_catchup_sse_resume_and_hidden_backfill`：分頁 gap catch-up、Last-Event-ID resume 不重複、backfill 不進 live feed、超前 cursor 422。 |
| B02；§11、§19（Part B） | `test_b02_dashboard_reopen_and_sse_gap_without_duplicates`（待第二步）：瀏覽器 baseline/reopen、frame 碎片/重疊/去重、token 更換、filters/草稿及 typing hold；Playwright 驗證。 |
| B03；§08、§11 | `test_b03_unknown_human_claim_is_not_api_actor_or_git_author`、`test_b03_rpc_admin_identity_is_not_claimed_human`：自報 操作者 保持 claim，RPC admin 是 local-admin；Git author 不升為 API actor。 |
| B02、B03；§11 | `test_b03_states_unknown_no_tab_and_field_times`：meta null／失敗、無 tab、journal-only null、離線與 gone 分軸；失敗不刷新上一個 activity 時間。Main 沒有可證明 session 終止的正式 journal source，因此 lifecycle 保持 unknown，不用 gone／turn abort 推論 ended。 |
| B03；§08、§11 | `test_b03_backfill_hidden_idempotent_and_unknown_boundaries`、`test_b03_migration_failure_rolls_back_and_restart_recovers`、`test_b03_backfilled_occurrence_time_filters_are_not_migration_time`：舊 journal 回填、全交易失敗回滾、重啟不重複、未知 range 邊界、原發生時間與隱藏 cursor。 |
| B03；§08、§11 | `test_b03_version_one_journal_runs_observation_backfill_once`：版本 1 → 2 回填一次；重開及已為 2 的 journal 不執行；新 journal 為 2，不重複回填。 |
| B01、B03；§08、§10、§11 | `test_b01_b03_saved_operation_refs_exclude_later_events_and_links`：early step、receipt、operation/checkpoint_run link、eventless operation 的 sources 或 target.operation_id；late step 與 resource.bound 兩種來源都掛 session/worktree。P 前 facts 不到後來資源，context 不含後來 IDs；原 events 的已知 refs 在 live/replay 一致。Inclusive seq=P 可見、snapshot <P 不可見；seq=None 目錄不變，unknown P 不推算 refs。重呼 backfill／reopen 零 writes。 |
| B01、B03；§08、§10、§11 | `test_b01_b03_checkpoint_refs_exclude_later_runs`：checkpoint 固定 capture source 保留，早 work-item event 不掛晚 run；checkpoint.continued、eventless run.created_at、unknown run time 三種證據，direct checkpoint_run 與 fallback 都守相同 boundary，live/replay 一致。`test_b03_delivery_snapshot_backfill_preserves_version_chain_and_private_text` 保留 delivery migration、原 documents、actor/privacy 及 reopen assertions；binding 前 preview/settlement 只從明確 kind 的 feed 讀，不回掛後來 session。 |
| B02；§10、§11 | `test_b02_related_event_feed_bounds_links_by_its_captured_head`：related-resource feed 使用已捕捉 head，晚 linked_at_seq 不在早 read 中出現；正常新 head 仍讀得到晚 link。只替換 journal instance 的 api_head，不全域 monkeypatch stdlib。 |
| B01、B03；§08、§10、§11 | `test_b01_b03_relation_closed_body_keeps_the_final_command`：兩個已連結 command 的 closure 保存最後一個，無 command 保存 null；end_command_id 在 history、current body、event seq revision、relations endpoint 四處一致，live／版本 1 replay 都驗證。用本包 iso 函式的遞增時間 fixture 驗證 live closure 只建一個 ended_at，不全域替換 stdlib。`test_b01_b03_relation_events_equal_their_lifecycle_revisions`：open/bind、lead/reviewer、replacement/parent、close 的所有新事件 body 逐欄等於 revision；legacy 只正規化未知的時間邊界。 |
| B03；§08、§11 | `test_b03_version_one_closure_reconstructs_the_final_command`：真正沒有 relation 事件的版本 1 task 從兩個 commands 重建 closed revision，ended_at=null。`test_b03_legacy_closure_event_cannot_erase_its_reconstructed_command`：舊 relation.closed 的 null 不覆蓋已證明的最後 command，原 core event body 保留；既有 projection failure 測試持續保證核心寫入與 gap flag。 |
| B01、B03；§08、§10、§11、§16 | `test_b01_b03_delivered_merge_history_uses_only_explicit_refs`：真實 fake GitHub merge/verify steps、session refs、worktree refs、無來源時不造關聯；PR title/body 不進事件摘要。`test_b01_delivery_preview_late_binding_respects_history_as_of`：晚到的 preview binding 不改舊游標結果。 |
| B03；§08、§10、§11、§16 | `test_b03_merge_receipt_history_keeps_moved_base_shas_without_commit_messages`：queue 受理後 base 前進，保存 actual merged/onto SHA、額外 commits 數量/parents，不回 commit message；後續 mutable refs 不改舊 receipt/context。 |
| B03；§08、§10、§11、§15 | `test_b03_metadata_settlement_history_has_codes_without_pr_text`：not_applied/conflict 回執各一事件、不重送 PATCH、不將背景觀測歸為 操作者。`test_b03_delivery_snapshot_backfill_preserves_version_chain_and_private_text`：版本 1 已有 delivery tables/documents，回填一次至 2、重開無寫入、原 documents 保留、原時間與未知 actor 保留。 |
| B03、C07；§08、§09、§10、§11、§15 | `test_b03_acknowledged_conflict_settlement_is_in_history_without_pr_text`：acknowledged PATCH 的 conflict settlement 可從 operation events 與明確來源 session history 讀到，保留 code、排除 PR text；live/backfill 兩路徑驗證，重複 insert 保留原回執、不追加事件，重開不重複回填。既有 `test_metadata_acknowledged_write_conflict_settles_and_releases_pr` 保持全部 delivery assertions。 |
| B01、B03；§08、§11 | `test_projection_failure_keeps_core_write_and_flags_event`：task state 與 operation step 的投影例外只回滾 savepoint，核心寫入及外部 step 成功；history 顯示 projection_error，無部分 resource／relation rows。 |
| B01、B03；§08、§10、§11 | `test_b01_b03_relation_history_keeps_roles_and_strips_free_text`：lead/reviewer 共用 session，execution/session history 的 opened/bound/closed 都保留 role；live 與版本 1 重播各驗證，nested source/result versions 的 role 保留，備註/prompt/commit message 仍移除，重開不追加事件。 |
| B01、B03；§08、§10、§11 | `test_b01_b03_pending_replacement_closure_links_only_its_own_session`：old 已關閉、new 無 branch 且 pending 後 task 關閉；old 沒有 new 的 closure/refs，new 收自己的 closure；task-source work-item link 只到當時的 new；execution 分頁每事件一次。Live／版本 1 replay 各驗證，重投影舊 milestone 仍用原 seq 的 old。 |
| B01、B03；§08、§10、§11 | `test_b01_b03_saved_task_facts_use_their_own_time_and_keep_execution`：版本 1 的 work-item link、integration receipt、eventless operation（sources 或 task ref）、operation step，各用自己的 linked_at/created_at/started_at。A 當時 open、之後 closed，B 後來 open；fact 只到 A 與 execution，不到 B，也不因 A 後來換 worktree 而掛新 worktree。相同 timestamp 用嚴格更晚 event；gap、無時間但 B 仍 open、原 journal 最後 event 之後，均只連 execution。Direct retry/reopen 零 writes。 |
| B03；§08、§11 | `test_b03_saved_fact_missing_or_unusable_timestamp_never_fails_backfill`：missing/null/空字串、非數字、布林、Inf/NaN、超界日期不拋例外，不 fallback 到其他時間；context/snapshot 為 unknown、無推算 session link。Epoch 0 仍是可用時間。 |
| B01、B03；§08、§10、§11 | `test_b01_b03_parallel_lead_reviewer_relation_events_have_exact_links`：同時開啟 lead/reviewer，共同 milestone 到兩邊；每個 opened/bound/closed 的 resource links 與 context IDs 只到自己的 session/relation，live／版本 1 replay 一致。 |
| B03；§08、§11 | `test_b03_malformed_relation_events_log_evidence_without_links`：三種 relation 事件缺 relation_id 或 session_resource_id，log 不洩漏 body，自帶 branch/caller refs 不能補猜；核心 row/evidence 保留、無 resource links，live／版本 1 replay 一致。 |
| B03；§08、§11、§15、§16 | `test_b03_backfilled_summary_retains_bounded_metadata_and_nested_roles`：saved_snapshot 的 enum/ID/seq/SHA/布林及 nested role 保存，混合容器、自由文字、數量/評分繼續移除。既有 `test_b01_b03_delivered_merge_history_uses_only_explicit_refs` 與 `test_b03_acknowledged_conflict_settlement_is_in_history_without_pr_text` 同時驗證 head_repo_id、files_may_be_truncated、write_acknowledged 的正式 writer 輸出及 PR 文字隔離。 |
| B01–B03；§10、§11 | `test_b01_b02_b03_http_mcp_cli_contract_parity`：HTTP/實際 MCP server/CLI 經同 daemon；params、keys、cursor、context 一致；observe、404、422 契約及四個新 tools。 |
| B03；§06、§11 | `test_b03_observation_never_starts_resumes_rehydrates_or_locks_git`：MockBat 零 write/git:status；unsafe Claude state 不呼叫，journal 讀取不讀 Fleet/registry、不寫 DB；temp repo HEAD/index/refs/files 不變且無 locks，既有 on-demand Git probe 使用 no-optional-locks。 |

現有基線證據：

- `tests/test_api_v1.py::test_inventory_observes_read_only_and_pages_every_row_once`、`test_inventory_keeps_offline_hosts_stale_and_marks_gone_after_two_misses`、`test_inventory_keeps_what_one_refresh_did_not_observe`：讀取限制、部分 B01/B02 的 inventory 與欄位保留。
- 同檔 `test_task_events_project_into_the_api_cursor_and_old_journals_backfill`、`test_sse_stream_resumes_from_last_event_id`：持久 seq／SSE 從指定 ID 補讀；未涵蓋完整 Dashboard reopen／gap 去重。
- `tests/test_task_service.py::test_minimal_prefers_warm_session_id_and_goose_provider_order`、`test_warm_start_mismatched_ack_remains_uncertain`、`test_bat_warm_reuse_claims_only_clean_completed_service_session`、`test_warm_candidates_are_limited_to_the_same_workstream`：現有 warm reuse 行為；未證明分頁歷史 relations。
- `tests/test_work_items.py::test_links_point_at_known_resources_and_removal_keeps_history`、`test_http_reads_and_one_operation_path`：既有 link/unlink 保留與 session reads；未證明全 timeline 的 actor/context。

Part A 必須跑 `uv run ruff check .`、`uv run pytest -q` 全套，記精確摘要。Part B 修改 Dashboard 時，JS 複製成 `.mjs` 做 `node --check`，Playwright 對 zh-TW/en/390 px 驗證沒有 null/undefined/[object 文字及上述 reopen/SSE 情境。Fixtures 不含真實 host/token/session/workspace/email，不向真 host 寫入。

## 尚未涵蓋

- Part B：Dashboard 跨專案 filters、timeline、relations、scope 卡、reopen／catch-up／SSE 去重及 Playwright 驗證，下一個分支才實作。Part A 不修改 Dashboard。
- 不提供 inventory snapshot isolation，不保存每 poll 的 session revisions 或 scans；完整巡覽使用 order=id 加 events。
- 背景 Git/SSH status 探測不在本包範圍，成本與寫鎖風險不為補欄位而擴大。
- 未授權／未配置 profiles、從未觀測的人工無 tab 資源與任意歷史 transcript 的全面搜尋；本包承諾已授權掃描及 journal 所知範圍，顯示 outside scan。
- 固定 BAT 沒有全域、不可恢復的 session resource 終止證據。若 main 新 writer 尚未提供，resource-ended 仍 unknown；開放問題是未來 provider 應以哪個正式回執證明該終態，不以 idle/gone 補推。
- Host alias 的改綁合併及 profile 切換遷移；本版偵測 scope change 並保留舊身分，新範圍需獨立 alias。舊 profile 缺證據不追認。
- Operation unification／Task Service 共用 gate、delivery 剩餘 provider 功能、§23 tombstone／retained refs／restore；是否已在 main 是 Phase 2 的條件式 adapter 清單，未落地的 facts 仍待各包提供。
- Worktree diff／檔案瀏覽、附件/B04、歷史保留／pruning 政策、原生 BAT deep link。既有資料讀取與人工唯讀邊界不因這些待辦改變。依 v2 決策不提供 Hub 匯入；B05 改為 Connector 既有資料升級與穩定 ID 保留。


### Delivery Part B adapter（v2 R07）

`deployment.selected/updated/verified/superseded/backfilled` 與 `deployment.drift` 由既有部署 writer 發出，
帶 deployment/operation ID、generation、state、source SHA、artifact identity、provider run/attempt 與固定 code。
只有 operation 的明確 source binding 才連 session/worktree，不能由 recipe/PR/SHA 推定來源。
provider_url 是去除 query/fragment 的 HTTPS URL；不含 verifier body、recipe config 或自由文字 diagnostics。
Step 3 的 backfilled 是當下保存快照，occurred_at=null；原始事件與 step 2 不改。參見 delivery.md 與
`tests/test_deployment_history.py` 的 migration/privacy/transaction 測試。
