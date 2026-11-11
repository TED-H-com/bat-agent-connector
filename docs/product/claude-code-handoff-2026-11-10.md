# Claude Code 開發交接筆記

本文保留 2026-11-10 的歷史快照。Connector 0.2.18／Desktop 0.1.14 的目前交付與
獨立的合併、發布、安裝及實機證據，請見[實作狀態](implementation-status.md)。
This is the November 10 historical snapshot; see implementation status for current
Connector 0.2.18 / Desktop 0.1.14 delivery and separate acceptance evidence.

交接日期：2026-11-10。接手者：Claude Code。此筆記涵蓋目前產品方向、已交付內容、
剩餘工作、實作入口、驗證與發布流程。先讀根目錄 [AGENTS.md](../../AGENTS.md) 與
[CONTRIBUTING.md](../../CONTRIBUTING.md)，再依本筆記找到適用合約；本筆記不取代它們。

目前功能主線為 `12f0f01a1f0909526ce156e002be725fc9b7eb39`，已包含 #511。
最新穩定發布仍是 Connector **0.2.17**／Desktop **0.1.13**，發布來源是
`0399db5a7b647486126c9bf7bb5900c40dc54ea7`。**#511 已合併，但不在 0.2.17 套件內。**
接手時先重新 fetch，本文 SHA 是交接快照，不應覆蓋之後的新主線。

## 先完成接手確認

1. 檢查目前 branch、工作樹變更、其他 worktree 與 worker 歸屬。不要重設或更新別人的 checkout。
2. `git fetch origin main`，確認 `origin/main` 的最新 commit；從它建立自己專用的 worktree。
3. 讀取下面的產品文件與本輪合約，查 PR／CI 的最新狀態。
4. 選一個有明確驗收條件的增量，先說明範圍，再實作、驗證、提交並更新交接。
5. 本次只交接，不代表要求重發 0.2.17、安裝到 owner 主機或立刻操作真 BAT／模型。

可在已確認的 repository 目錄執行：

```bash
git status --short
git branch --show-current
git worktree list --porcelain
git fetch origin main
git log -5 --oneline origin/main
```

確認沒有同名目錄／分支後，自行選擇新的任務名稱：

```bash
git worktree add -b feat/your-next-task "$HOME/agent-work/bac-your-next-task" origin/main
```

交接文件若尚在 `docs/claude-code-handoff-20261110` 分支，先讀該分支文件，
但功能開發仍從重新核對過的 `origin/main` 開始；不要把文件分支當作固定開發基底。
根目錄 `CLAUDE.md` 只提供導讀，規則本體仍是 `AGENTS.md`。

## 使用者授權與產品方向

使用者已要求「該合併發布就發布，繼續開發」，之後再次要求繼續。本次要求把工作完整
交給 Claude Code。一般實作、可逆修正、必要驗證、提交及符合門檻的合併／發布已有
授權，不要重複索取相同許可。這不等於任意操作正式 hosts、重設人工資源或宣稱真人驗收。
產品內既有已授權 UI 操作也不能再加一層聊天確認。

產品是一個專業 PM／工程交付系統：需求、scope、決策、實作、交付證據、維護與結案
屬於同一生命週期。v1.2、v1.3 與後續 UX／EC 修訂是同一產品的演進，不能把 PM 縮成
session 摘要，也不能把第一個增量當作整份計畫完成。

依此順序閱讀：

| 文件 | 用途 |
| --- | --- |
| [共用產品決策](realignment-v2.md) | October 8 Tauri 方向、中央權威、排除事項與後續澄清 |
| [最新實作狀態](implementation-status.md) | 最新交付；舊段落是各自當時狀態 |
| [v1.3 agent handoff](v1.3-agent-handoff.md) | 工作入口與 E／R／A 對應 |
| [v1.3 closed-loop plan](v1.3-closed-loop-plan.md) | E00–E11、R0–R5、A01–A40 全範圍 |
| [PM v1.2 plan](pm-v1.2-plan.md) 與 [status](pm-v1.2-status.md) | 未被修訂的 W01–W52、A01–A64、M0–M6 與生命週期待辦 |
| [Workbench UX](workbench-ux-plan.md) 與 [mapping](../design/workbench.md) | shell、selection、呈現與剩餘 UX 工作 |
| [EC-1.1](experience-convergence.md) 與 [delta](experience-convergence-delta.md) | 不重做已合併 Work loop；手機、跨 CLI、PM 收斂 |
| [外部歷史合約](../design/external-conversation-history.md) | 本輪功能與下一個相關增量的直接契約 |
| [Connector updates](../design/connector-updates.md) | 已納入的 Connector 自動更新，以及保留的 desktop lifecycle/signing gates |

E00 的互動基準與 owner R0 確認仍須分開追蹤。October 15 的持續開發授權不等於
R0／R1 接受。最近的歷史關聯工作主要是 E08／E10、EC-1.1 T5 增量；發布工作涉及 E11，
均不能因此關閉整組 E00–E11、R0–R5 或 A01–A40。

## 必須維持的架構與安全界線

- Python Connector／Task Service 是唯一中央業務權威。Tauri 負責本機視窗、credentials、
  Fleet transport；不在 Rust 或 frontend 建第二套 scheduler、授權或帳本。
- `desktop/src` 是 browser／desktop 共用來源；修改後 regenerate Dashboard assets。
  不直接修改 `src/bat_agent_connector/dashboard/` 的 generated files。
- Manual BAT session／worktree 永遠唯讀。Unknown 資源必須有既存 creation evidence 才能
  managed recovery；不能從 path、名稱或 source grant 推斷 ownership。
- 接續人工工作只能從固定已 commit checkpoint 建新的 managed 資源。保留 same-host
  read-only checkpoint continuation 與普通 artifact upload/download。
- 跨 host 程式同步走明確綁定 GitHub repository 的 published commits；不恢復舊計畫的
  unpublished Git objects／workspace 直接搬運要求。
- 重用 ActionDef、scopes、resource policy、coordinator gates、durable operations、final-frame
  檢查與 sent-effect reconciliation。未知結果保留原 operation ID/key、receipt、claim／lock；
  不重送 start、不清空所有鎖、不把 accepted／ACK／idle 當成完成。
- Project Hub 可有選擇地重用並註明來源；不做 snapshot importer、source mappings 或 importer
  dependency。B05 是 Connector 自有 IDs／history 的 schema upgrade 保留。
- 新 DDL 必須 idempotent，不自行配置 `user_version`；已有 data steps 1 event copy、
  2 observation history、3 deployment history，新 data step 要協調。
- 英文與 zh-TW、draft、穩定 IDs、operation keys 都要保留。正文與敏感 reference 不可因
  增加 UI 便利性就放進 localStorage。
- 不提交真 hostname、credential、fingerprint、私人部署設定或實際會話內容。測試只用
  mock BAT/providers、synthetic transcripts、暫存 Git／SQLite，不做 live writes。
- 使用者沒有要求多 agent 平行作業；這次交接不授權自動啟動其他 coding agents。

## 已完成的主線與發布

| 項目 | 狀態與來源 |
| --- | --- |
| [#507](https://github.com/TED-H-com/bat-agent-connector/pull/507) | 已合併為 `ddd80dd4`；整合外部歷史固定摘錄、Work／Attempt 關聯、閱讀入口、原生受限讀取與驗證修正 |
| [#508](https://github.com/TED-H-com/bat-agent-connector/pull/508) | 已合併為 `0399db5a`；明確 ID 查詢後跨同工作區可讀來源替換關聯 |
| [v0.2.17](https://github.com/TED-H-com/bat-agent-connector/releases/tag/v0.2.17) | 已於 2026-11-09 10:26:13 UTC 公開；Connector 0.2.17／Desktop 0.1.13，包含 #507／#508 |
| [#509](https://github.com/TED-H-com/bat-agent-connector/pull/509) | 已合併為 `69aadae3`；雙語下載／使用文件及網站更新 |
| [#510](https://github.com/TED-H-com/bat-agent-connector/pull/510) | 已合併為 `bf82348b`；發布證據及 PM／agent 交接狀態 |
| [#511](https://github.com/TED-H-com/bat-agent-connector/pull/511) | 已合併為 `12f0f01a`；Work 閱讀端明確保存清理 ID；**未發布**，見 CHANGELOG 的 Unreleased |

0.2.17 已有八個公開 assets：wheel、sdist、Linux deb、Windows exe、Mac ARM64 dmg、
Mac x64 dmg、`release-manifest.json`、`SHA256SUMS`。上傳後已逐一核對 size／GitHub SHA-256，
公開 latest、tag、manifest 與 stable discovery 皆讀回一致。manifest SHA-256 為
`8fc7559263f763b126883446d415729d93ac46be9254b18f06dbc605aa567c6e`。

Linux／Windows 套件未正式簽章；Mac 為 ad-hoc，未 notarize，沒有 signed desktop updater feed。
歷史圖庫仍固定在 0.2.13／0.1.9 的 `437ee6a7143a82e32eae1d6a008203b2df072aaf`；
Desktop／site-demo 的 offline producer receipts 不齊，因此完整 offline assembly 不合格。
四個必需 package components 的來源一致性與 native CI 是獨立證據，不能改寫成完整離線驗證。

這兩輪沒有手動安裝到 owner 主機，也沒有新增真 BAT／模型、原生使用者或實體手機驗收。
既有 managed Connector 的每小時自動更新可能自行運作；不能由「沒有手動安裝」推論所有
host 都沒更新。實際部署版本仍須逐 host 唯讀查證。

## 最近增量的確切行為

### 固定歷史與關聯

外部 Codex／Claude transcript 經具名、有限格式的 read-only collector 保留可讀部分。
`history_complete=false` 與 `execution_status=unknown` 保持明確；tool marker 不是完成證據。
固定 reference 有 source／content／excerpt digest，文字範圍用 Unicode codepoints，
不是 UTF-16 offsets。沒有任意 scan、控制原 CLI 或自動模型呼叫。

`pm.history.link` 連到既有 Work，Attempt 為明確選填。同一交易可用
`replaces_link_id`＋`expected_old_link_version` 替換；舊關聯／event 保留。
`pm.history.unlink` 以 `expected_link_version` 撤回。Creator／workspace owner 在仍符合
Work 可見性及 scope 的情況下，可於 source grant 撤回後清理**已知 ID**。

目前一般 link／excerpt／association reads 仍要求 current source grant；它們不是撤權後
ID discovery API。Cross-source replacement 必須能讀到新舊來源；無法讀舊來源時不能偷偷
轉成 create。Draft 只保存必要識別資料，已送出的原 request／key 優先於後來表單值。

Work-side reader 支援 Board、Requirements、standalone 與 session-linked Work。
Standalone Work 沒有 workspace binding，route／last workspace 只是提示，必須重讀中央
workspace 與精確 kanban binding。讀 excerpt 前後重查 relationship；切換、父層 safety read、
撤權、關閉／縮小 Results inspector 會撤回正文並隔離晚到回覆。

### #511 保存清理 ID

使用者開啟固定摘錄後，可明確點「保存關聯 ID 供日後撤回」。需要目前 manage／observe、
advertised unlink capability、allowed action，以及 creator 或 owner 身分。
保存前重讀 link，核對 state／version／Work／Attempt／creator／fixed reference；
standalone Work 再核對 workspace／binding。讀取本身不自動保存。

保存的是原有七個欄位：`workspace_id`、`link_id`、`work_item_id`、`version`、`created_by`、
`source_id`、`entry_id`。索引依 account／connection／workspace 隔離，上限 500、不 eviction。
正文、title、digest、reference body 不保存。滿額／損壞／storage failure 有可見提示，
不覆寫原 withdrawal intent；storage failure 可明確重試，不抹掉仍合法的閱讀內容。

之後可由「資源 → 外部對話 → 已保存的關聯 ID」撤回；產品內的工作區 route 是 `agents`，
不是 `resources`。失去 source access 後不必再讀 source／excerpt，lost reply 沿用原 key。
本增量沒有新的 backend／schema／API／native transport，沒有解決未讀且已撤權的 ID 發現。

## 接續工作的範圍與切入點

### 建議第一個相關增量

優先評估「從未在此 client 保存、且來源已撤權的關聯 ID 清理發現」。這是接續建議，
不是已定案的新 API。先補合約與測試設計，再做中央能力及共用 UI；不要把一般 source read
放寬成繞過 grant 的入口。

中央目前 `pm_external_history_links._cleanup` 已表達已知 ID 的 creator／owner、membership
與 Work 可見性條件；`read`／`link_get` 走 source grants。若新增 cleanup-only enumeration，
需自行定義最小 metadata、角色／scope、分頁、已撤回列與版本衝突的語意。特別注意：
新 client 只有 cleanup metadata 時，不應為了沿用本機舊索引而額外洩漏 source／entry IDs。
必要時調整 cleanup draft identity，但原有 request／key 的恢復必須保留。

最小驗收應包含：

- 同 workspace 的 creator／owner 與當前 Work 可見性；非成員、其他 creator、失去 Work
  可見性及跨 workspace 拒絕，global administrator 不自動獲得 membership／source access。
- Source grant 撤回後只見允許的 cleanup metadata，沒有正文、title、digest、path、原生 session
  識別資料或依錯誤訊息探測來源內容的旁路。
- 空頁／hidden rows／cursor 前進、上限、重複 ID、版本變化與讀取期間 membership 撤回。
- 原 operation key、衝突後不自動改版本重送、lost reply／reconnect／切帳號恢復。
- HTTP、daemon、MCP、capabilities、Tauri path/query/body 限制及英文／zh-TW 同步。
- 無需新 schema 時不要新增表；有必要時仍遵守 DDL／data-step 規則。

較大變更或新增 protocol channel 先依 CONTRIBUTING 開 issue。任何 API 名稱與 payload
在合約核對前都只是設計提案，不要先廣告 capability。

### 不可遺漏的其他工作

| 範圍 | 繼續方向 |
| --- | --- |
| E06／E07／E08、T5 | Work derive、自有 native Claude／Codex coding routes、AHP create-own／turn／result、可信 alias／去重。現有 usage／history reader 和 bounded PM runner 不等於 executor |
| E09 與 PM v1.2 | 需求／決策／接受條件、依賴解除、結果 assessment、六視圖一致、完整 Review／Maintenance。不得把摘錄連結當作採納或驗收 |
| PM 既有缺口 | 專業會議／Scrum 引導、語意影響判讀、完整 WIP、額外 renderer／文件模板／簡報、remote usage／交付效率歸因等，逐項回原 W01–W52 狀態查核，不照舊表日期重做已合併工作 |
| E10 | UI 與 API／MCP 等價能力、canonical skill／generated adapters、同一 durable journal 的 delivery／maintenance |
| E11／R0–R5 | 明確 profile 的真 BAT／模型、owner Windows／Mac／Linux 操作、實體手機／IME、資料遷移、正式簽章／notarization、完整離線來源收據與同版本 acceptance |

E00–E05 的既有 Work loop、adopt-and-start、next-turn queue、Result 與 managed recovery 已有
大量實作，先看當前 code／contract 再判斷差額。不要從歷史「未合併」文字重新開始一套。
R0 基準、R1 BAT、R2 跨 CLI、R3 agents/AHP、R4 PM 交付維護、R5 整組候選仍各有獨立門檻。

## 程式與測試導航

路徑皆相對 repository root。

| 區域 | 主要檔案 |
| --- | --- |
| 原始歷史收集與授權 | `src/bat_agent_connector/session_history.py`、`session_history_config.py`、`session_history_decoder.py` |
| Fixed reference resolver | `src/bat_agent_connector/pm_external_history.py` |
| Link／unlink／replace、DDL、CAS、讀取／cleanup authority | `src/bat_agent_connector/pm_external_history_links.py` |
| HTTP 與 daemon 接點 | `src/bat_agent_connector/api_v1.py`、`task_daemon.py`；搜尋 `pm_history_` 找到其他 adapter／MCP 接點，不假設只改 HTTP 就完成 |
| Native 受限 transport | `desktop/src-tauri/src/bridge.rs`；保持 exact method/path/query/body validation |
| Source reader／關聯表單 | `desktop/src/pm-external-history.js`、`pm-external-history-links.js` |
| Range／Attempt／replacement | `desktop/src/pm-history-selection.js`、`pm-history-attempt-picker.js`、`pm-history-replacement.js` |
| 本機 cleanup index／durable cleanup forms | `desktop/src/pm-history-records.js`、`pm-history-cleanup.js` |
| Work reader 與 workspace proof | `desktop/src/pm-work-history.js`、`pm-work-item-history.js` |
| 讀取入口 wiring | `desktop/src/pm-kanban.js`、`pm-traceability.js`、`app.js` |
| 雙語文字 | `desktop/src/pm-external-history-strings.js` |
| Backend tests | `tests/test_external_history*.py`，涵蓋 refs、grants、links、Attempt、API／daemon／MCP |
| UI tests | `desktop/tests/{external-history*,history-*,work-history*}.spec.ts`；#511 增加 `work-history-remember.spec.ts` 與 surfaces cases |
| Node tests | `desktop/tests/history-records.test.ts`、`history-selection.test.ts` 及 `npm test` 全集 |
| 操作測試 helpers | `tests/operation_helpers.py`，優先 `settle_operations`／progress-aware maintenance helpers |
| 分片完整性 | `tests/ci_shard_plugin.py`、`scripts/ci_sharding.py`、`scripts/ci_complete.py` |

## 已有驗證證據

### #511

Feature head：`a4e4095e5c0b2ca2b57fa24af028f0c029f8b3d3`；合併 commit：`12f0f01a`。
合併 tree 與 feature head 相同。34 項 PR checks 全數通過，沒有 unresolved review thread。

- 本機 Python 3.13：9,927 passed、84 skipped、0 failures/errors。
- 10,011 tests、436 files、16 disjoint shards；每個 pytest subprocess exit 0。
  Collection SHA-256：`550b2a091226320166d8823ee5790d3c53c3c6a47548068278cd9d9cb27a053b`。
- 614 Node tests、298 個相關 UI cases（九個 suites）通過；HTTP／injected IPC、角色、切頁、
  storage、原 key 恢復與 390px 繁中均覆蓋。UI 的 298 是不同案例數，不把補跑重複相加。
- Frontend build、browser regenerate／consistency、Ruff、public-content、staged gitleaks 通過。
- [PR backend CI](https://github.com/TED-H-com/bat-agent-connector/actions/runs/42791534332) 與
  [PR desktop CI](https://github.com/TED-H-com/bat-agent-connector/actions/runs/42791534284) 通過，
  包含 Python 3.10 全套分片及完整性核對。
- 交接時另核對合併後的 [main backend CI](https://github.com/TED-H-com/bat-agent-connector/actions/runs/42796357055)、
  [main desktop CI](https://github.com/TED-H-com/bat-agent-connector/actions/runs/42796357317) 與
  [Pages](https://github.com/TED-H-com/bat-agent-connector/actions/runs/42796357271)，皆成功。

本機全套外層工具 session 曾回報 143，但 16 個 pytest 子程序 exit-code 檔皆為 0、JUnit
無 failure/error；獨立重新執行 completeness verifier 亦 exit 0。這是已有證據的界線，
不要杜撰外層 signal 原因，也不必因此盲目重跑全套。

### 0.2.17 發布

固定來源的 [backend CI](https://github.com/TED-H-com/bat-agent-connector/actions/runs/42596030240)
及 [desktop CI](https://github.com/TED-H-com/bat-agent-connector/actions/runs/42596030227) 全通過。
四版 Python 各核對 10,011 tests、436 files、5 disjoint shards。Windows／Mac 安裝 fixtures、
Linux 封裝視窗檢查、0.2.12 與 0.2.16 baseline core upgrades、Connector 真隔離 enroll／upgrade／rollback
已核對；不是 owner live acceptance。

早一輪整合基底的兩版本機全套為各 9,925 passed／84 skipped／10,009 collected。
之後新增兩個 backend cases 才成為 10,011；不要混用這兩組數字或把 collected 當 passed。

## 開發與驗證命令

先在自己的 worktree 準備環境；這台主機已使用 Node 24，`desktop/package.json` 要求
Node ≥22.12。依 lock files 安裝，不在接手時順便升版 dependencies。

```bash
uv sync --locked --extra dev --python 3.13
cd desktop
npm ci
npm run build:all
npm run check:browser
npm test
BATC_UI_TEST_PORT=1447 npx playwright test tests/work-history-remember.spec.ts tests/work-history-surfaces.spec.ts --workers=4
```

UI port 要先確定未被其他任務使用；不要復用別人的 fixture server。其他相關 suites 應依
變更選取，不只跑新測試。API／native 變更另跑對應 parity／Rust／real central integration。
所有 UI screenshot／trace 使用 synthetic data，不提交私人畫面。

回到 repo root，必要全套順序為 Ruff → Python 全套 → backend 變更的 Python 3.10 全套：

```bash
uv run ruff check .
scripts/pytest-disktmp uv run pytest -q
scripts/pytest-disktmp uv run --python 3.10 pytest -q
```

第三項用於 backend 變更。切 Python 版本會更新同一 `.venv`，兩版不可共用環境平行跑；
結束後用 `uv sync --locked --extra dev --python 3.13` 恢復。即使純 frontend 變更，完整 CI
仍可能選擇 Python 3.10，不能因此宣稱本機也跑過 3.10。

**不要把 pytest、Git、SQLite、containers scratch 放在 `/dev/shm` 或任何 tmpfs。**
之前 RAM fixtures 曾造成 host-wide OOM。wrapper 建立唯一的 `~/agent-work/tmp/pytest/`
磁碟 basetemp 並在退出時刪除。共享 ancestor group-writable 導致 Fleet guard 拒絕時，
依 CONTRIBUTING 使用 `scripts/pytest-disktmp --private-tmp ...`，不能 chmod 共享目錄或
削弱正式 ownership guard。wrapper 自身測試在外層跑，避免把 namespace nesting skips
誤當作完整覆蓋；不要全域 monkeypatch stdlib classes。

本機完整測試曾用 16 個互斥分片，只有含 Fleet 的 shard 2 使用 `--private-tmp`。
這個 index 是該次 collection 的結果，不保證後續版本不變。若重用分片方式，使用新的
證據目錄，核對每個 exit code／manifest／JUnit，最後執行 `scripts.ci_complete`；
缺片、重複、collection digest 不同或 failures 都不能報全套成功。

公開／提交前還需：

```bash
uv run python scripts/check_public_content.py
git diff --check
gitleaks protect --staged --redact
```

這台主機的 gitleaks 不一定在 PATH，見下方工具位置。只對 task-owned scratch／processes
做清理，原 journal、人工 checkout、其他 worker 的 containers／locks 都不能動。

## 本機 worktrees 與證據位置

下列 `~` 指本機接手帳號的 home；跨主機時未必存在，GitHub／repository 是可攜來源。
不把這些本機絕對路徑或原始私人 deployment logs 複製進 commits。

| 本機位置 | 用途與狀態 |
| --- | --- |
| `~/agent-work/bac-history-discovery-20261110` | #511 開發 worktree；branch `feat/work-history-cleanup-index-20261110`，HEAD `a4e4095e`，已合併、乾淨 |
| `~/agent-work/bac-history-release-20261109` | #507 已合併整合分支；保留舊驗證來源 |
| `~/agent-work/bac-history-replacement-20261109` | #508 已合併跨來源替換分支 |
| `~/agent-work/bac-release-artifacts-0.2.17` | detached `0399db5a`，發布固定來源；不要在此續作 |
| `~/agent-work/bac-release-docs-0.2.17`、`bac-release-evidence-0.2.17` | #509／#510 已合併文件分支 |
| `~/agent-work/bac-claude-handoff-20261110` | 本份文件的 worktree／`docs/claude-code-handoff-20261110` 分支 |
| `~/agent-work/tmp/history-remember-20261110/` | #511 `full.log`、`local-validation-summary.json`、`pr-checks.json`、16 shards 的 manifests／JUnit／exit-code；`ui-captures/` 有繁中畫面 |
| `~/agent-work/tmp/history-remember-*.log` | #511 build、Node、UI、regression 與補充回歸輸出 |
| `~/agent-work/tmp/history-release-20261109/` | 前輪兩版全套、中央 PM、receipt race、HTTP revoke 及網站驗證 |
| `~/agent-work/releases/0.2.17/` | 六個 packages、manifest、checksums、CI ZIPs 與 release helper scripts |
| 上述目錄的 `validation/` | `published-readback/verified.json`、`stable-discovery.json`、`public-site-readback.json`、固定來源 CI／安裝／升級收據 |
| `~/agent-work/tmp/history-desktop-parity-20261106/gitleaks` | 本機使用過的 gitleaks executable，不是產品依賴 |

主要共享 checkout 當時仍在 local `main` 的 `61415f0e`，乾淨但刻意沒有切到新主線。
`origin/main` 已更新；不要把 local `main` 落後當成同步失敗後直接 reset/pull 人工 checkout。
許多歷史 worktrees 被標為 prunable，這不是清理授權。本次 test-owned 本機服務／程序已結束；
其他 worker 歸屬仍應接手時自行核對。

本機 `run-suite.py` 是一次性 helper，內含原 worktree 路徑。若使用，先讀懂並改成新任務的
repo／evidence 目錄；不能盲目複製執行、共用舊 manifest 或把它當作 repo 官方 CI runner。

## 尚未整合的 PR

交接時另有下列 open PR；不是本輪待提交程式，不代表已審查或可直接合併。
重新查 head、base、diff、owner、checks 與是否已被其他變更涵蓋，不能批次盲合。

- [#512](https://github.com/TED-H-com/bat-agent-connector/pull/512)：validation helper tests／host loopback check，新出現，本輪未審查。
- [#506](https://github.com/TED-H-com/bat-agent-connector/pull/506)、[#504](https://github.com/TED-H-com/bat-agent-connector/pull/504)：其他 core workflow tests。
  先前 #506 有 freeze-time marker 是否真的有 plugin 的疑慮；接手需重新驗證，不能把舊觀察當最終結論。
- [#491](https://github.com/TED-H-com/bat-agent-connector/pull/491)、[#486](https://github.com/TED-H-com/bat-agent-connector/pull/486)：dependency updates，需獨立檢查。
- [#481](https://github.com/TED-H-com/bat-agent-connector/pull/481)、[#480](https://github.com/TED-H-com/bat-agent-connector/pull/480)、
  [#479](https://github.com/TED-H-com/bat-agent-connector/pull/479)、[#454](https://github.com/TED-H-com/bat-agent-connector/pull/454)：舊 drafts，先查是否已被新主線取代。
- [#471](https://github.com/TED-H-com/bat-agent-connector/pull/471)、[#334](https://github.com/TED-H-com/bat-agent-connector/pull/334)：其他 Fleet／remote usage 工作，保留原歸屬。

Git push 顯示過 repository dependency alerts；本次未做獨立漏洞稽核或修補，勿把交接中的
測試通過理解為這些 alerts 已解決。若接手做安全修補，重新讀 advisory／現行 diff。

## 合併與後續發布方法

合併前固定 PR head SHA，檢查所有 required jobs／workflow conclusion、review threads 與
base 是否移動。GitHub PR 的 check `status` 曾與 `conclusion` 短暫不一致；核對完整 workflow
結果，不只數 IN_PROGRESS。正常進行中的長測試不可盲目 cancel/rerun。

新版本發布要以最後合併來源重新建置及取得主線 artifacts，不能把 #511 PR artifacts
直接冒充新的 release。0.2.17 的 helper scripts 可作為流程參考，**內含舊版本、source、run、
artifact 名稱及 baseline pins，不能直接重跑發布下一版**。

1. 固定最終 source、版本／canonical skill／generated adapters、lock files、CHANGELOG；
   新版 upgrade fixture 的「previous stable」應重新選實際最新發布來源（目前為 0.2.17）。
2. 跑固定主線 CI，核對 artifact repo／run／SHA／ZIP digest、內部 package version、source
   bytes／build-info、Dashboard／runtime、一致的 native install 與 core-upgrade fixtures。
3. 組裝 manifest／SHA256SUMS，只在所有已宣告 gates 成立後建立 release tag；不 force-move tag。
4. 先建立 draft release、上傳明確資產集合，讀回每個 size／digest。草稿 REST tag endpoint
   曾回 404：用 authenticated release listing 的精確 tag 找唯一 draft，再以 release ID 核對；
   不能因 404 就重建／重傳。網路結果不明時先查回。
5. 公開並標示 latest，重讀 public tag／release／assets／manifest，唯讀執行 stable discovery。
   `update_release.discover('stable')` 是讀取；不要順便 apply/install。
6. 更新雙語下載文件、網站生成器及對應 browser assertion（包含 installer command 的固定版本），
   更新進度／handoff，部署後讀回網站。發布、安裝及 live acceptance 分別報告。

GitHub app 沒有 release／binary upload 工具時，可用已配置的 `gh`；先確認 repository 與帳號。
前輪使用 repository `TED-H-com/bat-agent-connector` 的 Primary maintainer 身分，不使用 reviewer
帳號寫入。Claude Code 的工具／帳號可能不同，不要依賴 Codex plugin link ID。

## 可直接交給 Claude Code 的開始指令

> 請先讀根目錄 CLAUDE.md、AGENTS.md、CONTRIBUTING.md 與
> docs/product/claude-code-handoff-2026-11-10.md，再讀適用產品與設計合約。
> 核對最新 origin/main、目前 branch／worktree 與他人工作歸屬，從新 isolated worktree 接續。
> #511 已合併但未發布；穩定版仍為 Connector 0.2.17／Desktop 0.1.13。
> 不重做已合併 Work loop 或歷史關聯功能。先評估未讀且已撤權關聯的 cleanup-only discovery
> 合約與最小驗收，或依最新產品優先順序選一個完整增量；沿用中央權威及原 operation key。
> 完成實作與適當驗證，保持英文／zh-TW，更新交接並分別報告 branch、merged、released、
> installed、live-tested 狀態。不要把 fixtures 當真人驗收，不要碰人工 BAT／checkout 或其他 worker 資源。
