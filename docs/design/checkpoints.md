# Checkpoint：從人的版本另開 agent 工作

日期：2026-10-08。對應《Better Agent Dashboard／Connector 計畫》v1.0 的 §12（Checkpoint 接續與獨立執行）與工作包 W04。程式在 `checkpoints.py`，經 [OperationService](api-v1.md) 執行；資源規則見 [resource-policy.md](resource-policy.md)。

## 兩個 action

| Action | Scope | 做什麼 | 寫入 |
|---|---|---|---|
| `checkpoint.create` | operate | 讀來源 session 的資料夾、git root、分支、commit、未提交修改數，與最近 `last_n`（0–50）則對話 | 只寫 Connector 自己的 journal（`checkpoints`）；BAT 只用讀取 channel |
| `checkpoint.continue` | start | 在 Connector 自有 clone 的新 worktree 與分支上，從 checkpoint 的 commit 開新 BAT session，確認起點後才送第一個指令 | 只在 managed root 內寫 |

`start` 是獨立的 scope：開新 session 等於以呼叫者的指示在主機上跑一個 agent，比 `operate`（對既有 managed session 送字、回答、中斷）大。只拿 `operate` 的 token（例如 Hermes、Grokbot 原本的 token）不能從 checkpoint 開工；要開工就重發含 `--scope start` 的 token。

來源可以是任何 session，包括人在 BAT 建立、API 永久唯讀的 session。來源 session 與它的資料夾不會被送字、stash、commit、checkout 或鎖住。

## checkpoint.create

1. 經 inventory 的唯讀 fleet（client 核心拒絕所有寫入 channel）：`claude:get-session-meta` 取資料夾（沒有就用目錄裡的 `cwd`），`git:getRoot`、`git:branch`、`git:log`。
2. 指定 `commit`（完整 40 hex）時，必須在來源分支最近 200 個 commit 內，否則 `COMMIT_NOT_FOUND`。沒指定就用 HEAD。
3. 未提交修改數（`dirty`）經主機的 SSH alias 以 `git --no-optional-locks status --porcelain` 讀取；沒有 alias 時記為 `null`（未觀測）。不用 BAT 的 `git:status`：BAT 執行一般的 `git status`（`git_get_status_native`），工作檔的 stat 資料過期時會刷新並改寫人的 `.git/index`；失敗時它回 `[]`，看起來像「乾淨」。第一版只帶已提交的 commit，未提交內容不會進新工作（`preview.snapshot.supported = false`）。
4. 讀對話摘錄，經 secret 遮蔽後存成固定內容與 SHA-256。
5. 再讀一次 HEAD（SSH 讀到的 HEAD 也要一致）；沒指定 commit 而 HEAD 變了，回 `SOURCE_MOVED`，請使用者重做（計畫 §12「前後指紋」）。

Checkpoint ID 由 operation ID 衍生（`cp_<32 hex>`），所以重跑同一個 operation 不會產生第二個 checkpoint。之後來源分支前進，不會改變已選定的 checkpoint。

來源狀態讀取清除繼承的 Git index／worktree／config 環境覆寫，停用使用者與系統 Git 設定、
optional locks 及 lazy fetch。Repository／worktree 設定若含 filter、fsmonitor、hooks、
config include 或 promisor／partial-clone 宣告，會在執行狀態讀取前拒絕；含 submodule 的
index 也不略過或遞迴讀取。這些情況記為未觀測，不是零項修改。一般與 linked worktree
仍核對自己的根目錄、HEAD 及 index，不改寫來源。這項讀取保護不啟用本機 Git transport，
也不代替 managed clone 的建立證據、目錄鎖與後續執行核對。

## checkpoint.continue

BAT 的 `worktree:create` 不能指定起點 commit，所以 clone 與 worktree 由 Connector 經主機的 SSH alias 以 git 建立（與 verification 共用 `[verification] ssh_hosts`）：

1. `worktree.prepare`：在主機第一個 `managed_roots` 下的 `<repo>-<8 hex>`（同一主機、同一來源 repository 共用一個）準備 clone，再加 worktree `<clone>/.bat-worktrees/batc-cp-<12 hex>` 與分支 `batc/cp-<12 hex>`，起點是 checkpoint 的 commit。腳本可重跑：已存在的 clone 與 worktree 直接沿用。寫入前先以 `resource_policy.check_checkpoint_worktree` 確認 clone 正好在 managed root 下一層、worktree 與分支是固定名稱（mutation 表的 `checkpoint.managed_worktree`）。
   - 來源本身已在 managed clone 內（例如從 managed session 的 worktree 記下的 checkpoint），就沿用那個 clone，不再 clone 一份。
   - `git clone` 與 `git fetch` 只讀人的 repository。來源的 origin 是網路 URL（https、ssh、`git@`）時，clone 的 `origin` 改成它並去掉帳密；是本機路徑或沒有時移除 `origin`，所以從 clone push 不會進到任何人的資料夾。
   - 同一個 clone 的腳本在主機上以 `flock` 一次只跑一個：SSH 回應遺失後的重跑會等前一次跑完，不會同時改同一個 clone。
   - Clone 先建在暫存目錄，標上 `batc.managed-clone` 與 `batc.source` 後才搬到正式位置。目標路徑已存在但不是 Connector 的 clone（或屬於別的來源）時中止（`GIT_FAILED: not a connector clone`），不接管別人的資料夾。
   - 暫存路徑若已有檔案、目錄或符號連結（包含失效連結），沒有獨立建立收據可證明歸屬時立即中止並保留，不刪除後重建。Clone 期間若正式目的地出現，也保留暫存副本及目的地供核對。此保守拒絕包含未完成的舊暫存副本；不靠路徑或 marker 推定可以清理。
     這項保留修正不代替完整的跨程序 inode 鎖、clone 建立證據與來源程式隔離；本機 Git 仍未啟用。
2. 腳本回報的 HEAD 必須等於 checkpoint commit 且乾淨，否則 `START_MISMATCH`。
3. `verify.start`：BAT 的 `git:getRoot` 與 `git:log` 必須在新資料夾看到 worktree 本身與 checkpoint commit（計畫 §12 第 5 步），之後才啟動 session。這是一個 step：重跑時直接回傳記下的結果，不會因為 agent 已經 commit 而誤判起點不符。
4. `session.start`：`session_start` 以 `cwd_override` 在 worktree 開新 session，session ID 由 operation ID 衍生；資源政策確認目的地在 managed root 內（`managed_clone`），並用 `git:getRoot` 確認沒有連結到 managed root 外。Session 以 `write_scope="confined"` 啟動，見下方「Agent 的可寫範圍」。
5. `send`：第一個指令的第一行是 `[batc checkpoint <checkpoint_id> · <operation_id>]`，接著是 repository、來源分支與 commit、新分支與資料夾、未提交修改的提醒、對話摘錄（超過長度時保留最新的；標明是背景而不是指示，裡面的路徑是人的資料夾），最後是使用者的指示原文。
6. 記入 `checkpoint_runs`，並立刻更新該主機的目錄，讓新 session 馬上出現在 Dashboard。

每一步都是 operation step，回應遺失時以讀回判斷，不重送：

| 步驟 | 讀回 |
|---|---|
| `worktree.prepare` | 腳本可重跑，直接重跑 |
| `session.start` | Registry 沒有預留紀錄：start frame 從未送出，可以重跑。BAT 的 session meta 顯示該 ID 在這個 worktree：補記成功。其他情況（包括 meta 是 null；BAT 可能還在啟動）：維持 `uncertain` 稍後再讀，不再啟動第二次 |
| `send` | Claude：BAT 接受該 `clientMessageId` 後寫入的 turn 紀錄。Codex：新 session 對話中開頭是上述第一行的 user 訊息 |

新 session 的 registry 列與 `checkpoint_runs` 都記下來源；`GET /api/v1/sessions/{host}/{id}` 回 `started_from`。Checkpoint（人的成果）與 checkpoint run（agent 成果，以 `checkpoint.continue` 的 operation ID 指定）都能經 [integration](integration.md) 放進既有 PR。

## Agent 的可寫範圍

摘錄可能包含人的絕對路徑。計畫 §06／§12、A10 的執行限制不能靠 cwd 或 prompt 自律；實作與證據見 [confinement](confinement.md)。Checkpoint 與 repair 不論 host default 都帶 `write_scope=confined`。

| Agent | 啟動選項／證據 | 限制與缺口 |
|---|---|---|
| Claude，沒有已查核 account | `permissionMode: default`；`prompt_gated` | 未預先批准的工具會詢問；既有規則及 shell 可允許外部寫入，沒有 OS sandbox。表單推薦 Codex。 |
| Claude，已查核 account | `permissionMode: acceptEdits`；`host_account` | BAT callback 對 Write／Edit 等直接 allow，沒有 path check；帳號才是宣告 roots 的邊界。 |
| Codex | `workspace-write`／`on-request`；`os_sandbox`，最多 `options_confirmed` | BAT 不轉送 writable roots 或 network；OS 阻擋尚待 W12 實機驗證，逐次批准可能越過 sandbox。 |

Registry 從 reserve 記 creation snapshot 與實際 options。Resume 與 successor 保持限制；permission raise（含 force）、bulk、deferred raise、dont_ask_again 及放寬 mode 的 ExitPlanMode allow 都被拒絕。單次批准仍可能讓外部寫入通過。Existing loaded sessions 缺舊證據仍可 send；meta=null 的 confined resume 若缺原 policy 才被拒絕。Running sessions 不因升級而被重標。A10 在 W12 live run 前尚未證實。

## 讀取

- `GET /api/v1/checkpoints?host=&session_id=&limit=`：列表（不含摘錄，只有則數）。
- `GET /api/v1/checkpoints/{id}`：含摘錄與從它開出的 sessions（`runs`）；`live=true` 另回 `source.advanced`（來源分支是否已前進，計畫 §12「來源已有新版本」）。
- `GET /api/v1/sessions/{host}/{id}/checkpoint-preview`：記錄前的預覽（只讀）：branch、HEAD、最近 20 個 commit、`dirty`。
- MCP：`checkpoints_list`、`checkpoint_preview`、`checkpoint_create`、`work_continue_from_checkpoint`（後兩個與 `operation_submit` 同樣以 `BATC_API_TOKEN` 的身分執行，需要 `confirm=true`）。CLI：`batc checkpoint create|continue|list|show`。
- `capabilities.features.checkpoints` 列出能接續的主機（有 SSH alias、managed root、write 與 orchestrate tier）。

Dashboard 的 session 頁有「檢查點」區塊：「記下這個版本」展開提交選單及需求原文；列表的「從這裡接續」在按鈕下方展開指示表單。執行限制放在收合的說明中，仍沿用相同 confinement 與原 operation key。來源已有更新的提交時會標示。接續結果顯示中央操作狀態，中央回傳新工作階段 ID 後才提供開啟連結；操作 ID 留在詳細資料。主機具備能力時不顯示無法開工的說明，檢查點說明的開合狀態依中央連線身分隔離。新 session 頁標出它從哪個版本開出。

## 設定

```toml
[hosts.workstation]
writes = true
orchestrate = true
managed_roots = ["/srv/batc-managed"]   # 第一個是 Connector clone 的位置；請填實際路徑
```

```toml
# BATC_TASK_SETTINGS 指向的設定（0600）
[verification]
ssh_hosts = { workstation = "workstation-alias" }   # ~/.ssh/config 的 alias，BatchMode
```

## 附件接續（Part A；計畫 §12／§13，B04）

checkpoint.create 的 params.artifacts 凍結 ready `{artifact_id, revision, digest}`；continue 用 params.artifacts，省略才用 checkpoint 集合，永遠不取 latest。有附件須 expected_source_head_sha；Dashboard 一律帶。work_item_id 另帶 expected_work_item_fingerprint，handler 第一 step 建立 idempotent operation 連結。

worktree.prepare clean 後，先 source guard，再 transfer 到 session cwd 內 `.batc-inputs/<artifact_id>-r<revision>/<safe name>`，寫入 clone common info/exclude；獨立 hash 讀回全部成功才 start。start_in_worktree 只增加 before_send callback；send 尚無 record 時再次即時 guard／read-back，拒絕復用舊的通過紀錄。start／send 回覆遺失仍走既有 UUID5／message ID reconcile。

SOURCE_MOVED 保留原 commit、refs、parent；以 checkpoint.continue.revalidate 傳 observed_source_head_sha 與 expected_input_manifest_digest，透過既有 resume 續同 parent。失敗留 per-item evidence，部分已驗證不重傳。dispatch 後副本是 agent 工作副本；證據不是 live inventory，刪 worktree 不刪 Connector store 原件。原 client 無 guard 欄位且無附件仍保留原合約。CLI checkpoint revalidate 與操作頁提供確認；詳見 [artifacts.md](artifacts.md)。
## 整理與永久歷史（Part A）

見 [cleanup.md](cleanup.md)：純讀 preview、signed token、逐項 operations／retained refs／tombstones，原 ID
永久可查。Dashboard #/cleanup 與 work item 的整理入口，兩語預覽／逐項回執／歷史搜尋／實際 retained list；
Part A 無 restore 按鈕。Task-owned 資源由 Task Service 整理，本輪列 TASK_OWNED；原 terminal cleanup 不變。
Restore、reviewed task leftovers／coordinator 准入與 TaskDaemon 歷史投影在 Part B。Clone／area 與 pins 留存。

## 尚未涵蓋

- 未提交內容的唯讀 snapshot（計畫 §12「未提交修改」）：目前只顯示 `dirty` 提醒。
- 新工作固定在 checkpoint 所在的主機；直接搬運未發布 commit 到別台主機已由產品負責人排除交付範圍。
- 檔案 capture／artifact.accept（Part B）與明確 repository 的已發布 commit 同步（Part C），見 [artifacts.md](artifacts.md)。
- 復原 retained worktree 在 cleanup Part B；clone 退休、永久刪除另規格。
