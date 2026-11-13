# BAT 不改版時的任務協調架構評估

> 2026-11-13：產品負責人決定在本產品自己的 BAT 相容分支實作主機端合約，見[單一產品方向](../product/suite-direction-2026-11-13.md)。本文仍是 Connector 面對未修改的上游 BAT 主機時的保證與限制。

本文說明連接器在不修改 BAT 的前提下能提供哪些保證，以及哪些限制必須由介面明確呈現。這是通用架構評估，不是任何使用者的部署清單、操作紀錄或實機驗收報告。現行實作狀態見[產品實作狀態](../product/implementation-status.md)；任務協定見[任務服務](task-service.md)。

## 我的判斷

自動化需要**持久任務服務、任務級 MCP、可查證據的 Dashboard／CLI，以及有明確生命週期的執行器**。聊天整合負責接收指示和回報事件，確定性的等待、去重、取消與對帳由服務處理。

如果自動 session 沒有註冊 BAT GUI 頁籤，Dashboard 就必須提供對話、進度、卡點、worktree／commit、測試證據及日誌入口。不能因 BAT 有圖形介面，就假設任何自動 session 都可從那裡觀察或接手。直接 CLI runner、Goose 或其他執行器可作比較方案；評估提案不表示它們已交付或可直接替換 BAT。

## 現況與 BAT 的設計邊界

BAT [README](https://github.com/tony1223/better-agent-terminal/blob/5a61d43/README.md)描述多工作區終端與內建 Claude／Codex 面板。遠端 WebSocket API 的單位是 session、workspace 和事件，不是跨聊天或服務重啟的持久任務。協定依本庫[遠端協定筆記](../PROTOCOL.md)及相容版本的原始碼判定。

`claude:get-session-meta/state` 可供輪詢；串流事件不等於持久收據。`workspace:save` 覆寫整份 workspace 文件，連接器的 revision recheck 無法代替主機端原子 append。**不改 BAT 時，不能同時保證自動註冊頁籤與 GUI 編輯絕不互相覆蓋。** `orchestrate_register_tabs=false` 的 session 不會因此自動出現在 GUI。

[registry.py](../../src/bat_agent_connector/registry.py) 的鎖保護共用狀態目錄中的 connector 記錄，不控制其他主機上獨立的狀態副本，也不阻止操作者直接在 BAT GUI 或檔案系統寫入。跨入口自動操作必須交給同一 owner；網路連線、BAT token 與 task API 各有自己的權限邊界。

## 目標、介面與可行性

下表的「可」指可在 connector／協調器實作，不是功能完成或外部相容性的保證。

| 目標／介面 | 不改 BAT 的做法與界線 |
| --- | --- |
| 聊天或 CLI 提交 | 保存原始文字與來源參照；外部解讀分欄保存，不改寫權威要求。收到新聊天訊息不應取消已接受的任務 |
| 任務級 MCP | 提交回穩定 task ID；狀態、事件、補充要求、暫停與結果都查同一帳本。既有低階工具保留相容性，但不得建立第二個自動 writer |
| 持久任務服務 | 交易保存 task、command、owner、事件、候選版與驗證；不讓多個排程器各自以本機 registry 當唯一真相 |
| 命令去重與恢復 | 派送前保存 intent／idempotency key；未知結果只對帳，不自動重送。缺主機端命令收據時不能承諾 exactly once |
| Dashboard | 顯示持久任務、對話、工作與成果關係及證據；unknown、stale 與 unavailable 不顯示為成功 |
| 定時監看或外部通知 | 從已提交事件讀取，以 task/event/destination 去重；發文成功才保存游標。排程頻率由使用者設定 |
| 代理內層循環 | 明確給定工作範圍、測試及停止條件；確定性續推與有限重試由服務約束 |
| Fan-out 與 worktree | 子任務分別入帳並有明確 owner；檔案、測試埠、資料庫與暫存各自隔離。`git worktree lock` 不提供寫入授權 |
| BAT Claude／Codex | 沿用現有協定，但分別驗證訊息歸因、排隊與取消語意，不假設兩種 runtime 行為相同 |
| 直接 CLI runner | 可評估程序、日誌、退出碼與取消合約；認證、品質、額度與重啟恢復必須獨立驗證 |
| 配額路由與 failover | 僅使用可驗證的來源；原子預留 successor，舊新 writer 不得同時改工作樹。未知送出結果不可因更換供應商而重播 |
| 預算與期限 | 可信設定限制時間、回合和恢復次數；缺少成本或訂閱資料時明示不可用，不推算虛構額度 |
| 權限與人工接手 | owner／`control_version` 約束經服務的命令；先停派、確認目前 writer 停止、保存候選狀態再交接。BAT GUI 不受此鎖強制約束 |
| 驗證、採用及清理 | 區分回合完成、代理宣稱、可信候選及合併准許；候選變更即使舊證據失效。保留未合併 branch 與未提交工作 |
| Goose／ACP／MCP | MCP 是工具介面，ACP 是 agent session 合約；不能直接互換。可選 adapter 必須處理 prompt、事件、取消、權限及 resume |
| 主機維護 | 版本、磁碟、登入與 skills 健康屬獨立運維範圍；記錄實際版本與 digest，不能把派送成功當成安裝成功 |
| 跨主機存取 | 私有設定保存 host/profile 與 token 參照；服務預設 loopback。遠端存取需身分、scope、傳輸保護及稽核 |

通用資料流為：`操作者／整合客戶端 → 任務服務 → 已設定的執行器 → 可信驗證器`。Dashboard、CLI 和事件讀取端使用同一權威帳本。可選執行器不應透過高階提交工具反覆建立自己的重複任務。

## 日誌能證明什麼、還不能證明什麼

以下是故障分類方法，不對應私人事故或特定部署的時間線。

| 故障訊號 | 可以下的結論 | 還需查明 |
| --- | --- | --- |
| 等待呼叫耗時、回覆很短 | 同步監看占住呼叫者；短回覆本身不表示沒有進度 | status、游標、turn phase、是否重複等待同一命令 |
| 新訊息中斷等待 | 某次監看被取消 | 任務是否仍存在、是否恢復讀取、是否錯誤重送 prompt |
| 外部 thread 找不到 session | 整合的來源映射可能缺失 | 持久 source/thread → task → session 關係及重啟狀態 |
| metadata 或握手逾時 | 主機或傳輸不可用 | 主機負載、連線與服務日誌；不能只判定模型遺忘 |
| 舊輸出被重貼 | 回覆歸因或事件投遞需要檢查 | 來源 command、已送 event、游標持久性及去重收據 |
| BAT 接受但回覆遺失 | 派送結果可能未知 | 精確 session、送出前 fence、匹配內容及主機 message ID |

追蹤應使用來源參照、task ID、command ID、host/session ID 與訊息 ID。保存送出、接受、輪詢、斷線、對帳及外部投遞階段；不要記密鑰、完整私人 prompt 或原始供應商錯誤。私有事故調查可串接各層時間線，公開文件只保留可重現的故障條件與防護契約。

## BAT 不變時的可行控制規則

### 一個持久任務帳本，整合客戶端只做指揮

單一寫入協調器先用 SQLite WAL 與 OS owner lock 管理任務。每項任務固定 task ID、來源參照、原始文字、project/worktree、執行器及 owner。所有控制入口都使用該協調器；增加實例數量時不能只複製本機 registry。

網路操作前先在交易中保存 command ID、idempotency key、owner 與 control version。相同邏輯要求重讀原收據；相同 key 不同內容拒絕。事件讀取端獨立保存已投遞游標；失敗恢復不應創造第二次執行或第二份通知。

### Codex 的命令歸因與人工交接

[service.py](../../src/bat_agent_connector/service.py) 區分訊息 ID 與時間游標。BAT [相容版本的 Codex 路由](https://github.com/tony1223/better-agent-terminal/blob/5a61d43/src-tauri/src/commands/claude.rs)不能視為通用主機端 command receipt。時間戳、回合結束或後續 assistant 輸出都不能單獨證明某個 prompt 已執行。

1. 保存送前 session 身分、串流狀態、最後訊息及 turn 資訊；只有同一 writer 的精確觀察才能支持歸因。
2. 保存實際 prompt bytes 的 SHA-256；比對 fence 後的新 user echo。相同文字可能重複出現，故還要唯一匹配與主機 message ID。
3. 若提供 rollout 讀取，需驗證 session 身分、格式、輪替和存取範圍；不能把任意檔案路徑當可信證據。
4. HEAD、diff、代理文字能證明工作發生，不能單獨證明觸發工作的命令。證據不足維持 `uncertain`。

同一自動 session/worktree 只容一個 writer。人工接手前先停止新派送、提高 control version、確認 writer 已停止，再保存 HEAD、dirty tree 和最後訊息。這是工作交接；不保證同一個自動 session 可在 BAT GUI 無競態接手。交還權限後必須重新對帳，不自行恢復舊 prompt。

### 候選版本、測試和清理

把 `TURN_FINISHED`、代理宣稱、`VERIFIED_CANDIDATE` 和准許合併分開。[verification.py](../../src/bat_agent_connector/verification.py) 與任務 verifier 要求候選 commit/tree、可信命令、exit code、環境及日誌證據；修改候選後舊證據失效。`session_record_verification` 的呼叫者聲明不得偽裝成服務實際執行的測試。Jev 判斷或 `MILESTONE` 文字不能代替執行證據。

清理要保留未合併 commit、dirty worktree 和不明 ownership。合併、push、部署與公開發布分別遵守已授權的範圍，不把任務完成視為所有外部副作用的許可。

## 三種架構的取捨

| 路線 | 優點 | 條件與限制 |
| --- | --- | --- |
| 協調器＋BAT 原生執行 | 沿用 BAT session、既有 connector 與 worktree 支援 | 必須接受事件／命令歸因和 workspace 存檔邊界；GUI 可見性另行核對 |
| 協調器＋直接 CLI runner | 可建立程序、輸出、退出碼、取消及持久日誌契約 | 須實作可信 runner，驗證認證、額度、品質與恢復；不能假設 CLI 等同 BAT runtime |
| Goose 或其他可選 harness | 可提供局部規劃與受限工具流程 | 額外的 provider、resume 和權限契約；harness 的 session 鎖不會鎖住 BAT／Git |

[研究 Part 1／2](../research/2026-09-27-review.md)及[下一代設計](next-gen-connector.md)可供比較。Buzz 的一次性任務、期限、取消與結構化結果可借鑑；完整平台不是必要依賴。BAT 上游的原子 workspace append、Codex command ID 或 ownership gate 是可能改善點，不是 connector 已具備的能力。

## 有順序的建置與驗證計畫

先在合成或隔離環境驗證單一任務的提交、持久進度與恢復，再讓各控制入口共用帳本。新增執行器時，以相同任務比較可信交付、重工、人工介入、恢復與可觀察使用量；只有證據支持時才改預設路徑。規模擴充不應先於 ownership、停止與對帳契約。

驗證案例應包含：精確 Claude echo、排隊後舊回合輸出、Codex 重複 prompt、並行 failover、GUI 與頁籤註冊競爭、接受後立即斷線、監看取消、服務重啟、通知去重、取消後子程序仍存活，以及測試成功後候選變更。這些是驗證要求，不表示已在任何使用者的真實 Fleet 通過。

預期保證是持久可見、未知結果不亂重送、可停止並對帳。在沒有相應 BAT 主機端合約時，不承諾所有 Codex 命令恰好執行一次或完全無競態的 GUI 接手。
