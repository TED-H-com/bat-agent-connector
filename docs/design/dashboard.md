# Dashboard

更新：2026-10-08。既有 W09 畫面依 [Tauri v2 範圍](../product/realignment-v2.md) 納入 R02/R04 共用 frontend；介面參考 Project Hub，不提供 Hub 匯入器。資料與動作全部來自 [/api/v1](api-v1.md)；合併與部署的語意見 [delivery.md](delivery.md)，原生殼與驗收限制見 [desktop.md](desktop.md)。

## 形式

Web 與 Tauri 都是持續支援的入口；共用畫面、中央資料與操作，平台專屬能力按需提供。
同時開啟、獨立生命週期、草稿與版本對齊的決策見 [共用前端設計](shared-frontend.md)。

`desktop/src` 是 browser 與 Tauri 唯一共用 UI source，保留原有 JavaScript、CSS、DOM helpers 與表單；transport/state helpers 使用 TypeScript。`cd desktop && npm ci && npm run build:all` 以 Vite 產生 packaged desktop assets 與 Python browser assets。Tauri 正式執行不需要 Vite server。

`batc serve` 仍在同一個 loopback 埠提供 `/dashboard/`（`http://127.0.0.1:18796/dashboard/`，`/` 會轉到這裡）。套件的 `dashboard/` 仍只提供 `index.html`、`app.js`、`i18n.js`、`app.css` 四檔；它們由共用 source 產生，禁止手改。翻譯已 bundle 入 app.js，i18n.js 保留相容 stub。其他路徑一律 404。JavaScript/Cargo lockfiles 固定依賴，CI 重建並檢查 generated drift。

靜態檔本身不含資料，所以不需要 token；頁面發出的每個 `/api/v1` 請求都要 bearer token。遠端使用同樣經 SSH forward（`ssh -L 18796:127.0.0.1:18796 host`），daemon 不對外綁定。

## 安全

- 回應帶 `Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; …; frame-ancestors 'none'`、`X-Frame-Options: DENY`、`nosniff`、`Referrer-Policy: no-referrer`。頁面沒有 inline script 或 inline style，CSP 不需要放寬。
- Session 標題、訊息、PR 標題等外部文字一律以 `textContent` 寫入（`desktop/src/app.js` 的 `h()`），不經 HTML 解析。
- Host 必須是 loopback，與 `/api/v1` 同一條規則（擋 DNS rebinding）。
- Browser token 預設只存在 `sessionStorage`；勾選「在這台電腦記住」才寫入 `localStorage`。API token 只走 bearer；受管理瀏覽器工作階段另走 HttpOnly cookie，保留 origin、fetch-metadata 與 CSRF 檢查。工作階段憑證不能當作 bearer。
- 記住的 bearer 收到 401，或 Dashboard 必要讀取（capabilities、bootstrap、events）收到 403 時，顯示拒絕狀態並暫停自動送出。其他 403 保留個別資源／動作的拒絕訊息。拒絕記錄與權杖放在同一種 storage，依 endpoint 與具領域分隔的摘要識別；明確重試、斷線或成功換用新權杖才清除，草稿與操作鍵不變。相同 origin 的分頁會同步此記錄。
- Tauri 只載入打包的 frontend，透過限縮 native command 連受信配置中的中央服務。Token 留在 Rust，不進 WebView storage；Windows 使用原生憑證對話框與 Credential Manager enrollment，啟動環境變數仍是相容輸入。Linux 目前使用 native-memory adapter，不能當成跨平台 OS vault 驗收。中央 actor／API／contract mismatch 拒絕連線，原生 HTTP 不跟隨 redirect。GitHub HTTPS links 由受限 native action 開系統瀏覽器。完整邊界見 [desktop.md](desktop.md)。

## 畫面

| 畫面 | 內容 | 讀取 | 動作 |
|---|---|---|---|
| 待處理 | 「需要你處理」分為待回覆／權限、完成確認、需處理操作、主機連線；「執行中操作」獨立列尚未結束的操作；「未讀工作更新」列此身分尚未標記已讀的工作版本 | `work-items?pending=true`／`unread=true`、`sessions?attention=true`、`operations?status=…`、`hosts` | 各組分頁；工作詳情明確標記此版本已讀，見 [閱讀合約](work-item-reading.md) |
| 專案 | 專案樹與各專案的進度 | `projects` | 新增、改名、子專案、上下移、固定、封存與復原 |
| 專案詳情 | 說明、repositories、子專案、工作項目樹（狀態、步驟進度、等你決定） | `projects/{id}` | 新增項目、子項目、分支；上下移、固定、封存（連同子項目）與復原；編輯專案 |
| 工作項目 | 目標、需求原文、驗收、步驟、完成狀態、連結（附現況）、子項目與分支、紀錄 | `work-items/{id}` | 編輯、勾步驟、改狀態、確認完成或退回、連結與移除、從已連結的 checkpoint 派工（[work-items.md](work-items.md)） |
| 工作階段 | 主機／工作區導覽、已載入範圍搜尋；保留來源、存取方式、活動與過往紀錄 | `sessions`（keyset 分頁） | — |
| Session | 基本資料、來源、相關工作項目、最近訊息、checkpoint | `sessions/{host}/{sid}`、`…/messages`、`checkpoints` | Managed：送出（session 執行中預設排隊）、中斷、回答問題或權限。人工建立：唯讀說明。任何 session：記下目前版本，從版本開始 agent 工作（[checkpoints.md](checkpoints.md)） |
| 成果與 GitHub | 環境的選定／觀測／最後驗證版本、游標分頁部署歷史；PR 的 head／base、checks、可合併狀態 | `deployment-environments`、`deployment-environments/history`、`deployments/{id}`、`repositories/{o}/{r}/pulls/{n}` | 以 `deployments/preview` 固定環境代數與 recipe digest 後回退或重試固定版本；合併、合併並部署、部署已合併版本 |
| 操作紀錄／操作 | 狀態、原因、步驟、`external_refs`、結果 | `operations`、`operations/{id}` | 取消；`needs_attention` 時重新執行（resume）；合併並部署失敗時以 `merged_sha` 重試部署 |
| 連線 | Browser 輸入 API token；Tauri 顯示配置位置、原生憑證狀態；兩者顯示 actor 與 scopes | `capabilities`、`bootstrap` | 連線、斷線 |

Token 缺少某個 scope（`start`、`integrate`、`manage`、`approve`）時，對應的按鈕停用並說明要用哪個 scope 重新發 token。這只是提示：授權只在後端判斷，送出的請求仍可能回 403。

## 工作階段列表的資訊層級

參考 Project Hub `a277d2ed5ce439c248fc32fa8ff9fa4124a06027` 的緊湊側欄與主內容層級
（`hub/public/app.js`、`app.css`、`hierarchy.js` 與公開截圖），沿用本產品的元件、色彩與字體；
此整理未複製上游程式碼。BAT 仍是 agent 的執行介面，中央資源與操作合約不變。

- 頂部保留中央的主機與存取方式篩選；搜尋僅比對已載入列的標題、完整 session ID、主機、工作區、
  workspace ID、agent、model 與 branch。側欄及列數都明示「已載入」，不是主機或專案總數。
- 桌面使用 220px 工作區導覽與內容欄；900px 以下改為可展開的工作區選擇，避免擠壓內容。
  分組使用 `(host, workspace_id)`；ID 未記錄時才按實際 workspace 標籤分組，並標出未記錄 ID。
  標籤也未知時為該主機的未記錄範圍，不推測為同一 repository 或 project。相同名稱的不同 ID／主機不合併。
- 群組標頭顯示工作區與主機；同主機有重複名稱或缺少名稱時才顯示 ID，完整 ID 保留於展開證據。每列以標題、待回覆／活動狀態為第一層，來源、存取方式、agent／branch
  為第二層。狀態與紀錄按需展開完整 ID、時間、隔離與分項狀態證據；session 連結仍開啟原有訊息、歷程與操作。
  主機連結前往既有 discovery，專案連結前往既有專案頁，不建立推測的專案關係。
- `fields_stale` 或活動證據過期時不宣稱仍在輸出；舊 pending 保留提醒但明示尚未重新確認。
  未輸出、未載入、最近掃描未見均不代表結束或工作完成；只有中央明示 `lifecycle=ended` 才顯示結束。
  手動來源及唯讀界線保留，沒有新增封存、刪除、掃描或背景寫入。
- 沿用 15px 本文字型、14px 群組標題、12–13px 次要證據，以及既有 `--panel`／`--line`／`--chip`／
  `--muted`／狀態色。行距與 padding 保持緊湊，行動版表單與展開控制至少 44px；不引入新配色。
- 維持 `order=id&include_gone=true` 的中央 keyset 分頁；刷新重新讀已載入頁數並以 `(host, session_id)` 去重，
  保留可見列的捲動位置與已展開證據。多頁仍非原子快照。搜尋與工作區選擇保存在既有身份分區的
  sessionStorage；換 backend／principal 不帶入。所選範圍暫時沒有資料時不自動擴大到全部。
  較舊篩選的在途回應不覆蓋新選擇，事件等待排隊的最新讀取；失敗仍不確認 event checkpoint。

## 冪等與即時更新

Session 訊息以共用安全 DOM renderer 顯示程式碼區塊與表格，提供整段原文／程式碼複製、
複製失敗時的手動選取，以及不打斷向上閱讀的更新。沿用最近 30 則訊息範圍；閱讀位置離開
已載入視窗時明示限制。「回到最新訊息」恢復跟隨，閱讀位置不當作中央 ACK 或跨裝置已讀狀態。

- 每個草稿（例如「對這個 session 送字」「以這個 head 合併這個 PR」）在身份分區的 `localStorage` 有一把 `Idempotency-Key`；保存原 operation ID 以便查回。回應遺失後重開再重按仍使用相同 key。操作結束或請求被拒（4xx，409 除外）後才換新鑰匙。舊版無身份分區的資料保留原處，不自動指派給另一個 backend／actor；不明舊操作須先對帳。
- 專案與工作項目的修改帶上頁面讀到的版本、指紋、兄弟順序或固定狀態；別人先改了就回 409，畫面說明並重新載入，不覆蓋對方。編輯欄的草稿留在重新載入後的表單裡。說明顯示在頁面上方，不會被重畫掉。
- 既有 projects／work-items／delivery 的 live render 會避開開啟中的編輯欄或正在輸入的欄位，關閉或離開後再補上。新增步驟、連結的輸入框在相應 view 重畫時保留內容；這不代表所有表單都已持久化。Session 送字草稿另存於身份分區，重開仍可回復。
- 從 checkpoint 派工的表單送出後就不能再送：結果留在表單裡，關閉後頁面列出這次執行。
- Browser 與 Tauri 共用有界的 `GET /api/v1/events` polling，追平後每秒讀一次；原生 Rust 不另建 event log。中央 SSE 仍可供其他 clients 使用，但這個版本沒有 native streaming subscription。
- 新中央提供 [bootstrap／checkpoint 合約](dashboard-sync.md)。先取得 cursor/token，再讀需要的畫面資料，畫面 mounted 後續讀事件。Snapshot 與多頁讀取不是原子快照（`atomic:false`）；資料以 stable IDs 及 API 讀回為準。每頁等待所有 async/debounced view refresh 完成後才一起保存 `next_cursor` 與新 checkpoint token，不能先跳到 head。讀回失敗保留原 cursor/token，暫停操作並重讀同一頁；開啟的編輯表單延後 refresh 與 acknowledgment，保留草稿；單純等待不會禁止有版本前置條件的表單儲存，實際讀取失敗或 continuity reset 才暫停操作。此保障限於已訂閱的畫面更新；pending controls、linked resource invalidations 與 history/relations 畫面仍由 R04 完成。Cache／草稿／操作 keys 依 endpoint、server_id、principal_id、actor 分區。
- `EVENT_CURSOR_RESET` 重讀 bootstrap；同身份已開表單保留至使用者離開再刷新，其間不接受 mutation。切換 backend／principal 立即換分區，不把舊表單內容寫入新身份。Poll 失敗也會暫停 operation POST，保留草稿和原 key。舊中央無 bootstrap 時仍可讀目前畫面，但沒有 retention／continuity 保證。

## 附件與草稿（Part A；計畫 §13，B04）

工作項目編輯與 checkpoint 接續表單提供檔案 picker，選擇即 upload。原 `batc.draft.<scope>` localStorage 草稿擴充為 text／uploaded refs／尚未成功檔名／提交 operation；沒有 server draft 或 IndexedDB。File 只在記憶體，失敗可 Retry；重新載入列檔名並明確顯示「請重新選擇這個檔案。瀏覽器無法在重新載入後開啟本機檔案。」（英文同義）。成功 upload 不清草稿，continue／update succeeded 才清提交 snapshot，期間新編輯留下。

Dashboard 開接續表單時讀 source HEAD 並送 expected_source_head_sha；SOURCE_MOVED 在原 operation 頁明確確認原 commit／attachments，續同 parent。操作页展示各 materialization 的 pending／transferring／uncertain／verified／blocked evidence。prompt 只有 worktree-relative paths 與精確 ref／digest，無 client-local path；download attachment 無 preview。完整合約見 [artifacts.md](artifacts.md)。

## 語言

依瀏覽器語言選 zh-TW 或 en，預設 zh-TW。字串在 `desktop/src/i18n.js`；程式與 CSS 只用 API 的機器值（`status`、`api_access` 等），不比對顯示文字。

## 整理與永久歷史（Part A）

見 [cleanup.md](cleanup.md)：純讀 preview、signed token、逐項 operations／retained refs／tombstones，原 ID
永久可查。Dashboard #/cleanup 與 work item 的整理入口，兩語預覽／逐項回執／歷史搜尋／實際 retained list；
Part A 無 restore 按鈕。Task-owned 資源由 Task Service 整理，本輪列 TASK_OWNED；原 terminal cleanup 不變。
Restore、reviewed task leftovers／coordinator 准入與 TaskDaemon 歷史投影在 Part B。Clone／area 與 pins 留存。
## 執行限制證據（A10）

Session card 依 creation snapshot 顯示 level；OS sandbox 最多 options_confirmed，badge 明示尚未實機驗證。Detail 可展開建立選項、evidence／gap 及獨立 current verification；stale／mismatch 不改原 level。Checkpoint、work item 接續與 repair forms 隨所選 agent 及中央 `host_account.start_effect` 顯示限制：`recheck` 要求 start 時重新查核；`fallback_default` 的 Claude 使用 plain default；`refused` 才顯示阻擋與原因；`verified` 顯示已驗證 account 邊界。Codex 顯示 network／writable roots 不可設定與測試相容風險。所有字串有 en／zh-TW。Cwd 本身不稱為保護；A10 尚待 W12，見 [confinement](confinement.md)。

## 尚未涵蓋

- Worktree、diff、檔案瀏覽。Fleet Kit 的連線選擇已由 Windows [PS adapter](desktop-fleet.md) 與桌面設定頁提供；Rust parity 與實機驗收仍待完成。
- 推播通知。
- History／relations／discovery 與已開 session 的 pending 刷新、linked operation／parent project invalidation
  已有共用 UI／fixture；同候選的實機驗收仍待完成，見 [implementation status](../product/implementation-status.md)。
- Windows 真實 tray、cross-session ownership、native files、Fleet parity、OS credential enrollment 與 signed updates 未因桌面殼可編譯而視為驗收完成。

## 測試

`tests/test_api_v1.py::test_dashboard_serves_only_its_static_files_with_strict_headers` 檢查白名單、轉址、HEAD、方法與 Host 限制及安全標頭。`desktop` workflow 執行共用 frontend build、generated drift、TypeScript event helpers、Rust bridge 與 Chromium Playwright fixture tests；後者涵蓋 390/768/1440 寬度、原生 transport mock、遺失回應重試、reset 草稿與離線拒絕寫入。Linux genuine packaged WebKit fixture 另驗證啟動讀取、關窗隱藏和第二次啟動恢復。這些都是 fixture／native smoke 證據，不代替 Windows 安裝或真實中央與 BAT 的完整驗收。

## Task 控制與操作紀錄分頁

`#/task/{id}` 以既有 panel、chip、actions 與展開證據呈現原 Task Service 執行；保留 history、relations、
原 session、成果與 reviewed cleanup 入口。控制區提供「暫停派工／恢復派工」，暫停的「同時中斷目前這一輪」
預設不勾選。工作項目完成／封存與 task 控制沒有連動，也不新增 Task Service 派工或 failover 策略。

- 必須成功讀到同一 task ID、有效 `control_version`、paused 狀態，以及正向 action capability 和 operate
  scope 才能準備控制。已結束的 task 不能新建控制。Authenticated operation actor 是身份來源；不捏造 操作者
  source message 或 task capability。中央 coordinator／最後 frame gates 仍是唯一權威。
- 草稿依 backend/principal namespace 與 task ID 分區，送出前保存完整 action/target/params/preconditions/key。
  Replay 永遠保留原 control version；accepted ID 只讀回，不因新的 paused 狀態再送一次。First receipt 必須
  同 actor、key 及完整 envelope。Unknown／needs_attention 保留原操作與逐步收據連結。
- `CONTROL_VERSION_CONFLICT` 是 canonical key replay 之後、INSERT 之前的拒絕，允許使用者明確準備新控制；
  仍先保留舊 key，不自動改用較新版本。一般 auth／4xx、transport error 並不是未受理證明。儲存失敗時不送出。
- Event refresh 先等提交取得 identity 並查回原 operation，再重新讀 task；兄弟讀取全部 settle 才可確認 cursor。
  任一讀取失敗停用 task 控制並保留原草稿，持續訂閱與重讀；換帳號／離開頁面不能採用舊回應。
- 操作列表使用中央 `next_before`，載入較早頁及事件刷新都依序讀取。事件等待在途分頁後再重讀已載入頁數，
  以 operation ID 去重並保留可見列捲動位置；失敗保留原列表。這是已載入窗口，並非跨頁原子快照或總筆數。
  Home 的需處理／執行中區明示已載入操作筆數，連到相同 status filter 的可分頁列表。

`npm run test:task-controls` 使用本 checkout 的 generated assets、real central HTTP/journal/coordinator 與
temporary MockBat，檢查 lost reply 的原 key/version replay、暫停／恢復、明確 interrupt 的單一 BAT frame、
受理前版本 race、逐步收據及超過 100 筆操作的分頁。Browser/native IPC mock tests 另涵蓋權限、identity、
事件／分頁競爭、讀取失敗及雙語 390/768/1440。沒有 live host、真實 native 安裝或全產品驗收的宣稱。

## Connector 工作階段標籤（A03）

沿用現有 panel、chip、actions 和 details，不改工作區導覽或 BAT 標題。詳情提供標籤摘要與
預設收起的「編輯標籤」；每行一個，最多 8 個、每個 40 個字。列表最多顯示兩個，其餘用
數量提示；既有「已載入」搜尋也比對標籤，不宣稱搜尋所有主機或未載入頁面。

標籤是中央自己的 metadata。具正向 `session.labels.set` capability 與 `manage` scope 才能
儲存；人工、unknown、已不在主機上的已知 session 也可以整理，無須 host writes。
讀取不到 metadata 不視為空清單，停用儲存並保留草稿。完整 session ID、Connector
metadata 版本、原操作 key 與回執固定；版本衝突須明確檢視目前標籤後再準備變更，不能
自動換 key 或覆寫草稿。回覆遺失重送原 envelope，accepted 後只查原 operation。
事件等待原送出完成與最新 metadata／回執讀取，失敗不確認 cursor。身份隔離沿用
backend／principal namespace。中央資料與 atomic receipt 合約見 [session-labels.md](session-labels.md)。
