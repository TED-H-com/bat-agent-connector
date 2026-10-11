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

BAT 原生回覆的完成判定使用原始 event epoch／seq、turn／SDK session ID，保留重播缺口；
這是 runner 的內部基礎。目前核對的 BAT 介面可傳遞檔案唯讀設定，但沒有逐次限制外部 MCP
工具的契約，因此自動 Idea／Planning 執行仍不可用，明示 `external_tool_policy_unverified`。
這個限制與使用者自行處理的真人實機驗收分開。W09 與 #88 尚未全部完成。

第四批追蹤 [#91](https://github.com/teddashh/bat-agent-connector/issues/91)：專業計畫以固定來源、
八個管理領域、需求與接受條件保存不可變版本，未決事項仍維持未決。Planning 期間可明確
修訂選定來源；這不回寫較早的草稿、規格或已採用計畫。

正式採用經 PM 自有 writer 與 Git 保存精確文件，查實 commit 後才更新 journal 的接受指標。
文件版次、範圍版次、採用理由與 Git 證據分開記錄；只改 Markdown 而無法判斷範圍影響時，
保留 unknown，不假稱只是成果回填。Git 已保存但採用未完成，仍保留先前正式版本。
Web／Tauri 在同一討論畫面提供版本、來源、工作目錄準備與採用操作，API／MCP 使用相同契約。

第一、二批已合併於 #86、#90。第三批在 [#92](https://github.com/teddashh/bat-agent-connector/pull/92)
驗證；第四批在 [#93](https://github.com/teddashh/bat-agent-connector/pull/93) 驗證。未安裝到使用者環境，也未宣稱完成真人實測或 M4。

下表的「本批」是這次的實作範圍；測試／審查以 PR 證據為準。未完成部分保留在原編號下，
不以新增一張畫面或資料表當作完整 PM 能力完成。

| 工作 | 優先級 | 原計畫項目 | 狀態／尚缺 |
| --- | --- | --- | --- |
| W01 | P0 | Workspace identity 與 scope migration | 本批：workspace identity／membership；待舊專案明確綁定與部署遷移 |
| W02 | P0 | 全入口 controller policy | 第二批：既有執行入口、最後 frame、背景恢復與清理已接入原 controller／incarnation；驗證見 #87 的實作 PR |
| W03 | P0 | Operation request key 與查回 | 已接入 PM 原 request key、Git／journal 查回與取消；部分 Git transaction 保留原 writer，驗證以各批 PR 為準 |
| W04 | P0 | BAT capability adapter | 第三批：唯讀選項、native replay／completion 與能力缺口；自動 PM 執行仍缺工具限制契約 |
| W05 | P0 | Evidence IDs 與 replay 去重 | #94 開發分支：固定來源／digest／journal cursor、快照與重播去重；DoD 尚未評估，不把收集證據算成驗收 |
| W06 | P0 | Session worktree work 關聯 | #94 開發分支：原 project／work 明確綁定、固定已採用 plan／需求引用、既有來源關聯；不轉移 session 控制權 |
| W07 | P0 | PM worktree bootstrap | 第三、四批：設定綁定、固定來源、自有 worktree、不可覆寫草稿與計畫 commit；不夾帶程式或其他 index 修改 |
| W08 | P0 | Plan revision 與採用流程 | 第四批：不可變文件、專業領域／來源、Git 證據、accepted CAS、理由與範圍版次；取消／撤權／回覆遺失整合驗證中 |
| W09 | P1 | PM runner 與 adapter selection | 第三批：writer／context／reply provenance 基礎與手動脈絡準備；可自動執行的 adapter、工具限制與切換待完成 |
| W10 | P1 | 一位 PM 討論與 evidence snapshot | 第三批：固定討論脈絡、來源／版本／缺口；AI 參與及跨交付 evidence 關聯待做 |
| W11 | P1 | Plan diff 與影響分析 | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W12 | P1 | Project Brief | 開發分支：五項來源總覽、固定計畫／證據快照、來源查閱；完整偏差與品質判讀仍待實作 |
| W13 | P1 | Roadmap 與 scope | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W14 | P1 | Kanban | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W15 | P1 | Sprint artifacts 與時間 | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W16 | P1 | Planning Review Retro | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W17 | P1 | Risk change decision view | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W18 | P1 | GitHub issue mapping | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W19 | P1 | Orchestrator readiness 與派工 | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W20 | P1 | 現有 integration 與 merge 接入 | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W21 | P1 | 現有 deploy 與 receipt 接入 | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W22 | P1 | AHP observer 與 create-own | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W23 | P1 | 第一個 native CLI adapter | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W24 | P1 | PM adapter 切換 | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W25 | P1 | Fleet readiness 與安裝版 | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W26 | P1 | Canonical MCP skills | 本批：PM read tools 與共用 operations；其他 v1.2 契約逐批加入 |
| W27 | P1 | 端到端實機驗收 | 自動化 fixture／驗收支援待做；真人實機部分由使用者處理 |
| W28 | P2 | 其餘具名 CLI 能力 | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W29 | P2 | 額外 reviewer 與討論模板 | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W30 | P2 | 成本與較完整預測 | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W31 | P0 | Idea thread 與明確轉 Planning | 本批：持久 Idea 與指定輸入轉 Planning 草稿 |
| W32 | P0 | PhaseProfile 與 command policy | 本批：九種 profile 契約；Idea／Planning 可用，runtime 限制待接 adapter |
| W33 | P0 | InputRecord 與循序資訊取得 | 本批：來源與確認者、假設／未知分離、context-first 契約；研究 runner 待做 |
| W34 | P0 | Thread plan change Sprint batch 狀態 | thread、Planning 草稿修訂、文件與 accepted scope 分開；Sprint／batch 與工程 work package 待做 |
| W35 | P1 | 架構與 SDD 工程工作包 | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W36 | P1 | OpenSpec read adapter | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W37 | P1 | OpenSpec 自有產物與 reconciliation | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W38 | P1 | 有界批次與工作包交接 | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W39 | P1 | DocumentSnapshot 與五視圖 SVG | 開發分支：同一交易的不可變專案快照、權限過濾與摘要 digest；五視圖 SVG 待做 |
| W40 | P1 | 專案文件與簡報 | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W41 | P1 | Actor 決策責任與 follow-up | 本批：workspace decision role；跨成果決策與 follow-up 待做 |
| W42 | P1 | Idea 到 Review 真實驗收 | 自動化 fixture／驗收支援待做；真人實機部分由使用者處理 |
| W43 | P0 | ProjectLifecycle 與 Idea 長期停留 | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W44 | P0 | ReleaseBaseline 與發布 receipt | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W45 | P0 | 閒置與 workload coverage | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W46 | P1 | Maintainer profile 與政策執行 | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W47 | P1 | 依賴 Issue PR 維護來源 | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W48 | P0 | 維護排程與恢復 | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W49 | P1 | 每週 Maintenance Review 與改進研究 | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W50 | P0 | 暫停 關閉與 reopen | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W51 | P1 | 維護 API MCP skills 與 UI | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W52 | P1 | 完整生命週期實機驗收 | 自動化 fixture／驗收支援待做；真人實機部分由使用者處理 |

## 工作證據與分析 adapter

[#94](https://github.com/teddashh/bat-agent-connector/issues/94) 接入既有 Connector project／work，
保存原工作版本、已採用計畫引用及需求 ID。證據由中央 journal 收集，不接受 caller 提供的
完成內容；工作回報、驗證、PR 合併與部署保持各自的來源與時間。讀取時重新檢查來源可見性。
Web／Tauri 在討論旁提供明確連結與快照查閱，不依 repo 名稱自動推測歸屬。
本批仍在分支驗證，尚未包含完整品質判定、偏差分析與五視圖。

Linux native PM analysis adapter 已在開發分支整合：明確要求一次分析後，使用新建的自有
原生 thread、固定討論脈絡與限制工具的 runtime。API／MCP／Web／Tauri 共用原 operation，
完成回覆連同模型、原生事件與來源保存；取消與失聯查回原程序，不重送或接管其他 session。

2026-10-11 的兩次各自獨立合成模型操作：第一次因正式 summary terminal 尚未支援而保守
拒收，維持 aborted；修正並通過事件／journal 回歸後，新的操作以 Codex CLI 0.160.0、
模型 gpt-6.1-sol 成功完成單次分析、來源匯入、程序退出與 writer 釋放。虛構會員範圍測試中，
回覆保留登入／忘記密碼為已確認，推薦码與折扣為未採用建議。沒有機隊、repo 或真實工作資料。
這是 Linux 原生討論分析的有限實測，不代表 BAT 路徑、PM 文件 writer、全平台或 M4 驗收。

## 固定快照與 Project Brief

開發分支加入 `pm.snapshot.create` 與 API／MCP／Web／Tauri 的共同讀取契約。
五項總覽使用同一個時間點的 accepted plan、明確工作關聯與已保存證據；沒有正式基準時，
草稿只列為候選。工作回報、驗證、合併與部署保持原來的意義，未評估的品質與偏差清楚標示。
目前是可追溯的來源總覽，尚不宣稱已完成專業 PM 的完整成果判讀與偏差分析。

工作空間首頁顯示完整總覽；進入討論後，總覽置於右側，對話保留主要位置。
原始快照保持不變，但每次讀取重新核對來源權限，不向已撤權的讀者顯示私有證據。
文件／SVG／簡報可沿用此快照；這些 renderer 尚未完成。

## 後續順序

1. 完成目前 Git／journal 採用整合；接入具備已查證工具限制的 PM 分析 adapter 與原生回覆證據。
2. 工作／證據關聯、固定規格版本、工程工作包與就緒判斷。
3. 完整五視圖、Sprint／Review／Retro、決策與偏差，沿用現有 Orchestrator 交付並回填。
4. 發布基準、維護政策與有界排程、Maintenance Review、原子暫停／關閉與明確重開。
5. 依里程碑加入 AHP／CLI、文件／簡報／SVG 與安裝版自動驗證。

真人實機驗收由使用者負責；Agent 仍提供自動測試、fixture、操作說明與未驗證標示。
正式簽章、Mac 公證、正式更新通道及 Linux 原生憑證持久儲存維持先前排除範圍。
公開文件、測試、issues 與 PR 只使用一般化範例，不包含個人機隊資訊。
