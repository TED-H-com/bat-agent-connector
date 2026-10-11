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

第一、二、三批已合併於 #86、#90、[#92](https://github.com/teddashh/bat-agent-connector/pull/92)。
第四批計畫採用 [#93](https://github.com/teddashh/bat-agent-connector/pull/93)、工作證據 [#95](https://github.com/teddashh/bat-agent-connector/pull/95)、Project Brief [#96](https://github.com/teddashh/bat-agent-connector/pull/96) 與原生分析 [#97](https://github.com/teddashh/bat-agent-connector/pull/97) 已合併。未安裝到使用者環境，也未宣稱完成真人實測或 M4。

下表的「本批」是這次的實作範圍；測試／審查以 PR 證據為準。未完成部分保留在原編號下，
不以新增一張畫面或資料表當作完整 PM 能力完成。

| 工作 | 優先級 | 原計畫項目 | 狀態／尚缺 |
| --- | --- | --- | --- |
| W01 | P0 | Workspace identity 與 scope migration | 本批：workspace identity／membership；待舊專案明確綁定與部署遷移 |
| W02 | P0 | 全入口 controller policy | 第二批：既有執行入口、最後 frame、背景恢復與清理已接入原 controller／incarnation；驗證見 #87 的實作 PR |
| W03 | P0 | Operation request key 與查回 | 已接入 PM 原 request key、Git／journal 查回與取消；部分 Git transaction 保留原 writer，驗證以各批 PR 為準 |
| W04 | P0 | BAT capability adapter | 第三批：唯讀選項、native replay／completion 與能力缺口；自動 PM 執行仍缺工具限制契約 |
| W05 | P0 | Evidence IDs 與 replay 去重 | #95 已合併：固定來源／digest／journal cursor、快照與重播去重；DoD 尚未評估，不把收集證據算成驗收 |
| W06 | P0 | Session worktree work 關聯 | #95 已合併：原 project／work 明確綁定、固定已採用 plan／需求引用、既有來源關聯；不轉移 session 控制權 |
| W07 | P0 | PM worktree bootstrap | 第三、四批：設定綁定、固定來源、自有 worktree、不可覆寫草稿與計畫 commit；不夾帶程式或其他 index 修改 |
| W08 | P0 | Plan revision 與採用流程 | #93 已合併：不可變文件、專業領域／來源、Git 證據、accepted CAS、理由與範圍版次；不是執行授權 |
| W09 | P1 | PM runner 與 adapter selection | #92／#97 已合併：共用 writer、原生回覆與 Linux 單次分析；BAT 工具限制、其他 adapter 與切換待完成 |
| W10 | P1 | 一位 PM 討論與 evidence snapshot | #95／#96／#97 已合併：固定工作證據快照與 Idea／Planning 原生分析；完整 Follow-up／會議引導與品質判斷待做 |
| W11 | P1 | Plan diff 與影響分析 | 開發分支：固定版本／來源／接受條件比較、明確工作關聯的影響候選；語意判讀與採用變更流程仍待做 |
| W12 | P1 | Project Brief | #96 已合併：五項來源總覽、固定計畫／證據快照、來源查閱；完整偏差與品質判讀仍待實作 |
| W13 | P1 | Roadmap 與 scope | 開發分支：固定採用需求、里程碑／依賴、原定／預測／實際證據與歷史；Git materialization、里程碑品質驗收仍待做 |
| W14 | P1 | Kanban | 開發分支：原工作與執行紀錄的唯讀工作看板、逐頁 WIP／時間來源／attempts；相依、分派、WIP 政策與就緒判斷待做 |
| W15 | P1 | Sprint artifacts 與時間 | 開發分支：Product Goal／有序 Backlog／DoD、固定 Sprint Goal／角色／期間、選入工作與關閉歷史；品質驗收與完整事件待做 |
| W16 | P1 | Planning Review Retro | 開發分支：各自輸出、固定討論依據、明確完成與改善追蹤；Daily Scrum、LLM 會議引導與品質判定待做 |
| W17 | P1 | 風險、變更與決策 | 開發分支：固定計畫／快照來源、明確根因去重、版本決策與原採用結果關聯；自動分析、跨快照追蹤與提醒待做 |
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
| W33 | P0 | InputRecord 與循序資訊取得 | 開發分支：來源與確認者、假設／未知分離、固定八領域資訊與已確認回答的續接；語意缺口判斷、循序提問與研究 runner 待做 |
| W34 | P0 | Thread plan change Sprint batch 狀態 | thread、Planning 草稿、文件與 accepted scope 分開；開發分支加入 Sprint、治理與工程 work package；batch／實際交接待做 |
| W35 | P1 | 架構與 SDD 工程工作包 | 開發分支：固定採用需求／接受條件、規格 bytes、獨立程式版本、設計／技術工作／驗證與交付安排，API／MCP 準備及 Web／Tauri 審閱；實際 coding handoff 待做 |
| W36 | P1 | OpenSpec read adapter | 開發分支：既有 PM carrier 原固定 commit 的唯讀標準文件、來源 digest、Web／Tauri／API／MCP；其他 commit、自訂 schema 與 CLI JSON 契約待做 |
| W37 | P1 | OpenSpec 自有產物與 reconciliation | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W38 | P1 | 有界批次與工作包交接 | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W39 | P1 | DocumentSnapshot 與五視圖 SVG | #96 已合併：同一交易的不可變專案快照、權限過濾與摘要 digest；五視圖 SVG 待做 |
| W40 | P1 | 專案文件與簡報 | 待開發／接入 v1.2；既有底層能力按計畫重用 |
| W41 | P1 | Actor 決策責任與 follow-up | 開發分支：指定決策者、改善責任、固定治理／會議結果與最多三項 Follow-up 議程、明確前次比較；語意效果判斷與自動討論待做 |
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

這批在開發分支接入 API／MCP／Web／Tauri，尚未安裝或宣稱 M3／M4 完成。

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

這批完成 W35 的固定描述與審閱基礎，W19／W38 的工作包交接、批次與實際派工仍待接入。
