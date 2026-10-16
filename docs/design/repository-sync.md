# 從已發佈版本建立工作

依[產品負責人的 host／repository 澄清](../product/realignment-v2.md#原始需求與後續澄清)及
[成果 Part C](artifacts.md#主機與-repository-同步part-c依產品負責人澄清)，跨主機程式碼來自明確綁定的
GitHub repository。這不是搬移 workspace，也不採用未發布的 Git pack／bundle。

## 明確綁定與操作

既有 `github.repos` 是 repository 設定權威。每個 repository 可額外設定：

```toml
[[github.repos]]
repository = "example/project"
sync = { remote_url = "git@github.com:example/project.git", bindings = [{ host = "worker", workspace_id = "exact-workspace-id" }] }
```

同一 host／workspace ID 只能綁定一個 repository。`sync` 不授予 integration push 權限；未設定時
不提供本功能。workspace 名稱、路徑相同、`task_service.repo_urls`、既有 clone 的 origin 都不是綁定。
主機需 write／orchestrate、managed root、SSH Git runner 及現有 confinement readiness。
`remote_url` 沿用 repository URL 驗證，不接受 token、任意本機路徑或呼叫者 URL；本機 bare repository
只在 loopback mock GitHub fixture 允許。

唯讀 preview 需要 `observe`，指定 repository、host、精確 workspace ID、`refs/heads/…`。
回傳 provider repository numeric ID、ref 的完整 40-hex head SHA、綁定 digest、workspace ID／folder。
preview 不建立 clone、session 或 operation；不是永久授權。

`repository.continue` 需要 `start`：

```json
{"action":"repository.continue","target":{"repository":"example/project","host":"worker","workspace_id":"exact-workspace-id"},
 "params":{"source_ref":"refs/heads/main","source_sha":"<40 hex>","agent":"claude","prompt":"<instructions>"},
 "preconditions":{"repository_id":123,"binding_digest":"<64 hex>"},"idempotency_key":"<original key>"}
```

選填 `params.model`（1–256 字元）沿用既有 session start；`params.artifacts` 為 ready revision 的
`{artifact_id, revision, digest}` 清單，沿用中央附件數量／總 bytes 限制。原 prompt 不被改寫，
只附上已驗證相對路徑與 digest manifest，合計仍受 BAT prompt 長度限制。
專案入口另固定 `params.project_id` 與 `preconditions.expected_project_version`；中央要求專案未封存、
版本相同且 repository 屬於該專案。省略 project_id 的原有 published-start 合約維持相容。

本切片只接受已檢視 ref 的**精確 head**，不提供 ancestor／tag／PR fork 選擇。首次 fetch 前 ref 前進
即停止，永不把選定 SHA 換成 latest。已成功 fetch／固定的 receipt 優先於之後移動的 ref；重播不要求
原 head 永遠停在遠端。repository numeric ID、原 ref／head、remote URL、host profile、workspace ID／folder、
managed root、設定 digest 存在同一 operation 的固定來源 receipt，不另建工作佇列。

## 效果、回復與邊界

1. `source.resolve`：唯讀確認 GitHub repository numeric ID／名稱、ref SHA、workspace 身分、設定與 preview
   digest。具名 key 先回既有 operation；不同 payload 同 key 拒絕。CLI 省略 key 時每次產生新 key 並回傳；
   MCP／HTTP 明確要求 key，不能用預覽內容自動去重。
2. `repository.fetch`：在 managed root 內建立本 operation 自有尚未 checkout 的 clone，核對 marker、無符號／硬連結、
   無 alternates／replace／危險 Git 設定，使用設定的 host Git 憑證 fetch 固定 SHA。GitHub 讀取證據與
   host `ls-remote` 的精確 ref／SHA 必須一致。物件驗證及 operation ref 固定成功後才能繼續。
   host directory flock 防止舊 SSH 程序與重啟後讀回同時寫，Git 子程序繼承 lock FD；丟失 reply
   只依固定 marker／ref／object 讀回或安全補完。
3. `worktree.prepare`：只在自有 clone 加固定 operation branch／worktree。重試已有 carrier 需核對
   common Git directory、branch、HEAD、原 creation intent；不 reset、pull、checkout 已有 worktree。
   有附件時，先以同一 operation 保留精確 revisions，再於 `.batc-inputs/` materialize。
   helper 額外核對 published clone 的 operation／binding marker、完整 source SHA、固定 branch／worktree。
   每次傳輸前重查專案／目的地，每個未送出 start／prompt frame 前再驗證 materialized bytes。
   已送出 prompt 的回復只讀原 receipt，不因輸入之後改變就重新傳輸或送出指示。
4. 沿用中央 `verify.start`、`session.start`、`send` 的獨立 durable intents、claim／capacity、confinement
   與 receipt。每個未送出的 BAT frame 前重新驗證 host／workspace／設定與 ownership。回覆不明保留
   session ID／carrier／claims；沒有正向 readback 不再送 start 或 prompt。已完成 receipt 不因新政策而消失。
   reservation 的 created_at 在 start frame 離開前寫入 operation。只有兩種讀回證明 frame
   沒有送出，並可在既有的取消與 lifecycle 檢查之後重跑：registry 沒有這一列、operation 也沒有
   reservation ref；或 registry 列的 `start_sent` 為 false，且狀態、task 與建立欄位仍屬於這次
   operation。已寫下的 ref 若列已遺失、重建、retire、被 task 接管，或列的 `start_sent` 不是
   false，都不證明沒送出，停止於 needs_attention，不重開或覆寫後繼者。
5. receipt-owned cleanup 投影保留原 operation／session／branch／path，沿用 reviewed cleanup 的全部
   writer、shared owner、command、content、retention／uncertain gates。不能因沒有 registry row 就取得管理權。

GitHub token 只在中央讀 API；不送到主機或放入 URL。Git 網路操作用主機既有憑證；本 action 沒有
GitHub write／push。checkout 的 hooks／filters／submodules 不執行。來源人工 checkout 不讀 index、
不改 HEAD／refs／worktree 註冊，不做 pull／reset／merge。原人工目錄的更新由使用者自行操作。

UI 文案是 **Start from a published version／從已發佈版本建立工作**。只能從 capabilities 的明確綁定
選擇目標，先檢視固定 repository／host／workspace／ref／SHA，再保存原 envelope／key。accepted 後
只讀原 operation；重啟、credential 切換或 generic error 不偷偷換 key。來源主機不會被接管或停止。

## 共用 UI 與原生橋接

`#/published` 由工作階段清單與新增工作頁的明確連結進入；capabilities 沒有綁定時不顯示入口。
共用 `repository-start.js` 先以唯讀 `POST /repository-previews` 檢視精確 repository／host／workspace ID、
branch ref、完整 SHA，再凍結包含 agent／原文／preconditions 的請求與 key。尚未提交的預覽不在 reload 後
自動沿用；修改 ref／目標會清除預覽，晚到的舊回應不能替代新選擇。

原請求依中央 server／principal namespace 儲存；已知 operation ID 的 reload／event 只 GET 原操作。
未知 reply 重試保留同一 key／envelope，即使目前綁定或 host tier 改變也先交中央 replay 判斷；generic
403／409 不表示從未受理，不能據此旋轉 key。只有中央在 existing-key replay 之後、INSERT 之前回傳的
`REPOSITORY_NOT_BOUND`／`REPOSITORY_HOST_UNAVAILABLE` 可提供明確「準備另一筆」；terminal 結果也需使用者
明確建立下一筆。操作回覆須符合原 actor、key、完整 envelope、來源與 message ID；event 查回失敗不提交 cursor。

native bridge 僅增加固定且 typed 的 `/repository-previews`，接受四個 logical identity 欄位，不接受
remote URL、path、credential、force、query 或 operation key。它仍透過既有 `/operations` 執行中央動作。
成功 published execution 可進入既有 B2 managed artifact capture；unknown／尚無原始 prompt receipt 不成為候選。
介面不宣稱 clone／session 的完成表示任務完成、PR 合併或部署。

專案詳情的 **Quick project dispatch／專案快速派工** 進入 `#/dispatch/<project_id>`，重用同一個
composer。中央宣告 `features.project_dispatch.version = 1` 才提供新入口；只有唯一明確綁定時
自動選取目的地，多個綁定需自行選擇。預覽固定專案版本，專案更新會清除舊預覽而保留輸入。
標題／模型放在進階設定；附件沿用既有瀏覽器上傳、native file adapter 或已上傳 revision 選擇。
草稿依中央／身分／專案隔離；送出後固定原 envelope、key 與附件 digest，不建立額外 Work Item
或 Task Service task。封存或取消 host tier 後，未知回覆仍可用原 key 交中央判斷 replay。
同主機未發布狀態仍走既有 checkpoint continuation，此入口只消費已發布版本。
CLI `repository continue --project-id … --project-version … --model … --artifact '<ref JSON>'` 與
MCP `work_continue_from_repository` 使用相同 action；`--artifact` 可重複指定。

## 驗證與尚未涵蓋

測試使用臨時 Git、mock GitHub／BAT：同名未綁定、錯 numeric ID／URL／ref、移動 head、workspace
重綁、caller scopes、manual snapshot 不變、惡意 clone 設定／連結、fetch/start/send 斷線、重啟、
cancel、cleanup claim、capacity／confinement final-frame races、HTTP／RPC／MCP／CLI parity。
後端與 UI 分開提交；不以 fixture 宣稱 installed／live GitHub 或 host 驗收。

不含未發布跨主機搬運、任意 branch push、修改人工 checkout、task takeover、dirty snapshot、
LFS／submodule 展開、Git 憑證安裝、ancestor／tag 選擇。發布既有成果仍走原 integration／PR 流程，
本功能只消費明確已發佈版本。
