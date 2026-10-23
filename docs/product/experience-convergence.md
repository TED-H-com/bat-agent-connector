> 2026-10-23：[最新主線進度與未完成能力](progress-2026-10-23.md)，截至 `fea4a1c0`；沒有新發布，新增合併均未安裝或實機驗收。下方保留既有決議及歷史紀錄。
> [Latest main progress and remaining capabilities](progress-2026-10-23.md), through `fea4a1c0`; no new release, installation or live acceptance of the additional merges. Existing decisions and historical records remain below.

> 2026-10-22：[最新發布與未完成驗收](progress-2026-10-22.md)；下方保留既有決議及歷史紀錄。
> [Latest publication and remaining acceptance](progress-2026-10-22.md); the decisions and historical records below remain intact.

> 2026-10-17 owner 提供並要求接續的 EC-1.1 增量方向，更新原 EC-1.0 文件；E／UX／R／V／K 編號及未修訂範圍保留，不另立 v1.4。
> 開工入口仍是 [v1.3 handoff](v1.3-agent-handoff.md)，[差額](experience-convergence-delta.md)與[原驗收表](workbench-ux-acceptance.md#ec-10-整合驗收索引)共同使用。
> **現況校正：**附件研究時 `f31e7964` CI 尚在執行；現在該來源後端、四平台建置與工作循環均已通過，預覽版已發布並有安裝讀回。#280 的下載文件亦已合併並部署。真 BAT／模型與實體手機驗收仍分開，詳見[發布進度](progress-2026-10-18-delivery.md)。
> **同日新增授權：**owner 要求 Connector 自動取得更新，納入 T8／E11／K16，見[Connector 更新契約](../design/connector-updates.md)。這更新了此前對 Connector 自動更新的範圍排除；桌面簽章與 native 更新檢查沿既有契約。
> 本文以 owner 提供的 Markdown 為維護來源，僅正規化內部連結並將主機例子換成合成名稱。外部版本與來源主張保留為附件作者的固定查核紀錄，非本次重新執行競品測試。Word／ZIP 不另存成第二份規格；Git 保留 EC-1.0 歷史。

# Better Agent Dashboard
## 介面與使用者體驗收斂企劃書
### 保留可靠底座，讓日常工作變得直接

**文件版本：EC-1.1｜v1.3 產品體驗整合決議稿（2026-10-17 增量修訂）**\
**日期：2026-10-17｜產品負責人：使用者**\
**適用 repository：TED-H-com/bat-agent-connector**\
**本次查核主線：f31e7964c627e4b6d3af2e2370e7a38c64a20e25**\
**狀態：提供採用、整合與驗收的企劃，不是已完成或已實機驗證聲明。**

> **核心承諾：**像 Project Hub 一樣，說了就能做、做完能接著改；借鏡 Cantus 的工作閱讀與脈絡整理；所有動作仍回到既有 Connector／BAT／CLI、共同操作紀錄與權限模型，並持續形成可信的 PM 追蹤、交付與維護。

本文件不是 v1.4、不建立另一套產品，也不重置原有驗收。v1.3 的 E00–E11／R0–R5、Work-Centric 的 UX01–UX10／V0–V4 與既有驗收仍有效。本文件集中決定如何把它們組合成可使用的體驗；發現重複工作，應更新既有 issue，而不是另開平行 backlog。

---

## 2026-10-19 優先順序：實機穩定化與首次使用

產品負責人實際部署後回報設定困難、狀態互相矛盾與服務不穩，要求暫停功能擴張。
本輪先處理首次安裝、加入機器、背景服務恢復、連線判斷與日常操作；新增視圖、更多
adapter、自動替代路線與複雜 PM 擴充暫緩。安全、資料保全與既有缺陷照常修正。
PM v1.2 全生命週期及原 E／UX／R／V／K 範圍保留，改變的是先後順序與退出門檻。

介面以國中生與退休使用者能自行完成為目標。先呈現專案、討論、工作與下一步；
一般流程不要求先理解 profile、binding、runtime 或 operation。需求、時程、追溯與
維護能力按需要展開。故障先說明失敗在哪一層與一個合理的下一步，技術證據另行展開。

先對一台 Windows client 與一台非正式 Linux BAT host 建立同一候選版本的驗收證據：

- 保存現況，分開記錄磁碟安裝版、執行中的 Desktop／Connector、來源 commit、
  本機或遠端服務、workspace 與可用能力；不先刪資料、重建身分或全面重裝。
- 加入機器依序選用途、可信連線資料／既有設定、workspace、權限與自動連線偏好。
  既有設定先辨識與預覽；一般路徑不手改 JSON／TOML，也不預埋私人設定。
- 檢查與修復區分網路、服務、身分、工作能力與恢復。已送出但結果未知的工作先查回
  原結果，保留 operation key、草稿與資源；重連不重派工作。
- 用真實資料走完開啟、選專案、讀既有工作、建立自己的小修改、讀輸出、補一句、
  看成果與 PR、重開／換裝置後找回工作。乾淨、既有及半套失敗環境都必須驗。
- 包含 Tailscale 閒置、Connector 退出、啟動者關閉、睡眠喚醒、網路切換、手機鎖屏
  與雙 client；連線與工作狀態一致，服務退出後可恢復，連續兩個正常工作日不需 AI 救援。

以上是候選退出條件，尚未達成。每個修正必須進入同一可追溯安裝包再驗收；CI、
模擬截圖、已合併與曾經安裝都不能替代這份證據。逐機擴大部署在小範圍驗收之後。

### 2026-10-19 加入機器責任收斂（已定，尚未實作）

本輪沿 E01／E03／E11、T3／T8、K02／K08／K16 收斂「加入機器」與首次設定，
以同一份責任、來源、預覽／套用／讀回及驗收契約接續既有工作。
規範中的第一個選擇提供三種用途：只查看與操作既有服務、本機提供 workspace 執行能力、兩者兼有。
程式查核包含 main `511fc618` 與 [managed setup 預覽子集](../design/fleet-configuration.md#已實作managed-setup-的連線預覽)；
完整加入精靈尚未實作或取得 R0–R5／A01–A40 驗收。
責任事實只維護於 [Fleet 唯一責任表](../design/fleet-configuration.md#責任表與退休條件)；
[首次設定](../design/managed-installation.md#與加入機器共用的契約已定尚未實作)引用同一契約，產品決定與技術預設也集中於 Fleet 文件。

## 修訂導讀｜EC-1.1 不重置正在開發的工作

上一版 EC-1.0 已投入實作。本版將 #276 合併的工作循環列為「已整合，待真實環境驗收」，並擴充手機互動、低干擾同步、長討論與下一步。保留原 E／UX／R／V／K 編號。

本輪差額及具體 mobile 驗收見同包 `experience-convergence-delta.md`。其中 D1–D6 只是修改定位，不是新 backlog。未被該差額明確改動的本企劃規格繼續有效。

**最重要的基線更正：** Project Hub 已公開 4.124.0 source；我們已合併 #209 與 #276 所整合的採用／開始／結果／下一輪等工作。不要繼續按 EC-1.0 的舊狀態重新派工。

## 01｜企劃目的與產品定位

### 1.1 要解決的問題

使用者已看見相當多管理與執行功能，卻仍可能不知道「我現在可以在哪裡說一句話，讓工作開始」「第二句要送到哪裡」「這份成果對原本目標有什麼意義」。當背景設定、工作包與操作 ID 直接變成前置操作，底座的完整度就轉化為使用負擔。

本次不是再證明功能數量，而是讓人能在同一專案裡完成：**理解狀況、表達意圖、啟動自己的工作、檢視結果、持續修改、作出決策。** 外部 agents 透過 API／MCP 使用相同能力，不必點擊 UI；其工作與結果自然回到 Dashboard。

### 1.2 產品定位

Better Agent Dashboard 是「可直接做事的專案工作台」。它保留使用者原本的 BAT、VS Code 與 CLI，不要求把所有 coding 搬入一個新 IDE；但基本 AI 工作循環不能強迫使用者離開 Dashboard。

專業 PM 在執行過程中整理事實、規格、偏差與後續選項，不要求使用者先替 PM 把表格填滿。可靠底座的價值表現在不中斷別人的工作、結果可查回、不同入口看到同一份紀錄，而不是讓每次操作變得繁瑣。

### 1.3 三個必須同時成立的成功成果

| 成果 | 使用者感受到的價值 | 完成判準 |
|---|---|---|
| 直接工作 | 一句要求、第二句修改，都在原工作裡完成 | 真實執行與結果，不是模擬串流或 API 人工補資料 |
| 跨工具掌握 | BAT／CLI 的歷史與新成果回到同一專案 | 有來源、覆蓋範圍與能力說明；不冒認完成或重複計數 |
| 專業追蹤 | 知道工作為什麼做、成果是否符合目標、下一步如何選 | 同一證據更新需求、PM 視圖、交付與維護 |

本企劃將使用體驗視為架構的使用入口；不把「保留架構」解讀成保留目前所有不便，也不把「簡化」解讀成移除後端檢查。

## 02｜既有成果、參考來源與查核邊界

### 2.1 本次採用的基線

我們固定主線 `f31e7964c627e4b6d3af2e2370e7a38c64a20e25`，已含 #276 整合與 #277／#278。工作建立、採用／開始、同 Work 下一輪、profile/readiness、固定 Result／diff、驗證、PR、MCP/API等價及固定評估進PM草稿，已有整合實作。[S17][S18][S20]

這些工作轉入回歸與實機驗收，不重做。#209 的特定 Codex/BAT 下一輪限制仍在；T5 原生歷史和更多完整 route、T6 真實 PM 目標判讀與維護、E11 安裝與實機仍需完成。[S19][S21]

#276 候選CI通過；最新主線 push CI／Desktop 在本輪查核時仍執行中。未在本輪安裝應用或啟動真實BAT／模型，不以綠燈聲稱 ready。

### 2.2 參考範圍

| 來源 | 本輪版本與可採用內容 | 邊界 |
|---|---|---|
| Project Hub | `e5e1696a7a8df930b0693bbd8418696a15c52585`／4.124.0；公開 source已更新，低負擔清單、可調側欄、整理後下一步與一次擁塞交接 | 公開快照不等於全部私人開發歷史；不能照搬權限和自動Git行為。 |
| Cantus Panel | `496dc178ac8c32d6ae01c9c77a9c0c2dc8714e36`；手機全螢幕流程、底部視角、sheet、composer、來源自動刷新 | 瀏覽器touch test非真iPhone；queue依賴分頁；外部session續作政策不同；UNLICENSED。 |
| 先前五張參考圖 | 繼續作閱讀層級參考 | 不作真實功能證據，不含入公開展示。 |
| 原Work-Centric補充 | 架構、選取、48項UX驗收繼續有效 | 已合併視圖不重做；手機新增行為詳見差額。 |

本輪來源支持現況；以下介面與效能數值是產品目標，非本次測得。競品未提供可驗證的能力，不推定已完成。[S11–S22]

## 03｜借用什麼，以及如何改接到我們的底座

### 3.1 核心轉換矩陣

| 參考到的好體驗 | 我們採用的表層行為 | 接回既有架構的方式 |
|---|---|---|
| Project Hub：專案裡快速開始 | 輸入要求後，直接看到必要短計畫與開始動作 | 已有 binding／profile 自動帶入；重用 `work.start` 與 package／handoff |
| Project Hub：同一任務持續對話 | 第一輪、第二句、結果維持同一 Work | 以 Work／Attempt／controller 綁定輸入，不由前端直接寫 PTY |
| Project Hub：記住模型與草稿 | 只在目的地或影響真的改變時再問 | Profile 由 server 驗證；草稿由 client 按身分及對象隔離 |
| Project Hub：計畫後接執行 | 「採用並開始」可表達一次完整意圖 | 組合既有 adoption 與 start；保存每個子步驟、原 key 與部分成功 |
| Cantus：先找回已有對話 | 先連來源即可閱讀，不必先建立完整 PM 資料 | Adapter 只讀索引與增量結果；關聯不轉移 session 控制權 |
| Cantus：安靜的工作閱讀空間 | 清單、主內容、按需 inspector，主工作最突出 | 沿用 shared frontend、tokens、SelectionContext；不另存進度 |
| Cantus：Work／Lineage／Live | 同一選取工作，切三種理解角度 | 共用 authorized projection／事件來源，不長三套 store |
| Cantus：下一輪排隊容易理解 | 忙碌時顯示「加入下一輪」和可查回的佇列 | Queue 的權威放 server journal，不依賴分頁 timer 持續送出 |
| Cantus：跨模型帶上下文 | 可選另一個已就緒 runtime 做新的自有 attempt | 固定交接摘要及來源；不接手外部 session，不共享活動 worktree |
| 兩者：關聯容易追查 | 點問題即可在原位看證據並討論 | 同一 assessment／decision 可從 Work、討論、Overview 開啟 |

### 3.2 明確不搬進來的行為

不採用外部 session「只要閒置就可以 resume」；不將瀏覽器 queue 當唯一執行排程；不把新 session 等同獨立 Git worktree；不以 UI 勾選代替驗收證據；不為直接工作取消原生工具批准或 server 權限；不把競品的帳號登入、品牌、用量收費與更新系統整套引入。

**吸收的是使用者完成事情的方式。執行、狀態、授權與恢復，仍由我們的底座負責。**

## 04｜架構不變量，以及允許的優化

### 4.1 不變量

| 保留項目 | 本次不可退讓的行為 |
|---|---|
| Tauri＋共用 Web frontend | 同一份正式 UI；demo 只替換資料／transport，不另寫更漂亮的產品 |
| Workspace Service | `server_id + workspace_id` 為管理邊界；同 repo 不自動合併跨 server PM 紀錄 |
| 既有 Python Connector／TaskDaemon | API、MCP、UI、背景維護共用 domain commands 與 journal |
| 原始 controller 與操作紀錄 | 來源可讀不代表可寫；已送出的操作查原紀錄，不因重連重開 |
| BAT／CLI adapters | 保留原生 coding 工具與輸出；按具體 route／版本宣告能力 |
| 自有工作環境 | 人、其他 controllers、PM、修復工作使用各自有權管理的資源 |
| PM 文件與生命週期 | 保留 Idea、Planning、Sprint、Review、Maintain、Paused／Closed 與 PM worktree |
| Git／GitHub 交付 | 預期 PR head、固定成果、CI、部署版本與恢復依現有契約 |

「穩健」是要保留並持續驗證的設計要求，不是本文件替全產品作實機保證。若現有邏輯有 bug，應修正它；保留的是不變量，不是瑕疵實作。

### 4.2 本次只允許三類必要增量

**呈現投影：**把現有 Work、Attempt、Result、權限、來源與 freshness 組成可讀摘要；索引與快取可補強，但不另建第二份完成狀態。

**使用者意圖組合：**以現有 commands 組合「開始」「採用並開始」「下一輪」「依固定成果另開」。一次操作可有多個可恢復子步驟；不再要求使用者自行呼叫中間 API。

**能力與上下文配接：**統一 profile、adapter 與來源的 readiness、版本、可用動作、覆蓋範圍，以及輸入所綁定的 Work／Attempt。

上述是職責，不要求新建三個服務、三個 repository 或三套資料庫。只有現有能力確實不足時才新增 endpoint／schema，並附 migration、權限與讀寫責任。

### 4.3 權威路徑

**人類 Tauri／Web 與 agents API／MCP → 同一 Workspace commands／reads → PM、Orchestrator、journal → BAT／CLI adapter → 結果與 GitHub 證據 → 同一 Work／PM projection。**

Client 可保存草稿、偏好與尚未送出的文字，不能把本地儲存當 accepted plan、queue 或 ownership 的權威。來源 reader 可以有程序或 spool，但不能成為第二個 scheduler。

## 05｜產品空間：兩個層次、四個入口

### 5.1 日常層與管理層

日常層讓人「找到工作、說話、看結果、繼續做」；管理層讓人「理解 scope、milestone、風險、驗收與投入」。兩層共享專案脈絡，不要求小修改先通過所有管理頁。

主要入口維持 **今天／專案／工作／執行資源**。Activity、Connection、Settings 與進階工具放次要位置；命令列與搜尋只協助尋找，不在搜尋時啟動 AI。

| 現有管理能力 | 收斂後位置 |
|---|---|
| 專案總覽／Charter | 專案首頁；固定報告版本放報告選單 |
| 需求與交付追溯 RTM | 專案管理視角選擇器；Work 可直接打開所屬需求 |
| 時程／Sprint | 專案管理視角；Review 從實際成果進入 |
| 技術相依／維護 | 專案管理視角；維護工作仍用相同 Work 頁 |
| 問題、行動、風險與變更 | 專案管理視角；Today 聚合真正需注意的事項 |
| Agent 資源與效能 | 執行資源入口＋專案篩選 |
| Board | 同一 Work 清單的看板呈現，不是另一份工作紀錄 |
| 原生 session／worktree／operation | Work／來源詳情與搜尋；舊連結維持可用 |

### 5.2 不為了畫面強制生工單

Idea 可以只有對話、repo 與候選方案；尚未投入不算落後。外部 session 可以暫未歸類；它不因為被讀到就自動升格為 Work。關聯可以先由規則或 PM 提出候選，再由低風險 policy 或使用者採用；仍不取得控制權。

Today 的計數標明涵蓋來源；不同 workspace 的跨來源總覽只能是明確選用的閱讀聚合，不合併管理權威、Sprint 或成本。

## 06｜介面與視覺規格

### 6.1 一個穩定的閱讀骨架

桌面依空間提供：導航 rail、對象清單、主工作內容、可收合 inspector。以工作標題與目的開頭，下一行交代責任及位置，再給目前摘要和主要動作。技術 ID、receipt、完整路徑放 Details。

1440px 起始設計可沿先前 UX 補充的 208px rail、256px 清單、320px inspector；剩餘空間給主內容。這些是起始值，不是強制每頁四欄。當主欄將小於 520px，先收 inspector，再收 rail／清單；不縮字硬塞。

Idea 長談預設對話最大；Planning 才依需要展開草稿；執行中以輸出為主；Review 才打開 diff／證據。Inspector 同時只顯示一個具名對象，空面板不占位。

### 6.2 第一屏的工作摘要

示意：

> **修正登入重複送出｜待檢視**\
> 避免快速點擊造成重複請求，補上失敗後的重試案例。\
> 你的工作 · Claude 經 BAT · host-a\
> 已產生變更，兩項測試通過。下一步檢視差異並建立 PR。

不得把「已有紀錄／尚無紀錄」的六態清單當主要成果。只要求 PR 的工作，未部署不是本次缺陷；但開 PR 也不等於需求已驗收。主要摘要依交付目標組合，原始證據保持獨立。

### 6.3 共同規則

維持自家品牌、一個主要強調色、語意化狀態色、清楚字級與留白。借用安靜外框與視覺層級，不要求照抄玻璃效果。表格、diff、正文以可讀為先。

正文建議 14–16px；輔助 12–13px；同一畫面避免過多字級。繁中、英文、系統 fallback 字體、light／dark 一起驗證。主要行動每個情境區只強調一個；停止、清理與重新派工在具名選單，不和一般 Send 擺成相同權重。

桌面可以獨立捲動清單與主文，但避免 inspector 內再套多層小捲軸。手機使用單主面＋Back／清單 sheet；輸入框不可被鍵盤遮住。一般內容支持 320 CSS px 重排，圖與 diff 可用標示的局部捲動及文字替代。

使用 WCAG 2.2 相關條件驗證對比、鍵盤與焦點；一般文字至少 4.5:1、大字至少 3:1。最低指標 target 的 24×24 規則及例外按標準檢查，產品手機主動作以 44px 為設計目標。截圖不能代替實際渲染測量。[S9]

### 6.4 EC-1.1 手機互動契約

手機不是縮小四欄。全螢幕清單→對話／Work，資訊與設定使用sheet，返回保持選取和草稿。全域Today／Projects／Work／Resources與Work內的三視角分層，不攤七個底部入口。觸控橫向保持可讀操作。

composer 使用同一shared邏輯與server queue；visual viewport、safe area、IME、browser Back、方向切換、前景恢復都需測。隱藏無意義idle但保留錯誤／未知；圖示不省略可存取名稱與目前runtime。參照差額 §4 與原K15擴充。這是呈現層改動，不新增mobile-only API或排程器。[S11–S13]


## 07｜第一次使用：先有價值，再逐步啟用能力

### 7.1 三條入口不是三套 App

**體驗示範：**明確合成資料，不讀私人檔、不呼叫模型。示範不能比正式 UI 多一套功能邏輯。

**連接既有工作：**選 server／workspace，明確授權來源目錄或已有 adapter；先讀標題、歷史與成果。來源唯讀也有價值，不要求先填全部 PM 欄位。

**開始自己的工作：**選擇一個具備完整必要能力的 profile，完成最少就緒設定，再回原草稿。不將「已安裝 CLI」直接標為「可執行」。

### 7.2 Profile 是預設值，不是永遠有效的許可

Profile 保存可讀名稱、server、workspace／repo binding、adapter route／版本、模型選擇、支援能力和政策引用。憑證留在既有服務或原生安全儲存，不塞進 project plan、URL、匯出或模型上下文。

每次開工由 server 重查權限、版本、來源及能力。只有一個合理目的地就自動帶入；多個有實質差異時才請使用者選擇。測試推論必須明確顯示會用哪個帳號／配額；讀取或切頁不自動花費模型額度。

### 7.3 就緒問題要有可執行下一步

| 情況 | 主訊息與恢復 |
|---|---|
| BAT 可讀，PM 未就緒 | 顯示兩種能力；可用的 coding 路線不被一併關閉 |
| CLI 未登入／模型不可用 | 指向實際執行 host 的修復方法；不偷偷換帳號或付費來源 |
| 可開始但不支援下一輪 | 啟動前顯示限制，不在使用者第二句時才揭露 |
| 不支持本機 Git | 提供已有的遠端 route；不虛構 local-to-SSH fallback |
| 服務失聯 | 保留草稿與最後合法可見資料；修復後回原位置 |

首條實機路徑選一個已證明能開始、第二輪、回收結果的 BAT profile；可以先是 Linux 遠端。這不代表本機 Windows／Mac 或所有 CLI 已完成，同時不能把未支援 profile 的限制硬藏起來。

## 08｜Idea、Planning 與工程規格的自然銜接

### 8.1 長期 Idea 是正常狀態

預設「傳送給 PM」保存訊息並真正取得一般回覆；「只存筆記」是另一個明確動作。PM 不可用時，不顯示像已開始回答的狀態。標題可自動產生並可改，不是先聊天的必填表。

討論可以研究 repo、比較方案與反覆改想法。候選、否決、待定、使用者確認和程式證據需保存其來源。一般回覆不強制產生 proposal；引用文章中的 `/apply` 也不構成執行授權。

### 8.2 Planning 取得必要資訊，而不是要求人填欄位

PM 先讀既有計畫、repo、issues、規格與可見成果；自行查證可取得的資訊，再提出少量真正影響方向的問題。候選方案不能被寫成已採用 SHALL。一次審閱一組有意義的變更，而不是每個欄位一次 approval。

小修改可用目的、範圍、接受條件、執行 route、交付上限五項短計畫。架構改造、付費會員、schema 遷移等按風險增加 design／ADR、測試與 rollout，不為所有工作強制正式 Sprint。

### 8.3 SDD 和 PM 各守自己的事實

保留既有 OpenSpec 或 spec 路徑；PM worktree 的 `docs/pm` 以索引、plan、decision、review 為主，不抄出另一份規格。工程包使用固定需求、design、tasks 與驗證安排。

PM 可以自動回填已觀测的 PR／CI／部署，但 scope、Goal、接受條件變更仍依採用流程。新版本計畫不自動中斷按舊版本工作的 session；在 PM 顯示影響，由原 controller 繼續，或另開自有後續工作。

## 09｜從第一句到第二句：核心工作流程

### 9.1 採用並開始

使用者可說「先寫成計畫」或「採用這份，開始這三項」。前者只產生草稿；後者在權限與範圍成立時表達一次完整授權。

UI 送出一個可查回的 intent，後端組合 adoption、必要的 work binding／package、各自的 start。使用既有 #194／#197 與 #215 路徑，不再要求人手呼叫 `pm.work_package.create`。具名內部 actions 是接線點，不是使用者必須輸入的語言。[S3][S5]

點「採用並開始」立即展開原專案的 Work，先顯示本地提交中，再依回執顯示已受理、準備、啟動或失敗。畫面不先宣稱已執行；也不要求再點「開啟工作」。採用成功而啟動失敗時，保留採用版本，只恢復未完成步驟。

### 9.2 第二句是原工作的下一輪

自己的 Work 輸入框直接指向已綁定的 controller／attempt。忙碌時保存到 server 的 durable queue；client 關閉後，已確認排入的工作仍由 server 按政策處理。只有尚未送到 server 的文字才是本地草稿，兩者標示分開。

預設不 steer／interrupt 目前回合。取消 queue 只取消已證明尚未送出的 input；可能已送出但 ACK 遺失時，查原 receipt，不換新 request ID 重送。暫時無法查證則顯示結果未知，後續同目標的依賴 input 等待，不擅自移到另一個 session。

#209 的 Work-target `session.send` 與 server queue 已整合；本次不能把原生CLI能接續當作每條BAT route都可接續。特定Codex工作仍受回合歸因限制；由能力矩陣在開始前說明，不為demo關掉檢查。[S17][S19]

### 9.3 可停止、可修復，但不搶走工作

停止後續派送、停止自有目前回合、取消未啟動批次，是不同動作，UI 不用一個模糊 Pause 混在一起。外部或他人工作只提供觀察、送建議給 owner、或基於固定成果另開。

換模型／adapter 不要求沿用他人的原生 session。相同 owner 的合法接續沿原有規則；跨工具可建立新的自有 attempt，保留 predecessor 和未帶入上下文的說明。PM adapter 更換也遵守 PM worktree 單一有效 writer 的既定規則。

## 10｜Result：讓成果先被理解，再被深入檢查

### 10.1 說清楚這一輪做出了什麼

主要呈現順序為：成果摘要、修改範圍、驗證結果、交付目標與下一步；原始 transcript、工具輸出、原生 ID、receipt 在詳情。長 diff 按需讀取，不為開卡片就掃整個 repo。

執行活動、實作者自述、固定成果、測試、需求驗收、PR／merge、部署分開保存。UI 可整理一句摘要，但不能用 `streaming=false`、CLI exit code 或 archive 代替需求完成。

固定 Result 的 commit／artifact 不因下一輪改動而被覆寫。即時 changes 與固定成果分開標示；PM 審閱與 PR 預览針對選定版本。已有部署卻無需求驗收時，直接說明缺少哪類證據，而不是只顯示 0%。

### 10.2 外部工作也能進來，但先做來源與歸因

外部會話顯示原生標題或有來源的可讀摘要，hash 退到次要位置。可讀取歷史不代表即時；索引有資料但 transcript 不可用也要說清楚。不得要求所有 coding agents 改寫回答格式。

同一 native session 被兩個 reader 看到，依可信 provider／host／native identity 對應為多個觀測來源，不重算工作或費用。相同標題、repo 或 path 不足以去重。沒有可信關係時保留分開與待確認。

Session 在某個 cwd 執行，不足以把整個 repo 的 diff 歸因給它。以固定 base/head、native changeset、明確 artifact 與可查驗的來源表達；無法證明時標部分或未歸因，不能替來源自動 commit／stash 來補證據。

### 10.3 最少的能力契約

| 能力 | 使用者可見承諾 |
|---|---|
| 歷史讀取 | 可閱讀哪些內容、上限、更新時間與缺漏 |
| 即時觀察 | 實際從哪個事件／polling 得到狀態，不把歷史叫 Live |
| 建立自有工作 | 在指定受管環境建立新 session，不改外部 session |
| 下一輪輸入 | 相同自有 Work 的排隊、送出與原 receipt 可查回 |
| 成果回收 | 固定內容、差異與驗證可連回需求及 PR |
| 恢復 | 哪種重啟與斷線可自動查證，哪種只能保留 unknown |

BAT 是第一條落地路徑；原生 Claude／Codex、VS Code AHP 按原 E06–E08 擴充。MCP 是 agents 的工具入口；AHP／ACP／CLI 是 runtime 配接，不能混成一個「支援 AI」標籤。

## 11｜Work、Lineage、Live 和資源的一致性

Work 是對話與成果主頁；Lineage 是有來源的目標／需求／工作／成果／PR 關係；Live 是目前執行、已確認 queue 與等待原因。切視角不重新派送、不重設草稿、不丟失選定 result，也不增加另一個事件讀取器。

關係圖預設選取 Work 的上下游局部，提供可鍵盤操作的關係清單。`depends_on`、`derived_from`、`produced`、`integrated_into` 有不同含義；分支祖先不直接等於工作依賴，提案邊不會直接解鎖 scheduler。

Live 的 Now 只放有新鮮執行證據的工作，Next 只放 server 已確認排入的工作；PM 建議另行顯示。無排程預測就不顯示「2–8 小時」，沒有統一度量就不顯示「68% Available」。

資源頁分清 host、controller、runtime、model。可以簡短呈現「Hermes 委派／Claude 執行／host-a」，但不以 Worker 一詞混淆身分和機器。換來源清除不再有權的內容；延遲 response 不得把舊來源資料帶回新 workspace。

**EC-1.1 讀取面要求：**清單傳摘要，選取後按需读 steps／transcript／diff；來源刷新保留焦點與草稿。快取依來源、身分、權限和版本隔離；寫入前仍新鮮核對。觀測端異步與coalescing不改業務一致性。參照差額 §5。[S15][S22]

## 12｜PM tracking 與 Maintainer：我們自己的產品價值

### 12.1 五個重點自動整理

專案首頁持續整理目標、交付、阻塞、偏差、決策。程式取得確定事實；PM 解釋原因與選項，附來源、版本及資料截至時間。刷新 UI 不自動觸發一次付費分析；背景推論依已採用的觸發與費用政策。

範例（示意）：登入修復的測試與 PR 已有，帳號恢復仍少一個郵件失敗案例；建議先補驗證，推薦碼留在下一輪。這段建議可以從 Today、Work Result 或討論旁開啟，採用的是同一筆 decision。

低風險事實更新自動回填；範圍與承諾變更集中成少數有意義的 decisions。不要把每個 Unknown 都推給人，也不要把 agent 推測填成確定值。缺資料先讀取／查證，只有改變決策的缺口才引導使用者回答。

### 12.2 完成不是按一下顏色

Assessment 對應固定 requirement／scenario、DoD、candidate result 與驗證來源，結論可為通過、未通過、證據不足。服務端依政策採用後，Requirements、Roadmap、Sprint、依賴、Overview 共用這個結果；使用者不在六個地方重填完成。

Reviewer 可以用 agent，部分確定性檢查由程式處理；「必須所有成果都人類批准」不是本次要求。權限與風險規則保留，UI 只在真正需要人作決定時強調 Review。

### 12.3 發布後的照顧仍用同一條路

ReleaseBaseline 固定維護版本與證據；滿足已採用的條件才進 Maintain。無輸出、server 失聯或留著舊 worktree，都不能單獨推定已閒置。Maintainer 依政策查依賴、Issue、PR、文件與改進機會，修復走自有 Work／Result／交付，週報走同一 PM 討論。

重大新方向回 Idea；沒有人採用就不自動變下一個 Sprint。Paused／Closed 停止新自主投入，但已送出的外部操作仍查回結果。關閉管理專案不自動 archive GitHub repo，也不操作別人的 sessions。

## 13｜API、MCP、投影與事件契約

### 13.1 使用者操作到既有能力的落點

| 使用者意圖 | 接線原則 | 明確禁止 |
|---|---|---|
| 傳送給 PM／存筆記 | 重用已存在的一般 reply 與 note 路徑，模式與效果清楚 | 每句強制產生 plan，或保存後假裝 AI 已回覆 |
| 開始一件工作 | 沿 `work.start`、原 package／handoff／attempt | 新建平行 runner 或讓前端自行 launch SSH |
| 採用並開始 | 沿 #276 內已實作的採用／開始組合，子操作各有固定 key | 覆蓋單一布林成功，或失敗後整批重做 |
| 第二句 | 沿 Work-target `session.send`／server queue | 分頁 timer 成為派送權威、切工作後送到別處 |
| 查看成果 | 沿固定 Result 及按需 changes／diff | 讀取時寫進度，或改採用版本 |
| 外部來源接續 | 固定來源、建立新自有 attempt | 直接 resume 外部原生 session |
| PR／merge／deploy | 沿既有 integration、delivery 與權限 | 以 UI 自述或某段 log 取代成功回執 |

未實作的組合動作按能力顯示，不用假的成功按鈕補齊。命令名與參數以當前 registry 為準；這是接線規則，不授權前端猜測 endpoint。

### 13.2 最小共用讀取模型

工作清單、主頁、Lineage、Live 和 inspector 共用：identity／intent、responsibility、state facets、selected result、relationships、allowed actions、freshness／coverage、可選 PM assessment。優先映射現有 IDs／tables，必要的投影採有界查詢。

UI SelectionContext 保存 service／principal、workspace、主選取、被檢視對象、view、result、return target。草稿綁 thread 或 Work 與意圖；正式送出時 server 綁定實際 attempt。當 controller／attempt 已改變且語意可能不同，不能把舊草稿無聲轉送給新工作。

### 13.3 讀取與寫入的可靠邊界

可合法保留的舊資料顯示 freshness；明確撤權則清除原資料及衍生摘要。保留現有 admission／source withdrawal 規則，必要的遮罩與錯誤用局部顯示改善，不直接取消防護。

切頁不掛第二個 collector。高頻文字增量只更新相關區塊；背景更新不能搶焦點、清草稿或讓已按的控制項消失。送出前再查 membership、controller、工作版本與前置條件；queue 出隊時也重核，不沿用已撤銷的 writer 權限。

模糊副作用不承諾跨所有工具 exactly-once。承諾的是保存意圖、穩定 key、原結果查回、無證據不自動重送。舊的操作可以恢復自己未完成部分，但不能把恢復變成重新執行已完成 merge 或開始。

## 14｜工程整合：修改哪些、保留哪些

| 程式區域 | 本輪整合責任 | 不能擴張成 |
|---|---|---|
| shared shell／workbench／tokens | 四入口、主選取、面板、狀態摘要、舊連結相容 | 另一套 frontend 或所有頁面重新寫 |
| PM discussion／proposal | 真 reply、短計畫、就地採用、回原工作 | 第二個 PM 資料庫或每句多角色辯論 |
| work.start／follow-up／adopt composition | 讓正常路徑不留 API-only 缺口，保留恢復 | frontend 直寫 runtime 或改掉子操作契約 |
| Attempt／Result／evidence reads | 固定成果與活資料分清、可讀摘要、來源範圍 | 無界掃描全部歷史與重複狀態帳本 |
| Native reader／BAT／AHP adapters | 每個 route 分別驗證讀、做、第二輪、恢復 | 以廣告名稱推定全部能力 |
| API／MCP／skills | 等價入口、最少明確輸入、相同結果與深連結 | agents 以瀏覽器點 UI 才能使用 |
| delivery／maintenance | 固定 PR／發布版本，將結果回 PM | 新寫 CI 系統或背景修复引擎 |
| docs／demo／release | 同源 UI、可辨識版本、已支援能力、真實限制 | 同時維護一套永遠比較漂亮的 fixture app |

一位 UX integrator 掌握 shell／composer／selection；一位 contract owner 掌握 Work／Attempt／Result／actions；adapter 與測試人員依垂直路徑協作。不要讓多個 agents 同時覆蓋核心檔案或各自命名同一狀態。

保留必要測試；把與變更無關的重複工作從 PR 快路徑移開，候選版本仍跑完整必要套件。每張 PR 先證明使用者哪一步變好了，並報已測與未測範圍。長文件與多輪審閱不能代替一次真實工作循環。

## 15｜交付路線與原計畫對照

### 15.1 八條整合工作線

下列 T1–T8 只是本企劃索引，全部附掛既有 E／UX 工作，不另建競爭的 backlog。已合併部分不重做，先查差額。

| 索引與成果 | 對應原工作 | 主要依賴／驗收出口 |
|---|---|---|
| T1 共用工作空間 | E03；UX01–03 | 沿 #193，Work 成為可用入口，舊資料與連結不丟 |
| T2 第一句到第二句 | E02／E05；UX04／UX10 | #194／#197 接 #209／#215，真 BAT 完成兩輪 |
| T3 來源與 profile | E01／E08；UX08 | 先讀已有資料、必要設定、精確能力與回原草稿 |
| T4 固定成果與現場 | E04；UX03／UX05／UX06 | #196／#205 沿用；差異、queue、typed lineage 同源 |
| T5 Native／AHP 擴充 | E06／E07／E08 | 至少一條額外 route 跑通，再覆蓋原承諾矩陣 |
| T6 PM 判讀与後續工作 | E09；UX07 | 真 Result 到 assessment、依賴與決策／維護 |
| T7 MCP 與交付 | E10 | 人與 agent 同步可見，同權限同來源，不自動提權 |
| T8 遷移與可用性 | E11；UX09／UX10 | 全程參與，候選版、性能、故障、平台與新手驗收 |

### 15.2 三個對外可交付增量

**增量 A：可用的 BAT 工作台。** 完成 T1／T2 核心、T3 最少 profile、T4 必要 Result；T7／T8 同時參與。目標是在正式共用 UI 用真 BAT 做兩輪並開 PR。簡圖和美術可繼續打磨，但不可只交 mock。

**增量 B：跨來源與跨入口。** 加入既有 native 歷史、可信去重、第二種完整執行 route、agents 從 MCP 派工，以及真正的 queue／關聯。保留原 v1.3 的 BAT、原生 Claude、原生 Codex、AHP 及 API／MCP 成功條件；只完成一部分就明確標示。

**增量 C：PM 決策到維護。** 真實驗收更新目標與依賴，維護修復、週報與重新規劃回到同一套畫面。Workbench 的預設 rollout 依其導航、核心兩輪、恢復與相容性門檻，可先對已就緒 workspace 啟用，不必等待所有 PM／維護功能齊全；Classic 只作有限期回退。

實際日期與人力費用由團隊在差額盤點後估算，不在未知容量下承諾一週做完。開始實作前只需一次集中確認互動與風險邊界；間距、元件細節與非破壞重構由 integrator 決定。真實驗收環境尚未指定時，不阻擋唯讀、fixture 與契約工作，但不可虛報 live 完成。

## 16｜體驗 KPI 與 16 項整合驗收

### 16.1 成功指標（目標，不是目前成績）

| 指標 | 目標與量測 |
|---|---|
| 基本兩輪工作的外部工具切換 | 已就緒 workspace 下為 0；主動深入 IDE 不算被迫切換 |
| 背景重填／內部 ID | 第二句不重貼 repo／spec／session；一般路徑手填內部 ID 為 0 |
| 啟動理解 | 至少 3 位非開發者在就緒環境自行開始小工作，記錄提示次數與時間；模型時間另列 |
| 可辨識狀態 | 測試者能區分 queued、started、turn ended、verified、accepted、deployed |
| UI 反應 | 沿原計畫，本地 pending p95 ≤500ms；不冒充 server 已受理 |
| 事件更新 | 50 sessions／5 active streams 基準，collector 接受至 UI p95 ≤2s；記錄機器與poll週期 |
| PM 價值 | 不讀 raw logs 即可回答目前目標、主要阻礙、可信成果與一項下一步 |
| 安全與恢復 | 外部 sessions 收到的控制 mutation 為 0；測試情境不重複開工／重送 |

首次乾淨安裝、原生 CLI 登入與每日已就緒流程分開量測，不拿已配置 demo 掩蓋 onboarding 成本。另測 200 Work 清單、長中文標題、輸入法、高頻輸出和多 attempt。

### 16.2 16 項關鍵情境

K01–K16 是既有驗收的執行索引，不替代 v1.3 A01–A40 或 UX-A01–UX-A48。P＝原型／fixture；I＝真正 Connector 整合；L＝真實指定 BAT／CLI／GitHub。P 通過不能頂替 L。

| 編號／層級 | 情境 | 必要結果 |
|---|---|---|
| K01 P/I | Idea 長談、存筆記、貼 repo | 不自動 coding；reply／note 清楚；候選不成既定 scope |
| K02 I/L | 一次設定與修復 PM/profile | 只問缺口；回原草稿；能力與成本正確 |
| K03 I/L | 短計畫與採用並開始 | 直接進 Work；package 不需人工 API 補建 |
| K04 I/L | 採用成功、start 失敗／ACK 遺失 | 原計畫保留、查原 key，不重複開始 |
| K05 I/L | busy 時送第二句後關 client | server queue 繼續，下一輪送一次；本地草稿不假装排入 |
| K06 I/L | queue 等待時撤銷 writer 權限 | 出隊重核並停止新增副作用，合法歷史可讀 |
| K07 I/L | 看 diff、驗證、建立 PR | 固定 Result 可追溯；開 PR 不誤標需求已驗收 |
| K08 P/I | Work／Lineage／Live 切換 | 同選取、同草稿、同 result；無新 writer/listener |
| K09 I/L | 人工和 Hermes 工作同時存在 | 不碰其他 controller；僅明確 derive／inbox |
| K10 I/L | 原生歷史雙重收集 | 可信 alias 去重；無法歸因處保留部分／未知 |
| K11 I/L | 原生 Claude／Codex／AHP route | 各自驗證能力，未完成 route 不掛完整支援標籤 |
| K12 I/L | MCP 派工與 UI 查看 | 同一 Work／Attempt／Result、可用動作與來源權限 |
| K13 I/L | PM assessment 更新依賴 | 同一結果更新六視圖與後續工作，非手填完成 |
| K14 I/L | 發布後維護、暫停與重開 | 固定版本，修復走自有 Work；不自行開新 scope |
| K15 P/I | 撤權、舊 response、中文 IME、mobile | 不洩漏、不搶焦點、不誤送；草稿與閱讀位置可用 |
| K16 I/L | 安裝更新／UI回退與負載 | 不重播業務動作、不丟舊 links；版本與性能證據可核對 |

### 16.3 一段必交的實機演示

保留一個人工 BAT session 正在工作；Dashboard 要求修復登入重複送出，採用並開始，讀到真實輸出；再補失敗重試案例，關掉 client 後讓 server 處理已排入的指令；重連看同一 Work 的固定成果與 PR；Hermes 從 MCP 另開一件工作；PM 說明兩份成果對需求與 milestone 的影響。人工與其他 controller 的原 session 全程無控制操作。

交付包含候選 SHA、frontend/backend/runtime 版本、資料性質、operation／result refs、連續錄影與故障記錄。不在演示前手動植入 package、已驗收結論或假 PR，然後稱為完整閉環。

## 17｜風險、範圍控制與發布策略

| 風險 | 控制與退出條件 |
|---|---|
| 持續寫計畫／改設計，核心路徑一直未實機 | 本輪沿既有計畫落地；先交增量 A，非关键新需求延後 |
| 為少點一下取消後端安全檢查 | 組合操作與自動補上下文，保留每步授權及前置條件 |
| 只看主線綠燈就稱 ready | 分別回報 coded、merged、tested、packaged、installed、live |
| 引用舊PR標題誤判狀態 | 以固定 SHA 原始碼＋當前 metadata 核對；更新既有追蹤表 |
| 新舊 UI／demo 長期分叉 | 共用 source，逐能力開放；達門檻後預設新工作台，明定舊殼退場條件 |
| 全 CLI 同時開工，沒有一條完整 | BAT 垂直切片先，native/AHP 契約可平行；逐 route 提證據 |
| PM 誤判或產生假完成 | 確定事實由程式，推論有來源與版本；不足就unknown，不假填 |
| 權限很嚴造成日常全是確認 | 決策按風險與政策集中，讀取與確定性回填不重複請示 |
| 他人程式／素材授權不明 | 先獨立實作互動；移植前確認範圍、license 與 notice |
| Client 故障／更新破壞工作 | 回退只改 UI，不刪 journal、不重排 queue、不重啟外部 session |

本企劃不添加計費系統、Hub importer、跨 server PM 合併、完整 IDE／PTY、任意 DAG 編輯器或外部 PM 雙向同步。需要它們時另作價值評估，不作此次使用體驗的前置條件。

Cantus 公開版標示 `UNLICENSED`，與 The Unlicense 不同。可以參考已看到的互動並獨立實作，但直接整合其程式或素材前應取得明確許可；第三方元件獨立檢查授權。Project Hub 參考公開 MIT 程式時仍需保留相應 notice，新版完成品不可推定沿用相同條件。本包不含競品程式碼或素材。[S8][S10]

候選發行先對已就緒的試用 workspace 開新工作台，保留相同 IDs／深連結及短期 Classic 回退。Schema 變更先備份與相容性檢查；新舊殼共讀同一業務狀態，不 dual-write。回退 UI 不等於恢復旧採用計畫或重新發動已排工作。

## 18｜採用決議與開工指令

建議採用本企劃的體驗與邊界作為 v1.3／UX 補充的整合決議：保留可靠底座，將專業管理能力整理成日常可用的工作台。這份文件交付不等於批准 merge、部署、R0 或實機驗收。

團隊第一批交付應是：一份最新 main／PR 差額圖、一條使用正式共用元件的完整工作路徑，以及具名的首條實機 profile／repo。已完成 Workbench 和 Result 不重做；把已整合的第二輪／採用／結果在同一版本實機驗證，不重寫；剩餘T5／T6缺口沿原分工推進。

使用者只需確認明顯的產品取捨與真實環境授權。工程接線、可讀性微調、非破壞重構由指定 integrator 負責。各 PR 用「哪個使用者步驟現在能完整完成」回報，而不是以新增幾百行或通過幾輪文件審閱回報成功。

> **給 Dev 的一句話：以我們的 engine 承擔複雜，以借鏡而來的互動降低負擔。讓人說了就能做、做完能接著改、換視角仍在同一件工作；PM 同步說清楚是否正朝原本的目標前進。**

---

## 附錄 A｜來源與版本

S0–S10 為 EC-1.0 的歷史基線，不能用其舊狀態判斷本輪完成度；EC-1.1 的現況依 S11–S22。本文尺寸、工作分配與 KPI 是產品目標。開工時再核對固定候選及最新 metadata。

- **[S0] 既有規劃基線。** 使用者本對話已提供 `Work-Centric-Workbench-Plan-zh-TW.md`、`Dev-Handoff-zh-TW.md`、`Acceptance-Checklist-zh-TW.md`；以及 repo 的 `docs/product/v1.3-closed-loop-plan.md`。原 E／UX／驗收編號全部保留。
- **[S1] 本次主線。** https://github.com/TED-H-com/bat-agent-connector/commit/87433260f5b12a2b323c50d9c04260d5cd0d9293
- **[S2] Workbench 整合契約。** https://github.com/TED-H-com/bat-agent-connector/blob/87433260f5b12a2b323c50d9c04260d5cd0d9293/docs/design/workbench.md
- **[S3] 現有產品進度。** https://github.com/TED-H-com/bat-agent-connector/blob/87433260f5b12a2b323c50d9c04260d5cd0d9293/docs/product/progress-2026-10-16.md （該報告自己的產品 pin 為 265026d4；不把其舊「未合併」清單當所有 PR 的即時狀態。）
- **[S4] Work 第二輪。** https://github.com/TED-H-com/bat-agent-connector/pull/209 （EC-1.0 歷史查核：當時 head 23e17e76 為 draft；EC-1.1 的合併狀態改依 [S17–S20]。）
- **[S5] 採用並開始。** https://github.com/TED-H-com/bat-agent-connector/pull/215 （EC-1.0 歷史查核：當時 head 23f6d42f 為 draft 規格；後續真正實作已由 #276 整合。）
- **[S6] Project Hub 公開工作流程。** https://github.com/kieiken/project-hub/blob/a277d2ed5ce439c248fc32fa8ff9fa4124a06027/hub/README.zh-TW.md
- **[S7] Project Hub 4.123.1 公告。** https://github.com/kieiken/project-hub/releases/tag/v4.123.1
- **[S8] Cantus 分享版與限制。** https://github.com/arumwu/cantus-panel/blob/4343c6eef7090a6a9ad34841fea07ee18e73890d/README.md ；先前核對的執行／queue 實作可見 https://github.com/arumwu/cantus-panel/blob/7c9456f779d44bb7d8d593f77743125627d2b217/native-ui.js 與同版 `native.cjs`、`managed.cjs`、`handoff.cjs`。
- **[S9] W3C WCAG 2.2。** https://www.w3.org/TR/WCAG22/ （對比、重排、鍵盤、焦點、Target size 依適用條件驗證，不以截圖宣稱認證。）
- **[S10] GitHub 授權說明／Hub 公開授權。** https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository ；https://github.com/kieiken/project-hub/blob/a277d2ed5ce439c248fc32fa8ff9fa4124a06027/LICENSE

## 附錄 B｜本包與舊文件的使用方式

本企劃 EC-1.1 是 v1.3 與 Work-Centric 的收斂決議，開工時將優先順序、參考來源與本次補充行為整合回既有產品文件；不要永久維護互相衝突的三份規格。未明確修訂的生命週期、控制者、證據與原驗收保持有效。

[現行 agent handoff](v1.3-agent-handoff.md) 是簡短開工入口，不另定規格。Word 與 Markdown 是本企劃相同版本的閱讀／維護格式；Git 中以 Markdown 為建議的編輯來源。本包不附競品程式或未獲公開授權的參考圖，可沿用先前由使用者提供、供內部評審的圖片與導讀。


## 附錄 C｜EC-1.1 新查核來源

- **[S11] Cantus 手機操作與驗證邊界**：https://github.com/arumwu/cantus-panel/blob/496dc178ac8c32d6ae01c9c77a9c0c2dc8714e36/README.md
- **[S12] Cantus 手機導航與可視高度實作**：https://github.com/arumwu/cantus-panel/blob/496dc178ac8c32d6ae01c9c77a9c0c2dc8714e36/app.js
- **[S13] Cantus 手機瀏覽器測試**：https://github.com/arumwu/cantus-panel/blob/496dc178ac8c32d6ae01c9c77a9c0c2dc8714e36/cloud/mobile-ui.test.cjs
- **[S14] Project Hub 4.124.0 發布及 source 更新**：https://github.com/kieiken/project-hub/releases/tag/v4.124.0
- **[S15] Project Hub 本輪變更**：https://github.com/kieiken/project-hub/blob/e5e1696a7a8df930b0693bbd8418696a15c52585/hub/CHANGELOG.md
- **[S16] Project Hub 長對話整理與下一步**：https://github.com/kieiken/project-hub/blob/e5e1696a7a8df930b0693bbd8418696a15c52585/hub/README.md
- **[S17] BAT Work loop 主線整合 #276**：https://github.com/TED-H-com/bat-agent-connector/pull/276
- **[S18] 我們本輪實作與產品行為**：https://github.com/TED-H-com/bat-agent-connector/blob/f31e7964c627e4b6d3af2e2370e7a38c64a20e25/CHANGELOG.md
- **[S19] Work 下一輪前端**：https://github.com/TED-H-com/bat-agent-connector/blob/f31e7964c627e4b6d3af2e2370e7a38c64a20e25/desktop/src/pm-work-follow-up.js
- **[S20] 目前整合追蹤；以當次 metadata 為準**：https://github.com/TED-H-com/bat-agent-connector/issues/182
- **[S21] 本輪 handoff；早期段落保留歷史基線**：https://github.com/TED-H-com/bat-agent-connector/blob/f31e7964c627e4b6d3af2e2370e7a38c64a20e25/docs/product/v1.3-agent-handoff.md
- **[S22] Cantus 原生來源自動重讀的測試**：https://github.com/arumwu/cantus-panel/blob/496dc178ac8c32d6ae01c9c77a9c0c2dc8714e36/catalog-refresh.test.cjs

EC-1.1 的詳細差額、mobile行為、優先順序與原K驗收擴充，見同包 `experience-convergence-delta.md`。該檔與本企劃一起整合回repo；不要永久保留互相矛盾的產品指令。
