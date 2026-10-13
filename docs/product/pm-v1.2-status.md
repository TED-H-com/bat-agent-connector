# PM v1.2 開發狀態

目前介面及本機資源實作基準為 **2bf152d／#122**。M4 **755f126／#116** 與 M5 **f6c8f6b**
沿用下方候選紀錄，未因網站與樣式更新宣稱完成。
[Charter RTM 與資源交接](../handoff/2026-10-12-pm-v1.2-resources.md)保存 #121／#122 的
精確 CI、主線 Windows 新失敗及接續步驟；[前次進度](progress-2026-10-12.md)與
[前次交接](../handoff/2026-10-12-pm-v1.2.md)保留 M4／M5 的 frozen branches 及歷史證據。

## 目前整合狀態

- **Charter／RTM 已合併：#121／`8bc6cf3`。** 總覽閱讀與版本比較、專案內導覽及需求／工作／交付追溯已進主線。精確候選 `0d718f3` 的 Python 3.10–3.13、五個 desktop jobs 與 1,978 項 UI 通過；沒有 owner 安裝或 live 宣稱。
- **Agent 資源第一段已合併：#122／`2bf152d`。** 新增中央定期收集明確綁定的本機 Codex／Claude 紀錄、增量 cursor／去重、來源回報配額窗及共用資源頁。候選 `dc92f99` 的四版後端與五個 desktop jobs 通過；合併後主線 Windows synthetic PowerShell fixture 逾時，後端矩陣仍待收齊，不能宣稱主線全綠。精確涵蓋與缺口見 [resource observation 合約](../design/agent-resource-observation.md)。同一讀取另附每日用量桶（`buckets`，可選 IANA `tz`，預設 UTC）：只含仍計入總量的來源；整日 `complete` 僅在既有 cursor 已讀到檔尾且成功時間蓋過該本地日。缺欄位與未涵蓋日是 null，不是 0。圖表 UI 尚未接上。遠端 collector、交付效能歸因、容量、預測與完整 W30 仍待完成。

- **新的管理工作台方向已記錄，尚未完成：**[六視圖合約](../design/pm-project-control-center.md)
  定義 Charter、Agent 資源、RTM、技術相依、時程及問題／行動／變更；保留看板與工作空間。
  #118／#119 的樣式、截圖與表單整理不是這六頁的交付。W30 本機 collector 與有限配額投影已隨 #122 合併；完整跨主機資源能力仍待完成。
- **已合併：#117–#119。** 公開文件／截圖更新、分欄看板、Planning 閱讀和共用視覺調整；
  #119 修正中央整合測試的收合區操作，四平台 desktop jobs 已成功。後端矩陣另外追蹤。
- **已合併：#98–#115。** 包含計畫、Sprint／Kanban、治理、工程包／交接、有界批次、發布／維護、成果評估／依賴、自然對話採用、政策內修復、AHP 唯讀及 BAT 結構化分析。
- **M4 候選：#116。** Roadmap 品質、Brief 偏差、原 runtime 完成及人工單次唯讀觀察；原同帳本完整流程在 Python 3.10／3.12 各通過。最終 CI 尚未完成，Windows synthetic PowerShell 首次呼叫逾時阻擋 merge。
- **M5 本機候選。** Workspace 持久分析選擇與人工循序輸入唯讀觀察；81 項範圍整合及靜態檢查通過，完整候選／平台矩陣待驗證。
- **安裝／live。** 本輪沒有 owner 安裝或實際 BAT／provider／模型／部署驗收宣稱。CI、套件 fixture、真人環境分開記錄。

完整 [已採用計畫](pm-v1.2-plan.md)的 20 章、W01–W52、A01–A64、M0–M6 保持不變。
M4 是 BAT 日常可用里程碑，不能代表全部跨工具 v1.2 已完成。

## 工作與剩餘範圍

下表依固定來源歸類；「主線」表示程式已合併，不代表所有驗收條件或實際部署完成。
各行保留原計畫優先級及編號，驗證數字見進度報告。

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
| W10 | P1 | 一位 PM 討論與 evidence snapshot | 主線：固定脈絡、來源快照、Idea／Planning／Follow-up 分析與品質紀錄；更完整專業會議主持仍待做。 |
| W11 | P1 | Plan diff 與影響分析 | 主線：固定版本／來源／條件差異及影響候選；#113 提供候選更新及整組採用，完整語意判讀待強化。 |
| W12 | P1 | Project Brief | 主線：固定 Project Brief 與選定品質評估；M4 候選補條件偏差及同快照說明。 |
| W13 | P1 | Roadmap 與 scope | 主線：固定里程碑、需求與時程證據；M4 候選補採用需求品質與涵蓋缺口投影；完整 Git materialization 仍待做。 |
| W14 | P1 | Kanban | 主線：工作／執行狀態、WIP、固定品質投影及依賴證據；M4 補原 runtime 完成；完整 WIP 政策待做。 |
| W15 | P1 | Sprint artifacts 與時間 | 主線：Sprint 承諾、固定 DoD／接受條件及原成果品質；完整 Scrum 事件與引導待做。 |
| W16 | P1 | Planning Review Retro | 主線：Planning／Review／Retro 的固定討論、結果與改善追蹤；Daily Scrum／LLM 會議主持待做。 |
| W17 | P1 | 風險、變更與決策 | 主線：風險、變更、決策、固定來源、明確根因去重與責任；自動跨快照追蹤／提醒待做。 |
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
| W30 | P2（原計畫） | 成本與較完整預測 | 六視圖修訂將資源觀測列為 Agent 頁及 PM 資源安排的必要依賴：各 CLI 增量聚合、去重、配額池、合格交付歸因與區間預測；本機 Codex／Claude collector 與來源配額窗已於 #122 合併；尚無跨主機 collector、完整 quota adapters、效能歸因／預測或自動模型政策。 |
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
| W47 | P1 | 依賴 Issue PR 維護來源 | 主線：有界 GitHub PR／checks／Actions、明確 Issue／Dependabot／alert 來源與固定 findings；完整依賴掃描仍有限。 |
| W48 | P0 | 維護排程與恢復 | 主線：有界到期檢查、週 Review、窗去重／漏跑合併、原 owner 憑證、容量／期限及原 children 查回。 |
| W49 | P1 | 每週 Maintenance Review 與改進研究 | 主線：固定週報、原 findings／repairs、前次決策、owner outcome、责任與雙語輸出；改進研究待做。 |
| W50 | P0 | 暫停 關閉與 reopen | 主線：pause／close／reopen、原 execution epoch、在途查回與保留；不復活舊佇列、不接管外部 session；M4 joined 有接續回歸。 |
| W51 | P1 | 維護 API MCP skills 與 UI | 主線：維護與品質使用同一註冊操作，Web／Tauri／API／MCP 共用；新能力隨候選更新。 |
| W52 | P1 | 完整生命週期實機驗收 | M4 755f126 同帳本完整生命週期本機兩版通過；最終候選 CI／平台有阻擋，owner live 未執行。 |

## 文件及歷史

- [進度報告](progress-2026-10-12.md)：面向產品負責人的現況、Sprint 建議回覆、用量方向與下一步。
- [開發交接](../handoff/2026-10-12-pm-v1.2.md)：精確來源、測試／失敗、M5 與停放草稿、接續 gate。
- [歷史實作紀錄](pm-v1.2-history-through-2026-10-12.md)：保留原批次敘述與當時證據，不覆蓋本頁。
- [安裝及 PM 試用](../getting-started.zh-TW.md)：實際產品操作與 runtime 前提。
