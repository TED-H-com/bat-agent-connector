# Better Agent Dashboard：共用產品與 Tauri 介面方向

2026-10-17：owner 的 [EC-1.1 體驗收斂決議](experience-convergence.md)統一既有
v1.3／Work-Centric 的 UI 與交付順序；保留本頁核心架構及 PM 生命週期要求。
日常開工與差額見 [v1.3 handoff](v1.3-agent-handoff.md)，不另立 v1.4。

目前方向以已採用的 [PM v1.2 計畫](pm-v1.2-plan.md)為準：專業 PM 是產品核心，
涵蓋 Idea、Planning、工程與交付、Maintainer 及暫停／結案。本頁保留既有底層和 UI
決策；v1.2 新增範圍與完成情況見[專用狀態表](pm-v1.2-status.md)。

日期：2026-10-08。來源：產品負責人提供的《Better Agent Dashboard Tauri 校正開發計畫
第二版》v2.0，§01–27。本文保留 repository 需要的決策；私人環境設定不放入 repo。
產品負責人後續澄清：**v1、v2 是同一產品的計畫／UI 設計修訂，不是兩套產品或分開的功能發行。**
既有 Connector、Task Service 與共用功能 backlog 持續累加；依第二版採用 Tauri 介面方向，
保留以下明列的中央權威、唯讀來源與不做 Hub 匯入器等決策。檔名保留以維持既有連結。
實際提交與證據見
[implementation-status.md](implementation-status.md)，既有細節仍以 `docs/design/` 為準。

2026-10-10 結案範圍更新：產品負責人排除正式簽章、Mac 公證、正式更新通道與 Linux 原生憑證持久儲存；46 項真人實機驗收由使用者處理，不列 Agent 待辦或 Issue #59 結案門檻。
其餘實作、自動回歸、原生 fixtures 與合併證據仍須完成。下文保留原計畫與里程碑參考，交付判定以此最新範圍及[目前實作狀態](implementation-status.md#目前續作)為準，不宣稱未執行的真人驗收已通過。

## 原始需求與後續澄清

以下整理產品需求與架構決策。核心是：使用者在 BAT desktop/mobile 開發，Hermes／Grokbot
經 Connector 派工，Dashboard 統一呈現專案、工作、sessions、worktrees、成果與歷史，並能直接
執行同一套中央管理操作。Fleet 負責 client 的連線與啟動；Project Hub 提供可重用的管理規則
與介面。人工資源永久唯讀，Agent 在獨立 managed clone/worktree 開工，成果經 Git／共同 PR 整合。

產品負責人本次另釐清主機與 repository 的關係，優先於上傳計畫 §16 的直接跨主機傳輸要求：

- GUI、CLI、MCP 是操作入口；實際工作留在所選 BAT 主機的 workspace。切換入口不搬動工作區。
- 涉及 Git 的工作依明確的 host／workspace／repository 綁定；不同主機可以各有同一 repository
  的 clone，不因路徑或名稱相同就自動接續、同步或取得管理權。
- 同主機接續保留固定 checkpoint 與獨立 managed 工作區；不要求先將該本機 commit 發布。
- 若明確需要另一台主機取得程式碼，先發布選定 branch／commit 到綁定的 GitHub repository，
  再 fetch 固定 commit，在目標 managed 工作區開工。人工原目錄的 pull 由使用者自行操作。
- 未發布內容留在原主機；Connector 直接 relay Git pack/bundle、搬運別台 workspace 的能力
  **不列本次產品交付條件**。一般附件上傳、成果擷取／下載及必要的 artifact materialization 保留。

未合併的直接 Git-pack 實驗已獨立保存，沒有進入 main。GitHub 同步與明確 repository 綁定若
尚缺實作，仍如實列出；不把移除額外能力當作測試或里程碑通過。這項澄清不移除其他既定
功能、Rust Fleet parity、中央 authority 或人工保護；installed/live 人工驗收的責任與結案範圍依上方最新更新。

## Dashboard 整理方向

產品負責人指定 [Project Hub](https://github.com/kieiken/project-hub) 清楚的 session 整理為
介面參考；Tauri 是同一 Dashboard 的桌面入口。保留既有色彩與元件，以清楚的工作歸屬、
精簡清單、明確狀態及詳情層級讓大量 sessions 仍容易瀏覽，繁體中文採自然的使用者用語。
分組只能使用中央已有的 host／workspace／project 關係，不從路徑或名稱猜測歸屬。
載入部分資料時不得把筆數當成完整總數；收整與歷史必須保留穩定 ID，不能把 idle、離線或
沒有 tab 當作完成。共用 frontend 的 browser fallback 同樣維持這套資訊層級。

## 固定決策（R00）

2026-10-10 介面補充：產品負責人以 Project Hub 截圖指出全域分頁過多，要求左側直接展開
專案與工作樹，選中後在右側一起閱讀對話、回覆與查看成果。這是共用 Web／Tauri 的主要
導覽方向；管理工具可收納，Windows Fleet／連線入口保留。專案歸屬只沿用中央明確關聯，
不依主機、資料夾或名稱推斷，也不改中央與 BAT 的責任分工。


2026-10-10 首次安裝補充：正式桌面包應自動建立產品所需的 runtime、中央背景服務、資料與
個人身分，持續維持已選定連線；使用者可從系統匣／選單列點一下開啟 Dashboard 網頁。
關網頁或 UI 不停止中央工作。連接既有中央是進階加入路徑，不是一般使用者的首次設定。
BAT／GitHub 登入、信任與授權仍透過引導完成，不要求先裝 Python、手改 JSON 或理解 actor。
早期 client-only 驗證包未提供此能力；PR #81 已加入 bundled runtime、managed provisioning 與引導設定，來源及各平台檢查見[目前實作狀態](implementation-status.md#目前續作)與
[Managed installation](../design/managed-installation.md)。下載時須核對該候選的套件，不把歷史 release 當作新版。Web／Tauri 仍共用同一中央與前端。

2026-10-09 補充：Web 與 Tauri 都是持續支援的產品入口，共用 `desktop/src` 與中央
Connector／Task Service。中央本身提供 Web 靜態介面及 API，不因關掉 Tauri 而停止工作。
畫面與中央操作保持對齊，本機憑證、Fleet、系統匣及原生檔案能力依平台提供；
草稿目前各入口獨立保存。建置、版本相容及驗收細節見 [共用前端設計](../design/shared-frontend.md)。

1. 新桌面程式使用 **Tauri 2、Rust、Vite、TypeScript**，放在本 repo 的 `desktop/`。
   保留既有 Dashboard 行為與 CSS，不要求換 React/Vue。`desktop/src` 是共用 frontend；
   browser fallback 使用同一 source 的產物，generated files 必須標示來源及 build 指令。
2. **Python Connector / Task Service 是唯一中央業務後端**。HTTP、MCP、CLI、Tauri
   共用現有 ActionDef、OperationService、resource policy、task coordinator 與 journal。
   Rust 不另造任務排程、Git/GitHub delivery 或中央帳本；離線不自動啟第二個 daemon。
3. **不做 Project Hub 匯入器**。#39 不合併、不列依賴，不新增 snapshot parser、Hub
   source mapping 或匯入驗收。保留已完成 project/work-item 功能，可選擇重用 Hub
   規則、UI 與測試，記錄 upstream SHA、來源檔、改接與授權。
4. **B05 改為 Connector 自有資料升級**：既有 IDs、階層、外部連結、provenance、
   operation/event history 保留，不讀 Hub snapshot。不刪既有使用者資料。
5. **人工 BAT desktop/mobile 資源永久唯讀**。Unknown 只可依既存 creation intent、
   receipt 及 binding 恢復原 managed 身分。接續人工工作使用固定已 commit checkpoint，
   在 Connector 自有 clone 建新 worktree/branch/session；原人工 checkout 不變。
6. 工作項目、Task Service task、execution、session、worktree、operation 是不同身分。
   普通待辦或 direct managed session 不自動變成另一套 task recipe。
7. Dashboard 有權限且已檢視固定範圍時可直接派工、傳檔、整合、merge、deploy、整理。
   按鈕呼叫中央操作，不先經 LLM 重複批准。產品具有部署能力不代表開發工作可任意
   操作正式 hosts/environments。
8. ACK、accepted、idle、無 tab 不代表完成或死亡。送出後結果不明保留原 ID/key、
   claim/worktree 與 receipt，先查回。Cancel 不能抹除外部效果或釋放仍在使用的排他資源。
9. Fleet 沿用已驗證 inventory/readiness/ownership。早期 PS adapter 與 Rust 移植任一
   時刻只允許一個本機 tunnel owner；Rust parity 是完整產品交付 gate。
10. Canonical API、MCP/CLI adapter、agent skill 分層。Hermes/Grokbot 只有薄的環境
    配接，使用自己的 principal；能力不足不改走 raw BAT 或 shell 旁路。

## 中央與 native 合約

R00 查核基底：Connector `224bc7ed2a3ccce4d443f4d224a07df80da2babb`（#41 merge）。
實際定義為 [`api_v1.py`](../../src/bat_agent_connector/api_v1.py)、
[`operations.py`](../../src/bat_agent_connector/operations.py) 及各 `ACTIONS` registry；
本表是 adapter 的接點，不是第二份 API registry。

| 接點 | 現有合約與使用方式 |
| --- | --- |
| 身分／相容 | `GET /api/v1/version`：`api_version=1`、`contract_version=2026-10-08`、connector version；`GET /api/v1/capabilities`：已驗證 actor、scopes、actions、features、hosts、repositories、deploy_recipes |
| 操作 | `POST /api/v1/operations` + `Idempotency-Key`；body 為 action、target、params、preconditions；actor 由 token 決定，不信 request 自稱 |
| 查回 | `GET /api/v1/operations/{operation_id}`，以及既有 cancel/resume endpoints；accepted 後保存 operation ID，未知時保留原 key |
| 資源／管理 | 沿用 `/hosts`、`/sessions`、`/projects`、`/work-items`、`/checkpoints`、`/integrations` 等 `/api/v1` read models；#35 history/relations 以合入後 capability 為準 |
| 即時事件 | `/api/v1/events` 與 `/api/v1/events/stream`；中央 seq 與本機 Fleet events 分開。只保存已處理 cursor；retention reset 重讀 snapshot、保留草稿 |
| 現有 domain actions | session.send/answer/interrupt、checkpoint、integration、project/work_item、github.pr.update/merge、deployment.start、delivery.merge_and_deploy；精確名稱／scopes 讀 registry/capabilities |
| Native adapter | 受限的 connector request、Fleet state/selection、開 BAT profile、選檔 handle、artifact download、視窗/tray；每項 capability 隨實作揭露，不先假稱已存在 |

中央目前僅接受 loopback Host，遠端 client 沿用可信 SSH tunnel 或部署既有的驗證通道。
Tauri 不以開放 browser Origin 或遠端特權 WebView 解決 transport。Endpoint 來自可信配置；
native 驗證 method/path/大小及 window/command 權限，不提供任意帶 token URL、shell、PID kill
或任意檔案路徑 API。長效 token 留在 OS 保護儲存或 Rust；frontend 只有身分及 credential handle。
Fleet observe 身分與使用者 mutation 身分分開。

## 執行與整合不變條件

- #36 的 task pause/verifying/binding/incarnation/control version/pending-command gate 與
  #37 的 confinement/start claim/transport fence 累加保留，不能在解衝突時只留一層。
- 送出前拒絕可回滾；送出但 ACK 遺失不能移除可能已使用的 worktree；ACK 後 evidence
  read 失敗不能重送 start。Same ID 重試須與 rollback 留下的 metadata 一致。
- #37 一般 start lost-ACK 及 #38 remove 後 readback recovery 已有修補，保留回歸，
  不把同名修補重算新功能。已完成 effect receipt 優先重播，恢復只檢查未完成效果。
- 附件固定 revision/digest/manifest，驗證 materialization ready 後才開工；不能把 client 路徑
  當遠端可讀路徑。程式碼跨主機同步經已綁定 GitHub repository fetch 明確已發布 commit，
  不直接搬運人工 workspace 或未發布 Git objects。
- 多成果在獨立 integration workspace 依固定 SHA/順序組合同一 PR，普通 push；
  preview 變動停止舊意圖。Merge、deploy 各自保存效果，支援「已合併、尚未部署」。
- 部署 recipe 固定 source SHA、operation ID、revision/route/environment；缺 source SHA
  映射零 POST 拒絕。Composite resume 同時要 merge/deploy scopes。外部 run 未終結時
  cancel 仍讀回並保留 environment slot，不把 workflow success/skipped 當部署版本證據。
- Reviewed cleanup 保留人工/unknown、active writer、pending、未對帳 command、live
  consumer、引用與唯一內容。Task-owned 的 Part A `TASK_OWNED` 保留直到 coordinator
  cleanup 通過驗收；retained-content restore API 是後續擴充，不阻擋完整產品交付。

## 分期與驗收

| 階段 | 退出條件 |
| --- | --- |
| M0 | R00 共用產品決策、現況/owners/合約、排除 importer |
| M1 | 可安裝 Tauri、真中央資料、manual/managed/unknown、history/relations、pending/linked 即時刷新、close-to-tray、Dashboard-only；PS 可暫為唯一 Fleet backend |
| M2 | R01/R05/R06/R08 必要控制與輸入、pause/unknown recovery/cleanup、Rust Fleet parity |
| M3 | R07/R09/R10：同 PR 整合、固定版本 merge/deploy、skills/install 同版、完整實機展示 |

沿用 A01–A10、B01–B05（B05 已改義）、C01–C07、D01–D06、E01–E06，加入 T01–T12：
打包 app；視窗/tray/多登入；Dashboard-only；PS/Rust ownership 遷移；PID/SID/inventory
證據；native 權限；pending/linked 即時一致；native 檔案；client 恢復不重派；固定 bootstrap；
backend/principal 隔離；共用 UI/版本/簽章相容。完整 46 項仍以 v2 原計畫 §24 為準。

正式展示使用同一 release candidate：Windows 登入 → Fleet → 中央 → 唯讀人工 checkpoint
→ 兩個獨立 managed 工作與附件 → 同 PR → merge/deploy 並查實際產品版本 → reviewed cleanup
→ client 重開不影響中央 → 新 agent 以自己的 MCP 身分完成支援流程。
每台啟用無人寫入的 host 另留 protected-roots 實際隔離證據。Mock/CI、branch、main、installed、
live 五種狀態分開記錄；未測平台、簽章或環境不宣稱完成。

## 尚未涵蓋

此文件完成 R00 範圍與接點，**不是 M1–M3 完成證據**。所有未整合 PR、native/Fleet、
事件 store、檔案傳輸、legacy operations、交付及實機項目見狀態表；不得由本文件推論已安裝。
