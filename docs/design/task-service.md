# 任務服務：持久協調與恢復契約

本文記錄通用 task-service 契約及相容路徑，不記錄任何使用者的主機配置、聊天來源、實際部署或私人驗收結果。現行產品範圍見[實作狀態](../product/implementation-status.md)。`needs_ted`、`ted_actions` 等既有欄位／狀態是相容識別碼，介面應顯示中性的操作者文案，不更名已保存的協定資料。

## 現在要解決的事

聊天整合或 CLI 提交一次原始要求，取得 task ID 後即可結束同步等待。原始文字逐字保存；舊呼叫者的 `interpretation` 僅作**非權威外部附註**，不放進 coding prompt。規劃與拆解交給熟悉 repository 的 lead session。服務持久保存任務、命令和進度，不能用聊天記憶、GUI 頁籤或短暫事件代替帳本。

等待可能被使用者插話取消，主機 metadata／握手可能逾時，程序也可能在接受命令後失去回覆。這些故障分別處理；監看取消不是任務取消，缺少 GUI tab 不是 session 消失，transport ACK 不是完整回合歸因。

## 服務與提交契約

`batc serve` 是擁有狀態目錄的單一長期程序。SQLite WAL 保存任務、命令及事件；loopback API 供已驗證客戶端使用。遠端客戶端需自行配置受控存取路徑，不能複製帳本再開第二個自動 owner。

`work_submit` 保存原話、來源參照、recipe、驗收條件及 idempotency key；相同 key 回原 task，同 key 不同內容拒絕。`work_status`、`work_pause`、`work_resume`、`work_result` 使用同一帳本。網路操作前先持久保存命令 intent；同一 task 的 host/session 只有一個 writer，送 frame 前重查 owner、pause 與 control version。未知結果保持 `uncertain`，**不重播 prompt**。

Goose 路徑的每個 task 使用其設定 session；`GooseConfig.enabled` 預設關閉。模型選擇、訂閱和配額資訊必須依可信設定／來源判定，不能把 recipe 提示當成可用性證明。相容 rules/minimal 路徑仍有自己的驗證與 reviewer 契約，詳見下節及 [provider-routing.md](provider-routing.md)。這些路徑的描述不表示任一私人供應商或外部部署已通過驗收。

### 相容 minimal 路徑與候選驗證

相容的 `task_path="minimal"` 是獨立 opt-in 路徑；它的 gate 不構成 Goose 可用性的證明。首次 `work_submit` 只列出**實際可執行**的引擎：Goose live contract gate 關閉時只有 rules，直接採用、不問 Jev（原因 `only_runnable_engine`）。只有兩個引擎都可執行時才問 Jev **一個** typed choice：`rules_engine` 或 `goose`；原文只作分類資料，不由 Hermes 改寫或拆解。Jev 不可用、逾時或 typed 回答無效時選 rules。選擇、信心、`jev_backend`、實際引擎與原因在同一提交交易中寫入 `engine_decision` 事件；重複 idempotency key 不再重新決定。最小路徑不記 PM 每步 routing；`work_status` 仍回報提交時的決策。

最小路徑的 `small-task-with-tests` recipe 是明確的小任務選擇，因提交時的一個 Jev 問題不能同時暗中判斷規模。它仍要求目前乾淨 commit/tree 上由服務觀察到的 trusted test 命令 exit code 0，並確認 lead 已閒置。測試通過後，服務取得完整的小候選 diff 與檔案路徑，把 diff 及 使用者的 `original_words`（離開主機前遮蔽疑似 credential）交給**一個 typed Jev choice**，同時判斷是否滿足要求、有無明顯風險。沿用 TypeSafe → OpenRouter Decisions `typesafe/jev-1.13` 的同題 fallback；不用 chat/completions、`typesafe/jev-router` 或一般 LLM。只有 PASS 且信心 ≥ 0.50、backend 有效、測試及乾淨 commit/tree 仍吻合，才略過獨立 reviewer。低信心、FAIL、風險／不確定、敏感路徑、diff 超過 3500 字、缺 diff／baseline、Jev 兩端無效或失敗，一律轉現有獨立 reviewer；敏感／過大 diff 不送 Jev。私有 0600 `BATC_PM_PROVIDER_CONFIG` 的 `[router]` 可調 `minimal_review_confidence_threshold`、`minimal_review_max_diff_chars`（最多 4000）和 `minimal_review_sensitive_paths` glob 清單，預設涵蓋 auth、secrets、CI、deploy、migrations、credentials、tokens、Docker、infra。`minimal_review_gates` 先記 candidate commit/tree、完整 diff SHA-256 與 pending，再記 verdict、confidence、backend、原因並寫事件；重啟遇 pending 直接升級 reviewer，不重問 Jev。`work_status.minimal_review_gate` 可查決策；保留事件另記 diff 字數與路徑。`python -m bat_agent_connector.gate_eval --db <journal>` 唯讀列出每個 gate 候選的 Jev 選擇／信心、同 candidate 的獨立 reviewer 結果，以及 0.50、0.45、「PASS＋極小非敏感 diff」三種政策的 false pass／false escalate 次數（不呼叫模型）。門檻仍為 0.50。`work_status`／`work_result` 的 `delivery` 區分 `verified`（受信測試＋reviewer／gate 通過）與 `adopted`、`merged`、`deployed`；後三者只在以 `work_mark_stage` 記錄（附 commit／PR／部署參照）後出現，服務本身不 merge、不部署。`work_submit` 可附 `context_refs`（attachments、previous_message_id、plan、commit），僅保存為資料。通過獨立 reviewer 時仍需同 commit/tree 的結構化 PASS verdict（見〈Release boundary〉）；Jev PASS 本身不代表測試通過。其他 recipe 照舊需要 reviewer。服務只在**同一工作流**的後續任務重用同 project、host、workspace、agent 的**已完成、沒有共享 reviewer session** task 之既有 lead session/branch：新任務的 `parent_task_id` 指向該 task（或同一 parent 的兄弟 task），或 `continuation=true` 且 `discord_thread_id` 相同；並且 worktree 目前 HEAD 必須等於前一 task 的 `verification_commit`。獨立的新請求一律從 base 開新 branch（工具快取照常共用）。移轉前撤銷舊 task-scoped capability，移轉後任何舊 task 工具讀取遇到 registry 所有權變更即拒絕；須驗證 registry 所有權、BAT meta、worktree path/branch、Git root、非 streaming、無 pending、工作樹乾淨且寫入間隔已過，再以 registry lock 原子轉移所有權。任何證據不足即建立新 session/worktree；重新接手仍記新的 task branch，task_id 不變。帶明確 `base_branch` 的 task 不跨任務重用。此路徑仍遵守全域每小時寫入上限與後續 prompt 間隔。

### 里程碑事件與外部通知

里程碑事件流：服務不再知道 Discord 存在，也不發任何聊天訊息。唯讀 MCP 工具 `work_events(since_cursor, limit)`（RPC `work_events`、CLI `batc task-events`）只回傳四種里程碑：`started`（首次 accepted/running，或 needs_ted 後恢復）、`needs_ted`（附具體原因）、`done`（附 commit／PR 連結）、`failed`。每筆含單調遞增的 `cursor`（event id）、`task_id`、`project`、`workspace`、`origin_thread_id`（提交時的不透明來源參照，欄位仍名為 `discord_thread_id`）、`kind`、簡短 `summary`。`next_cursor` 會越過非里程碑事件；整合讀取端發文成功後才保存游標；`limit=0` 只回 `head_cursor`，新讀取端可從「現在」開始、不補發歷史。主要路徑是推播：私有設定 `[task_service.event_webhook]` 的 `url`（僅限 loopback）與 `secret_file`（0600）設定後，每個已提交的里程碑依 cursor 順序以純 JSON POST 出去（HMAC-SHA256 `X-Webhook-Signature-V2`、`X-Webhook-Timestamp`、`X-Request-ID`、`delivered_through`），推播游標首次設定時從「現在」開始、只在 2xx 後前進，失敗以指數退避（上限 300 秒）重試；`work_events` 只作接收端斷線後的補漏。舊帳本開啟時移除 `events.discord_status`、`events.discord_message_id` 與 `board` 表，舊積壓永遠不會被發出。

### 啟動與私有設定

`batc serve --host 127.0.0.1 --port 18796` 僅在明確啟動時運行；寫入操作另需主機 `writes=true`、`orchestrate=true` 及 API scope。MCP 透過 `BATC_TASK_URL`（預設 `http://127.0.0.1:18796/rpc`）連同機 daemon，本身不擁有第二份帳本。這些是產品介面預設，不是任何使用者的端點清單。

受信測試只執行管理員預設的命令，缺設定時不能把候選標成已驗證。Goose binary、provider base URL、model、token 參照與 credentials 由私有設定提供；設定值及原始對話不提交 Git。啟用、升版或切換整合路徑需依[cutover 指引](hermes-cutover-plan.md)與下方契約門檻驗證。

`submitted_at`、`delivered_at`、`delivered`、`review_rejections`、`ted_interventions`、`session_replacements` 是既有帳本欄位。`ted_interventions` 是呼叫者記錄且來源 ID 去重的介入次數，不能當成受信外部事件或實際使用者活動統計。Provider 使用次數也不是訂閱剩餘額度。

### Send receipt 與 verification 故障界線

Send intent 保存完整 prompt SHA-256 及送出前最後一筆 BAT message ID，或可核實的空 session 證據。回覆不明時，只在**同一 session** 唯讀查最多 100 筆、最多兩次且總共五秒。必須見到 fence 之後**恰一筆**完整 user prompt、匹配的 SHA-256 和 BAT user message ID，才標 `accepted` 並記 `send_reconciled_delivered`。

這只證明 prompt 送達，不證明測試、review 或 task 完成。缺 fence、分頁排除訊息、文字不符、僅有時間戳或後續 `REVIEW: PASS` 都維持 `uncertain`；重啟也不重送。BAT 回 `accepted` 而 Codex 僅附 `timestamp_cursor` 時，仍要相同 read-back 證據才能升為可歸因的接受結果。

驗證截止必須涵蓋主機查詢、候選檢查、測試與 reviewer 等待，不能只限制單一 subprocess。例外只記結構化原因或例外類型，不保存可能含敏感內容的原始錯誤。進度、絕對期限、程序退出證據與有限返工規則見下方〈Verification lifecycle〉。

參考：[架構評估](no-bat-change-evaluation.md)、[下一代設計](next-gen-connector.md)、[回歸檢查指南](../research/2026-09-28-whole-repo-review.md)。

## 可恢復的狀態機與帳本

單一 task ID 永不更換。`branches` 表另記每次 BAT lead／reviewer session 與 PM provider 選擇，包含 `branch_id`、`session_id`、provider、角色、上游 branch ID、原因、時間；`work_status` 回傳完整 branch 歷史。這裡的 branch 是任務執行分支，與 Git branch 名稱不同。`tasks` 保存原話、工作空間、recipe、狀態、pause/control_version、lead/reviewer session、turn marker、commit/tree、review marker、提交／交付時間與指標。`commands` 有唯一 idempotency key、預留 session ID、BAT message ID、kind、送出前游標和狀態。`events`、`routing`、`provider_usage`、`observed_verifications`、`capabilities`、`ted_actions` 與 owner lease 各自保存可稽核資料。所有網路呼叫前先提交命令意圖；同一 key 不會生第二條命令。

### Session 身分、缺失訊號與恢復界線

每次初始 lead **和 reviewer** prompt 前，以該 task 的 durable branch／start intent、BAT 精確 session ID metadata、workspace/worktree path 和唯讀 Git root 核對；既有 registry row 也要比對，不再因 row 存在就略過驗證。**Lead 的 `worktree:status` 必須明確回 path 和 branch，且與 registry／Git root 相符；即使 session metadata 有值、舊 registry 有 path/branch、磁碟 Git root 存在，BAT worktree status 為 null 仍屬未知，不能送初始 prompt。** Headless reviewer 的**自身** BAT `worktree:status` 常為 null，因 reviewer 沿用 lead worktree；此時 lead 的 BAT worktree status 仍須明確回 path/branch，並與 journal 的 lead session、reviewer metadata `cwd` 精確相等，才能恢復 reviewer lookup。BAT lead/reviewer start 回覆都必須含與預留 ID 相同的 `sessionId`，缺失或不同都不能視為成功。若 BAT session 存在但本地 registry 遺失，就以受驗證資料補回 headless lookup，**不新建 BAT session**。

**null 不是消失證據。** BAT protocol 說 `claude:get-session-meta` 可在重啟後、session 尚未 resume 時回 null；`worktree:status` 也可在 host 記憶狀態消失但磁碟 worktree 仍在時回 null。三次 meta 都為 null 時，BAT adapter 只回 `uncertain`，不標 `vanished`、不釋放原 registry 名額、不建替代分支；就算 worktree status 也為 null，結論仍然相同。若尚未建立 send intent，下一 worker tick 可重新驗證身分；若已建立 intent，仍須按命令對帳，不能重播。現有 `session_replacements`／最多一次 branch 機制只供日後有**明確、強於 null 的 host absence proof** 的 adapter 使用；真 BAT 目前沒有這種證據，故自動替代保持停用。無 GUI tab 不代表 BAT session 消失。

Pause 在任何等待的 presence lookup 後重查；`service.session_send` 的 session/meta/state 等待結束後、BAT send invoke 前，以及 BAT client 建連後送出 WebSocket frame 前，再以 journal task control version、pause、session ID、command ID/status 和 prompt hash 作同步檢查。已寫但確定尚未送 BAT frame 的 intent 標 `cancelled`，resume 用新 control version 建新的 send command。對已送 frame 的不明回覆仍維持 `uncertain`，不能因 pause／resume 自動重送。這些檢查在 task/session writer lock 內執行；pause 可以先持久化控制版本，晚於 frame 送出時則屬已開始的回合。

| 原狀態 | 可自動進入 | 條件 |
| --- | --- | --- |
| `queued` | `dispatching` | 預留 lead session ID、寫 start intent 後啟動 |
| `dispatching` | `accepted`／`verifying`／`uncertain` | BAT start 明確成功／reviewer start 成功／回覆不明 |
| `accepted` | `running`／`uncertain`／`queued`／`needs_ted` | 已先記 `needs_review` 送出標記；BAT 回覆可歸因／不明；`queued` 替代只供日後有明確 host absence proof 的 adapter，BAT null 訊號不能啟用 |
| `running` | `accepted`／`verifying`／`waiting_permission`／`quota_limited`／`needs_ted` | 回合結束續推／里程碑／權限／額度／人為阻塞 |
| `quota_limited` | `uncertain`／`needs_ted` | Claude→Codex 原子預留 successor 與獨立 handoff send；Codex 送出後等命令證據或人工對帳 |
| `verifying` | `done`／`accepted`／`needs_ted` | 乾淨候選 commit/tree 的觀察測試與 fresh reviewer PASS／REJECT／測試失敗 |
| `uncertain` | `accepted`／`running`／`verifying`／`human_owned`／`done`／`needs_ted` | 可歸因的 BAT 證據或有權操作者針對特定命令明確處理；絕不自動重送 |

`pause` 先持久化控制版本，停止新派送；可選擇 interrupt 當前 turn。start 等待期間發生 pause 時，已接受的 session 保存為 `accepted` 但不送初始 prompt。若 pause 在 send 前的 presence lookup 期間發生，服務在 BAT send 呼叫前再次讀 pause，取消尚未送出的命令。`resume` 清除 pause，下一 tick 先查 pending 命令；若無待對帳且 session 尚未收到 prompt，才以新的 control version 送第一次 prompt。每個 host/session 在單 daemon 內有 writer lock；程序間以私有 owner lock 限制為一個 daemon（POSIX 使用 `flock`；Windows 使用平台鎖定）。heartbeat 只作觀測，不能搶占存活 owner。外部 BAT GUI 不受此鎖控制；connector 舊低階工具也先走 task gate。遇無法歸因的 turn 仍保留 uncertain。

送 prompt 前先持久記 `needs_review`，包含 message ID、prompt SHA-256 與送出前游標；即使 crash 發生在實際送出前，也按**可能已送出**處理。逾時、斷線或回覆遺失後保持 `uncertain`。Claude `session_read` 的 `correlated`／`correlated_after_prior_turn` 可用於對帳；Codex 的 `timestamp_cursor` **只是時間位置，不能證明回覆屬於本命令**，連同後來出現的 `REVIEW: PASS` 都不能用來結案或自動重送。Codex 只有明確的命令／turn 綁定證據才可自動歸因，否則走下述人工對帳。Reviewer PASS 同時要求獨立 reviewer prompt／turn 歸因、lead 已停筆、乾淨的當前 commit/tree 和受信測試 exit 0。BAT 明確回 busy/rejected 時標 rejected、請 操作者 處理，不假設可安全重送。start 對帳使用預留 session ID 和 BAT meta；lead／reviewer／failover successor start 的 ack 都須回精確預留 session ID。相同 worktree 的 failover 另比對 host worktree path/branch、Git branch 與 registry branch，任何變動都 fail closed。failover 的 registry＋BAT meta **只證實 successor session 存在**，handoff `batc-*` ID、registry `sent` 和後來不相關的回覆都不能證明 prompt 所屬 turn。handoff `send` 維持 uncertain，可用獨立一次性命令 capability 人工對帳；確認 session 閒置後才可送文字不同的新 prompt。遇 candidate commit 或 tree 改變，清除舊 verification/reviewer/PASS，建立新 reviewer session。

Failover 預設把 task ledger 摘要交給 successor：操作者的**完整逐字**原話、狀態、候選 commit、近期命令／事件，避免以可能極長或過時的聊天全文作權威。原話可在 handoff 內完整容納時直接寫入，**不再截到 2,200 字**。超過 3,000 字就先把完整原話寫入 0700 私有目錄中的 0600 archive，以 SHA-256 驗證，再由明確配置的 `BATC_TASK_ARCHIVE_VERIFY_SSH_HOST` 以 BAT successor 身分執行唯讀 hash 檢查；`BATC_TASK_LOCAL_HOST_ALIAS` 必須等於 task host。handoff 僅送 archive 路徑、字數、hash 與先讀原話的指示，不能稱縮寫摘要會覆蓋操作者要求範圍；實際 BAT handoff prompt 在送出前再次檢查路徑／hash 或完整內文仍在。archive 無法被 successor 驗證時 fail closed，不送 prompt。提交原話上限 19,000 字，超過時明確拒絕，避免初始 BAT prompt 靜默截斷；18,000 至 19,000 字的 failover 一律用完整 archive。若 ledger 摘要失敗，且本機路徑共享條件成立，服務才讀 BAT history 並建立同機 0600 archive／excerpt，**同時保留上述權威原話或原話 archive**；遠端無法共享路徑時 failover 保持 uncertain。超過 200,000 字時 excerpt 保留前 12,000＋後 148,000 字並標明中段省略。archive 絕不能放進 Git。

Failover 恢復只接受帳本預留的**同一個 successor session ID**。`failover` intent 要指向同 task 的獨立 handoff `send` command；兩者的 session ID、原 lead ID、handoff message ID／command ID 必須與 registry 完全相同。BAT 必須明確回 successor metadata 的工作目錄、舊 lead 與 successor 各自的 `worktree:status` path／branch、以及唯讀 Git root，並與原 lead registry worktree 一致。null 是未知；實際值不同或先前 successor 搶占是衝突，持久記為 `operator_only`，重啟後也不自動採用。衝突時不改 command session、task session 或 branch 歷史，不重送 handoff；管理員可用 task／command-scoped `work_reconcile` 記錄證據並交人工接管，但不能憑它把未驗證的 successor 自動變成新 lead。低階 failover 遇已存在的 successor，也必須與 caller 預留 ID、handoff message／command ID 一致。Reviewer 借用 lead worktree，只是讀取者，不擁有自動 cleanup／merge 權限。

Failover handoff 在舊 Claude 狀態查詢、新 Codex session start、BAT 連線等等待之間可能遇到 pause。服務在 handoff intent／prompt hash 已入帳後，送出前重查 task `paused`、`control_version`、原 lead／預留 successor 與 handoff command；BAT client 在真正寫入 `claude:send-message` frame 前再做一次相同檢查。若 pause 已提交，handoff `send` 留在 uncertain、failover 標 `operator_only`，恢復派工也不會重送，須由有權操作者對帳。

BAT 確認預留 successor start ID 後，**handoff 送出前**先查 successor metadata `cwd`、舊／新 session 的 worktree path／branch、Git root，與 task 舊 lead registry、預留 ID 和 handoff command 綁定；任何具體不符都不送 handoff，轉 `operator_only`。BAT client 在提交實際 handoff frame 前計算該 frame 的 prompt SHA-256，與 journal intent **逐字 hash 相等**才寫入 frame，並將該 frame hash 記入私有 registry。正常回覆與重啟恢復都要求 registry frame hash 等於 journal prompt hash；只有 64 位 hash 的外形並不足夠。null host 身分仍是 uncertain，不用猜測或重送。

Task lead 的 `task_id` 在 `session_start` 的 registry 預留交易當下寫入，早於 worktree 建立和 BAT start。即使 BAT start 已完成而 task 在初次 prompt 前被 pause，舊 `session_cleanup`／`main_session` 也必須排除它。外部 task worktree 到 `done`／`failed` 時，只在乾淨、HEAD 與 branch 相等，且 `done` 的 HEAD 等於已驗證 commit 時移除工作樹。移除前建立 `refs/batc/tasks/<task-id-prefix>` 指向完整 commit，保留原 branch；**不執行 force remove 或 branch -D**。journal 只有收到路徑、branch、保留 ref、commit 的明確證據才清除工作樹指標並記事件。Git 移除後若程序中斷，重啟依仍在的兩個 ref 對帳；失敗則保留指標並退避重試，不以忽略錯誤當作清理成功。

Successor 身分在 handoff 前查過後，BAT 連線等待仍可能改變 registry 或 host 狀態；所以連線後再查一次 successor `cwd`、舊／新 worktree path／branch 和 Git root。實際 frame 邊界以同一個 registry 檔鎖原子比對 task ID、預留 successor ID、`failover_of` 原 lead、共享 worktree、path／branch、handoff command／message ID 與 pending 狀態，並寫入實際 frame hash；在送 frame 前再檢查 pause／control version。若 owner link、Git root 或其他具體身分在兩次查詢間變動，零 handoff frame，命令維持 uncertain／operator-only。這是 connector 自身送出界線；BAT 外部行為仍須獨立實機驗證。

BAT client 的最後一輪 host 身分查詢現在在**取得送出 semaphore 之後**執行；這三種查詢只允許 `claude:get-session-meta`、`worktree:status`、`git:getRoot`，直接使用已連線的唯讀 roundtrip，不再次取得同一 semaphore。查詢完成後同步執行 task pause／control-version、journal prompt hash 與 registry 原子 owner/link 檢查，緊接著提交 handoff frame；等待 semaphore 期間改變 Git root、worktree branch 或 `failover_of` 都必須是零 handoff frame 並留下 uncertain／operator-only。這仍不是 BAT 提供的跨系統原子交易；外部直接修改 BAT session 的競爭需真機契約測試界定。

## 權限、設定與操作恢復

HTTP 僅聽 loopback 並檢查 peer。一般 work RPC 需本機 0600 admin bearer token；Goose 只取得 1 小時的 task-scoped capability，不能呼叫 `work_submit`、操作另一 task 或直接提供測試 exit code。對不明一般 send 或 failover handoff send，管理員用 `work_reconcile_capability` 為**一個 task／command** 發 10 分鐘一次性 capability，再用該 token 呼叫 `work_reconcile`，提供 `delivered`／`not_delivered`／`superseded`、操作者、來源和證據。admin token 本身不能直接解決命令。`batc task-reconcile` 在本機完成這兩步；若要接續，必須提交**不同文字**的新 prompt，先記新 intent，並確認 BAT session 閒置；新 prompt 不重播舊命令。人工聲稱 review PASS 還要提供當前 commit/tree、exact turn reference，服務重查乾淨候選、reviewer 已停筆及當前觀察測試。無證據的 resolve 留在 `human_owned`，`work_resume` 不會偷偷續推。這是有權操作者的可稽核證詞，並非 BAT 自動證明。POSIX 狀態目錄 0700；SQLite、token、lock、私有 handoff archive 0600。Windows 使用對應 owner-only ACL 與安全檔案操作。不要將 token、shim 金鑰、原始聊天或 provider 錯誤全文寫入日誌。遠端整合客戶端需透過受控的 loopback 轉接與驗證身分呼叫同一 daemon；權限依部署設定，不把 loopback HTTP 直接開到 LAN。

私有 `BATC_TASK_SETTINGS` TOML 必須 0600，例如 `[verification.commands]` 的 `project = ["uv", "run", "pytest", "-q"]`；remote host 可用 `[verification.ssh_hosts]` 指定既有 SSH alias。測試 stdout/stderr 只存本機 `task-artifacts` 的 0600 檔案（至多保留前 2 MB，另存完整輸出 SHA-256）；可在 `[verification]` 設 `artifact_dir` 到私有路徑。`[task_service] register_tabs = true` 是額外的 service-only 顯式開關，預設 false；主機既有 `orchestrate_register_tabs=true` 也必須同時成立。lead 和 reviewer tab 都用 connector 的 append/revision recheck；不改 BAT。介面必須清楚顯示 `workspace:save` 整份覆寫的 race。

（已移除）舊的 `BATC-EVENT`／`BATC-BOARD` 發文、`batc task-delivery` 與 `work_delivery_*` 對帳流程已刪除；聊天投遞由擁有 Discord 的 Hermes 依 `work_events` 游標負責。

若 failover 身分有明確衝突，管理員也可對 `failover` command 取得一次性 capability 並對帳；此動作同時結清它綁定的 handoff command、留下 actor／source／evidence，task 進入 `human_owned`，**不**把未知 successor 變成 lead，也不能在同一次對帳送新 prompt。若 handoff command 本身的帳本綁定已損壞，必須先由管理員修復帳本，服務拒絕自動採用或猜測。

## PM provider 設定與限額

私有 `BATC_PM_PROVIDER_CONFIG` TOML (0600) 有多個 `[[providers]]`：`id`、`kind`（`agy-shim`、`openai-compatible`、`codex-acp`、`claude-acp`、`gemini`）、`base_url`、`model`、`daily_cap`；預設頂層 `fallback_order = ["claude", "agy-claude", "codex"]`。`claude` entry 為 `kind = "claude-acp"`、`model = "claude-opus-5-5"`；`agy-claude` 為 `kind = "agy-shim"`、`model = "claude-opus-4-6-thinking"`。前者在 Goose 子程序用 `GOOSE_PROVIDER=claude-acp`、`GOOSE_MODEL=claude-opus-5-5`；後者用 `GOOSE_PROVIDER=openai`、`GOOSE_MODEL=claude-opus-4-6-thinking`、`OPENAI_BASE_URL` 與從環境讀取的 `BATC_AGY_SHIM_TOKEN`。Anthropic [官方模型文件](https://platform.claude.com/docs/en/models/opus-5-5/overview) 確認 5.5 的完整 ID；[Goose v1.52.0 Claude ACP source](https://github.com/aaif-goose/goose/blob/v1.52.0/crates/goose/src/providers/claude_acp.rs) 使用 provider 名 `claude-acp` 並轉送 session model config option。這是程式與 fake ACP 契約證據，**尚未在已安裝 Goose／Claude ACP 驗證 5.5 可用性或實際消耗 Claude 額度**；live gate 仍關閉。`base_url` 僅允許無 credential 的 loopback HTTP；不能提交設定或使用付費 API key。每 task 可給 `pm_provider`，recipe 可給預設；未給則首選 Claude ACP。新 provider 只需一個小 adapter class、設定 entry 及自己的 fake endpoint／ACP contract test。設定日上限按 UTC 日計成功使用次數；`quota_error`、`rate_limited`、`auth_error` 只能在**確認未送 prompt**時切換至下一 provider，寫 `provider_usage` 與新的 task PM branch。已送、`needs_review` 或 uncertain prompt 必須先對帳，禁止切換時重播同一內容。錯誤以結構化 HTTP status/code 分類，不以可能含敏感內容的錯誤字串猜測。

`agy-claude` 的唯一允許 model 是 `claude-opus-4-6-thinking`；預設 `claude` 只允許 `claude-opus-5-5`，所以不能因省略 model 意外用到 Sonnet。`[router]` 可設定（舊 `confidence_threshold` 仍接受但不使用）`agy_claude_daily_cap`（預設每日 10 次已記成功使用）、`status_provider`（`agy-gemini-flash`）、`scarce_provider`（`claude`）、`secondary_provider`（`agy-claude`）、`fallback_provider`（`codex`）、`allow_gemini_status`（true）；沒有 Gemini endpoint/model 時不會選它。Task 明確指定 provider 優先於 recipe，recipe 優先於 router，所有 override 以原因記錄。ModelRouter 的使用量與 provider 錯誤來自本服務帳本；只做路由決策的 rules 步驟不算 API 使用。受信驗證命令 argv 可能含私有值，因此 journal 僅保存 argv SHA-256，不複製原始參數；受信私有設定與 0600 artifact log 可供管理員核對。

Jev 共用客戶端先用 TypeSafe `/v1/systemone`；主端逾時、錯誤或回傳不符合 typed `choice`／`noul` 機率契約時，以環境變數 `OPENROUTER_API_KEY` 呼叫固定 `https://openrouter.ai/api/alpha/decisions`、固定 model `typesafe/jev-1.13`（[OpenRouter Decisions 模型頁](https://openrouter.ai/typesafe/jev-1.13)）；送出的 JSON 只包含 `model`、`state`、`questions`，回應須有通過同一 typed validator 的 `answers`。沒有 key 就跳過；兩端都失敗時沿用原確定性路徑：minimal 引擎選 rules、候選審查轉完整 reviewer、triage 保留 pattern、cleanup 不因 Jev 缺席而 merge。不使用 chat completions、一般大模型或未經 Decisions 契約驗證的 alias 作備援。任何 key 只讀環境、不寫 TOML／journal／log。引擎選擇與 minimal 候選審查記錄回答者 `typesafe`／`openrouter_jev`，未回答則 null。fake transport 測試僅涵蓋模型呼叫契約；真端點、帳號存取與實際配額需使用者在私有環境驗證。PM 步驟 provider 以明確規則選擇（見上），不呼叫 Jev。規則引擎仍是 deterministic，routing 記錄的 provider 就是實際啟動的 BAT agent；Goose live gate 仍關閉。額度 cap 是服務的保守計數，不代表第三方真實剩餘額度。OpenAI 相容 adapter 的探針要求 `/v1/models` 含**確切**設定 model ID；ACP/provider 的完整驗證仍需 pinned Goose contract test。真 provider 執行、成本與品質 A/B 屬 M2。

## Pinned Goose 啟用門檻（M2 contract-test checklist）

此清單用於選擇性 Goose adapter 的啟用與升版。相容設定固定 **Goose v1.52.0**；`goose --version` 必須符合設定，並核對[官方 release](https://github.com/aaif-goose/goose/releases/tag/v1.52.0) 的下載與 checksum。Goose 與 connector 分開 pin 版本，不改簽署的 Goose bundle；私有 integration config 與 handoff state 先備份，不能提交。每次升版在**隔離 synthetic session** 完成下列檢查後才可能開 live Goose：

1. 跑 connector repo tests／syntax，對安裝的 Goose 驗證 `initialize`、`load/new`、`configOptions`、`session/set_config_option` 的真實 schema 與回覆。
2. 檢查 host UI 真的顯示 handoff 與 target-model 選擇；target setup 必須驗證回覆的 provider choice 與 approval-mode setup。wrapper 對 protocol version 非 1 必須拒絕。這些檢查仍非完整相容性保證，未知 protocol/UI 變更可能破壞流程。
3. 切換 provider、記 synthetic marker、reload 後確認 history 與 identity；強制 provider 失敗，確認原 session／內容保留。工具 approval 要保留為 approval，不能自動變成 granted permission。
4. 失敗時保持 handoff 停用，保留舊 session／私有 state，把 adapter/config 回退至 last known good。略過 wrapper 只會回原 agent，不會合併 target branch messages。
5. 替換 relay 之前要讓 in-flight prompt 結束或明確取消，不能為安裝功能殺掉 active writer。

目前 ACP adapter 的 fake smoke 使用 protocol 1，遇非 1 或沒有 session ID 即拒絕；上述 `load/new`、`configOptions`、`session/set_config_option`、真 provider choice／approval UI、故障保留、rollback 及 active-writer 項目尚**不是可執行 gate**。此外還要證明 task-scoped MCP capability、subprocess env/process 隔離、ACP session 在 daemon 重啟後可恢復、provider auth/quota 分類、no-resend 及 pinned 版本在目標主機的實機行為。現有 BAT adapter contract tests 使用 mock host，涵蓋 start／recover／Codex send／reviewer 唯讀；真 BAT timeout、busy、failover、reload 和 tab revision race 仍未實機驗證。**尚未通過啟用契約的 live Goose 必須保持停用。** Mock coverage 不代替目標環境的相容性檢查；部署決定由操作者依私有環境證據作出。

## Release boundary

Reviewer 只以**最後一則 agent 訊息**中的 JSON verdict 決定：`{"verdict":"pass|reject","candidate_commit","tree_hash","findings":[]}`。缺少、格式無效、同一訊息內互相矛盾（含舊式 `REVIEW: PASS`／`REVIEW: REJECT` 文字與 JSON 不一致）、commit/tree 與 review candidate 不符、訊息被讀取截斷、或 PASS 但含 high／critical finding（含未標記的 `High:` 行），一律視為 reject，走有上限的 rework（`review_verdict_rejected` 事件記原因），超過 recipe 上限才 `needs_ted`。Lead 的 `BAT-STATUS` 也只取最後一則 agent 訊息的最後一個 marker；前面引用的 marker 不會推動狀態。Task adapter 讀取時每則訊息上限提高到 20k 字，避免尾端 marker 被預設 2k 截斷。

直接 send／answer／interrupt／permissions 的共用 gate 讀既有 owner 的 `task-service.json` 與 journal，然後轉交**同一個** coordinator。pointer／DB／row／lease 不可用時 `TASK_OWNER_UNAVAILABLE`，registry／task／預留 start／branch ownership 不一致時拒絕，不能降級為 standalone。`paused`、`verifying`、未對帳命令與不允許的 task state 都阻擋低階操作；queue／force／approve-pending／deferred raise／relay 不會插隊。send、client-resume、answer、interrupt、兩個 Codex permission channels 都在 frame 前重查 control_version。只有原 journal command 綁定的內部 send／受信 verification，以及該 pause 版本的 abort，能使用 coordinator 內部權限。

## Verification lifecycle

受信測試的啟動、輸出讀取與程序結束共用**一個 deadline**。POSIX 命令在自己的 session／process group 執行；逾時或取消時整個 group 被 SIGKILL，確認沒有非 zombie 成員才算結束。Windows 使用 owned Job Object；終止後仍需等待預先綁定的 process handles 確認退出，不能把 active process count 變零當成唯一證據。SSH 主機上以 `setsid -w` 執行並把 pgid 寫入遠端 `$HOME/.batc-verify-<marker>.pid`；逾時先殺本地 ssh，再以另一條 ssh 對該 pgid `pkill -KILL -g` 並輪詢直到空，輸出 `gone` 才算確認。無法確認即 `VerificationProcessStuck`，task 進 `needs_ted`（fail closed）。超過 2MB 的輸出保留開頭與真正的結尾。

驗證期有兩個時鐘：`progress_at`（候選 commit/tree 變動、lead 或 reviewer 仍在 streaming、測試結果寫入）延長等待，上限為 recipe 的閒置預算（900/1800/3600 秒）；`verifying_started_at` 從進入新一輪 verifying 起算，絕對上限為閒置預算的三倍。Reviewer 啟動（經 dispatching）與 uncertain 回復不會重置；`updated_at` 心跳不再延長驗證。操作者 resume 會重置兩個時鐘。

受信測試失敗不再直接 `needs_ted`（stuck handler）：以 log 尾端做決定性分類。缺依賴時，每個候選 commit 只跑一次 repo 追蹤中的 lockfile 安裝（pnpm／yarn／npm ci／uv sync／cargo fetch），工作樹須保持乾淨，再重測；安裝後仍缺即視為程式問題。程式／測試失敗時把遮蔽後的輸出尾端（≤2500 字）送回 lead 做有上限的 rework（`verification_failures`；small 1 次、其他 2 次），再驗證新 commit；超過上限才 `needs_ted`。逾時、權限、登入、網路、磁碟等環境問題直接 `needs_ted`。


## 執行限制證據（A10）

[confinement](confinement.md) 在既有 start command payload／registry 保存 options 與 creation snapshot；`work_status.session_confinement` 分開顯示 creation 與 current verification。Lead、reviewer、warm、resume／recovery 不改模型、引擎、recipe 或 verifier 命令。`allow_all` 仍用原選項，level=none＋task_recipe_compatibility；host 新增 confined 時 Task Service 沿用原 default 行為並列 gap。Claude acceptEdits 沒有 path check，cwd 不構成保護；實際帳號與檔案系統限制需另行驗證，不能把設定選項當成隔離證據。


## Operations 與唯一 owner

[統一操作規格](operations-unification.md) Part A 已把 task.submit／pause／resume／mark_stage、task_send（session.send 的 task target）、task.verify、task.request_ted、task.command.reconcile 包成 OperationService actions。HTTP、原 MCP／RPC 和 CLI task-reconcile 共用原 Journal／TaskCoordinator；原結果增加 operation_id／operation_status。新增 scope 對照與拒絕碼見 [api-v1.md](api-v1.md)。有 key 時依驗證 actor 去重；無 key 的舊 controls 每次是獨立要求。Goose step_id 仍可重試。一般 session runtime actions 即使走 legacy service，也先提交原 task command，未知結果不重送。

`OpContext.effect` 以同一 journal transaction 保存原 task effect 與 operation_steps receipt。restart 讀到 receipt 就重用結果；只剩 started 意圖表示 effect transaction 沒提交，可安全重做本機 journal method。pause 的 local effect（含操作者 action 去重）先提交，abort_current 為獨立 intent／readback step，resume 後舊版本的 abort 無法送出。reconcile 的一次性 capability 消耗、原 command resolution、新 next_prompt command 與 receipt 同交易提交；跟進 prompt 仍走原 _send，未知結果只回查。

task send 在等待 session lock／派送準備時被 pause 擋住，operation／refusal step 為 failed、code 為 `TASK_PAUSED`，不是成功或 uncertain；command／frame 尚未建立時也不改 pause 的 task snapshot。成功必須有該 operation 的 send command 回執與 accepted／settled 狀態；其他提早回傳明確拒絕。相同 key 重讀拒絕，resume 後新的派送使用新 key。

owner lock 固定在 registry/state directory 的 `task-daemon.lock`，不同 --db 仍爭同一個平台 owner lock。TaskDaemon 在取得 owner 後才初始化 Journal、0600 admin token、providers、listener 與 worker。第二個 daemon 回 OWNER_CONFLICT 與既有 pointer metadata，不修改候選 journal／token／pointer。heartbeat 是觀測，不能用過期時間奪取 live lock；OS 釋放後才可重啟並沿用 journal。升級需先停舊版（舊版鎖在 DB 父目錄），不得混跑。

Owner pointer 在取得 lease 時公布 pid、owner_id、journal 與 lease 路徑；只有 listener 綁定成功後才加入實際 endpoint。只嵌入 Journal 的 owner 沒有 endpoint。缺少 endpoint 表示 owner 尚未就緒：控制轉交回 `TASK_OWNER_UNAVAILABLE`，managed launcher 在原期限內重試，不能退回預設 port。Managed launcher 及安裝測試 probe 在送 bearer 前先驗證 installation proof；正常關閉先撤下 endpoint，再關閉 listener 並釋放 lease，保留其餘 pointer metadata。程序被強制終止仍可能留下舊 metadata，不能把檔案本身當成存活或身分證據。

`localhost` 綁定會公布所選 socket 的實際 loopback address，而非字面上的 `localhost`。Browser origin 與 managed identity proof 共用明確的未就緒拒絕；沒有 endpoint 時回 `TASK_OWNER_UNAVAILABLE`。Shutdown 保留 lease 與 journal，先結束已接受而尚未完成的 HTTP requests 並關閉連線，再停止 workers、關閉 journal 和釋放 lease；未完成的 request 得到連線結束，不能當成操作成功或自動重送。這個順序不依賴舊 Python 的 `Server.wait_closed()` 是否等待 clients。

Rollback 後，既有 managed reader 已會拒絕缺少 endpoint 並重試；舊版控制轉交 reader 會把缺值送進其預設網址 fallback，因此不能保證舊 reader 的行為。本變更不新增舊版使用政策；重新啟動的舊套件也會沿用它自己的 pointer writer。

Part A 沒有 schema migration、業務資料搬移或歷史 operations 回填。僅 local-admin work_submit 相容入口可按原 payload hash 連到既有 task；其 bridge receipt 與 operation intent 同交易提交，crash 不會造成重啟錯建 task。新提交使用 operation identity 作 task key，其他 actor 不認領歷史 key。

## 相容性與外部邊界

現行各入口的 operations 覆蓋以[統一操作規格](operations-unification.md)、[API v1](api-v1.md)及[實作狀態](../product/implementation-status.md)為準；本文件保留 task-service 的協定與安全界線。BAT GUI 的外部派送與沒有明確 turn 證據的 Codex 回覆仍不能由 connector 保證歸因。Mock coverage 不代表 live BAT／Goose 的完整實機驗收。
