# PM v1.2 開發狀態

2026-10-23：[最新主線進度](progress-2026-10-23.md)記錄至 `eb1e087e` 的 51 個新增合併 PR，
並列出 Fleet Apply 與本機參與者尚缺的能力。沒有新發布；預覽版仍為 Connector 0.2.13／desktop 0.1.9
（`437ee6a7`），穩定版仍為 0.2.12／0.1.8。新增合併均未發布、安裝或實機驗收；下方保留歷史紀錄。

October 23: the [latest main progress](progress-2026-10-23.md) records 51 additional merged PRs through
`eb1e087e` and the remaining Fleet Apply and native-participant capabilities. There is no new release:
preview remains Connector 0.2.13 / desktop 0.1.9 (`437ee6a7`), stable 0.2.12 / 0.1.8.
The additional merges are unreleased, uninstalled and not live-tested; the entries below remain historical.

2026-10-22：[最新進度與發布紀錄](progress-2026-10-22.md)列出主線至 `793c6541` 的合併內容。
已發布預覽版為 Connector 0.2.13／desktop 0.1.9（`437ee6a7`），穩定版仍為 0.2.12／0.1.8。
#399–#403 不在預覽版內；本預覽版尚未安裝或實機驗收。下方日期紀錄保留歷史狀態。

October 22: the [latest progress and publication note](progress-2026-10-22.md) records merges through
`793c6541`. The published preview is Connector 0.2.13 / desktop 0.1.9 (`437ee6a7`);
stable remains 0.2.12 / 0.1.8. The preview excludes #399–#403 and has not been installed or live-tested.
The dated entries below retain their historical status.

已合併發布來源 `14c62830`（截至 #314，2026 年 10 月 19 日）支援建立 Work、採用並開始已審閱計畫、對同一工作追加第二句、讀取固定成果與差異、執行已設定驗證、從同一成果發布 PR，以及選兩份既有評估帶入 PM 討論。工作設定檔、readiness 與 API／MCP 對等也已納入。整合測試使用真正 Connector、磁碟 Git 與 mock BAT／providers；安裝與真實 BAT／模型驗收仍須另有證據。

完整範圍仍依 [PM v1.2](pm-v1.2-plan.md)、[v1.3](v1.3-closed-loop-plan.md) 與 [EC-1.1](experience-convergence.md)。Python Connector／Task Service 是唯一中央權威，Web／Tauri 共用 `desktop/src`。
[最新發布與接續狀態](implementation-status.md#2026-10-19-發布與接續狀態)區分 0.2.12 發布與歷史安裝回執；[10 月 18 日進度報告](progress-2026-10-18-delivery.md)保留前次交付證據。

## EC-1.1 接續

2026-10-19：實際部署回饋已將下一輪改為
[實機穩定化與首次使用](experience-convergence.md#2026-10-19-優先順序實機穩定化與首次使用)。
新增功能暫緩，先驗候選包、加入機器、狀態一致與恢復；W01–W52 的完整範圍保留。
日常導覽簡化只能算 UI 增量，不能作為 Fleet 或兩個工作日穩定性通過的證據。

Workbench 新增手機全螢幕工作清單，返回時保留搜尋與位置，成果面板支援瀏覽器上一頁／下一頁。Classic 仍為預設。#285 與 #287–291 的 CI、發布與 Fleet 讀回已完成。Discussion 面板保留上一頁／下一頁位置，回到前景先核對 server 狀態，手機輸入區配合可視畫面。工作設定檔在開始前顯示該路線的下一輪限制；PM 可明確建立有來源的接續摘要，供後續討論沿用。#292／#293 與 #300 已納入本版套件。總覽可明確請 PM 建立附固定來源與歷史的判讀；資源頁可讀取另行授權的本機 Codex／Claude 對話檔案，不取得控制權。清單先載入摘要；尚未送出的佇列指令可以留成 Idea。真實 PM 品質與跨 CLI 完整能力仍待驗收或實作。

已整合工作循環轉入具名真實路線驗收；手機導航／恢復、低干擾讀取、長討論與 PM 下一步沿原工作擴充。owner 同日要求 Connector 自動更新，納入 T8／E11／K16，見[更新契約](../design/connector-updates.md)。Connector 自動更新已由 #282 實作、發布並完成部署讀回，見[最新發布狀態](implementation-status.md)。其餘 EC-1.1 文件採用不代表新功能或實體手機驗收完成。

## M4 已合併主線（#116）

M4 已於 2026-10-15（UTC）以 #116（`d04eed37`）合併至主線。
M4 為每個原完成回合留下生命週期證據，並重新查核清冊；已採用的維護政策因此可以繼續，保留中的工作階段與工作樹不受影響。
Roadmap 與 Brief 使用同一份固定的需求與完成定義評估。遺失回覆時沿用原 key 查回，不會再送出第二次輸入。
版本：Connector 0.2.6、desktop 0.1.2。不包含 M5（工作區 runtime 選擇與人工序列輸入觀察）。

[10 月 13 日候選紀錄](../handoff/2026-10-13-m4-refresh.md)保留合併前的量測與失敗。#116 合併前 CI 全部通過，包括四個原生建置；當時安裝與實機驗收尚未進行。最新發布與安裝狀態見下表。

## 目前整合狀態

| 層級 | 狀態 |
| --- | --- |
| 發布（published） | `v0.2.12`，固定來源 `14c62830`；Connector 0.2.12／desktop 0.1.8。前版 `v0.2.11` 固定於 `d7876d64`；兩版發布及其回執分開保留。 |
| 主線（merged） | 截至 #314；#306 已整合工作區 PM runtime 與人工序列輸入觀察，#309–313 補安裝／設定／automation，#314 修正 Windows SSH 路徑比對。原 Work loop 與 Connector 自動更新已合併，不重做。 |
| 安裝／原生（installed/native） | 歷史讀回為七台 Connector 0.2.10、三台 Linux desktop 0.1.6，既有 MCP 程序保留。本次更新未取得 0.2.11／0.2.12 的逐主機新回執，不能把發布版本當作目前安裝版本。前版原生失敗及一般登入驗收缺口仍保留。 |
| 實機（live-tested） | 本輪沒有真實 BAT／provider／模型完整驗收回執；軟體部署與綠燈 CI 不代替實機驗收。 |

### 主線現在的操作

- **總覽／報告／需求（#151、#158）：** 即時進度指出尚缺的驗收證據，提供查看需求與 PM 討論入口。
  固定報告另讀，可比較版本；需求詳情分開條件、關聯工作、成果、決策與交付。
  資料未變的交付讀取保留需求列與焦點，不因回應順序跳位。
- **討論（#154）：** 可搜尋標題與脈絡；來源標籤、自動標題與筆記保留。
  討論資料按需開啟脈絡、資訊與計畫；提案審閱與採用沿用 Connector 授權。
- **問題與變更／工作看板（#157）：** 篩選列、具名發現、原時間線與共用詳情已合併。
  看板可搜尋、依需求或 Agent 篩選；內容核准、需求驗收、合併與部署分開。
- **治理／會議／後續追蹤（#147）：** 原紀錄、來源、結果與恢復操作按需展開。
  會議類討論的資料面板有會議分頁；後續追蹤放在問題與變更。
- **維護（#150、#161）：** 技術相依提供發布基準與維護政策；問題與變更提供維護修復及審閱。
  發布來源先預覽再登錄，背景預覽保留理由欄文字與焦點；登錄不是發布或部署。
- **工程工具（#149）：** 需求的更多操作開啟品質、成果證據、規格、工程工作包、交接與議題發布；
  工作看板的更多操作開啟工作批次。原草稿與操作識別不變。
- **首次使用（#153）：** 準備開始工作先展開「1. 連線至 BAT」。貼上 BAT 連線網址後核對主機代號，
  再按「驗證並儲存主機」；成功後按「查看工作階段」。儲存庫與驗證命令需要時再展開。
  結果未知時保留原請求，使用「查回請求」。
- **獨立工作目錄／執行詳情（#156）：** 工作目錄、檢查點、執行與驗證使用共用摘要、詳情與原操作入口。
  不改資源歸屬或原請求；人的 BAT 工作階段與工作目錄仍唯讀。
- **工作階段狀態與歷史（#152）：** 工作階段只在需要處理時顯示狀態。歷史不完整時說明原因：達到 3,000 則訊息上限以一行淡色文字提示，封存結尾未確認或訊息缺少 ID 則列在詳細資料。
- **外殼與匯出（#155）：** 匯出簡報開啟匯出工作並移入焦點；變更階段須明確選擇；工作區 ID 移到詳細資料；主要資料缺少時，檢視不再被 Sprint、觀察或政策面板取代。
- **失敗訊息與會議（#162）：** 十五個 PM 面板、總覽報告與技術相依每次失敗只顯示一則在地化訊息，錯誤代碼收在詳細資料；會議在缺少 Sprint 時說明前提。
- **版本與診斷匯出（#171）：** 設定 → 關於顯示確切版本與 commit；匯出診斷資訊只存成已遮蔽敏感資訊的本機檔案，不會上傳。
- **工作階段路徑與代理（#169）：** 唯讀工作階段列出三條路徑：在 BAT 回覆、從已審閱的檢查點接續，或由 MCP 派工。啟動與派工以收合區塊說明代管工作。設定 → 代理提供唯讀的 PM 資格檢查，並依安裝方式說明 MCP 設定，可返回原討論。
- **PM 視圖與 Follow-up（#174）：** PM 工作區只讀取開啟中的視圖，其他視圖與面板在開啟時才讀取；Follow-up 請 PM 評估過去在實際 Connector 上必定失敗，現已修正。
- **失敗訊息與建立表單（#175）：** #162 未涵蓋的工作區重新整理、生命週期、已採用計畫預覽與報告匯出，失敗時也只顯示一則在地化訊息；建立表單在需要時才開啟；已結束的 PM 請求只留下「新請求」。
- **Git 傳輸合約（#167）：** 定義 Git transport 合約，本機 Git 仍回報無法使用。
- **組織遷移與連結（#177）：** 連結、專案頁面與更新 feed 指向 TED-H-com。
- **總覽文案與欄位（#179）：** 繁中總覽文案、阻擋原因用詞與單行負責人欄位修正。
- **介面細節與首次執行（#180）：** 手機版頁首、工作階段首次執行、訊息讀取失敗只顯示一則錯誤、緊湊列與繁中權限名詞。
- **需要關注與原紀錄（#185）：** 總覽的「需要關注」與「問題與變更」開啟原紀錄，顯示誠實進度與一個主要動作。
- **其餘面板的錯誤（#186）：** 失敗時只顯示一則在地化摘要，錯誤代碼收在收合的詳細資料。
- **對話中的提案（#188、#189）：** 提案在對話裡審閱、採用與解決，並在每個子操作完成時自行結算。
- **討論撰寫（#192）：** 一般 PM 回覆用「傳送給 PM」；另有「只存筆記」與「整理計畫」。
- **互動展示（#191）：** 網站連結到由共用 Dashboard 原始碼建置的展示，資料為合成且唯讀。
- **工作台預覽（#193、#195）：** 工具裡的可還原工作導向版面（今天、專案、工作、資源），含工作、譜系與即時檢視、舊深連結與功能地圖。經典版面仍是預設。
- **開始工作（#194、#197）：** `work.start` 從已審閱的已發布 commit 與已採用計畫，在自己的受管 BAT worktree 與工作階段開始已綁定的工作。工作詳情的「開始工作」面板顯示就緒事實、目標與需求選擇、過期預覽防護與嘗試時間線。它需要 `work.start` 能力、已設定的 BAT 主機與已綁定的儲存庫。
- **成果讀取（#196、#205）：** 已開始的工作嘗試有固定成果讀取。看板工作詳情的成果區（E03 part A）只讀取，不開始、不送出、不合併。公開圖庫沒有打開這個面板。
- **焦點、捲動與重新整理（#199、#202、#204、#208、#210）：** 檢視器載入時焦點留在檢視器內；「再檢查一次」與 PM 表單送出鈕在工作中保持焦點。讀取途中離開時，看板與需求保留捲動位置。專案在重新整理途中切換時，重新整理不會一直停用。
- **v1.3 文件與原型（#183、#187、#190）：** 文件與可點選原型已合併。原型維持合成，不向 Connector、BAT、GitHub 或模型發出請求。#198、#200、#201 是測試可靠度修正，不是功能。#184 是前一次文件 pin 更新。

既有 #132／#134／#139 提供總覽、討論來源、登記、時程、相依、每日資源桶與發現身分。
#133 合併工作台與 [walkthrough](../../desktop/tests/pm-walkthrough.mjs)；後續原始 walkthrough 失敗與修正紀錄另存，受控測試不代替真人驗收。
本機 Codex／Claude collector 與來源配額窗已合併。遠端收集、交付效率歸因與預測仍待完成。
缺資料保留未知；來源撤回時移除相關列、計數與詳情，晚到回覆不能復活資料。

### 尚待完成

第 7 輪已合併項目不再列為未實作；Shell、工作階段狀態、失敗訊息、版本診斷、唯讀路徑、PM 檢視按需載入、表單按需開啟及 Git transport 合約已合併。
#174、#175、#167、#177、#179 與 #180 已合併至主線。
[#141](https://github.com/TED-H-com/bat-agent-connector/issues/141) 的連線網址首次使用已由 #153 合併，工作階段狀態、診斷與路徑亦已隨 #152、#171、#169 合併；
[#142 本機主機設計](../design/local-host.md)的本機 Git 尚未實作；#167 的 Git transport 合約已合併，本機 Git 仍回報無法使用。
[#143](https://github.com/TED-H-com/bat-agent-connector/issues/143) 的 PM 重點判讀已由 #300 合併並納入本版套件：
固定總覽依據、既有 native／BAT 分析、不可變判讀、來源撤回、受理限制及雙語卡片／詳情／歷史。
實作沿用[既定契約](../design/pm-overview-assessment.md)的保守預設；模型判讀必須明確啟動，
不會自動採用或執行建議。已合併、發布及安裝；尚未進行真實 PM 品質驗收，不能視為整個 PM 計畫完成。

完整[採用計畫](pm-v1.2-plan.md)的 W01–W52、A01–A64、M0–M6 保留。
M5 保留[歷史來源](progress-2026-10-12.md)（M4 已隨 #116 合併至主線），不能由本輪 UI 推定完成。
下表的候選敘述沿用其原歷史範圍，不是主線已提供的功能。

## 後續實作與發布界線

後續 W24 工作區 PM 路線選擇由 [#302](https://github.com/TED-H-com/bat-agent-connector/pull/302) 提供；W45 人工序列輸入觀察由 [#303](https://github.com/TED-H-com/bat-agent-connector/pull/303) 提供。兩項均不在本次固定 0.2.10／0.1.6 套件內；本段不宣稱已安裝或實機通過。

## 工作與剩餘範圍

下表依固定來源歸類；「主線」表示程式已合併，不代表所有驗收條件或實際部署完成。
各行保留原計畫優先級及編號，驗證數字見進度報告。
視圖以 `265026d4` 為準，包含 #133、10 月 14 日的 UI 更新與後續合併至 #205；Connector 合約沿用 #132／#134／#139。
安裝版／原生及實機驗收仍需各自留證。

| 工作 | 優先級 | 原計畫項目 | 目前狀態／尚缺 |
| --- | --- | --- | --- |
| W01 | P0 | Workspace identity 與 scope migration | 主線：workspace identity、membership 與 scope；舊 project 仍需明確綁定，不依 repo 名稱合併。 |
| W02 | P0 | 全入口 controller policy | 主線：原 controller／incarnation、全入口與最後執行檢查；人工資源不接管。 |
| W03 | P0 | Operation request key 與查回 | 主線：原 key、Git／journal 查回及取消；已送出未知結果保留，不另發一份。 |
| W04 | P0 | BAT capability adapter | 主線：專用 Linux BAT PM profile；owner fork producer 已合併、固定實包 fixture 通過，未宣稱已安裝或其他 CLI 可用。 |
| W05 | P0 | Evidence IDs 與 replay 去重 | 主線：固定來源／digest／journal cursor、快照與去重；#113 接上明確品質／需求接受，證據收集仍不等於驗收。 |
| W06 | P0 | Session worktree work 關聯 | 主線：project／work、固定 plan／requirement 與 execution 關聯；來源讀取不轉移控制權。 |
| W07 | P0 | PM worktree bootstrap | 主線：PM 自有 carrier、固定 commit、不可覆寫草稿與文件採用，不夾帶程式碼修改。 |
| W08 | P0 | Plan revision 與採用流程 | 主線：版本、來源、accepted CAS、理由、scope revision 與 Git 證據；採用不直接授權執行。 |
| W09 | P1 | PM runner 與 adapter selection | 主線 #115：按操作選 native／BAT 有界分析、計畫提案與 Follow-up；持久 workspace 選擇見 W24。 |
| W10 | P1 | 一位 PM 討論與 evidence snapshot | 主線：固定脈絡、來源快照、Idea／Planning／Follow-up 分析與品質紀錄。#132 已合併有序 context、來源撤回、durable rename 與中央提案可採用性；#134 已合併決策流程後端；#133 已合併前端及 walkthrough；更完整專業會議主持仍待做。 |
| W11 | P1 | Plan diff 與影響分析 | 主線：固定版本／來源／條件差異及影響候選；#113 提供候選更新及整組採用，完整語意判讀待強化。 |
| W12 | P1 | Project Brief | 主線：固定 Project Brief 與選定品質評估；M4 #116 已補條件偏差及同快照說明。即時 overview、可見需求進度／待決／偏差與涵蓋標記已由 #132 合併；#134 已合併採用後目標與近期決策；#133 已合併即時／報告介面。固定報告不混入即時數字，見 [API 合約](../design/pm-overview-discussion-context.md)。 |
| W13 | P1 | Roadmap 與 scope | 主線：固定里程碑、需求與時程證據。#132 已合併唯讀時程投影（[合約](../design/pm-schedule.md)）；不推算預測或 critical path；#133 已合併甘特圖。M4 #116 已補採用需求品質與涵蓋缺口投影；完整 Git materialization 仍待做。 |
| W14 | P1 | Kanban | 主線：工作／執行狀態、WIP、固定品質投影及依賴證據；M4 補原 runtime 完成；完整 WIP 政策待做。 |
| W15 | P1 | Sprint artifacts 與時間 | 主線：Sprint 承諾、固定 DoD／接受條件及原成果品質；完整 Scrum 事件與引導待做。 |
| W16 | P1 | Planning Review Retro | 主線：Planning／Review／Retro 的固定討論、結果與改善追蹤；Daily Scrum／LLM 會議主持待做。 |
| W17 | P1 | 風險、變更與決策 | 主線：風險、變更、決策、固定來源、明確根因去重與責任。#132 已合併登記讀取投影（`pm_register_list`／`pm_register_get`）的列、完整可見掃描合計與明確連結時間線。#134 已合併採用後問題結案引導；#133 已合併篩選表與時間線及 #139 的具名發現；結案不代表需求驗收，自動跨快照追蹤／提醒仍待做。 |
| W18 | P1 | GitHub issue mapping | 主線：明確選取公開內容、穩定 Issue 關聯、追加版本及原操作查回；完整外部同步、多對多、明確重開待做。 |
| W19 | P1 | Orchestrator readiness 與派工 | 主線：固定 package 經既有交接派工；原接受證據可滿足明確依賴；保留最後來源與權限檢查。 |
| W20 | P1 | 現有 integration 與 merge 接入 | 主線：integration preview／apply／conflict handoff／PR merge 接原 package 與 lineage；merge receipt 不代替需求驗收。 |
| W21 | P1 | 現有 deploy 與 receipt 接入 | 主線：deploy／rollback／merge-and-deploy、固定 release receipt 與交付投影；未宣稱 owner 實際部署。 |
| W22 | P1 | AHP observer 與 create-own | 主線 #114：固定 AHP 1.0 canonical subset／VS Code 0.10 唯讀 observer；create-own、寫入及 live host 未驗證。 |
| W23 | P1 | 第一個 native CLI adapter | 仍待完成一般 native coding adapter；現有 Linux 有界 PM 分析不能代替 coding／隔離 canaries。 |
| W24 | P1 | PM adapter 切換 | #302 增量：持久 workspace native／BAT 分析選擇、原文件與請求恢復，涵蓋總覽與接續摘要；[契約](../design/pm-workspace-adapter.md)。不在固定 0.2.10 套件，真實切換與完整 M5 矩陣另驗。 |
| W25 | P1 | Fleet readiness 與安裝版 | 主線 #115：core 0.2.5／desktop 0.1.1 平台 CI 及原實包升級通過；M4 #116 最終 CI 已通過；本次 0.2.10／0.1.6 精確 main CI、套件及七台 Connector／三台 Linux 桌面讀回完成，實機條件另列。 |
| W26 | P1 | Canonical MCP skills | 主線：PM read tools、註冊 operations 與共用 skills；後續契約需同版生成及能力揭露。 |
| W27 | P1 | 端到端實機驗收 | 受控自動 fixtures 已有；owner 真人硬體驗收另行執行，未宣稱通過。 |
| W28 | P2 | 其餘具名 CLI 能力 | 其他具名 CLI 的能力與版本逐一接入，尚未宣稱全面支援。 |
| W29 | P2 | 額外 reviewer 與討論模板 | 額外 reviewer、專業討論模板與品質驗證仍待完成。 |
| W30 | P2（原計畫） | 成本與較完整預測 | 六視圖修訂將資源觀測列為 Agent 頁及 PM 資源安排的必要依賴；本機 Codex／Claude collector 與來源配額窗已於 #122 合併，每日資源桶已由 #132 合併；#133 已合併每日圖表及一致讀取。跨主機 collector、完整 quota adapters、合格交付／返工歸因、區間預測與自動模型政策仍待完成。 |
| W31 | P0 | Idea thread 與明確轉 Planning | 主線：持久 Idea thread、明確來源選擇及轉 Planning 草稿；可長期停留 Idea。 |
| W32 | P0 | PhaseProfile 與 command policy | 主線：九種 phase profiles 與 policy 契約；不能把 profile 定義視為所有階段 runner 已完成。 |
| W33 | P0 | InputRecord 與循序資訊取得 | 主線：有來源資訊、Follow-up、最多三個問題、自然對話候選與整組採用；完整研究 runner／其他階段引導待做。 |
| W34 | P0 | Thread plan change Sprint batch 狀態 | 主線：thread、plan、accepted scope、Sprint、治理、package 與 batch 分開保存；M4 驗證同帳本接續。 |
| W35 | P1 | 架構與 SDD 工程工作包 | 主線：固定需求／接受條件、規格 bytes、程式基底及工程安排；原驗證／品質／需求接受各自保留。 |
| W36 | P1 | OpenSpec read adapter | 主線：原 carrier commit 的 OpenSpec 文件唯讀來源；其他 commit、自訂 schema 與 CLI JSON 契約待做。 |
| W37 | P1 | OpenSpec 自有產物與 reconciliation | 主線：PM 自有 Git append-only 規格、immutable artifact、穩定 task ID 及刪除歷史；不宣稱 OpenSpec CLI 相容。 |
| W38 | P1 | 有界批次與工作包交接 | 主線：1–16 筆固定 handoff、有界並行／期限與 proven-unsent retry；M4 #116 已補原完成及容量觀察。 |
| W39 | P1 | DocumentSnapshot 與五視圖 SVG | 主線：DocumentSnapshot 與 Brief 分頁 SVG；其餘四視圖仍在未完成草稿，不得直接合入。 |
| W40 | P1 | 專案文件與簡報 | 主線：同一固定 Brief 的 Markdown／HTML／SVG 及雙語存檔；其他文件、可編輯簡報／播放／下載待做。 |
| W41 | P1 | Actor 決策責任與 follow-up | 主線：决策責任、改善追蹤、固定前次結果與有來源 Follow-up；完整主動主持及判斷品質待驗證。 |
| W42 | P1 | Idea 到 Review 真實驗收 | M4 755f126：真實磁碟 Git／驗證器及 mock providers 的會員案例，兩個 Python 版本完整 joined 通過；真人驗收另列。 |
| W43 | P0 | ProjectLifecycle 與 Idea 長期停留 | 主線：生命周期及有固定政策／基準／完整新鮮工作量證據的 Maintain gate；階段名稱不授權效果。 |
| W44 | P0 | ReleaseBaseline 與發布 receipt | 主線：明確 release／已驗證部署基準與原通道／commit／receipt；完整 registry adapter 待做。 |
| W45 | P0 | 閒置與 workload coverage | 主線已有中央工作量、來源涵蓋、M4 原完成與人工單次觀察；#303 增量補 2–64 次嚴格循序唯讀觀察，須完整 replay 與當前 inventory；[契約](../design/pm-execution-runtime-completion.md)。不在固定 0.2.10 套件，真實人工序列另驗。 |
| W46 | P1 | Maintainer profile 與政策執行 | 主線 #113：採用政策內的自有 repair、驗證／PR／交付上限、原完成與基準更新；M4 #116 完整串接及最終 CI 已通過；真實維護驗收另列。 |
| W47 | P1 | 依賴 Issue PR 維護來源 | 主線：有界 GitHub PR／checks／Actions、明確 Issue／Dependabot／alert 來源與固定 findings，#132 已合併 [固定 commit 相依清冊](../design/pm-dependencies.md)、宣告／撤回、CI hints 和有界 impact。#133 已合併矩陣／相依圖；安裝版／原生及實機驗收仍待執行。 |
| W48 | P0 | 維護排程與恢復 | 主線：有界到期檢查、週 Review、窗去重／漏跑合併、原 owner 憑證、容量／期限及原 children 查回。 |
| W49 | P1 | 每週 Maintenance Review 與改進研究 | 主線：固定週報、原 findings／repairs、前次決策、owner outcome、责任與雙語輸出；改進研究待做。 |
| W50 | P0 | 暫停 關閉與 reopen | 主線：pause／close／reopen、原 execution epoch、在途查回與保留；不復活舊佇列、不接管外部 session；M4 joined 有接續回歸。 |
| W51 | P1 | 維護 API MCP skills 與 UI | 主線：維護與品質使用同一註冊操作，Web／Tauri／API／MCP 共用；新能力隨候選更新。 |
| W52 | P1 | 完整生命週期實機驗收 | M4 755f126 同帳本完整生命週期本機兩版通過；M4 #116 最終 CI／四平台建置已通過；owner live 未執行。 |

## 文件及歷史

- [10 月 16 日進度報告](progress-2026-10-16.md)：本輪合併、閉環界線與檢查。
- [10 月 14 日進度報告](progress-2026-10-14.md)：前一份固定來源與當時證據。
- [10 月 12 日進度](progress-2026-10-12.md)：前次固定來源、Sprint 建議與歷史證據。
- [本輪交接](../handoff/2026-10-14-docs-ux-v2.md)：公開文件、截圖、檢查及接續步驟。
- [歷史交接](../handoff/2026-10-12-pm-v1.2.md)：M4／M5 與停放草稿的原始證據。
- [歷史實作紀錄](pm-v1.2-history-through-2026-10-12.md)：保留原批次敘述與當時證據，不覆蓋本頁。
- [安裝及 PM 試用](../getting-started.zh-TW.md)：實際產品操作與 runtime 前提。

## M4 原始失敗證據

整合前的 [M4 固定來源紀錄](https://github.com/TED-H-com/bat-agent-connector/blob/755f126330aded27b4685bdd9be7409d9248df5d/docs/product/pm-v1.2-status.md)保留每次原失敗、查詢量測與各版本結果。
本次更新不提高 deadline、不跳過測試，也不以重跑成功抹去原失敗。
