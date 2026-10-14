# PM v1.2 開發狀態

這輪改版把「看懂狀況 → 找到問題 → 與 PM 討論 → 審閱並採用」接在同一個專案脈絡。
本頁以已合併主線 `aba02180` 為準。
後端 [#132](https://github.com/teddashh/bat-agent-connector/pull/132) 於 2026-10-13 以 `0c354861` 合併，
[#134](https://github.com/teddashh/bat-agent-connector/pull/134) 於 2026-10-14 以 `f25e1556` 合併；
前端 [#133](https://github.com/teddashh/bat-agent-connector/pull/133) 隨後於 2026-10-14 以 `cb7e9f42` 合併。
本輪尚未安裝或實機驗收；合併不等於已交付使用者環境。
[10 月 14 日進度報告](progress-2026-10-14.md)列出交付範圍、證據及剩餘工作。

既有 Charter／RTM 與本機資源頁基準為 **2bf152d／#122**。
[Charter RTM 與資源交接](../handoff/2026-10-12-pm-v1.2-resources.md)保留 #121／#122 當時的證據。
M4 **755f126／#116** 與 M5 **f6c8f6b** 沿用下方歷史候選紀錄，沒有由本輪圖庫重新驗證。
[前次進度](progress-2026-10-12.md)與[前次交接](../handoff/2026-10-12-pm-v1.2.md)保留原固定來源。

## M4 本次主線合併候選

本分支保留 M4 `47707025` 的所有提交，合併固定主線 `8a50a717`；這是 #116 的
本機候選，不代表已推送、已合併到主線、已安裝或實機驗收。以下 `aba02180` 的主線
陳述保留為既有快照；本分支也包含其後的第 7 輪介面與首次使用增量，未以本次合併
宣稱尚待完成的完整 PM 功能已交付。

本次保留同帳本完整生命週期、原回合完成證據、新鮮工作量／清冊查核、Roadmap 與
固定 Brief 的共用需求／完成定義評估，以及原候選提交／驗證引用。工作看板改用主線
元件與共用詳情，人工單次觀察放在「詳細資料」；唯讀角色不提供新觀察入口，原收據
依目前來源權限查回。未收到回覆時保留原 key，已知操作 ID 只讀回原效果。來源讀取的
同步重用仍保留寫入／await 邊界、最後送出檢查及原 60 秒工作波期限。

[10 月 13 日候選紀錄](../handoff/2026-10-13-m4-refresh.md)保留先前量測與失敗，
不當作本次候選通過證據。本次完整檢查結果另隨本分支交接；最終 CI、原生套件與
安裝驗收仍須由整合者核對，M5 的人工循序觀察及持久分析選擇不在本次分支。

## 目前整合狀態

| 層級 | 本輪狀態 |
| --- | --- |
| 分支整合（branch） | #133 的整合分支已合入主線；文件工作線 `ux/docs-r6` 更新公開介紹。 |
| 主線（merged） | #132（`0c354861`）、#134（`f25e1556`）先合併，#133 於 2026-10-14 以 `cb7e9f42` 合併；圖庫固定於 `aba02180`。 |
| 安裝／原生驗收（installed/native） | 本輪尚未安裝或原生驗收；browser 截圖不代替原生套件。 |
| 實機驗收（live-tested） | 本輪尚未實機驗收；CI 與合成資料不代替實際 BAT／provider／模型／部署證據。 |

- **#132 後端主線，2026-10-13 以 `0c354861` 合併：**固定 commit 相依清冊及有界 impact、
  唯讀 baseline／forecast／actual 時程、登記表、每日資源桶、即時總覽、有序討論來源、改名與提案可採用性。
  見[相依](../design/pm-dependencies.md)、[時程](../design/pm-schedule.md)、
  [登記](../design/pm-register.md)與[總覽／討論](../design/pm-overview-discussion-context.md)合約。
- **#134 決策流程後端，2026-10-14 以 `f25e1556` 合併：**保留採用後目標、近期決策及跨版本原需求 ID 的阻礙比對；
  採用後使用既有 `pm.governance.revise` 回報問題已解決，結案不代表需求驗收。
- **#139 結構化發現身分，2026-10-14 以 `2b8c4add` 合併：**中央登記表、總覽與討論來源提供 `finding`；
  `title` 仍為 null，未證實欄位不補值，撤回時一併移除身分。`aba02180` 已包含此實作。
- **#133 前端主線，2026-10-14 以 `cb7e9f42` 合併：**Web／Tauri 共用 `desktop/src`，同一側欄串起八個管理檢視及 Session。
  第 5b／6 輪包含近期決策、明確 Resolve 步驟、具名發現、時程軸、Unknown／尚無紀錄，以及一致的 Agent 讀取。
  即時總覽與固定報告分開；人工 Session 唯讀，撤回、草稿與原請求 key 保留。
  [walkthrough 腳本](../../desktop/tests/pm-walkthrough.mjs)已包含明確解決與原 key 恢復，
  使用真中央、暫存 Git 和 mock 外部傳輸；[證據界線](../design/pm-decision-walkthrough.md)不包含安裝或 live 驗收。
- **既有主線：#121／#122。** Charter／RTM、本機 Codex／Claude collector、cursor／去重及來源配額窗已合併；
  #132 的每日桶與 #133 的資源頁也已合併。遠端 collector、交付效率歸因、容量及預測仍待完成。
- **既有主線：#117–#119、#98–#115。** 既有公開介紹、共用 UI 與 PM 底座保留原歷史證據，不算作本輪重新驗證。
- **M4／M5 歷史候選。** M4 `755f126` 及 M5 `f6c8f6b` 保留原紀錄；本輪沒有重新驗證其狀態。
- **尚缺：**第 7 輪、下列新工作線與完整 PM v1.2 backlog；安裝／原生及實機驗收另列。

剩餘視覺修整是 **第 7 輪**，由 [Issue #140](https://github.com/teddashh/bat-agent-connector/issues/140) 追蹤；尚未實作。

- Discussions：討論列、來源脈絡與提案閱讀的收尾。
- 登記表與 Board：列、卡片、篩選與詳情的層級修整。
- 次要面板：按需展開工具的版面與文案。
- Shell：導覽、工作樹與窄螢幕層級。
- 載入：首次開啟、返回與更新時的安靜狀態及穩定版面。

下一條工作線以 `aba02180` 的狀態為準：

- [#141 本機首次使用](https://github.com/teddashh/bat-agent-connector/issues/141)：單次貼上 BAT 連線網址、易讀錯誤、安靜狀態、含建置識別的診斷，以及「這裡能做什麼」；仍待實作。
- [#142 本機 BAT／本機 Git 設計](https://github.com/teddashh/bat-agent-connector/pull/142)：設計文件已合併，對應 #141；[本機主機合約](../design/local-host.md)尚未實作。
- [#143 Overview 的 PM 重點判讀](https://github.com/teddashh/bat-agent-connector/issues/143)：[#144 設計文件](https://github.com/teddashh/bat-agent-connector/pull/144)已合併；[判讀合約](../design/pm-overview-assessment.md)尚未實作。

完整 [已採用計畫](pm-v1.2-plan.md)的 20 章、W01–W52、A01–A64、M0–M6 保持不變。
M4 是 BAT 日常可用里程碑，不能代表全部跨工具 v1.2 已完成。

## 工作與剩餘範圍

下表依固定來源歸類；「主線」表示程式已合併，不代表所有驗收條件或實際部署完成。
各行保留原計畫優先級及編號，驗證數字見進度報告。
視圖已由 #133 合併，`aba02180` 包含 #132／#134／#139 的中央合約。
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
| W12 | P1 | Project Brief | 主線：固定 Project Brief 與選定品質評估；M4 候選補條件偏差及同快照說明。即時 overview、可見需求進度／待決／偏差與涵蓋標記已由 #132 合併；#134 已合併採用後目標與近期決策；#133 已合併即時／報告介面。固定報告不混入即時數字，見 [API 合約](../design/pm-overview-discussion-context.md)。 |
| W13 | P1 | Roadmap 與 scope | 主線：固定里程碑、需求與時程證據。#132 已合併唯讀時程投影（[合約](../design/pm-schedule.md)）；不推算預測或 critical path；#133 已合併甘特圖。M4 候選補採用需求品質與涵蓋缺口投影；完整 Git materialization 仍待做。 |
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
| W24 | P1 | PM adapter 切換 | M5 本機 f6c8f6b：持久 workspace native／BAT 分析選擇、文件／writer generation、原 request 恢復；未合併、完整矩陣待跑。 |
| W25 | P1 | Fleet readiness 與安裝版 | 主線 #115：core 0.2.5／desktop 0.1.1 平台 CI 及原實包升級通過；M4 0.2.6／0.1.2 最終 gate 尚未完成。 |
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
| W38 | P1 | 有界批次與工作包交接 | 主線：1–16 筆固定 handoff、有界並行／期限與 proven-unsent retry；M4 候選補原完成及容量觀察。 |
| W39 | P1 | DocumentSnapshot 與五視圖 SVG | 主線：DocumentSnapshot 與 Brief 分頁 SVG；其餘四視圖仍在未完成草稿，不得直接合入。 |
| W40 | P1 | 專案文件與簡報 | 主線：同一固定 Brief 的 Markdown／HTML／SVG 及雙語存檔；其他文件、可編輯簡報／播放／下載待做。 |
| W41 | P1 | Actor 決策責任與 follow-up | 主線：决策責任、改善追蹤、固定前次結果與有來源 Follow-up；完整主動主持及判斷品質待驗證。 |
| W42 | P1 | Idea 到 Review 真實驗收 | M4 755f126：真實磁碟 Git／驗證器及 mock providers 的會員案例，兩個 Python 版本完整 joined 通過；真人驗收另列。 |
| W43 | P0 | ProjectLifecycle 與 Idea 長期停留 | 主線：生命周期及有固定政策／基準／完整新鮮工作量證據的 Maintain gate；階段名稱不授權效果。 |
| W44 | P0 | ReleaseBaseline 與發布 receipt | 主線：明確 release／已驗證部署基準與原通道／commit／receipt；完整 registry adapter 待做。 |
| W45 | P0 | 閒置與 workload coverage | 主線：中央工作量、來源涵蓋與穩定期間；M4 原完成／人工單次觀察、M5 人工序列觀察尚未合併。 |
| W46 | P1 | Maintainer profile 與政策執行 | 主線 #113：採用政策內的自有 repair、驗證／PR／交付上限、原完成與基準更新；M4 完整串接仍待最終 CI。 |
| W47 | P1 | 依賴 Issue PR 維護來源 | 主線：有界 GitHub PR／checks／Actions、明確 Issue／Dependabot／alert 來源與固定 findings，#132 已合併 [固定 commit 相依清冊](../design/pm-dependencies.md)、宣告／撤回、CI hints 和有界 impact。#133 已合併矩陣／相依圖；安裝版／原生及實機驗收仍待執行。 |
| W48 | P0 | 維護排程與恢復 | 主線：有界到期檢查、週 Review、窗去重／漏跑合併、原 owner 憑證、容量／期限及原 children 查回。 |
| W49 | P1 | 每週 Maintenance Review 與改進研究 | 主線：固定週報、原 findings／repairs、前次決策、owner outcome、责任與雙語輸出；改進研究待做。 |
| W50 | P0 | 暫停 關閉與 reopen | 主線：pause／close／reopen、原 execution epoch、在途查回與保留；不復活舊佇列、不接管外部 session；M4 joined 有接續回歸。 |
| W51 | P1 | 維護 API MCP skills 與 UI | 主線：維護與品質使用同一註冊操作，Web／Tauri／API／MCP 共用；新能力隨候選更新。 |
| W52 | P1 | 完整生命週期實機驗收 | M4 755f126 同帳本完整生命週期本機兩版通過；最終候選 CI／平台有阻擋，owner live 未執行。 |

## 文件及歷史

- [10 月 14 日進度報告](progress-2026-10-14.md)：改版合併、證據與下一步。
- [10 月 12 日進度](progress-2026-10-12.md)：前次固定來源、Sprint 建議與歷史證據。
- [開發交接](../handoff/2026-10-12-pm-v1.2.md)：精確來源、測試／失敗、M5 與停放草稿、接續 gate。
- [歷史實作紀錄](pm-v1.2-history-through-2026-10-12.md)：保留原批次敘述與當時證據，不覆蓋本頁。
- [安裝及 PM 試用](../getting-started.zh-TW.md)：實際產品操作與 runtime 前提。

## M4 原始失敗證據

整合前的 [M4 固定來源紀錄](https://github.com/teddashh/bat-agent-connector/blob/755f126330aded27b4685bdd9be7409d9248df5d/docs/product/pm-v1.2-status.md)保留每次原失敗、查詢量測與各版本結果。
本次更新不提高 deadline、不跳過測試，也不以重跑成功抹去原失敗。
