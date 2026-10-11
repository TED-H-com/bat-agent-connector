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
新增 controller ID 也不代表既有 session 寫入入口已完成跨 controller 隔離。

合併、安裝與真人實測狀態以本批 PR 的實際驗證為準；本表不宣稱 M0–M4 已完成。

## 工作與剩餘範圍

下表的「本批」是這次的實作範圍；測試／審查以 PR 證據為準。未完成部分保留在原編號下，
不以新增一張畫面或資料表當作完整 PM 能力完成。

| 工作 | 優先級 | 原計畫項目 | 狀態／尚缺 |
| --- | --- | --- | --- |
| W01 | P0 | Workspace identity 與 scope migration | 本批：workspace identity／membership；待舊專案明確綁定與部署遷移 |
| W02 | P0 | 全入口 controller policy | 本批只建立 controller 身分；所有既有 session 入口隔離仍待做 |
| W03 | P0 | Operation request key 與查回 | 本批重用 PM operation key／receipt；Git 採用與外部效果仍待驗證 |
| W04 | P0 | BAT capability adapter | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W05 | P0 | Evidence IDs 與 replay 去重 | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W06 | P0 | Session worktree work 關聯 | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W07 | P0 | PM worktree bootstrap | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W08 | P0 | Plan revision 與採用流程 | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W09 | P1 | PM runner 與 adapter selection | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W10 | P1 | 一位 PM 討論與 evidence snapshot | 本批：持久討論／來源／有限上下文；PM runner 與 evidence snapshot 待做 |
| W11 | P1 | Plan diff 與影響分析 | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W12 | P1 | Project Brief | 待開發／接入 v1.2；既有底層能力按計畫重用 |
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
| W34 | P0 | Thread plan change Sprint batch 狀態 | 本批：thread 與 draft 獨立；accepted scope／Sprint／batch 待做 |
| W35 | P1 | 架構與 SDD 工程工作包 | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W36 | P1 | OpenSpec read adapter | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W37 | P1 | OpenSpec 自有產物與 reconciliation | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W38 | P1 | 有界批次與工作包交接 | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W39 | P1 | DocumentSnapshot 與五視圖 SVG | 待開發／接入 v1.2；既有底層能力按計畫重用 |
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

## 後續順序

1. 補齊 controller admission、BAT capability wrapper、工作／證據關聯、PM 自有 worktree 與 BAT runner。
2. 接入 Git／journal 可恢復計畫採用、固定規格版本、工程工作包與就緒判斷。
3. 完整五視圖、Sprint／Review／Retro、決策與偏差，沿用現有 Orchestrator 交付並回填。
4. 發布基準、維護政策與有界排程、Maintenance Review、原子暫停／關閉與明確重開。
5. 依里程碑加入 AHP／CLI、文件／簡報／SVG 與安裝版自動驗證。

真人實機驗收由使用者負責；Agent 仍提供自動測試、fixture、操作說明與未驗證標示。
正式簽章、Mac 公證、正式更新通道及 Linux 原生憑證持久儲存維持先前排除範圍。
公開文件、測試、issues 與 PR 只使用一般化範例，不包含個人機隊資訊。
