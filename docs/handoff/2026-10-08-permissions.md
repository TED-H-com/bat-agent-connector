# 共用產品：durable permissions 交接

下方 commit 引用使用重寫後公開歷史中保留的對應來源；CI 與審查結果仍描述當時的執行，不代表對重寫後 commit 重新驗證。目前驗證及下載方式見[實作狀態](../product/implementation-status.md#目前續作)。

接續 [#56 的交接](2026-10-08-shared-product.md)；本輪追蹤
[#57](https://github.com/TED-H-com/bat-agent-connector/issues/57)。基底 main
`da66c545120c1b5fb2703277ae6c0e180c309c46`。兩份計畫仍是同一產品，Tauri 與 browser
共用 frontend；Python authority、人工資源唯讀、不做 Hub importer 的決策不變。
候選完整 SHA、最後 exact-head CI、peer review、merge 與 外部安裝版本 以 #57 關聯 PR
及收斂留言為準。以下局部驗證不代替整組 CI，也不代表 installed/live 驗收。

## 本輪行為

`session.permissions` 使用原 OperationService／TaskCoordinator。HTTP 要求明確 mode；
legacy MCP／CLI 保留原預設 mode、confirm／tier、caller principal 與 no-key 語意，新增 key／
control-version。Claude mode 與 Codex sandbox／approval 分別保存固定 intent／literal true ACK。
每個未送 frame 重查原 session/task 身分、pause/version/owner、resource policy、confinement、
host policy 與 Claude 正面 idle evidence。Rate limit 與原 actor 的逐 frame audit 保留。

Sent error／lost ACK 不以相符 metadata 補證，也不重新送 setter；Codex 第一個 ACK 不代表整組完成。
重啟只續做尚未建立 intent 的後續 frame；所有 ACK 已保存時，只完成原 command／registry 的
本機 bookkeeping。Cancellation 不阻止已證明外部效果的 receipt 完成，也不授權新的 frame。
Task state 只有原 command 留下且仍完全符合的 uncertainty proof 才可還原；後來的 pause、
version、binding 或 registry policy 不被覆寫。詳見 [完整合約](../design/session-permissions.md)。

BAT 的 true ACK 只表示接受設定，不證明 live SDK／OS enforcement。Claude streaming 明確拒絕，
不排 deferred raise；歷史 `permission_raise_pending` 保留為診斷 evidence，helper 不自動採用
actor/version。Combined `approve_pending` raise apply 在任何 answer／raise 前拒絕；preview
保留，改用逐項中央 answer／permissions。Durable bulk approval 仍是獨立後續合約。

Shared UI 只有正面 managed/provenance、operate scope、host writes 與 action capability 才能使用。
草稿依 backend/principal/完整 session 隔離，保存固定 mode/key/operation ID；unknown reply 重試
原 key，accepted 後查固定 ID。明確 admission refusal 或終態可由使用者另開草稿，預設回 default。
Receipt 檢查 key/action/target/mode，completion event 等待在途 POST 後重新讀取，兩語系一致。

Canonical workflow 為 `2026-10-08.6`，digest
`ffcf9f587a34b9e595a772f750814a37478345cc3bbadda33110b113ded86df6`。
Hermes/Grokbot 由同一 source 產生；package `0.2.4`、API `1`、contract `2026-10-08` 不變。
Installer 仍要 pin 完整 code SHA，不能只比 package version；保留 draft_candidate。

## 審查與局部驗證

- UI `f30d91b96a78fcc5a43facd9c08bb95622e1c3aa` 的三個 peer findings：明確 admission refusal
  鎖死草稿、receipt 未核對原 key、completion event 早於 POST reply 時留舊狀態。
  `aff4955a1fc0ac841b46438c1f118c2851fba3de` 修正並通過獨立複查。
  141 shared UI cases（40 permissions）在 fixture-only `fb052ae` 後通過，runtime 與 `aff4955` 相同。
  8 state tests 先前通過，本輪未變更其 source／tests；兩個 builds／browser drift check 另有重跑。
  後續 `6afd8661e288746ee926f6981b7c134284804f63` 加 confinement admission refusal 的明確 reset；
  六個 targeted safety cases／builds 通過，獨立複查無 findings；完整整合 UI 以最後 CI 為準。
- Actual-central fixture `fb052aed3a18c95d2b277de026fee9b65e3c9a59` 獨立 review 無 findings。
  它使用真 TaskDaemon HTTP/auth/OperationService/journal 與 generated UI；只有 host transport
  是 MockBat。Browser 在真 POST 完成後中斷 reply，驗原 key replay；另驗 policy admission 零 row／frame、
  streaming、明確新 key、accepted reload、Codex partial ACK 在 metadata 改變後仍 uncertain／零重送。
  它不代替 process-restart、Windows installed 或 live provider 證據。
- Backend 首個固定 source `6e86502d50782141c9cbb998607c67b25fd50142` 的 62 個新 permissions
  cases 在 Python 3.13 通過，含實際 journal 重開與 coordinator/operation 兩種恢復順序。
  Follow-up `b1c43703ba903769cc72040f09cc063969af8785` 的 permissions／修正 deferred cases 共 83 通過。
  同 head 的 broader Python 3.13 focused set **906 passed**（4m44s）。
  最後 backend `16ffd88659c3a6d309756883a7b4ed60a342989e` 的 targeted set **90 passed**；
  補 confinement admission 403、needs_attention 全 ACK cancel receipt completion、unsupported engine
  與 admitted task owner（含原本沒有 owner）變動的零 frame 拒絕。
  該 exact head 獨立 peer review 無剩餘 findings。最後 broader focused、整合 fixture 與同一候選完整四版
  CI 結果見 #57；不把前一 head 的 scoped checks 冒稱為最後候選 full。
  `16ffd88` 的 Python 3.10 broader set **913 passed**（4m54s）。
- 整合 `7b4e15001f980b9edec53143246cab3c0d0a2831` 的 actual-central permissions fixture 通過，
  明確取消 source/Python overrides，只有該 checkout 的 source、venv 與 generated assets；前後 worktree 乾淨。
  同 head 的 17 skill/principal/dashboard checks 通過；pytest 對既存 temporary garbage 目錄的清理
  留下三個警告，沒有 assertion failure。其後只有本交接的 evidence attribution 修正，runtime 未變；最後 CI
  仍必須對完整最終 head 執行。未刪除其他工作留下的 temporary resources。
- 保留的開發失敗：初版 44 cases 中兩個拒絕碼被 generic REFUSED 吞掉，actual-central fixture
  同樣抓到 streaming code mismatch；另查出 admission host-policy 變成 502 BAT_ERROR。
  已補明確 runtime code 與 admission 403 regression，未降低 assertions。Root 查出 durable 分支
  繞過原 audit/rate，亦已補回並驗 partial recovery 只新增未送 frame 的 attempt、hourly budget 不豁免。
- 獨立 backend review 在 `6e86502` 重現：guard-read/connect failure 在 `on_transport` 前零 setter
  frames，卻把 step 留成 uncertain。`b1c4370` 以 PERMISSIONS_NOT_SENT 明確區分已知未送，
  task command 拒絕且可由新 key 再試；另驗 task／standalone、connect／guard-read 與四種錯誤。
  真正 process crash 只有 intent、沒有邊界 evidence 時仍保守，不由零 mock frames 推論可重送。
- Broader 3.13 首輪到 541 passed 後有三個 test-only field mismatch：更新的 historical deferred
  assertions 誤讀 `code`，既有診斷欄位是 `error_code`。修正欄位後須重跑；該輪不是完整綠燈。
- Repo generator／Ruff／secret scan 另驗；generic skill validator 不認既有 top-level version。
  保留 installer 相容欄位，實際版本/digest 由 repo contract tests 驗證；暫存移除該欄位的 shape check
  不冒稱原 validator 直接通過。GitHub Codex bot quota 已耗盡，使用獨立本機 Codex peer review。

Desktop CI 現在跑七組 actual-central fixtures：central、cleanup、artifacts、delivery、observation、
capture、permissions。Merge gate 仍是完整候選 Python 3.10–3.13、Windows/Linux native/package CI。

## 下一步與環境

剩餘 R01 bulk/start/orchestration、R05 B2 managed capture/accept 與跨 host Git、native file/save/download、
Task Service integration/cleanup、Rust Fleet parity 都留在共用 backlog。Windows installed/WebView/tray/
credentials、逐 host confinement、同 RC 的 46 項驗收仍缺；Linux production distribution gate
[#53](https://github.com/TED-H-com/bat-agent-connector/issues/53) 保留。

本輪沒有真 host/provider writes 或安裝。共用核心由單一整合者處理，完整本機測試依序執行。
