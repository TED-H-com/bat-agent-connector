# 專案與工作項目

日期：2026-10-08。對應《Better Agent Dashboard／Connector 計畫》v1.0 的 §05（權威來源）、§08（領域模型）、§10（`/projects`、`/work-items`）、§19（專案總覽與工作樹、工作詳情）、§20（Project Hub 重用）與工作包 W05。程式在 `work_items.py`，經 [OperationService](api-v1.md) 執行。

專案與工作項目是 Connector 自己的管理資料，只存在 journal。它們不碰主機、Git 或 GitHub；執行事實（session 是否在跑、PR 是否合併）仍由各自的來源決定，工作項目只連結到它們。

## 兩種紀錄

| 紀錄 | ID | 內容 |
|---|---|---|
| 專案 | `prj_` + 20 hex | 名稱（啟用中的專案不重名）、說明、上層專案、`derived_from`、repositories（`owner/name`）、對應的 Task Service 專案名稱 |
| 工作項目 | `wi_` + 20 hex | 標題、目標、需求原文、驗收條件、步驟（`{text, done}`）、狀態（`todo`／`doing`／`waiting`／`done`）、上層項目（同專案）、`derived_from`（任何專案的項目） |

ID 隨機產生，封存的列保留，所以 ID 不會重用。改名、移動、排序都不改 ID。

2026-10-10 共用介面補充：專案與工作清單可拖曳同層項目，固定項目不跨越未固定群組，
不因拖放改變 parent。拖曳與「…」內的上移／下移都提交完整 `before`／`order`，另帶
`expected_versions={id:version}`；中央在 admission 與執行時都查核各 sibling version。
舊 client 只有 `before` 仍沿用原順序合約。右鍵、Shift+F10 與「…」開同一份動作／表單，
Escape 關閉並還原焦點；開著草稿或拖曳時不由事件刷新重建畫面。

Session、直接開工及專案派工輸入區另提供按鈕／Enter／Ctrl 或 ⌘+Enter 的入口偏好。
Shift+Enter 保留換行，IME composition／229 keycode／重複按鍵不送出；快捷鍵只呼叫原按鈕，
沿用其權限、固定意圖、附件 readiness 與中央 admission gates，不自行提交另一套 request。

## Actions

| Action | Scope | 前置條件 |
|---|---|---|
| `project.create` | manage | — |
| `project.update`（改名、說明、移動、repositories；或只帶 `archived`） | manage | `expected_version` |
| `project.order` | manage | `before`：你看到的兄弟順序 |
| `project.pin` | manage | `before`：你看到時是否已固定 |
| `work_item.create` | manage | — |
| `work_item.update`（內容、步驟、狀態、移動；或只帶 `archived`） | manage | `expected_version` |
| `work_item.order`、`work_item.pin` | manage | 同上 |
| `work_item.approve` | approve | `expected_fingerprint` |
| `work_item.continue` | manage | `expected_fingerprint` |
| `work_item.link`（`remove: true` 移除） | manage | — |
| `work_item.read`（個人已讀；不改完成狀態） | observe | 已閱讀的 `expected_version`；細節見 [工作更新閱讀](work-item-reading.md) |

前置條件在保存操作前檢查一次（不符回 409，什麼都不存），執行時在同一個交易裡再檢查一次。兩個依同一版本送出的修改，後執行的那個失敗為 `VERSION_CONFLICT`。

每個修改是一個 SQLite 交易，連同 `management_applied` 的一列（operation ID 與結果）一起寫入。Daemon 在寫入後、記下結果前重啟時，重新執行同一個 operation 會回傳記下的結果，不會再套用一次。

## 完成確認

Agent 把狀態設成 `done` 是「回報完成」，不是完成：項目顯示為待確認（`awaiting_approval`），列在「待處理」。人以 `work_item.approve` 確認時帶上自己讀到的內容指紋（標題、目標、需求原文、驗收條件、步驟的 SHA-256）。標題也算：任何有 `manage` 的人都能改名。

- 確認後內容再被修改（改名也算），指紋不同，項目回到待確認，改內容的人成為新的回報者（`claimed_by`）。
- `work_item.continue`（「還沒完成，繼續」）把 `done` 改回 `doing` 並清掉確認。
- 步驟全部勾完、狀態還不是 `done` 時，也會等人決定；`continue` 記下當時的步驟，同一組步驟不再詢問，加了新步驟並勾完會再問。
- 還有未勾的步驟時不能設成 `done`（`STEPS_OPEN`）。已完成的項目改了步驟後仍有未勾的（取消勾選或新增），狀態回到 `doing`；改名等其他修改不會。
- `approve` 可以從任何狀態直接把項目標為完成（人在 Dashboard 按「標記完成」）。

`approve` 是獨立的 scope：有 `manage` 的 agent 能編輯項目、回報完成，但不能替自己的回報簽核。發 token 給 agent 時不要給它 `approve`。

完成確認只代表人接受了這個項目的內容，不代表測試通過、PR 已合併或已部署（計畫 §05）。

工作更新的 `reading` 與 `completion` 分開：讀過的項目仍可等待完成確認，已接受完成的項目
也可能對另一個 principal 是未讀。HTTP／RPC 工作列表與詳情依認證 principal 回傳 reading；
列表的 `unread` filter、CLI `item list --unread` 與 MCP 參數都沿用中央版本和分頁。

## 樹與順序

`parent_id` 決定在樹上的位置；`derived_from` 只記「從哪裡分出來」。Dashboard 的「分支」會建立一個與來源同一層、`derived_from` 指向來源的項目。

每個上層各存一份順序（`tree_order`：`projects` 或 `items:<project_id>`，最上層是空字串）：

- 固定的項目一定在未固定的上面；排序不能讓固定的項目排到未固定的下面（`PINNED_FIRST`）。
- 排序要帶 `before`，與目前順序不同就回 `ORDER_CHANGED`。
- 從沒排過的分支放在來源後面；其他新項目放在最後。
- 封存的項目保留原本的位置，復原後回到那裡；第一次排序時，已封存的項目也照預設順序保有位置。
- 專案樹與工作項目樹最多 32 層（`TOO_DEEP`）；移動時連同被移動項目底下的層數一起算。

## 封存

- 封存工作項目會連同它底下所有啟用中的項目一起封存，並記下是哪個 operation 封存的。復原只帶回同一個 operation 封存的子項目；在那之前就各自封存的不會一起回來。
- 上層還在封存中時不能復原（`PARENT_ARCHIVED`）。
- 專案還有啟用中的子專案時不能封存（`HAS_CHILDREN`）。專案封存後，它的工作項目不再出現在列表，也不能修改，直到專案復原。

## 連結

`work_item.link` 把項目連到它的執行與成果：

| kind | ref | 檢查 |
|---|---|---|
| `session` | `host/session_id` | 設定裡的主機，且 inventory 看過這個 session |
| `checkpoint` | `cp_…` | journal 有這個 checkpoint |
| `operation` | `op_…` | journal 有這個 operation（例如 `checkpoint.continue`、`integration.apply`、`github.pr.merge`） |
| `task` | Task Service 的 task ID | journal 有這個 task |
| `pull_request` | `owner/name#123` | 格式 |

讀取時每個連結附上它現在的狀態（session 標題與存取方式、operation 狀態與開出的 session、checkpoint 的 commit），全部來自 journal 與 inventory，不問主機。移除連結只標記移除者與時間，紀錄留在 `removed_links`。Session 與 operation 的讀取另附連到它的工作項目（`work_items`）。

Dashboard 的工作詳情可以從已連結的 checkpoint 直接派工：指示預先填入項目的標題、目標、需求原文、驗收與步驟，開出的 `checkpoint.continue` operation 會自動連回這個項目。

## 讀取

| 入口 | 內容 |
|---|---|
| `GET /api/v1/projects`（`include_archived`） | 專案樹（顯示順序）與各專案的項目統計 |
| `GET /api/v1/projects/{prj}` | 一個專案、它的路徑、子專案與工作項目樹 |
| `GET /api/v1/work-items`（`project_id`、`state`、`pending`、`include_archived`、`limit`、`cursor`） | 跨專案，最近修改的在前；`pending=true` 是等人決定的項目。下一頁帶上回應的 `next_cursor`（修改時間加 ID：整棵封存或復原的項目時間相同） |
| `GET /api/v1/work-items/{wi}` | 一個項目、完成狀態（`completion`）、路徑、子項目、分支、連結與最近 50 筆紀錄 |
| MCP | `projects_list`、`project_get`、`work_items_list`、`work_item_get`；修改用 `operation_submit` |
| CLI | `batc project list|show|create|update`、`batc item list|show|create|update|approve|continue|link`。`batc item approve` 要帶 `--fingerprint`（`batc item show` 的 `completion.fingerprint`），確認的是你讀過的內容；`--archive`／`--restore` 不能和其他修改一起用 |

事件：`project.*` 與 `work_item.*`（`created`、`updated`、`state`、`approved`、`continued`、`linked`、`unlinked`、`archived`、`restored`、`pinned`、`unpinned`、`ordered`），帶 actor 與 `operation_id`。

## 與 Project Hub 的對照

規則移植自 Project Hub v4.68.2（kieiken/project-hub@031aedd4，MIT；聲明見 [THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md)）。

| Hub 檔案 | 這裡 | 不同之處 |
|---|---|---|
| `hub/lib/hierarchy.js` | 改名與關係 | 關係只存 ID，所以改名不用改寫其他紀錄；以版本號取代內容雜湊 |
| `hub/lib/project-order.js`、`hub/public/project-order.js` | `siblings()`、`_save_order()` | 新項目放最後（Hub 放最前）；固定也適用於工作項目；分支的位置由 `parent_id` 決定，不跟著來源移動 |
| `hub/lib/completion.js` | `completion()`、`approve`／`continue` | 指紋含標題（Hub 的改名由人操作；這裡 agent 也能改名）；確認需要 `approve` scope |
| `hub/lib/task-ids.js` | 隨機 ID、封存保留 | 不需要依日期編號與預約檔 |

## Typed attachments（Part A；計畫 §08／§13，W05b）

create／update 的 attachments 為 `{artifact_id, revision, digest, role: input|result}` 陣列；必為 ready 精確版本。GET 回同集合，CLI item create／update 的 --attachment 接 JSON。有附件才把它加入 fingerprint，空集合保持既有 hash；改 ref／role 會讓已接受內容重新等待確認。result role 只是工作項目附件用途，尚未實作 Part B 的 managed-result capture／artifact.accept。

artifact_references 留每次 work_item 引用與釋放歷史，checkpoint 與 continuation operation 也有引用。本包不刪 artifacts，不另建 retention read model。Dashboard 編輯表單可 upload／選 role，refs 與需求原文保存於 localStorage 草稿；只有成功更新才清除那次提交。從 linked checkpoint 派工時，後端第一 step 記 work item 連結與 expected fingerprint，送出前再次查內容。見 [artifacts.md](artifacts.md)。

## 整理與永久歷史（Part A）

見 [cleanup.md](cleanup.md)：純讀 preview、signed token、逐項 operations／retained refs／tombstones，原 ID
永久可查。Dashboard #/cleanup 與 work item 的整理入口，兩語預覽／逐項回執／歷史搜尋／實際 retained list；
Part A 無 restore 按鈕。Task-owned 資源由 Task Service 整理，本輪列 TASK_OWNED；原 terminal cleanup 不變。
Restore、reviewed task leftovers／coordinator 准入與 TaskDaemon 歷史投影在 Part B。Clone／area 與 pins 留存。

## 尚未涵蓋

- 匯入 Project Hub 的資料（驗收 B05）；manual／managed artifact capture 與 acceptance（Part B）。
- 專案的階段與專案層級的完成確認。
- 在專案之間移動工作項目。
- `work_submit` 與 checkpoint 派工以外的入口自動建立連結。

## EC-1.1 navigation projection

`GET /api/v1/projects/{prj}?projection=navigation` retains the original Work IDs,
ordering, hierarchy and fresh completion state. Work rows carry title/state/version,
step counts and the first unfinished step, without request/goal/acceptance text,
attachments or the full step array. Approval fingerprints remain on the full read.
The existing project detail defaults to `projection=full`; MCP `project_get`, RPC
and CLI `batc project show --projection navigation` share the same implementation.
Unknown or duplicate HTTP projection selectors are refused. Tauri permits this
selector only on the exact project GET route.

The shared navigation chooses the projection only when the central capability
`project_navigation.version=1` is advertised; older servers retain their existing
read. Selecting an item still retrieves its full original detail. Event-page
coalescing, cursor acknowledgements, source withdrawal, selected Work/Attempt,
search and focus behavior use the original controller. No new poller, source
collector, persistent cache or read-marker write is added.

Execution lineage in one synchronous project read shares one current registry
load per configured host. It still performs the original creation/ownership and
principal visibility checks. The snapshot never survives the request or reaches
write admission: the next read, execution preview and `revalidate` load fresh
registry evidence. No Git/transcript source scan is started by navigation.

The payload regression compares actual HTTP responses on the same journal with
long original requirements and twenty steps. Its size reduction is fixture
measurement, not a live network or p95 claim. Browser fixtures cover HTTP/IPC,
older-server fallback, one refresh for a twelve-event page, query/focus retention
and displaying the original full goal after selection. Backend readback also
checks that the unchanged request and full steps remain available.

A 60-second Chromium fixture measurement used two readers per projection,
twenty Work items with twenty steps each, and eleven twelve-event batches.
Each reader made eleven project refreshes while retaining its query and focus;
none sent a write. UTF-8 JSON response bodies (excluding initial load, headers
and compression) totaled about 3.51 MB per legacy reader and 0.140 MB per
navigation reader. Selecting the full Work detail took 98–104 ms in these four
samples; this is not a latency percentile or a live-server speedup claim.
The actual HTTP payload test separately measured 36,645 → 982 bytes on one
journal-backed item. The lineage fixture reduced one project read from three
registry loads to two, including its unchanged principal control check, and
proved the next read and write revalidation see changed source evidence.

These fixtures measure bounded payload/read behavior. Real source-collector
cost, multi-client deployment latency and physical-mobile behavior remain
EC-1.1 T3/T4 acceptance work.

## GitHub board source

The optional [GitHub board sync](board-github-sync.md) imports and adopts GitHub
issues/PRs through registered project/item actions, with stable external identity,
legacy URL deduplication and retained history. Its Connector-owned schedule and
provider reads live outside this journal-only module. External closure has a
separate board lane and never grants content approval or completes local steps.
