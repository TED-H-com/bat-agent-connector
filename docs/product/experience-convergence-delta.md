> 2026-10-23：[最新主線進度與未完成能力](progress-2026-10-23.md)，截至 `5f49e712`，共 55 個新增合併 PR；沒有新發布，新增合併均未安裝或實機驗收。下方保留既有決議及歷史紀錄。
> [Latest main progress and remaining capabilities](progress-2026-10-23.md), through `5f49e712`, covering 55 additional merged PRs; no new release, installation or live acceptance of the additional merges. Existing decisions and historical records remain below.

> 2026-10-22：[最新發布與未完成驗收](progress-2026-10-22.md)；下方保留既有決議及歷史紀錄。
> [Latest publication and remaining acceptance](progress-2026-10-22.md); the decisions and historical records below remain intact.

> 本附錄與[現行收斂企劃](experience-convergence.md)共同更新原工作，不新增平行 backlog。附件的「CI 執行中」是研究當時狀態；最新發布、安裝與未驗收範圍見[進度](progress-2026-10-18-delivery.md)。同日 owner 另要求的 Connector 自動更新歸入 T8／E11／K16，契約見[此處](../design/connector-updates.md)。

# EC-1.1｜24 小時更新研究與差額修訂
## 手機優先互動、低干擾同步與真實閉環驗收

**日期：2026-10-17｜產品負責人：使用者**\
**適用：Better Agent Dashboard，TED-H-com/bat-agent-connector**\
**文件性質：EC-1.0 的修訂附錄；不是 v1.4，不重置 E／UX／R／V／K 驗收。**

> 本輪的重點不是再造一個更大的架構。已合併的工作循環轉入候選驗收；將 Cantus 的手機操作，以及 Project Hub 的低干擾閱讀、長對話整理，接到現有 Tauri／Web、Workspace Service、API／MCP、journal 與 adapters。

## 1. 查核基準與證據層級

| 來源 | 固定版本 | 本次能確認的範圍 |
|---|---|---|
| 我們 | `f31e7964c627e4b6d3af2e2370e7a38c64a20e25` | 已含 #276 整合，以及 #277／#278 後續修正。不是本文件替實機完成背書。 |
| Cantus | `496dc178ac8c32d6ae01c9c77a9c0c2dc8714e36` | 觸控手機導航、composer、來源刷新、原生回合相關修正。 |
| Project Hub | `e5e1696a7a8df930b0693bbd8418696a15c52585`／4.124.0 | 公開 source 已更新；上版「仍停在 4.90」已過時。 |
| 舊企劃 | EC-1.0，2026-10-16，`87433260…` | 保留其架構、工作編號與未修訂需求；修訂的是現況、細部 UX 和交付順序。 |

查核依據包含原始碼、正式 README／CHANGELOG、PR metadata、測試內容與 CI 狀態。沒有在本輪登入真實模型、控制 BAT、操作 GitHub 寫入或測試實體手機。GitHub 網頁快取可能落後，以固定 SHA 與 connector 讀取的 metadata 為準。[S11–S21]

#276 整合候選 `bde3dcd0…` 的 CI、Desktop 與 Pages 檢查已通過；本次查到最新主線 `f31e7964…` 的 push CI／Desktop 仍在執行。前版綠燈不能代替後續 head。Cantus 的手機測試使用 Chromium 觸控模擬，縮短 viewport 是鍵盤模擬，不是實體 iOS Safari 鍵盤證據。[S13][S17]

## 2. 先把已完成工作從「待實作」移出

| 項目 | 本輪狀態 | 下一步，不重做 |
|---|---|---|
| `work.start`、固定 Result、工作成果區 | 原有基礎保留 | 回歸與同版本實機驗收。 |
| Work-target 下一輪與 server queue | #209 已併入 #276 | 驗證 client 關閉、重連、重複提交與角色撤回；不是再寫 queue。 |
| 採用並開始 | #215 是規格；真正實作、UI 及自動進 Work 由 #252／253／257 等整合 | 固定 source、scope、target，驗證部分成功，不把合併規格等同完成。 |
| Profile／readiness | 已整合已存 profile 選擇、版本釘選與返回草稿 | 首次設定、每條 runtime 能力與成本、手機呈現。 |
| Work／Attempt 導航、Tauri typed bridge | 已整合 | 檢查同身分跨 Web／desktop／mobile 連續性。 |
| Result／diff／驗證／PR | 正式路徑已整合，另有兩筆固定 assessment 開 PM 草稿 | 真實 BAT／provider／GitHub 證據、PM 實際判讀品質。 |
| T5：原生 CLI 歷史、第二條 execution route、AHP | 不因本輪 BAT 整合就算完成 | 按原 E06–E08 能力矩陣繼續。 |
| T6：目標影響、接受、依賴、維護 | 有既有 domain 能力及新 PM 來源入口 | 不把兩筆 assessment 草稿等同完整 PM 判讀與 maintenance 驗收。 |

[S17–S21] #182 的全範圍 checkbox 可以保留待驗收，但必須補上 coded／merged／integration-tested／live-tested 分層。後續 agents 不得根據舊段落的「尚未合併」重開同一項功能。

**目前限制仍重要：**這條 Work follow-up 對 Codex 的特定 BAT session 還有 `codex_uncorrelated_turn_boundary` 限制。這不是 Codex 產品本身不能接續。不能關掉邊界檢查來湊 demo；首個候選 profile 要事先驗證能完成兩輪。[S19]

## 3. 本次借鏡：採用、轉換、延後

| 觀察到的改善 | 決策 | 接回我們的方法 |
|---|---|---|
| Cantus 全螢幕手機清單／會话、底部導航、sheets | 現在細化 | 共用資料和 controller，新增自適應呈現，不 fork 業務 UI。 |
| Cantus 小而集中的輸入區、可見觸控操作 | 現在細化 | 同一 composer、同一 server queue；隱藏無意義 idle，不隱藏錯誤。 |
| Cantus 自動發現新會話、不強迫刷新 | 現在細化 | 利用既有 collector 及增量來源；不每個 client 重掃磁碟。 |
| Hub 可調側欄、精简模型顯示 | 小步納入 | 個人布局偏好＋可讀的 provider/model/profile，不改值或權限。 |
| Hub 清單只送摘要，選中工作才讀全量；减少重複 Git／檔案讀取 | 現在納入 | 讀取面優化，寫入 admission 維持新鮮檢查。 |
| Hub 整理長對話後指出下一步 | 接既有 PM | 持久摘要含固定來源與未決事項，原文可回看；不是新採用計畫。 |
| Hub 未送出指令退回 Idea 台帳 | 延後小功能 | 只處理證明尚未送出的 input；先安全取消，再以原 key 存 draft。 |
| Hub 模型混忙後一次交接 | 先做可見性，自動化延後 | 明確錯誤分類、預先許可的替代 profile、固定來源的新自有 attempt。 |
| 登入動畫、品牌標記、計費／授權更新機制 | 非本輪核心 | 不作驗收前置；不搬未授權素材、不新增授權系統。 |

Cantus 的 queue 仍依賴分頁；其原生續作與我們的「不接管外部 session」不同。Project Hub 的預設權限和自動 Git 保存也不能原封照搬。本輪只採用互動和讀取方法。[S11][S14–S16]

## 4. 手機：獨立互動規則，共用同一產品

### 4.1 導航層級

主層維持「今天／專案／工作／資源」。可以用底部四入口，工作頁內才顯示 Work／Lineage／Live 的局部視角。不要把四個全域入口與三個工作視角攤成七個底部按鈕。正在長談的 Idea 不必顯示空的 Lineage／Live。

手機依序是全螢幕清單、全螢幕對話或工作、按需 sheet。返回、瀏覽器 Back／Forward、關閉 sheet 和回到工作是不同層次；同一按鈕只處理最近一層。側邊與清單的捲動位置、選取和 query 要恢復，不要求重新搜尋。

頂端持續顯示短工作名和目前 workspace／server；長標題可以截短並提供完整可讀名稱。工作區切換器與「更多」有不同目的，不把使用者送回大型設定頁。橫向的觸控手機仍採可用的小螢幕布局，不因寬度稍大就攤開桌面四欄。

### 4.2 Composer 與上下文

對話、下一輪與權限回答沿用原邏輯，不在 mobile 另建 send。自己工作顯示「傳送下一步／排到下一輪」；外部來源顯示「與 PM 討論此成果」或「依此另開工作」，不偽裝成原 session 可續作。

provider 圖示可以節省位置，但要有可存取名稱與可見的目前選擇；不允許一個無說明的 auto 圖示暗中換費用或目的地。手機正常使用不露內部 ID。控制區以 44 CSS px 為本產品觸控目標；這是設計目標，不是測量聲明。

正文、輸入、send 和必要 pending gate 優先。未操作的高級設定、長環境資訊與原始 receipt 進 sheet。失敗／未知必須保留可見原因，不因「去掉 idle」就連異常也隱藏。

### 4.3 鍵盤、焦點與狀態保存

使用可視 viewport 和 safe area 作布局輸入，兼容 API 不可用的 fallback；縮放时不把 pinch zoom 當鍵盤事件。輸入高度要有上限，讓人仍能看到近期對話。中文組字、語音輸入、貼上多行不能觸發誤送。

背景刷新、方向切換、開關 sheet 不重建已輸入的 composer。可以移动同一個元件或保存完整狀態後恢復；不可同時留下兩個有效 send handler。手機／桌面適配共享 SelectionContext、authorization、events 和 schema。

草稿是本地未送文字；server 已確認 input 才是排入工作。手機進背景、鎖屏、斷網後不自動重播本地草稿。重新登入、切換帳號／workspace 後，不把舊待送文字送到新來源。回到前景先查原 key，再更新 UI。

### 4.4 首次手機價值

先能讀最近工作及其可靠摘要，再回答真正阻擋工作的一個問題、補下一句、看一次結果／PR。完整 RTM、甘特與差異仍可深入，但不要求手機主畫面塞入桌面資料密度。這不是刪功能，而是明確閱讀順序。

## 5. 低干擾同步與效能：改善讀取，不削弱寫入

借用 Hub 4.123.12–14 的方法，不照抄秒數：清單只取名稱、狀態、摘要、下一步與計數；完整 steps、transcript、diff 在選取後分頁取用。相同請求中的 project／profile／路徑／Git 讀取共用，耗時讀取避免阻塞整個清單。[S15]

每個 server 由現有 collector 決定重讀和來源版本；client 讀增量。收到新工作後，保留原選取、草稿、焦點和卷動，只提示「有新工作」。頁面在背景可降低視覺與網路更新，server 工作與 queue 不依賴前景。

快取 key 至少考量來源身分、principal／permission scope、workspace、必要版本及讀取選項。撤權或切來源會撤回相關衍生內容；不能靠較長 TTL 繞過讀取權限。寫入前仍重新驗權、scope、controller、attempt、固定來源與外部狀態。

將慢來源局部呈現為更新中／逾期；不要讓一台慢 host 拖住全部清單，也不要在它沒回時顯示 0。既有 partial-source 和 masking 規則維持。一般讀取不啟動模型、不產生新工作、不寫 PM 完成狀態。

量測分開：server source scan、API first response、選中工作到可讀內容、使用者輸入回饋、每分鐘 payload 和背景請求數。沿原 KPI 的 UI 回饋 p95 ≤500ms；事件傳遞與 collector 新鮮度分開記錄，不用 mock 數據宣称真實延遲。

## 6. 長討論與「下一步」：補 PM 價值，不只補 mobile

長討論整理輸出一份有版本的 continuation brief：原目標、已採用決定、候選、否決方向、已完成有據事項、未決問題、剩餘工作、必要附件／spec 引用及下一步。保留原文和引用，明示被省略的範圍。[S16]

已有 summary 可以增量更新，不每次重送所有 transcript。來源改變後先檢查相關性；模型摘要不是新的需求採用。若 PM 把已否決的方案重新當已採用，必須有回歸案例攔住。切換 PM runtime 使用自己的環境，不 resume 其他 controller。

Today／Project／Work 旁的 PM 判讀共用一份評估：最多先突出一至兩件影響目標的決策，其餘分成可自動處理、持續觀察與背景資訊。每項含 evidence、as-of、影響目標及可執行下一步。刷新不自動付費分析；由既有已採用政策或明確操作觸發。

我們本輪已能將兩份固定 assessment 帶入 PM 草稿；下一步要驗證模型能否據此作出正確解釋，並沿已有 quality／requirement／dependency／maintenance 流程處理，而不是再寫 assessment 資料庫。[S17][S18][S21]

## 7. 後續增量：待指令退回 Idea、可控的擁塞替代

### 7.1 待指令退回 Idea（第二優先，不擋首個候選）

只有 queue 證明尚未送出的 input 才可轉成稍後想法。server 先沿原取消契約完成；確認不會出隊後，再以穩定來源鍵建立 PM Idea／backlog draft，保存原因與原 input。取消成功但 draft 保存失敗，要有可恢復紀錄，不重啟工作、不遺失文字。

若可能已送出、結果未知或正在 transport，就不允許以「轉 Idea」解除原操作的未決狀態。可以另存一份註記，但原操作仍查回。不得靜默取消別人的 input。回到 Idea 不採用新 spec，也不自動開下輪 Sprint。

### 7.2 模型擁塞替代（先提供選項，背景自動化另驗）

官方／結構化的容量或擁塞錯誤、配額限制、登入失敗、普通網路錯誤、結果未知要分開。第一版顯示「等待／選用另一個已就緒 profile／保留稍後處理」，提供費用與範圍差異。

只有預先採用的 policy 才可自動替代；限定可用 route、次數與預算，不能把示例模型名硬寫死。來源還在執行或副作用未核對時不啟動競爭 writer。用固定成果和 continuation brief 建立新的自有 attempt；不取走外部 session，不重播已完成工具操作。原 provider、原失敗與 fallback receipt 都保留。

這不是本輪要新增的全機隊模型調度器。先把已支持的 BAT 路線跑通，再在已有 runtime contract 內補能力。Hub 的一次交接是參考，不代表我們已具備相同安全恢復條件。[S15]

## 8. 工作與驗收沿舊編號更新

D1–D6 是本修訂的定位標籤，不另建一套 backlog。

| 差額 | 原工作線 | 優先與完成出口 |
|---|---|---|
| D1 手機互動契約 | T1／T8、E03／E11、UX02／04／09 | P1 與實機BAT驗收並行；真 iPhone Safari／Android Chrome 跑返回、鍵盤、第二句、foreground 恢復。 |
| D2 低干擾來源同步 | T3／T4、E04／E08、UX05／06／08 | P1；有界摘要與詳情，新增 session 可見但不奪焦點，不多派 collector。 |
| D3 整合候選實機 | T2／T7／T8、R1、UX10 | P0；已合併段不再實作，完成真 BAT＋provider＋GitHub＋手機重連證據。 |
| D4 長對話／PM下一步 | T6、E09、UX07 | P1；用真資料核對決策、scope、成果與未決事項，少問但不猜。 |
| D5 input 退回 Idea | T2／T6、E02／E09、UX04 | P2；沿取消／PM草稿做可恢復組合，先完成 queue 核心。 |
| D6 擁塞選項／有限替代 | T3／T5、E01／E06–08 | P2；先明示 route 和費用，無靜默改模型，完整矩陣不提前宣稱。 |

## 9. 在原 K01–K16 追加實際情境

| 原驗收 | 本次擴充，不重新計分 |
|---|---|
| K01 | 長 Idea 摘要不把候選變成採用；一個經取消的 input 可恢復成非執行 draft。 |
| K02 | 手機從 profile 修復回原 Work 和草稿；可開始但無第二輪者在開始前揭露。 |
| K04／K05 | 第一個 input 回執不明、第二句已確認排入、關頁／鎖屏後由 server 查回，不重送。 |
| K08 | 手機列表→Work→Lineage／Live→返回，保持 exact Work／Attempt／Result；不產生新操作。 |
| K09 | 另一 client 和 Hermes 同時工作，mobile 不取得 foreign 控制權。 |
| K10 | 外部 CLI 新增會話自動進入索引；兩個 reader 不重算成果，partial 不當空。 |
| K11 | 明確列 BAT/Claude/Codex/AHP 的 read/live/start/next/result/recovery；容量fallback不能被網路錯誤觸發。 |
| K13／K14 | 兩份真成果支持 PM 目標影響分析；Maintenance 仍用同一責任和版本。 |
| K15 | 390/320/768 寬、觸控橫向、實體鍵盤、中文 IME、safe area、browser Back、權限撤回、背景後登入過期。 |
| K16 | 同版本 installer／Web／demo 標示；候選可回退，讀取壓力與payload量測，不重播副作用。 |

瀏覽器改 viewport 高度只算 keyboard simulation。iOS Safari、Android Chrome、桌面 Tauri 的結果分開保存；touch emulation 不可代替實體鍵盤、鎖屏或 OS 回收頁面的行為。[S13]

## 10. 下一輪執行順序

**立即：固定已合併候選，完成一條真實 BAT 工作日。** 小修復→採用→第一輪→第二句→關 client→固定 Result→驗證→PR→PM回填。自己與其他 controller 的原工作不變。不要再以 E01–E05 未勾完為理由重做已合併實作。

**並行：手機殼與讀取面。** 一位 UX integrator 管導航／composer／焦點；一位 backend/read-model owner 處理摘要、source freshness 與 queue receipts。沒有需要就不改 controller schema、不換 framework、不建立 mobile-only command path。

**接著：T5 和 T6。** 至少另一條完整 route 與可信外部歷史，以及真正有品質的 PM判讀。Codex 特定路線的限制列差額；只完成 BAT 不算完整跨工具。

**延後：裝飾、通用自動切模型、廣泛通知平台、外部 PM 同步、完整 native mobile app。** 手機先用既有 Web client，Tauri仍為桌面。從手機可安全使用既有工作，不代表要重寫 iOS/Android 程式。

## 11. 給 orchestrator 的合併與回報規則

本次只提供文件，不執行 repo 寫入、merge、deploy 或真模型操作。團隊沿既有授權執行，對不可確認的環境欄位使用 discovery／read，不自行猜主機、帳號或工作目錄。

每項回報回答：原來卡在哪一步、現在怎麼完成、使用哪份SHA、哪個來源仍為fixture、哪個平台真測過，以及剩餘限制。要求 GitHub metadata、原始碼和狀態文件一致；文件歷史段不能覆蓋新 head 的事實。

新 Workbench 預設 rollout 不必等所有PM維護功能都完成；但要等其核心導航、兩輪工作、恢復、權限與既有功能落點通過目標環境驗收。只為已就緒 workspace 啟用，Classic 作有限期回退。回退改的是UI，不取消server既有工作。

## 12. 保留的設計決定

保留 Tauri＋shared Web、Python Connector／TaskDaemon、journal、server/workspace 邊界、API／MCP 同路、PM 專屬 worktree、Idea／Sprint／Maintainer。讀取、連結或閒置不轉移 session 控制權；不改 coding agent 的 output 格式。

Cantus 的目前公開 README 仍標示 UNLICENSED；本包不含其程式、品牌或截圖。借用可觀察的操作模式，獨立在現有架構實作；直接移植另核授權。Hub 新版 source已公開，但採用程式前仍核對檔案及授權。[S11][S14]

> **本輪成功不是比別人更快加功能，而是：已完成的 engine 能在手機與桌面自然使用；PM 的判斷可追溯；所有平台仍共用同一個可信的工作紀錄。**

## 來源（固定基線，避免以後主線變動混淆）
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
