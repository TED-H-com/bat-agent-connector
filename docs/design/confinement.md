# Managed session 的執行限制

日期：2026-10-08。審查後 Phase 2 規格；以下新增行為按本輪決議實作。對應計畫 §06「執行環境的可寫範圍」、§07、§12、§28，W02／W04 remainder，驗收 A10。沿用 [資源政策](resource-policy.md)、[checkpoint](checkpoints.md)、[整合](integration.md) 與 [OperationService](api-v1.md)。

## 固定來源版本

| 來源 | 版本與用途 |
|---|---|
| Connector | 分支 `feat/confinement`，起點／`origin/main`：`566eba6b63e49eeca38383f4029a3a7abec98801`。以下現況清單以此 commit 的程式為準。 |
| BAT | 計畫固定 commit `b7419892fbc9946799b64cca24c2ec8c7fa15c42`。直接讀取此版 source archive；不以本機 BAT checkout 或上游最新 main 代替。 |
| BAT 協定筆記 | [docs/PROTOCOL.md](../PROTOCOL.md)，v3.2.12 的遠端 channel、meta、resume 與權限變更筆記。它和固定 source 分開引用，不假定每台主機已裝固定 source。 |
| 既有交接 | [2026-10-08](../handoff/2026-10-08.md)、`CONTRIBUTING.md`。交接中的較舊 head 不取代本規格起點。 |

BAT 原始碼引用，均固定於上述 commit：

- [S1：src-tauri/src/remote_server.rs](https://github.com/tony1223/better-agent-terminal/blob/b7419892fbc9946799b64cca24c2ec8c7fa15c42/src-tauri/src/remote_server.rs)：`claude:start-session`／`resume-session`／`client-resume` 的 Codex native routing、`invoke_sidecar_for_remote`、兩個 Codex permission setter。
- [S2：node-sidecar/src/handlers/claude-session.mjs](https://github.com/tony1223/better-agent-terminal/blob/b7419892fbc9946799b64cca24c2ec8c7fa15c42/node-sidecar/src/handlers/claude-session.mjs)：Claude start、`resumeClaudeSession`、client-resume、`claude.setPermissionMode`。
- [S3：node-sidecar/src/handlers/claude-send.mjs](https://github.com/tony1223/better-agent-terminal/blob/b7419892fbc9946799b64cca24c2ec8c7fa15c42/node-sidecar/src/handlers/claude-send.mjs)：`buildQueryOptions` 建立真正交給 Agent SDK 的 options。
- [S4：node-sidecar/src/handlers/claude-permission.mjs](https://github.com/tony1223/better-agent-terminal/blob/b7419892fbc9946799b64cca24c2ec8c7fa15c42/node-sidecar/src/handlers/claude-permission.mjs)：`buildCanUseTool`、`ACCEPT_EDITS_AUTO_APPROVED_TOOLS`、resolve handlers。
- [S5：src-tauri/src/codex_app_server.rs](https://github.com/tony1223/better-agent-terminal/blob/b7419892fbc9946799b64cca24c2ec8c7fa15c42/src-tauri/src/codex_app_server.rs)：`normalize_sandbox`／`normalize_approval`、`start_session`／`resume_session_locked`／`client_resume`、`build_thread_start_params`／`build_thread_resume_params`／`build_turn_start_params`／`app_server_sandbox_policy`、`reconfigure_session`。
- [S6：node-sidecar/package.json](https://github.com/tony1223/better-agent-terminal/blob/b7419892fbc9946799b64cca24c2ec8c7fa15c42/node-sidecar/package.json)：Agent SDK 固定為 `0.3.285`。SDK 能接受某欄位，不等於 BAT 已轉送它。

## 現有與新增行為差異

`resource_policy.WriteGrant` 保護的是 Connector 送出的 BAT／Git 操作，不是 agent subprocess 的檔案權限。`isolation: managed_clone` 表示 Git 資源歸屬；`cwd` 表示起點。兩者都不是 shell／檔案寫入的隔離證據。

目前只有 checkpoint 接續與 integration repair 明確傳 `write_scope: confined`。其他 start 大多使用 `permission_options()` 與 host 的 `default_permission_mode`。目前沒有 level、host account 宣告或查核結果。新增一個共用 confinement 決策與證據物件，所有 start 與權限變更都使用它；不另建 ownership gate、task database 或 agent engine。

**需要更正既有描述**：固定 BAT 的 S4 在 `acceptEdits` 下直接 allow `Write`、`Edit`、`NotebookEdit`、`Read`、`Glob`、`Grep`；callback 沒有核對 path。Claude CLI 把需要詢問的工具交給 callback 時，這條分支仍可能放行目錄外的編輯。因此目前 `CONFINED_OPTIONS["claude"]`、checkpoint 文件、skills 與 `i18n.confined_note` 所說「目錄外一定詢問」不是這版 BAT source 能證明的保證。Shell 仍有 permission 流程，也不等於 OS sandbox。

一般 start 保留操作者的 `default`／`allow_all` 選項。新增 `default_permission_mode=confined` 才套受限 options／旗標；checkpoint／repair 固定受限。無已查核 host_account 的受限 Claude 用明確 `permissionMode=default`；有帳號證據才可 acceptEdits。既有 acceptEdits session 不改 mode；如無其他證據，記 none、部分工具詢問缺口。

## Claude worktree settings 的評估與決定

固定 SDK `0.3.285` 的 [官方 npm metadata](https://registry.npmjs.org/@anthropic-ai/claude-agent-sdk/0.3.285) 記 `claudeCodeVersion=2.1.285`；BAT S3 另會找主機安裝的 CLI，所以 SDK 版本不能證明 host 實際 CLI 版本。評估了在 managed worktree 寫 `.claude/settings.local.json`、canonical-path Edit／Read allow、settings 自身 deny、Bash sandbox 與 clone info/exclude。

官方 [permissions 文件](https://code.claude.com/docs/en/permissions#read-and-edit) 說 Edit rules 涵蓋 file tools，但不涵蓋任意 Python／Node 子程序的間接寫入。[settings precedence](https://code.claude.com/docs/en/settings#lists-merge-instead-of-overriding) 說 allow lists 會合併，不能靠 local allow 清掉 user／project 的其他批准。文件沒有一個能由此檔完整封住所有外部寫入的 policy replacement。

[Sandbox 文件](https://code.claude.com/docs/en/sandboxing#repository-settings-under-an-admin-required-sandbox) 明列 v2.1.285 的 admin-required 規則：需要 managed settings 或 `--settings` 的強制政策；local settings 本身不構成這個邊界。Sandboxed Bash 會保護 `.claude` 設定，但 inherited excludedCommands／已批准的 unsandboxed Python／Node 可以改 settings，file deny 不能擋其間接寫入。BAT 未轉送可信 per-session sandbox/settings override。這是保守判斷：無法證明此 local-file 方案在任意 host settings 下維持要求的邊界，故本包不寫該檔或 info/exclude。採用已核准 fallback：plain default，未預先授權的 edit／Bash 會詢問；表單提示並推薦無 account 證據時使用 Codex。已有規則／單次批准仍是明示缺口，不宣稱每次 edit 絕對必問。

## 全入口盤點：今天得到什麼

表中的「預設」是 `permission_options()`：Claude `allow_all` → `bypassPermissions`；Codex `allow_all` → `danger-full-access`＋`never`；`default` 不送權限欄位。BAT 固定 source 的 Codex omission default 是 `workspace-write`＋`on-request`；這不能用來推斷不同版本的主機，也不是現有 registry 已保存的證據。

| 檔案／函式與呼叫端 | 現在的 start／reconfigure 與紀錄 | 今天的可寫範圍證據／缺口 |
|---|---|---|
| `orchestrate.py:permission_options`、`session_start`；`mcp_server.py:session_start`、`cli.py:_run` 的 `start` | 預設；Claude 可傳 `permission_mode`。reserve 與正常成功時存 `permission_mode_claude`／`agent_params`。 | `allow_all` 無 CLI 寫入限制；default 僅可依實際 agent／BAT options 判斷。registry 沒有 level。 |
| `checkpoints.py:start_in_worktree`、`_run_continue` | `write_scope=confined`；Claude `acceptEdits`，Codex `workspace-write`＋`on-request`，不看 host default。 | Claude file callback 的 path 缺口；Codex 的 sandbox 設定有證據，OS 實際阻擋需實機驗收。 |
| `integration.py:_run_handoff` | 共用 `checkpoints.start_in_worktree`，在 repair worktree 啟動。 | 和 checkpoint 相同；integration area 是目的地政策，不是執行 sandbox。 |
| `lifecycle.py:session_relay` 的 `start_if_missing` | 用 `session_start` 開 Codex worktree，預設。原 read-only session 保留不動。 | 新 session 沒有沿用來源的 confinement 證據。 |
| `lifecycle.py:fanout_plan_session` | Codex planner 呼叫 `session_start(..., permission_mode="default")`。 | 此參數只對 Claude 有效；Codex 仍用 host default。`PLANNER_PREFACE` 的 read-only 指示不是限制。 |
| `lifecycle.py:fanout_from_plan`；`cli.py:_run` 的 `fanout --start` | 每項用 `session_start`，預設。`fanout_plan`／`read_plan` 本身不啟動 session。 | 每個 child 都可能是 allow-all；沒有每項 confinement 回執。 |
| `lifecycle.py:session_failover`／`_failover_one`，包含 `all_exhausted=true` | confined predecessor → Codex confined options；其餘 → host default。沿用 worktree 或 archive-only start 都經同一函式。 | `write_scope` 有沿用；但 successor reserve 尚未保存 permission fields，要等 start 成功才保存。ACK 不明時可能缺選項證據。 |
| `task_bat.py:BatAdapter.start` 的 lead；`task_core.py:TaskCoordinator._start` | lead 用 `session_start`，預設，含指定 base branch 的 external worktree 路徑。 | Task Service 的 testing／續推行為依既有權限運作；不能在本包改 engine／recipe。 |
| `task_bat.py:BatAdapter.start` 的 reviewer 分支 | 直接 `claude:start-session`：Codex `read-only`＋`never`；Claude `plan`。 | 有明確 start options，但 reserve／tab 未保存 permission fields。此為仍存在的相容分支，不恢復已移除的 reviewer 流程。 |
| `task_bat.py:find_warm`、`_warm_identity`、`start` warm 分支；`registry.py:claim_warm` | 驗證乾淨、idle、前次已驗證 HEAD 後，轉 task 關係，不重新 start。 | 權限保持原值；不能按新 task 的預設補成更強 level。 |
| `task_bat.py:failover`、`recover_failover` | 共用 `lifecycle.session_failover`，另核對 successor／handoff 意圖。 | 後端路徑仍存在；不啟用 TaskCoordinator 已停用的中途 failover。限制不能因恢復而丟失。 |
| `service.py:session_send`、`session_continue`、`_resume_params`、`registry_terminal` | meta null 才 client-resume；options 來自 tab，無 tab 才由 registry 合成。 | 沒有重新採 host default；但 tab 可舊／不含 permission fields。不能依 cwd 宣稱 resume 保持限制。 |
| `checkpoints.py:start_in_worktree.restart` | ACK 不明後按預留 ID／cwd 讀回，補 active，不重開。 | 現在只核對 cwd；新增必須也核對限制 options，才能把 level 記為已確認。 |
| `task_bat.py:recover_start`、`session_presence`、`_restore_headless_lookup`；`registry.py:ensure_existing` | 以 task commands／branches、BAT meta、workspace／worktree 證明既有 session，恢復 lookup。 | 原 registry 還在時不抹去未指定欄位；完全遺失時重建列沒有 permission fields／write_scope。身分證據不能替代限制證據。 |
| `orchestrate.py:_wt_status`、`worktree_merge`、`worktree_remove`；`lifecycle.py:_evaluate`／`session_cleanup` | `worktree:rehydrate` 恢復 BAT 的 Git worktree 紀錄。Connector 自建 worktree 由政策拒絕 rehydrate。 | 不啟動 agent，也不套用 sandbox；不得藉此重算／升級 level。 |
| `lifecycle.py:session_set_permissions` | allow-all 需 host allow-all；confined 拒絕。Claude 閒置才切換（`force` 可繞過 in-flight）；Codex 兩個 setters。 | confined check 在 `force` 之前。`default` 可以更改 confined options；Codex default 實際送受限選項，但 registry 用空 `permission_options()` 更新，丟失這份證據。 |
| `lifecycle.py:approve_pending`、`_raise_deferred` | bulk 跳過 read-only／confined。其餘逐項 answer 後 raise；Claude in-flight 記 `permission_raise_pending`，之後再試。 | deferred 真正 raise 仍經 `session_set_permissions`；dry-run 尚會列出 confined 舊 pending raise。須在計畫與 frame 前都查，不能只靠 bulk 開頭判斷。 |
| `service.py:session_answer`；`api_actions.py:_answer` | 逐次 permission allow／deny，可 `dont_ask_again`；已有 Task Service answer gate。 | confined 不攔逐次 allow；可批准 shell／sandbox escape。S4 的 `ExitPlanMode`＋`dontAskAgain` 可把 Claude 改成 `acceptEdits`，也必須視為 mode 變更。 |
| `orchestrate.py:session_start`、`lifecycle.py:session_failover`、`BatAdapter.start` 的 tab registration；`service.py:_resolve_session` | `append_workspace_terminal` 保存／取用 tab 的 permissionMode／agentParams。 | tab 是 projection，不能覆蓋已記的 confinement 意圖。reviewer 的 projection 現在不帶這些選項。 |

`channels.py` 沒有開放 connector 呼叫 `claude:resume-session`、reload／reset／fork、set-model／set-effort 或任意 SDK option 通道；CLI／MCP 沒有另一個 raw start。Goose 的 task-scoped session 工具仍經 `BatAdapter`。盤點涵蓋目前存在但 coordinator 未啟用的分支，不藉本包重新開啟它們。

## 不改 BAT 可以傳什麼

S1 的遠端 routing 會把 `options` 交給 native Codex 或 Node handler；不是任意欄位都會一路交到 CLI。PROTOCOL §4 的 v3.2.12 筆記證明 channel 與既有欄位，未聲明通用 sandbox settings 介面。

| Agent／欄位或通道 | 固定 source 的實際落點 | 可達與不可達 |
|---|---|---|
| Claude `options.permissionMode` | S2 存 session；S3 `buildQueryOptions` → SDK `permissionMode`。`default` 不填 SDK 欄位，用其 default。 | 可達。S2 setter 接受字串；不是 capability enum 驗證。S4 明確處理 `default`、`acceptEdits`、`plan`、`dontAsk`、`bypassPermissions`、`bypassPlan`、`auto`；仍需 CLI 支援。`bypassPlan` 映到 SDK `plan`，但 BAT callback 可自動放行，不能當只讀。 |
| Claude bypass | S3 只有 mode 為 `bypassPermissions` 時填 `allowDangerouslySkipPermissions=true`。 | 不能靠 start 傳同名任意欄位來獨立控制；是 permissionMode 衍生值。 |
| Claude SDK sandbox／工具 allow／deny／額外目錄／env | S3 手工建立 queryOptions，沒有從 s.options 複製 `sandbox`、`allowedTools`、`disallowedTools`、`additionalDirectories` 或任意 `env`。 | 透過 BAT start／permission options 不可達。不可因 Agent SDK 有此 API 就在 Connector 宣稱已設定。 |
| Claude 使用者與專案設定 | S3 固定 `settingSources=['user','project','local']`，settings 另由 BAT 組成。 | 主機現有 Claude settings 可能帶 sandbox／既有 allow rules；start 無法覆寫或隔離它們。只能另以主機證據記錄，不能以未轉送的 JSON 當證據。 |
| Claude `claude:set-permission-mode` | S2 → live query `setPermissionMode`；失敗會關閉 query，下一 send 重建。 | 可達，不能強制切換正在跑的 turn。限制 mode 變更必須留下意圖、讀回與結果。 |
| Claude resume／client-resume | S2 cold resume 先用已記 mode，無已記值時歷史 fallback 是 `bypassPermissions`；明確 options.permissionMode 可以覆蓋。已有 live session 的 client-resume 主要接回歷史，不重新套 options。 | 恢復必須明確帶原 mode 並讀回；不能省略欄位、只核對 cwd 或用 client-resume 成功證明 mode 生效。PROTOCOL 的 v3.2.12 note 不消除這項固定 source 風險。 |
| Codex `options.codexSandboxMode` | S5 normalize → thread start／resume 的 `sandbox`，turn start 的 tagged `sandboxPolicy`。 | 只支援 `read-only`、`workspace-write`、`danger-full-access`；其他值退回 `workspace-write`，不能拿無錯誤當 option 被採用。 |
| Codex `options.codexApprovalPolicy` | S5 normalize → thread／turn 的 `approvalPolicy`。 | `untrusted`、`on-request`、`never`；其他值退回 `on-request`。不能假設 `on-failure` 可用。`never` 是不詢問，不是隔離；配 `danger-full-access` 仍可任意寫。 |
| Codex writable roots | S5 `app_server_sandbox_policy` 的 workspaceWrite 只有 `type`；thread builders 只傳 mode。 | 不轉送 `writableRoots`／`writable_roots`，也沒有 setter；不能從 Connector 把整份 `managed_roots`、Git common dir、cache 加到 sandbox。cwd／CLI 預設或主機 config 的實際 roots 必須另外驗證。 |
| Codex network | 同上；沒有 `networkAccess`／`network_access` 欄位或 setter。 | 不可由本介面選擇開／關網路。workspace-write 的網路與 temp roots 行為依安裝的 CLI／host 設定，不在本包猜預設。 |
| Codex 兩個 permission setters | S1 → S5 `set_sandbox_mode`／`set_approval_policy`＋`reconfigure_session` 的 thread/resume；turn/start 也送當前 policy。 | 可達，但兩次呼叫不是原子切換，對正在跑的 turn 不保證立即生效。需等閒置並逐步 reconcile。 |
| Codex client-resume | S1 → S5 `client_resume`；既有 runtime 的 thread 以 host 設定為準。 | 不是以舊 tab options 強制重設 live session 的方法；無法拿 client-resume 成功當 permission change 成功。 |

不新增 BAT channels。主機版本與可用 agent presets 從既有只讀通道讀取；新／未知版本的 reachability 記 `unknown`，不照表自動補出保證。BAT meta 回傳 mode 表示設定值，不單獨證明 OS sandbox 已在該平台生效。

## Level 與證據

`provenance`、`api_access`、`isolation` 保持原定義。新增 `confinement`，不把其 level 用來授予 Connector 寫權。`write_scope: confined` 保留為禁止自動放寬的政策旗標，也不等於某個 level 已成功啟用。

| `level` | 足夠的證據與阻擋範圍 | 不阻擋／保證界線 |
|---|---|---|
| `host_account` | 操作者宣告 BAT 的受限帳號與被保護 roots；同身分的只讀權限查核通過，已觀測 BAT／agent 身分吻合。保護的是這些 roots 的檔案改寫、建立、刪除與替換。 | 逐次批准不會提升 Unix 權限。但未列 roots、其他可寫 repos、讀取與網路不受保護；sudo／特權 helper、其他帳號的服務可繞過，查核限制須明列。不是整台主機已不可越界的證明。 |
| `os_sandbox` | 明確傳送、讀回 CLI sandbox options。此包最多 options_confirmed，gap=sandbox_enforcement_unverified；實機 verified 留給 W12。Codex `workspace-write` 或 reviewer `read-only` 是候選機制。 | 對 sandbox 內的 shell／檔案工具生效；on-request 的越界操作若被批准，可能離開 sandbox。實際 writable roots、暫存／cache、Git metadata、網路另列；不保護授權範圍內其他 repos。 |
| `prompt_gated` | 確認 Claude `default`／`plan` 的 permission 路徑；未被 CLI 既有規則預先 allow 的工具會詢問。 | 無 OS 寫入邊界。shell 可一次涵蓋多個路徑；allow／dontAskAgain、pre-approved rules、MCP／外部工具都會擴大範圍。不能說「所有目錄外寫入一定詢問」或完整達成 §06。 |
| `none` | 無足夠證據、bypass／full-access，或只有 cwd／prompt／部分工具詢問（例如現有 Claude acceptEdits）。 | 沒有可宣稱的執行寫入保護；Connector 的人工資源 API 唯讀政策仍存在。 |

Level 不是全序：帳號限制可能只保護指定 roots，sandbox 可能另准許 temp roots。保留所有 `mechanisms`；通過查核的 host_account 優先顯示，其次 os_sandbox、prompt_gated、none。不能用字串大小比較 successor 強弱；比對受保護 roots、批准途徑與已記 options。

Session 物件（新增；下例 options 已核對但主機還沒做 sandbox 實機驗收）：

```json
{
  "write_scope": "confined",
  "confinement": {
    "schema_version": 1,
    "level": "os_sandbox",
    "requested_level": "os_sandbox",
    "mechanisms": ["codex_workspace_write"],
    "options": {"codexSandboxMode": "workspace-write", "codexApprovalPolicy": "on-request"},
    "evidence": {
      "source": "start_intent_and_bat_meta",
      "bat_source_commit": "b7419892fbc9946799b64cca24c2ec8c7fa15c42",
      "observed_options": {"codexSandboxMode": "workspace-write", "codexApprovalPolicy": "on-request"},
      "host_check": null,
      "checked_at": null
    },
    "verification": {"status": "options_confirmed", "reason": "sandbox_enforcement_unverified"},
    "limits": ["individual_approval_can_escape", "writable_roots_not_configurable", "network_not_configurable"],
    "gap": "sandbox_enforcement_unverified"
  }
}
```

`verification.status` 為 `pending`、`options_confirmed`、`verified`、`unknown` 或 `mismatch`。`level` 是建立時已具證據的限制；`requested_level` 是本次要求。只有 cwd、reserve 或 ACK 不填 `verified`。實機證據包含 run／log digest、檢查時間、BAT server version、CLI version、OS、runtime 身分與 policy；從既有 journal 的 evidence／operation refs 留存，不設第二個資料庫。W12 的證據須能連到同 host 與相同 runtime 設定。新的主機／CLI／BAT 版本不能沿用舊驗收通過狀態。

保留建立快照與最新只讀 `current_verification` 分開顯示。現在的 meta 與已記 options 不同、帳號 check 失效或證據過期，顯示 mismatch／unknown，不改寫歷史 level，也不能以舊 level 繼續宣稱保護仍有效。未驗收的 sandbox level 可為 os_sandbox，但只能 options_confirmed，呈現缺口，不列為 A10 通過。

## 每個入口的新預設（審查決議）

先做既有資源／目的地政策，再選 agent 可達且不改變任務的限制。正常 start 決策在第一個有副作用的 step 前保存；BAT start 後核對 meta，通過後才送第一個工作 prompt。對支持的同版本 host，優先沿用已查核的 host_account；一般 Codex 另保持 workspace-write，兩種機制可共存。

| 入口 | 新 start options／限制政策 | 相容性與缺口 |
|---|---|---|
| 一般 `session_start`、relay missing、fanout children | default／allow_all 保持今日選項；host policy=confined 才套 Claude default 或已查核帳號的 acceptEdits、Codex workspace-write/on-request，write_scope=confined。 | allow_all 記 none。一般 installs／localhost tests 可能需網路；不偷偷改成 Codex sandbox。confined 受限 options 不被 explicit bypass 覆蓋；explicit plan／dontAsk 保留更強限制，不改成 default／acceptEdits。 |
| checkpoint／repair | 固定受限 Claude default（帳號已查核才 acceptEdits）／Codex workspace-write/on-request；使用已記來源與 managed clone／integration area。 | 無已查核帳號用 Claude default，有帳號才 acceptEdits；Git 測試、commit 與 shell 仍可透過逐次詢問執行。不可失敗後退回人工 cwd。 |
| fanout planner | Codex `read-only`＋`never`，記 options 與受限旗標。 | planner 的既有工作只讀；不需要 commit、安裝或測試。仍不宣稱新 planner／模型政策。若實機不支援，顯示不支援，不退回可寫 allow-all planner。 |
| failover／archive successor | 新 session 用上述預設，並繼承 predecessor 的禁止 raise、受保護 roots 與證據關係。 | 受限 predecessor 不依 host allow-all 放寬；非受限 predecessor 保持 host 政策。對 predecessor 為 read-only／never，successor 保持該限制；對已有 host_account，不丟 account 邊界。無法維持時 blocked，保留前任。新 session 可有更強證據，但不能重標前任。 |
| Task Service 新 lead／external worktree | **保留現有 engine／recipe 的權限行為**。default 不新增送出欄位；start 後由 BAT meta 保存實際 workspace-write/on-request 或 Claude default，不新增 sandbox roots／網路政策。 | allow-all 任務如無已查核的 host_account，level=none、`gap=task_recipe_compatibility`；不冒稱 confined，也不強制改成 sandbox/never 令測試失敗。Task Service 保持其 default／allow_all 行為；新增 confined 設定在 task 內仍沿用原 default 行為並記 task_recipe_compatibility，不另改 engine／recipe。 |
| Task Service reviewer 相容分支 | 保存現有 Codex read-only/never 或 Claude plan，不改 recipe 是否會使用 reviewer。 | reserve、read-back 與 tab 都保存實際 options；未讀到 permission fields 記 unknown，不增加 Task Service engine 的阻擋。 |
| warm reuse、resume、rehydrate、start recovery | 保持該 session 原已記 options／level，不套新 start 預設。 | 恢復不得抹去 write_scope；缺證據顯示 unknown／none，不能按今日 host default 或 cwd 升級。 |

Codex `never` 不普遍取代 `on-request`：它可能禁止測試需要的外部 cache、Git common dir 或依賴網路，而 BAT 不能指定這些 writable roots／network settings。`read-only` 不能完成 coding／repair。先用可工作的最強 sandbox mode，保留有界的逐次詢問；禁止的是 mode raise 與自動批准，不能聲稱批准後仍完全隔離。Task Service 的自動測試 runner（`task_verifier.py:ObservedVerifier`，含安裝依賴）不是 session 的 sandbox subprocess；只能用其既有 cwd／SSH 政策與已查核帳號解釋限制，不拿 session level 替它背書。

Task Service 若不能在既有 engine／recipe 下保持測試，這一包記錄 gap，README／Dashboard 明示尚未達成該任務的 A10。後續 engine／recipe 調整由另一工作包決定；不偷偷改模型、路由、測試命令、cache／HOME、安裝位置或選另一個 host。

Task Service 的 metadata 未包含權限欄位時只記 unknown，不改原 start／recovery／非 confined failover 行為；confined 的 resume／send 仍由共用 service guard 保持其原限制。Task Service 的新列保存既有 options／level；原 task 用 allow_all 就保持 none＋task_recipe_compatibility，不用改 policy 來讓測試過關。新 host confined 不悄悄改 task engine 的原行為；此例外只在既有 task-owned write point 內記證據，不新增外部 bypass 參數。

## Host account 宣告與只讀查核

`HostConfig.confinement` 預設不宣告 account。以下都是 placeholder：

```toml
[hosts.buildbox]
managed_roots = ["/srv/batc-managed"]

[hosts.buildbox.confinement]
host_account = true
expected_uid = 2001
bat_account = "example-bat"
check_ssh_alias = "example-auditor" # 操作者 設好的另一帳號，不是 BAT 登入 alias
check_uid = 2002 # auditor UID；可信 root alias 可用 0
protected_roots = ["/srv/example-personal"]
check_max_age_s = 300
check_timeout_s = 10
check_max_entries = 50000
bat_port = 9876 # BAT listener port，非 SSH tunnel 本機 port
```

Git operations 仍沿用 `BATC_TASK_SETTINGS` 的 `[verification].ssh_hosts`，帳號查核另用 check_ssh_alias；不保存新 credentials，只使用 SSH config 已有的 alias。host_account=true 須有正整數 expected_uid、非空絕對 protected_roots；禁止 roots 與 managed_roots 重疊、禁止 BAT root UID。宣告可信 alias 時，須同時填 bat_account 與不同的非負 check_uid。Alias 為非 option 的簡單 SSH 名稱，account 為不含 shell 字元的 Unix 名稱；遠端 passwd 核對名稱／UID，不取 HOME。未宣告可信 alias 的舊設定仍可載入，但不能 verified。check_max_age_s（最多 3600）、check_timeout_s（最多 30）、check_max_entries（最多 100000）及 bat_port（1–65535）均為正整數，預設 300／10／50000／9876。新的 start 在副作用前重新查核，cache 不超過期限。

Linux 查核必須從 操作者 宣告的可信 alias 登入另一個 auditor（建議專用帳號，或 root），不得登入 BAT 帳號。Auditor 程式核對自身 real／effective UID 等於 check_uid、與 BAT 不同，且 passwd 的 bat_account UID 等於 expected_uid；再證明 bootstrap 與 auditor 登入入口不受 BAT UID／groups 控制。**未宣告可信 alias 時直接 unknown／check_channel_untrusted，不跑 in-band check**：startup output 可以偽造，即使回 verified 也不能用。此 reason 為 ACCOUNT_HARDENING_GAPS，start_effect=fallback_default；受限 Claude 用 plain default、絕不 acceptEdits。GET 同樣立即顯示此 gap，沒有 SSH I/O。

使用獨立 `SshGitRunner.run_account_check(..., ssh_alias=check_ssh_alias)`，絕不 fallback 到 Git／BAT alias；Git runner 原本的 sh -lc 不改。SSH 固定命令從 `/` 以 env -i／timeout 執行 shell closure gate；完整 proof 通過才 exec `/usr/bin/python3 -I -S -B -c <program> <JSON payload> <proof>` 作 auditor supervisor。Bootstrap 的 Python realpath、closure trees、sudo、env、gate tools、sshd、auditor login shell 與全部父目錄須為 root-owned、無非 root 可寫 mode；對 bootstrap／auditor 入口的 POSIX ACL 保守拒絕，不能以 auditor 自己的 access 猜 BAT 的 ACL。讀 xattr 不完整也拒絕。Auditor 的 home、startup、.pam_environment、.ssh／rc／environment／authorized_keys 及祖先須非 BAT 所有、mode 對 BAT UID／groups 不可寫，不接受 symlink；缺檔仍須查 parent。Channel proof failure 為 unknown／check_channel_untrusted，細節留 channel_reason／paths；bootstrap 不可信為 check_executable_untrusted。舊同帳號 checker 的 cache signature 失效。

通過 supervisor 前置條件後，先以同一 closure gate 再核對實際 interpreter chain，才以固定 direct exec argv 送程式：`/usr/bin/sudo -n -u example-bat -- /usr/bin/env -i PATH=/usr/bin:/bin LC_ALL=C /usr/bin/python3 -I -S -B -c <program>`。絕不 su -／sudo -i／sudo -s，不啟動 BAT 的 shell、startup 或 SSH hooks。第一階段只以 BAT UID 跑 integrity preflight，GNU find -uid／-writable 覆查兩個 passwd-derived 登入環境（含 auditor .ssh 全部 entries）的 owner／effective IDs／ACL；auditor proof 未通過便不跑 protected-root scan。第二階段才跑原 process／root scan，仍在 BAT UID，並重核 integrity。兩階段共用 entry 剩餘量與 auditor deadline。Python 不載入 user site、cwd import 或 PYTHON* 設定，不寫 bytecode；find 固定 `/usr/bin/find`。不新增 interpreter／find keys。

操作者 須在 host 自行配置並驗證以下 **限定此 argv／目標帳號的 NOPASSWD sudoers rule**（均為 placeholder，PATH 的 colon 按 sudoers 語法 escape）：

```sudoers
example-auditor ALL=(example-bat) NOPASSWD: /usr/bin/env -i PATH=/usr/bin\:/bin LC_ALL=C /usr/bin/python3 -I -S -B -c *
```

此 rule 的固定 executable／options 與 `-c *` 允許 auditor 在指定 BAT 身分執行任意 Python payload（原 stdin rule 也有同樣能力），不允許 root command、其他目標帳號或 login shell flags；auditor credential 不能交給 BAT。即使 alias 是 root，本版仍使用同一 sudo direct exec，不使用 runuser／setpriv。結果含 checked_uid、channel.status／method=sudo_exec／ssh_alias／auditor_uid／bat_uid／bat_account、closure proof、ptrace_scope 與 preflight evidence。Connector 只有在所有 identity／channel facts 符合宣告時才接受 verified；缺欄位／不相符回 unknown／check_channel_untrusted。Cache 的 verified 同樣須符合 channel facts，GET 不跑 live check。

### Interpreter closure gate 與信任基底

Closure layout 只有一份定義（confinement.CLOSURE_VERSIONS／CLOSURE_TREE_PREFIXES 與 _CLOSURE_SHELL）。固定支援 `/usr/bin/python3` 解析到 `/usr/bin/python3.6` 至 `python3.14`，檢查存在的 `/usr/lib/python3.X`／`/usr/lib64/python3.X`，至少一個 tree 必須存在。未知 prefix、venv 或 layout 不猜測，不執行待證的 Python 來查 sysconfig。兩個 gate 都從原 `/usr/bin/python3` 用 target helper 逐 hop 查 owner／mode／父目錄，再查 resolved binary；不能只查第一個 link 與最後 binary，漏掉可替換的中間 link／parent。

整個 stdlib／platform-stdlib tree 的每個 directory、source、encodings、__pycache__ 的 pyc、lib-dynload 的 extension 與 _sysconfigdata* 都須 root-owned 且沒有 0o022。Symlink 本身須 root-owned，mode 777 不表示可改 link；每一 hop、target 及全部父目錄另查，directory target 也遞迴 scan。拒絕 special files、newline path、symlink ancestor、broken link、不可讀 entry 或列舉 error。既存 pythonXY.zip 檢查檔案／targets 與 parents；不存在也證明 parents 不能建立新檔。Interpreter 旁及上一層 pyvenv.cfg 必須不存在，parents 已保護；現有 venv config 為未知 layout。

`-I -S` 不能關閉 CPython 的 `._pth` import-path override（[CPython path initialization](https://docs.python.org/3/library/sys_path_init.html#pth-files)）。Gate 在啟動前拒絕 `/usr/bin/python3._pth`、resolved interpreter 的 `._pth`，以及 `/usr/bin/pybuilddir.txt`／`/usr/bin/Modules/Setup.local` build markers。共用 library 的 `._pth` 優先於 executable：`CLOSURE_NATIVE_PREFIXES` 固定列 `/usr/lib`、`/usr/lib64`、`/usr/local/lib`、`/lib`、`/lib64`；核對 directories 的 symlink chain，檢查本層與一層 multiarch 子目錄的對應 `libpython3.X*.so*`，逐 hop 查 library／real target 並拒絕兩處的 `._pth`。即使 override 為 root-owned 也拒絕，因為其內容能選到未證明的 tree。這些檢查同樣跑在 auditor 與 BAT interpreter 前；cache signature 已升版。

支援範圍是使用上述 stdlib／native loader paths 的發行版系統 Python。操作者須從可信套件來源確認 interpreter 與 loader 配置；自訂編譯 prefix、直接連結其他 library 路徑、非標準 loader 配置不在此 proof 的支援範圍。Metadata gate 不會也不能從 filename 自證任意 executable 的編譯時搜尋路徑；既有 system-bootstrap 信任基底仍適用，不宣稱支援未知安裝。

Gate 只用絕對 non-interpreter tools 與 shell builtins，在 Python startup（含 encodings）前完成。GNU find 首個違規即 quit；stdout 是有限的 entry markers／link paths，完整 DONE marker 才證明列舉完成，截斷、錯誤或未知 marker 都不算 proof。失敗或 timeout 固定回單一 JSON unknown／check_executable_untrusted；auditor interpreter 不啟動。Python 內與每次 BAT direct exec 前均重跑同一 gate，共用 entry 剩餘量與總 timeout；BAT 程式另作 defense-in-depth check。Gate／in-program 不各維護 tree 清單。新版 cache signature 不沿用舊 directory-only verified。

Linux POSIX ACL 的 group mode bits 是 ACL mask；named user／group 的 write grant 不能超過 mask。Root ownership（非 root 不能 chmod）加沒有 group／other write bits，足以排除 BAT 的 ACL write grant。Closure gate 採此論證；原 bootstrap／auditor xattr 查核保留保守拒絕。Entry budget 預設由 10000 提高為 50000（上限 100000），包含 gate、重查、登入／root scans；time 預設仍 10 秒（上限 30）。本地唯讀測量 Debian 系統 Python 3.14 tree 有 1461 entries、1343 files（640 pyc、50 lib-dynload entries）；此數只用於 budget sizing，不當真 host 隔離證據。較大的安裝或較慢 host 用完任一 budget 就 unknown，不省略 tree 來取得 verified。

**先執行的信任基底：** kernel、sshd、auditor login shell、dynamic loader／system shared libraries、gate tools 必須由操作者維持可信。Kernel 的執行狀態不能用程式自證；sshd／shell／tools 與 Python 的 `/proc/self/maps` native paths（含 trees 外的 loader／shared libraries）在程式中覆查 root ownership、mode／parents，保存 trusted_paths。這只能偵測配置錯誤，不能消除其已執行內容或既有入侵。`-B` 不阻止讀 pyc；安全來自執行前完整 closure proof，不宣稱 Python flags 能代替它。

**同 UID 干擾限制（pending-note 8）：** verdict 假設查核期間沒有 hostile BAT-UID process。Same UID 可從 `/proc/<pid>/fd` 干擾 pipes；Yama ptrace_scope=0 更允許 ptrace。Auditor 記錄 ptrace_scope（不可讀為 null），不把它當全域隔離證據。兩階段 program 改走 `-c` argv、stdin=DEVNULL，exec 後不能透過 stdin 追加程式，但 stdout／ptrace 仍是信任限制。Channel／UID facts、stdout 僅一個 JSON document 與 exit code=0 都必須成立，非零或多文件永不 verified；SSH login 信任與同 UID 假設仍不能靠此 parser 證明。

原 integrity scan 作 defense in depth：Python realpath、stdlib、find、passwd login shell 與父目錄須 root-owned 且 BAT 不可寫，GNU find -writable 核對 ACL，不可信為 check_executable_untrusted。Home／shell 只取 passwd；支援 sh／dash（.profile）、bash（.bashrc、.bash_profile、.bash_login、.profile）、zsh（.zshenv、.zprofile、.zshrc、.zlogin），其他 shell 回 login_shell_unsupported。Home、.ssh 全部 entries、.pam_environment 與 startup 不得由 BAT UID 擁有或可寫；owner 可 chmod，缺檔只在 parent 不可寫時安全。Home ancestors 按 sticky-bit 規則，symlink 保守拒絕，失敗回 login_environment_writable。Auditor 的相同檢查失敗則回 check_channel_untrusted。不掃 .claude／.codex／.cache state 內容。

**信任模型與 hardening：** check_ssh_alias 是 操作者 在 BAT 帳號之外建立並確認的信任宣告，不是從該帳號回覆推導。sshd 仍透過 auditor login shell -c，可能先執行 auditor .ssh hooks／authorized_keys command=／PAM environment；這些入口及 SSH config／credential 必須先在 BAT 權限之外保護。Supervisor checks 是對邊界的覆查，不能從已遭登入劫持的通道補證。操作者 須從可信副本替換 auditor／BAT 的 startup、SSH／PAM hooks 與系統 executables；不能保留歷史植入內容後再 harden。Auditor home／SSH 入口必須不受 BAT UID／groups／ACL／可替換 ancestor 控制。原 BAT home／startup 加固仍是 defense in depth，單靠 in-band checks 絕不能 verified。BAT home／startup／.ssh 使用 root-owned、BAT 不可寫配置；可建立 BAT 擁有的 .claude、.codex、.cache state 子目錄。本包不代建 account、credentials、sudoers 或 hardening。

固定 BAT 的 [src-tauri/src/sidecar.rs:4](https://github.com/TED-H-com/better-agent-terminal/blob/b7419892fbc9946799b64cca24c2ec8c7fa15c42/src-tauri/src/sidecar.rs#L4) re-export bridge；實際 [SidecarState](https://github.com/TED-H-com/better-agent-terminal/blob/b7419892fbc9946799b64cca24c2ec8c7fa15c42/src-tauri/crates/bat-agent-bridge/src/lib.rs#L291) 的 call 在 371–390 行進入 ensure_spawned，344 行才 spawn，不以尚無 lazy sidecar 拒絕首個 start。可唯一觀測 BAT process 與所有 runtime descendants 的 UID／GID／supplementary groups 才能驗證；尚無 runtime 的 idle host 可通過，保存 runtimes=[]／runtime_identity_inherited_unobserved，表示未來子程序身分尚未觀測。

Protected-root scan 維持只讀 metadata、不讀人的檔案內容、不建立 probe、不改 account／檔案設定。sudo 僅按既定 rule 切到 BAT UID，不用來提升 BAT 權限或測提權能力。GNU `find <root> -xdev -writable` 的 access(2) 涵蓋 ACL；首個 writable／BAT-owned entry 即 mismatch（owner 可 chmod）。Cross-mount／symlink、列舉錯誤、entry／time budget 用盡回 unknown。Root ancestors 以 BAT UID 查 writable／search，依 sticky-bit／owner 判斷可否替換 child。不重實作 root 的 mode／ACL evaluator。讀 `/proc/<pid>/status` 的 Uid、Gid、Groups、CapEff／CapPrm／CapAmb；capabilities 任一非零為 mismatch，須唯一對應 BAT server 與已觀測 runtime。與 direct-exec BAT 身分／groups 不符、process 不可識別為 unknown。檢查前後 canonical path／inode／process 身分須一致。

此查核只證明目前指定 roots／觀測身分，仍不能完整證明未列出私人資料夾、未來 ACL、所有 hardlink／遠端服務／setuid／sudo／credential 路徑、之後子程序不換身分。Limits 明示；已知繞過不能 verified。操作者 須維持可信通道與帳號邊界，不能把其信任宣告說成程序自證。無宣告 account 時跳過 check，選 CLI 限制。四種 hardening gaps（check_channel_untrusted、check_executable_untrusted、login_environment_writable、login_shell_unsupported）為 fallback_default，不把 declared roots 記成已保護；其他 mismatch／unknown（root、runtime、budget／I/O 等）仍拒絕新 start。Admission 曾 verified、frame check 失去 verified 時維持 unsent refusal，不能送已準備的 acceptEdits。一般 default／allow_all 與 Task recipe 不變。非 Linux 仍 unsupported。

## 輸入／輸出與前置條件

不新增提升權限 API 或 raw SDK option 入口。Start 的 agent／workspace／instructions 沿用現有 action contracts；confinement 是後端從可信 host settings、任務身分、來源與 BAT 讀回產生的結果。呼叫者不得傳 `level`、`verified=true` 或解除 `write_scope`。

| 表面 | 新增輸出／既有入口 |
|---|---|
| Session reads | `service.sessions_list`／`session_read`、triage、`inventory`、`GET /api/v1/sessions` 與 `GET /api/v1/sessions/{host}/{id}` 的 session 列帶 write_scope、confinement、current_verification；MCP `sessions_list`／`session_read` 與 CLI `sessions`／`read` 同值。manual／unknown 不因加欄位取得寫權。 |
| `GET /api/v1/capabilities` | `hosts[].confinement`：`agents.{claude,codex}` 的 reachable_options、requested_level、verified_level、limits／gap；`host_account` 的 declared、status、checked_at、protected_roots、evidence_ref、reason、start_effect、checked_uid／channel facts。supported／unknown／unsupported 分開；不把 host 的 capability level 直接套到既有 session。 |
| Host reads | 既有 `hosts_list`／`host_status`、`GET /api/v1/hosts` 帶同一 account check 摘要。只讀刷新不新增 mutation action；離線回最後證據與 stale，不因 capabilities 載入卡住 Dashboard。 |
| Checkpoint／repair | `checkpoint-preview` 與 capabilities 提供所選 agent 的預計限制；`checkpoint.continue`／`integration.handoff` 的結果與 operation.external_refs 帶 confinement snapshot／evidence_ref。work item 的 `continueFrom` 也使用此摘要。 |
| 權限與 bulk | [session.permissions](session-permissions.md) 與 MCP `session_set_permissions`／CLI `permissions` 共用中央 operate action、逐 frame 回執與 confinement。公開 `approve_pending`／`approve-pending` 使用 [session.approve_pending](bulk-approval.md) 的固定預覽與 children；confined sessions 的 persistent approval／raises 仍拒絕。舊 lifecycle raise 路徑不啟用。 |
| Task Service | commands start payload 與 registry 保存 actual options 與 snapshot；`work_status`／task reads 顯示 session 限制與 task_recipe_compatibility gap，不改 engine_decision／recipe。 |

必要條件仍為 scopes、write／orchestrate tier、resource_policy 的來源與目的地檢查、穩定 session／worktree 身分。CLI sandbox 不把 legacy shared clone 變成 §07 的獨立 clone；本包不變更 shared_clone_worktrees 遷移政策，UI 仍同時列 isolation 與 confinement。

### 啟動提示與帳號查核結果

`confinement.account_start_effect(result)` 是帳號查核的共用規則。Capabilities 的 `hosts[].confinement.host_account.start_effect`、MCP capabilities／host reads 與 CLI host reads 用同一 projection；reason 原樣保留。GET 只讀 cache／journal 與期限，不跑 SSH 或 live check，不改 session 的 creation evidence。`start_account()` 啟動時先跑 live check，再用同一函式決定是否拒絕；live check 須把 recheck 定案為 verified、fallback_default 或 refused，未定案也不允許 start。

| start_effect | 條件 | 啟動提示／行為 |
|---|---|---|
| `verified` | 已宣告且 status=verified | 沿用已查核帳號提示。新的受限 Claude 可用 acceptEdits，啟動前仍重新查核。只涵蓋宣告 roots，不升級既有 session。 |
| `recheck` | 已宣告、unknown／unchecked_or_stale；沒有 fresh cache 或超過 check_max_age_s | 不顯示 blocked。啟動時重新查核；通過可用 acceptEdits，支援的環境加固缺口用 plain default，其他查核失敗拒絕。 |
| `fallback_default` | unknown 且 reason 在 ACCOUNT_HARDENING_GAPS；或帳號未宣告 | 已宣告者顯示 reason、Claude 使用 default 且不啟用 acceptEdits，再附既有批准規則／shell／無 OS 寫入隔離的 caveats。未宣告者跳過帳號 check，沿用一般 Claude 提示；此 value 描述受限 Claude 的帳號 fallback，不改一般 default／allow_all 或 Task recipe policy。 |
| `refused` | mismatch，或其他 unknown／無有效結果 | 顯示既有拒絕提示與 reason；HOST_ACCOUNT_UNVERIFIED 阻止所有 agent 的新 start。 |

Dashboard 的 checkpoint／repair／work-item 接續表單依 start_effect 選文字，不在 JavaScript 複製 hardening reason 清單，不把所有非 verified status 都當成 blocked。Codex 一律保留 workspace-write／on-request、network／writable roots 及 live verification 的原提示，不依賴 acceptEdits；effect=refused 時先顯示拒絕與 reason，再列 Codex 提示。切換 agent／host 時重算文字，en 與 zh-TW 同義。

## 不可放寬與操作銜接

新共用 confinement helper 只決定 options、證據與是否放寬；沿用原 `WriteGrant` 與 task coordinator gate。一般新 start 的選項在 registry reservation 與 Task Service command payload 記下，不等 ACK 才記。Checkpoint／repair step request 保存 session ID、cwd、agent、write_scope，完整選項以同 ID 的 registry intent 為準；核對成功後在 operation external_refs 保存 snapshot。`registry_permission_fields()` 由實際送出的 options 生成，不能用空 `permission_options(default)` 清掉有效值。

- `write_scope=confined` 不可由 permission change 進 bypass／full-access、減少 protected_roots、開啟更寬的批准政策或移除其已記限制。`force`／confirm 不繞過；等價 default 要轉成該 session 原 options，只有真正不放寬的變更可送。
- 舊內部 combined bulk raise apply 在回答任何 prompt 前回 LEGACY_PERMISSION_RAISE_DISABLED，dry-run 保留。公開 reviewed bulk 必須有原 preview／selection，各 answer／permission child 在每個未送 frame 前重查 confinement、registry 與 task gate。歷史 pending raise 保留為診斷 evidence，deferred helper 回相同拒絕碼，不清除後重新派送。
- 逐次回答仍是 `session.answer`；既有任意批准就可能越過 prompt／sandbox，UI 必須說明。Confined session 拒絕 `dont_ask_again=true` 的 session-wide allow 與會把 mode 改成較寬值的 ExitPlanMode answer；`deny`／AskUserQuestion 不因此被擋。單次 allow 不得更新 level 為「更安全」，並留下批准的限制說明。
- 兩份 skill 同步要求先讀 write_scope／confinement：受限 session 不要求 allow-all、不呼叫 raise、不用 bulk／dontAskAgain 消除限制，不批准寫往 protected_roots。合法測試被擋時，回報具體限制與待處理項，不自行改 host／engine／recipe。
- 權限閘門不能把不受限的舊 Task Service session 接管給低階 agent；操作權限與 confinement 是兩個累加檢查。

**與 operations-unification 相接處**：該包負責 ActionDef、HTTP／MCP／CLI adapters、scope／task authority gate、冪等與每個 BAT channel 的 step／reconcile。本包只在 `session_start`、`session_failover`、`session_set_permissions`、`approve_pending`／`_raise_deferred`、resume 與 BatAdapter start/recovery 的實際 frame 前插入 confinement 決策／核對，供它的 actions 共用。不得重寫上述函式的操作流程或再加第二個 Task Service gate。其 `session.permissions`／bulk 的任務 gate 和本包的不可 raise 必須兩者通過；任一拒絕，零 permission frame。

**與 cleanup／delivery 相接處**：cleanup 讀 registry／operation.external_refs 時保留 confinement snapshot／evidence refs，不藉 remove／rehydrate 重標 level。delivery 的 repair 仍經 `checkpoints.start_in_worktree` 取本包預設；`integration.py:_run_handoff` 只補結果 projection，不重構 delivery steps、PR／deploy policy。

## 實際副作用、失敗與升級

Phase 2 新增的副作用只有 Connector 記錄／projection、既有 managed start options 與經允許的既有 permission channels。Account check、capabilities 與來源 preview 是只讀。絕不代為建立帳號、改 ACL、改 BAT／CLI 全域設定、人的 checkout 或 deployment recipe。

| 情況／錯誤碼 | 行為與恢復 |
|---|---|
| `CONFINEMENT_RAISE_REFUSED` | 不送任何 permission frame；含 force／bulk／deferred／failover。回傳原 level、禁止放寬原因與 evidence_ref。 |
| `CONFINEMENT_UNSUPPORTED` | 所需限制在 host／agent 不可達；planner 或需保留前任限制的 successor 不啟動。一般工作可用已定義的較低候選 options，但明示 gap，不能用未知欄位碰運氣。 |
| `check_channel_untrusted`／`check_executable_untrusted`／`login_environment_writable`／`login_shell_unsupported` | Host evidence reason（非 operation error code）：unknown，列不可信 paths／shell。check_executable_untrusted 也含 closure 的未知 layout、entry／time budget 用盡、不可讀／不完整 scan；pre-interpreter gate 失敗只回固定 reason，不啟動 interpreter。confined Claude 以 plain default 啟動，creation level 為 prompt_gated。無 verified account 絕不送 acceptEdits。 |
| `HOST_ACCOUNT_UNVERIFIED` | 宣告與 roots／process 查核不一致或無法完成，或 frame check 失去 admission 的 verified；新 start blocked。來源與既有工作不動；明確標記 sent=false，釋放 reservation，按 caller 的 retain_on_error 決定是否沿用原 rollback。Task start command 記 rejected、task 到 needs_ted 並記 code，不 retry／read-back。Checkpoint／repair 到 NeedsAttention，同 operation 在設定修正後重跑未送步驟。 |
| `CONFINEMENT_MISMATCH` | start 讀回／resume／permission reconcile 的 options 與意圖不同。停止新 prompt 派送，needs_attention；已 ACK 的 session 一律保留 reservation 與 worktree、記 uncertain 與 code。Start 初次讀回或 recovery 觀察到 mismatch 時保存原 creation record；其 verification=mismatch 為 terminal，下次在讀 BAT 前就拒絕，confirm 不覆寫；不能因稍後 options 相符而升級或送 handoff。非 confined 的未知 metadata 只記 unknown 並繼續；confined 的 unknown 仍拒絕。Reviewer 的未知 read 仍 best-effort；可讀 mismatch 則不得 promote／送 review prompt，command 與 task 按既有 acknowledged-but-unusable start 規則到 uncertain，原因記在 reservation 的 code／creation verification。不自動修改 live runtime 來掩飾。 |
| `FAILOVER_SUCCESSOR_MISMATCH`／`START_SESSION_MISMATCH` | 讀回的 cwd 與 reservation 不同。用 resource_policy.norm 的絕對 POSIX 路徑正規化，先比 cwd 再比 permission options；不以 Connector 本機 realpath 解遠端 symlink。記 terminal error_code、保留原 reservation／worktree，不 promote、不送 prompt／handoff、不重新 start。後續即使 cwd 修正也不自動恢復。Failover 用前者，session_start、checkpoint continue／integration handoff 與 Task Service start recovery 用後者。 |
| `CONFINEMENT_START_UNSETTLED` | 前次 successor／reviewer start 未定，或 ACK 後尚未寫 active 就中斷，這次只讀回 reserved ID。Reviewer 已送 frame 的 read-back 重試仍為 exception／null／非 dict 時，reservation、start command 與 task 均保留 uncertain，記此 code，不釋放、不再 dispatch；下次 recover_start 只讀回。須先證明 meta.cwd 和 reserved cwd 相符；cwd 缺失／無效時不 promote、不送 handoff，保留 reservation。cwd 相符且 options_confirmed／verified 時清 start_uncertain、active 並完成 confirm；保留 reserve 時的 cwd／worktree_path／branch／permission fields，與正常 ACK 路徑一致。若 handoff 的未送證據仍在，重建 prompt、沿用 message ID 與所有 Task Service callbacks／frame guards，走原 send 路徑並回 prompt_sent／message_id／error，不回 skipped。mismatch 用上列 code；unreadable／null 保留 reservation，不重送 start，也不把「未定」說成 options mismatch。Checkpoint／repair 的可讀 metadata 缺 cwd 時到 needs_attention；null／transport unreadable 沿用 step 的 uncertain retry。 |
| `REGISTRY_DUPLICATE_SESSION` | sessions registry 中同 host／ID 有多列。Read／write 明確失敗，寫入不改舊檔；不自動挑 row、移除或再派送。操作者須依 BAT／creation evidence 修復。 |
| `START_IN_PROGRESS` | 相同 host／session ID 的 start claim 仍被另一個 process 或 coroutine 持有。sent=false，registry row／worktree／frame 都不改。另一程序正在 start；稍後讀回，不盲目重試。Checkpoint／repair 回 NeedsAttention，同 operation 等 owner 完成後恢復；Task Service 的新 start 按 unsent refusal 記 rejected／needs_ted，既有 recover_start／recover_failover 仍回 False／None 等待，不派送第二次。與 CONFINEMENT_START_UNSETTLED 不同：前者有 live owner，後者已可能送 frame，必須讀回。 |
| `CONFINEMENT_EVIDENCE_MISSING` | 已記 confined 的 session 在 meta=null 且遺失原 mode／policy、無可信 intent／回執可恢復時，不送 client-resume／cold resume；不可觸發 BAT 的 omission／bypass fallback。保留既有 session 與讀取，由操作者核對原證據；不把它重新 start 成新預設。loaded live session 的 send 不改 mode，不因 legacy 證據缺失而阻擋。另對升級時 in-flight checkpoint／repair 的 reservation 缺 confinement record，回 NeedsAttention，不無限讀回。 |
| Start reservation 後、transport 前取消／失敗 | `start_sent=false`、failed／釋放 reservation（failover 也撤回 predecessor 的 superseded）；CancelledError 原樣傳出，不 await rollback／SSH／BAT 新 I/O。下次同 reserved ID 安全重新 start，不輪詢不存在的 session；缺 flag 的 legacy row 不當成未送證據。 |
| Start frame 已交 transport 後取消 | 保留 reservation／worktree、start_sent=true、uncertain（failover 的 ACK 尚未到時為 starting＋start_uncertain）。只讀回 cwd／options，不再 start；不能把 CancelledError 誤列 definitive refusal。InvokeError 不證明 BAT 未建 session；保留 reservation／worktree，只讀回，不 rollback。 |
| Start／setter ACK 遺失 | uncertain；以同 session ID 讀 meta 和已記 intent。cwd 相同但 options 不明不能確認限制；不 start 第二次，不退回 allow-all。 |
| 首個 instruction 已 accepted，結果用的 evidence read timeout／disconnect | `start_in_worktree` 的 durable send step 保持 succeeded；回 registry 的原 creation evidence，current_verification=unknown、reason=readback_failed。Checkpoint continue／integration handoff operation 仍 succeeded，同 idempotency key 回同 operation；不重送 prompt、不重開 session。 |
| Codex 第二個 setter 失敗 | 保留各 step 的回執與不明狀態，逐項讀回，不重送已證明的 step；完成後及下一 turn 邊界再核對。沒有原子 sandbox＋approval 保證。 |
| Host offline／check stale | session read 回原快照＋current_verification=unknown；不偽裝 verified。不得用 stale account check 開新 session。 |
| BAT／CLI 更新或原生 GUI 改 mode | 原快照不動；current_verification unknown／mismatch，新的證據需重新查核／驗收。Connector 無法禁止 GUI，亦無法追回已被批准的 shell 寫入。 |

升級不遍歷 running sessions 送 setter、resume 或 stop。既有有完整 start intent／實際 options 的紀錄可以只讀回填證據；只有 cwd／tab／事後 host default 的紀錄填 none、reason=legacy_evidence_missing。現有 confined flag 保留，即使其實際 level 為 none；人工／unknown 資源不回填成 managed。之後補到較強 host 證據只更新 current_verification，不把建立快照升級；要採新限制，開新的 managed session。Warm reuse 同樣不升級。

### Task authority 與 outcome 分類

Task lead／reviewer 的 start 綁定原 task incarnation、durable start command 與 reserved ID；start command 保存 control_version。實際 frame 的順序是 task gate → confinement async check → task gate（即使 check 拒絕仍核對）→ registry start claim 的 on_transport fence → BAT。準備 worktree 的 frame 也核對 task gate。pause、version 變動或 command 取消若發生在 connect／confinement await 期間，仍是未送，保留操作者的 pause／新 version，不把舊 tick 改為 needs_ted。Warm reuse 在 awaited identity 後先核對 incarnation 才轉移所有權。

下表補充 [operations-unification.md](operations-unification.md) 的共用 outcome 規則；send 的 durable task command／operation receipts 仍由該文件定義。

| 證據／邊界 | Start | Send |
|---|---|---|
| Task gate 在 transport 前拒絕 | 原 command cancelled；保留 pause／新 version；不送 frame、不自動換 ID | pre-frame StepFailed；取消失效 intent，不重送 |
| ConfinementRefused(sent=False)，task authority 仍有效 | rejected＋needs_ted，記 code；安全的 operation resume 先用 reconcile 判斷原 reserved ID | pre-frame refusal；未送，保留明確 code |
| sent=True／sent=None，或 frame 已可能送出 | uncertain，保留原 ID／carrier，只讀回，不因 InvokeError 重送 | 可能送出的例外為 AmbiguousOutcome，讀回原 command／message |
| BAT 明確 error reply | start 尚不能證明 absence，仍按 sent fence 讀回 | 已有明確錯誤回覆的 send 可記 definitive failure |
| NeedsAttention 來自外部 operation step | step uncertain；resume 必須 reconcile，證明未生效才 RERUN | 同一共用 step 規則，不以 attention 當未送證據 |

### Start frame 與取消邊界稽核

`BatClient._invoke_checked` 的順序是 connect → semaphore → await before_frame → before_send／frame_guard → _roundtrip。`_roundtrip` 在 connection 檢查及 JSON 編碼之後，同步呼叫 on_transport；registry／Task command 的 start_sent=true 落盤後立即呼叫 websocket.send，中間沒有 await。故 account check、connect、semaphore 或更早 await 的取消都是未送；websocket.send 內或等待 ACK／metadata 的取消均保守當成可能已送。Marker 不是「BAT 已接受」的證明；crash 在 marker 與 transport 之間仍保持未定，不能為此重送。

Start reserve 一開始保存 start_sent=false；這只證明 frame 未交 transport，不能證明 owner 已離開。每個 starting reservation 都有 process-held claim：在 registry flock 內以 LOCK_EX／LOCK_NB 取得 `start-claims/<sha256(host + NUL + session_id)>.lock`（0600），同 transaction 寫 starting row 與診斷用 start_claim_token。Flock 才是存活證據，不用 PID、TTL 或 token 判斷。Claim 檔不 unlink，避免下一個 recoverer 鎖到不同 inode。Connector 維持 POSIX-only。

`claim_unsent` 是所有同-ID recover 的共用 primitive。只有 failed／starting＋明確 false 才候選；starting row 的 claim 無法取得就回 START_IN_PROGRESS，零 row／worktree／frame 變動。Failed＋false 的 replacement 同樣須持 claim；第一個 recoverer 在 registry flock 內替換後，第二個不得刪掉其 live starting row。Daemon hard crash 的 OS 釋放 claim，下一程序可在同 flock 內安全重用原 ID，不重複 row／cap；缺 flag 的 legacy row 不是未送證據。Claim 被持有時即使 row 已 failed，仍不可由別的 coroutine／process 重用。

Process-local claim table 以 registry path、host、session ID 區分。Start call scope 在成功、失敗、CancelledError 的 finally 釋放自己的 FD，涵蓋 active／failed／uncertain／superseded 的同步更新與整個 call 返回或丟例外；diagnostic token 正常清為 null，清除失敗也不改 start 結果，flock 仍是唯一證據。另一 coroutine 不能借用 ContextVar 或 table 的 claim，必須開新的 file description；同 process 也會衝突。Task lead 的 preparation claim 可交給同 coroutine 的 nested session_start，nested call 返回／丟例外即釋放，只有明確未送的失敗可用原 ID 重試；已送的 InvokeError 不可重試 start。Fork child 關閉繼承的 claim FD，不 LOCK_UN 父程序的共同 description；exec 不繼承 FD。Owner 的 fail_reservation／StartFrame cancellation／成功路徑共用同一 scope；外部 fail_reservation 或 terminal update 必須先取得未送 row 的 claim，不能釋放 live owner 的 reservation。

不要只看例外種類判 sent；WriteRefused 可出現在 ACK 之後。同步記錄未送結果後，CancelledError 原樣傳出，取消期間不啟動 worktree:remove、read-back、SSH 或任何新 await。`guard_new_start` 在 caller 與 registry transaction 內均拒絕以任何 start_sent=true 的原 ID 再 reserve／start（包括舊 failed row）；一般或 Task lead 的後次 start retry 不能把前次已送證據蓋成 false，必須走原 read-back recovery。Warm reuse 不是新 start，仍只核對原 session。

| 同-ID reuse 邊界 | Claim 行為 |
|---|---|
| `orchestrate.session_start` 的 caller-supplied ID／previous worktree | 先 claim_unsent，再 git／account preparation；reserve 同 flock 持 claim 才可替換 false row，成功後才核對／重用 worktree。所有 relay／fanout starts 共用 reserve。 |
| `BatTaskAdapter.start`／`recover_start` | Adapter 在 external worktree、lead presence／shared-worktree 準備前 claim；lead 經 session_start，reviewer 自己 reserve。Recover 的相同 ID 走原 start，不另行刪 row；live claim 回 False、沒有新 frame。 |
| `lifecycle._failover_one` 的 failed＋false／starting＋false prior | 在 worktree:status／prompt 重建前 claim_unsent；原 fail_reservation 撤回 predecessor handoff 與 reserve 都只使用這次 call 已持有的 claim。Live owner 先拒絕，不呼叫 fail_reservation；dead owner 可同 ID 恢復。Cap、failover_of lookup、replaces supersede 規則維持。 |
| `BatTaskAdapter.recover_failover` 的 pending＋false | 經原 failover adapter 與上述 lifecycle primitive，保留 reserved IDs／journal hash／guards；live claim 回 None 等待，不能替換 row。 |
| `checkpoints.start_in_worktree.restart`（checkpoint continue／integration repair） | false row 的 RERUN 只表示候選；真正 restart 仍須 session_start claim。Live owner 到 needs_attention；claim 釋放後同 operation／reserved ID 才可恢復。true 或缺 flag 繼續只讀 recovery，不重新 start。 |

### Registry identity 與 start error reply

選擇 **fence 已送的 ID**，不把 InvokeError 當成 definitive absence。固定 BAT 的 [S5 start_session](https://github.com/tony1223/better-agent-terminal/blob/b7419892fbc9946799b64cca24c2ec8c7fa15c42/src-tauri/src/codex_app_server.rs#L5224) 先插入 session，ensure_session_connection／thread-start request 的部分 error 會移除 row，但 [5254–5260](https://github.com/tony1223/better-agent-terminal/blob/b7419892fbc9946799b64cca24c2ec8c7fa15c42/src-tauri/src/codex_app_server.rs#L5254) 在 response 缺 thread ID 時直接回 BridgeError，沒有移除已插入 row。Connector 的 InvokeError 只表示 error reply，沒有 structured absence proof，不能從訊息猜可重送。因此一般／Task lead／failover 都保留已送 reservation／worktree；guard_new_start 對任何 start_sent=true 的 row（包括舊 failed）拒絕 CONFINEMENT_START_UNSETTLED。Task lead loop 不送第二個 frame，command／task 維持 uncertain，由 recover_start 讀回。Failover 的 sent error 記 start_uncertain，保留 predecessor handoff，後次只讀原 successor；舊 failed＋true 也進 read-back。Transport 前失敗仍 failed＋false，按 claim 規則重試。

Registry 對 (host, session_id) 永遠至多一列。reserve 的查重／claim／replace 仍在同一 flock：只有 starting／failed＋明確 false 且已取得 claim 的 abandoned row 可替換；其他既存 row 不 append，先拒絕，不能因 terminal status 重用 ID。共同 _read／_write 用 _validate_unique，duplicate 回 REGISTRY_DUPLICATE_SESSION，讀與落盤皆明確失敗，寫入前即檢查，不選第一列或自行 deduplicate。舊檔案若已有 duplicates，交操作者以 BAT／原 creation evidence 核對後修復；不自動丟失未知 session。Cap 的 active／starting 計數、failover_of successor binding 與 replaces handoff 維持；舊 failed＋true successor 納入 read-back lookup，現在同一 ID 不可能被算兩次。

| Append／claim 路徑 | 唯一性保證 |
|---|---|
| registry.reserve（一般／relay／fanout／Task starts／failover 共用） | 同 flock 讀取與 guard；同 ID 只允許 claimed unsent replacement，否則拒絕；_write 再驗證。 |
| registry.ensure_existing（Task／headless start recovery） | 同 flock 先找 host／ID，存在時只 update 原 row；不存在才 append；共同 read／write check 阻止舊 duplicates。 |
| registry.claim_warm／confirm_failover_start | 只更新原 row；read／write check 均生效，不 append，不改 handoff／claim 既有條件。 |
| registry.record_turn 的 items.append | 此為 turns.json，identity 是 host／session／message，不是 session registry；每個 session 可有不同 message rows。保留原 turn fence，不套 session 唯一性。 |

| 路徑／函式 | Reservation 後的 await／before_frame 使用者 | Start frame、取消與恢復 |
|---|---|---|
| `orchestrate.session_start`（一般／relay／fanout／Task lead） | worktree:create（或未送重試的 worktree:status）、git:log、client connect／semaphore、guard_start_frame 的第二次 SSH check；frame 後 meta／tab reads | 每次 call 只有一次 start invoke；client 對 start channel 也只有一次 transport attempt，不自動重送。用同一次 StartFrame 記實際 transport entry；未送 failed＋false，已送 ACK 未定則保留 uncertain，下次 guard_new_start 在新的 reservation／Git／worktree preparation／frame 前拒絕，交原 recovery 讀回。InvokeError 也保留 uncertain＋true／worktree，不能當成 absence proof；舊 failed＋true 同 ID 同樣由 guard_new_start 拒絕。先記已知 worktree path／branch 再 await log。未送 rollback 明確成功時清 carrier identity，同 ID 再 create；未確認移除或選 retain 時，只讀核對原 path／branch 後重用。Re-reserve 保留原 identity，即使重複核對失敗也不能丟失它、改成盲目 create；後次 refusal 不 rollback 先前留下的 worktree。 |
| `checkpoints.start_in_worktree.restart`（checkpoint.continue／integration.handoff 共用） | restart 自身先讀 registry；只有 sent 不為 false 才讀 BAT meta／cwd／options。真正 start 經 session_start | false 回 RERUN，同 operation／reserved ID，用前面已完成 step 的同一個 SSH-managed worktree／branch；沒有 start meta poll。此 carrier 是外部管理且 retain_on_error=true，不走 BAT worktree:remove，沒有已移除 identity 要清。已送則只讀 reconcile，保留所有 identity／terminal mismatch guard。 |
| `lifecycle._failover_one` successor start | readonly source／shared-worktree checks、start_decision 在 reserve 前；reserve 後只 await start invoke 的 connect／semaphore／guard_start_frame（後續 meta 是已送） | 未送 fail_reservation 並回復 predecessor，start_uncertain=false。下次使用 failed／starting＋false 的同 reserved successor ID、原 options／handoff_message_id／command binding；不讀不存在的 successor。Fail／re-reserve 不移除共用 carrier，仍先核對其 identity。已送只讀回，不重送 start；原 handoff frame fence 仍控制只送一次。 |
| `BatTaskAdapter.start` reviewer | journal command 比 registry 更早，含 lead presence／shared authorization／admission checks；registry reserve 後一次 invoke／frame account check、identity poll 的 read／backoff、ACK 後 meta／tab | 每個 reservation 只送一次 start frame。Transport 後的例外或未確認回覆只重試 get-session-meta：最多三次 invoke，間隔 0.25／0.5 秒（client 對 read 的既有 retry 不變）；dict 經 guard_start_record／guard_start_cwd／ensure_confirmed 才 active。仍不可讀則 uncertain＋CONFINEMENT_START_UNSETTLED，交下一 tick 讀回，不 fail／release／重送。Transport 前失敗維持 failed＋false；StartFrame context 保留取消傳播、無 await rollback。Reviewer 不建立或移除 lead carrier，同 ID retry 重新核對 lead。只有有效 ACK 後的顯示 evidence read 才為 best-effort；readable mismatch 仍 terminal。 |
| Task lead preparation／`BatTaskAdapter.start` lead／`TaskCoordinator._start` | command intent 在 adapter 的 warm identity、external worktree、workspace／git reads 與 registry reserve 前；lead loop 呼叫 session_start(retain_on_error=True) | 原 command write point 先記 start_sent=false；實際 start transport callback 在原 evidence write point 記 true（無新表／ownership DB）。因此準備中取消且還無 registry row，可由 journal false 恢復同 ID；warm reuse 記 null，不能把正在跑的 warm session當未送。Lead loop 對已送的同 ID 再呼叫時，retained uncertain＋true 先被 guard_new_start 以 CONFINEMENT_START_UNSETTLED 拒絕；terminal mismatch 也先拒絕。Loop 不會送第二個 start frame，後續只由 recover_start 讀回。 |
| `BatTaskAdapter.recover_start`（lead／reviewer；含 headless lookup） | 先讀 registry／原 command 的 evidence；sent start 只讀 meta，再核對 workspace／worktree／git identity 與 options | Registry false，或 registry 缺失且 journal 明確 false，才走同 ID start 與 claim。Lead 正常 retain carrier；若原 caller 已確認 rollback，沿 session_start 重建新 worktree；不自移除 carrier。原 start refusal 的 needs_ted 仍須既有人工恢復流程，不能由本包改 engine state policy。True／legacy missing 只讀回；未證明則 False 或傳出 read exception，command／task 維持 uncertain。Terminal creation mismatch 在讀 BAT 前拒絕；可用的 read-back 經 _restore_headless_lookup 才 active、清 start error code；不送 start frame。 |
| `BatTaskAdapter.recover_failover` | Task journal 既有 failover／handoff IDs；實際恢復經原 adapter.failover／lifecycle frame boundary | successor false＋handoff pending＋明確 null frame fence 時，走原 callbacks／guards，再核對 journal hash。沿用 lifecycle 的共用 carrier，不另移除，沒有已移除 path 留在 row 的分支。已可能送 start 或 handoff 的結果維持原 read-back／uncertain，不重送。 |
| 其他 before_frame users：`service.session_send`、session_start 首 prompt、failover handoff | verify_at_frame／guard_frame／check_handoff_frame 是 send（含 resume）guard，沒有 start reservation | 不加 start_sent marker、不改原 send 去重／frame fence；受限 options 仍在 transport 前核對。 |

本輪選 **未送 start 的既有恢復** 保留 worktree：checkpoint／repair replay 先前已完成的 worktree step，BAT worktree 則核對 path／branch 後重用。未恢復的 worktree 保持現狀，不把取消改成自動清理；本 branch 的 session_cleanup 不掃 failed reservations，不能假稱 cleanup 已處理。BAT worktree identity 不可讀時拒絕重試，交操作者按既有受控 cleanup 流程處理。若取消在 worktree:create 回執前，path 尚未知，沿用既有同-ID BAT worktree 準備；這不是重送 agent start frame。

對一般 session_start 已建立、尚未送 frame 且 retain_on_error=false 的 carrier，保留既有 rollback，但 row 必須反映其回執。worktree:remove 回 success=true 後，同步將 worktree_path／branch 清為 null、cwd 回到 origin folder，並同次記 worktree_rolled_back=true、rolled_back_worktree_path／rolled_back_branch 供 audit；retry 因此重新 create 自己的 worktree，不採用外來 carrier。Removal 丟例外、被取消或沒有明確成功回覆時保留 identity；同 ID retry 只能經 worktree:status 證明原 path／branch，再沿用。Cancellation 本身仍原樣傳出，不追加 I/O；sent=true 的 start 不 rollback。新的 reservation 保留未證實移除的 identity，不能在一次失敗核對後將其丟掉，讓再下一次 retry 盲目建立 replacement。

BAT 的 worktree manager 在重啟後可能遺失 mapping，此時 `worktree:remove` 即使沒有刪除任何東西仍回 success。上述清除還必須等 `git:getRoot` 對原 worktree path 明確回 null 才能進行；仍有 root、非預期回覆、read-back 失敗或取消都保留原 identity，不能把 success ACK 當實際移除證據。讀取取消後不追加 I/O。`test_a10_successful_rollback_reply_needs_carrier_absence_readback` 涵蓋 no-op success／read error／cancellation 與重複同 ID recovery，既有正常 rollback 測試仍證明真正移除後可重建。

Failover reserve 先保存 branch、permission fields、handoff_message_id（Task Service 用 journal 已保留的 ID）、handoff_status=pending 與明確的 handoff_frame_sha256=null。只有 pending＋明確 null 才證明未進入 frame 派送；缺 key 的舊 active row 不算。升級前仍在 start block 的 unsettled／starting row 尚未嘗試 handoff，可在 readonly read-back 成功時，以 registry flock 補 null 與缺失的 ID／branch；不得覆寫另一個恢復程序已記的 hash 或 ID。

session_start 在已知工作目錄後、start frame 前保存 cwd／worktree_path／branch，讓 ACK 遺失時也有原目的地。Checkpoint／repair 共用 start_in_worktree.restart；升級前尚缺 cwd 的 reservation 只能沿用原 session.start step 已持久化的 cwd，不採新的 host default 或 observed cwd。它們與 failover、Task Service reviewer poll／headless recovery 均在 promote 前核對原目錄。Creation permission mismatch 與 identity error 不由重試清除；只讀 session fields 仍可顯示 current_verification，不能因此改 creation snapshot。

Reviewer start 的 ACK 後 read 與 lost-ACK identity poll，都在 cwd check 後、active 前以原 requested options 核對可讀 meta。Mismatch 一次就保存 creation verification=mismatch／permission_options_changed 與 error_code=CONFINEMENT_MISMATCH，reservation 保持 uncertain，並在既有 start command payload 保存同份 evidence。沒有第二次 read 洗掉 mismatch，沒有 start retry、branch settlement、reviewer_session_id 或 review send intent。TaskCoordinator 沿用原 start failure 路徑：command／task 到 uncertain，因 BAT 可能已有該 session，不能當成 unsent refusal 釋放或到 needs_ted。recover_start 對 registry 或 journal 已記 mismatch 在讀 BAT 前回 False；後續 tick 保持 uncertain，不生成 review prompt。有效 ACK（dict、ok 不為 false、sessionId 等於 reserved ID）後的 unreadable／null evidence read 仍只記 unknown，沿用既有 reviewer activation 行為。

Reviewer 未收到有效 ACK 時，read-back 是 start 是否成立的必要證據，不能當成顯示 evidence read。Start frame 只送一次；後續三次 bounded retry 都讀同一 reserved ID，不重新 invoke start。回覆不同 sessionId 不證明 reserved ID 的狀態；result 的 ok=false 也不當成 definitive refusal：此 envelope 沒有證明 reserved session 不存在或 start 已回滾，可能只是沒有確認完成。兩者都保留 reservation 並讀回；reviewer 對 transport 後的 invoke-error 也採相同保守處理。Read-back 始終為 exception／null／非 dict 時，記 uncertain／CONFINEMENT_START_UNSETTLED（sent=true），task 與 command 到 uncertain，沒有 review prompt。下一 tick 的 recover_start 繼續只讀，guard 通過後才 settle 到 active／verifying；creation mismatch 不被重試覆寫。

Task Service 的 promotion／第一個 prompt 邊界查核如下。一般 task 的 default／allow_all／recipe 不變；未知 options 仍明示 gap，不因這次修正新增 policy。

| 路徑 | 可讀 mismatch 的處理 |
|---|---|
| Lead `BatTaskAdapter.start` → `orchestrate.session_start` | 原 post-ACK ensure_confirmed 在 active／首個 prompt 前拒絕；保留 uncertain reservation 與 terminal creation record。下一次 start／recover_start 不重讀或重送。 |
| Reviewer `BatTaskAdapter.start` 的 ACK read／identity poll | 兩處共用 cwd／requested options guard；保存 terminal mismatch、raise ConfinementRefused，不 active、不 settle start、不送 review prompt。只有有效 ACK 後的未知 read 沿用 best-effort；ACK 未定且 bounded poll 仍不可讀時到 uncertain，不能重送 start。 |
| `recover_start`／`_restore_headless_lookup` | 先拒絕 registry／journal 的 terminal record，再在 ensure_existing 前核對新的可讀 meta。Pending creation 首次 mismatch 保存至 registry（若仍在）與原 command payload；lookup 遺失時不重建 active。已確認 creation 遇到 live drift 保留原快照、拒絕本次恢復；有 lookup 時記 uncertain／code。 |
| Headless `session_presence`／TaskCoordinator 首個 `_send` | 共用上述 restore gate；mismatch 回 uncertain，不建立 send intent。不把 active lookup 或已存在 session 當作權限證明。 |
| Warm lead `_warm_identity` | 在 claim_warm 前核對原 creation options；可讀 drift 不提供／不 claim warm session，不撤銷舊 task capability、不改 creation snapshot。 |
| Task failover successor | lifecycle 的 post-start／reserved-start recovery 先核對 options 才 active；Task 的 _verified_failover_successor 另核對 creation options，before_handoff 與實際 frame guard 遇到可讀 drift 回 False／TaskIdentityMismatch，不送第一個 handoff。既有 mismatch record 在讀 BAT 前就拒絕；保留 journal／hash／ownership guards。 |

實際 handoff frame 在所有 async checks 後、transport 前以 flock 消耗 null 證據並記 SHA-256，再執行原 handoff_frame_guard；Task Service 仍核對 journal 的 prompt hash 與 ownership。pending／sent／uncertain 集合不新增值。讀回到 send 之間再 crash，null 仍在、下次可送一次；正常 ACK 到 active／handoff 之間的中斷也走同一恢復路徑。hash 已存在就永不重送，即使 crash 發生在 fence 與 transport 之間；pending 的此種結果記 uncertain，保留既有 prior-successor 回應。Codex 不依 clientMessageId 去重，不能把這個保守未定當成已收到 prompt。Start timeout／connection loss 同樣保留非 confined successor；一般 policy 仍不變，metadata 真 mismatch 或不可讀仍拒絕 read-back。

已寫入後的 evidence reads 查核如下；只為顯示證據的 read 不能讓已完成 operation failed。派送前的 identity／confinement／Task Service authority checks 仍須通過，不改成 best-effort gate。

| 路徑／read | 時點與處理 |
|---|---|
| `checkpoints.start_in_worktree` 最後的 `service._meta`／`session_fields` | durable send accepted 後的顯示證據。捕捉 timeout／disconnect／其他 read exception，保存 creation、current 標 unknown／readback_failed；operation succeeds。 |
| `checkpoints._run_continue`／`integration._run_handoff` 結果 | 取上述共用 helper 的 fields，不再必須讀 meta；continue 的 inventory refresh 已 suppress exception。Repair receipt 的本地 journal 寫入沿用原流程。 |
| `orchestrate.session_start` post-ACK `_meta` | 在第一個 prompt 前確認 start options；read exception 轉 None，non-confined 只記 unknown、confined 沿用原拒絕未知的 gate。ACK／reservation 不丟；結果用已記 record，accepted prompt 後不追加 evidence read。 |
| `lifecycle._failover_one` post-start `_meta`／結果 | 同上，read exception 不 orphan successor；confined 的 required options gate 在 handoff 前。Handoff 完成的 result 只回已記 record，沒有額外 evidence read。 |
| `BatTaskAdapter.start` reviewer post-start meta | 有效 ACK 後的未知 read 為 best-effort；read exception 記 unknown／session_unloaded，仍 active。Lost／unconfirmed ACK 的 identity poll 則是必要 start proof，不可讀維持 uncertain、只重試 read。可讀但 cwd 或 requested permissions 不同則為必要拒絕，保留 reservation，非顯示證據失敗。 |
| `BatTaskAdapter.failover` 的 after-send `_verified_failover_successor` | 此為既有 Task Service journal／registry／worktree authority proof，非顯示 evidence read，保持原未知／不相符行為；也核對 creation options 的可讀 drift，不能用此修正略過 Task gate。 |

Journal schema additive：confinement_host_checks 在每次 open 以 CREATE TABLE IF NOT EXISTS 建立，不占 user_version，不需要 data migration。Data steps 固定為 1（既有 api_events copy）、2（observation history backfill）、3（後續 delivery deployment history）；其他工作包 DDL 都不另占版本。利用原 commands／operation_steps／sessions_observed 的 JSON 與 evidence refs，不重建 task tables。registry 增 schema_version／confinement 欄位，missing 值按上述保守規則解讀。

與 observation 合併後，離開 active config 的 host 仍可讀既有 session／history／relations。Creation snapshot 保留原證據；目前帳號查核回 unknown／host_not_configured，不用舊 cached verified 宣稱目前邊界。只有 durable identity 尚無觀測 row 的 session 也補共用 unknown confinement fields，不另查 BAT。

## Dashboard、文件與預計修改檔案

Session card 與 detail 同時顯示 level、write_scope、Git isolation；可展開 actual options、來源、檢查時間、目前核對狀態與 limits。Checkpoint continue、work item continueFrom、repair form 在選 agent 時更新預計限制，unknown／gap 要可見；後端結果取代預估值。表單的提示不是「只能寫自己的資料夾」。不新增使用者必須自行調整 SDK options 的流程。

兩種語言最低共用字串如下；每個限制原因另以 key 翻譯，不能印出 null／undefined：

| key／語意 | zh-TW | en |
|---|---|---|
| level `host_account` | 主機帳號限制（指定目錄） | Host account restriction (listed roots) |
| level `os_sandbox` | OS sandbox 選項（尚未實機驗收） | OS sandbox options (live acceptance pending) |
| level `prompt_gated` | 工具詢問；批准後可越界 | Tool approvals; an approval can allow access outside the folder |
| level `none` | 無已證實的執行寫入限制 | No verified execution write restriction |
| options confirmed／OS unverified | 已核對 sandbox 選項；OS 阻擋尚未驗收 | Sandbox options confirmed; OS enforcement not yet tested |
| account check unknown | 帳號宣告尚未查核／查核不完整 | Account claim unchecked or check incomplete |
| Task Service gap | 保留既有測試流程；本任務限制不足 | Existing test workflow preserved; this task has a confinement gap |
| confined policy | 禁止提升權限與批次批准 | Permission raises and bulk approvals are disabled |

預計 Phase 2 修改；Phase 1 只提交本文件：

| 檔案 | 最小變更 |
|---|---|
| 新 `src/bat_agent_connector/confinement.py`、`config.py` | 共用 options／不可放寬判斷、evidence schema、HostConfig 宣告、只讀查核。使用現有 runner，不新增可寫 channel。 |
| `orchestrate.py`、`lifecycle.py`、`service.py`、`registry.py` | 各入口、reserve／resume／permission／failover 保持限制與證據；修 registry_permission_fields 缺口；registry 持有 start claim／診斷 token，統一同 ID 的未送恢復。不重構 operations-unification 的流程。 |
| `task_bat.py`、`task_journal.py` | start／recovery／warm／reviewer 保存證據；migration；Task Service 相容缺口，不改 engine／recipe／verifier 命令。 |
| `checkpoints.py`、`integration.py` | start read-back 核對限制、preview／result projection。 |
| `inventory.py`、`triage.py`、`api_v1.py` | Session／host／capabilities projections、只讀 freshness、同一 evidence schema。 |
| `api_actions.py`、`mcp_server.py`、`cli.py` | 沿用統一 operations；暴露結果與穩定拒絕，沒有 bypass action。 |
| `dashboard/app.js`、`app.css`、`i18n.js` | cards／detail／checkpoint／repair forms、en 與 zh-TW；沿用 fill()、CSP 與 live-update hold 規則。 |
| `skills/bat-agent-connector/SKILL.md`、`skills/hermes/bat-agent-connector/SKILL.md` | 同步讀證據、不要求受限 session raise；修正 acceptEdits 說法。 |
| `README.md`、`README.zh-TW.md`、`CHANGELOG.md` | 設定、限制與缺口，連到本文件；unreleased entry 引計畫 §06／§07／§12、A10。 |
| `docs/PROTOCOL.md`、`docs/design/checkpoints.md`、`resource-policy.md`、`integration.md`、`api-v1.md`、`dashboard.md`、`task-service.md` | 引固定 source 可達欄位；更正 A10／acceptEdits 描述，更新各自「尚未涵蓋」但保留尚未實機驗收、Task Service gap。 |
| `tests/mockbat.py`、`tests/test_confinement.py`（新增）、`tests/test_start_claims.py`（新增）、`test_checkpoints.py`、`test_lifecycle.py`、`test_task_service.py`、`test_api_v1.py`、`test_config.py` | 下列 mock、真 process／flock、失敗恢復與 projection 驗證。 |

## A10 測試計畫

Mock 只能證明 Connector 的 options、gate、evidence 與顯示；不假造 Claude／Codex 的檔案 sandbox。所有人路徑是 fixture，例如 `/srv/example-personal/source`；不送 real-host 寫入 channel。用 `tests/mockbat.py`、checkpoint 的 `LocalRunner`／`RealGitLog` 與 temp repos。

取消測試在 account hook 與 session_start 呼叫邊界捕捉原 CancelledError，核對同一個 exception／args；Task await 只核對取消確實傳出。Python 3.10 會在 await 已取消的 Task 時丟失 cancel message，不能把該 runtime 差異誤判為 Connector 改寫例外。完整 suite 仍在 3.10 與 worktree interpreter 執行。

| 驗收／計畫 | 實作測試 | 結果與邊界 |
|---|---|---|
| A10；§06、§07、§12 | `test_start_preframe_cancellation_is_unsent_and_reuses_reserved_id`、`test_start_cancellation_at_earlier_await_is_also_unsent`、`test_task_preparation_cancellation_recovers_from_unsent_command_before_registry`、`test_start_postframe_cancellation_keeps_uncertain_and_does_not_resend`、`test_task_lead_lost_ack_keeps_sent_evidence_until_readback_recovery` | Account check／git log／connect／semaphore 取消：零 start／rollback frame、false＋failed、原 CancelledError；starting＋false 模擬未 unwind crash，原 ID／worktree 只 start 一次。已送後取消保留未定，read-back 不重送。 |
| A10；§06、§12；C03 | `test_checkpoint_preframe_cancellation_restarts_unsent_reserved_session_once`、`test_repair_preframe_cancellation_restarts_unsent_reserved_session_once`、`test_task_failover_preframe_cancellation_recovers_reserved_start_and_handoff` | 同 operation replay：未送不用 meta poll，已送只讀回；checkpoint／repair 原 worktree／ID，恰一個 start 與 instruction。Task failover 同原 reserved IDs／journal hash／guards，恰一個 handoff。 |
| A10；§06、§07、§12 | `test_host_account_refusal_before_frame_releases_start`、`test_task_start_refused_before_frame_needs_ted_not_uncertain`、`test_checkpoint_continue_host_account_refusal_needs_attention_and_resumes` | 明確 unsent refusal：零 start frame、釋放 reservation、按 retain_on_error rollback；task rejected／needs_ted 與 code；同 operation resume。 |
| A10；§06、§12；v2 A06 | `test_a10_unsent_rollback_clears_carrier_and_same_id_creates_once` | before_frame refusal／BatError 之後確認 removal：failed＋false、清 carrier identity、保留 rollback audit；原 ID 再 create，恰一個 start frame／registry row。 |
| A10；§06、§12；v2 A06 | `test_a10_unconfirmed_rollback_keeps_carrier_and_retry_proves_or_refuses` | Removal exception／CancelledError／非成功回覆保留 path／branch；相符 status 才 reuse，missing identity 的重複 retry 都拒絕、不再 create、不丟 retained path。 |
| A10；§06、§12、§28；v2 A06 | `test_a10_task_lead_recover_start_recreates_a_confirmed_rollback_under_same_id` | journal-owned lead 的未送 refusal 到 rejected／needs_ted；原 caller 選 rollback，沿既有人工恢復 transition 再 recover_start，同 ID 新 worktree、active、一個 start frame／row。正常 Task adapter 的 retain policy 不變。 |
| A10；§06、§12 | `test_general_start_unreadable_meta_records_unknown_and_keeps_session`、`test_post_start_mismatch_keeps_reservation_and_worktree`、`test_reviewer_start_meta_failure_still_activates`、`test_post_start_cancellation_keeps_acknowledged_reservation` | ACK 後不釋放 session；non-confined unknown 可繼續、真 mismatch 保留 uncertain；reviewer best-effort。 |
| A10；§06 | `test_confined_failover_unsettled_successor_reads_back_before_new_attempt`、`test_host_check_table_is_created_without_consuming_schema_version`、`test_legacy_codex_predecessor_uses_its_recorded_agent`、`test_checkpoint_reconcile_legacy_reservation_requires_evidence` | Successor confirmed／mismatch／unreadable／null 的 readonly recovery；vanished 尚缺可信 absence signal。高版本 journal 補 table 不占版本；legacy agent 辨識與缺證據停在 needs_attention。 |
| A10；§06、§12 | `test_manual_failover_recovered_start_sends_reserved_handoff_once`、`test_task_failover_recovered_start_preserves_journal_and_frame_guards`、`test_recovered_failover_row_matches_normally_confirmed_successor` | Manual confined／allow_all 的 lost ACK／post-start evidence failure；Task Service 沿用 reserved message ID、before_send 重記 hash、原 identity／frame guards 通過、after-send verification=True。只送一個 handoff；恢復後 row 除 timestamps 外等同正常 successor。 |
| A10；§06、§12 | `test_failover_crash_before_handoff_keeps_durable_unsent_proof`、`test_failover_crash_after_start_ack_before_activation_recovers_handoff`、`test_failover_attempted_handoff_is_never_resent`、`test_legacy_unsettled_failover_backfills_proof_without_resetting_a_frame` | Read-back 到 send 之間與正常 ACK 到 activation／handoff 的 crash，可用原 ID 重建 prompt；frame 已嘗試則保持 uncertain、永不重送。Legacy start-only row 可補證據，較晚的 read-back 不清已記 hash／ID。 |
| A10；§06、§12 | `test_failover_recovery_checks_reserved_cwd_before_promotion`、`test_acknowledged_start_checks_reserved_cwd_before_promotion`、`test_reviewer_readback_checks_reserved_cwd_without_retrying_start`、`test_checkpoint_start_readback_refuses_identity_and_terminal_mismatch`、`test_handoff_start_readback_refuses_identity_and_terminal_mismatch` | 不同 cwd 保留 reservation、terminal identity code、零 prompt／handoff、不 promote；缺 cwd 保持 unsettled；正規化相同的 cwd 可恢復。Start／checkpoint／repair／reviewer 共用檢查，無追加 reviewer start frame。 |
| A10；§06、§12 | `test_failover_recorded_permission_mismatch_is_terminal`、`test_checkpoint_start_readback_refuses_identity_and_terminal_mismatch`、`test_handoff_start_readback_refuses_identity_and_terminal_mismatch` | 初次 post-start 或 recovery 記 mismatch 後，matching metadata 也不重新讀取、覆寫或 promote；creation record 不變、零 handoff，confined／allow_all 同樣 terminal。 |
| A10；§06、§12 | `test_checkpoint_accepted_send_survives_evidence_read_failure`、`test_handoff_accepted_send_survives_evidence_read_failure` | Timeout／disconnect 都在首個 send accepted 後發生；operation succeeded、creation 不變、current unknown／readback_failed，僅一個 start／prompt frame，同 key 回同 operation。 |
| A10；§06、§12、§28 | `test_reviewer_permission_mismatch_keeps_task_uncertain_without_prompt` | Codex danger-full-access／Claude bypassPermissions 取代原 read-only／plan；ACK 與 lost-ACK poll 都只一次 read 就拒絕。Reservation uncertain／CONFINEMENT_MISMATCH、creation terminal、command／task uncertain、零 review prompt／send intent；matching meta 或 lookup 遺失後的 recover_start 仍不讀 BAT。 |
| A10；§06、§28 | `test_reviewer_matching_readback_still_activates`、`test_reviewer_start_meta_failure_still_activates` | Claude／Codex matching ACK／identity poll 仍 active；有效 ACK 後的 unreadable meta 仍 active、unknown／session_unloaded，不改既有 best-effort 行為。 |
| A10；§06、§12、§28 | `test_reviewer_lost_reply_polls_only_and_recovers_next_tick`、`test_reviewer_unconfirmed_reply_keeps_single_start_frame` | Claude／Codex 的 start 已交 transport：ConnectionLost、不同 sessionId 或 ok=false；meta exception／null／非 dict 都只送一個 start frame、三次 read。Reservation／command／task uncertain＋CONFINEMENT_START_UNSETTLED，零 review prompt；下次 tick 以 read-back active／verifying，不增加 frame。 |
| A10；§06、§12、§28 | `test_reviewer_readback_backoff_can_settle_without_another_start`、`test_lead_lost_reply_retry_is_fenced_until_readback_recovers` | Reviewer 的前兩次 metadata read exception／null／非 dict，第三次相符即可 active；只重試 read、不重送 start。Lead loop 的 lost ACK 由 sent evidence／guard_new_start 阻擋第二次 frame；後續不可讀不 settle，可讀後只用 recover_start active。 |
| A10；§06、§12 | `test_host_account_refusal_before_frame_releases_start` | Reviewer 的 before_frame refusal 仍零 start frame、failed＋start_sent=false、釋放 reservation，不 retry／read-back；既有取消測試繼續涵蓋相同未送邊界。 |
| A10；§06、§12、§28 | `test_task_lead_start_mismatch_never_sends_initial_prompt`、`test_task_headless_start_mismatch_never_promotes`、`test_task_failover_options_mismatch_blocks_first_handoff`、`test_bat_warm_reuse_claims_only_clean_completed_service_session` | Lead／reviewer 的 reserved／headless recovery、pending／confirmed creation 分開；已確認快照不被 live drift 改寫。Lead start、warm claim 與 failover post-start／before-handoff／frame checks 均拒絕 mismatch，零第一個 prompt，不新增 engine／recipe policy。 |
| A10；§06、§12 | `test_a10_lead_invoke_error_is_fenced_and_recovers_one_registry_row`、`test_a10_general_invoke_error_retains_and_fences_the_sent_id`、`test_session_start_keeps_worktree_on_error_after_transport` | 真 invoke-error wire reply 在 mockbat 建 session 後回 error。Lead／一般 start 只送一次、保留 worktree／uncertain；舊 failed＋true 同 ID 也拒絕。Task command／task uncertain，recover_start 讀回 active、一列；cap=1 只算一次。 |
| A10；§06、§12 | `test_a10_reserve_cannot_append_another_row_for_an_existing_id`、`test_a10_registry_duplicate_identity_fails_loudly_without_changes`、`test_a10_registry_write_rejects_duplicate_identity_atomically`、`test_a10_start_failover_recovery_and_warm_claim_preserve_unique_rows`、`test_recovered_start_updates_one_record_for_resource_policy` | Reserve 的 failed／superseded／legacy ID 不 append；duplicate 檔案的 read／reserve／ensure／warm claim 與 write 都明確失敗、不改檔。逐次檢查 start、failover error、read-back、repeated failover、recovery helper、warm claim 後每 host／ID 只有一列；failover 只一個 successor frame，原 handoff fence／cap 不變。 |
| A10；§06、§12 | `test_two_processes_reclaim_unsent_start_only_once`、`test_crashed_owner_releases_unsent_start_claim` | 真 spawn processes、真 flock、mockbat：starting／failed＋false 同 ID 僅一個 start frame；另一程序 START_IN_PROGRESS、row 不變。os._exit 無 Python cleanup 後 OS 釋放 claim，可用原 ID start。Claim file 為 0600，正常 return token=null。 |
| A10；§06、§12 | `test_live_start_claim_refuses_another_coroutine_before_worktree_or_frame`、`test_child_coroutine_cannot_borrow_parent_start_claim`、`test_start_claim_survives_failed_status_until_owner_returns`、`test_nested_start_releases_its_claim_before_owner_retries` | Session／failover／task lead／reviewer 的第二 coroutine 在 worktree／frame 前拒絕；不能 fail／update／ensure_existing 另一 live row。Inherited ContextVar 不借 claim；terminal failed 尚未 return 時仍持 claim，nested start 的已證明未送 retry 仍可行。 |
| A10；§06、§12 | `test_failover_unsent_recovery_requires_abandoned_claim`、`test_owner_unsent_failure_releases_claim_for_same_id_retry`、`test_start_preframe_cancellation_is_unsent_and_reuses_reserved_id`、`test_start_postframe_cancellation_keeps_uncertain_and_does_not_resend` | Failover 真 process live claim 零 fail_reservation／worktree／frame；dead claim 同 ID start／handoff 一次。Owner 拒絕／取消後釋放 claim，checkpoint／repair／Task 同 ID retry 可行；frame 可能已送的既有 uncertain 規則與零重送維持。 |
| A10；§06、§12 | `test_a10_checkpoint_absolute_path_carries_confinement`（Claude／Codex） | 摘錄含人的絕對路徑；options／flag／level、拒絕 force raise、bulk skipped、來源零寫入。 |
| A10；§06 | `test_a10_general_start_preserves_operator_policy_and_records_evidence`、`test_a10_confined_start_preserves_stronger_explicit_claude_mode`、`test_a10_level_requires_options_not_cwd` | default／allow_all／confined 的 options／level；explicit plan／dontAsk 不放寬；相同 cwd 不決定 level；os 最多 options_confirmed。 |
| A10；§06、§12 | `test_a10_accept_edits_requires_verified_account_and_checks_are_read_only`、`test_a10_declared_unverified_account_blocks_new_start` | 未查核 account 不送 acceptEdits；宣告失敗不降級 start，零 start／worktree frame。 |
| A10；§06 | `test_a10_raise_and_deferred_raise_are_refused`、`test_confined_sessions_stay_confined_on_an_allow_all_host` | force、dry-run、舊 pending raise、bulk 與 failover 保持限制。 |
| A10；§06、§12 | `test_a10_loaded_legacy_send_works_but_missing_evidence_resume_is_blocked`、`test_a10_resume_uses_original_options_not_stale_tab`、`test_a_lost_start_reply_is_read_back_not_started_again`、`test_a10_lost_confined_start_ack_retains_options_and_worktree` | loaded legacy send 不受缺舊證據阻擋；null meta 的 confined resume 須原 policy；lost ACK 不重開。 |
| A10；§06、§07 | `test_a10_planner_is_read_only_never_and_successor_preserves_limits`、`test_relay_to_bat_session_starts_a_new_worktree_instead`、`test_fanout_from_planner_starts_verbatim_and_cleans_planner`、`test_c03_handoff_resolution_resumes_without_recomposing_or_receiving_twice` | 真正的 planner／relay／children／repair starts 保存 evidence；successor 不丟原限制。 |
| A10；§06、§28 | `test_a10_task_service_keeps_engine_policy_and_reports_gap`、`test_a10_task_failover_records_missing_options_without_changing_engine`、`test_reviewer_start_polls_existing_session_after_start_timeout`、`test_bat_restart_null_meta_and_worktree_never_creates_replacement`、`test_claude_sessions_pin_opus_55_for_lead_and_reviewer`、`test_bat_warm_reuse_claims_only_clean_completed_service_session`、`test_headless_session_lookup_restored_from_task_branch_and_bat_meta` | Engine／recipe／verifier 行為不變；Task Service 的 gap 與原 snapshot、warm/recovery 證據；metadata 缺欄位／重開 journal 不增加 engine 阻擋。 |
| A10；§06、§07、§12 | `test_account_check_remote_command_is_isolated_and_git_runner_unchanged`、`test_account_integrity_rejects_account_controlled_check_inputs`、`test_account_integrity_accepts_root_owned_nonwritable_layout`、`test_account_integrity_unknown_uses_plain_default_never_accept_edits`、`test_account_integrity_gap_at_frame_never_sends_prepared_accept_edits` | Synthetic passwd/home/executable layouts；absolute isolated command、不可替換 interpreter/find/stdlib/parents、owner/ACL/SSH/startup 入口、缺檔 parent、unsupported shell、未知 gap fallback default；第二次查核失去 verified 時零 start frame。Git SSH 不變，無 fleet host／真 home 寫入。 |
| A10；§06、§12 | `test_a10_closure_gate_blocks_writable_module_bytecode_and_extension_before_interpreter`、`test_a10_closure_gate_incomplete_proof_never_runs_interpreter`、`test_a10_closure_gate_checks_symlink_targets_and_their_parents`、`test_a10_both_closure_gates_prove_intermediate_interpreter_symlink_hops`、`test_a10_clean_closure_gate_keeps_verified_claude_start`、`test_a10_account_verdict_requires_one_json_document_and_zero_exit` | Real shell／GNU find 的 disposable synthetic tree：module／pyc／extension 可寫、layout／budget／time／unreadable、intermediate link／parent 皆在 interpreter 前拒絕。Clean path 仍 verified／acceptEdits；argv -c／ptrace_scope evidence；非零或多 JSON 不 verified。 |
| A10；§06、§12 | `test_a10_both_closure_gates_reject_python_import_redirection`、`test_a10_closure_gate_accepts_proven_native_library_without_overrides`、`test_a10_closure_gate_rejects_incomplete_native_directory_search` | Both pre-exec gates reject executable／shared-library `._pth` and build markers; library aliases／multiarch／real targets without overrides still pass. Inaccessible native directory enumeration fails closed; markers share the entry／time budget. |
| A10；§06、§07、§12 | `test_a10_same_account_forged_verdict_never_enables_accept_edits`、`test_a10_verified_channel_facts_must_match_declaration`、`test_a10_auditor_program_uses_exact_direct_exec_and_requires_preflight`、`test_a10_auditor_login_inputs_are_checked_as_bat_uid` | 無可信 alias 不執行可偽造的 BAT 登入；GET／start 為 check_channel_untrusted／fallback_default，Claude default。可信 alias 使用固定 sudo／env／isolated Python argv，UID／channel facts 必須相符；same UID、auditor startup 可寫在 drop 前拒絕，GNU find 再以 BAT UID／groups／ACL 覆查 auditor .ssh／startup。原 root scans 透過 channel fixtures 驗證。 |
| A10；§06 | `test_a10_linux_read_only_scan_uses_find_and_process_identity`、`test_a10_host_account_cache_expires_without_upgrading_creation`、`test_a10_closed_host_evidence_journal_returns_unknown`、`test_a10_host_account_configuration_rejects_uncheckable_claims`、`test_a10_host_evidence_migration_is_idempotent_and_persists` | Linux /proc 身分、capability、實際 GNU find 的可寫 fixture／symlink／readonly owner 的 chmod 出口、父目錄、budget／timeout／process 不明、stale 與 journal 不可讀；不寫 probe。Exec fixture 只替換其 pathlib import，保留真 Path；Python 3.10／3.11 也跑完整 suite。ACL 使用 find 的 access 判定，完整 ACL host matrix 留 W12。 |
| A10；§06 | `test_a10_partial_permission_ack_preserves_creation_and_reports_drift`、`test_a10_start_mismatch_stays_uncertain_and_never_sends_prompt`、`test_a10_frame_guard_catches_drift_after_first_read`、`test_a10_creation_snapshot_survives_runtime_drift_and_send_refuses` | Partial setter ACK／GUI drift／start mismatch 保留快照並顯示 current；受限 drift 不送下一 prompt。 |
| A10；§06 | `test_a10_persistent_and_exit_plan_approvals_are_refused` | dont_ask_again、ExitPlanMode mode raise 被拒絕；deny／一般問題仍走原流程。 |
| A10；§10、§19 | `test_a10_session_capabilities_inventory_and_triage_share_evidence`、`test_a10_cached_legacy_inventory_exposes_unknown_evidence_without_rewriting` | REST／service／inventory／triage 一致；既有 MCP／CLI 直接轉出同 reads。manual readonly 不變。Dashboard Playwright fixture：en／zh-TW、390／768／1440 px、session card 與 checkpoint／repair forms 切 agent、無 null／undefined／[object／水平 overflow。 |
| A10；§06、§10、§12 | `test_a10_capabilities_account_start_effect_is_cached_read_only`、`test_a10_account_start_effect_and_gate_agree`、`test_a10_recheck_passes_live_and_confined_claude_uses_accept_edits` | Unchecked、stale、三種 hardening gap、mismatch、其他 unknown、verified、undeclared 的 start_effect／reason；REST GET、daemon MCP 與 CLI／MCP host reads 同值、零 runner call。非 stale state 的 projection 與 start gate 一致；recheck 後 live 通過才送 acceptEdits。 |
| A10；§06、§10、§12 | `test_a10_dashboard_start_note_matches_account_effect_in_both_languages`／`tests/dashboard_confinement_note.mjs` | 用真 app／i18n 的 confinementNote 跑 22 個 Node rendering cases：四種 effect × Claude／Codex × en／zh-TW、未宣告、切 agent／host。與 status 相反的 effect 及任意 reason 證明 UI 不另建規則。Refused＋reason 先列、Codex 不談 acceptEdits，fallback 附原 caveats，recheck 不誤稱 blocked。 |

Mock 與只讀 fixture 只能證明 options／gate／證據，不能宣布 A10 的實機阻擋通過。完成須跑 `uv run ruff check .`、完整 `uv run pytest -q`、`.mjs` 的 `node --check` 與雙語 Playwright。Host check table 的 DDL 每次 open 執行、idempotent，不更動 user_version；既有高版本 journal 缺 table 也可補齊。

## 交給 W12 的實機驗收程序

這是書面交付程序，本工作包不連實機做寫入。只在專用驗收 host／repo 執行；由主機操作者提供測試帳號、managed clone 與 SSH alias。人的既有 folder 永遠不作 write target；它們只可供上述 account metadata check。

1. 記錄 Connector commit、BAT serverVersion／來源版本、實際 CLI 版本、OS、session preset、SSH／BAT／CLI 身分、options、managed roots 與 network 狀態（無法讀到就記 unknown）。確定測試 host 沒有共用人的可寫工作區，停用本次 bulk auto-approve。
2. 操作者在專用測試 parent 建一個唯一的 `batc-a10-<random>` **目錄**，canonical path 必須位於所有 managed roots 外，且和 protected 個人 roots 完全不重疊。Sandbox 案例也須在 CLI 實際准許的其他 writable roots 外；不能使用 CLI 可寫的 temp／cache parent 來宣稱越界。不得用人的 home／repo 當 parent。先用 `mktemp -d`／`realpath` 證明 ownership 與位置，記錄 inode；只在這個目錄建立 `sentinel.txt`（固定內容）及預備 `new.txt` 的路徑。
3. Sandbox／prompt-gated 測試：目錄對 BAT UID 可寫，先由操作者以同 UID 在此測試目錄做一次寫入正向控制並還原 sentinel，確認失敗不是 host DAC。Host-account 測試：改用另一個專用 fixture owner，於本次專用目錄內另建 parent／target，讓 BAT UID 可只讀列舉但不可寫；測試 target、其 parent 與可替換的祖先都必須阻擋 BAT 的寫入／刪除。所有 chmod／chown 都限本程序建立的專用 fixture，不改既有 parent 或個人資料夾。透過同身分 metadata check 證明文件／目錄／parent 的 deny，記錄結果。
4. 用專用來源 session／temp repo 建 checkpoint。摘錄放入這個 **測試目錄**的絕對路徑，指示「用 Write／Edit 改 sentinel，再用 shell 改 sentinel、建立 new.txt、rename／刪除 sentinel」。從 checkpoint 開新的 managed Claude／Codex session；記錄 start intent／meta／confinement 與 operation ID。另在 managed worktree 指示寫一個測試檔、跑既有小測試並 commit，驗證限制仍能完成正常工作。
5. Prompt-gated 不批准越界詢問；保存工具名稱、input、pending／deny 結果。os_sandbox 保存真正的拒絕錯誤／runtime log，不能以 agent 自稱「沒寫」代替；on-request 的批准出口未使用。測 host_account 時，在這個專用 fixture 的測試請求可由操作者逐次批准，仍須被帳號權限拒絕，證明批准不改 DAC。Claude file tools 與 shell 分開驗證，不能只測 shell 就掩蓋 acceptEdits 缺口。
6. 對新的受限 session 測一次 allow-all／force、approve-pending dry-run／apply 與 deferred raises；應 refused／skipped，零 raise frame。驗證 failover successor 的 policy 不變；不靠切換 agent 逃出限制。resume／daemon restart 後讀回原 level 和 actual options；null／不明保持 uncertain，不自動再啟動。
7. 操作者在同一測試目錄只讀比對 sentinel digest／inode、new.txt 不存在與目錄 entries；任何寫入、刪除、rename 成功都判該宣告失敗。若只看到待詢問，最多證明 prompt_gated，不能給 os_sandbox。若 CLI 不可觀测 sandbox 拒絕，記未證明。network 沒有測／不能設定時保持 unknown，這次檔案測試不升級成網路隔離。
8. 留存帶 timestamp 的開始選項、meta、拒絕 log、讀回指紋、正向測試結果、批准／raise 拒絕與清理結果，連到 W12 的 A10 run；證據匯入格式及可信 evidence file 由 W12 工作包定義。遮蔽真實 host／person paths 後才產生可公開報告。不同 BAT／CLI／account／policy 的證據不可混用；host-account 的 DAC 失敗不可代作 sandbox 的正向控制／拒絕證據。
9. 操作者停止測試 session，按既有 managed cleanup 流程整理測試 worktree。只移除第 2 步保存的專用目錄：先核對唯一命名、canonical parent、owner／inode 與無 mount／symlink 替換，再清理已知 fixture entries 與空目錄。不能對 prompt 回傳的任意路徑執行 recursive remove；個人 roots 全程沒有 probe，亦不做清理。

A10 的通過紀錄必須注明 level／機制與批准限制。`none`、只有 options_confirmed、或 Task Service 相容缺口都不是實機驗收通過。prompt_gated 只證明未授權的工具請求被擋，不能宣布帳號或 OS 永久阻擋；完整保護須相符的 os_sandbox／host_account 測試證據與上列界線。

## 尚未涵蓋

- Start transport marker 落盤與 websocket.send 之間的 hard crash：保守保持可能已送，不以缺 meta 重送；仍需可信 absence signal／操作者確認。此 fence 只保證不重送，不保證 session 一定存在。
- W12：真 host 的 trusted auditor alias、實際 SSH 登入 UID、sudoers 固定 argv、auditor SSH／startup／ACL 邊界與 verdict 仍待 live acceptance；本包只用 synthetic fixtures，不能宣稱這些主機已 verified。可信副本替換、未交給 BAT 的 auditor credentials 與 host setup 由操作者在 BAT 權限之外確認；從已遭登入劫持的 channel 不能自證乾淨。W12 尚須在真 Debian／Ubuntu 與 RHEL host 核對 closure layout、symlink chains、gate budgets、-c sudoers 與 native／ptrace evidence。Bootstrap／auditor 的 ACL-bearing layout 本版保守 unknown，完整 ACL preflight 與 root 的 runuser／setpriv 替代路徑未實作。

- 舊 active successor 的 pending 若沒有明確 null frame fence，不能證明未送，保持原 prior-successor 行為；不回填成可重送。Reserved start 沒有可比對的 permission options 時，仍無法得到 options_confirmed，維持 CONFINEMENT_START_UNSETTLED；仍有 live claim 的未送 start 則回 START_IN_PROGRESS，不為 handoff 恢復而假造證據。已記 frame hash 的 crash 結果只保證不重送，不證明 BAT 收到 prompt。
- Failover 的 vanished proof 待釐清：既有 BatTaskAdapter.session_presence 對 null 一律 uncertain，claude:list-sessions 列 SDK history；無 tab 的 headless／unloaded session 也可存在。不得以 missing tab／null 或 history 缺項釋放可能仍在跑的 successor。已有唯讀 read-back 可恢復相符 successor，其餘保持 unsettled；需可信 host absence signal 才可重新 start。固定 BAT 的 [remote_server.rs:3174](https://github.com/TED-H-com/better-agent-terminal/blob/b7419892fbc9946799b64cca24c2ec8c7fa15c42/src-tauri/src/remote_server.rs#L3174) 以 cwd／agentKind 呼叫 list_sessions_native；不提供完整 live local-session-ID 清單。
- W12 的 sandbox_evidence_file 設定、可信證據匯入、其檢查與測試延後；schema 保留 verified，但本包不把 OS sandbox 標為 verified。
- 真 host 的 BAT／CLI 版本、OS sandbox 實際 roots／network 與實機阻擋尚未驗收。W12 要提供可核對同 runtime 的證據；沒有證據的主機保持 gap。
- Task Service 的 allow-all engine／recipe 如何改成更強限制且保留測試，屬後續決策。這裡只保存現況與限制不足，不修改 §28 禁止增加的模型／recipe 政策。
- 需主機操作者確認哪些 protected_roots 構成完整的私人寫入邊界，trusted alias 是否為宣告的獨立 auditor，direct exec 是否真為 BAT UID。無法從現有 BAT protocol 取得完整身分或全域不可提權證明。
- macOS／Windows 的 account／ACL 查核；任意 SDK sandbox、Codex writable roots／network 的 BAT pass-through；容器／新 runner／BAT 上游改動，都不在本包。
- 原生 GUI、逐次批准與其他主機服務的外部寫入不可由 Connector 全面禁止。`cwd`、managed clone、skill 指示與任何單次檢查都不稱為完整永久保護。
