# 資源政策：人工 session 對 API 永久唯讀

日期：2026-10-07。對應《Better Agent Dashboard／Connector 計畫》v1.0 的 D05、D06、§06、§24，工作包 W02（全 mutation 政策、canonical path、manual 保護），驗收 A01、A02。程式在 `src/bat_agent_connector/resource_policy.py`，測試在 `tests/test_resource_policy.py`。

## 保證

每個 BAT 寫入 frame 都要帶一張 `WriteGrant`。`BatClient.invoke` 在送出前核對 grant 的 host、channel 與 `sessionId`，不符就丟 `ResourceReadOnly`，frame 不會離開程序。`append_workspace_terminal`（`workspace:save`）同樣檢查。只有 `resource_policy.py` 會建立 grant，`test_only_the_policy_mints_write_grants` 掃描原始碼確認這點。因此 MCP、`batc`、task service 與舊工具都走同一個檢查，`confirm`、`force`、`discard_uncommitted`、`allow_unmerged`、`all_exhausted`、`approve_pending` 都不能繞過。

人工（`manual`）與來源不明（`unknown`）的 session 永遠不收到 send、continue、answer、interrupt、client-resume、權限變更、批次批准、stop、rehydrate、merge、remove 或 cleanup。

## Session 來源

| 來源 | 證據 |
|---|---|
| `connector_managed` | Connector registry 有這個 session 的建立紀錄（`registry.reserve` 先寫入的 `created_at`，或 task journal 回復時經 BAT 身分核對的 `recovered_from = task_journal`），而且 BAT 已確認啟動（狀態不是 `starting`、`uncertain`、`failed`）。 |
| `manual` | BAT workspace 有這個 tab，但 connector 沒有建立紀錄。永久唯讀，不會轉成 managed。 |
| `unknown` | 其他情況：沒有 tab 也沒有紀錄，或 connector 預留了 ID 但 BAT 沒確認啟動。對帳完成前唯讀。 |

`has_tab`、`orchestrated`、provider、資料夾名稱、UI 認領或口頭確認都不授予寫入。重試啟動留下的同 session 多筆 registry 紀錄，以最新一筆為準。

## 工作目錄歸屬

Session 歸屬與資料夾歸屬分開判斷。Managed session 只有在 connector 擁有它的工作資料夾時才可驅動：

| 歸屬 | 條件 | 隔離程度 |
|---|---|---|
| `managed_root` | 資料夾在主機設定的 `managed_roots` 內（connector 自有 clone）。 | `managed_clone` |
| `connector_worktree` | 紀錄的 `worktree_path` 等於 `cwd`，位於 workspace 資料夾（或其上層 git root）的 `.bat-worktrees/<name>`，而且是 connector 自己建立的；失效接手的 successor 與 reviewer 共享的 worktree，要追溯到同樣條件的 managed session。 | `legacy_shared_clone` |
| 人工 | 主 checkout、BAT GUI 建立的 worktree、共享人工 session 的 worktree。 | 無 |

Connector 經 SSH git 自己建立的 worktree（checkpoint 的 `batc-cp-…`、解衝突的 `batc-fix-…`、Task Service 的 `batc-task-…`；registry 記 `worktree_made_by: "connector"`，舊紀錄以 `batc/` 開頭的分支辨認）BAT 沒有紀錄。BAT 的 `worktree:rehydrate` 會把它登記在 session 的 workspace 資料夾（實際使用時可能是人的 checkout）底下並把那裡的 env 檔案複製進去，`worktree:remove` 會在那個 repository 執行 prune，所以 rehydrate、merge、remove 對它一律回 `NOT_A_BAT_WORKTREE`，`session_cleanup` 也保留它。送字、停止等 session 動作不受影響。

Reviewer 的 session 建立紀錄不證明共享 worktree 的建立者。Registry root walk 若停在沒有 connector marker 的 reviewer（例如只有 task_id、缺 lead_session_id 的舊列），maker 為 `unknown`，BAT 的 rehydrate／merge／remove 同樣回 `NOT_A_BAT_WORKTREE` 並說明 creation root 無法證明；managed_roots 不繞過此限制。Policy 不為 raw CLI 啟動 daemon 或猜 journal 路徑。Observation 可用自己 journal 的 lead 證據保存身分，但不能代替 mutation grant；已證明的共享 carrier 與 session 動作沿用原規則。

`legacy_shared_clone` 的 worktree 仍在人工 clone 內，共用它的 refs 與物件庫（見 git-worktree(1)），不是完整隔離。只有 `managed_clone` 符合計畫 §07 的獨立 clone。位於人工資料夾的 connector session 是 §24 說的 legacy boundary：保留觀測與歷史，全部唯讀（包括 stop），由操作者在 BAT 處理。

## 寫入前即時核對

Registry 只是 connector 自己的說法。每次寫入前再讀 BAT：

1. `claude:get-session-meta` 的 `cwd` 要等於紀錄的資料夾（每個動作）。
2. `git:getRoot(cwd)` 要等於 worktree 本身（managed root 則要落在 managed root 內）。指回主 checkout 的連結會在這裡被擋下。送字、回答、權限與 worktree 動作做這項；interrupt、stop 不碰資料夾，只核對 1。
3. Worktree 動作（merge、remove、rehydrate）另外比對 `worktree:status` 的路徑。這個 channel 會計算整條分支的 diff，大分支要一分鐘，所以送字、回答、中斷不讀它，以免在 host 寫入鎖內等待。

BAT tab 記的 `cwd`／`worktreePath` 和紀錄不同也是 `BINDING_MISMATCH`；紀錄沒有 worktree 而 tab 有，同樣算不符，否則 worktree 動作會作用在 tab 指的路徑。

不符回 `BINDING_MISMATCH`。送字、回答、權限與 merge 另外要求資料夾存在（`WORKDIR_MISSING`）；stop、interrupt、remove 在資料夾已消失時仍可執行。`session_cleanup` 先看 registry 與 tab（不讀 host），只在要碰 worktree 時才做即時核對；資料夾已消失時不送 `worktree:rehydrate`。

`managed_roots` 請填實際路徑（不要經過 symlink）。開新 session 前會用 `git:getRoot` 讀目的地的實際位置：managed root 內的路徑若解析到 managed root 之外（連結到人工 checkout），在任何寫入前拒絕（`DESTINATION_MANUAL`）。

## 目的端

- **新 session**：不能直接在人工 checkout 工作（`use_worktree=false` 只限 managed root）。在人工 clone 內開新 worktree，只有 `shared_clone_worktrees = true`（預設，legacy）時允許。BAT 把 worktree 建在 workspace 資料夾解析後的 git root 下，所以經 symlink 開的 workspace 以 `git:getRoot` 的結果比對，並記入 registry 的 `origin_root`。BAT 回傳的 worktree 不在預期位置時中止，且不 rollback：位置不明的路徑可能就是人工 checkout，`worktree:remove` 加 `deleteBranch` 可能刪掉人的分支。
- **Merge**：BAT 的 `worktree:merge` 在主 checkout 執行 `git checkout`／`merge`，只有主 checkout 位於 managed root 時才允許，否則回 `DESTINATION_MANUAL`。主 checkout 取 session 建立時記下的 `origin_cwd`，而且必須仍是 tab 所在 workspace 的資料夾；workspace 後來改指別處就是 `BINDING_MISMATCH`，不會改用新位置。`session_cleanup` 對準備好合併但目的端是人工 checkout 的工作改判 `ESCALATE`。
- **Task service 的 SSH worktree**：只能是 `<workspace>/.bat-worktrees/batc-task-<12 hex>` 與分支 `batc/task-<12 hex>`，而且 `shared_clone_worktrees = false` 時（workspace 不在 managed root）在任何 SSH git 之前就拒絕。
- **Fan-out planner**：在自己的 worktree 規劃；fanout 後只透過原 stop path 停止 idle planner，保留 worktree 與 branch，交由 resource-cleanup 預覽。
- **Failover（`all_exhausted`）**：Claude 額度是整個帳號共用，人的 tab 會和 managed session 一起用完。唯讀 session 先剔除、列在 `skipped_read_only`，`max_start_per_call` 只算 managed session。

## 全 mutation 清單

權威來源是 `resource_policy.MUTATIONS`；`batc policy HOST` 與 MCP `session_policy(host)` 輸出同一份表，`test_mutation_table_classifies_every_bat_write_channel` 確認每個寫入 channel 都有歸類。

| 動作 | 入口 | 規則 |
|---|---|---|
| `session.send` | `session_send`、`session_continue`、`session_relay`、task service send | managed session ＋ connector 擁有的資料夾 |
| `session.answer` | `session_answer`、`approve_pending` | 同上 |
| `session.permissions` | `session_set_permissions`、`approve_pending` | 同上 |
| `session.interrupt` | `session_interrupt`、task interrupt／`work_pause(abort_current)` | 同上 |
| `session.stop` | `cleanup.apply`、fanout planner | 同上 |
| `worktree.rehydrate`、`worktree.remove` | `worktree_merge`、`worktree_remove` | 同上 |
| `worktree.merge` | `worktree_merge` | 同上，且目的端在 managed root |
| `session.create` | `session_start`、`session_failover`、`session_relay(start_if_missing)`、fan-out、task lead／reviewer 啟動 | 先在 registry 預留 ID；資料夾是 managed root、connector 新建的 worktree，或共享 connector 擁有的 worktree |
| `workspace.register_tab` | `session_start`、task reviewer 啟動 | 只為剛啟動的 managed session 追加 tab，其他 tab 必須保持不變 |
| `task.external_worktree` | task service 的 `base_branch` 啟動與整理（SSH git） | 固定命名的 connector worktree |
| `checkpoint.managed_worktree` | `checkpoint.continue`（SSH git） | managed root 下一層的 connector clone，與固定命名的 worktree 及分支 |
| `integration.area` | `integration.preview`、`integration.apply`、`batc integrate`（SSH git） | 只寫 `<第一個 managed root>/.batc-integration/<name>-<8 hex>/repo.git`，每次寫入前核對身分；其他 repository（包括人的）只當 fetch／ls-remote 的來源 |
| `integration.push` | `integration.apply`（SSH git，scope `remote`） | 一般 push 一個確切 commit 到同 repository、開著的 PR 的 head 分支；不強推、不刪除，不推 base、預設或受保護分支。見 [integration.md](integration.md) |

只寫 connector 自己資料的動作（`session_record_verification`、`work_*` 帳本）不碰 BAT 或 Git，不需要 grant。

## 拒絕代碼

| 代碼 | 意思 |
|---|---|
| `MANUAL_READ_ONLY` | 在 BAT 建立的 session。 |
| `UNKNOWN_READ_ONLY` | 來源無法證明，或啟動尚未確認。 |
| `WORKDIR_NOT_MANAGED` | Managed session 但資料夾是人工的（legacy boundary）。 |
| `BINDING_MISMATCH` | BAT 的實際狀態和 connector 紀錄不符。 |
| `WORKDIR_MISSING` | 需要資料夾的動作，但資料夾讀不到。 |
| `NOT_A_BAT_WORKTREE` | BAT 的 worktree 動作（rehydrate、merge、remove）用在 connector 經 SSH 建立、沒有記錄 path，或 reviewer 建立根節點無法證明的 worktree。 |
| `DESTINATION_MANUAL` | 新 session 或 merge 的目的端是人工 checkout。 |
| `DESTINATION_UNKNOWN` | 目的路徑不是絕對路徑，或 BAT 建在預期外的位置。 |
| `TIER_DISABLED` | 只出現在 `session_policy`：主機沒開對應層級。 |
| `CLONE_NOT_OURS`、`CLONE_CONFIG_TAMPERED` | 整合區的標記、本地設定或完整性不符（被改過），什麼都不做。 |
| `PR_CLOSED`、`PR_HEAD_IN_FORK`、`TARGET_REF_FORBIDDEN` | 整合不推送到已關閉、fork 或 base／預設／受保護分支的 PR head。 |
| `GRANT_REQUIRED`、`GRANT_MISMATCH` | 程式缺陷：某段程式沒經過政策就要送寫入 frame。 |

## 升級後的行為差異

- `session_relay` 不再寫入人工主 session。預設目標改為同 workspace 最近的可寫 managed session；沒有可寫目標時回 `no_session` 或 `read_only`，`start_if_missing=true` 在新 worktree 開 Codex session。
- `session_failover` 只接續 managed session。人工 session 的後續工作應從其 commit 另開 managed worktree（計畫 §12，checkpoint 流程屬 P2）。
- `fanout_plan_session` 的 planner 在自己的 worktree，不在主 checkout。
- `approve_pending` 跳過唯讀 session（`skipped: read_only`）。
- `session_cleanup` 對人工與 legacy session 一律 `KEEP`，不 stop、不 merge。
- `sessions_list`、`sessions_triage`、`worktree_status` 每列多了 `provenance` 與 `api_access`。

## Reviewed cleanup（Part A）

[cleanup.md](cleanup.md) 使用同一 ownership policy；cleanup.guard 從原 registry 在每 process 讀同一
reservation／confirmed tombstone，回 CLEANUP_IN_PROGRESS／RESOURCE_CLEANED。authorize_session 與 checkpoint／
repair／integration area／external worktree checks 各一個 hook；legacy merge/remove 也不能穿過 guard。
Marker 不授予 ownership；SSH 每次重新驗證 canonical paths、markers、common dir、manifest／CAS。
先 preserve 再非 force remove，clones／areas 與全部 refs/batc/* 保留。Task-owned 資源本輪 TASK_OWNED，
TaskDaemon 原 terminal cleanup 不變。Legacy session_cleanup 的 apply 一律 LEGACY_CLEANUP_DISABLED (409)；
auto_cleanup deprecated，只保留 config parsing，不啟用 writes；read evaluation／explicit merge/remove 保留。
## Task authority（2026-10-08，Part A）

task link 不授予 resource write grant。send／answer／interrupt／permissions（含 client-resume、approve-pending、deferred raises 與 relay target）先通過本政策，再交同一 owner 的 TaskCoordinator；paused／verifying／pending commands／stale control_version 皆拒絕。frame 邊界再次核對原 journal command、session ownership 與 owner lease。registry task 標記遺失時仍查原 journal 的 current session／start reservation／branch，不能因此變成 standalone。task-owned worktree／failover／外部 verification 不由低階工具接管，cleanup KEEP；原受信 verifier 仍可保存其 observed evidence。詳見 [operations-unification.md](operations-unification.md) 與 [api-v1.md](api-v1.md) 的穩定拒絕碼。

## 尚未涵蓋

- **A10 執行環境限制**：[confinement](confinement.md) 已新增 options／creation snapshot／current_verification 及不可 raise 閘門。Claude acceptEdits 沒有 path check；未查核 account 時 confined Claude 用 default，僅 prompt_gated。Codex sandbox 最多 options_confirmed，A10 尚待 W12 live run。資源歸屬、cwd、legacy_shared_clone 都不證明 agent 執行隔離；Task Service 保留測試行為並顯示 gap。
- **Managed clone 的建立與 checkpoint 接續**（P2）。目前 `managed_roots` 只描述已存在的 clone。
- **`workspace:save` 與 GUI 同時存檔的 race**（既有，見 SECURITY.md）。
- **Part B legacy operations**：HTTP OperationService 已走同一 resource policy；其餘舊工具的 operation 轉接與外部 steps 拆分見 [operations-unification.md](operations-unification.md)。

## 設定

```toml
[hosts.buildbox]
managed_roots = ["/srv/batc-managed"]   # connector 自有 clone；可合併到其主 checkout
shared_clone_worktrees = true           # false：只允許在 managed root 內開新 worktree
```

## Authenticated controller authority (PM v1.2 W02)

Resource provenance and caller authority are separate. A connector-created session
retains `connector_managed` provenance when another caller reads it, but its action
verdicts refuse that caller's mutations. The existing manual, unknown, folder,
confinement, task, failover and merge checks still apply.

`controller_authority.binding()` derives the controller from the authenticated
principal and the journal's existing durable `server_id`. Clients cannot submit a
controller ID, and a shared repository, BAT workspace, account, path or `Fleet.actor`
is not controller evidence. Multiple authenticated clients with the same actor on
that central server share the controller identity. A different server or actor does
not. Administrator or PM owner status does not override a foreign controller.

The authenticated operation/task adapter enters `controller_authority.use()` before
new effects. Bindings contain the original server/controller IDs, actor, scopes and
administrator flag, plus an optional **PM** workspace ID; they contain no credential
or credential hash. PM workspace IDs are distinct from BAT's inventory workspace
IDs. An explicitly scoped caller cannot switch workspace. Acting on a PM-scoped
resource also requires current write membership, checked against the context's
journal. Revoked membership blocks unapplied effects. Credential revocation blocks
future authenticated calls; it does not silently replace an already admitted
intent's original controller.

New registry reservations persist immutable `controller_authority` and
`controller_incarnation` fields. A creation grant may validate a destination before
reservation, but every BAT write frame must find the actual reserved row with the
same controller and incarnation. Existing session grants pin that incarnation too.
Shared worktrees, replacements, failover predecessors and warm session reuse must
belong to the same controller; an ended session does not transfer ownership of its
carrier. Registry projections and same-ID unsent retries cannot replace the original
authority fields or drop their PM workspace scope. A durable fresh-worktree start
may read a foreign source folder to create its own separate carrier; this read does
not claim that source or permit a session start in it. The actual resulting carrier
still passes the controller checks before later frames.

Explicit source selectors retain their admission-time identity; prefix and automatic
selection persist the resolved session ID before further reads. Failover, fanout,
relay and worktree merge do not retarget a pending operation to a replacement with
the same ID. `SESSION_SOURCE_CHANGED` requires reviewing the current source before
submitting a new intent. Read-only use of an unchanged foreign planner remains allowed.

Recovery may restore a missing registry row only from original durable controller
and incarnation evidence, together with the existing session/task identity checks.
Legacy rows without this evidence remain readable and are not adopted from an actor
string or path. `use(None)` permits observation and sent-effect reconciliation but
cannot grant a new write. Direct service helpers without an authenticated context
fail closed. Operation-owned SSH/Git carriers use their creating operation's
binding; fixed path validators remain classification checks and confer no caller
authority. The shared Connector integration area retains its existing serialized
service contract rather than becoming one session controller's checkout.

New refusal codes include `CONTROLLER_AUTHORITY_REQUIRED`,
`CONTROLLER_OWNER_UNPROVEN`, `FOREIGN_CONTROLLER`,
`CONTROLLER_INCARNATION_CHANGED`, `CONTROLLER_WORKSPACE_CHANGED`,
`CONTROLLER_MEMBERSHIP_CHANGED`, `CONTROLLER_IMMUTABLE` and `SESSION_SOURCE_CHANGED`. These restrictions are
shown per action without rewriting provenance or granting takeover through `force`.

## AHP owned-resource frame seam

`ahp_owned_resources.schema(journal)` explicitly installs an append-only private
ledger; daemon startup does not install it or allocate a data step. Reservations
are intents and cannot satisfy `controller_authority.require_entry`. A typed
creation receipt requires the original create ACK and exact session/chat snapshots
from that connection, including provider, directory and config values. Optional
native `createdAt` comes from that connection's exact catalog summary. A catalog
URI, managed-root path, token, manage scope or administrator is not ownership.
Changed evidence conflicts without rewriting history. Endpoint changes refuse;
a matching URI or timestamp after restart cannot adopt another resource.

Only `resource_policy.mint_ahp_grant` issues the process-local typed capability.
Its identity and immutable signature bind operation, controller incarnation,
adapter/host/endpoint, Work/Attempt, session/chat/turn, method/action, managed
creation receipt and config/profile/source digests. Copies and outside constructors
are refused. The future central worker must provide synchronous current binding
and permission/busy readback; this seam does not invent an adapter runtime.

`ahp_owned_transport` checks after connect, semaphore and fresh-read awaits:
current credential scopes, membership and controller; shared task/coordinator and
lifecycle gates; fixed Work/Attempt/profile/source; managed-directory receipt,
ownership, editor permission, busy and cancellation. The final synchronous check
and `pm_source_lineage.require_frame()` precede the durable sent-fence commit and
synchronous socket enqueue, with no intervening await. Outer transactions refuse.
After the fence the effect remains unknown, including transport failure; no retry,
new operation/turn substitution or lock clearing can resend it. Only future
readback reconciliation may resolve it. Work budget remains thirty minutes and
permission answers remain in the editor.

No production action, route or API mints this grant. Create, turn, result, API and
UI implementations are deliberately absent; `require_owned_write()` still refuses.
Tests use a test-only receipt producer and synthetic frames over a disk Unix socket.

繁體中文：AHP 帳本明確安裝、只追加，不進 BAT registry。預留不是所有權；須有同連線
ACK 與精確 snapshot，重啟不認領同 URI。只有中央資源政策鑄造 grant；每個 await 後
重驗現行權限、controller、coordinator、來源與生命週期、Work／Attempt／profile、
所有權、permission、busy 與取消，再經 lineage guard、提交 sent fence，才同步送出。
跨 fence 的未知結果只能查回，不重送；本輪沒有 create、turn、result、API 或 UI。

### Separate AHP creation authority

`AhpCreateGrant` is distinct from the existing turn `AhpGrant`. Only the central
policy issues either capability; copies, outside constructors and cross-kind
frames refuse. Creation binds the original handoff operation, controller
incarnation, Work/Attempt, adapter/endpoint, reserved session URI, exact
`createSession` request, carrier reference and config/profile/source digests.
It uses Work-start scopes; turns retain observe/operate and existing ownership.
Creation is not a turn and cannot satisfy `controller_authority.require_entry`.

The explicit ledger reservation can retain a create intent without a chat URI:
client/request IDs, provider/config, original handoff parent, step, carrier and
bounded catalog absence evidence. The policy verifies the durable handoff
relation; the same host/session URI cannot be reserved under a different resource
ID to conceal an earlier intent. Each handoff operation retains one create intent,
including after an unknown send; a new URI cannot replace it. Identical intent
replays confer no new grant.
It reads back the original `CarrierReceipt` through `native_carrier`.
It repeats readback before the final frame. Fresh complete absence must describe
the same endpoint and connection; a catalog window is never creation ownership.

Both grant types use the same guard and current-authority checks after awaits.
After the lineage gate, the grant type selects the existing `create_sent` fence
or the turn fence. The transaction commits before socket enqueue. A create fence
prevents another send even if the reply was lost; observation alone cannot clear it.

An exact same-connection ACK and ready session snapshot establish creation.
The advertised default chat, or sole advertised chat, is then bound in a separate
append-only record referencing that creation receipt. Turns require this record.
A rebind validates the fresh snapshot and preserves the first record for the same
creation and chat. A different creation or chat conflicts without replacing it.
No createChat, route, action, API, UI or production write caller is installed.

繁體中文：create grant 與 turn grant 分型，均只能由中央政策鑄造，不可互換。
create 只授權原 Work／Attempt／handoff child 的未送出預留；不是 session 所有權。
原 carrier 在鑄造及最後送出前都須查回，absence 須完整、即時且同 connection。
共用 guard 依型別選擇既有 create／turn fence，提交後才送出；未知結果不重送。
session 建立證據成立後，另行追加其宣告 chat 的綁定；重綁須驗證新 snapshot，
相同建立與 chat 保留首筆記錄，不同身分為衝突；沒有綁定
不能 turn。此增量仍無正式 create route、action、API 或 UI。
