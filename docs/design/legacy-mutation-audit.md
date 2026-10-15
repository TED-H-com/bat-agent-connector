# Legacy mutations：R01 接續盤點

2026-10-08；查核基底 `1485efff4ae6e7241b00cf7c3e42787446c733b7`，追蹤 [#55](https://github.com/TED-H-com/bat-agent-connector/issues/55)。
v1/v2 是同一產品與共同功能 backlog；Tauri 是 UI／client 計畫修訂，不把既有後端能力另分成產品。
沿用 [operations-unification.md](operations-unification.md) 的 actions、TaskCoordinator 與最後 frame 檢查。
本表是基底快照；有 policy／command guard 不等於 public adapter 已有 operation、caller scope 或跨入口冪等性。

2026-10-09 收尾候選已把 send／continue／answer、permissions、固定 bulk approval、start、
relay／fanout／standalone failover、external verification testimony 與 `worktree.merge` 接到
原中央 OperationService；各入口使用原 principal，不因 MCP／CLI 更換入口取得額外權限。
Task controls 保留既有 coordinator／command authority。`worktree.merge` 的雙 carrier reservation、
checked-exit Git proof、positive loaded/unloaded runtime 與逐 effect ACK 見
[worktree-merge.md](worktree-merge.md)。Verification 是外部證詞，不能取代 trusted task verifier。
Unsafe legacy remove／cleanup apply 保持停用，使用既有 reviewed cleanup；task-owned cleanup
已經中央 coordinator finalize contract。下表與問題敘述保留查核基底歷史，不代表目前仍須重做。
合併、exact-head checks、installed/live 的實際狀態見 [implementation-status](../product/implementation-status.md)。

| 入口 | 已有 authority／durability | 尚缺與依賴 |
| --- | --- | --- |
| MCP/CLI send、continue、answer | HTTP `session.send`／`session.answer`；service 的 policy、task command、FrameGuard | 正在接同一 action。保留 message_id、dont_ask_again、完整結果；literal intent 先 replay，才固定完整 session/prompt。 |
| interrupt | MCP/CLI/RPC/HTTP 共用 `session.interrupt`；no-key projection、固定 ID、lost-ACK readback | 保留原語意與回歸；不是其他 legacy 已完成的證據。 |
| permissions | `lifecycle.session_set_permissions` 的 policy/confinement、每個 channel 的 task FrameGuard | 下一片 `session.permissions`；operate scope、固定 incarnation、逐 channel receipt，處理下述 deferred identity。 |
| approve_pending／deferred raises | 每次 answer/raise 經同一 service/coordinator gate | 固定每項 session、prompt、mode；answer 與 raise 分開 effects，partial receipt 不重新挑下一個 prompt。依賴 answer/permissions。 |
| client-resume | send 內部準備；task binding/version/confinement gate；不是新 send command 的 effect frame | 不是另一個 public task.resume。保留 resume 與 send 的不同證據，未知結果不得推論新 prompt 已送。 |
| start／relay／failover／fanout | creation policy、start claims、confinement；task failover 只接受原 coordinator 發出的 authority | durable parent/children、固定 IDs/plan/text/selection、所需 start/operate scopes；不另做 task queue。fanout 保留 confirmed all-success stop-only retirement，worktree 交 reviewed cleanup。 |
| worktree merge/remove／record-verification | manual/unknown/owned 拒絕；驗證紀錄不能取代 task trusted verifier | canonical actions、固定 Git binding、每個外部 effect 與 readback；本修正停用 unsafe legacy remove，改用已存在的 reviewed cleanup，能力仍留 backlog。 |
| work_submit/pause/resume/mark_stage；task_send/verify/request_ted/reconcile | 已走 ActionDef＋原 task commands/coordinator；task-scoped／command capability | 保留原能力。admin-only `task_session_control` 是內部 compatibility transport，不是公開 scope-token bypass。 |
| checkpoint、integration、projects/items、Delivery、artifact/capture、cleanup.apply、operation controls | 已走原 OperationService、各 action scopes／預覽／回執 | Task-owned cleanup 仍須 Task Service finalize contract；不可因 terminal 就解除保護。 |
| token issue/revoke、reconcile capability issuance、import-bat、serve | 明列的管理憑證／本機初始化例外 | 不包成 BAT action；不啟第二個中央 authority。 |

入口依據：[MCP](../../src/bat_agent_connector/mcp_server.py)、[CLI](../../src/bat_agent_connector/cli.py)、
[ActionDefs](../../src/bat_agent_connector/api_actions.py)、[task actions](../../src/bat_agent_connector/task_actions.py)、
[coordinator gate](../../src/bat_agent_connector/task_control.py)、[TaskCoordinator](../../src/bat_agent_connector/task_core.py)。
基底 operator-profile MCP 與 CLI 的其餘 direct writes 不查 API token scopes；principal-only MCP 不註冊這些工具。
這是待收斂的 compatibility 邊界，不能把已有低階 task gate 稱為缺失，也不能宣稱全入口已統一。

## Permissions 的固定合約

後續 #59 已加入[固定 bulk approval](bulk-approval.md)：公開 MCP/CLI/HTTP 共用
`session.approve_pending` parent、原 answer／permissions children、固定 prompt 與 partial receipts。
沒有選定 preview 的 apply 拒絕，歷史 deferred flags 與舊內部 raise 路徑仍不啟用。
本表上方保留原基底盤點；此進展不代表 start／relay／fanout 或 task cleanup 已完成。

本輪接續追蹤 [#57](https://github.com/TED-H-com/bat-agent-connector/issues/57)，實作與回復細節見
[session-permissions.md](session-permissions.md)。以下保留接手時的需求與舊問題；新路徑不產生
deferred flag，歷史 flag 及 legacy bulk-raise apply 在任何 BAT effect 前拒絕。

`session.permissions` 使用 operate scope，target 為 host/session，params 只收 mode，preconditions 可含
control_version。相容入口保留 confirm、local read-only/tier，使用自己的 principal；daemon 不可用時沒有
direct fallback。沿用 interrupt 的 named-key replay→解析→原子保存固定 ID/incarnation、no-key sentinel/null projection。
不要暴露內部 force、FrameGuard、task ownership 或 callback 參數。

重用 lifecycle 與 coordinator；Claude mode、Codex sandbox/approval 是各自有 intent/receipt 的效果。
每個 frame 都重查原 task/version/confinement。Codex 第一個設定成功、第二個結果未知時，不能重送整組或
宣稱完整成功；現有 task permissions 沒有足夠 readback 時維持 uncertain，仍由原 command reconciliation 處理。
Registry execution options 只在效果已證明後更新，保留原 task command linkage。

**Deferred identity 不能遺失：** 基底 [lifecycle.py](../../src/bat_agent_connector/lifecycle.py) 的
`session_set_permissions` 在 Claude streaming 時先寫 `permission_raise_pending=mode` 再拒絕；
`_raise_deferred` 後來把它當新呼叫，沒有原 actor/operation/control-version。只包一個 failed operation
會讓後續 approve_pending 在 operation 之外執行舊意圖。可先讓 durable permissions 不產生此 legacy flag，
idle 後由使用者以新 key 明確重試；或保存並接續同一 bound operation。不要新增另一個 task queue。
既有 legacy flags 的遷移／拒絕與 bulk approve 的固定 prompt 契約須另明列，不暗中以新 incarnation 代替。

## Legacy remove 的 consumer 缺口

基底 [orchestrate.worktree_remove](../../src/bat_agent_connector/orchestrate.py) 只看選中 sid 的 activity；
idle predecessor 的 worktree 可能仍由 active failover successor 使用。`resource_policy.authorize_session`
會檢查 cleanup reservations，但不等於 [cleanup.py](../../src/bat_agent_connector/cleanup.py) 的完整
live-consumer／command／operation／integration-preview proof。`discard_uncommitted` 或 `allow_unmerged`
也不能授權刪除其他 session 正在使用的目錄。

**本次處理：** 保留 confirm/tier、manual/unknown policy 與 Task Service ownership 檢查，之後回
`LEGACY_WORKTREE_REMOVE_DISABLED`，指向 `cleanup_preview`／`cleanup_apply` 或
`batc resource-cleanup preview/apply`。在 `_wt_status` 可能 rehydrate 之前拒絕；全部 discard/unmerged/
delete-branch 選項均不能恢復舊寫入。不自動建立 preview、operation、journal，也不自動 apply。
Read/status、merge、start rollback 與既有 reviewed cleanup 不變。

原 consumer-guard prototype 需要另一個 read-only journal adapter、嚴格 runtime projection 與
final-frame read-channel 擴充，仍未補齊 reviewed retention/receipts，因此未採用。這次只關閉不安全的
相容刪除路徑；未重造 cleanup authority，也不宣稱已交付 `worktree.remove` durable action。
後續收斂該名稱時須接中央 reviewed cleanup 的完整 dependency/retention/receipt 合約，不能復原 raw helper。

BAT GUI／外部 client 不參與 Connector 鎖，不能把既有 readback 說成已阻止所有外部 workspace race。
Retained-content restore 仍是可選 backlog，不是已存在的工具
或完整產品交付 gate；operations-unification 的歷史清單不可用來宣告其可呼叫。
