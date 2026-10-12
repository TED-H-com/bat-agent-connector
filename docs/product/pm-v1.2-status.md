# PM v1.2 開發狀態

方向以 [已採用的完整 v1.2 計畫](pm-v1.2-plan.md)為準，保留 20 章、W01–W52、A01–A64 與 M0–M6。
專業 PM 的目的，是管理需求、價值、範圍、時程、資源、品質、風險、決策與成果；討論與執行基礎是支援這些責任的能力。

## 第一批：workspace、Idea 與 Planning 草稿

追蹤 [#84](https://github.com/teddashh/bat-agent-connector/issues/84)，基底 `05396b1a`。
本批提供 server 身分下的獨立 PM workspace、角色、可恢復討論、資訊來源與確認歷史，
以既有 operations 接入 Web／Tauri、API 與 MCP。選定輸入形成固定 Planning 草稿，
未選構想仍保留；沒有自動採用、派工、發布或 LLM 回覆。

九種 PhaseProfile 描述專業討論的目的、上下文、資訊缺口、提問政策與成果契約；
目前只開放 Idea／Planning 的資料操作。提供 profile 不等於已完成該模式的 runtime 或領域模型。
外部 agents 可以透過同一 MCP 讀取脈絡及提交獲授權的筆記；自有 BAT PM runner 與 PM worktree 留在下一批。

現有已安裝 client 仍按原 managed installation 運行。本批不搬遷既有 journal、不合併同 repo
的舊 project、不啟動其他 daemon；新 PM workspace 由使用者明確建立，舊資料保留未歸屬。
第一批建立 controller ID；既有 session 寫入入口的隔離由以下第二批補齊。

合併、安裝與真人實測狀態以本批 PR 的實際驗證為準；本表不宣稱 M0–M4 已完成。

## 第二批：原控制者與可恢復操作

追蹤 [#87](https://github.com/teddashh/bat-agent-connector/issues/87)，接續第一批。
已認證的 server／actor 決定穩定 controller；新 session、背景 task 與 command 保存原始 authority。
受理與最後執行點核對 controller 和建立時的 incarnation，涵蓋 API／MCP／CLI、背景派工、
failover、fanout、relay、permissions、worktree merge 與 reviewed cleanup。
相同 actor 的多 client、token 輪替與重啟不改變身分；admin 不接管其他 controller 的資源。

讀取他人的來源後可以建立自己的 worktree；共同使用同一個可寫工作目錄仍受原控制者限制。
舊資料缺少建立證據時維持唯讀，已送出但回覆遺失的操作保留查回路徑。
個人閱讀資料與共用 GitHub integration 沿用既有授權；它們不會因為引用 session 就取得控制權。
Dashboard inventory 與帶 token 的 CLI／MCP policy 顯示目前 caller 的操作可用性，來源標記保持不變。

本批是 PM 自有執行環境的必要基礎；PM worktree、BAT capability wrapper、PM runner、
專業 PM 領域模型與完整生命週期仍按下表開發。自動驗證與發布狀態以本批 PR 為準。

## 工作與剩餘範圍

第三批追蹤 [#88](https://github.com/teddashh/bat-agent-connector/issues/88)：已實作固定討論脈絡、
workspace writer generation、PM 自有 controller、來源／原生回覆紀錄，以及設定綁定下的
固定 commit 工作目錄與不可覆寫草稿。API／MCP／Web／Tauri 共用 `pm.run.prepare`；
畫面可查回原始討論版本，保存脈絡不會產生 AI 回覆或採用計畫。

BAT 原生回覆的完成判定使用原始 event epoch／seq、turn／SDK session ID，保留重播缺口。
第三批的通用 BAT 介面沒有逐次限制外部 MCP 工具的契約，仍明示
`external_tool_policy_unverified`。#113 整合候選另外加入專用 Linux Codex 單次分析路徑，
要求固定 producer、runtime 與原程序退出證據；不能因此宣稱通用 BAT 路徑已驗證。
相應 BAT producer 已合併至 owner fork，尚未安裝。長期工作 adapter 接續與 W09／#88
的剩餘範圍仍待完成，與使用者自行處理的真人實機驗收分開。

第四批追蹤 [#91](https://github.com/teddashh/bat-agent-connector/issues/91)：專業計畫以固定來源、
八個管理領域、需求與接受條件保存不可變版本，未決事項仍維持未決。Planning 期間可明確
修訂選定來源；這不回寫較早的草稿、規格或已採用計畫。

正式採用經 PM 自有 writer 與 Git 保存精確文件，查實 commit 後才更新 journal 的接受指標。
文件版次、範圍版次、採用理由與 Git 證據分開記錄；只改 Markdown 而無法判斷範圍影響時，
保留 unknown，不假稱只是成果回填。Git 已保存但採用未完成，仍保留先前正式版本。
Web／Tauri 在同一討論畫面提供版本、來源、工作目錄準備與採用操作，API／MCP 使用相同契約。

第一、二、三批已合併於 #86、#90、[#92](https://github.com/teddashh/bat-agent-connector/pull/92)。
第四批計畫採用 [#93](https://github.com/teddashh/bat-agent-connector/pull/93)、工作證據 [#95](https://github.com/teddashh/bat-agent-connector/pull/95)、Project Brief [#96](https://github.com/teddashh/bat-agent-connector/pull/96) 與原生分析 [#97](https://github.com/teddashh/bat-agent-connector/pull/97) 已合併。未安裝到使用者環境，也未宣稱完成真人實測或 M4。

下表的「本批」是這次的實作範圍；測試／審查以 PR 證據為準。未完成部分保留在原編號下，
不以新增一張畫面或資料表當作完整 PM 能力完成。

| 工作 | 優先級 | 原計畫項目 | 狀態／尚缺 |
| --- | --- | --- | --- |
| W01 | P0 | Workspace identity 與 scope migration | 本批：workspace identity／membership；待舊專案明確綁定與部署遷移 |
| W02 | P0 | 全入口 controller policy | 第二批：既有執行入口、最後 frame、背景恢復與清理已接入原 controller／incarnation；驗證見 #87 的實作 PR |
| W03 | P0 | Operation request key 與查回 | 已接入 PM 原 request key、Git／journal 查回與取消；部分 Git transaction 保留原 writer，驗證以各批 PR 為準 |
| W04 | P0 | BAT capability adapter | Connector 整合候選新增 Linux 獨立 Codex BAT PM profile、原生完成及原程序退出證據；對應 BAT 修改已由 owner fork #2 合併，但尚未安裝或 live-tested，不推定其他工具／平台可用 |
| W05 | P0 | Evidence IDs 與 replay 去重 | #95 已合併：固定來源／digest／journal cursor、快照與重播去重；DoD 尚未評估，不把收集證據算成驗收 |
| W06 | P0 | Session worktree work 關聯 | #95 已合併：原 project／work 明確綁定、固定已採用 plan／需求引用、既有來源關聯；不轉移 session 控制權 |
| W07 | P0 | PM worktree bootstrap | 第三、四批：設定綁定、固定來源、自有 worktree、不可覆寫草稿與計畫 commit；不夾帶程式或其他 index 修改 |
| W08 | P0 | Plan revision 與採用流程 | #93 已合併：不可變文件、專業領域／來源、Git 證據、accepted CAS、理由與範圍版次；不是執行授權 |
| W09 | P1 | PM runner 與 adapter selection | 開發分支：中央原生與所選 BAT 的單次分析可明確切換，各自保存原請求與未知結果查回；長期工作 adapter 接續仍見 W24 |
| W10 | P1 | 一位 PM 討論與 evidence snapshot | #95／#96／#97 已合併：固定工作證據快照與 Idea／Planning 原生分析；完整 Follow-up／會議引導與品質判斷待做 |
| W11 | P1 | Plan diff 與影響分析 | 開發分支：固定版本／來源／接受條件比較、明確工作關聯的影響候選；語意判讀與採用變更流程仍待做 |
| W12 | P1 | Project Brief | #96 已合併：五項來源總覽、固定計畫／證據快照、來源查閱；完整偏差與品質判讀仍待實作 |
| W13 | P1 | Roadmap 與 scope | #98 已合併：固定採用需求、里程碑／依賴、原定／預測／實際證據與歷史；Git materialization、里程碑品質驗收仍待做 |
| W14 | P1 | Kanban | #100 已合併原工作／執行紀錄與 WIP；整合候選加入固定品質投影；需求前置接受條件已接交接就緒判斷，分派與完整 WIP 政策仍待做 |
| W15 | P1 | Sprint artifacts 與時間 | #99 已合併基本 Sprint；開發分支接入固定 DoD／接受條件評估與原成果品質投影，完整 Scrum 事件與引導仍待做 |
| W16 | P1 | Planning Review Retro | #100 已合併：各自輸出、固定討論依據、明確完成與改善追蹤；Daily Scrum、LLM 會議引導與品質判定待做 |
| W17 | P1 | 風險、變更與決策 | #101 已合併：固定計畫／快照來源、明確根因去重、版本決策與原採用結果關聯；自動分析、跨快照追蹤與提醒待做 |
| W18 | P1 | GitHub issue mapping | #108 已合併：明確選定公開內容、同一工作的一個 Issue 與追加版本、原操作查回、私有停止追蹤、API／MCP／Web／Tauri；外部狀態同步、多對多與明確重開仍待做 |
| W19 | P1 | Orchestrator readiness 與派工 | #110 已合併固定工作包經既有 repository.continue 交接，#112 加入有界批次；整合候選再以原始已合格需求評估滿足明確前置條件，保留原來源及最後執行檢查 |
| W20 | P1 | 現有 integration 與 merge 接入 | #112 已合併既有 integration preview／apply／conflict handoff／PR merge 的固定工作包與來源 lineage，最後 frame 與已送出結果查回分開；Web／Tauri／API／MCP 可讀原推送／合併 receipt，不據此接受需求 |
| W21 | P1 | 現有 deploy 與 receipt 接入 | #112 已合併既有 deploy／rollback／merge-and-deploy 的原來源權限與固定 release receipt，交付投影分開顯示合併、部署驗證與目前部署；未安裝或 live-tested |
| W22 | P1 | AHP observer 與 create-own | 開發分支已接明確設定的 AHP 有界唯讀觀察、原始來源權限、API／MCP／Web／Tauri；固定 1.0 canonical subset 與 VS Code 0.10 profiles 已有 fixture，完整 CI／合併待完成；create-own、寫入及 live host 尚未驗證 |
| W23 | P1 | 第一個 native CLI adapter | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W24 | P1 | PM adapter 切換 | 單次 PM 分析已有明確 adapter selector 與獨立原操作查回；長期 workspace adapter／PM 文件接續與 writer 切換尚待完成 |
| W25 | P1 | Fleet readiness 與安裝版 | 沿用既有 managed installation；開發分支準備 core 0.2.5／desktop 0.1.1 及固定舊版實包升級 fixture，跨平台升級 CI 與完整 Fleet 矩陣仍待完成，尚未發布或安裝 |
| W26 | P1 | Canonical MCP skills | 本批：PM read tools 與共用 operations；其他 v1.2 契約逐批加入 |
| W27 | P1 | 端到端實機驗收 | 自動化 fixture／驗收支援待做；真人實機部分由使用者處理 |
| W28 | P2 | 其餘具名 CLI 能力 | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W29 | P2 | 額外 reviewer 與討論模板 | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W30 | P2 | 成本與較完整預測 | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W31 | P0 | Idea thread 與明確轉 Planning | 本批：持久 Idea 與指定輸入轉 Planning 草稿 |
| W32 | P0 | PhaseProfile 與 command policy | 本批：九種 profile 契約；Idea／Planning 可用，runtime 限制待接 adapter |
| W33 | P0 | InputRecord 與循序資訊取得 | #103／#105 已合併有來源的資訊續接及固定 Follow-up 單次分析，保留影響／選項及零至三個問題；整合候選新增自然對話候選與整組採用，語意品質驗證及其他階段引導／研究 runner 待做 |
| W34 | P0 | Thread plan change Sprint batch 狀態 | thread、Planning 草稿、文件與 accepted scope 分開；#99–#112 已合併 Sprint、治理、工程工作包、單次交接與有界批次；整合候選的品質／依賴接續待最終回歸 |
| W35 | P1 | 架構與 SDD 工程工作包 | #104 已合併固定需求／接受條件、原規格 bytes、獨立程式基底及工程安排；整合候選新增原執行驗證、明確品質／DoD 評估及有來源的需求接受，不以測試通過自行接受需求 |
| W36 | P1 | OpenSpec read adapter | #102 已合併：既有 PM carrier 原固定 commit 的唯讀標準文件、來源 digest、Web／Tauri／API／MCP；其他 commit、自訂 schema 與 CLI JSON 契約待做 |
| W37 | P1 | OpenSpec 自有產物與 reconciliation | #112 已合併 PM 自有 Git append-only 規格與一般 immutable artifact、原操作查回、穩定 task ID／刪除歷史、完整工程包引用與最後 frame 驗證；Web／Tauri／API／MCP 已接入，不宣稱 OpenSpec CLI 相容或規格勾選等於完成 |
| W38 | P1 | 有界批次與工作包交接 | #112 已合併 1–16 筆固定 handoff 與有界 starts／並行／期限／proven-unsent retry；整合候選增加明確選原 CI／接受條件收據，保留原 child 查回 |
| W39 | P1 | DocumentSnapshot 與五視圖 SVG | #96 已合併固定快照；#106 已合併 Project Brief 的分頁 SVG、目前權限／來源／輸出 digest 與共用下載；其餘四視圖 SVG 待做 |
| W40 | P1 | 專案文件與簡報 | #106 已合併同一固定 Brief 的 Markdown／HTML／SVG、中英文及 Web／Tauri 存檔；其他文件種類、可編輯簡報／播放／下載待做 |
| W41 | P1 | Actor 決策責任與 follow-up | #101／#103／#105 已合併指定決策者、改善責任、固定治理／會議結果與前次比較，以及有來源的偏差／決策效果與下一步分析；建議仍未採用，判斷品質與自動排程待完成 |
| W42 | P1 | Idea 到 Review 真實驗收 | 整合候選包含真實磁碟 Git／驗證器及合成 BAT／provider 的會員案例；對話採用到相依派工的整合回歸進行中；真人實機部分由使用者處理 |
| W43 | P0 | ProjectLifecycle 與 Idea 長期停留 | #107 已合併；後續分支允許具原政策／基準／完整新鮮工作量證據的 Maintain 決策及有界自動轉入，pause/reopen 仍核對原 epoch，不由階段標籤授權執行 |
| W44 | P0 | ReleaseBaseline 與發布 receipt | #109 已合併：明確 GitHub Release 或已驗證部署的固定基準、原 repository／channel／commit／receipt、主要基準與歷史、API／MCP／Web／Tauri；registry adapter 與自動進 Maintain 待做 |
| W45 | P0 | 閒置與 workload coverage | #111 已合併中央工作量、來源涵蓋與穩定期間；整合候選新增完整證據下的 Maintain gate，依原輸入與目前完整觀察排除已完成維護回合；原 worktree 留存不等於活躍或完成 |
| W46 | P1 | Maintainer profile 與政策執行 | 開發分支：owner 採用固定來源、路徑、接受條件、驗證、交付上限及預算；有界自有 repair 沿既有工程交接、驗證、PR 建立與 merge/deploy，新增原回合完成／容量釋放及同通道部署基準更新；完整流程仍在驗證 |
| W47 | P1 | 依賴 Issue PR 維護來源 | #111／#112 已合併雙次有界 GitHub PR／checks／statuses／Actions 觀察，及明確設定的 Issue labels、Dependabot 原生 Bot／分支／manifest 與 alerts，保存固定 finding／原始頁面／既有 work 關聯；發布版本適用性與完整依賴掃描仍是 unknown，finding 不授權修復 |
| W48 | P0 | 維護排程與恢復 | #111／#112 已合併既有 worker 到期檢查與每週 Review、固定窗去重／漏跑合併、原 owner 憑證、並行／期間上限與絕對期限、原 children 查回；API／MCP／Web／Tauri 已接入；自有 repair 流程另見 W46 |
| W49 | P1 | 每週 Maintenance Review 與改進研究 | #112 已合併固定週報、原 finding 比較、討論／owner 決策／責任追蹤、共用排程容量及雙語輸出；整合候選增加原修復與子操作回執的固定投影，改進研究仍待做 |
| W50 | P0 | 暫停 關閉與 reopen | #107 已合併基礎；整合候選補齊維護子操作：同 journal 的操作／step／local effect 邊界、原 execution epoch、在途查回與明確保留；重開不復活舊佇列、不封存 GitHub、不接管外部 session；中央回歸待跑 |
| W51 | P1 | 維護 API MCP skills 與 UI | 開發分支：lifecycle／政策／觀察／排程／Review／repair／原執行驗證與品質接受使用共用註冊操作，Web／Tauri／API／MCP 已接入；最終整合回歸待完成 |
| W52 | P1 | 完整生命週期實機驗收 | 整合候選包含原維護修復、PR／merge／部署／基準及固定 Review 的 fixture；最終完整回歸進行中；真人實機部分由使用者處理 |

## 2026-10-11 續作驗證

2026-10-12 00:01 UTC（美東 10 月 11 日晚間）查核：Roadmap #98、Sprint #99 與 #100–#112 已合併，主線為 `a7db9389`。#110 `3f134717`、#111 `14db7901`、#112 `dddcf532` 各自精確來源提交均通過 10 項 required checks 後依序合併。新增成果評估、維護修復及自然對話採用仍在後續整合候選。不能以較早分支通過代替目前提交。尚未安裝本次整合版本，也沒有本輪真人或 live Fleet／provider／model 實測。

本機固定提交的交付鏈後端在 Python 3.12／3.10 各通過 278 項；大型規格核心在 Python 3.12 通過 35 項、Python 3.10 延伸回歸通過 126 項。設定維護來源的修正版在 Python 3.10 延伸回歸通過 202 項。來源撤權與原操作恢復另有共用入口及 Web／Tauri fixture 驗證；最終合併來源的 Ruff、完整 Python suites 與原生／UI 檢查仍需完成。

本輪固定來源的批次模組 Python 3.12／3.10 各通過 25 項；之前 Python 3.10 延伸回歸另有 114 項通過，不能合稱同一全套執行。週報與設定 finding 整合在 Python 3.12 通過 33 項、Python 3.10 延伸回歸通過 94 項。共用介面新增批次掛載、唯讀政策條件及固定 finding 報告後，Web／Tauri／EN／zh-TW 的 124 項 UI 檢查通過，建置、生成資產一致性與 Ruff 通過；先前固定共享來源的 native 全套亦通過。上述皆為本機 fixture，並非 Windows／macOS 實機或 live 交付證據。

原回合完成／工作量固定來源 `096a9c8` 的 Python 3.12 225 項及 Python 3.10 延伸 304 項通過；共享整合來源 `586c845` 的介面回歸 259 項通過。品質 Brief 下載版本修正 `c18502b` 有 44 項 UI 與 9 項原生檢查通過。這些分段結果不能代替部署、基準更新、容量釋放及固定週報的整條流程驗證。

整合候選已加入自然討論的帶來源提案、整組 owner 審閱、既有採用子操作與 PM 自有 Git 保存，保留排除項目及明確需求刪除。會員案例已接通這條採用流程，再以實際磁碟 Git／驗證器及合成 BAT／provider 執行登入、忘記密碼與維護 Review；與提案回歸合跑的 14 項 Python 3.12 檢查通過。測試通過仍須原需求的正式評估才可解鎖依賴。BAT 原 PM carrier／重啟接續的固定來源 `99d8378` 在 Python 3.12／3.10 各通過 331 項；包含原回合完成後僅回收本地 registry 容量，不傳送停止訊號。

固定已採用文件已接入原生與 BAT 的單次分析，可預覽精確 Git／採用收據、整份檔案及明列省略的涵蓋範圍。原來源撤權會隱藏討論與衍生提案、計畫、Follow-up、diff、工作包及快照，但不阻止已保存副作用的原收據查回。[整合 PR #113](https://github.com/teddashh/bat-agent-connector/pull/113) 保存本批最終來源、完整檢查及合併狀態。

早期整合的完整 Python 3.10／3.11／3.12／3.13 CI 各有 5,494 項通過、11 項失敗、42 項跳過。後續修正未評估成果事件的成員權限缺口、維護 fixture 在注入異常前就完成原回合的時序，以及已採用 PR 發布路徑的舊架構斷言。維護回歸 `9dc2c046` 在 Python 3.12／3.10 各 41 項通過；事件／Sprint／品質／發布回歸 `04758b2d` 各 56 項通過。這些分段結果不代替最終整合全套。

共享前端 `698a944` 的 1,860 項 UI 通過／3 項原有跳過，原實際中央服務流程及受控延遲流程均通過；其精確來源 desktop CI 五個 jobs 全部通過，涵蓋 Windows、兩種 macOS 及 Linux 的既有安裝版驗證。原生未變來源先前有 132 項通過／1 項忽略。先前 Windows 啟動 PermissionError 的失敗紀錄保留；另行原生 fixture 已重現合法讀取者與狀態檔替換的衝突：修正前 2 項實際 WinError 5，保留同檔案／目錄 handle 並逐次驗權的有界替換修正後，在 Windows `61ebbd83` 有 24 項通過／2 項跳過。這修正了已重現的碰撞，不能因此補造舊錯誤缺失的失敗位置。以上均不是 live 模型、BAT 或使用者硬體驗收。

M4 尚未完成：這批整合仍待完整回歸及精確來源 CI／合併；BAT 修改未安裝，真人硬體驗收仍由使用者負責。M5／M6 及原 W01–W52 範圍保持不變。

2026-10-12 00:24 UTC 更新：BAT owner fork 的 [#1](https://github.com/teddashh/better-agent-terminal/pull/1)
已合併 44 個 upstream 前置提交至 `7ff2124a`；專用 PM runtime、原輸入 epoch 與 queued-input
防護由 [#2](https://github.com/teddashh/better-agent-terminal/pull/2) 合併至 `dcc33d8a`。
兩個 merge tree 分別等於測試來源 `b7419892` 與 `ff486a87`。
[精確來源 CI](https://github.com/teddashh/better-agent-terminal/actions/runs/38187417986)
的 baseline／feature Rust 分別 540／561 項通過，各有 1 項原有 ignored；feature 包含
16 項專用 PM 與 86 項一般 Codex 測試。兩者的 TypeScript、build、13 組 sidecar script、
headless check 與 inotify／bubblewrap 探針通過。先前本機 inotify 耗盡導致的監看 fixture
失敗仍保留為該次紀錄，不能改稱該次已全過；新 CI 的同一監看 fixture 已通過。
這些 BAT 來源已合併，尚未安裝、發布 release、部署或完成 live 模型／Connector 端到端驗收。

M4 第 13 步的 followup 已接入本批整合：固定 Maintenance Review 候選可成為既有 Idea 的
未確認輸入，完整原候選隨討論／Planning context 保存，沿既有確認、採用與 Follow-up 規則接續。
來源撤權也會隱藏衍生輸入與計畫。整合共享介面 77 項及 Node 34 項通過，包含 13 項候選介面案例；
固定 `bddeb08` 的 12 項後端來源／權限案例在 Python 3.12／3.10 均通過；同來源的 Review、討論、提案、Planning、Follow-up 與差異回歸在 Python 3.12 另有 211 項通過。

## 下一批：AHP 觀察與實包升級

這批獨立於 #113，依 [AHP 觀察契約](../design/pm-ahp-observer.md)接入同一操作服務。
管理者明確綁定 endpoint 與可見 workspace，使用者只選 adapter 及其原生 catalog 中的來源。
觀察保留固定原始資料、版本與缺口；重連資料先核對原 session 身分，來源撤回後整份歷史、
操作與事件一起隱藏。早期紀錄只能從自身完整證據恢復，不用最新清單補認原來源。
畫面沿用原操作 key、各 adapter 草稿與英／繁中工作區，不取得任何 session 控制權。

固定整合 `cfea00e` 的 AHP socket、來源／歷史、API 與 skill 回歸在 Python 3.12／3.10
各 112 項通過。原生來源 `fa0107c` 的全部 targets 有 134 項通過、1 項既有忽略；
共享 UI 的最終欄位回歸 `bf318e1` 有 38 項通過，Node 34 項、兩種建置與生成資產檢查通過。
這些是分段 fixture 結果，完整後端與目標平台 CI、合併及 live host 驗證仍分開追蹤。

同批準備不同的 core／app 版號，使新 client 不會因 core 同版號而重用舊程式。
升級 fixture 使用兩份實際打包 binary，核對原身分、workspace、operation key／歷史、
三個並行 clients、重啟與拒絕降版；未知外部結果必須阻擋升級並保留舊 owner。
較早的 Linux 隔離 package pair 已通過，但不把該結果歸給最後 AHP 整合包。
CI 將由固定 `56d1bf5`／0.2.4 建立 baseline，在各桌面平台驗證新 core；目前尚未跑完。
這是 core 升級 fixture，不代表應用程式 installer 原地升級或使用者環境驗收。

W22 的 create-own、W23 的一般 coding adapter、W24 的持久 adapter 切換仍未完成。
這批沒有啟動模型、接手外部 session、寫入 live host 或安裝到使用者環境。

## 工作證據與分析 adapter

[#94](https://github.com/teddashh/bat-agent-connector/issues/94) 接入既有 Connector project／work，
保存原工作版本、已採用計畫引用及需求 ID。證據由中央 journal 收集，不接受 caller 提供的
完成內容；工作回報、驗證、PR 合併與部署保持各自的來源與時間。讀取時重新檢查來源可見性。
Web／Tauri 在討論旁提供明確連結與快照查閱，不依 repo 名稱自動推測歸屬。
工作證據基礎已在 #95 合併，尚未包含完整品質判定、偏差分析與五視圖。

Linux native PM analysis adapter 已在 #97 合併：明確要求一次分析後，使用新建的自有
原生 thread、固定討論脈絡與限制工具的 runtime。API／MCP／Web／Tauri 共用原 operation，
完成回覆連同模型、原生事件與來源保存；取消與失聯查回原程序，不重送或接管其他 session。

2026-10-11 的兩次各自獨立合成模型操作：第一次因正式 summary terminal 尚未支援而保守
拒收，維持 aborted；修正並通過事件／journal 回歸後，新的操作以 Codex CLI 0.160.0、
模型 gpt-6.1-sol 成功完成單次分析、來源匯入、程序退出與 writer 釋放。虛構會員範圍測試中，
回覆保留登入／忘記密碼為已確認，推薦码與折扣為未採用建議。沒有機隊、repo 或真實工作資料。
這是 Linux 原生討論分析的有限實測，不代表 BAT 路徑、PM 文件 writer、全平台或 M4 驗收。

## 固定快照與 Project Brief

#96 已合併 `pm.snapshot.create` 與 API／MCP／Web／Tauri 的共同讀取契約。
五項總覽使用同一個時間點的 accepted plan、明確工作關聯與已保存證據；沒有正式基準時，
草稿只列為候選。工作回報、驗證、合併與部署保持原來的意義，未評估的品質與偏差清楚標示。
目前是可追溯的來源總覽，尚不宣稱已完成專業 PM 的完整成果判讀與偏差分析。

工作空間首頁顯示完整總覽；進入討論後，總覽置於右側，對話保留主要位置。
原始快照保持不變，但每次讀取重新核對來源權限，不向已撤權的讀者顯示私有證據。
#106 已合併 Project Brief 的 Markdown、HTML 與分頁 SVG，三種格式使用同一份
可讀快照，保留未知、來源時間與涵蓋限制。下載前核對固定版本與 bytes；Tauri 由原生
對話框選目的地並再次向中央查證，不接收前端傳入的路徑或內容。其餘四視圖輸出、
完整文件種類及可編輯簡報仍待完成，生成報告不算 Review 完成。

## 投入階段與原操作查回

#107 已合併 [lifecycle 契約](../design/pm-lifecycle.md)。暫停／結案先阻止新的投入，
原先已受理的外部步驟繼續以原操作查回；未知結果與尚未釋放的 writer 不被改算成功。
owner 可以明確保留指定在途操作並留下理由，這只處理管理責任，不停止或重啟該操作。
重開保留歷史，未送出的舊請求仍須新的明確授權。

Web／Tauri 在 workspace 首頁及討論旁提供相同控制，管理階段、討論模式與 Sprint 分開。
目前使用者的來源權限決定歷史與在途紀錄可見性；部分隱藏不等於整個專案沒有工作。
共用介面已以合成資料檢查原 key 恢復、版本變更草稿、撤權、owner 與中英文版面；
基礎版本的中央與原生檢查見 #107；後續維護整合仍待完整回歸，尚未安裝或真人驗收。

## 明確選定的 Issue 發布與追蹤

#108 已合併 [Issue mapping 契約](../design/pm-issue-mappings.md)。固定工程工作包對應
同一份工作與原 repository；使用者另外填入公開標題、內容，私有計畫、證據、路徑與理由
不自動帶入 GitHub。第一次建立 Issue，後續版本追加 comment，不覆寫其他人的內容。

共用工作畫面保留選定的 package、mapping 版次與原操作 key。回覆遺失時先查回原請求，
不因刷新或另一份草稿重建 Issue。停止追蹤只更新本地管理紀錄；明確填入公開說明才追加
comment，不關閉 Issue、不停止 coding session。暫停期間仍能留下純本地的停止理由。
Web／Tauri 合成回歸涵蓋連續發布、草稿版本衝突、來源撤權、讀者權限與中英文窄畫面；
中央與原生路由驗證見 #108 的精確提交檢查；尚未安裝或真人驗收。

## 後續順序

2026-10-11 Sprint 檢視後，先用可完成的工作流程驗收，保持完整計畫範圍：

1. 凍結交付／品質／維護整合來源，完成後端、共享 UI 與原生檢查，依精確提交的 CI 依序合併。
2. 會員登入與忘記密碼案例：固定 A 的真實驗證與責任評估成立後才派 B；自有修復沿原交付、基準、完成證據回填真正的 Maintenance Review 討論與固定快照。
3. 自然討論形成帶來源的候選資訊／scope／plan 更新，一次審閱變更與未決事項，再走原採用流程，減少逐筆整理欄位。
4. 完整 PM runtime：原自有 carrier、受限來源、固定已採用文件脈絡、原回合完成及服務重啟後的新自有 session 接續。未知結果不釋放 writer，也不因 prompt 宣稱隔離。
5. 目前四視圖及 AHP 唯讀觀察的獨立成果先保存在原分支；核心流程收斂後，依 M5／M6 接續 adapters、文件／簡報／SVG 與安裝版自動驗證。

真人實機驗收由使用者負責；Agent 仍提供自動測試、fixture、操作說明與未驗證標示。
正式簽章、Mac 公證、正式更新通道及 Linux 原生憑證持久儲存維持先前排除範圍。
公開文件、測試、issues 與 PR 只使用一般化範例，不包含個人機隊資訊。

## 固定計畫比較與排程

W11 比較兩份明確指定的不可變計畫，顯示來源、需求、接受條件與管理領域的原始差異。
工作影響只涵蓋明確選入的歷史 binding；不讀取最新工作來假造歷史，也不自動改派或開 issue。
保留固定 journal cursor，缺少保留的建立證據就回報無法證明，文字的語意影響維持未評估。

W13 在 workspace 主畫面加入範圍與里程碑。排程基準、預測與已保存成果分開記錄；
里程碑引用固定已採用需求與工作，重訂基準保留最早承諾及所有歷史。觀察只使用選定快照，
無法從工作聲稱、測試或 merged PR 推定 DoD、實際完成日期或里程碑通過。

API／MCP／Web／Tauri 共用相同 reads 與 operations。回覆遺失保留原 request key、
版本與內容；編輯草稿可恢復，換用目前基準是明確操作。自動化包含 mock BAT 加真實磁碟 Git
採用、工作關聯、排程、預測及成果快照串接；這不是使用者機隊實測。

## Sprint 與迭代承諾

Product Goal、Product Backlog、Definition of Done 與 Sprint Goal 分別版本化。
Sprint 固定角色、期間及承諾引用；選入工作保留 How、原始 binding 與變動理由。
只有指定且仍有 owner 身分的人能開始、修改尚未開始的 Goal、結束或取消；
開發者的 How 與自己的工作選擇保留個別責任。

Web／Tauri 在同頁查看及操作；API／MCP 共用服務。結束／取消不停止 session，
也不自動搬移工作到下一輪。尚未評估 DoD 的工作不計為已驗證 Increment，
但不因此聲稱實作或測試失敗。Scrum 模式不宣稱完整符合；Daily Scrum 與品質判定尚未完成。

## 工作流與專業討論

工作看板直接投影既有 work、command、operation 與原始事件，一工作一卡。
已受理執行、完成聲稱、內容核可及品質驗收各自呈現；時間證據缺失就維持未知。
WIP 只涵蓋目前回傳頁，固定證據快照與現在執行狀態分開，不另存一套進度。

Planning、Review、Retro 引用固定 Sprint 承諾與明確選定的專案快照。
準備摘要仍是草稿；指定且仍有權的方向負責人明確記錄討論結果後才完成。
會議決策不自動採用 scope、改動 backlog 或派工；後續改善保留責任人、預期效果、
驗證方法及原始紀錄，回報完成也不等同已證實改善有效。

工作看板、Sprint 與討論共置於 workspace 畫面，修改表單按需展開。
原始操作來源被撤權時，衍生紀錄與事件一併隱藏；會員變更使既有
checkpoint 失效，即使仍在輸入也立即清除舊畫面。草稿與原操作 key 留在
原 principal 分區，恢復授權後可查回，沒有重新派送工作的副作用。

這批已由 #100 合併 API／MCP／Web／Tauri；尚未安裝或宣稱 M3／M4 完成。

## 固定來源的治理紀錄

風險、假設、問題、相依、偏差及變更提案保存原計畫與快照。
同一明確根因不重複產生紀錄；不同問題共用證據時須留下區分理由。
方向決策只由目前 workspace owner 記錄，正式採用仍沿用既有操作。
採用關聯核對原 successful operation、候選文件與先前基準；不會修改
scope 或再送一次 adopt。所有讀取、操作結果及事件重新核對原始來源。
目前是有來源的管理紀錄，尚未完成自動根因分析或完整 PM Follow-up。

## 先整理資訊的 Follow-up

工作區首頁可準備固定版本的討論資料：沿用已採用計畫與證據快照，明確選定
討論、治理版本及已完成會議的改善紀錄。八個管理領域保留原始資訊身分；
後來確認的回答另列來源，不偷偷改寫原本未決的基準。

同一問題保留穩定身分；指定前次資料後可比較新增、變動與未再選入。
最多三項議程依已記錄的待處理狀態排列，沒有問題就不湊題，也不假造商業優先級。
API／MCP／Web／Tauri 共用原操作與版本檢查。來源撤權時，衍生資料、原操作及事件
一併隱藏；恢復授權仍查回原請求，不另做一次。

這批完成討論前的資料整理，尚未接模型討論、語意根因／影響判讀、提醒或維護排程。
既有成果回報仍未經品質驗收；準備議程不代表完成 Follow-up 會議或已採用變更。

## 固定工程工作包

既有 Connector 工作可透過共用操作準備不可變工程描述。需求與接受條件來自原先
明確採用的計畫和工作關聯，規格原文由服務讀回並保存版本與摘要；程式的已發布 commit
另行記錄。設計、技術工作、驗證方法及遷移／上線／回復／觀察安排保留裁適理由與未知。

Web／Tauri 在工作區同頁審閱需求、工程安排、固定來源及目前準備檢查。
「可進行工程交接審閱」只表示本地準備條件通過，尚未查證執行工具、預算、遠端目標或品質。
API／MCP 可建立新版本，原工作、session、attempt、規格與先前工作包保持不變。
來源撤權時，衍生內容、原操作與事件一致隱藏；不轉成普通 artifact 繞過來源權限。

這批完成 W35 的固定描述與審閱基礎；後續開發分支已接入下方 W19／W38 的單次固定交接，批次與完整交付仍待完成。


## 固定工程包交接與原來源權限

[#110](https://github.com/teddashh/bat-agent-connector/pull/110) 的 [W19／W38 交接](../design/pm-package-handoff.md) 沿用既有
`repository.continue`。同一明確操作選定完整 package、已發布程式基底、原工作與目標；
規格和程式 commit 分開保存，不因兩者不同而宣稱不相容，也不額外要求 owner 重複批准。
完整需求、接受條件、設計與驗證安排必須符合既有首則訊息上限；超限整份拒絕，不截短規格。

原始來源在受理、尚未送出的 Git／BAT 步驟及對外讀取時重新檢查。派生 session、工作、
artifact、operation 與事件保留同一來源權限；撤權後不能透過另一種檢視取得原資料。
已送出效果仍以原 operation／session／message 查回，不因撤權再送一次。
後續整合分支已接入具完整原規格與來源契約的 integration／merge／deploy 衍生操作，
並保留各自已送出效果的查回。完整 M4 驗證仍按最終來源提交記錄。

## 發布基準與維護準備

[#109](https://github.com/teddashh/bat-agent-connector/pull/109) 提供已發布成果的
[基準登錄](../design/pm-release-baselines.md)。GitHub Release 必須查實原 release、tag、
repository 與 commit；部署來源必須有原本的產品版本驗證 receipt。
未查 registry、功能接受條件或目前 production 狀態時仍明示未查，不以成功 workflow 代替。
Web／Tauri 明確讀取候選，保存審閱版本、草稿及原 key；登錄本身不再發布任何成果。

維護政策與 workload collector 已在開發分支接入共用入口。政策保存採用者、原發布來源、
精確 repository／provider／branch／workspace 範圍及投入上限。工作量以中央已知工作、
承諾、在途效果與具範圍的 BAT 觀察判斷；失聯、等待 CI、blocked 或未知效果都不算閒置。
舊政策的 repository 範圍不能用少數已連結 PR 宣稱全部查完。
有界 GitHub 觀察須明確採用 `github_open_prs_and_actions_v1`，限制分頁、列數、bytes、
時間與兩次遍歷結果；它不代表 Issue／依賴內容或所有第三方 CI 已查完。

[有界檢查排程](../design/pm-maintenance-checks.md) 已接入原 daemon 與 operation worker。
到期時間窗、原操作與容量在同一交易保留，漏跑只安排最近一期；每次 provider 讀取仍受
原檢查剩餘期限限制。來源或原控制者失權、生命週期改變、額度用完會停止新步驟；
未知的原 children 保留容量並沿原 receipt 查回。中英文畫面顯示下次時間、阻止原因、
原操作及覆蓋缺口，讀取畫面不啟動工作。後續分支已加入有界修復、自動 Maintain gate 與每週 Review；完整整合驗證仍待完成。

上述區分開發分支、草稿 PR、合併、安裝與真人驗收；早期提交的測試數不套用到後續整合版本。

## 2026-10-11 品質、維護交付與 BAT 分析整合

整合分支 `feat/pm-v12-delivery-quality-integration` 已包含固定執行驗證、原 DoD／需求接受條件評估、
相依工作與批次的明確品質來源選擇，以及 Sprint／工作證據／Kanban／Brief 的唯讀品質投影。
自有維護工作以原已採用政策，沿既有工程交接、固定版本驗證、中央 PR 建立與有界交付引擎前進。
PR、合併、部署、功能接受及發布版本是各自的事實；原回合或交付結果未確定時保留容量與原操作。
Maintenance Review 保存當時的原工作和子操作回執，後續變更不回寫舊報告。

BAT 分析使用明確設定的 PM worktree binding 及獨立 Linux Codex profile。設定符合條件不等於執行可用；
每次核對原 context／native thread／程序／binary 與工具限制，送出結果未知時不重送。
原始完整終端事件、精確回覆及原程序退出均證實才匯入 assistant 訊息。共用畫面保留中央原生與 BAT
各自的原請求，切換不改寫已送出內容。這仍不是 W24 的長期 workspace adapter 接續。

精確提交的本機證據：BAT Connector `23aeb59` 有 Python 3.12 85 項、Python 3.10 延伸 222 項通過；
Maintenance Review `3883d12` 在兩版本各 30 項通過。整合後分析／Review UI 53 項，以及
修復完成／交付顯示 `0d971ad` 的雙語及三種寬度 UI 42 項通過。上述不是最終全套來源驗證。
BAT runtime `a1ee88e` 的 16 項專屬測試通過；工作區 Rust 回歸 556 通過、1 個既有 ignored，
1 個既有檔案監看測試因主機 inotify 耗盡失敗，仍列為環境驗證限制。

#103 與 #108 的新提交已通過 Windows 安裝後 fixture；其他必要 CI 仍依精確 head 收齊。
這些是 CI／離線 fixture，並非真人 Windows／macOS 硬體或 live Fleet／provider／model 驗收。
本次程式未安裝至使用者環境。BAT 分支後續已合併至 owner fork，詳見上方 00:24 UTC 更新；
合併不代表安裝或 live 驗收。
