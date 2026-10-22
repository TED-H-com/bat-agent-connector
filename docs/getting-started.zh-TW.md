# 安裝與首次使用

[產品介紹](../README.zh-TW.md) · [English](getting-started.md) · **繁體中文**

## 選擇相符的安裝包

2026 年 10 月 22 日核對的最新[穩定版下載](https://github.com/TED-H-com/bat-agent-connector/releases/tag/v0.2.12)仍為 Connector **0.2.12**／desktop **0.1.8**，來源 `14c62830240148b14a4d28a9c428f7f8b5ba8bf3`。[已發布預覽版（prerelease）](https://github.com/TED-H-com/bat-agent-connector/releases/tag/v0.2.13-preview.20261022)為 Connector **0.2.13**／desktop **0.1.9**，來源 `437ee6a7143a82e32eae1d6a008203b2df072aaf`。下載所選版本的相符平台套件及其 `release-manifest.json`、`SHA256SUMS`。每版各有 wheel、sdist、deb、兩個 DMG、Windows 安裝器、來源清單與校驗碼，共八檔。CI 與安裝 fixture 不代表原生或實機驗收。

穩定版尚未包含路線喚醒、本機服務復原及首次設定／狀態變更；預覽版已包含。#399–#403 在預覽版來源之後才合併，不在這個套件內。已合併、已發布、已安裝與實機證據見[目前交付狀態](product/implementation-status.md)。下方首次啟動步驟描述穩定版 0.2.12；下載較新套件不代表已安裝或實機驗收。

| 平台 | 穩定版 0.1.8 資產 | 預覽版 0.1.9 資產 | 格式 |
| --- | --- | --- | --- |
| Windows x64 | `better-agent-dashboard_0.1.8_windows_x64.exe` | `better-agent-dashboard_0.1.9_windows_x64.exe` | NSIS `.exe` |
| Mac Apple Silicon | `better-agent-dashboard_0.1.8_macos_arm64.dmg` | `better-agent-dashboard_0.1.9_macos_arm64.dmg` | `.dmg` |
| Mac Intel | `better-agent-dashboard_0.1.8_macos_x64.dmg` | `better-agent-dashboard_0.1.9_macos_x64.dmg` | `.dmg` |
| Linux | `better-agent-dashboard_0.1.8_linux_amd64.deb` | `better-agent-dashboard_0.1.9_linux_amd64.deb` | `.deb` |

請使用發布頁的相符平台資產；另行提供的 GitHub Actions 候選 artifact 可能要求登入，也會到期。Windows 包未正式簽署，Mac 採 ad-hoc 簽署。正式簽章、Mac 公證、正式更新通道及 Linux 原生憑證持久儲存不在本次交付範圍；GitHub 發布標記不等於實機穩定性通過。

## 首次啟動：你的本機 Connector

安裝並開啟相符套件。包內含 Python runtime，會準備一套私人本機中央、資料與個人身分；這條流程不需先裝 Python／uv、輸入 actor、編輯憑證檔或複製 API token。尚未配置 BAT 主機時，Dashboard 會開啟**設定 → 連線**，並展開**準備開始工作 → 1. 連線至 BAT**。

1. 將 BAT 複製的連線網址貼入密碼欄位，在本機填好連線資料。
2. 核對**主機代號**，再按**驗證並儲存主機**。
3. 成功後按**查看工作階段**；人工工作階段維持唯讀。

**連結 GitHub 儲存庫**與**設定驗證命令**預設收合，閱讀工作階段不需要先完成。派工、Git 工作或 Task Service 驗證需要時再設定。**允許受管工作**也預設收合，連線不會自動授予這些權限。

**Windows Fleet 管理本機連線與啟動；Python Connector／Task Service 授權並派工；BAT 在選定主機執行工作。** 使用遠端 BAT 主機時，Dashboard 電腦不需安裝 BAT。Windows Fleet 設定與中央引導設定各有分工。

### 1. 連線至 BAT

開啟「設定 → 連線 → 連線至 BAT」，使用「貼上 BAT 連線網址」。在 BAT v3.2.15 的「設定 → 遠端 → 遠端存取 → 連線網址 → 複製」取得網址；無介面模式可複製啟動訊息 `connect:` 行的網址。密碼欄位會在本機填好位址、權杖與憑證指紋後立即清空，欄位下方會以勾號確認讀取的位址與縮短的憑證指紋，不會顯示權杖。按「驗證並儲存主機」才會送出。手動修改位址或指紋後，確認文字會消失。

「主機代號」須以英文字母或數字開頭，只能使用英數字、`-`、`_` 或 `.`，例如 `local`。首次設定時，回送位址會建議 `local`；這只是代號建議，不會判定 BAT 與中央在同一台電腦，也不會授予本機執行權限。

「手動輸入」保留「BAT 連線位址」、「憑證指紋（SHA-256）」、「BAT 工作區設定檔代號」與「BAT 連線權杖」，也可選取可匯入的「BAT 連線設定檔」。若無法讀取設定檔，選擇欄位旁會顯示說明。位址取自 BAT 的「連線網址」，指紋與權杖在「遠端存取」分別標示為「Certificate fingerprint (SHA-256)」與「連線 Token」。手動位址必須是含主機及連接埠的 `wss://` 位址，不含權杖或查詢參數。貼上的 `ws://` 會轉成 `wss://`，未指定連接埠時使用 9876。手動指紋請向主機擁有者核對；加密或損壞的設定檔授權需重新提供有效權杖。

常見輸入錯誤會在暫存權杖前顯示於欄位旁，權杖仍留在密碼欄位。中央明確證明未受理請求時，表單會自動解除鎖定並保留非機密草稿。暫存後的權杖會從畫面清除，這一頁可在一小時內沿用不透明參照重試；重新載入可編輯草稿後須再貼上。若回覆遺失或結果不明，原請求與識別碼會保留，請使用「查回請求」，重新載入後也一樣。單憑設定錯誤代碼不能判定請求未受理。

連線成功後，「1. 連線至 BAT」會收合，按「查看工作階段」繼續。展開這個步驟或按「修改設定」即可再次編輯；下次進入時預設收合。「設定操作」連結與狀態放在「詳細資料」中。頁面標題旁的「重新整理」會查回設定狀態，並保留目前草稿與權杖欄位。GitHub 儲存庫與驗證命令仍收合，可依需要設定。

需要派工時，展開「允許受管工作」，選擇所需的對話／開工權限與專用「受管工作目錄」。Git 同步、檢查點及成果操作使用「此電腦已設定的 SSH 別名」。SSH 帳號、金鑰、已知主機與遠端權限須已可用，程式不會猜測或產生這些授權。

SSH 別名由中央所在電腦的服務帳號解析。使用打包本機中央時，就是桌面電腦；若加入遠端中央，須在該中央主機準備 SSH 存取。瀏覽器或 Tauri client 不會將自己的本機 SSH 金鑰轉送給中央。

直接 BAT 開工若需共用 Git 中繼資料，須明確勾選「允許共用 Git 複本的獨立工作目錄」；沒有此需求就保持關閉，也不因此取得人工工作階段與獨立工作目錄的控制權。按「驗證並儲存主機」會做唯讀的連線與設定檔檢查，儲存不會派工。

### 2. 連結 GitHub 儲存庫

在「連結 GitHub 儲存庫」填入 `owner/repository` 與已配置主機，按「讀取主機工作區」，選擇實際「BAT 工作區代號」，提供「Git 遠端位址」及必要的 GitHub 權杖。依工作需求開啟成果整合、PR 合併或中繼資料更新，再按「驗證並連結儲存庫」。

儲存庫必須與主機、工作區明確綁定，不能用相似的專案名、資料夾或遠端位址代替。驗證只讀取 BAT／GitHub，不會推送、建立 PR 或合併。從已發布來源派工另需受管開工權限、專用受管工作目錄及可信 SSH 別名；部署流程仍另外配置。

### 3. 有需要時設定驗證命令

「設定驗證命令」適用於 Task Service 專案。填入精確的「Task Service 專案名稱」、「執行程式」、「參數（每行一個）」及「逾時秒數（1–3600）」。每個非空白行是一個完整參數，行內空格會保留；不解析 shell 引號、管線或展開，也不要填入憑證。

「儲存驗證命令」只保存設定，不執行命令。其他專案的命令保留，逾時秒數套用到所有已配置專案。請使用 task 實際的 project key，不會自動由 Dashboard 專案 ID／標題對應。若編輯期間中央設定改變，重新載入已存驗證設定、檢視保留草稿後再儲存。後續仍由既有 task、ownership 與執行條件決定何時驗證。

### 4. 派工與追蹤

開啟「專案」，連結已配置儲存庫，選擇綁定主機／工作區，先檢視固定來源提交，再送出指示、模型與選用附件版本。同主機可從固定本機檢查點接續；另一主機則經綁定儲存庫取得明確已發布提交。人工來源工作保持唯讀。

可調整的左側樹讓所選對話、回覆控制與成果連結留在同一畫面。發送回執記錄該次請求結果；歷史「已排入佇列」不是目前 BAT 佇列位置。模型偏好影響選項，不會取代 provider 授權。已儲存技能會固定經檢視的目錄來源，不代表自動啟用到執行中的 Agent。「建立修復工作」保留固定失敗證據，仍須先檢視目的地與來源再派工。

### 背景、瀏覽器與恢復

按「在瀏覽器開啟 Dashboard」或使用系統匣／選單列入口。程式用一次性登入票證交接，長效 API 權杖不放入網址或瀏覽器儲存。Web 與 Tauri 使用同一個中央身分和帳本；瀏覽器登出後，從桌面入口重新開啟。

勾選「登入電腦時啟動」可設定這套安裝的登入啟動。關閉網頁或 Dashboard 視窗，中央仍持續運作；明確退出桌面時，中央工作仍保留，Fleet 另依既有正常停止條件處理。重開會找回同一份安裝，新包只有在自有服務同意安全停止後才升級；忙碌／結果未知工作會阻止切換，舊包也不會降版帳本。

設定變更會等待執行中的 operations／tasks。回覆遺失時使用「查回請求」及「詳細資料」中的「設定操作」，不要另送新請求猜結果。憑證失效、ownership 不符或啟動失敗需要恢復原安裝，不是另建資料庫的理由。

<a id="pm-agent-setup"></a>

## PM 代理設定

開啟 **設定 → 代理**，檢查 Connector 主機的 PM 安裝狀態。**重新檢查**只讀取資格，
不會啟動模型，也不讀取登入檔內容。**可用**表示可進入每次執行前的檢查，不代表已驗證模型執行。
從討論進入時，也會讀取該工作空間既有的 BAT 分析設定與權限；沒有工作空間脈絡時，
只檢查本機安裝。無法使用時，請依下方執行環境前提調整。人工工作階段維持唯讀。

## 試跑一條 PM 工作流程

使用同一個已審閱 PM 候選版本的中央與 client。功能依中央回報的能力顯示，舊安裝版
可能只提供其中部分步驟。已合併、候選與剩餘範圍見 [PM 實作狀態](product/pm-v1.2-status.md)。

**PM runtime 前提：**討論分析、計畫提案與 Follow-up 分析可分別明確選擇已設定的
Linux 中央 runtime 或所選 Linux BAT 主機。Windows/macOS client 也能使用中央回報可用的
路徑。中央 runtime 的服務帳號需要 Codex CLI **0.160.0**、bubblewrap，以及
`CODEX_HOME`（通常是 `~/.codex/auth.json`）中的可讀一般登入檔。目前不導入自訂 provider
設定或環境變數中的 API key，也不支援系統受管理的 Codex 設定；不要移除管理政策來啟用。
詳見 [原生 runtime 邊界](design/pm-native-runtime.md)。
BAT 分析需要在所選 Linux BAT 主機安裝已審閱的專用 PM runtime；一般 BAT 檔案唯讀設定
不足以啟用，詳見 [BAT 分析前提](design/pm-bat-analysis.md)。能力回報只說明安裝是否適合進一步
檢查；每次執行仍會核對實際 runtime、登入與原回合完成證據。已保存的請求會保留原 adapter
和 key，回覆遺失時也沿用原請求查回。所選 adapter 不可用時不會自動改走另一條路徑。
舊中央的計畫提案與 Follow-up 保留原生分析行為。

1. 從工作空間切換器按 **新增工作空間**，在 **討論** 按 **新增討論**，使用 **想法** 模式。
   先用一般筆記寫下目標與限制，例如會員登入與忘記密碼，第一版排除推薦碼。
   按**傳送**建立討論並儲存筆記後，再開啟**討論資料**。
   同一側欄可切換專案總覽、討論、需求、時程、技術相依、問題與變更、Agent 資源及工作看板；
   **工作** 區則開啟明確關聯的工作階段。
2. 在 **討論 → 討論資料 → 脈絡** 展開 **準備討論脈絡** → **保存到 PM 工作目錄**，選擇預定的既有 repository／host／workspace
   連線，填入已發布分支並按 **預覽已發布版本**。核對固定 commit、填寫目的後，按
   **儲存討論脈絡**。此步驟需要前述 repository 綁定、managed Git 與 start 權限。
   **只保留討論紀錄** 不會建立採用計畫或 BAT 分析需要的 Git 工作區；後續可選
   **沿用既有 PM 工作目錄**。
3. 在 **討論資料 → 脈絡 → 分析選項** 選擇可用的分析 adapter，再按 **詢問 PM**。
   也可從原紀錄按 **與 PM 討論**，直接帶入來源標籤與標題。審閱 **建議更新**，核對來源引文、
   保留需求、排除項目、提議刪除的需求與未決問題，必要時修改文字，再按 **審閱並採用**，一次採用整組更新。
   採用需要 workspace owner 權限及已準備的 PM 自有 Git 工作區。
4. 在 **需求** 檢視已採用需求與關聯工作；頁首 **更多操作 → 工程工作包** 開啟既有工作包清單。
   核對工作包的已採用需求及明確儲存庫／提交目的地。介面列出既有工作包；建立工作包使用已註冊的 `pm.work_package.create` API 操作。
   已採用計畫保存方向，啟動工程套件是另外的產品操作。
5. 在 **需求 → 更多操作 → 品質審閱**，用已設定命令或原始 GitHub checks 驗證固定成果。
   針對原驗收條件與完成定義指定
   **品質審查**，由負責審查者記錄結論、理由與原始證據。把這份評估選入工作或相依條件，
   共用視圖沿用同一份原結果；只有測試通過時，需求仍未被接受。
6. 在 **技術相依** 展開 **發布基準** 與 **維護政策**，發布後保存已驗證基準，
   檢視觀測來源、修復限制與交付上限。**問題與變更 → 更多操作 → 維護審閱**
   會保留原 finding 與修復回執；已開 PR、已合併、已驗證部署及
   runtime 已完成仍是各自獨立的事實。
   預期自動檢查或修復前，需明確採用並啟用 GitHub 觀察範圍、來源、額度與修復規則。
   進入 Maintain 需要新鮮、完整且符合條件的工作量觀察；看似閒置的 session 或保留的
   已完成 worktree，都不能單獨決定階段。
7. 在 **專案總覽 → 報告 → 儲存報告** 保存快照，用 **報告 → 版本** 選定，
   再從 **報告 → 匯出摘要** 展開下載控制，選 SVG、Markdown 或 HTML 及語言。每份匯出使用同一固定快照。Review 選定預期快照，記錄討論與回饋後，
   再記錄 owner 的結論、負責人與後續步驟；準備報告或下載 SVG 不代表完成 Review。
   準備下一份 Review 時，選定前次 Review，保留原決策與已回報效果。
8. 從 **問題與變更 → 更多操作 → 維護審閱** 開啟已存候選，
   先在此 workspace 的 **討論** 建立或選擇既有 **Idea**。
   展開候選的 **加入 Idea 討論**，選擇 Idea 與主題後送出。完整原提案、理由與來源身分
   會一起保留，新輸入是待確認狀態。開啟選定討論，確認或選擇方向後，再明確準備 Planning
   或計畫提案。加入候選不會採用範圍、自動建立討論或派工。
9. 再次分析時，在 **討論 → 討論資料 → 脈絡 → 分析選項** 選擇 adapter，再按 **詢問 PM**。
   展開 **分析使用的已採用計畫（選填）**，
   預覽已採用修訂、核對完整／省略的文件範圍，再選定原版本。不選文件時使用有界討論脈絡。
   BAT 路徑會在原回合與程序退出都證實後，以相同綁定建立新的自有 session。服務重啟仍保留
   討論與固定文件；原 writer 結果未知時會阻止後繼工作。
10. 從 **專案總覽 → 更多操作 → 變更階段** 開啟 **專案活動**，填寫理由並暫停或結案，新 managed 工作會停止；檢視原在途操作與可讀結果。
    重新開啟需 owner 以目前生命週期版本明確操作，不會重播舊命令或恢復舊 execution epoch。
    保留未確定操作，不代表 runtime 已停止，也不授權重送。

缺少 runtime、Git 工作區、驗證器或原始來源時，畫面會指出該步驟的限制，補齊後再開始
新操作。回覆遺失時，用已保存草稿查回原請求，保留原版本與操作身分。
人的 checkout 與手動 session 維持唯讀。
以上說明可用的產品操作，不表示已完成完整生命週期的真人驗收。實際 operation、來源、runtime
與交付收據，需與離線測試和 CI 分開記錄。

### 讀取 AHP 主機 catalog

在 workspace 的 **Agent 資源** 檢視展開 **AHP 觀察**。操作者需先在中央為此 workspace 設定
adapter、端點、憑證參照及憑證傳輸方式；詳見 [AHP 觀察契約](design/pm-ahp-observer.md)。
擷取時會協商支援的協定版本，已保存收據列出原版本與 decoder profile；設定檔沒有協定 profile 欄位。
這裡不輸入主機網址或憑證，也不會變更分析 runtime 或將 writer 移到其他 adapter。

選擇已設定的 adapter，按 **擷取主機 catalog**。從該份已保存 catalog 選取最多
8 個 session，可選擇包含已列出的靜態 changeset，再按 **擷取選定快照**。
僅讀取 catalog 列出的 session 與有界 metadata；不建立、傳訊或停止 session。
**重讀已保存觀察**及 **已保存觀察歷史**只查中央收據，不連到主機。
展開 **原始來源文件與收據**，可查看原始身分、事件 metadata、重播缺口、缺失來源及
原操作。不完整的觀察不能證明不存在、完成、成本或管理權。

切換 adapter 會隔離選取草稿與請求 key。回覆遺失時，回到原 adapter 按
**查回原請求**；來源改變或撤權後，可能需恢復原來源權限才能查回。不要在其他
adapter 重建結果未明的請求。設定支援的協定與離線測試不代表已通過真實主機驗收。

## 進階：既有中央與操作者部署

已存在的桌面中央設定會保留。要明確加入其他服務，可用「進階：加入既有中央」，再登錄該中央的獨立憑證。以下說明操作者自行維護中央的方法，不是一般打包安裝的前置條件。

### 1. 準備中央主機

以下操作者範例使用 Linux 與 Python 3.10–3.13，與桌面打包流程分開。中央必須能存取 BAT server；managed Git 工作另需 SSH／Git、Connector 自有 managed root 及明確的 repository 綁定。

兩種 token 用途不同：

| 憑證 | 誰使用 | 用途 |
| --- | --- | --- |
| BAT remote token | Connector 連 BAT 主機 | 使用 BAT 遠端協定，搭配固定 TLS 指紋。 |
| Connector API token | Dashboard／MCP 連中央 | 識別呼叫者並授權中央操作；每個 client／actor 分別簽發。 |

Dashboard 登入欄位不能填 BAT remote token。

### 2. 安裝 Connector

兩個已發布版本都含驗證安裝器；啟動腳本與安裝須使用同一 tag。穩定版：

```bash
curl -fsSL https://raw.githubusercontent.com/TED-H-com/bat-agent-connector/v0.2.12/scripts/install.sh | sh -s -- --tag v0.2.12 --channel-check stable
```

預覽版：

```bash
curl -fsSL https://raw.githubusercontent.com/TED-H-com/bat-agent-connector/v0.2.13-preview.20261022/scripts/install.sh | sh -s -- --tag v0.2.13-preview.20261022 --channel-check preview
```

`--channel-check stable` 拒絕預覽版；預設 `preview` 可接受穩定版與預覽版。
Linux 加上 `--enable-updates` 才會登記所選通道並啟用使用者排程，取得該通道符合條件的較新版本；
否則依印出的收據、登記與排程指令操作。安裝前提及收據流程見[英文安裝說明](getting-started.md#2-install-connector)，通道與回退見[更新指南](connector-updates.md)。

也可在中央主機使用 `uv` 安裝開發來源，固定已檢視 commit，避免不知情跟著 `main` 變動。請將 `REVIEWED_COMMIT` 換成與選定桌面／中央部署相符、已檢視的來源 commit：

```bash
uv tool install 'git+https://github.com/TED-H-com/bat-agent-connector@REVIEWED_COMMIT'
batc --help
```

開發 checkout 可改用 `uv sync --locked --extra dev`，以下指令以 `uv run batc` 執行。桌面與中央須維持 contract 相容；桌面會拒絕不相容的合約或身分。

### 3. 配置 BAT 存取

這台主機已有 BAT desktop profiles 時：

```bash
batc import-bat
batc hosts
```

匯入會寫出 `~/.config/bat-agent-connector/hosts.toml`，預設關閉 writes；保存 endpoint、固定 TLS 指紋及 **token 參照**，不複製 token 值。`batc hosts` 會探測已配置主機，成功不代表 Git／部署也就緒。

Profile 位於別處可用 `batc import-bat --profiles-dir /path/to/profiles`；加 `--output -` 預覽。不要只為跳過既有設定而加 `--force`。

中央沒有本機 BAT profiles 時，依 [hosts.example.toml](../examples/hosts.example.toml) 配置實際 endpoint、可信指紋與 `token_ref`：

`file:` token 檔與 `bat-profile:` token 儲存檔必須是目前使用者擁有的一般檔案，不可為符號連結，且群組與其他使用者不得存取（例如 `chmod 600`）。支援的平台會檢查擁有者；Windows 不檢查權限位元。Token 檔上限為 64 KiB，BAT token 儲存檔上限為 1 MiB。稽核檔採相同規則：既有檔案不符合時拒絕存取，不自動修改權限；新檔案使用 0600，新建父目錄使用 0700。

- `env:NAME`：從服務的私人環境讀取。
- `file:/path`：從私人 token 檔案讀取。
- `bat-profile:ID`：從支援的 BAT client token store 讀取。

預設 profile 路徑不代表完整跨平台自動發現。保留 BAT 原設定，憑證不要提交至 repo。

### 4. 啟動中央並開啟 Web

在中央的一個終端機執行：

```bash
batc serve
```

預設為 `127.0.0.1:18796`，process 須持續運行；這條命令不會安裝登入自啟服務。出現 `OWNER_CONFLICT` 時，先使用已存在的 owner，不要另建一份資料庫。

在同一台主機的另一個終端機簽發唯讀身分：

```bash
batc api-token issue --actor personal-dashboard --scope observe
```

Token 只印一次，請私下保存。開啟 `http://127.0.0.1:18796/dashboard/`，在連線畫面登入；應能進入待處理、專案與工作階段。這個初始身分刻意沒有派工或編輯權限。

中央在遠端時，沿用已驗證 tunnel。例如已配置且可信的 SSH alias：

```bash
ssh -N -L 18796:127.0.0.1:18796 central-alias
```

保持 tunnel，再在 client 開同一個 loopback 網址。不要靠改 bind address 或關閉 Host／Origin 檢查來公開服務；目前沒有通用的公開 hosting 入口。

### 5. 從桌面加入該外部中央

在 managed 桌面的連線設定選「進階：加入既有中央」，輸入可信位址與預期 actor，在原生視窗確認後，透過「加入憑證」另行登錄該中央的 API token。Windows 使用 Credential Manager，Mac 使用 Keychain。原有 managed 服務與工作保留，其個人憑證不會轉送至外部位址。

升級會保留已存在的 `central.json`，即使它需要修復。外部中央連線失敗不會另建本機資料庫。舊版 client-only 包本來就以這個外部連線畫面開始；看到此畫面不代表下載檔包含新的 managed runtime。

Linux 連外部中央時，原生憑證仍使用 `BATC_DESKTOP_TOKEN` 的記憶體 adapter，也可用瀏覽器登入。這不妨礙候選本機 managed 安裝保存自有服務的私人憑證。[原生設定與憑證](design/desktop.md#configure-the-native-client)。

「忘記已存憑證」只移除本機記錄，不會撤銷中央 token。Windows Fleet 的連線／登入選擇另見 [Windows 指南](windows.zh-TW.md)。

### 6. 啟用實際工作

先確認觀察功能正常，再由操作者配置：

1. 目的 host 的 `writes`／`orchestrate`、自有 managed root、Git／SSH runner 與 confinement。
2. 在既有 `github.repos` 明確綁定 repository、host、workspace；專案名、路徑或 Git remote 不是綁定證據。
3. 所需 scopes。專案派工通常需要 `observe`、`manage`、`start`；操作 session 使用 `operate`。整合、接受、merge、部署、整理各有權限。

綁定範例，須把展示值換成已檢視的實際目的地：

```toml
[[github.repos]]
repository = "example/project"
sync = { remote_url = "git@github.com:example/project.git", bindings = [{ host = "worker", workspace_id = "exact-workspace-id" }] }
```

這不是完整 host／GitHub 設定，也不授予 integration push 或部署權限。請搭配 [repository 設定](design/repository-sync.md)與[設定範例](../examples/hosts.example.toml)。

在 Dashboard 建立專案並明確選 repository，再開專案派工：選目的地、分支、檢視固定 commit，加入原始指示與選用附件／模型後送出。回原專案追蹤工作與成果入口。

成果進 PR 前先看整合預覽。Merge／部署另需設定與 scopes；部署 recipe 還要有真實版本／健康驗證。[交付設定](design/delivery.md)。

<a id="cli-and-agent-access"></a>

## CLI 與 Agent 存取

**設定 → 代理**會為管理員安裝版（`uv tool install`）提供可複製的派工設定，
權杖欄位只含預留值。桌面套件只附受管服務啟動器，不會在 PATH 提供
`bat-agent-connector-mcp` 或 `batc`。使用桌面套件時，請先在預定的 Connector 主機
依上方管理員安裝步驟設定，再連接 MCP 用戶端；不要複製桌面的個人權杖。

管理員須對同一服務的設定與帳本簽發獨立權杖：

```bash
batc api-token issue --actor mcp-agent --scope observe --scope operate --scope start
```

將印出的權杖私下儲存為 MCP 程序的 `BATC_API_TOKEN`。只有需要變更專案或工作項目時，
才授予 `manage`。保留 `--principal-only`；主機限制、權限與受管資源歸屬檢查仍適用。
預定 Task Service 不是預設位址時，另設 `BATC_TASK_URL`。

人工工作階段有三條路：在 BAT 回覆；檢視並擷取固定檢查點，再於新的受管工作階段繼續；
或讓持有專屬權杖的 MCP 代理操作受管工作。檢查點路徑不會改動人工工作階段或原工作目錄。
啟動與派工表單下的 **派工如何運作**說明工作樹、BAT 工作階段與成果追蹤。


CLI 可讀取相同觀測與操作紀錄：

```bash
batc hosts
batc inventory
batc history --help
batc relations --help
batc op --help
batc repository --help
batc delivery --help
batc resource-cleanup --help
```

在 MCP client 支援的設定位置註冊 `bat-agent-connector-mcp`。常見唯讀 JSON 格式：

```json
{
  "mcpServers": {
    "bat": {
      "command": "bat-agent-connector-mcp",
      "args": ["--principal-only", "--read-only"]
    }
  }
}
```

先啟動已配置中央，透過私人環境給此 MCP process 專屬的 `BATC_API_TOKEN`，讀取也須包含 `observe`。確認執行檔與設定可用；`--principal-only` 讓 Agent 固定使用自己的中央身分，開啟寫入時仍應保留。確實需要操作才移除 `--read-only`，host 開關與 scopes 仍會檢查。中央 RPC 位址不同時，另私下設定 `BATC_TASK_URL`。

沒有 `--principal-only` 的操作者相容模式，包含 local-admin 讀取備援與直接 Fleet 工具，不是依 scope 隔離的 Agent 入口。可信本機操作者可使用 `bat-agent-connector-mcp --read-only` 直接觀察 BAT，不必走中央工作流程；但共用 Agent 整合不以此為預設。

選用的 loopback MCP HTTP 是另一個 adapter：

```bash
bat-agent-connector-mcp --principal-only --read-only --http --port 8765
```

MCP 位址是 `http://127.0.0.1:8765/mcp`，不是 Dashboard 網址。Durable 工作及復原請使用[共用 skill](../skills/bat-agent-connector/SKILL.md)與[同版 Agent 配接](agent-skills.md)。Task Service recipes 另有設定；planner 關閉時 task 排隊，不代表派工出錯。[Task Service](design/task-service.md)。

## 常見問題

### Connector 安裝錯誤：`entrypoint_directory_not_owned`

安裝程式會顯示有問題的目錄與 `chmod g-w,o-w <dir>` 修正提示。
例如 `~/.local`、`~/.local/bin` 或 `~/.config` 必須由目前使用者擁有，
且不可允許群組或其他使用者寫入。確認顯示的路徑後，對該目錄執行提示中的
指令，再重試安裝或 `--verify-only`。安裝程式不會自動修改權限。
若目錄擁有者不正確，僅移除寫入權限仍無法解決，請洽系統管理員。


| 畫面／情況 | 檢查與處理 |
| --- | --- |
| 桌面要求外部中央／actor | 核對是否下載舊版 client-only 包，或已有外部設定。相符的 managed 候選包，一般首次啟動會自行準備本機中央。 |
| Managed 啟動／升級需要處理 | 保留安裝與資料，重開相符程式；未解工作或憑證問題可能阻止啟動／升級，不刪 journal 或另造 owner。 |
| BAT profile 無法匯入 | 核對 metadata、憑證信任與 token 可用性；token store 加密或損壞時改用手動主機表單。 |
| 已連線但沒有主機／sessions | 檢查「連線至 BAT」與實際可達性；離線或過期觀測不代表工作停止。 |
| 專案派工沒有目的地 | 儲存精確 repository／host／workspace 綁定，並把專案連到該 repository。 |
| 設定顯示忙碌或回執未確認 | 等目前工作穩定，先查回原設定請求和操作，再決定變更。 |
| 操作不可用 | 閱讀 scopes、主機權限、ownership 及來源／binding 條件。 |
| 發送／開工後回覆遺失 | 保留 operation ID 與 request key，先查回，不再派一次。 |
| 原生 BAT merge ACK 持續未知 | 保留原操作與資源；另外的人工裁決 API 不在目前 merge 合約範圍。 |
| 整理被拒絕 | 閱讀保留原因，未解 writer 與唯一內容須保留。 |
| Fleet 沒有啟動 | 新安裝使用[公開 Rust 設定](design/fleet-configuration.md)，再核對所選連線、前置條件及 ownership。未指定 backend 時沿用 PowerShell；既有 owner 需經預覽後遷移。 |

真實 Fleet、帳號與部署驗收由使用者執行。[46 項驗收參考](product/acceptance-v2.md)供這項工作使用，不列入 Agent 完成清單，也不宣稱已經通過。

## 診斷資料

開啟 **設定 → 關於 → 匯出診斷資料**，或使用錯誤訊息「詳細資料」內的相同按鈕。
先檢查摘要與 JSON，再儲存或複製；程式不會上傳資料。檔案包含版本與 commit、
匿名主機狀態、能力與政策開關、介面已讀取的工作階段計數、最近最多 50 筆 API
失敗紀錄（以路徑範本表示），以及已載入頁面內失敗或結果未確認的操作。
無法取得的來源與版本保留未知；Connector 有提供 `reading.incomplete_reasons`
時，才會列出歷史紀錄不完整的原因計數。

檔案不包含金鑰、機密參照、憑證指紋、主機名稱／連線位址／SSH 別名、使用者路徑、
工作階段與討論標題、訊息、提示詞、草稿、儲存庫／遠端網址／分支名稱、操作者 ID
或電子郵件。程式先挑選明確允許的欄位，再對序列化文字進行第二次去識別化。
請檢查預覽，再自行分享儲存的檔案。

桌面版狀態資料庫的預設位置如下；自訂位置可能不同：

| 作業系統 | 狀態資料庫 |
| --- | --- |
| Linux | `~/.local/share/io.betteragent.dashboard/central/state/tasks.sqlite3` |
| macOS | `~/Library/Application Support/io.betteragent.dashboard/central/state/tasks.sqlite3` |
| Windows | `%LOCALAPPDATA%\io.betteragent.dashboard\central\state\tasks.sqlite3` |

`config/hosts.toml` 與 `installation.json` 也在相同的 `central` 目錄。
這些是狀態與設定檔，不是匯出的日誌。桌面版目前**不會將 Connector 的標準輸出與
錯誤輸出寫入日誌檔**；啟動錯誤只保留在記憶體。獨立 CLI 服務的輸出由啟動它的
終端機或服務管理工具接收。診斷資料不會猜測自訂資料庫位置；依目前
`config.state_dir()`，Linux／macOS 預設為
`~/.local/state/bat-agent-connector/tasks.sqlite3`，Windows 為
`~\.local\state\bat-agent-connector\tasks.sqlite3`；設定 `BATC_STATE_DIR`
或 `XDG_STATE_HOME` 時，位置會不同。

「關於」顯示 Connector 版本與 Git 安裝來源的 commit；一般套件或沒有 Git 安裝
來源資訊的開發目錄會顯示「未知」。桌面安裝包另顯示桌面程式版本與 commit；
打包時記錄兩者。本機原生建置若未設定 `BATC_BUILD_COMMIT`，commit 顯示「未知」。
瀏覽器使用提供網頁的 Connector 版本資訊，不把開發目錄的 HEAD 寫入網頁產物。

## Linux Connector 自動更新

Connector 0.2.7 加入可選的使用者更新排程，定期取得已發布版本。
參閱[啟用、狀態與回退](connector-updates.md#發布與使用邊界)。更新供下一次啟動使用；
桌面內附 runtime 仍遵循既有生命週期。
