# GitHub 交付：PR 描述、合併、部署與回退

日期：2026-10-08。對應計畫 v1.0 的 §09、§10、§15–§18、§26、§28，工作包 W07／W08。既有功能的驗收為 C04–C07、D01–D04；Phase 1 補訂 metadata、rollback、environment ordering、merge scope 與完成驗證，Phase 2 Part A 已交付 metadata／merge 部分，主要驗收 C04、C05、C07、D03、D05、D06。

沿用本文件，因為新功能延伸同一套 `delivery.py`／`github.py`、recipe 與恢復流程；另開文件會有兩份完成判定。以下「現有」各節記錄起點；metadata／merge 的 Phase 2 規格已由 Part A 取代相應合約，Part B 已交付後端、非 Dashboard 入口與環境卡。PR head 更新仍見 [integration.md](integration.md)。Phase 1 規格與 review 修訂曾各自提交；以下保留當時的合約演進。2026-10-08 review 後分成兩步：Part A（已合併）metadata、merge preview／verify、C04／C05／C07、PR 卡與各入口文件；Part B（後端與環境卡已交付）部署 runtime evidence、generations／ordering、history／rollback、D03／D05／D06 與環境卡。

Part A review follow-up：修正 metadata 無落地的期限、預覽成本／保留、submit 前檢查位置與最後合併歸因；保留已批准的 action／digest／scope 與 Part B 邊界。

本輪 rebase 的固定來源為 main `2824be51b0c5de2726610f24845a771aa4cf3cfa`（#33），Part A replay 後為 `6ce4ec6`；合併 token 輪替、ambiguous transport／rate limit／204 run lookup 與 sent-write 讀取恢復。Part A 的唯讀 steps 不作為「已送出寫入」證據；Delivery 建表／索引使用每次 open 都執行的冪等 DDL，不佔 user_version。

PR #34 Codex review follow-up 以 `ed6378c` 為固定來源；補上 verify 時新 native stack 的拒絕與 unresolved metadata 第三種內容的 settlement，沿用已批准的 action／receipt／恢復路徑。

Journal DDL follow-up 以 `92b9c02` 為固定來源；移除純 DDL 的 user_version stamp，保留交易與既有資料回填，依共用 journal 規則區分 schema 與一次性資料步驟。

## 固定來源版本

Part B 起點：`b038d467b9e4203d060d1c32c9ee17caf26a46f5`（#40 metadata ACK conflict fix），分支 `feat/delivery-b`。後端包含 HTTP／MCP／CLI、capabilities 與接入文件。環境卡輪以 `9e8bc91` 為固定來源，先以 `f72e49a` 補上 stopped provider cadence／error 恢復，再接既有 store／operations；本輪不 rebase、不改 OperationService core。

| 來源 | 固定版本與用途 |
|---|---|
| Connector | `566eba6b63e49eeca38383f4029a3a7abec98801`，Phase 1 原始起點；Part A rebase 採以上 #33 pin。ACK conflict follow-up 採 main `208dd13d75f48e77915dda212ae84819ceef508e`（#34），分支 `fix/metadata-conflict-settle`。依據 `delivery.py`、`github.py`、`config.py`、`operations.py`、`task_journal.py`、`api_auth.py`、`resource_policy.py`、`api_v1.py`、`task_daemon.py`、`integration.py`、Dashboard、`tests/test_delivery.py` 與 `tests/fakegithub.py` |
| 計畫與交接 | 計畫 v1.0，2026-10-06；[前輪交接](../handoff/2026-10-08.md)。交接的 main pin 是前輪紀錄，本次以以上 Connector pin 為準；不將私有計畫複製進 repo |
| BAT | `b7419892fbc9946799b64cca24c2ec8c7fa15c42`，[worktree.rs](https://github.com/tony1223/better-agent-terminal/blob/b7419892fbc9946799b64cca24c2ec8c7fa15c42/src-tauri/crates/bat-git/src/worktree.rs)。本包不新增 BAT channel、SSH Git 或 worktree mutation；人工／unknown 規則沿用 [resource-policy.md](resource-policy.md) |
| GitHub REST | API version `2026-03-10`；2026-10-08 查核官方文件，adapter／fake 固定此版本。官方文件可變動，不宣稱是不變的 snapshot；未知 schema／無法證明的 scope 不放行 |

## 現有與新增行為差異

| 項目 | 固定版本的行為 | Phase 2 |
|---|---|---|
| PR metadata | `pr_preview()` 只有 title，無 update action | title／原始 Markdown body、寫前比較／寫後讀回；既有 scope `integrate` |
| Merge | `_merge()` 帶 head；409 只核對 head／method；未保存 reviewed base | 保存完整 scope preview、base、觀測範圍；偵測 stack／間接合併；既有 request 核對 action／scope；驗證最後版本 |
| 部署序列 | `_no_deploy_in_flight()` 比 recipe 名稱，並非 environment | repository ID＋environment 的 desired generation／dispatch slot，跨 recipe 共用，晚到結果為 superseded |
| 部署成功 | `_deploy()` 查 run／job，只有 on_merge 核對 run head | 查 pending environment、指定 attempt jobs、實際 runtime version／artifact／健康；workflow SHA 與產品 SHA 分開 |
| 歷史／回退 | 回執在 operation，無部署表／rollback action | 保存 deployment identity／evidence，以同 recipe 的舊紀錄建立新 rollback operation |
| Delivery UI | Part A 已有 metadata 編輯與 scope preview | 不依 PR 的 repository／environment 卡；desired／observed／last verified、游標 history、rollback／retry／superseded／drift 與限制 |

保留 `OperationService`、`ActionDef`、具名 steps、actor-scoped idempotency、中央 journal、`api_events`、既有取消／resume 與 integration 互斥。不建立第二個 task database、planner、任意 shell 或人工資源接管（計畫 §06、§09、§28）。

## 現有原則

- Dashboard 的按鈕與有權限的 agent 走同一個 handler；沒有 LLM 轉述，也不需要 task_id。
- 只有 `[[github.repos]]` 列出的 repository 能合併；只有 `[[deploy.recipes]]` 列出的 recipe 能部署。瀏覽器只能指定 recipe 名稱，不能傳 workflow 或任意 inputs。
- GitHub token 只在後端（`[github] token_ref`），不回傳給瀏覽器或 MCP。
- API 版本固定為 `2026-03-10`（`X-GitHub-Api-Version`），依據 GitHub 公開的 OpenAPI 描述撰寫。

## 現有合併：`github.pr.merge`

Scope `merge`。輸入：`target {repository, pull_number}`、`params {method}`、`preconditions {expected_head_sha}`（必填，完整 40 hex）。

1. 讀 PR。已合併：只有合併時的 head 等於 `expected_head_sha` 才算成功，並標明 `merged_by_this_operation`；不同 head 轉 `needs_attention`。已關閉、草稿、head 已變（`TARGET_HEAD_CHANGED`，409）直接失敗；有衝突轉 `needs_attention`。
2. 必要 checks 尚未完成（`mergeable_state = blocked` 且有未完成的 check run）時轉 `waiting_checks`，不送合併。
3. 步驟 `merge.submit`：`PUT /pulls/{n}/merge-async`，帶 `sha` 與 `merge_method`、`merge_action = default`（有 merge queue 就進 queue）。
   - 202：記下 request UUID。
   - 200 `merged`／`enqueued`：已合併或已在 queue。
   - 409：既有請求。只有 `expected_head_sha` 與方法相符才沿用它的 UUID，否則 `needs_attention`。
   - 400：先讀 PR；已在 reviewed head 合併則走正常結果驗證，否則仍 PR_NOT_MERGEABLE。
4. 輪詢 `GET …/merge-async/{uuid}`：`pending` 轉 `waiting_external`；`failed`（分支保護或規則在執行時才判斷）轉 `needs_attention`；`enqueued` 等 PR 真正合併，queue 狀態不算合併。
5. 讀回 PR，以 `merged` 與 `merge_commit_sha` 為準記錄實際合併版本。

送出後回應遺失（逾時、5xx、429、回應內容被截斷）：步驟記為 `uncertain`。回查 PR：已合併就補記成功；仍開著才再送一次，因為 GitHub 會把同一 PR 的待處理請求以 409 回傳原 UUID，不會產生第二次合併。

讀取沒有回應（含帶 rate-limit header 的 403）時 `waiting_external`，稍後再讀。讀取被拒（401、403、404）時，若這個操作還沒送出任何寫入就 `failed`；已送出合併請求或 dispatch 後則轉 `needs_attention`，因為 GitHub 可能仍在合併或部署，`failed` 會釋放 recipe 的部署鎖。修好 token 或權限後 `resume`。Token 每次請求重新讀取，所以輪替或會過期的 token（GitHub App token 一小時）不必重啟 daemon。

## 現有部署：`deployment.start`

Scope `deploy`。輸入：`target {recipe}`、`params {source_sha}`（完整 40 hex，通常是實際合併版本）。同一個 recipe 同時只允許一個未結束的部署操作（409 `DEPLOY_IN_PROGRESS`）。

| Recipe 模式 | Connector 的動作 |
|---|---|
| `workflow_dispatch` | 步驟 `deploy.dispatch` 呼叫已配置的 workflow，inputs 只能來自 `source_sha`、`operation_id`、`environment`、`repository`。200 回應帶 run ID；舊格式 204 則以 run-name 裡的 operation ID 找 run |
| `on_merge` | 不 dispatch。找該 workflow 中 `head_sha = source_sha`、event 為 push 的 run；還沒出現就 `waiting_external` |

追蹤 run：未完成時 `waiting_external`（環境審核等待會標明）。完成後要 run 的 conclusion 為 success，**且** recipe 的 `deploy_job` 也為 success；job 被 skipped 或不存在都不算部署（`DEPLOY_NOT_RUN`）。

Dispatch 回應遺失：以 run-name 含 operation ID 的 run 回查；找不到就維持 `uncertain`，不再 dispatch。所以 `workflow_dispatch` recipe 必須把 `operation_id` 列為 input，workflow 也要設定含它的 `run-name`，例如：

```yaml
on:
  workflow_dispatch:
    inputs:
      source_sha: { required: true }
      operation_id: { required: true }
run-name: deploy ${{ inputs.source_sha }} (${{ inputs.operation_id }})
```

接入條件：workflow 本身要在部署前檢查實際部署的內容等於 `inputs.source_sha`。Connector 的事後讀回不能取代這個檢查。

## 現有合併並部署：`delivery.merge_and_deploy`

需要 `merge` 與 `deploy` 兩個 scope。先依上面的合併流程取得實際合併版本，記入 `external_refs.merged_sha`，再以這個版本部署。部署失敗時操作為 `failed`，但合併結果保留；Dashboard 以 `merged_sha` 發起新的 `deployment.start` 重試，不會再合併。

Tauri v2 §18／C07：`delivery.merge_and_deploy` 的人工 resume 也同時需要目前 principal 的
`merge` 與 `deploy`，即使 actor 與原操作相同。縮減權限後回 `FORBIDDEN`，不改 operation、
steps 或 events、不呼叫 provider；恢復完整權限後沿用已完成 receipts 與固定 merged SHA。
Local admin 保留既有權限。唯讀查詢、背景 reconciliation、cancel 不增加這個 resume gate。
`tests/test_delivery_resume_scopes.py` 驗證相同／不同 actor、縮權、admin 與已 merge 後續做。

## 現有讀取

- `GET /api/v1/repositories/{owner}/{repo}/pulls/{n}`（MCP `github_pr_preview`）：head／base SHA、mergeable 狀態、check run 數量（總數、未完成、失敗）、允許的合併方法與該 repository 的 recipes。按鈕送出時以這裡的 `head_sha` 作為 `expected_head_sha`。
- `GET /api/v1/capabilities` 列出已配置的 repositories 與 recipes。

## 現有設定

```toml
[github]
token_ref = "env:GITHUB_TOKEN"   # 或 file:；建議使用只安裝在指定 repositories 的 GitHub App token
wait_max_s = 3600                # 等 checks、queue、run 超過這個時間就轉 needs_attention

[[github.repos]]
repository = "owner/name"
merge_methods = ["squash", "merge"]
default_merge_method = "squash"

[[deploy.recipes]]
name = "site-production"
repository = "owner/name"
environment = "production"
mode = "workflow_dispatch"       # 或 on_merge
workflow = "deploy.yml"
ref = "main"
deploy_job = "deploy"
inputs = { source_sha = "source_sha", operation_id = "operation_id" }
run_name_contains = "operation_id"
```

所需權限依用到的 API：Contents（merge）、Pull requests、Checks read、Actions read／write（dispatch 與讀 run）。不需要 Administration、secrets 或規則 bypass；`bypass_rules` 永遠不送。

## Phase 2 規格：資源與權限

| Action | Scope | 必要能力 |
|---|---|---|
| `github.pr.update`（新增） | integrate | configured repo、`allow_pr_update=true`（預設 false）、GitHub Pull requests write |
| `github.pr.merge` | merge | 既有 repo allowlist／method／checks；Contents write、Pull requests read，加完整 scope preview |
| `deployment.start`／`deployment.rollback`（新增） | deploy | configured recipe／environment、固定 identity；Actions read／write（dispatch）；rollback 另須明確支援 |
| `delivery.merge_and_deploy` | merge＋deploy | 同 repo 的 PR／recipe，各階段依自己的條件執行 |

Metadata 使用既有 integrate capability（計畫 §10、§15），與 head 更新在 action 層分開。Per-repo allow_pr_update 預設 false；只有啟用該 repo 後既有 integrate token 才能編輯 metadata，不新增 scope、不重新發 token。Capabilities 的 combined allowed 值同時檢查 merge＋deploy。

每個 mutation 經共用 action 的 allowlist／目標身分檢查，列入 `resource_policy.MUTATIONS` 的遠端 mutation 說明。沿用現有資源政策，不新增第二套 ownership 判斷；本包不呼叫 BAT／Git mutation，不修改人的 session、檔案、HEAD、index 或 refs。所有 actions 都不要求 task_id／managed execution／work item，只有人工成果的 PR 也可用（C06 精神、計畫 §18）。

Actor 來自 API principal，provider origin／repository ID 另記 scope 證據，GitHub credential 留在後端；不從 request body／Git author 猜 actor，也不以 metadata 讀回推定實際編輯者。HTTP、MCP、CLI 執行同一 handler，agent 不繼承 Dashboard 的 grant（C07）。Browser／MCP 不取得底層憑證；不要求 Administration、secrets 或 bypass，不送 `bypass_rules`。

## Phase 2 規格：輸入／輸出與入口

此節列完整兩步 contract；Part A 的 deployment.start 保持現有輸入，combined 只新增 merge preview preconditions。Generation、recipe digest、history 與 rollback 均屬 Part B。

Mutation 仍是 `POST /api/v1/operations`，現有 envelope `{action, target, params, preconditions, idempotency_key}`，回 `{operation}`；同 actor／key／內容回原 operation，不同內容回 `IDEMPOTENCY_CONFLICT`。沒有直接 PR PATCH／dispatch HTTP 旁路。

| Action | target | params | 必填 preconditions | 結果重點 |
|---|---|---|---|---|
| `github.pr.update` | `{repository, pull_number}` | `{title?, body?}`，至少一欄 | `{expected_metadata_digest}` | repository ID／PR、before／after digest、實際 title／body、URL、verified |
| `github.pr.merge` | 同現有 | `{method, preview_id}` | `{expected_head_sha, expected_base_sha, preview_digest}` | actual merged SHA、destination base／base_moved、UUID／queue、scope verification、attribution |
| `deployment.start` | `{recipe}` | `{source_sha, retry_of?}`（完整 40 hex；retry_of 從保存 identity 取得，SHA 必須相符） | `{expected_environment_generation, expected_recipe_digest}` | deployment ID、identity、generation、run／attempt、evidence、is_current |
| `deployment.rollback` | `{recipe}` | `{deployment_id}`（選定舊紀錄） | 同 start | 新 deployment ID、rollback_of、固定舊 identity、此次 run／generation／evidence |
| `delivery.merge_and_deploy` | `{repository, pull_number, recipe}` | `{method, preview_id}` | merge＋deploy 的前置條件 | `{merge, deploy}`，deploy 失敗仍保留 merge receipt |

Generation 從 0 開始，以選定順序遞增，不依 commit 時間或 SHA 大小排序。Recipe digest 包含 recipe 名稱、provider origin、repository ID、environment、mode、workflow／ref、inputs、ordering、verification、rollback。Browser 只能選 recipe／保存的版本，不能提供任意 workflow、inputs、URL、headers 或 shell。

| HTTP（reads 為 observe） | MCP／daemon RPC | CLI（新增） |
|---|---|---|
| `GET /api/v1/repositories/{owner}/{repo}/pulls/{n}?method=`（擴充） | `github_pr_preview`（擴充） | `batc delivery pr OWNER/REPO NUMBER [--method METHOD]` |
| `POST /api/v1/operations`：update | `github_pr_update` → `op_submit` | `batc delivery update-pr OWNER/REPO NUMBER --metadata-digest DIGEST [--title TITLE] [--body-file FILE] --key KEY` |
| 同上：merge／combined | `github_pr_merge`（薄 wrapper）與既有 `operation_submit` | `batc delivery merge --preview ID [--recipe NAME --generation N --recipe-digest DIGEST] --key KEY` |
| `GET /api/v1/deployments/preview?recipe=NAME` | `deployment_preview` | `batc delivery preview NAME` |
| `GET /api/v1/deployments?recipe=NAME&cursor=&limit=` | `deployments_list` | `batc delivery history NAME [--cursor CURSOR]` |
| `GET /api/v1/deployments/{dep_id}` | `deployment_status` | `batc delivery show DEP_ID` |
| `GET /api/v1/deployment-environments/history?recipe=NAME&cursor=&limit=`（Dashboard 薄 read） | —（既有 per-recipe reads 不變） | — |
| `GET /api/v1/deployment-environments?recipe=NAME` | `deployment_environment_get` | environment 隨 preview／history 輸出 |
| `POST /api/v1/operations`：start／rollback | `deployment_start`／`deployment_rollback` → `op_submit` | `batc delivery deploy NAME --sha SHA --generation N --recipe-digest DIGEST --key KEY`；`batc delivery rollback NAME DEP_ID --generation N --recipe-digest DIGEST --key KEY` |

CLI merge 讀保存的 immutable preview 組 envelope，不自動刷新；已加 `GET /api/v1/delivery/previews/{mpv_id}`、MCP `github_merge_preview_get`／RPC 同名，scope observe。Part B 另加 deployment_retry：從已保存部署 identity 組 deployment.start，需新 key／generation／recipe digest，不能重新 merge（D04）。新 MCP write wrappers 都沿用 `principal_daemon()`：confirm=true、BATC_API_TOKEN 必填，read_only server 不註冊。新 CLI mutation 也以 BATC_API_TOKEN 的 principal 呼叫，不退回 local admin；讀取沿用既有 read 授權。`operation_get`／`batc op` 用於追蹤、取消、resume。

PR preview 保留現有 fields，加 `{body, metadata_digest, metadata_update, merge_preview}`。`integration.pr_card()` 繼續包 `delivery.pr_preview()` 加既有 integration 欄位，不建立第二個 head 更新入口。Merge preview 保存 ID／digest／method、repository ID、固定 head／base refs／SHA、完整 commit set、觀測範圍、受影響 PRs、blocking／warnings、created／expires。

第一次 merge.submit 前才檢查預覽有效期；已送出／queued 的 operation 即使預覽過期也必須讀回原 request，不拿過期當作沒有合併。已成功的 metadata plan 與 combined merge receipt 重播其保存值，不重新套原始 stale 檢查而掩蓋已落地的副作用。

Deployment preview 回 recipe digest／readiness、environment generation、desired、current／last verified／observed identity、ordering、rollback 支援與限制。History 使用 `(created_at, deployment_id)` keyset cursor（預設 50、上限 200），逐筆回 identity、generation、operation／provider URLs、state／evidence、is_current、rollback_eligible／拒絕原因。Cursor timestamp 僅接受 signed 64-bit integer 或 finite float，保留 integer 比較精度；格式錯誤在 history query 前回 `INVALID_PARAMS`（HTTP 422）。讀取不 dispatch。

## Phase 2 規格：PR metadata update

計畫 §10 要區分 metadata 與 head。本次只更新 title／原始 Markdown body。Body 可含使用者選定的工作項目／operation links，不加入自動管理區塊：現有 `work_item.link` 已管理 Connector 關聯，計畫未定義 marker／公開連結規則／同步權威。`links`、state、base、head、labels 等欄位拒絕，不能靜默忽略。

Title 是非空字串；body 是字串，空字串清空，省略保留。CLI body-file 保留原始換行；Dashboard 以 textarea 初值判斷是否修改，避免只改 title 時因瀏覽器 CRLF 正規化而順帶改 body。GitHub null body 正規化為空字串；不 trim 或改 Markdown／換行。`metadata_digest` 是 UTF-8 `json.dumps({"title": title, "body": body}, sort_keys=True, ensure_ascii=False, separators=(",", ":"))` 的 SHA-256。比較整對 title／body，單欄更新也察覺另一欄被改；不用隨其他 PR 活動變動的 updated_at 作 revision。

| 步驟 | I/O／前置條件 | 實際副作用 | 失敗恢復 |
|---|---|---|---|
| admission | integrate、repo allow_pr_update、合法 fields／digest、正整數 PR number；同 PR 無未 settlement 的進行中 update／unresolved write | 保存 operation 意圖 | 403／422／`PR_UPDATE_IN_PROGRESS`（409），換 key 不繞過 |
| `pr.metadata.plan` | GET PR 比 digest；保存 before 與 intended after，省略欄取 before | journal snapshot | 不符為 `PR_METADATA_CHANGED`，保存差異；zero PATCH |
| `pr.metadata.write` | step intent commit 後、PATCH 前再次 GET，核對 repo ID 與整對 before；PATCH `/pulls/{n}` 只送指定欄位 | 改遠端 title／body，可能觸發通知／事件 | 寫前變更停止；200 保存回執；失聯／429／5xx 為 uncertain，先回查 |
| `pr.metadata.verify` | 寫後再 GET，整對內容必須等於 after | 保存 observed、digest、URL；相符才 succeeded | 已 ACK PATCH 的不同結果立即保存 conflict settlement、metadata_reconciliation=PR_METADATA_CONFLICT／metadata_difference／verification_pending=false，釋放 PR，再以 PR_METADATA_CONFLICT／needs_attention 保留 audit；401／403／404 仍 verification_pending=true、無 settlement 並保留 lock，保存拒絕回執後在 step 外 needs_attention，resume 用 verify.retry.<seq> 只讀重查，不重 PATCH；含舊版非 200 receipt |

Write reconcile／重啟：after 是正向證據，`observed_intent=true`，不猜作者；before 在 write step 開始後未滿 10 分鐘仍維持 uncertain，沒有 RERUN；第三種內容為 conflict。Cancel／resume 不覆蓋或自動還原。處理方式是讀新內容、重新編輯、以新 digest／key 建操作；操作不能在 resume 換 precondition。

取消不掩蓋已收到的 PATCH 回執；保存 `write_acknowledged`／`verification_pending`，UI 顯示已修改、待驗證。尚未證明的 started／uncertain write 即使 operation cancelled，10 分鐘 settle window 內仍阻擋另一個 metadata update。Delivery 的只讀 reconciliation 亦處理 cancelled／cancel_requested 與 `UNCERTAIN_UNRESOLVED` rows，永不重 PATCH。

| Recovery 讀回 | Settlement／下一步 |
|---|---|
| after | 正向觀測證據；沿用已批准的 step／refs 完成路徑，保留 private service calls 的介面回歸測試 |
| 已 ACK PATCH，verify 讀回不同於 after | 寫入與 readback 均已結束，立即保存 status=conflict、code=PR_METADATA_CONFLICT、observed、settled_at 的 settlement 與差異 refs，不等 600 秒；釋放 admission lock，fresh-digest／新 key 更新可受理，舊操作保留 needs_attention 與 audit |
| PATCH 後 readback／uncertain write reconcile 被拒（401／403／404） | GITHUB_401／403／404、needs_attention；不固化成 failed write／verify step，修復 token／權限後 resume。Plan 與 PATCH 前讀取被拒仍 failed、zero PATCH，即使已有 readonly plan／write intent |
| before，step 開始未滿 600 秒 | 保留 uncertain／PR_UPDATE_IN_PROGRESS，等原請求讀回；取消不釋放 |
| before，step 開始已滿 600 秒 | 客戶端 timeout 早已結束；delivery 自己的 `pr_metadata_settlements` 保存 `not_applied`／`PR_METADATA_NOT_APPLIED`／observed／時間，釋放 PR，之後不再週期 GET 此 row；不改另一操作的 steps／refs |
| 第三種內容，unresolved step 開始未滿 600 秒 | 保留 uncertain／PR_UPDATE_IN_PROGRESS；不 PATCH、不 undo，等 settle window 結束 |
| 第三種內容，unresolved step 開始已滿 600 秒 | 沿用 not_applied 的 timeout／settle 界線，先保存 metadata_reconciliation=PR_METADATA_CONFLICT／metadata_difference=before、intended、observed／verification_pending=false refs，再於 pr_metadata_settlements 保存 status=conflict、code=PR_METADATA_CONFLICT、observed、settled_at。Receipt 釋放 admission lock 並停止背景 GET；step 不冒充 applied，不 PATCH／undo，新操作仍需 fresh digest |
| 已保存 not_applied 的非取消操作 reconcile／resume | 只讀保存結論，step 不再 PATCH；handler 以 PR_METADATA_NOT_APPLIED 明確 failed。原 UNCERTAIN_UNRESOLVED 操作保留 needs_attention，直到 caller resume；cancelled 仍 cancelled，不自動恢復 |
| 已保存 conflict 的非取消操作 reconcile／resume | ACK／unresolved conflict 都只讀保存結論，不再查 GitHub 或 PATCH；handler 為 PR_METADATA_CONFLICT／needs_attention，不能誤報 not_applied。原 UNCERTAIN_UNRESOLVED audit 保留至 resume；ACK conflict 的 audit／steps 保留，cancelled 仍 cancelled，新的 fresh-digest update 已可受理 |
| settlement 後舊寫入非常晚才落地 | 每個新操作仍在 plan 與 PATCH 前比較新 digest／整對 metadata，差異為 PR_METADATA_CHANGED、zero 新 PATCH；不偷偷覆蓋。仍受下段 GitHub 無 CAS 的最後讀寫窗口限制 |

Journal 保存必要的 before／intended／observed；沿用 api_events 的 action／target／狀態／錯誤摘要，不複製 body；完整內容在 operation／steps 與必要 settlement receipt。

[GitHub update PR](https://docs.github.com/en/rest/pulls/pulls?apiVersion=2026-03-10#update-a-pull-request) 沒有 body CAS。本規格是 read-compare-write 加寫後讀回，仍有最後 GET 與 PATCH 之間的窗口，也無法發現窗口內被覆蓋且沒留下不同結果的編輯。不宣稱零遺失更新；UI 在編輯區簡短說明限制。可編輯 open／closed／merged PR 的 metadata，依 GitHub 權限，不把 merge 狀態當本地寫入權。

## Phase 2 規格：stack／其他 commits 與 merge scope

官方 [async merge](https://docs.github.com/en/rest/pulls/pulls?apiVersion=2026-03-10#merge-a-pull-request-asynchronously) 會涵蓋 open downstack PRs。[REST stack membership](https://docs.github.com/en/pull-requests/reference/stacked-pull-requests-apis-and-webhooks) 提供原生線索；[Stacks API](https://docs.github.com/en/rest/pulls/stacks?apiVersion=2026-03-10) 的 `GET /repos/{owner}/{repo}/stacks?pull_request=N`、`GET …/stacks/{stack_number}` 提供由下到上的成員。不能只猜 branch 名稱。

本版只支援單一、非原生 stack 的 PR。原生 stack 一律 `STACKED_PR_UNSUPPORTED`，包括底層，因為其[上層分支的自動重整](https://docs.github.com/en/pull-requests/get-started/about-stacked-prs)亦未納入 contract。Preview 仍列實際 open downstack／上層影響；人工串接 chain 標 dependency，不假稱是原生 stack。這是本包的保守支援範圍，不是 GitHub 禁止單獨合併底層。

| 預覽步驟 | 讀取／保存 | 拒絕與恢復 |
|---|---|---|
| 目標 | GET repo／PR，provider origin、repo ID、head repo ID／ref／SHA、base ref／SHA、method | 身分不全為 `MERGE_SCOPE_UNPROVEN`；closed／draft／conflict 沿用代碼 |
| 原生 stack | 查 membership／Stacks API，核對成員 PR；列 number／title／head SHA／base ref／URL／影響原因 | 無法讀取、缺欄或不一致為 unproven；403／404 不能當作沒有 stack |
| Commit 範圍 | [compare](https://docs.github.com/en/rest/commits/commits?apiVersion=2026-03-10#compare-two-commits) 用兩個固定 SHA 分頁取完整 BASE..HEAD／parents／merge base／檔案摘要；重讀 PR 確認期間未前進 | 不用有 250 上限的 PR commit list 冒充完整資料；comparison 不完整／不相關 blocking；files 摘要有 300 上限，明示 truncated；每檔只存 filename／status／additions／deletions／changes／previous_filename，SHA 固定內容，不存 patch／blob／contents URLs |
| 其他 PR | 完整分頁讀 open PRs；查 base→另一 PR head 的 chain；查另一同 base PR 的 head 在 target head 可達但在 target base 不可達 | Chain 列 stack unsupported；可能間接合併列 `MERGE_SCOPE_EXPANDED`。純共用祖先不算，squash／rebase 不把 SHA 可達誤報成一定關 PR |

| 保存 | `pr_merge_previews` immutable `mpv_…`，有效一小時；digest＝目標／method／commit set／觀測範圍／stack／受影響 PR 身分與內容 | 換 method／scope 必須重新預覽；不保存 credential／主機路徑 |

保存前重用相同 repository／PR／method／digest 的未過期 row，不延長原 expires_at。每次保存清掉 expired 已超過 24 小時的 rows，但保留任何非 terminal merge／combined operation 的 params.preview_id；queue／resume／verify 仍讀原 snapshot。操作終態後，下次保存才可清除它的舊 row。

事件驅動讀取用 `from_event=true`（HTTP query／daemon read 參數）；每個 repository／PR／method 的 scope 最多 60 秒重算一次，head 或 base SHA 改變立即重算。每次仍 GET PR／checks，title／body／metadata_digest 等便宜資訊保持即時；手動載入仍重新查 scope。獨立 `pr_merge_scope_reads` 保存最後 checked_at／preview_id，digest 相同而重用舊 row 亦刷新節流時計；不改 immutable 文件／expiry，同 key 的並行重載共用 lock。舊 cache pointer 隨保存清理。節流僅控制事件觀測成本，不取得 merge authority。

[間接合併](https://docs.github.com/en/pull-requests/reference/pull-request-merges#indirect-merges) 與 native stack 分開列證據。其他 PR 沒有 metadata 關聯的 commits 仍按固定 BASE..HEAD 完整列出，不猜 task 所有權。PR／stack array response 要在 `github.py` 統一成 `{items}`；分頁讀到結束並核對總數，不把第一頁／錯誤視為空資料。

等待 required checks 時只比既有 PR read 的 head／base，不重算完整 scope。完整 scope 檢查在 ready 後、緊接 `ctx.step("merge.submit", …)` 之前執行一次，與保存 snapshot 比對：head 變 `TARGET_HEAD_CHANGED`、base 變 `TARGET_BASE_CHANGED`、PR／commit 範圍變 `MERGE_SCOPE_CHANGED`，保存差異，zero PUT。檢查放在 step function 外，暫時無法讀取為可 resume 的 MERGE_SCOPE_UNPROVEN／needs_attention，不產生 failed submit step；resume 再讀完整 scope。未知 submit 的 reconcile 仍在回 RERUN 前重查完整 scope。不 retarget／update branch／加來源／force push。API 只有 expected head，沒有 expected base／scope CAS；提交瞬間仍可被別人改動，須以最後結果驗證處理。

## Phase 2 規格：C04／C05 與 merge 恢復

保留 merge.submit、固定 head／method／merge_action=default、checks／queue、integration.apply 互斥。Metadata update 的本地互斥獨立，不取得 Git writer。

Merge method 在 admission 固定為保存 preview 的 method；單獨／combined、checks 等待、重啟與 reconcile 都只用這個值，不重取 default。每次 PUT 前重新檢查目前 allow_merge／merge_methods；撤回為 MERGE_DISABLED／INVALID_PARAMS，首次提交在 step 外失敗、zero PUT，未知請求的重送則 needs_attention、不再 PUT；已受理請求繼續只讀觀測／驗證。Explicit method 與 preview 不同仍在 admission 拒絕為 PREVIEW_MISMATCH。

| 回應／步驟 | 判定與恢復 |
|---|---|
| 202 | 保存 UUID，pending 為 waiting_external，不是 merged |
| 200 merged | GET PR 核對 reviewed head／真正 merged SHA，接結果驗證 |
| 200／輪詢 enqueued | 記 queue，GET PR 等實際合併；不重送、不部署 |
| 409 existing | 比 head、resolved method、merge_action=default，重查 repo／base／單 PR scope；缺證據或不同為 `EXISTING_MERGE_REQUEST`。default method 只有能證明解析成選定值才接受 |
| 400 | 先 GET PR；merged 且 head 等於 reviewed head 則走 merge.verify，不把拒絕的 PUT 歸因為本操作合併（merged_by_this_operation=false）。未合併／closed／不同 head 仍 PR_NOT_MERGEABLE；只讀拒絕或 ambiguous 時保留可恢復操作，resume 不重 PUT |
| 輪詢 failed | 沿用 MERGE_FAILED，不能 bypass |
| `merge.verify`（新增） | GET merged PR／commit parents、固定 base 到真正 merged SHA 的 comparison、受影響 PR 狀態；保存逐 PR 結果與 actual merged SHA |
| verify 時發現預覽未列的 native stack，含 target 與其他成員 | 不論其他成員仍 open 或已 merged，均 MERGE_RESULT_SCOPE_CHANGED／needs_attention；保留 merged_sha／preview_id／verified=false、stack number 與每個 member 的 number／state／current head_sha／base_ref，affected_prs 列其他成員；combined 不 dispatch |
| submit 後 PR／commit／compare／stack／recent PR read 被拒（401／403／404） | GITHUB_401／403／404、needs_attention；保留 merge receipt／lock，修好 token／權限後 resume，不重 PUT。Verify 拒絕回執在 step 中保存、在 step 外轉 attention；uncertain submit reconcile 的拒絕亦可 resume |
| UUID 404 | 保留已批准的 expired UUID 路徑：改查 PR／queue、不重 PUT；只有 PR 可讀才繼續觀測，PR 自身 401／403／404 仍 needs_attention |

Merge.verify 查實際 merged PR／commit 與 base history：PR 必須 merged；merge method 的 merge commit 第二 parent 等於 reviewed head，squash／rebase 則 merged PR 的 head SHA 等於 reviewed head；結果在 base branch history 上，且 reviewed base 是其祖先。merge.verify 在讀取前保存 step intent；readback 尚未答覆則 waiting_external，重讀／resume 以 merge.verify.retry.<seq> 保存另一次只讀證據，suffix 取前次 step seq，避免多次 resume 的 operation attempts 相同而重播舊拒絕回執。成功的 receipt 不再重驗或重送合併。若 PR 是別人事先合併、此 operation 沒有送寫入，唯讀驗證被拒仍 fail fast；readonly verify step 不冒充 write。保存 merged_onto_base_sha（merge／squash 的第一 parent，rebase 以實際 merged SHA 沿 reviewed commits 數量回溯的 base，證據不足時不猜）、base_moved、其他將一起發布的 commits／數量。

提交被受理後 base 前進是正常行為，包括 queue 先合成其他 PR，不比預覽 tree、不做 rebase blob mapping。UI 顯示「合併到較新的 base：另有 N 個 commits 會一起發布」。Combined 驗證通過就部署 actual merged SHA；§17 pipeline pre-deploy check 與 Part B D03 runtime evidence 驗證最後版本。

重查預覽 affected list（stack／chain／indirect candidates）及提交時新出現的 stack 成員。Native stack 會自動 rebase 上層分支，preview 已拒絕此副作用；verify 看見 target 與其他成員所在的新 stack，直接 MERGE_RESULT_SCOPE_CHANGED，不因上層仍 open 放行。Receipt 的 stacks[].members 保存全部成員的現況，affected_prs 保存其他成員。

原有 merged candidates 規則不變：須以其 head 在本次 head 的可達性、其 merge SHA／目標 merge parents／實際 base history 與時間等證據區分先前獨立合併，不能把合法 base 前進誤判。新 native stack 或已證明額外 PR 被掃入為 MERGE_RESULT_SCOPE_CHANGED；缺少證據才 MERGE_RESULT_UNVERIFIABLE。保存 merged_sha／merge_receipt／逐 PR 結果，scope changed 時 combined 不 dispatch。On_merge 可能已觸發，只觀測不假稱無副作用，不自動 revert。

若本次 merged SHA 是候選 merge commit 的祖先，候選為之後獨立合併，保存 `merged_after=true`／`independent=true` 並接受；驗證延遲不將後來的合法 merge 誤判成缺證據。補查提交後出現的 PR 用 [list pulls](https://docs.github.com/en/rest/pulls/pulls?apiVersion=2026-03-10#list-pull-requests) 的 state=all、sort=updated、direction=desc，updated_at 早於 operation created_at 就停止分頁；保留 admission 整秒，避免 GitHub 秒級時間漏掉同秒 merge。預覽已列的 affected candidates 與當前 stack 成員仍逐筆驗證，不因 paging 截止略過。

Lost reply／真正重啟：先 GET PR／已存 UUID，merged 時核對原 head／scope／結果。僅 readback 沒可歸因 request 證據時 merged_by_this_operation 不能 true。Open＋UUID 繼續查；UUID 24 小時過期／404 時改查 PR／queue，不當作 failed。保留 provider-specific deduplicated submit 的 RERUN：未知 UUID 時，必須重新證明完整固定 scope 未變、PR 仍 open，再送完全相同的 head／method／action；GitHub 的同 PR pending request 會回 409，再核對 options。只有 PR open 不足以重送；scope 讀不到或已變則 uncertain／needs_attention。這是經過回查的同 intent，不是換版本重試；Resume 不換 preview。

## Part B（第二步）：recipe／部署證據／rollback limits

保留 workflow_dispatch／on_merge。每個 environment 只有一條觸發路線；多 recipe aliases 必須使用同 ordering group／觸發模式，否則設定拒絕。以下是新增設定，非已可用 TOML；正式接入值待配置。

```toml
# 置於既有 [[deploy.recipes]]，其他欄位沿用上例
inputs = { source_sha = "source_sha", operation_id = "operation_id", generation = "environment_generation" }
ordering = { mode = "serialized", concurrency_group = "deploy-owner-name-production", cancel_in_progress = false }
verification = { kind = "http_json", url = "https://deployment.example/version", version_required = true, health_required = true }
rollback = { supported = true, identity = "source_sha", not_undone = ["不撤銷資料庫 migration", "不撤銷已送出的外部通知"] }
```

Inputs 白名單新增 environment_generation／artifact_id／artifact_digest，其他沿用。Dispatch 必須有 source_sha／operation_id；ref 是配置的 branch／tag，workflow ID／SHA 與產品 SHA 分開保存。[Dispatch](https://docs.github.com/en/rest/actions/workflows?apiVersion=2026-03-10#create-a-workflow-dispatch-event) 200／相容 204 只證明請求，不證明 deployed。精確 operation ID token 關聯 run-name，另核對 repo／workflow／event／ref，不能 substring 命中；多筆為 DEPLOY_RUN_AMBIGUOUS。

本包新增唯一 verification 方式 `http_json`：後端 GET recipe 固定 URL，JSON 至少 repository_id／environment；依 recipe 的 version_required／health_required 取得 source_sha／artifact identity 或 healthy，至少要求一種 runtime check；artifact version check 另需 artifact_id／artifact_digest。Verifier 的 response 白名單為 repository_id／environment／source_sha／artifact_id／artifact_digest／healthy；其他欄位不保存。SHA 完整 40 hex、digest 完整 sha256。Endpoint 必須回實際服務版本，不可只回 inputs。可只驗版本、只驗健康或兩者。Evidence／UI 精確說明，例如「健康通過；此 recipe 未檢查 runtime 版本」。Job success 單獨不能 deployed；要求版本時 mismatch 仍是 D03 失敗。

Verifier 預設 timeout_s=10（可配置 2–60）、max_bytes=65536、拒絕 redirect，正式 URL 只允許配置的 HTTPS；HTTP loopback 僅供 fake。可選 verification.token_ref（env:／file:）提供後端 Bearer 認證，不接受 client headers、不把憑證寫入 journal。缺 runtime check 的 recipe 為 not fully configured，capabilities／UI 禁用 deploy 並點名缺少設定，直接 mutation 回 `DEPLOY_VERIFICATION_REQUIRED`，歷史可讀；資料缺欄／回查失聯為 unverified／waiting，不退回 job success 判定。這是 D03 明確接入升級。

Ordering mode=serialized：Connector 的 dispatch 共用 env provider-in-flight slot；provider workflow concurrency group 跨 aliases／自動觸發一致，cancel_in_progress=false。Aliases 可以有不同 workflow／ref，但同 env 必須宣告同一 ordering。宣告不是遠端已配置的證明，接入驗收須核對固定 workflow 及故障注入；未驗證不得宣稱阻止 runtime 覆蓋。Generation 始終保護 current 報告，不增加 provider fencing、自動取消或重派。

Rollback supported 預設 false；true 必須 identity=source_sha|artifact、not_undone 陣列（可明確為空）。本版僅 workflow_dispatch 支援；on_merge 只追舊 run，無同 recipe 主動重部署能力，supported=true 設定拒絕。不偷偷改模式、revert／push main 或把舊 run 成功當 rollback。

Artifact rollback 必須配置 artifact_id／digest inputs；一般 start 仍選 SHA，兩個 optional inputs 送空，成功證據保存實際 artifact identity／原始 artifact run ID／期限。派送 rollback 前以 [artifact API](https://docs.github.com/en/rest/actions/artifacts?apiVersion=2026-03-10) 核對 saved ID／digest／repo／來源 run／未過期，workflow promotion 再驗一次。Artifact 來源 run 與此次發布 run 分開，不以 workflow head SHA 代替產品來源證據。過期／不可取得拒絕，不換 latest／同名 zip。Source SHA rollback 標明「以此 SHA 重新建置」，不承諾相同位元 artifact。

## Part B（第二步）：journal／desired version／順序（D05）

同一 Journal 的 schema 使用冪等 DDL：CREATE TABLE／INDEX IF NOT EXISTS；column adds 先以 PRAGMA table_info 查存在。每次 open 都執行，不讀取或寫入 PRAGMA user_version。Part A 的 pr_merge_previews、pr_metadata_settlements、pr_merge_scope_reads 與 preview index 沿用各自交易；Part B 的部署表／索引／欄位亦遵循同一規則，不取得版本號。user_version 只留給一次性資料回填／重寫。Reviewer 已分配 delivery history data step 3：只有 user_version=2 時，在同一交易從舊操作回填並設為 3；crash 整筆 rollback。Observation 的 step 2（#35）由其套件先執行；同次 open 的 step 3 隨後回填部署，LATEST_DATA_STEP=3。以後的 data step 仍先向 orchestrator 申請號碼。沒有新 tasks authority。

| 新表 | 欄位／約束 |
|---|---|
| `pr_merge_previews` | immutable mpv ID、provider origin／repo ID／name、PR／method、snapshot／digest、created／expires |
| `pr_metadata_settlements`（Part A） | operation ID PK、delivery 的 not_applied／conflict receipt（status、code、observed、settled_at）；ACK readback conflict 立即保存，unresolved 依 settle window；不改別的 operation steps |
| `pr_merge_scope_reads`（Part A） | PK(repository, PR, method)、preview ID、scope checked_at；與 immutable authority 分開 |
| `deployment_environments` | PK(provider origin, repository ID, environment)；desired generation／deployment ID／identity、current ID／generation、observed identity／時間／health、attention、dispatch slot；name 是顯示／定位值 |
| `deployment_reconcile_reads` | 獨立 read_key／checked_at；只記 polling cadence／adaptive interval／provider digest，無 deployment evidence／version 權威；冪等 DDL 不占 user_version |
| `deployments` | dep ID、operation ID UNIQUE、recipe name／digest／snapshot、env key、generation、source／artifact／release identity、rollback_of、run／attempt、workflow ID／SHA、state／evidence／times／URLs；UNIQUE(env key, generation) |

Deployment preview GET repo 核對 ID，保存 environment 觀測 binding；改名沿用 ID，同名不同 ID 停 REPOSITORY_ID_CHANGED。Admission 只用可信 saved binding，缺時 DEPLOY_PREVIEW_REQUIRED；外部 mutation 前 fresh GET 再核 ID。

Operation 固定 recipe snapshot。Dispatch 前發現設定已換為 RECIPE_CHANGED，不用新設定代送；已有外部寫入時仍以原 snapshot 追原 workflow／environment，不能因目前 recipe 改名／移除就停止必要回查或換成另一條發布路線。

不修改 OperationService.create()／ActionDef admission。Part B 的第一個 local deploy.select step 以 journal transaction 比 recipe digest／expected generation，保存 op-linked applied receipt／deployment row／desired／event；沒有 network。Combined checks 尚在等待時不 reserve；checks／policy 通過後，第一個具名 local step 先 select／claim slot，才 final scope check 與 merge.submit。已成功的 select 重播 no-op，stale generation 在執行時異步 ENVIRONMENT_CHANGED，不回同步 CAS 結果。

1. Preview 讀 g。Operation 先依既有流程受理；deploy.select 用 expected g 寫 g+1，兩 client 只有一個 select 成功，另一個異步 failed／ENVIRONMENT_CHANGED。Generation 是成功選定順序，不是完成順序。
2. Select 固定 source identity；combined 可保存 source_pending_merge／reviewed PR，再綁 verified actual merged SHA。Merge 失敗 desired 保留失敗，不偷偷倒退。
3. 新 desired 可受理，dispatch 等 env slot。已送出的舊 run 繼續觀測；未 dispatch 就被取代者 superseded／needs_attention／DEPLOY_SUPERSEDED，zero POST。等待不另建 scheduler，沿用 operation worker。正常 serialized 路線不應逆序發布；仍須以延遲回執、外部 rerun／自動 run 或違反接入 concurrency 的故障注入驗證 reporting 防線。
4. On_merge 不 dispatch，只定位已有 push run；provider concurrency 排序外部自動 run，不能聲稱本地 generation 控制它開始的時間。
5. 最後版本驗證／current 更新再以 desired generation／deployment ID CAS；只有同代且證據通過才更新 current，較舊結果不能以晚完成覆蓋。

Desired 不因 failed／cancelled／刪歷史倒退，rollback 亦取得較大 generation。Current 是最後驗證的證據，附 observation time，不是持續健康保證。舊 run 晚到、runtime 回查已是舊版，env 記 ENVIRONMENT_VERSION_DRIFT／needs_attention，current 失效但保留 last verified 歷史，effective current=null；UI 同時顯示 desired 新版與 observed 舊版，不把任一誤報成已達成新 desired。不靜默重 dispatch。

| Deployment state | Operation／current |
|---|---|
| selected／waiting_order／queued／building／waiting_environment／deploying／verifying | running／waiting_external；尚未 deployed |
| succeeded | 證據交集通過；generation 同代才 is_current=true |
| superseded | 新 desired 已取代；保留 provider_status／實際結果；operation needs_attention，不新增全域 operation state |
| failed／cancelled／uncertain／needs_attention／unverified | 不升 current；未知副作用不能冒充已取消外部 run |

Cancel 只停後續步驟，不取消 GitHub run。Slot 等 provider 終態證據才釋放，unknown／needs_attention 不釋放。Combined on_merge（包含 legacy）即使取消後 PR 才 merged，也不視為 provider 終態：只讀核對 reviewed head（merge method 另核第二 parent）、實際 merge SHA 可達 recipe ref，固定該 SHA 並以正常 repo／workflow／push／branch／SHA 條件追 run，直到 run 終態才釋放。Closed 未 merged 才可立即釋放；workflow_dispatch recipe 的取消不再 dispatch，merged／closed 可釋放。`task_daemon.py` 的 `delivery.reconcile_deployments()` 每 10 秒只讀掃描停止中 operation 的 provider 未終態 rows；每個 environment 的 current deployment 另回查 run／runtime，以捕捉其外部 rerun／版本漂移。Provider 已終態且有 recorded result 或 final state 的其他歷史不回查 GitHub／runtime。Provider 已終態但尚未完成 record 的 stopped row 只做 local 結案，不恢復操作或補 runtime proof。以 row version／generation CAS 保存 facts；runtime GET 前固定 environment row version，stale receipt 不使新 current 失效。不改已終態 operation；不能 dispatch／resume。避免只掃 RUNNABLE operations 漏掉晚到 run，重啟／重播不重增 generation／事件。

`[github] deployment_reconcile_interval_s = 300` 控制 current 及 unresolved locate 的 cadence（60–86400 秒，拒絕無限／非數字）；`deployment_reconcile_reads` 保存每 environment current／deployment locate 的開始時間，含失聯／refused read，跨 daemon restart 仍節流；current ID 改變亦共用該 environment 的 cadence，新部署本身的正常 verify 不受此背景節流影響。Current 未變的 successful attempt 沿用保存的 jobs／approval 證據，不重新分頁，每個 cadence 最多一筆 run GET＋一筆 runtime GET；預設每小時每 env 各 12 次。Settled history 的 read budget 為零。Stopped provider 的 run／完整 attempt jobs／pending deployments、legacy run 與未決 merge PR（必要時 merge-async status）共用 per-deployment 持久化 cadence：初始 15 秒；每次 read 的 provider 狀態／attempt／jobs／pending／merge facts 不變則倍增至 deployment_reconcile_interval_s，改變立即回 15 秒；重啟保留 interval／digest／checked_at。預設等 approval 一小時的 run 最多 18 次 observation（單 jobs page 時 ≤54 次 GitHub GET）；run 完成後在下一次 due read 釋放 slot；on_merge 綁 SHA 後轉為節流 locate。成功完成的 read 清除既有 reconciliation_error；節流未到期不冒充成功。任何單列例外記錄類型、保存 RECONCILE_FAILED 後繼續下一列；所有 facts 仍只在改變時寫入。Locate 使用保存的 dispatch／merge send time（向前保留一秒）加入 [GitHub created filter](https://docs.github.com/en/rest/actions/workflow-runs?apiVersion=2026-03-10#list-workflow-runs-for-a-workflow)，仍保留完整 token／repo／workflow／branch／event／SHA 核對；standalone on_merge 可採用早於 admission 的 push run，不用 admission time 排除。每次 filtered search 最多 1000 筆、11 頁（含空尾頁），超出 GitHub 上限為 DEPLOY_RUN_AMBIGUOUS，不猜唯一 run。Journal 只在 state／evidence 改變時寫 deployment／增加 version；新 GET 的 checked_at 單獨變動不算新證據，polling 時間留在獨立表。

Migration 從舊 start／combined result／external refs 建歷史 identity（generation 可 null）；Data step 3 不改 tasks／operations／events；只有舊 success 為 unverified、不可 rollback／自動 current；failed 保存 failed／原 error code，cancelled 保存 cancelled，legacy 不保存 selected。未送出任何 dispatch／merge 的終態 provider_terminal=true，不持鎖；已送出的終態除已知 provider failure／success 外，仍等原 provider 終態證據才釋放。已送出的舊非終態先查原 run、不重新 dispatch；未送出的舊操作 DEPLOY_PREVIEW_REQUIRED 停住。缺少 environment／mode／routing 時由仍存在的 configured recipe 補齊一次，保存 legacy_binding_sources，不從 merge step 推測 on_merge。Provider 未終態的 legacy row 以 repository（不分大小寫）＋environment 占用真實環境，跨 recipe admission 回 DEPLOY_IN_PROGRESS、order 等 waiting_order。Migration 重播安全，保留原 tasks／operations／events。

## Part B（第二步）：部署／rollback 的步驟與恢復

| 步驟 | I/O／前置條件 | 副作用 | 恢復 |
|---|---|---|---|
| `deploy.select`（local transaction） | recipe digest／env g、SHA 或舊 dep ID | 同交易固定 desired／intent／snapshot／event | stale 整個交易 rollback；key 重送 no-op |
| `deploy.source` | fresh repo ID；SHA 以 ref...sha 的 identical／behind 證明可達；舊 dep 同 recipe／repo／env、已驗證，artifact 可取得；combined 有 verified merged SHA | 只讀；一次性綁來源 | rollback target／artifact 不可用拒絕，zero dispatch，不改舊紀錄 |
| `deploy.order` | 同 generation desired、env slot 可用 | local slot claim | waiting_order／superseded；重啟沿用 claim |
| `deploy.dispatch` | intent 先 commit，白名單固定 source／artifact／operation／generation | 新 run／真正發布，可能 migrations／通知 | 200 run ID／204 精確關聯；429 明確 refusal，以 Retry-After 在 wait_max_s 內等待，重送前先精確查 token，找到即採用；其餘失聯 uncertain，查不到也不重 POST |
| `deploy.locate`（on_merge） | repo／workflow／ref／push／固定 source SHA | 只附加舊 run，無 POST | 尚未出現等待；多筆不猜、不改走 dispatch |
| `deploy.observe` | run、固定 attempt 的完整 jobs、pending environments | journal refs／狀態 | waiting_environment；failed／cancelled 保存原因；missing／skipped job 不 deployed |
| `deploy.verify` | run＋指定 job success，目標 environment 無 pending，runtime identity／health 通過 | 保存實際 observed evidence | mismatch／health failure attention；缺資料等待，超時 DEPLOY_VERSION_UNPROVEN |
| `deploy.record` | evidence 完整、再比 desired generation／ID | 短交易保存 succeeded／superseded、current CAS、event；釋放終態 slot | 重播 no-op；舊版不升 current／不自動重派 |

Named reads 可重跑；外部寫入使用既有 `OpContext.step()`／reconcile，意圖先 commit。Local applied evidence 與資料同交易，不將尚未完成的 provider 觀測快取成已完成 step。等待沿用 wait_max_s；lost write 沿用 uncertain backoff，上限後保留 unknown／needs_attention。

D03 成功是交集：run success；[指定 attempt deploy job](https://docs.github.com/en/rest/actions/workflow-jobs?apiVersion=2026-03-10#list-jobs-for-a-workflow-run-attempt) completed＋success；[pending deployments](https://docs.github.com/en/rest/actions/workflow-runs?apiVersion=2026-03-10#get-pending-deployments-for-a-workflow-run) 中沒有目標 environment；runtime repo／env 相符；要求的版本／artifact 及／或健康通過（至少一種）。缺任一不能 deployed=true。Dispatch run.head_sha 是 workflow ref SHA，不能直接比產品；on_merge 保留 RUN_VERSION_MISMATCH。外部 rerun 換 attempt 時 DEPLOY_ATTEMPT_CHANGED，保留原 evidence／觀測新副作用，不借另一 attempt 的成功。

Rollback 只收舊 deployment ID，取 saved identity，不接受 client 替換 SHA。新 operation／deployment row／generation／run，rollback_of 指原紀錄，走同 recipe observe／verify／current CAS。刪 row、標 inactive、改 stage 都不產生 runtime rollback，不提供為回退入口（D06）。

失敗不撤銷已發生的 migration／資料／通知；UI 列 not_undone 與本次實際結果。Retry 用新 key／最新 env g／recipe digest／原固定 identity，只重試 deploy（Part B deployment_retry wrapper），不重新 merge、換 main/latest 或靜默重派。Combined merge receipt／merged SHA 保留（D04 回歸）。

## Phase 2 規格：Dashboard／skills／相容性（Part A PR 卡；Part B 環境卡）

Delivery PR 卡加 metadata drawer，載入 body／digest，保存草稿，一次存檔提交 operation。Stale／conflict 顯示 before／intended／observed 差異，不自動合併 body。Merge preview 列 commits／受影響 PRs／blocking，stack disabled，直接 API 亦拒絕；SSE 不替使用者換已選版本。

Part B 依 configured repository（不分大小寫）／environment 分組，每組一張卡，不依 PR open。卡面並列 desired 的固定 SHA／artifact、state 與操作連結、目前 observed SHA／artifact／時間；下方直接顯示 last verified 的固定 identity／驗證時間。Runtime 文案明示 version／health 各證明了什麼。缺觀測／版本用兩語系空狀態，不把 null 當文字。

History 每頁 5 筆，前後頁保存 API cursor；SSE 重載保留頁碼，不累積全部 records。新增 HTTP-only observe read `GET /api/v1/deployment-environments/history?recipe=NAME&cursor=&limit=` 是既有 history status／cursor 的薄投影，涵蓋同 repository／environment 的 recipe aliases 與已保存 legacy rows；不呼叫 GitHub／verifier、不建 preview 或 operation。Legacy 未保存環境／repository 的卡面用 configured recipe 補顯示，未保存 identity 用空狀態，不改舊 snapshot。既有 per-recipe history／MCP／CLI 不變。Status 另公開保存且已過濾的 runtime_evidence／reconciliation_error，供卡片和展開詳情顯示。

每筆 eligible old record 有「Rollback to this version／回退到此版本」，旁邊列完整 SHA、artifact ID／digest、環境與 not_undone；unsupported、unverified、expired、unavailable 依 backend rollback_reason 顯示原因。Failed 且 provider 已終態、非 legacy、保存 SHA 的版本可「Retry deploy／重試部署」；按鈕點名固定 SHA，送 deployment.start＋retry_of，不再 merge。Capabilities／readiness／allowed actions、deploy scope 與狀態決定可用性，disabled 旁列原因；combined PR 部署另需 merge scope。Superseded 連新 desired 操作與原 provider run；drift 或 needs_attention 顯警示，把選定與觀測值並列，不自動部署。

按鈕開 confirmation drawer，先讀 deployment preview 的 generation／recipe digest，再送該 preconditions；drawer 上列固定 identity 與 rollback limits。同步 busy guard 與每個 intent 的 actor-scoped idempotency key 防止 double click；lost reply 重用原 key。DEPLOY_PREVIEW_REQUIRED／ENVIRONMENT_CHANGED／RECIPE_CHANGED 顯拒絕與新 preview，使用者另按才建立新操作，不自動重送；等待 local admission step 的結果仍只讀原 operation。沒有聊天批准或 managed task。Operation ID、provider run ID／attempt、step states／error codes 收在可展開 operation details，卡面只留有語意的操作連結。關閉 drawer 同時釋放巢狀 details 的 editing hold，SSE 不丟選定版本或確認框。

Tauri v2 R07／§18 的環境卡修正：rollback／retry 的 draft scope 固定為 `deployment.<kind>.<deployment_id>`，不含 render 產生的亂數；`draftId` 仍雜湊完整 request（含 preview preconditions），`keyFor` 對已知 terminal operation 沿用原換 key 規則。相同意圖的回應遺失後，Close、SSE 重畫、page reload、重新開 drawer 均沿用原 key；環境／recipe 的新預覽形成不同意圖，必須重新確認。手動 Load PR 只受 PR 卡自己的 drawer 保護，不被其他環境的詳情／確認框擋住；自動 live reload 保留原全頁 editing／typing hold。Provider run 連結只有 `URL` 成功解析且 protocol 為 `https:` 才輸出，拒絕相對、無效與其他 scheme。zh-TW 的新部署 labels 使用「環境第 N 代」、產物、執行環境與服務商執行紀錄，保留 PR 與 deploy 權限代碼。

沿用 `fill()`、`setEditing()`、`holdRender()`、`liveReload()`，開 drawer／focus 時不重畫，en／zh-TW、390 px 可操作，不印 null／undefined／[object]；CSP 不加 inline style，必要時 el.style.setProperty。技術回執在可展開 operation details。

Part A 兩份 skills／README／CHANGELOG 同步教 integrate metadata／digest、完整 merge preview／前置條件、base moved evidence、lost reply 查原 operation；Part B 已交付 deploy generation／runtime evidence／rollback／superseded 文件。環境卡輪不改 agent workflow，所以不再改兩份 skills。

Contract_version 保持 YYYY-MM-DD，以該部分變更日期更新（Part A 為 2026-10-08）；Part A 宣告 metadata_update／merge_scope_preview，Part B 宣告 deployment_history／environment_generation／runtime_check／rollback_readiness；2026-10-08 UTC 同日變更維持日期，以 capability 名稱辨識。舊 merge client 缺 preview／base／digest 回 PRECONDITION_REQUIRED，點名 github_pr_preview／HTTP preview read，不隱式讀最新版代送；Part A 不更改 deploy 前置條件；Part B 舊 start 缺 generation／recipe digest 回 DEPLOY_PREVIEW_REQUIRED，點名 deployment_preview／HTTP read，不代選新版。

## 穩定錯誤與處理

| Code | 狀態／admission HTTP | 下一步 |
|---|---|---|
| FORBIDDEN／REPO_NOT_CONFIGURED／PR_UPDATE_DISABLED | 403 | 使用有 grant／設定的 principal／repo |
| PRECONDITION_REQUIRED／INVALID_PARAMS | 422 | 相容 client 讀預覽／補正欄位 |
| PREVIEW_NOT_FOUND／PREVIEW_MISMATCH／PREVIEW_EXPIRED | 404／409 或 failed | 讀保存的 preview 或重新預覽；expiry 只在首次 submit 前檢查 |
| PR_UPDATE_IN_PROGRESS | 409 | 查原 operation，包括尚未證明的 write |
| PR_METADATA_CHANGED | failed／409 | 讀差異，新 digest／key |
| PR_METADATA_UNVERIFIABLE | needs_attention | 保留 write receipt，補足 readback；不重新 PATCH |
| PR_METADATA_CONFLICT | needs_attention | 保留三份內容，不覆蓋／還原 |
| PR_METADATA_NOT_APPLIED | failed（reconcile／resume） | 600 秒後仍 before 的寫入已結案；讀新 digest／key 開新操作，不重 PATCH |
| GITHUB_401／GITHUB_403／GITHUB_404（read） | 無 sent write 為 failed；寫後為 needs_attention | 修復 token／權限再 resume；唯讀 plan／verify 不算 sent write；不重送已受理的 merge／PATCH |
| STACKED_PR_UNSUPPORTED／MERGE_SCOPE_EXPANDED／MERGE_SCOPE_UNPROVEN | preview blocking；needs_attention | 檢視各 PR／commits，正常重整或補讀取證據後重新預覽 |
| TARGET_HEAD_CHANGED／TARGET_BASE_CHANGED／MERGE_SCOPE_CHANGED | failed／409 | 重新預覽，zero PUT |
| EXISTING_MERGE_REQUEST | needs_attention | 核對既有 intent，不借不同操作 |
| MERGE_RESULT_SCOPE_CHANGED／MERGE_RESULT_UNVERIFIABLE | needs_attention | 保存 merged evidence，檢視最後版本，combined 不 dispatch |
| DEPLOY_SOURCE_NOT_ON_REF | admission 422（combined）；執行 failed（SHA／retry／rollback） | 核對 recipe ref 與 reviewed PR base／可達固定 SHA；zero merge PUT／dispatch POST |
| DEPLOYMENT_NOT_FOUND | 404 | 讀 deployment history，選現存 dep ID |
| DEPLOY_PREVIEW_REQUIRED／DEPLOY_VERIFICATION_REQUIRED | 409／422 | 讀新預覽／配置實際版本 endpoint |
| ENVIRONMENT_CHANGED／RECIPE_CHANGED／REPOSITORY_ID_CHANGED | 409 或執行中 needs_attention | 看新目標，不自動換 repo／recipe |
| ROLLBACK_UNSUPPORTED／ROLLBACK_TARGET_INVALID／ROLLBACK_ARTIFACT_UNAVAILABLE | 422／409 | 選可取得的已驗證同 recipe／env 版本 |
| DEPLOY_NOT_RUN／DEPLOY_FAILED | failed | 保留 job evidence，只重試 deploy |
| DEPLOY_RUN_AMBIGUOUS／DEPLOY_ATTEMPT_CHANGED | needs_attention | 查實際 run／attempt，不混證據 |
| RUN_VERSION_MISMATCH／DEPLOY_VERSION_MISMATCH／DEPLOY_ARTIFACT_MISMATCH／DEPLOY_HEALTH_FAILED | needs_attention | 顯示 selected／observed，不 deployed |
| DEPLOY_VERSION_UNPROVEN | needs_attention | 修復 readback，resume 只查原 run |
| DEPLOY_SUPERSEDED／ENVIRONMENT_VERSION_DRIFT | needs_attention | 人選新 deploy／rollback，無自動 dispatch |
| WAIT_TIMEOUT／UNCERTAIN_UNRESOLVED（沿用） | needs_attention | 保存未知副作用，查原操作 |

Worker 錯誤記 operation.error_code／status_reason，已受理 HTTP 不假裝同步 409；preview blocking 仍 200、列證據，handler 重查。INTEGRATION_IN_PROGRESS／MERGE_IN_PROGRESS、PR_CLOSED／PR_DRAFT 等既有代碼沿用。

## 預計修改檔案（Phase 2）

| 檔案 | 改動 |
|---|---|
| `src/bat_agent_connector/delivery.py`、`pr_delivery.py`（新增） | Part A 在 delivery 註冊 action／merge 流程，PR helper 保存 immutable scopes、metadata、verification／只讀對帳，隔開 Part B deploy 演進；不改 core admission |
| `src/bat_agent_connector/github.py` | repo／PATCH／stack／compare／array 分頁、pending env／attempt jobs／artifact reads、fixed version |
| `src/bat_agent_connector/deployment.py`、`deployment_store.py`（新增） | deploy steps／固定 snapshots／CAS／read-only reconcile；history／rollback／retry；冪等 DDL 與 data step 3 |
| `src/bat_agent_connector/deployment_verifier.py`（新增） | 可注入、有限、recipe 固定 URL 的 HTTP JSON readback，無外部寫入 |
| `src/bat_agent_connector/config.py`、`api_auth.py`、`resource_policy.py` | 既有 integrate scope／repo opt-in／mutation inventory；Part B recipe 欄位／inputs；不改 api_auth scopes，沿用人工唯讀邊界 |
| `src/bat_agent_connector/task_journal.py` | Part A preview migration；Part B deployment local transactions／tables／CAS，不改 operations.py／ActionDef |
| `src/bat_agent_connector/api_v1.py`、`task_daemon.py` | routes／RPC／capabilities／contract，cancelled／superseded provider runs 與未證明 metadata write 只讀對帳 |
| `src/bat_agent_connector/mcp_server.py`、`cli.py` | 薄 wrappers／delivery 命令、caller token／confirm／key |
| `src/bat_agent_connector/integration.py` | 必要時只接 pr_card 新 fields，不改 head integration |
| `src/bat_agent_connector/dashboard/app.js`、`app.css`、`i18n.js` | Delivery 新流程／兩語系／草稿 hold |
| `tests/fakegithub.py`、`tests/test_delivery.py` | REST shapes／故障注入、fake runtime verifier／主要驗收 |
| `tests/fakeverifier.py`、`tests/test_deployments.py`、`test_deployment_config.py`、`test_deployment_rollback.py`、`test_deployment_verifier.py`、`test_deployment_surfaces.py` | runtime fake、D03／D05／D06、#32、data step 3、security／transport parity |
| `tests/manual/delivery_dashboard_fixture.py`、`delivery_dashboard.mjs`（新增） | 真實 daemon／FakeGitHub／MockBat／fake verifier 的兩語系 × 三寬度操作驗收；screenshots 與 matrix 存 /tmp，不加入版面鏡像 tests |
| `tests/test_api_v1.py`、`tests/test_mcp_and_config.py`、`tests/test_integration.py` | transports／grants／migration／head integration 互斥回歸 |
| `docs/design/delivery.md`、`docs/design/api-v1.md` | 完成狀態、route／scope tables、尚未涵蓋 |
| `README.md`、`README.zh-TW.md`、`CHANGELOG.md`、`skills/bat-agent-connector/SKILL.md`、`skills/hermes/bat-agent-connector/SKILL.md` | 同步接入／能力／workflow 文件 |

## 驗收與測試計畫

Part A 已實作下列 tests；Part B 後端與環境卡的實際 test names 見後表。既有負面 assertions 保留，integration 互斥測試只補新的 merge envelope。

| 計畫／驗收 | Part A tests 與證明 |
|---|---|
| §10／§15、C06 精神 | `test_metadata_human_only_pr_preserves_body_and_clears_explicitly`：人的 PR、title/body 單欄與空 body、Markdown；`test_metadata_stale_digest_and_last_read_race_send_zero_patch`；`test_metadata_write_readback_conflict_never_overwrites`；`test_metadata_lost_reply_restart_and_cancel_never_resend`；`test_metadata_cancelled_unknown_write_stays_blocking_until_proven`；`test_metadata_cancel_after_acknowledgement_retains_verified_effect` |
| C04、§16 | 保留 `test_merge_records_the_real_merged_sha`、`test_pending_required_checks_wait_without_merging`、`test_existing_merge_request_is_adopted_only_when_it_matches`；新增 `test_c04_existing_request_requires_exact_options`（head／method／action 缺欄或不同拒絕）、`test_c04_expired_request_uuid_reads_pr_without_resubmit`、`test_c04_old_merge_client_requires_saved_preview` |
| C05、§16 | `test_c05_base_changed_before_submit_has_diff_and_zero_put`；`test_c05_queue_newer_base_verifies_actual_merge`（merge／squash）；`test_c05_newer_base_combined_deploy_uses_actual_sha`；`test_c05_rebase_verifies_reviewed_head_and_actual_destination`；`test_c05_merge_restart_preserves_actual_sha_without_attribution`；`test_c05_wrong_merge_parent_keeps_receipt_and_stops_deploy`；`test_merge_preview_expiry_only_before_first_submit`；`test_c05_merge_verification_intent_precedes_reads_and_resume_is_read_only` |
| C05、§09／§16 method／policy | `test_c05_merge_method_pinned_to_preview_across_default_change`（單獨／combined：省略 method、等待 checks 後換 default 並重建 service，step／單一 PUT／verify 仍為 merge）；`test_c05_merge_method_removed_from_policy_stops_before_put`（移除 method／關閉 allow_merge，zero PUT／POST，明確 failed code、無 write intent）；`test_c05_explicit_merge_method_must_match_preview_at_admission`；`test_c05_merge_policy_revoked_before_reconcile_never_resubmits`；`test_c05_merge_policy_change_after_acceptance_keeps_verifying` |
| §16 stack、C04／C05 | `test_c04_native_stack_and_branch_chain_are_listed_and_refused`、`test_c04_native_bottom_lists_upper_rebase_and_refuses`；`test_c04_indirect_merge_differs_by_method_and_shared_ancestor`；`test_c04_complete_compare_pagination_over_250_commits`、`test_c04_paginated_pr_list_finds_late_indirect_candidate`；`test_c04_scope_changes_or_missing_pages_never_submit`；`test_c04_stack_read_errors_are_not_empty_membership`；`test_c05_swept_downstack_fails_verification_and_never_dispatches`；`test_c05_new_stack_during_acceptance_never_dispatches`（list 的 merged_at／秒級時間、無 merged boolean）；`test_c05_independently_merged_candidate_on_new_base_is_allowed` |
| C07、§09／§15 | `test_c07_metadata_integrate_scope_opt_in_and_fields`；`test_c07_delivery_transports_share_actions_and_principals`（真 HTTP／MCP／CLI，同 actor/key 回同 operation、scope 不借 Dashboard、combined capabilities）；`test_c07_delivery_wrappers_require_confirmation_and_token`（confirm／token／read_only）；既有 `test_merge_admission` |
| §09／§28 journal schema | `test_part_a_preview_migration_reopens_without_data_change`：version 1／8 缺表的 journal 都補齊三表／index 並保留原號碼；第二次 open 的 schema 不變、preview row 完整保留。既有 task event backfill test 保留。Part A 不呼叫 BAT／SSH Git mutation |

Review follow-up 的新增回歸：

| 計畫／驗收 | Tests／負面證據 |
|---|---|
| C07、§10／§15 metadata recovery | `test_metadata_lost_before_write_settles_not_applied_after_window`（cancelled／UNCERTAIN_UNRESOLVED、新操作受理、停止 GET／zero 第二 PATCH）；`test_metadata_late_landing_after_settlement_is_caught_by_digest`；`test_metadata_positive_cancel_reconciliation_pins_private_service_calls` |
| C07、§09／§10／§15 ACK conflict | `test_metadata_acknowledged_write_conflict_settles_and_releases_pr`（直接 readback／先被 401／403／404 拒絕後 resume：待驗證時仍鎖住，conflict 立即保存 receipt／refs、fresh-digest 更新成功；舊操作只送一 PATCH、重啟／resume／reconcile zero GitHub calls，cancel 保持 cancelled） |
| C04／C07、§16 preview | `test_pr_card_reuses_identical_preview_and_prunes_expired`（摘要欄位、未過期重用、queued row 保留到 verify）；`test_event_reloads_do_not_recompute_scope_within_window`（GitHub calls、SHA 改變立即刷新、digest 重用後時計仍刷新）；`test_event_reload_throttle_is_shared_by_http_and_rpc` |
| C04／C05、§16 submit／verify | `test_transient_scope_read_error_before_submit_is_resumable`（failed step 不存在、resume 僅一 PUT）；`test_checks_wait_only_rereads_head_and_base_before_final_scope`；`test_verify_accepts_affected_pr_merged_after_this_merge`；`test_verify_stops_updated_pr_pagination_at_admission`（排序與 pages） |

#33 rebase 後新增：`test_refused_read_after_merge_submit_needs_attention_and_resumes`（PR／commit／compare／recent PR／stack／affected PR × 401／403／404，連續 resume、單一 PUT）；`test_refused_read_after_metadata_write_needs_attention_and_resumes`（ACK readback／lost PATCH reconcile × 三種拒絕，單一 PATCH）；`test_metadata_refused_read_before_patch_fails_fast`（plan／pre-PATCH × 三種拒絕）；`test_readonly_merge_verification_refusal_before_write_fails_fast`。對應 C04／C05／C07、計畫 §09／§10／§15／§16。

Issue #32 low item：`test_merge_async_400_reads_pr_before_failing`（open／closed／reviewed head 已 merged／其他 head 已 merged，GET 必在 PUT 後；不 bypass verify／零第二 PUT）；`test_merge_async_400_readback_refusal_remains_resumable`（401／403／404、修復後正常驗證、不重送）。對應 C04／C05、計畫 §16。

PR #34 Codex review：`test_c05_stack_created_after_final_check_needs_attention_and_never_dispatches`（PUT 時才建立 native stack；open 上層與獨立 merged 成員皆保留完整 receipt／needs_attention，單一 PUT、zero POST）。對應 C05、計畫 §09／§16。

Metadata：`test_unresolved_metadata_write_third_value_settles_as_conflict_after_window`（cancelled／UNCERTAIN_UNRESOLVED；600 秒前仍拒絕新 edit，之後 conflict receipt／refs、新 digest admission、停止背景 GET；resume 沿用 conflict、總共一 PATCH、不 undo）。對應 C07、計畫 §09／§10／§15。

Part B 後端、入口與 Dashboard 的實際 tests（計畫 §09／§10／§17／§28）：

| 驗收／規格 | 實際 test names |
|---|---|
| D03：run／job／pending／repo／env／版本／健康交集 | `test_d03_success_requires_job_environment_version_and_health`；`test_runtime_artifact_mismatch_is_d03_failure`；`test_d03_runtime_unavailable_expires_as_unproven` |
| D03：workflow 與產品 SHA／固定 attempt／完整 jobs | `test_d03_workflow_sha_is_not_product_sha`；`test_d03_run_attempt_cannot_borrow_other_jobs`；`test_d03_attempt_jobs_are_paginated`；`test_d03_external_rerun_invalidates_current_and_keeps_original_attempt` |
| D03：版本或健康、明確 evidence | `test_d03_health_only_recipe_states_exactly_what_was_proven`；`test_deploy_preview_required_and_missing_verifier_disable_writes_but_not_history`；`test_capabilities_missing_runtime_verification_disables_deploys` |
| D05：CAS、跨 aliases／獨立 env、未送出 superseded | `test_d05_environment_generation_is_atomic_across_recipes`；`test_d05_independent_environments_have_independent_generations`；`test_d05_undispatched_selection_superseded_has_zero_post` |
| D05：晚到 current／drift／重啟／cancel／combined | `test_d05_late_old_run_is_superseded_never_current`；`test_d05_current_survives_old_verification_that_arrives_after_new_record`；`test_d05_runtime_drift_invalidates_current_without_redispatch`；`test_d05_restart_and_cancel_keep_provider_slot_and_desired`；`test_d05_combined_selects_generation_then_binds_real_merge_sha` |
| D06：SHA／artifact identity、新 operation／generation／run、不可用拒絕 | `test_d06_rollback_redeploys_saved_identity_through_same_recipe`；`test_d06_rollback_refuses_unverified_expired_or_other_environment`；`test_d06_rollback_lost_reply_restart_never_dispatches_twice`；`test_d06_record_inactive_or_deleted_is_not_runtime_rollback` |
| D01／D02／D04 回歸 | `test_lost_dispatch_reply_is_matched_by_run_name_not_redispatched`；`test_on_merge_recipe_tracks_the_existing_run_and_serializes_the_environment`；`test_merge_and_deploy_keeps_the_merge_when_the_deploy_fails`；`test_d01_d02_204_requires_exact_unique_operation_token_and_never_redispatches`；`test_d04_retry_keeps_saved_identity_and_never_merges_again` |
| §09／§28：data step 3、crash／reopen／fresh post-step-2、舊操作 | `test_delivery_v2_migration_keeps_history_unverified_and_reopens`；`test_delivery_data_step3_crash_rolls_back_whole`；`test_delivery_fresh_post_step2_journal_ends_at3_and_ddl_preserves_other_versions`；`test_legacy_dispatched_operation_reads_original_run_and_never_dispatches`；`test_legacy_undispatched_operation_stops_for_deployment_preview` |
| §09／§28、D05：legacy 終態與共用環境鎖 | `test_legacy_terminal_states_release_unsent_or_definitively_failed_recipe`；`test_legacy_run_blocks_alias_of_real_environment_until_provider_terminal` |
| D05／#32：cancelled combined on_merge 與 legacy | `test_cancelled_combined_on_merge_holds_slot_through_late_push_run`（兩種來源）；`test_legacy_cancelled_dispatch_merge_uses_config_mode_and_never_locates_push`；`test_cancelled_legacy_on_merge_requires_reviewed_head_and_ref_or_unmerged_close` |
| D03／D05、§09／§17：停止中 provider 退避／error 清除／單列隔離 | `test_stopped_provider_cadence_bounds_hour_and_resets_on_state_change`；`test_reconcile_success_clears_error_once_without_unchanged_version_writes`；`test_reconcile_bad_row_logs_and_continues_without_repeated_version_writes` |
| D03／D05、§09／§17：reconcile 預算／restart cadence | `test_reconcile_budget_skips_twenty_settled_rows_and_polls_current_once_per_cadence`；`test_current_reconcile_cadence_is_shared_when_current_changes`；`test_lost_dispatch_locate_is_throttled_across_restart_and_filters_saved_send_time`；`test_locate_refuses_github_truncated_filtered_search`；`test_deployment_reconcile_cadence_has_a_minimum_and_finite_bound`；`test_deployment_reconcile_cadence_default_and_override` |
| §09／§10、C07：transport／grants／history offline | `test_deployment_transports_share_fixed_identity_generation_and_principal`；`test_deployment_wrappers_require_confirmation_token_and_are_hidden_read_only`；`test_history_keyset_is_stable_offline_and_rollback_disabled_for_missing_config` |
| §06／§17：政策、設定／憑證／verifier 有限讀取 | `test_delivery_actions_never_mutate_manual_or_unknown_resources`；`test_recipe_change_blocks_new_dispatch_but_removed_recipe_cannot_stop_original_readback`；`test_repository_identity_and_recipe_are_rechecked_before_dispatch`；`test_verifier_security_refuses_redirect_oversized_non_https_and_slow`；`test_verifier_backend_token_and_raw_body_never_enter_journal_logs_or_events`；`test_verifier_refuses_secret_echo_in_whitelisted_field`；`test_verification_settings_cannot_be_overridden_by_a_client` |

Issue #32 本輪六項：

| Item | 實際 test names |
|---|---|
| 1 on_merge 完整 branch／repo／workflow／push／SHA | `test_issue32_on_merge_locate_requires_recipe_branch_repository_and_workflow` |
| 2 start／retry／SHA rollback 必須 on ref | `test_issue32_source_must_be_reachable_from_recipe_ref`（三種）；`test_issue32_artifact_retry_also_requires_source_on_recipe_ref`；`test_issue32_unknown_source_comparison_refuses_before_dispatch` |
| 3 combined reviewed base 必須等於 recipe ref | `test_issue32_combined_admission_requires_preview_base_on_recipe_ref` |
| 4 cancel 保留 provider slot／recipe lock | `test_issue32_cancel_keeps_provider_slot_and_recipe_lock_until_terminal`（combined merge／dispatch）；`test_issue32_cancelled_on_merge_wait_keeps_slot_until_the_exact_run_finishes` |
| 5 dispatch source／operation inputs 必填 | `test_issue32_dispatch_requires_source_sha_and_operation_id_inputs` |
| 6 dispatch 429 明確 refusal／Retry-After／查 token | `test_issue32_dispatch_429_waits_retry_after_and_adopts_before_resend`；`test_issue32_dispatch_429_retries_only_after_backoff`；`test_issue32_dispatch_429_wait_is_bounded_without_early_resend` |

UI 用真實 daemon API＋FakeGitHub／MockBat／fake verifier 驗收 zh-TW、en × 390／768／1440 px：編輯中 SSE 不丟草稿、conflict 差異、history 分頁、rollback limits／unsupported、superseded／desired-observed 不同、stack 各 PR 與後端拒絕。將 app.js 複製為 /tmp 的 .mjs 再 node --check；不為文案／低影響排版寫鏡像 tests。

各部分完成前跑 `uv run ruff check .`、`uv run pytest -q` 全套。Phase 1 同跑基線，不聲稱新驗收已實作。真實 repo／environment 接入由 orchestrator 另授權，本 clone 不呼叫 GitHub mutation、不推送、不開 PR。

環境卡的 Playwright runner 為 `tests/manual/delivery_dashboard.mjs`，fixture 為 `tests/manual/delivery_dashboard_fixture.py`。從 repo root 跑 `BATC_BROWSER_FIXTURE=/path/to/fixture.json uv run python tests/manual/delivery_dashboard_fixture.py`，另一終端以已安裝的 Playwright 與同一個 `BATC_BROWSER_FIXTURE` 執行 `BATC_BROWSER_FIXTURE=/path/to/fixture.json BATC_PLAYWRIGHT_MODULE=/path/to/node_modules/playwright node tests/manual/delivery_dashboard.mjs`；也可用 BATC_PLAYWRIGHT_MODULE／BATC_CHROMIUM／BATC_BROWSER_OUTPUT 指向自己的安裝與輸出目錄。只用 synthetic IDs／SHA／保留 example domain；fixture 控制 listener 不在產品 router。

| Part B 環境卡驗收（en／zh-TW × 390／768／1440 px） | 實際 case／test 名稱 |
|---|---|
| D03／D05、§17 desired／observed／last verified、drift／superseded | `dashboard_environment_versions` |
| D05、§11／§17 cursor、同環境 recipe 分組、offline read／observe scope | `dashboard_history_cursor`；`test_environment_history_read_pages_aliases_and_legacy_offline_with_observe_scope` |
| D06、§17 limits／unsupported／unverified／expired／unavailable | `dashboard_rollback_readiness` |
| D05／D06、§09／§17 stale refusal＋fresh preview，無自動重送 | `dashboard_stale_preview` |
| D02／D06、§09 actor-key／double click 一筆 operation | `dashboard_double_click` |
| D02／D06；v2 R07／§18 相同 rollback／retry 的遺失回應、Close、SSE、reload、重開沿用 key 與原 operation | `dashboard_lost_reply_reopen`（真 daemon 接受後暫停處理；僅攔截 browser ACK） |
| v2 R07／§18 環境 history 詳情保持開啟時，手動 Load PR 即時更新 PR 卡 | `dashboard_load_pr_with_history_open` |
| v2 R07／§18 Provider link 僅限 HTTPS，無效／相對／script／data／HTTP 不建立可點連結 | `dashboard_https_provider_links` |
| D04、§17 retry 固定 SHA＋retry_of，只 deploy 不 merge | `dashboard_retry_fixed_identity` |
| C07、§10 scope disabled 與原因 | `dashboard_scope_disabled` |
| §18 open confirmation＋SSE 保留 identity | `dashboard_confirmation_sse` |
| §18／§28 兩語系、390／768／1440、console／overflow／空值 | `dashboard_dom_console_overflow`（每格零 console errors、無橫向 overflow／null／undefined／[object]） |

## 尚未涵蓋

- 本分支已接 observation step 2；version-1 journal 在同次 open 依序執行 2、3，原 events/operations 不重寫。正式 main 合併與 live acceptance 另行記錄。
- PR head 更新仍是 [integration.md](integration.md)；integrate.verify、Task Service 成果來源、fork／LFS／跨主機整合不屬本包。
- 不支援 native stack 建立／重整／多 PR merge；本版偵測、列出並拒絕。GitHub 沒有 base／scope／body 原子鎖，觀測間的競爭窗口仍存在。
- 最後的 pre-PUT scope check 將檢查完成至提交的窗口縮至一次 GitHub 請求往返。若 merge 後 GitHub 已解散新 stack membership，verify 無法再看見該 stack；不能從其他 open PR 的 head 變化猜測，因為作者也會自行 push／rebase。
- 自動 managed links 區塊延後；需要時先決定 markers、公開 links、與 work_item.link 的權威關係，不猜自動同步需求。
- 未知 provider、on_merge 主動 rollback 路線、資料 migration 反向執行、artifact 長期保存／清理不屬本包，沒有可取得的同 identity 路線就 unsupported。
- 實際接入待確認：各 env 的 runtime version URL／認證、固定 workflow 的 source／artifact 驗證、跨 recipe concurrency、rollback identity／not_undone。本例只用保留 example domain，不猜真實設定。
- Review 決策：Part A integrate＋repo opt-in、ISO date contract、預覽升級已批准；Part B generation／runtime check 接入值待配置；本包不以未確認設定啟用 production mutation。


### Tauri v2 R07：Observation 整合回執

部署 row 的有效變更同交易追加 `deployment.updated`；同值讀回不新增 row version 或事件。
`deployment.selected/verified/superseded` 保留，所有事件帶明確 operation/deployment ID、generation、state、
固定 SHA/artifact identity、run/attempt 與機器 error code。只記 provider HTTPS URL 的 origin/path，移除 query、
fragment 與含帳密 URL。Verifier body、runtime response、recipe config、authorization/token、自由 diagnostic 不進事件。
`deployment.drift` 明存受影響的 deployment/operation ID。Observation 依 operation 的既有證據索引；不由相同 SHA、
PR 或路徑猜 session/worktree 關係。一般沒有 source binding 的部署不出现在任何 session timeline。

Step 3 為舊 operation 回填部署後，以 `deployment.backfilled` 附記當下的 saved snapshot，
`backfilled=true`、`occurred_at=null`；不偽造選取／執行／驗證時間，不修改原 api_events。
已有 Part B row 也得到一次 snapshot；同交易 crash rollback，重開不重複。舊成功仍是 unverified。

回歸：`tests/test_deployment_history.py` 驗證 step 1→2→3、原 events 不變、重開無重複、session history 的
明確 operation link、未洩漏 provider/verifier 私密欄位、相同狀態 quiet、state/event 同交易 rollback；
`test_delivery_v2_migration_keeps_history_unverified_and_reopens` 保留 unverified 與 provider-slot 契約。


Metadata 與 deployment 的 daemon 背景 read-reconcile loop 每輪都先查同一 `journal.owner_valid()`；
owner 不可用時不開始 provider reconcile。這是既有 fleet lease 的使用，沒有第二個 daemon 或 authority。
Operation steps／取消／恢復仍走同一 OperationService；同一輪進行中的 provider reads 沿原 read-back 語意。
測試 `tests/test_delivery_owner.py` 覆蓋兩個 loop、owner 有效／失效的零 provider-call 行為。
