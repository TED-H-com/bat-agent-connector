# 共用產品續作交接

下方 commit 引用使用重寫後公開歷史中保留的對應來源；CI 與審查結果仍描述當時的執行，不代表對重寫後 commit 重新驗證。目前驗證及下載方式見[實作狀態](../product/implementation-status.md#目前續作)。

接續 [前輪整合](2026-10-08-tauri-v2.md)；追蹤 [#55](https://github.com/TED-H-com/bat-agent-connector/issues/55)。
產品負責人澄清 v1/v2 是同一產品、不同 UI 設計修訂，共用功能 backlog。Tauri 介面、中央 Python authority、人工唯讀、排除 Hub importer 的既定決策保留。
最新 exact-head CI、合併與 外部安裝版本 證據在 #55 關聯 PR，不能把以下個別分支結果當整組 full。

## 已整合來源

- `041d907bca87ff886f4be4727be7d65609b566ad` + `7440a5832157de2197c0df5d7307c306c0e56a18`：legacy send/continue/answer 共用中央 actions，完整 session/prompt 綁定、caller token、no-key、完整結果與拒絕理由。MCP 需自身 token；CLI 保留明確 token 優先的既有本機 admin 相容模式，agent 不借此升權。
- `d77643467b8c3f20178447f29b2a2970b928be71`：B1 共用 capture UI、受限 native preview route、scope/backend 隔離草稿與固定 key/readback；來源是相對遠端路徑，不是本機 chooser。
- `2b2f4d56bf95c658959e023c24174cd8b5019fe0`：停用 unsafe legacy worktree remove，指向 reviewed cleanup。原 confirm/tier/manual/task gate 保留，全部 override 也零 rehydrate/remove。
- Canonical workflow `2026-10-08.5`；digest `88ea453c65513563ed6af392b30a8360b322b6273e6a8ac0e5a2bbce91e029f7`。同一 source 生成 Hermes/Grokbot，installer pin 要完整 code SHA，不能僅對 package `0.2.4`。

## 驗證與保留的失敗

- Capture 最終整組 101 shared UI、8 state、21 Linux Rust、fmt/Clippy/release check 與真中央 temporary-source fixture 通過。讀回 binary artifact，manual bytes/index/refs 不變，零 BAT writes／推論專案歸屬。獨立審查 exact head 無 findings。
- 舊 UI full 的 100 pass／1 timeout 是不可用 capture 過早建立 hidden summary；已修為 positively manual + capability 後才掛載，完整 101 重跑通過。
- Controls `041d907` broad focused：3.10 591 通過；3.13 590 pass／1 既有 verifier entered.wait 五秒 timeout；未改 source 的 12-case family 在 2.42 秒通過。沒有把 retry 稱為 full 綠，也沒有提高 timeout。
- 兩個舊 MCP fixtures 缺 token 且同時提供兩種 pending prompts；改成自身 caller token／唯一 prompt，保留 task command assertions 並新增 actor/binding assertions。
- Peer P2 是 compatibility receipt 漏掉已保存的 status reason，導致 operate-only caller 看不到拒絕指引；`7440a58` 補 projection 及真 CLI/MCP same-key replay，controls+interrupt 154 案例在 3.13/3.10 均通過。
- Legacy removal 基底 regression 已重現 active successor 的共用 worktree 可被刪。Focused 3.13 112 pass；新增 canonical apply assertion 曾誤期待 retained receipt，實際 API 正確回 PREVIEW_BLOCKED；修正 assertion 後兩版本 exact case 通過。沒有改 canonical cleanup production code。
- Root 整合另驗 17 skill-schema/principal/dashboard cases、generated browser/skills、Ruff、secret scan。Generic skill validator 不認既有 top-level `version`，保留 installer 相容欄位；repo generator/contract tests 驗實際版本與 digest，移除該欄位的暫存 shape check 另通過，不冒稱原 validator 直接通過。

Desktop CI 現在跑 `test:central/cleanup/artifacts/delivery/observation/capture` 六組 actual-central fixtures，仍是 MockBat/temporary Git，不是 live acceptance。Python 四版 full、Windows/Linux package/native CI 以同一 PR head 為 merge gate。

首輪整組候選 `3f07d60` 的 Linux CI：101 UI 與 central fixture 通過，但 cleanup fixture 的 operation 回
`INTERNAL/KeyError`。這是 controls 整合造成的 production regression：`OperationService.create` 把
所有 admission params 複製，遺失 cleanup admission 寫入的 server-only `_accepted_authorization`。
修正保留原 prepared params 的 admission/persistence 語意；只有 private legacy answer 的固定 prompt
需要獨立 resolved overlay。此失敗與修正後 exact-head CI 證據均保留在 #56，沒有把 fixture failure
當作單純 timeout 或降低 cleanup assertions。
同一舊 head 的 Windows package CI 與其餘四個 actual-central fixtures 通過；確認 regression 後停止
尚未完成的 Python matrix，避免繼續測已知壞版。舊 matrix 是 cancelled，不是 full pass；修正後候選仍須四版全綠。

## 下一步

1. 依 [legacy mutation audit](../design/legacy-mutation-audit.md) 補 `session.permissions`：Codex sandbox/approval 各自 durable receipt；deferred flag 不能遺失原 actor/operation/control version。不可整包一個 step 後讓舊 flag 另行寫入。
2. R05 B2 managed capture/accept、C 跨 host Git；native file/save/download、Task Service integration/cleanup、Rust Fleet parity 仍在同一 backlog。
3. 固定下一候選 code/skill/installer pins，維持唯一 owner。既有 installer 可做 verify-only/mock 驗證；本輪沒有真安裝、host/provider writes。
4. Windows installed/WebView/tray/credentials、逐 host confinement、同 RC 的 46 項 I/L 驗收仍缺；Linux production gate #53 保留。

共用核心由單一整合者處理，完整本機測試依序執行；本輪使用獨立 peer review。
