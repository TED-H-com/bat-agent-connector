# Better Agent Dashboard／Connector 實作狀態

## 2026-11-06：外部對話關聯的桌面傳輸候選

分支 `fix/external-history-desktop-parity-20261106` 以 `d78cfc70` 為基底，
接續 E08／E10、EC-1.1 T5：既有 HTTP／MCP 的固定摘錄預覽、摘錄讀取、Work 關聯
清單／詳情及來源反查，現在也可通過 Tauri 的受限 bridge。
Rust 只驗證路徑、精確欄位、固定 ID／digest、文字範圍及分頁上限；
Python 中央仍負責目前來源授權、workspace membership、版本、關聯與 durable operations。
沒有新增 schema、第二個排程器、原生 CLI 控制或關聯 UI。

已通過相關 Python 測試 158 項、Node 測試 581 項、ruff、Rust 格式檢查與公開內容檢查。
Rust 1.98 對實際 bridge 驗證程式與測試的獨立編譯有 58 項通過，包含新增的兩項測試；
這個隔離編譯只涵蓋請求驗證，不能替代整個 Tauri binary 的建置。
完整原生測試因本機缺少 GTK／WebKit／DBus 開發套件而未完成。
Python 3.13 完整五分片共 9,964 項：9,866 passed、84 skipped、14 failed。
Collection manifests 已確認 434 個檔案、無遺漏或重複；原完整檢查仍記為失敗，
不因後續局部重驗改寫原結果。14 項失敗全在未修改的 `test_fleet_bootstrap.py`：
主機測試目錄的上層具有群組寫入權限（0775），被原有 `_secure_path` 正確拒絕。
相同程式與整個該模組改以隔離容器的安全路徑掛載磁碟暫存資料後，44 項全部通過；
沒有變更主機共享目錄權限、產品安全檢查或 fixture。容器與測試資料已移除。
84 項略過依原有平台、選跑整合、具名 CLI 版本或已移除能力的規則保留。
更早的測試曾因暫存路徑超過 Unix socket 長度限制失敗；縮短磁碟暫存路徑後
改跑上述完整五分片。原失敗與中止紀錄保留，沒有改變產品逾時或減少覆蓋。

狀態：已在分支實作，尚未合併、發布、安裝或實機驗收。
E00–E11、R0–R5、A01–A40 與 PM v1.2 其餘待辦維持原狀。

The unmerged E08/E10 candidate admits existing fixed-history relationship reads
through the restricted desktop transport. Central authorization and durable
operations remain authoritative. Focused Python tests, all Node tests and the
isolated Rust request-validator tests passed. The full Python run selected all
9,964 tests without gaps or duplicates: 9,866 passed, 84 skipped and 14 failed.
All failures came from unchanged Fleet bootstrap tests refusing group-writable
temporary-directory ancestors. The unchanged module passed all 44 tests with a
disk-backed temporary directory mounted at a secure container path; the original
full-run failure is retained. No host permissions or product guards were relaxed.
Missing Linux native development libraries prevent a full Tauri test run.
No release, installation, native CLI execution or live acceptance is claimed.

## 2026-11-05：Connector 0.2.16／Desktop 0.1.12 發布準備

2026-11-05：#502 已合併為 `53171768`；`release/0.2.16` 正準備 Connector 0.2.16／Desktop 0.1.12 發布。
修正 v0.2.12 受管理主機每小時更新的 `installed_source_mismatch`，將 wheel 來源證明移至
`bat_agent_connector/skill/build-info.json`，並加入 `install_connector.py --repair-managed [--dry-run]`
修復遭 `already_update_managed` 拒絕的受管理安裝。v0.2.12 主機將於下一次每小時計時器自動更新。
發布來源與 CI 以 `release-manifest.json` 記錄為準；發布前仍需核對最終來源、CI 與套件 digest。
本次僅為發布準備，尚未發布、安裝或新增實機驗收；已發布穩定版仍為 0.2.15／0.1.11。
PM v1.2、E00–E11、R0–R5、A01–A40 其餘未完成範圍維持開放。

November 5: #502 is merged as `53171768`; `release/0.2.16` is preparing Connector 0.2.16 / Desktop 0.1.12.
It fixes hourly managed-update provenance checks on v0.2.12 hosts and adds explicit managed-install repair.
Hosts on v0.2.12 auto-update on the next hourly timer. Release source and CI are recorded in
`release-manifest.json`; final source, CI and package digests remain publication gates.
This preparation claims no publication, installation or live acceptance. Stable remains 0.2.15 / 0.1.11;
open PM v1.2 and E00–E11 / R0–R5 / A01–A40 requirements remain unchanged.

## 2026-11-05：Connector 0.2.15／Desktop 0.1.11 發布

[穩定版 v0.2.15](https://github.com/TED-H-com/bat-agent-connector/releases/tag/v0.2.15)
於 2026-11-05 11:26:09 UTC 發布 Connector 0.2.15、Desktop 0.1.11 與八個檔案，固定來源為
`82584a66dfac19ff8f1beebc1ca8fbb3b11f6aad`。
該來源的 [backend CI](https://github.com/TED-H-com/bat-agent-connector/actions/runs/42013172939) 與
[desktop CI](https://github.com/TED-H-com/bat-agent-connector/actions/runs/42013173068) 全部通過：
backend 26／26 jobs，Python 3.10–3.13 各 9,940 tests；desktop 18／18 jobs。
八個公開檔案的大小與 API SHA-256 均核對相符，公開 manifest／checksums 與本機驗證內容一致。
`release-manifest.json` SHA-256 為 `29fac0c65aaff0fcd688bf4b4d064c78b0a8b015bd7d9da4c004afb4c298d62d`；
stable updater discovery 也選到此版與相同來源。原始檔案與驗證範圍以發布頁的
`release-manifest.json`、`SHA256SUMS` 及發布說明為準。

#498（`7681c207`）加入 Connector SQLite 背景文字保存、有界回填、固定版本閱讀與
有原訊息版本的摘錄，Dashboard 不必保持開啟。PM 可透過既有已設定路徑，明確選擇保存版本
產生有來源的接續摘要，原 SQL run／receipt 與存取限制繼續適用；不自動呼叫模型、
採納決策或由不完整對話宣告工作完成。未曾讀取且來源已不存在的文字無法補回。
#495 另加入選填、按需的 SSH 主機部署唯讀觀察，不會安裝、重啟或更新遠端服務。
詳見[保存合約](../design/session-retention.md)、[PM 接續](../design/pm-continuation-brief.md)
與[部署觀察](../design/host-deploy-status.md)。

核心升級 fixture 分別從 Connector 0.2.12 的固定發布來源 `14c62830240148b14a4d28a9c428f7f8b5ba8bf3`
與 0.2.14 的 `24f76015776cb3bcc46877c11874dc500627fae3` 建置舊核心，再升級至本版，
核對身分、資料與原操作保留。這是核心升級測試；未執行舊桌面安裝器到新安裝器的完整升級。
Windows 發布接續情境亦未執行，仍需 POSIX 測試 BAT 主機；本版 Windows／兩種 Mac
安裝 fixture 的套件 digest 證據另列於 manifest。
Windows 更新請從系統匣退出後執行 0.1.11 安裝器，保留既有資料。
Linux／Windows 未簽章，macOS 為 ad-hoc 簽章且未 notarize；不宣稱可使用正式桌面簽章更新。

已合併、已發布與實際安裝分開記錄。本版沒有新增 owner 電腦、真 BAT／模型、
實體手機驗收，也不宣稱已重現或解決某台電腦的連線失敗。
圖庫仍固定於 0.2.13／0.1.9 的歷史合成來源，不是新版截圖或實機驗收。
套件 wheel／sdist／Dashboard／managed runtime 的來源一致性檢查通過；
完整離線組裝仍受 Desktop／site-demo producer receipt 缺口與歷史圖庫來源限制，
不能把已驗證的套件子集稱為完整單一候選組裝通過。
本項沿 E04／E08／E09／E10／E11、EC-1.1 T3／T4／T6 交付；
PM v1.2、E00–E11、R0–R5、A01–A40 其餘未完成範圍仍保留。

Stable v0.2.15 publishes Connector 0.2.15 / Desktop 0.1.11 from the exact source
and CI recorded above. #498 adds background saved conversations and explicit PM
continuation from fixed message versions; #495 adds optional host deployment
observations. Both exact-source CI workflows passed, and all eight public assets
matched the verified digests and sizes. Upgrade fixtures build older cores from
pinned published 0.2.12 and 0.2.14 sources; old-installer upgrade tests and Windows
publication continuity remain outside that evidence. Package-subset consistency
passed; missing Desktop/site-demo producer receipts and historical screenshots
still limit full offline assembly. Publication does not establish user
installation, real BAT/model or physical-device acceptance. Gallery provenance
remains at 0.2.13 / 0.1.9; remaining PM lifecycle and E/R/A work stays open.
Earlier preparation and release entries below retain their historical scope.

## 2026-11-05：對話保存已合併，0.2.15／0.1.11 發布準備

[#498](https://github.com/TED-H-com/bat-agent-connector/pull/498) 已合併為 `7681c207`，
合併 tree 與通過完整 CI 的 `48fc3bd8` 相同。Connector 背景將已讀到的 BAT 對話保存至
SQLite，提供固定版本、來源摘錄及明確 PM 接續摘要；詳見[對話保存合約](../design/session-retention.md)。
原 session 消失不會刪除已保存內容，未曾讀到就刪除的來源仍無法補回。
PM 模型摘要由既有明確操作觸發，沿用來源權限與原操作紀錄，不自動採納決策或宣告工作完成。

`release/0.2.15-desktop-0.1.11` 準備 Connector 0.2.15／Desktop 0.1.11，
並以正式 0.2.14 的固定來源驗證實際核心升級。發布前需核對最終 main 的 CI、
套件來源與 digest；目前尚未發布或安裝，也沒有新的真 BAT／模型、Windows owner 或實體手機驗收。
這是 E04／E08／E09／E10、EC-1.1 T3／T4／T6 增量；PM v1.2、E00–E11、R0–R5、A01–A40
其餘未完成範圍維持開放。穩定版下載仍是下方 0.2.14，直到新版正式發布。

PR #498 is merged as 7681c207 with the same tree as fully tested 48fc3bd8.
Background SQLite retention, fixed saved versions and explicit source-grounded PM continuation
are preparing for Connector 0.2.15 / Desktop 0.1.11. Final main-source CI and package
provenance remain publication gates. No publication, user installation or live acceptance
is claimed by this preparation entry; the remaining PM lifecycle and E/R/A work stays open.


## 2026-11-04：Connector 0.2.14／Desktop 0.1.10 發布

[穩定版 v0.2.14](https://github.com/TED-H-com/bat-agent-connector/releases/tag/v0.2.14)
提供 Connector 0.2.14、Desktop 0.1.10 與八個發布檔案；完整來源、套件 digest
及對應 CI 見該版 `release-manifest.json`、`SHA256SUMS` 與發布說明。
#496 整合 #494 的三個修正，並納入主線已累積的 Windows 背景服務、
本機 Connector 恢復、Fleet 監控及原操作查回改善。
新版本修正未連線診斷的本機假 401、保留已設定 Fleet 的恢復入口，並修正資料與日誌目錄說明。
Windows 舊未簽章套件請從系統匣退出後執行新版安裝器，保留既有應用程式資料。

已合併、已發布與使用者安裝分開記錄：本版沒有新增 owner 電腦、真 BAT／模型或
實體手機驗收證據，也不宣稱已重現或消除某台電腦的舊版故障。
完整離線組裝檢查仍受 Desktop／demo receipt 缺口與歷史 gallery 來源限制；
明確的套件子集檢查、原生 CI 與暫存安裝 fixture 各自列明，不混稱完整驗收。
本項交付 E03／E11 恢復改善；E00–E11、R0–R5、A01–A40 及 PM v1.2
其餘未完成工作仍依現有計畫接續。下方保留準備階段及失敗紀錄。

Stable v0.2.14 publishes Connector 0.2.14 / Desktop 0.1.10. Its release manifest
identifies the exact source, asset digests and CI evidence. #496 includes #494
and the accumulated managed-service, Windows and Fleet recovery changes.
Publication does not establish installation on an owner's machine or live BAT/model
and physical-device acceptance. Offline component consistency and native installation
fixtures retain their stated scopes; historical screenshots remain pinned to 0.1.9.
Open E/R/A and PM v1.2 work remains open. Earlier preparation and failure records follow.

## 2026-11-04：Connector 0.2.14／Desktop 0.1.10 發布準備（分支）

依 owner 要求直接推進更新，不再以舊版現場排查作為發布準備的前置條件。
`release/0.2.14-desktop-0.1.10` 包含 #494 的未連線診斷與 Fleet 恢復入口，
以及主線已合併的 Windows 背景程序、服務恢復與 Fleet 監控修正。
Python／桌面版本一起前進；打包升級測試改以正式 0.2.12／0.1.8 的固定來源為起點，
驗證原身分、資料與操作意圖的保留。新版套件仍走未簽章安裝器的手動更新方式。
本機兩份實際核心的 0.2.12 → 0.2.14 升級測試通過，含並行啟動、忙碌拒絕、
原發布操作與資料保留。首次候選 CI 找出 canonical／產生的技能版本尚未同步，
已更新來源版本並重新產生兩個 adapter；該次失敗不列為通過證據。
後續 Intel Mac CI 找出測試用共用鎖的等待競爭：釋放後其他 fixture 可先取得鎖，
原五秒收件期限因而誤判失敗。測試改在既有有界 admission 完成、join waiter 後收件，
保留持鎖時不得進入的斷言；未增加子程序工作期限或改動產品行為。修正後 CI 另行記錄。
目前為發布候選：尚未合併、發布、安裝或實機驗證，不能據此宣稱舊版故障已在現場消失。
E03／E11 的恢復改善不代表 E00–E11、A01–A40、R0–R5 或 PM v1.2 全部驗收。

## 2026-11-04：未連線時的 Fleet 恢復與啟動診斷（分支）

`fix/disconnected-startup-diagnostics-20261104` 從 `a3204616` 接續。
診斷匯出在尚無前端憑證時不再自行新增 capabilities 401；來源標為未嘗試，
真正 HTTP 拒絕仍保留。桌面匯出新增唯讀 native／managed 狀態、錯誤碼與各自讀取時間，
並提供 Python `managed-central/service.log` 的固定位置範本；不匯出私人路徑、身分或錯誤全文。
同時依原生安裝器修正既有診斷與雙語指南的資料庫目錄：`central` 改為 `managed-central`。
首次連線設定尚未完成時，已設定且支援的本機 Fleet 仍能從設定頁讀取與接續原轉換。
重新開啟只查回原紀錄，需使用者按既有按鈕才推進；未設定的 Fleet 維持原首次設定畫面。
本項是 E03／E11 恢復與診斷增量，不代表實機 Fleet 轉換、Connector 啟動或 BAT 連線已恢復，
也不代表 E00–E11、A01–A40、R0–R5 或 PM v1.2 未完成項目已驗收。

This branch adds disconnected-startup evidence without making Connector calls
that cannot leave the client. Native and managed status reads remain independent,
read-only and fenced by connection epoch. Real HTTP 401 responses remain visible.
Existing configured Fleet also remains reachable during incomplete Connector setup,
with explicit continuation of the original migration and no automatic start.
Node 581 and related browser/IPC 138 checks pass (4 declared skips), with both builds and Ruff.
The corrected managed installation paths also pass 32 diagnostic Node checks and
43 diagnostic browser/IPC checks after rebuilding both frontends.
The initial new UI cases used an ambiguous export-button locator; the corrected
cases pass in both locales. Full Python regression was interrupted to include the
subsequently confirmed Fleet recovery fix; its replacement run is in progress. This branch is not
merged, released, installed or live-tested; no private incident files are committed.

## 2026-11-04 續作：操作紀錄完整續頁（分支）

從主線 `dfefdbfc` 接續；前項原操作恢復已由
[#489](https://github.com/TED-H-com/bat-agent-connector/pull/489) 合併為 `8878efc8`，
該來源 PR 與主線 CI 通過。下方 11 月 3 日的分支與本機失敗紀錄保留為當時證據。

`fix/operation-history-cursors-20261104` 修正共用操作紀錄只傳時間續頁、
因而跳過同時間操作的問題，沿用後端已有的時間／ID 配對。
可見結果為空仍保留明確續頁；無效或倒退游標保留已載入結果，等使用者重新整理。
背景刷新保留分頁深度、搜尋、篩選與原操作選取，舊版僅時間的中央服務仍相容。
設計見 [操作紀錄](../design/shared-frontend.md#attention-and-work-updates)。

本項是 E03／E11 的唯讀查找與恢復增量，HTTP／IPC fixtures 涵蓋 A29／A30
原操作查回的介面路徑；E00–E11 其餘工作、A01–A40 驗收、R0–R5 及 PM v1.2
未完成項目仍開放。本分支尚未合併、發布、安裝或實機驗收；正式 Fleet
Apply／detach、本機 participant 與首次設定完整接線不在本項內。

This branch continues from `dfefdbfc`, after original-operation recovery merged in
#489 (`8878efc8`, PR and main CI passed). Shared history now preserves the server's
timestamp/ID cursor and explicit continuation through empty visible pages. Refresh
retains loaded depth, search, filters and a selected original still in the window;
invalid cursors keep prior rows with an error. Timestamp-only servers remain compatible.
This E03/E11 read/recovery increment is not completion of E00–E11, A01–A40, R0–R5 or
open PM v1.2 requirements. It is not merged, released, installed or live-tested.

本機驗證：Node 579、相關瀏覽器／IPC 97、既有後端游標與隔離案例 9 通過；
兩種前端建置、Ruff、公開內容與 staged 秘密掃描通過。完整回歸以固定 PR 來源 CI
另行記錄。首次 UI 執行找出續頁焦點遺失並修正；選取視窗的刷新案例改用正常事件
刷新，避免對手機已被 inspector 遮住的按鈕發送點擊。原失敗／中止紀錄仍保留。

Local validation: Node 579, related browser/IPC 97 and existing backend cursor/
confinement checks 9 passed, along with both frontend builds, Ruff, public content
and staged secret checks. Full regression is recorded separately against the PR head.
Initial UI failures exposed lost pagination focus, now fixed; selected-inspector
refresh is exercised through events, including the mobile modal. Initial failures
and interrupted runs remain in the local logs.

PR #492 的首次網站 CI 另發現主線 #490 已更換截圖工具 import，但公開截圖 manifest
仍記錄舊工具 hash。本分支以同一已發布來源 `437ee6a7` 的獨立 checkout 重新擷取
16 張圖、48 組雙語響應式檢查，更新真實擷取時間與 digest；671 個連結／靜態資源及
網站瀏覽器檢查通過。產品來源、版本與實機驗收宣告沒有改變，原 CI 失敗仍保留。

The first #492 website CI found a stale capture-tool hash inherited from #490.
All 16 screenshots were recaptured from the same published `437ee6a7` source in an
isolated checkout, with 48 bilingual responsive checks. The refreshed manifest records
the actual capture and digests; 671 link/asset checks and the website browser checks
passed. The product source, release and acceptance claims remain unchanged; the initial
CI failure remains recorded.

## 2026-11-03 續作：原連線操作的恢復說明（分支）

從主線 `4986c7e4` 接續 EC-1.1 首次使用與恢復穩定化。
`fix/connection-operation-recovery-20261103` 在既有操作頁區分本機結果未知、
本機步驟完成及共用關聯儲存衝突，提供雙語說明與符合原取消語義的結束按鈕。
查詢與恢復使用原 operation ID；連點只送一次，回覆未確認時先讀回，讀取失敗移除舊控制，
離頁後的晚到回覆不恢復原畫面。原 key、回執、已知設定效果與中央授權均保留。
契約及限制見 [原操作恢復](../design/fleet-configuration.md#original-operation-recovery-in-the-shared-ui)。

本項是 E03／E11 的恢復介面增量；HTTP／IPC fixtures 對應 A29／A30 的查回與重開原則，
不宣告 A01–A40 或 R0–R5 驗收完成。E00–E11 其餘缺口及 PM v1.2 未完成範圍仍保留。
本分支未合併、未發布、未安裝、未實機驗收；沒有新增 Apply／detach 入口，
正式 participant 儲存、執行與首次設定接線仍待完成。下方保留既有歷史紀錄。

This branch continues first-use and recovery stabilization from `4986c7e4`. The shared
operation page explains unknown local results and completed effects whose association
was not saved, using the original read/resume/cancel endpoints. It preserves evidence
and prevents repeated in-flight recovery requests. This is an E03/E11 UI increment,
not R0–R5 acceptance or completion of A01–A40. It is not merged, released, installed or
live-tested; production Fleet Apply/detach activation and the remaining v1.2 work stay open.

驗證來源為 `4bcfa791`：Node 572、相關瀏覽器案例 53（最後修改後新案例另跑 28）、
相關 Python 案例 169 均通過；兩種前端建置、產物同步、Ruff、公開內容與秘密掃描通過。
完整 Python 3.13 回歸為 **9723 passed、82 skipped、14 failed**，不是整輪全綠。
14 個失敗均在 `test_fleet_bootstrap.py`：磁碟暫存目錄的祖先目錄原為 0775，
因此既有安全檢查拒絕 fixture；暫時收緊為 0755 後，完整回歸結束再整檔重跑 **44 passed**。
沒有修改後端、安全檢查、測試斷言或等待條件；原權限已還原，任務測試暫存已清除。
更早一次完整執行因 Unix socket 暫存路徑過長而中止，改用短的磁碟 basetemp 後重跑；
原中止與失敗輸出均保留於本機驗證紀錄。這些本機結果不代表 native／實機驗收。

Validation at `4bcfa791`: Node 572, related browser 53 (28 new cases rerun after the
final change), and related Python 169 passed; builds, generated assets, Ruff, public
content and secret checks passed. Full Python 3.13 finished with 9723 passed, 82 skipped
and 14 bootstrap failures caused by group-writable scratch ancestors. The entire affected
file passed all 44 tests after temporarily correcting those permissions; original modes
were restored and test scratch removed. The full run remains recorded as failed, and the
earlier Unix-socket path-length interruption is retained. No backend or test rules changed.

2026-10-23：[最新主線進度](progress-2026-10-23.md)記錄至 `5f49e712` 的 55 個新增合併 PR，
並列出 Fleet Apply 與本機參與者尚缺的能力。沒有新發布；預覽版仍為 Connector 0.2.13／desktop 0.1.9
（`437ee6a7`），穩定版仍為 0.2.12／0.1.8。新增合併均未發布、安裝或實機驗收；下方保留歷史紀錄。

October 23: the [latest main progress](progress-2026-10-23.md) records 55 additional merged PRs through
`5f49e712` and the remaining Fleet Apply and native-participant capabilities. There is no new release:
preview remains Connector 0.2.13 / desktop 0.1.9 (`437ee6a7`), stable 0.2.12 / 0.1.8.
The additional merges are unreleased, uninstalled and not live-tested; the entries below remain historical.

2026-10-22：[最新進度與發布紀錄](progress-2026-10-22.md)列出主線至 `793c6541` 的合併內容。
已發布預覽版為 Connector 0.2.13／desktop 0.1.9（`437ee6a7`），穩定版仍為 0.2.12／0.1.8。
#399–#403 不在預覽版內；本預覽版尚未安裝或實機驗收。下方日期紀錄保留歷史狀態。

October 22: the [latest progress and publication note](progress-2026-10-22.md) records merges through
`793c6541`. The published preview is Connector 0.2.13 / desktop 0.1.9 (`437ee6a7`);
stable remains 0.2.12 / 0.1.8. The preview excludes #399–#403 and has not been installed or live-tested.
The dated entries below retain their historical status.

## 2026-10-19 發布與接續狀態

本輪開工核對主線 `5fa1d1552945988394f0d3fe41db380a831717a5`，已包含
#315 路線喚醒、#316 本機服務復原、#321 草稿測試同步及 #319 首次設定／主機狀態。
它們不在下列 `14c62830` 安裝包；舊交接的「待合併」是歷史狀態。
依產品負責人最新回饋，接續順序改為
[穩定化與首次使用](experience-convergence.md#2026-10-19-優先順序實機穩定化與首次使用)。
本工作分支將預設專案導覽收斂為四個日常入口，其他管理頁按需展開；
分支改動尚未合併、發布、安裝或實機驗收。

接續的 Fleet 增量將既有檢查整理為每台機器的狀態、下一步及可展開的網路／身分／
服務／工作區證據。讀取失敗、過期、監控停止或選擇尚未套用時，不再顯示舊的就緒
結果；初次讀取失敗也可直接重試查詢。背景刷新保留草稿、詳細資料與焦點，進階的
連線方式與開機啟動設定預設收合。沿用原 IPC 和操作回執，不新增遠端修復寫入。
對應 E03／E11 的可用性與恢復、A29／A30 的既有不重送回歸及 A39 的介面準備；
不代表 A01–A40 或 R0–R5 的實機驗收完成。完整加入機器精靈、安裝角色選擇、
統一工作能力修復與同版本日常實測仍待完成。設計及驗證邊界見
[Fleet 共用檢查](../design/desktop-fleet-native.md#shared-machine-checks)。

已發布 [v0.2.12](https://github.com/TED-H-com/bat-agent-connector/releases/tag/v0.2.12)：
Connector **0.2.12**／desktop **0.1.8**，固定來源
`14c62830240148b14a4d28a9c428f7f8b5ba8bf3`（#314）。
此版修正 Windows SSH executable 路徑分隔符比對；該來源的
[後端 CI](https://github.com/TED-H-com/bat-agent-connector/actions/runs/39201873778) 與
[桌面 CI](https://github.com/TED-H-com/bat-agent-connector/actions/runs/39201873842) 已通過。

前一正式版 [v0.2.11](https://github.com/TED-H-com/bat-agent-connector/releases/tag/v0.2.11)
固定於 `d7876d64966e7e558a9232af58758568372106c7`，包含 #306 的工作區 PM runtime、
人工序列輸入唯讀觀察與接續讀取修正，以及 #309–313 的設定分片、automation facade、
固定 tag 安裝與既有 uv tool 安裝納管。#305／#307 的修正經 #306 整合，沒有另行合併。

發布不代表每台已安裝或完成一般登入驗收。下方 0.2.10／0.1.6 的部署讀回保留為
10 月 18 日歷史證據；本次文件更新沒有新一輪逐主機安裝回執。Classic 仍為預設。
真 BAT／模型兩輪、實體手機、一般登入 Tauri、Native／AHP 路線及 PM v1.2 剩餘範圍
仍按原 E／UX／R／V／K 編號接續，不重做已合併 Work loop。

完整測試另依精確來源記錄：[#283 最新補記](https://github.com/TED-H-com/bat-agent-connector/issues/283#issuecomment-6262388027)
確認 `0bf36674` 的 Python 3.13／3.10 各 7218 passed、60 skipped，
`2c2b3a8f` 的 Python 3.13 為 7311 passed、60 skipped；後者 3.10 原紀錄未完成。
原停滯、失敗與未完成紀錄保留，不能移用為本版完整本機測試或根因已修復的證據。
#308 的暫存路徑保留修正、#315 的路線喚醒及 #316 的本機服務復原均不在上述發布來源內；
各候選的整合與驗證狀態以其 PR 為準。以下日期段落保留較早快照。

## 2026-10-18 PM 判讀與外部歷史交付

#300／#301 已合併，發布來源 `b44a83abc7d548747983298b8290ab1d544898d6`，Connector 0.2.10／desktop 0.1.6。
七台 Connector 經既有 updater 更新、三台 Linux desktop 已安裝並讀回。
總覽可明確請 PM 建立附固定來源與歷史的判讀；資源頁可讀取另行授權的本機 Codex／Claude 對話檔案，不取得控制權。清單先載入摘要；尚未送出的佇列指令可以留成 Idea。 #292／#293 本次已納入套件。Classic 仍為預設；真 BAT／模型、PM 判斷品質、
實體手機及一般登入桌面仍未驗收。外部歷史只讀不表示已完成跨 CLI 執行與歸因。
完整證據及剩餘工作見[本次交付進度](progress-2026-10-18-delivery.md)。以下保留較早快照。

## 2026-10-18 討論接續與手機連續操作預覽

#287–291 已合併，發布來源 `001662eba19cc43a1a5d2085d1f4f9a6fe4a8457`，Connector 0.2.9／desktop 0.1.5。
七台 Connector 經既有 updater 更新、三台 Linux desktop 已安裝並讀回。
Discussion 面板保留上一頁／下一頁位置，回到前景先核對 server 狀態，手機輸入區配合可視畫面。工作設定檔在開始前顯示該路線的下一輪限制；PM 可明確建立有來源的接續摘要，供後續討論沿用。 Classic 仍為預設。上述已實作部分不重做；#292 的清單讀取優化與合成量測已另行合併，尚未納入本版套件。接續來源同步、
跨路線能力與真實 PM 判讀。真 BAT／模型與實體手機／一般登入桌面仍未驗收。
完整證據及剩餘工作見[本日接續進度](progress-2026-10-18-continuity.md)。以下是較早快照。

## 2026-10-18 手機導航預覽

#285 已合併為 `1a5834fe6a42d6cb47048c1bdfc5be8308620a8a`，發布 Connector 0.2.8／desktop 0.1.4。
Workbench 新增手機全螢幕工作清單，返回時保留搜尋與位置，成果面板支援瀏覽器上一頁／下一頁。Classic 仍為預設。 七台 Connector 已沿既有更新服務取得新版，三台 Linux desktop 已安裝；
來源、套件與原程序持續性已讀回。真實 BAT／模型、實體手機和一般登入桌面仍未驗收。
本輪檢查與原失敗見[10 月 18 日進度](progress-2026-10-18.md)。以下保留前版快照。

## 2026-10-17 Connector 自動更新預覽

[#282](https://github.com/TED-H-com/bat-agent-connector/pull/282) 已合併為
`e94ea547051a059bd040c44a23aff0e24bcd2294`。
[0.2.7 預覽版](https://github.com/TED-H-com/bat-agent-connector/releases/tag/v0.2.7-preview.20261017)
包含 Connector 0.2.7 與 desktop 0.1.3，來源、套件與檢查結果見發布清單。
Linux 使用者安裝可由既有部署收據啟用自動更新，每小時檢查已發布版本；
僅切換下一次啟動的套件，保留既有 MCP、central、BAT 程序與工作資料。
用法與回退見[自動更新指南](../connector-updates.md)。

EC-1.1 手機、來源同步與長討論增量已納入原計畫編號，並非全部已實作。
真實 BAT／模型兩輪、實體手機與一般登入桌面驗收仍分別待完成。
原完整測試的逾時、停滯與主機前提不足紀錄持續保留；分片 CI 通過不改寫原紀錄。
七台 Connector 已安裝 0.2.7 並啟用 preview 更新排程，三台支援的 Linux 圖形主機
已安裝 desktop 0.1.3；套件、來源與排程已逐台讀回，既有程序保留。
下方 0.2.6 安裝狀態保留為上一版快照，詳細最新紀錄見[當日進度](progress-2026-10-17.md)。

## 2026-10-17 預覽發布與安裝

[預覽版 v0.2.6-preview.20261017](https://github.com/TED-H-com/bat-agent-connector/releases/tag/v0.2.6-preview.20261017)
已發布 Connector 0.2.6、desktop 0.1.2，固定來源為 `f31e7964c627e4b6d3af2e2370e7a38c64a20e25`（#276–278）。
六個套件及來源清單、SHA-256 共八個檔案已核對；最終來源完整 CI 與四平台建置通過。
已完成版本目錄安裝及入口／套件內容／commit 讀回，既有 MCP 程序保留至正常重新連線。
Linux 安裝後隔離原生診斷一次通過，先前兩次啟動失敗獨立保留追蹤；一般登入桌面與真實 BAT／模型驗收仍待完成。
私人主機紀錄保留於部署清冊；公開證據與剩餘計畫見[10 月 17 日進度](progress-2026-10-17.md)。
下列整合與日期快照保留當時狀態，不以新發布覆寫舊失敗。

## 2026-10-17 主線整合

#276 已於 2026-10-17 合併為 `0daf283c7606289020e0f659bb11e0eaf6aff410`，
保留至 #275 的原提交。完整流程、固定版本檢查與缺口見
[10 月 17 日進度](progress-2026-10-17.md)。下方日期快照保留當時狀態。

整合候選的完整 CI 已通過；本機先前完整測試及工作循環的失敗仍保留，
不因 CI 或單項重跑而改寫。安裝、native 與真實來源驗收尚未完成。
#277／#278 分別追蹤 Fleet probe 測試時鐘與操作來源撤回補強。

## 2026-10-16 體驗收斂方向

Owner 已採用 [EC-1.0 整合決議](experience-convergence.md)，將既有 v1.3／Work-Centric
的優先順序收斂到正式 BAT 工作循環，未改動本文既有底層交付結論。最新開工差額與
候選 head 見 [v1.3 handoff](v1.3-agent-handoff.md)，原編號與未完成驗收保持有效。
這是產品方向更新，不是已安裝或實機通過紀錄。

本頁保留底座與整合歷史。最新 PM 範圍以[採用計畫](pm-v1.2-plan.md)及
[PM 狀態](pm-v1.2-status.md)為準；本輪 PR 與證據見[10 月 16 日進度](progress-2026-10-16.md)。
前次原始測試及阻擋保留在[10 月 12 日報告](progress-2026-10-12.md)與[歷史交接](../handoff/2026-10-12-pm-v1.2.md)。
M4 #116 已於 2026-10-15（UTC）以 `d04eed37` 合併至主線；先前候選紀錄見[2026-10-13 候選驗證紀錄](../handoff/2026-10-13-m4-refresh.md)。

## 2026-10-16 前次產品快照（截至 #205）

固定產品來源：已合併主線 `265026d4c1fc2bc04a9aaa727d566c1a2906b256`（#205）。
負責人已採用 v1.3 closed-loop 修訂計畫，文件為 `docs/product/v1.3-closed-loop-plan.md` 與 `docs/product/v1.3-agent-handoff.md`，由 #182 追蹤。
已合併的閉環部分是開始一項工作（`work.start` 與其「開始工作」面板）、該嘗試的固定成果讀取，以及看板工作詳情中的唯讀成果區（E03 part A）。尚未合併：對同一工作的第二次指示（P5）、一步採用並開始（P6）、依要求讀取目前變更與逐 commit 差異（E03 part B）、工作 profile 與 readiness（E01）、MCP／API 迴圈對等（E10），以及端到端閉環。尚未觀察到真實的 BAT 開始、追加或成果；沒有安裝或實機驗收。
Python Connector／Task Service 是唯一中央權威；Web／Tauri 共用 `desktop/src`。

| 主線增量 | 人現在可以看到或做什麼 |
| --- | --- |
| #132／#134／#139／#133 | 即時總覽、來源討論、採用、原問題解決回報、需求追溯、登記、時程、相依、資源、看板與唯讀人工工作階段（前端 #133 於 2026-10-14 以 `cb7e9f42` 合併）。 |
| #154／#157 | 搜尋討論、按需開啟討論資料；篩選登記與看板，開啟共用詳情。 |
| #147／#150／#149 | 開啟治理、會議、後續追蹤、維護及工程工具；沿用原來源、草稿與操作恢復。 |
| #151／#158 | 查看進度仍缺的驗收、比較固定報告、閱讀需求詳情；資料未變的刷新保留列位置與焦點。 |
| #153 | 先貼 BAT 連線網址並驗證儲存，再查看工作階段；儲存庫與驗證命令依工作需要設定。 |
| #156／#161 | 查看獨立工作目錄、檢查點、執行與驗證詳情；發布來源預覽保留理由欄文字與焦點。 |
| #152／#162／#155 | 工作階段只在需要處理時顯示狀態，並說明歷史不完整的原因；PM 面板每次失敗只顯示一則訊息；匯出簡報開啟並聚焦匯出工作。 |
| #171／#169 | 設定 → 關於顯示版本與 commit，可匯出診斷資訊；唯讀工作階段列出三條路徑；設定 → 代理提供 PM 資格檢查。 |
| #116 | M4：原完成回合的生命週期證據、清冊重新查核、共用的需求／DoD 評估，遺失回覆以原 key 查回（Connector 0.2.6／desktop 0.1.2）。 |
| #174／#175 | PM 工作區只讀取開啟中的視圖，其他視圖在開啟時才讀取；Follow-up 請 PM 評估在實際 Connector 上不再失敗。工作區重新整理、生命週期、已採用計畫預覽與報告匯出失敗時也只顯示一則訊息；建立表單在需要時才開啟；已結束的 PM 請求只留下「新請求」。 |
| #167／#177 | Git 傳輸合約（本機 Git 仍回報無法使用）；連結、專案頁面與更新 feed 指向 TED-H-com。 |
| #179／#180 | 繁中總覽文案、阻擋原因用詞與單行負責人欄位；手機版頁首、工作階段首次執行、訊息讀取失敗只顯示一則錯誤、緊湊列與繁中權限名詞。 |
| #185 | 總覽的「需要關注」與「問題與變更」開啟原紀錄，顯示誠實進度與一個主要動作。 |
| #186 | 其餘面板失敗時只顯示一則在地化摘要，錯誤代碼收在收合的詳細資料。 |
| #188／#189 | PM 提案在對話內審閱、採用與解決，並在每個子操作完成時自行結算，不再固定等待。 |
| #192 | 討論撰寫區可對一般 PM 回覆按「傳送給 PM」；另有「只存筆記」與「整理計畫」。 |
| #191 | 網站連結到由共用 Dashboard 原始碼建置的互動展示（合成、唯讀）。 |
| #193／#195 | 工具 → 工作台預覽：可還原的工作導向版面（今天、專案、工作、資源），含工作、譜系與即時檢視、舊深連結與功能地圖；經典版面仍是預設。 |
| #194／#197 | `work.start` 與 PM 工作詳情的「開始工作」面板。從已審閱的已發布 commit 與已採用計畫，在自己的受管 BAT worktree 與工作階段開始已綁定的工作；含預覽、嘗試與原 key 恢復。面板需要 `work.start` 能力、已設定的 BAT 主機與已綁定的儲存庫。 |
| #196／#205 | 已開始工作嘗試的固定成果讀取（Connector API）。看板工作詳情有唯讀成果區（E03 part A），只讀取，不開始、不送出、不合併。 |
| #199／#202／#210 | 檢視器載入時鍵盤焦點留在檢視器內；設定 → 代理的「再檢查一次」與 PM 表單送出鈕在工作中保持焦點。 |
| #204／#208 | 讀取途中離開時，看板與需求保留捲動位置；專案在重新整理途中切換時，重新整理不會一直停用。 |
| #183／#187／#190 | v1.3 文件與可點選原型。原型維持合成，不向 Connector、BAT、GitHub 或模型發出請求。 |

#198、#200 與 #201 是測試可靠度修正，不是使用者功能。#184 是前一次文件 pin 更新。
即時總覽與固定報告分開。採用、內容核准、需求驗收、合併、部署與 runtime 完成各有證據。
來源撤回移除相關列、計數與詳情；人工資源唯讀；結果未知沿用原請求與 key。

| 層級 | 狀態 |
| --- | --- |
| 分支（branch） | `ux/docs-v13-refresh` 是網站與狀態更新工作線；不包含產品程式變更。 |
| 主線（merged） | 產品來源 `265026d4`，截至 #205。自 `d66f032d` 起再合併 24 項 PR。 |
| 安裝／原生（installed/native） | CI 產出未簽章桌面套件；針對 `fb0863cf` 靜態核對 Linux 套件二進位檔與隨附 Connector 記錄該 commit，但未實際安裝，無安裝驗收。 |
| 實機（live-tested） | 本輪沒有實際 BAT／provider／模型／部署驗收回執。綠燈 CI 與截圖不是實機證據。 |

詳細增量、合約與檢查見 [PM 狀態](pm-v1.2-status.md) 與 [進度報告](progress-2026-10-16.md)。
第 7 輪已合併的部分不再列為待實作；#174、#175、#167、#177、#179 與 #180 已合併至主線。
本機連線網址已由 #153 提供；[本機 Git](../design/local-host.md) 尚未實作，#167 的 Git transport 合約已合併，本機 Git 仍回報無法使用。
[Overview PM 重點判讀](../design/pm-overview-assessment.md)（#143）已依後續 EC-1.1 授權進入程式候選，
包含固定來源、結構化判讀、既有分析路線與雙語卡片／歷史；尚未合併、發布或完成真實 PM 品質驗收。
一般 native coding、AHP 寫入、其餘文件／簡報、更多 CLI、遠端資源收集與交付效率歸因仍待做。
完整工作表見[PM 狀態](pm-v1.2-status.md)；M4 已於 #116 合併，M5 保留歷史候選及檢查，不由本輪重新驗證。

以下為既有桌面／Connector 底座及前次固定來源紀錄，標示日期屬於當時，不能用來推定新的 PM 里程碑已完成。

### 2026-10-10 Issue #59 候選收斂（PR #81）

這版原始碼包含已整合的產品實作；目前套件以 [`main` 的 desktop workflow](https://github.com/TED-H-com/bat-agent-connector/actions/workflows/desktop.yml?query=branch%3Amain)及[同一來源 commit 的檢查](https://github.com/TED-H-com/bat-agent-connector/actions?query=branch%3Amain)為準。選擇目前公開歷史的成功 run，核對平台 job 與安裝 fixture；仍在建置時不宣稱已通過。歷史 PR artifacts 不作為目前下載來源，也不由此推論已正式發行新安裝包。

| 項目 | 候選實作與界線 |
| --- | --- |
| 一般首次安裝 | 平台包包含 Python runtime；首次啟動建立私人資料、自有背景中央及個人身分。驗證 artifact digest、核心檔案鎖及服務身分，重開／並行 clients 沿用同一份帳本。 |
| 平台與生命週期 | Windows 私人 ACL／handle／reparse 檢查與檔案鎖；Mac／Linux 使用對應儲存保護。背景中央獨立於 UI，登入啟動屬於該安裝；升級須由原 owner 同意安全停止，忙碌／unknown 工作不強制終止，禁止降版帳本。 |
| BAT／repository 引導 | 匯入可讀 profile 或手動輸入可信 endpoint／fingerprint／token；明確設定 managed 權限、遠端目錄及已有 SSH alias。GitHub repository 綁定精確 host／workspace；儲存只讀驗證，不推送或派工。 |
| 設定與身分恢復 | 祕密先存為私人不透明參照，durable setup action 固定 config revision；原子保存及同意圖查回。損壞 profile 不使頁面崩潰；失效憑證不重新發出身分或建立另一個 DB。 |
| 驗證命令 | 設定 UI 按精確 Task Service project key 保存 executable／逐行 literal argv／1–3600 秒 timeout；不解析 shell、不在儲存時執行。既有 task／ownership gates 決定後續驗證。 |
| 共用工作介面 | 可調左樹、對話閱讀、未讀／模型偏好、成果連結、固定失敗證據的修復工作與同版技能目錄。技能選取保存 digest，`selected_not_applied` 不代表 runtime 啟用。 |
| 發送／恢復 | 保存精確原操作、訊息與發送回執；歷史 queued 回執不推論現在排隊位置，也未提供 BAT 逐訊息取消。Unknown 回覆保留原 key／資源並先查回。 |
| 既有中央與 Fleet | 已配置外部中央沿用；進階加入須原生確認與另行登錄憑證，自有背景工作保留。Windows Fleet 管連線／啟動，Python 管授權／派工，BAT 執行工作；原有 profiles、Kit ownership 與遷移守衛保留。 |

正常使用方式見中英文 [首次使用](../getting-started.md)／[繁中](../getting-started.zh-TW.md)及 [Windows](../windows.md)／[繁中](../windows.zh-TW.md)；[managed 安裝合約](../design/managed-installation.md)列出來源及自動 fixture。下載時須核對目前 `main` run 的來源 commit 與成功平台 job。

**本次排除範圍：**正式簽章、Mac 公證、正式更新通道、Linux 原生憑證持久儲存。完整 46 項實際環境的人工驗收由使用者負責，不列入 Agent 未完成工作，也不宣稱已通過。原生 BAT merge 的 unknown ACK 保留原操作及資源；獨立人工裁決 API 不在[目前 merge 合約](../design/worktree-merge.md)範圍，不另列交付門檻。

**驗證與整合紀錄：**以對應來源 commit 與檢查頁為準。歷史來源引用已對應到公開歷史中保留的 commit；這不改寫原 CI receipt，也不把歷史 run 視為新 commit 的驗證。以下保留各階段當時的實作紀錄及基線；其中「尚未實作」和「待驗收」描述是歷史狀態，不覆蓋本節的候選實作與最新排除範圍。

## 歷史實作紀錄（保留當時狀態）

### 2026-10-10 專案工作樹與對話工作空間

Issue #79／`feat/project-workspace-shell` 將共用前端改為固定專案工作樹、主要對話與回覆區，
以及同畫面的工作／成果摘要。專案派工直接打開明確綁定的 session；工作項目的已知
session 連結也可並列對話與審閱操作。所有 session 可另依主機／workspace 展開，
不推論專案歸屬；展開狀態、已載入頁數、原草稿與事件 ACK 邊界保留。

頂部保留待處理、管理工具與連線；Fleet／原生能力未刪除。Web 與 Tauri transport 的
實際中央 fixture 已驗證從左樹進對話、成果預覽，再將兩份固定成果送入同一測試 PR。
公開網站與 README 使用這次實際共用介面的合成資料截圖。完整 gates、合併與原生
套件驗證另以候選 PR 為準；此項不宣稱完成自動安裝或使用者真實 BAT 主機驗收。

### 2026-10-10 安裝產品定位與公開文件

負責人明確要求桌面安裝包自動準備整個產品環境，包括持續運作／重連的 Connector 與
點擊開啟 Dashboard 網頁。這是正常安裝路徑；existing central 僅是加入既有環境的進階選項。
[安裝合約](../design/managed-installation.md) 記錄 runtime、身分、owner、瀏覽器登入交接、
登入啟動與跨平台驗收要求。現有套件尚未實作自動 provisioning，不能以文件重包裝代替交付。
README 與網站依此區分產品目標、已交付能力、驗證包與開發試用設定。

### 2026-10-10 E2E UX 審查後的共用介面修正

`fix/ux-audit-followup` 修正 Web 手機登入欄位溢出、表單缺少標籤、首次 session 讀取失敗
卻宣稱人工建立，以及不支援 task cleanup 時的 `null` 文字。未連線時只呈現連線入口；
原生首次設定收起路徑與進階說明，設定連線目標後才呈現 Fleet／Tailscale。本機設定恢復仍可展開，
已配置的 endpoint、actor、credential source 保持可見，原生確認與憑證隔離不變。

分支可輸入 `main` 或完整 `refs/heads/main`，預覽／提交仍固定正規 ref 與 SHA，失敗重試沿用
原意圖。Delivery 可貼 GitHub PR 網址，自動取得 repository／PR 編號；所有讀寫仍送到已驗證
中央，絕不直接帶憑證抓使用者貼上的網址。啟動 operation 成功改標「已啟動」，不表示工作完成。
待處理空狀態提供專案／開工入口；缺管理權限時提供帳號處理指引。
建立專案時可明確選擇中央既有的 repository binding，建立後直接接到專案派工；不預選
儲存庫，也不建立新的主機設定或額外 task。尚無可選 binding 時仍可先建立空專案。

本項是已確認 UX 缺陷與互動修正，**尚未完成下載後自動安裝中央、簽發身分、配置 BAT 主機／
repository binding 的首次使用流程**。現有中央依賴 Unix 鎖與檔案操作，不能把收起設定說明
當成 Windows 本機後端已可用。完整零設定啟動與 PR 列表選擇仍需後續實作。
分支、檢查、合併、原生安裝與實際主機驗收分開記錄。

### 2026-10-09 原生首次連線

`feat/first-connection` 接續 Delivery 修正，將缺少 `central.json` 的首次啟動接成：輸入中央
位址／預期身分 → 原生視窗確認 → 原子建立固定設定檔 → 既有原生憑證驗證。取消或拒絕保留
非機密輸入草稿；既有設定／symlink 不覆寫，確認期間 disconnect 或重新載入會拒絕舊保存。
原先啟動時沒有有效設定的環境權杖仍丟棄，不會轉送到新位址。Web 的同源連線保持原路徑。

Dashboard-only 可直接使用既有中央；Fleet 的 Kit／inventory／SSH 仍需先配置，登入選項與
Rust／PowerShell 遷移沿用現有明確預覽。這一步完成中央初次設定，尚未宣稱自動安裝整套 Fleet
或 Linux 原生憑證儲存。候選 gates 與 Windows／Mac 安裝 fixture 另由 PR/checks 留證。

### 2026-10-09 Delivery 資訊正確性與派工到 PR 的關聯

`fix/delivery-lineage` 接續 #71 的 `ca6358c`，修正候選清單以 `streaming=false` 推論完成。
活動三值、待回覆、觀測過期／缺失、成果未驗證與交付 receipt 分開呈現；派工成功只標受理。
新 `execution`／`task_command` source 使用中央 accepted execution 證據，將一般 start、
published continuation 與 Task Service 原命令接到既有固定 commit preview/apply。preview digest
固定 lineage／registry incarnation／worktree identity；送出前重查、送出後仍查回原 push。

專案直接列出明確關聯的派工、session／worktree 與 PR 入口，不增設 task、不依 repository 名稱
推論專案歸屬。新來源也接入 cleanup 的固定身分交付覆蓋與有效預覽保留。Worktree 改為資訊摘要，
unknown BAT merge 顯示保留目錄與 ACK 證據／結案限制。桌面連接頁補使用順序，Fleet 顯示實際
backend 及 PowerShell 相容預設；完整無設定檔首次安裝精靈、正式簽章／更新仍未完成。

該次候選用暫存 Git、MockBat／FakeGitHub 驗證來源與交付；190 項派工／整合回歸、85 項
來源／整理檢查、674 項共用 UI 與 31 項 Rust bridge 檢查已通過。實際中央 HTTP／IPC 的
兩次原派工已各自接到同一個測試 PR，固定來源 receipt 一致，人工 checkout 不變。預覽選擇
不再被自己的事件刷新清除；Tauri bridge 限定開放指定成果的查詢。完整候選 gates 另留紀錄於
本機驗證紀錄（未隨原始碼發布）；合併、固定候選安裝及使用者真實主機驗收
不能由這些 fixture 結果推論。實機目標尚待指定，見 [固定候選驗收](acceptance-v2.md)。

### 2026-10-09 專案快速派工、進階設定與附件草稿

`feat/project-quick-dispatch` 接續 #70 的 `31e7a4f`，推進 Project Hub 審查 P1-3。
專案詳情新增共用 Web／Tauri 入口，限定中央明確綁定的 repository／host／workspace；
唯一目的地自動選取，多個目的地需明確選擇。標題／模型收進進階設定，附件沿用既有草稿與
ready revision。原請求固定專案版本、GitHub head、原文、模型、附件 digest 和 operation key。

中央擴充既有 `repository.continue`，附件只寫入該操作自有的 published carrier；傳輸與每個未送出
BAT frame 前重查專案／目的地與 bytes。回覆丟失、專案封存或 host tier 改變後仍回到原 key；
不另建 Work Item 或 Task Service task。[合約](../design/repository-sync.md) 明列同主機未發布
狀態仍走 checkpoint，本入口只完成已發布版本派工。CLI／MCP 接同一合約。

相關 128 項後端與 57 項初次 UI 回歸已通過；實際中央的 HTTP／IPC 派工與原 published 流程亦通過。
`test:dispatch` 已加入 desktop CI；候選完整 gate 與平台證據由 PR/checks 及
本機驗證紀錄（未隨原始碼發布） 記錄。#71 已合併為 `fb4fa60`，tree
與通過 Python 3.10–3.13、Windows／Linux／Mac 兩架構 CI 的 `ca6358c` 相同；尚未安裝或 live 驗收。
P1-4 手機資訊／輸入區收合及可調側欄、P1-5 設定分組／首次使用導引仍在後續 backlog。

### 2026-10-09 共用待處理分類與工作更新已讀

`feat/shared-attention-states` 接續 `02eab82`。首頁把待回覆／權限、完成確認、需處理操作、
主機連線分組，原「待確認」分頁改為「執行中操作」；各組明示已載入數量、可繼續分頁，
讀取失敗保留舊資料並阻擋事件 checkpoint 前進。

[工作更新閱讀合約](../design/work-item-reading.md) 使用中央 journal 的 principal/work-item
版本標記與 `work_item.read` durable action。Web／Tauri 同一身分同步；只有詳情的明確點擊
才標記已讀，不改 completion、pending、工作項目版本或來源資源。CLI／MCP 讀取亦接同一
principal 與 unread filter；Rust bridge 只允許 work-items 的布林 unread query。

這完成 P1-2 的狀態分離與「未讀工作更新」範圍。完整聊天訊息未讀數與跨入口對話閱讀位置
仍未實作，不能從局部訊息摘要、最後活動時間或 event ACK 推算。

該次候選已通過 107 項後端相關測試、30 項 Rust bridge 測試、20 項 Node 測試及實際中央 fixture。
另驗證 14 項身份切換、延遲回覆與草稿保留回歸：初次完整 UI run 抓出的舊首頁請求晚到問題
已修正。`test:attention` 已加入 desktop CI；完整 shared UI 與 Python 3.13／3.10 gate 的
候選結果另由 PR/checks 與 本機驗證紀錄（未隨原始碼發布） 記錄。
#70 已合併為 `5b52ea6`，tree 與通過完整 CI 的 `31e7a4f` 相同；尚未安裝或進行 Mac/Windows 實機驗收。

### 2026-10-09 共用 Web／Tauri 與對話閱讀

使用者確認 Web 要持續與 Tauri 對齊。[共用前端設計](../design/shared-frontend.md) 明列
單一 UI source、中央服務獨立運作、平台能力差異、草稿與版本相容；既有 `/dashboard/`
由 Connector 提供，無須另建第二套服務或維持舊 UI。

`feat/shared-conversation-reading` 完成審查 P1-1：安全程式碼／表格呈現、原文複製與失敗備援、
串流／刷新保留閱讀位置及「回到最新訊息」。沿用最近 30 則讀取；未加跨裝置已讀狀態。
Web 與 Tauri 共用 `conversation.js`／`message-format.js`，兩個 build 已更新。
該次候選已有 20 項 Node、616 項 shared UI 通過；包含 25 項新增閱讀／雙入口案例及
中英 × browser/IPC × 390/768/1440 畫面。實際 Connector＋MockBat fixture 的事件續接、
retention reset 與草稿恢復通過；兩種 build 的 JavaScript 相同，CSS 扣除 generated notice 也相同。
初次 clipboard fallback 測試發現 textarea 會正規化 CRLF，已修正整段手動複製並通過回歸。
實作提交 `038df8f`；Ruff、generated browser drift 與 Python 3.13 全套
**3187 passed／33 skipped** 通過（磁碟暫存 wrapper；1574.89 秒）。測試暫存已清除，
證據保存在 本機驗證紀錄（未隨原始碼發布）。
尚未合併或安裝；IPC fixture 不代表 Windows/macOS 原生剪貼簿驗收。

### 2026-10-09 Project Hub 前端完整複查

已重新核對上游最新 `main` v4.90.0（`a277d2ed5ce439c248fc32fa8ff9fa4124a06027`），
包含全部 15 個自有 JS 模組、CSS／入口／manifest，以及未合併 PR #2／#3／#4。
[82 項逐項採用決策](project-hub-frontend-audit-2026-10-09.md) 以我方 Mac #69 合併後
`bc5cb21` 為比較基準；[來源清單](project-hub-frontend-inventory-2026-10-09.json) 固定各檔雜湊、PR head 與決策對應。

後續優先順序：對話程式碼／表格／複製及閱讀位置 → 未讀與待回覆／完成確認分開 →
專案快速派工及附件草稿 → 手機資訊／輸入區收合與可調側欄 → 設定分組與首次使用導引。
模型偏好、中央額度資訊、Skill 目錄及父工作成果入口列 P2；人工 session 原地續跑、
原目錄自動提交、AI 決定刪除範圍與自動清歷史不採用。
這是評估與採用順序，尚未新增上述產品功能。52 項上游 UI 測試通過，隔離資料產生 26 張
桌面／平板／手機截圖；沒有把展示 fixture 或未合併 PR 當成我方 native/live 驗收。

### 2026-10-09 macOS 交付續作

使用者新增 Mac 版本要求。從已合併 #68（`b127195`）開工，新增 Apple Silicon／Intel
DMG、ICNS、Dock Reopen／正常 Quit 與兩種架構的 disposable native install fixture。
Mac 候選須以自己的完整 head CI、DMG digest、WKWebView screenshots 和清理 receipts 驗證，
不能沿用 Windows/Linux 的結果。新增 Mac 原生 secure-field／Keychain adapter 與隔離 Keychain fixtures，並備妥正式
Developer ID／公證 candidate workflow；待各自 exact-head 結果。正式簽署執行、Mac updater 及 I／L
仍分開追蹤；Windows Fleet supervisor 不因 Mac 可啟動就宣稱跨平台支援。

後續結果：[#69](https://github.com/TED-H-com/bat-agent-connector/pull/69) 已合併為
`bc5cb21e48ce05f55d320dbe0ec98fbf6114e520`；候選 `99f05ac98bf7acff4078ca302d784cf8569b1fdf`
在當時合併時已核對 tree 相同；此為歷史比對，不是新套件證據。
[Python CI](https://github.com/TED-H-com/bat-agent-connector/actions/runs/37967171572)
3.10–3.13 各 3187 passed／33 skipped；
[desktop CI](https://github.com/TED-H-com/bat-agent-connector/actions/runs/37967171409)
591 shared UI、Windows NSIS／Linux deb 與 Apple Silicon／Intel DMG 原生 fixture 通過，
包含 Mac 隔離 Keychain、安裝／啟動／關窗／Reopen／Quit 及清理證據。
Mac 驗證包仍為 ad-hoc 簽署；正式 Developer ID／公證、Mac updater、實機／live 驗收尚未完成。

#68 的固定候選 `651b964fe7b7d8164bb6990653e8a14d5f046827` 已通過
[Python 四版各 3187／33](https://github.com/TED-H-com/bat-agent-connector/actions/runs/37949144734)
及 [Windows 真 NSIS 安裝／Linux 實際 deb WebView](https://github.com/TED-H-com/bat-agent-connector/actions/runs/37949144823)，
合併當時已核對 merge 與候選的 tree 相同；此為歷史比對。

### 2026-10-09 發行門檻收斂

#64／#65／#66 已合併；以下各段保留當時候選的歷史證據。
[#67](https://github.com/TED-H-com/bat-agent-connector/pull/67) 再合併為
`c506d38ae7f106a18280c16914abe76b1aecd766`，與已測候選
`d10ab7eb122740b169aba147415aa1412300a0b0` 的 tree 在合併當時已核對相同
（歷史比對，非本次重建證據）。Canonical workflow 保持 `2026-10-08.14`。
GLib 保留 0.18.5 原版本並 backport upstream 的兩行指標修正，來源完整性／真正 dependency graph
與 optimized iterator regression 成為 gates；未忽略 advisory。
[#53](https://github.com/TED-H-com/bat-agent-connector/issues/53) 已關閉，Dependabot alert #1 為 fixed，未 dismissed。

固定候選 [Python CI](https://github.com/TED-H-com/bat-agent-connector/actions/runs/37934974542)
3.10–3.13 各 **3187 passed／33 skipped**；
[desktop CI](https://github.com/TED-H-com/bat-agent-connector/actions/runs/37934974673)
Windows NSIS／Linux deb、585 shared UI、12 state、每平台 81 app Rust、release verifier、GLib release
regression 及 Linux 真 packaged WebView／關窗隱藏／single-instance 通過。Windows metadata locale
failure 已修正並由最終 Windows job 通過，舊失敗保留。這些仍是 unsigned／F／N 證據。
正式簽章、實機／live 驗收及 release feed 發布仍未完成。

新增 Windows installed fixture 只允許 GitHub-hosted disposable runner：驗證 NSIS 真安裝、
binary digest／PE 與 registry version、license resources、真 WebView loopback polling、WM_CLOSE、
single-instance、process restart 與 uninstall，保存 screenshots／JSON。這是 N 層安裝 fixture，
不能替代真人 Credential Manager／跨登入、實體 tray 操作、signed update 或真中央／BAT 的 I／L 驗收。
其執行結果以包含此 fixture 的候選 CI 為準，不沿用 #67 的綠燈。

### 先前整合證據

最新追蹤 [#59](https://github.com/TED-H-com/bat-agent-connector/issues/59)。[#61](https://github.com/TED-H-com/bat-agent-connector/pull/61)
已合併為 `3cd49b7658aee9828941e3e6d6d0d8cd8d2ce578`；固定候選 `ee1d095b1f07befb86a8c4824d5e20fb77dc0873`
與 merge 的 tree 在當時已核對相同（歷史比對）。Managed start、task reviewed／automatic cleanup、
native 選檔／上傳／Save As、成果擷取／獨立審核及 canonical `.8` 已在 main。Project Hub 的 session 整理方向保留：
依實際 host／workspace 分組，不從名稱猜測專案歸屬。GUI／CLI／MCP 都在指定 BAT host／workspace 工作；
跨主機只接續明確 repository 的已發布 commit，未發布 Git-pack transport 不在交付條件。

Exact-head CI：Python 3.10–3.13 各 **2680 passed／33 skipped**；333 UI、12 state、每平台 53 app Rust，
Fleet core Windows 63／Linux 58 通過。Windows NSIS／Linux deb unsigned packages、Ruff、generator、Clippy、
generated browser drift 與 secrets scan 通過。Actual-central fixtures 用 temporary Git／MockBat 驗證 start、bulk、
cleanup、native files 及獨立 reviewer 的固定 bytes/revision。Local full Python 3.10、3.13 各 2680／33；先前磁碟
壅塞的 3.13 run 有未釐清 failures 且中止，沒有計為通過，後續 isolated fixtures 與 exact-head CI 完整通過。
當時 Linux GLib release gate #53 尚開啟；現況見上方收斂紀錄。

後續主線與候選分開追蹤，不計為 installed 完成：

- [#62](https://github.com/TED-H-com/bat-agent-connector/pull/62) 已合併為 `aa34f18313a4a3a62fdc32bc11b4e142256614e5`。
  候選 `e309700ad1c5cff96661f07f0c7ffdee02f72029` 與 merge 的 tree 在當時已核對相同（歷史比對）。
  明確 repository binding、GitHub numeric ID、固定 ref/head 與 fresh managed carrier、published-start UI 及 `.9` 已在 main。
  Exact CI Python 3.10–3.13 各 **2725 passed／33 skipped**，兩平台 desktop／unsigned packaging 通過。
  Local 3.13 full 先有 operation settlement timeout，重跑另有兩項 subprocess deadline failure；沒有宣稱 local full 綠燈。
  原碼 targeted integration 1、subprocess 3 通過；本機高負載下停止重複全套，以四版 exact-head CI 作完整驗證。
- [#63](https://github.com/TED-H-com/bat-agent-connector/pull/63) 已合併為 `41261de7a3d2c06c7a7b02dc75cb67e3c61fbdc9`。
  候選 `245cad0b6b9899aac77471335154a8d46ea19e43` 與 merge 的 tree 在當時已核對相同（歷史比對）。
  Central relay、fanout planner／fixed-plan dispatch、standalone Claude→Codex failover、omitted-key task controls 與 `.11`
  已在 main。Exact CI Python 3.10–3.13 各 **2888 passed／33 skipped**，Windows／Linux desktop packaging 通過。
  Focused 490 在 3.13／3.10 通過；舊 principal fixture 斷言已修。兩次主動中止的 local full 不計通過，第二次是在 CI 四版通過後停止重複驗證。
- [#64](https://github.com/TED-H-com/bat-agent-connector/pull/64) 整合 native Fleet／bootstrap／signed update；來源候選 `472ea843cfcbe0d0be47700e450c01a1d63d16d0`。
  真 Windows fixture 先暴露空閒 endpoint 在 100 ms 尚未回 refusal，修為 bounded 3 s refusal＋bind proof。
  接著修正 ownership 掃描與 selection 的短路徑差異、WScript Startup 短路徑辨識，以及並行 discovery fixtures 的時間戳撞名。
  完整 Windows Fleet core gate 已通過，包含真 WScript synthetic links；22 discovery、42 ownership/configuration regressions 與獨立 review 通過。
  另一次 app fixture 在原 10 s PowerShell contract budget 逾時，後續不同條件的 probes 通過不當作原測試通過；失敗及原碼查核／重跑證據保留於 PR。
  原 combined evidence：416 UI、12 state、218 Linux core、78 app Rust／1 fixture-only ignored、release verifier example 1；bootstrap 44 在兩版 Python 通過。
- [#65](https://github.com/TED-H-com/bat-agent-connector/pull/65) 整合四個 orchestration 表單、task pause/resume、操作分頁、明確 BAT profile 入口、principal transcript/wait、A03 labels 及中央 verification testimony。
  候選 `7b647a95f981575876c4e8b10537eb669e0e3c86` 保留原 actor/key/request 與 receipts。Labels 只改中央 metadata，含人工／unknown／retained 與版本 CAS；verification 是外部證詞，不能授予 trusted task verification。
  Observation 86、labels／verification／principal／lifecycle／canonical 152 在兩版 Python 通過；3 trusted verifier、91 affected UI、actual-central labels／task／orchestration fixtures 通過。
  Full CI 在 `cc48542` 發現 3 個 API remount 重複註冊 failures 與 1 個舊 MCP 工具清單斷言；`4696cd1` 修正後，101 related regressions 在 3.13／3.10 通過並經獨立 review。
  本機 orchestration fixture 曾在一般暫存磁碟遇到 5 s UI deadline，原碼在 `/dev/shm` 通過；沒有放寬 deadline 或計為 local full 通過。
- [#66](https://github.com/TED-H-com/bat-agent-connector/pull/66) 是同一產品的最後整合工作線，累加 Tailscale recovery、中央 `worktree.merge` 與 canonical `.14`。
  Tailscale 使用固定 Windows vendor paths、bounded sanitized status、原 request receipt、Launcher／Quit／update fences；返回畫面只查狀態，不自動登入或修改網路。
  當時的 Tailscale 實作已獨立 review；9 durable core＋1 synthetic process、3 Linux Tauri IPC、29 final UI、12 state 通過。Windows MSVC isolated compile 是分層證據，實際 Windows matrix 另留 CI。
  Merge 固定來源／目的 Git identity 與全部 consumers，保留雙 carrier reservation、各 effect 的 ACK、未知結果與原 key。支援明確 idle／unloaded／headless／retired managed consumers，拒絕 task-owned／manual／unknown／未決 writer。
  先以設定的 verifier SSH 讀 effective Git config，拒絕 diff/filter programs、ambient overlays、submodules、incomplete status，再讀 BAT worktree status；SSH mapping 必須由 operator 綁到同一 BAT Git account/configuration context，不能當作獨立環境 attestation。
  當時 merge 實作的 140 merge/client cases 在 3.13、254 related cases 在 3.10 通過；先前 shared layers 705／707 通過。獨立 review 的 Git diff-program finding 已修正，原始失敗／修正 evidence 保留。
  Root integration 的共用 UI **585**、state **12** 全通過；merge／verification／client／MCP／canonical／cleanup／SSE／labels 共 **282** 在 Python 3.13／3.10 均通過。
  固定候選的完整 Python／Windows／Linux、late recovery corrections 與 installer SHA/digest，以此 PR 最後記錄為準。

本節是送審來源與分層 evidence 快照；PR 的完整 SHA、checks、merge commit 與 tree 比對記錄才是合併判準。
不以先前 head 綠燈、較小 focused suite 或不同來源的 installer pin 當最終 release candidate 證據。

以上沒有正式簽章身分、release feed 發布、Windows installed 或 live host/provider writes 的完成宣稱。
本機 fixtures 不代替 PID／account／Startup／原生視窗及完整產品展示。GitHub Codex review quota 用盡，
本輪採具體 source／repro 的獨立本機 peer review，不宣稱 bot approval。

本輪來源與審查（以下保留合併前各層證據）：

- Native credentials：Windows 原生憑證對話框／Credential Manager、固定 endpoint／actor／
  contract 及中央身分驗證、可恢復連線。獨立審查找到 native bootstrap 404 誤入 legacy namespace，
  及手動連線時 Disable Disconnect；後續修正，51 focused UI cases 通過。原 head 的 156 UI、
  32 Linux Rust、8 state 與 Windows 模組 typecheck 是分層 fixture 證據；完整 Windows build 交 CI，
  沒有原生對話框／Credential Manager 實機驗收。
- Managed artifacts：獨立 preview、中央 accepted execution／command lineage、固定 bytes／HEAD，
  `artifact.capture.managed` 與精確 revision 的 `artifact.accept`。獨立審查無 findings；整合 B1／B2
  119 cases 通過。原工作線的 Python 3.13 B1 instrumentation stub 曾漏 optional keyword，僅修 fixture
  signature 後通過；未抹去先前失敗。不標記工作完成或交付，UI 仍需接入。
- Bulk approval：明確選定 preview、原 answer／permissions children、逐項 partial receipts、
  送出前重新核對 prompt／owner／取消狀態。獨立審查無 production findings；同一 focused regression set
  Python 3.13／3.10 各 753 passed。後續修正依 child deadline 限制 parent polling，71 focused
  cases 在兩版本均通過，明確取消／恢復仍立即喚醒。原內部 deferred raise 保持停用；公開入口缺 preview 時拒絕。
- B2／bulk／canonical `.7` 的整合 123 cases 通過；API/MCP/RPC unions 與 generated skills 經獨立審查。
  本機後端驗證使用獨立 `/dev/shm` Git／bytes／SQLite fixtures 避開磁碟排程壅塞，並非斷電持久性證明。
  最後候選仍須完整 checks、exact-head CI、Windows packaging；installer 尚未 pin 本輪候選。
- Session 整理：依記錄中的 host／workspace 分組、已載入範圍搜尋與筆數、compact rows，
  詳情保留完整 ID／狀態證據，長名稱不溢出。獨立畫面／source review 修正 runtime fields stale
  仍顯示目前 streaming 的誤判。175 shared UI 在最後文字與展開細節調整前通過；最終 34 focused UI、
  12 state、真中央 MockBat observation fixture、desktop／browser builds 與 generated drift 通過。
  Root 已檢視 en／zh-TW 的桌面、平板、手機及極長名稱截圖；整組候選 UI 與 backend full 正在驗證。

以下保留 #57 的權限工作紀錄：

追蹤 [#57](https://github.com/TED-H-com/bat-agent-connector/issues/57)，基底 main `da66c54`（#56）。
R01 `session.permissions` 接中央 authority，Claude／Codex 每個 setting 保存 intent／receipt，
保留 task incarnation／control version、confinement 與 host policy；sent error／lost ACK 不以相符
metadata 當成功證據。共用 UI 只對 positively managed 且 action／write scope 支援的 session 顯示控制，
保存要求的 mode、key／operation，未知結果不自動換 key。Claude streaming 不排 deferred；歷史 flag
與 legacy bulk-raise apply 停用，preview 保留。完整合約見 [session permissions](../design/session-permissions.md)。
Canonical workflow 為 `2026-10-08.6`；最新候選 SHA、exact-head CI、peer review、merge 與 installer pin
證據以 #57 關聯 PR 為準，不把功能清單當作 installed/live 驗收；
[本輪交接](../handoff/2026-10-08-permissions.md) 保留實作與失敗修正脈絡。

## 上一輪續作（#56 已合併）

候選 `58b8ef69e92a1f506437bb56c2086fc8ac92582e` 已合併為 `da66c545120c1b5fb2703277ae6c0e180c309c46`，
tree 完全相同。Python 3.10–3.13 各 **2370 passed／33 skipped**；101 UI、8 state、20 Windows／21 Linux Rust、
六組 actual-central fixtures 與 Windows／Linux unsigned packages 通過。
收斂證據見 [#55](https://github.com/TED-H-com/bat-agent-connector/issues/55#issuecomment-6072185401)。

追蹤 [#55](https://github.com/TED-H-com/bat-agent-connector/issues/55)，基底 main `1485eff`。
以下三個提交已整合到同一候選工作線；最新整組 CI、合併與 installer pin 證據見該 issue 關聯 PR。

| 包 | 提交與行為 | 這次 fixture／review 證據 |
| --- | --- | --- |
| R01 | `041d907` + `7440a58`：legacy send／continue／answer 共用 durable operations；原 caller、完整 session/prompt、message ID、queue、dont_ask_again、完整 receipt 與 no-key 語意保留 | 94 個新案例含真 HTTP/MCP/CLI、lost ACK／restart、task final-frame、無 token／owner 拒絕。3.13 focused 曾 590 pass／1 個既有 verifier 5 秒進入等待 timeout；未改原碼的 12-case family 重跑通過，不當作 full 綠燈。3.10 同組 591 通過；peer 發現 receipt 漏 status reason，7440a58 修補後 controls/interrupt 154 個案例兩 Python 版本均通過。 |
| R05 B1 UI | `d776434`：共用 browser／Tauri remote-file preview/capture；明示 host、完整 manual session ID 與相對路徑；保存原 intent/key 並明確加入附件草稿 | 101 UI（含 27 新 capture）、8 state、21 Linux Rust、fmt/Clippy/release check、真中央 temporary-source fixture；bytes/index/refs 不變、零 BAT writes。獨立 exact-head review 無 findings。 |
| Legacy removal | `2b2f4d5`：人工／task policy 後停用 unsafe `worktree_remove`，回 `LEGACY_WORKTREE_REMOVE_DISABLED`；改用既有 reviewed cleanup | 基底 active-successor regression 重現；所有 override 零 rehydrate/remove；canonical cleanup shared consumer 拒絕與 eligible removal 仍測到。Focused 3.13 112 通過；新增 apply 斷言原預期錯誤，改為既有 PREVIEW_BLOCKED 拒絕後兩 Python 版本 exact case 通過。 |

UI 曾有 100 pass／1 timeout：不支援 capture 的 session 過早建立隱藏 summary；修為 positively manual 且 capability 支援才掛載，最後整組 101 通過。
兩個既有 MCP fixtures 已改用 caller token 並只提供一種 pending prompt，保留原 task command assertions，另驗 operation actor/binding。
Canonical workflow 更新為 `2026-10-08.5`，Hermes／Grokbot 從同一 source 產生；installer 仍須 pin 已審完整候選，不能只比 package version。
Desktop CI 現在也跑六組 actual-central fixtures，保存合成 UI 證據；完整交付仍依 46 項矩陣，沒有 Windows installed/live 宣稱。
該輪留下的 R01 permissions 合約由上方 #57 接續；bulk approval、starts／orchestration 仍有獨立契約，詳見 [mutation 盤點](../design/legacy-mutation-audit.md)。

## 本輪收斂結果（2026-10-08 23:26 UTC）

- 公開 repo 已合併 #35、#36、#37、#38、#42–#51；產品整合 main 為 `56cc159149873ff0ba2a6a30d54cb4a587d5d636`（#51）。
  #39 importer 關閉且保留 branch，沒有合入。Issue #32 的六項 Delivery findings 已隨 #47 合併並關閉。
- 固定候選 `3ed2c0bcf17de00cae40eae60dc23f3881121831` 的 [Python CI](https://github.com/TED-H-com/bat-agent-connector/actions/runs/37857510798)：3.10、3.11、3.12、3.13 **各 2264 passed / 33 skipped**，Ruff、generated skills、secret scan 通過。
  [Desktop CI](https://github.com/TED-H-com/bat-agent-connector/actions/runs/37857510691)：Windows NSIS、Linux deb、74 shared UI、8 state tests 通過；Rust Windows 19、Linux 20 tests 通過，Windows 包含真正 system PowerShell 子程序 fixture。
- 合併當時已核對產品整合來源與已測候選的 Git tree 完全相同；此為歷史比對，不是重寫後 commit 的新 CI 或套件證據。
  本次收斂文件是後續 docs-only 更新，未更動該 runtime/build/skill source，沒有另宣稱文件 head 跑過一次 full suite。
- 獨立審查修正了 artifact scalar admission、deployment cursor bounds、interrupt history prefix binding、capture replay/control credential binding、installer YAML 與 concurrent config preservation。
  先前 full/CI failures 均保留；#48/#49/#50 的舊 fixture failures 已由 #51 的 canonical-source/data-step 修正及整組 CI 覆蓋，未把舊紅燈改稱綠燈。
- #38 早期本機 3.10 full 曾為 1834 passed/33 skipped/1 個 60 秒 settlement timeout；journal 顯示持續進展，原碼單測 2.52 秒重跑通過。後續 exact-head 四版 CI 各 1835/33 通過；不把單測 retry 當 full。
- 當時新增 [Linux GLib release gate #53](https://github.com/TED-H-com/bat-agent-connector/issues/53)：registry `glib 0.18.5` 受 [RUSTSEC-2024-0429](https://rustsec.org/advisories/RUSTSEC-2024-0429.html) 影響。後續 #67 已 backport upstream 修正並驗證來源／optimized regression，#53 關閉、Dependabot fixed；見上方固定候選證據。
- GitHub Codex bot 後期額度用盡，後續使用獨立本機 Codex peer review，沒有冒稱新 bot verdict。
  **沒有 live host/provider writes、主機安裝或 Windows installed acceptance；M1/M2/M3 與完整產品交付尚未完成。**

## 2026-10-08 接手基線（歷史）

| 來源 | 接手時 remote head | 狀態及下一步 |
| --- | --- | --- |
| #35 observation | `4e80b47` | CI 綠；無資料時 relations cursor 驗證待修，完成後審查與整合；legacy reviewer root/policy 一致性待核對 |
| #36 operations Part A | `3dbef57` | CI 綠但不是最新修正；已有 `5bf45c6` pause/verifier 修正，內部 failover authority 尚在跑 |
| #37 confinement | `ba6a10c` | 衝突、無 CI；Python 啟動前依賴完整性與 pre-send rollback 待修；須累加 #36 gate |
| #38 cleanup Part A | `242218c` | 衝突、無 CI；已有 `5138b9d`/`1f18017` 容量及移除 host 修正，retirement follow-up 尚在跑 |
| #39 Hub import | `09dc74a` | 明確排除，不合併、不作依賴；通用 project/work-item 已在 main |
| Delivery Part B（local） | `2281498` | 既有已審成果需在 #35/#36 後整合；data step 3、history adapter、owner lease、固定 UI key 等 pending notes 仍適用 |
| Artifacts Part A（local） | `e71ba65` | 保留已有成果；在 confinement/cleanup 後整合 exact replica manifest，無 Hub import 依賴 |

當時四項工作仍在進行，整合需避開各自未提交的修改。只有完成審查的 SHA
可作整合來源；依 remote head 與 expected head 核對，避免合入未審的新 push。

## 2026-10-08 接口與依賴（歷史分工）

| 包 | 負責範圍 | 交付與依賴 | 當時狀態 |
| --- | --- | --- | --- |
| R00 | 產品整合 | 決策、scope、狀態、中央/native 邊界 | 已記錄產品方向文件 |
| R01 | 中央操作與隔離 | #36 → #37；legacy operations Part B；shared operation/task gates | #36/#37/#49 main；當輪加 send/continue/answer，其餘依盤點 |
| R02 | 桌面與共用前端 | `desktop/**`、共用 UI source、受限 transport、window/tray；依 R00 | #44 已合併；installed 驗收待做 |
| R03 | Fleet（待 R02 bridge） | Fleet inventory/PS adapter → Rust parity、ownership、bootstrap | #45 PS adapter 已合併；Rust parity 尚缺 |
| R04 | 觀測與前端資料 | #35 read models → 共用 frontend state/history/relations | #35 main；#48 frontend 已合併 |
| R05 | 成果與接續 | fixed checkpoint、artifact bytes/manifest、明確 repository 的已發布版本同步；依 R01/R02/R08 接口 | checkpoint/#46/#50/B1 UI main；B2 工作線續作，repository 同步待查核；直接未發布 Git relay 已排除 |
| R06 | 專案與工作項目 | 既有 project/work_items、completion + R04 詳情；無 importer | 基礎 main；桌面 history/details 已合併，剩餘能力待驗收 |
| R07 | 交付 | integration、cancel 追蹤、composite scopes、source SHA/recipe、UI；依 R01 | Part A main；#47 Part B 已合併 |
| R08 | 整理與恢復 | #38 + R01/R04/R05 引用、task cleanup、recovery | #38 main；task-owned cleanup 仍保留 |
| R09 | API／技能與版本整合 | canonical API/skill + 薄 wrappers、installer/client pins；隨 R01–R08 | #43 canonical/generated 已合併；final candidate pin 已固定；實際安裝待做 |
| R10 | 安裝版與使用者環境驗收 | 同 SHA app/backend、Windows WebView、BAT、選定測試 repo/environment、多 client | 未實機驗收 |

共用 `operations.py`、`service.py`、`api_v1.py`、`task_daemon.py`、`task_journal.py`
及 contract generator 當時由中央整合統一管理；桌面工作負責 frontend transport/state
抽取與 build。其他功能提出接口需求，避免同時重構核心檔。Registry/flock、start claims
及 cleanup reservation 需要組合回歸。

## 原定執行順序（完成狀態以上方快照為準）

1. 記錄 R00，更新 agent 入口，排除 #39。
2. 已在跑的 #36/#35 修正完成 → review → 順序跑 full checks → 合併；R02 可平行。
3. #37 接 #36/#35 並保留兩層 frame gate；再接 #38 observation IDs/flock/claim 衝突。
4. 共用 frontend 接 observation，完成事件 store、pending/linked 刷新與 M1；Fleet
   adapter 遵循唯一 owner，不靠第二個 monitor 補缺口。
5. 整合現有 artifacts/Delivery Part B，補 R01 Part B、R05/R06/R08；R03 Rust parity。
6. R09 版本與安裝一致；R10 用同一候選版本跑 46 項及正式展示，才宣稱 M3／完整產品交付完成。

## 驗收證據與外部依賴

- R00：依 `git`/GitHub heads、handoff、工作紀錄、actual API registry 查核；無 runtime 變更。
- R02：已合併 source 有 build/fixture/native 子集證據；尚無 Windows installed/tray/跨登入、簽章或 live 組合證據。
- Backend：各舊候選的 full suite 結果保留在交接／工作報告；未當作整合結果。
- #37 trusted auditor `check_ssh_alias`/`check_uid`/`bat_account` 與限定 sudoers 需實際
  host 配置。沒有這些證據不得宣稱全域隔離或授予較高受限執行模式。
- 正式部署示範仍需選定測試 repo/environment、使用者 scopes、Windows 與 signing 設定；
  開發授權不代表任意正式部署授權。

## 尚未涵蓋

M1/M2/M3 皆未完成。此表不代表已安裝或 live accepted；之後每輪加入實際 SHA、
user-visible behavior、測試層級及仍缺接口，保留未驗收項目。
