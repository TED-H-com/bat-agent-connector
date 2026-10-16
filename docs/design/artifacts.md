# Artifacts：附件版本與派工前驗證

日期：2026-10-08。依 Tauri v2 §16 附件／跨主機接續、§19 清理，R05／R08。
歷史 spec 的 W05b、B04 與 A03 用於對照既有測試，不代表新範圍已完成。
A03 的人工單檔擷取另列 Part B1；本文件分為 A／B1／B2／C，目前實作 A、B1 與 B2 backend；B2 UI 另行整合。

## 固定來源版本（A／B／C）

Connector 起點 `566eba6b63e49eeca38383f4029a3a7abec98801`，原 spec `024c370`，套件 0.2.4。沿用 [checkpoints.md](checkpoints.md)、[work-items.md](work-items.md)、[api-v1.md](api-v1.md)、[resource-policy.md](resource-policy.md)、[dashboard.md](dashboard.md)、[交接紀錄](../handoff/2026-10-08.md)、[CONTRIBUTING.md](../../CONTRIBUTING.md)。

BAT 固定來源 `b7419892fbc9946799b64cca24c2ec8c7fa15c42` 的 [remote_server.rs](https://github.com/tony1223/better-agent-terminal/blob/b7419892fbc9946799b64cca24c2ec8c7fa15c42/src-tauri/src/remote_server.rs)、[commands/worktree.rs](https://github.com/tony1223/better-agent-terminal/blob/b7419892fbc9946799b64cca24c2ec8c7fa15c42/src-tauri/src/commands/worktree.rs)、[commands/git.rs](https://github.com/tony1223/better-agent-terminal/blob/b7419892fbc9946799b64cca24c2ec8c7fa15c42/src-tauri/src/commands/git.rs)。worktree:create 沒有起點 SHA；git_get_status_native 使用一般 git status；fs:upload-* 不是 Connector 授權的 artifact 合約。既有 v3.2.12 協定筆記不等同此 commit 或每台主機的安裝版本。

沒有新增 BAT channel。channels.py 仍拒絕 fs:readFile／fs:upload-tmp-begin；沒有 PTY 或 agent shell 傳輸。若日後改採 BAT 檔案 channel，須依 CONTRIBUTING 另行證明來源、分類與 tier 拒絕測試。

## 現有與新增行為差異（A）

| 已核對現況 | Part A 新增 |
|---|---|
| checkpoint 無附件，continue 固定 cp.host，source_head 只供顯示 | 凍結 refs、同主機 materialization、來源 guard 與同 parent 確認 |
| start_in_worktree 依 verify.start／session.start／send 執行 | 只增加 keyword-only before_send callback，保留 start／send reconcile |
| work item fingerprint 不含附件 | typed attachments；有附件才增加指紋欄位，空集合保持舊算法 |
| ApiV1.handle 在路由前讀 JSON，MAX_BODY=200000 | 固定 content route 在讀 body 前分流，先認證／檢查意圖，再有界接收 |
| OperationService 共用 intent／step replay | 唯一修改是 additive wake(operation_id)，沒有 stream 傳給 worker |
| task context_refs.attachments 只是字串 | 原樣保留歷史，不推測為檔案或已 verified artifact |
| Dashboard 草稿已有 localStorage 文字 | 延伸相同草稿保存 refs／上傳狀態；尚未成功的 File 只在記憶體 |

## 必要前置條件（A；C 的目標選擇另行實作）

- Connector 的 journal 目錄或配置 store_root 是自有實體目錄，無 symlink；設定來自 0600 的 BATC_TASK_SETTINGS。
- observe 可讀／download；manage 可 upload／修改 work item；operate 可 checkpoint.create；start 可 continue／確認原版本。work item 連回派工另須 manage 與 expected_work_item_fingerprint。
- 同主機 continue 需要既有 writes、orchestrate、managed_roots、BAT workspace 與 [verification] ssh_hosts alias。host helper 支援 Python 3.9+（不假設 macOS 有 3.10）、Git 2.31+（absolute common-dir paths）、dirfd／no-follow、link／fsync。probe 回 git_version；已觀測 readiness 不符在 admission 拒絕 ARTIFACT_ADAPTER_UNAVAILABLE，capabilities 保留版本與原因。
- SSH BatchMode、StrictHostKeyChecking=yes、既有 known_hosts。alias 只能由配置提供，不能來自 request。
- ArtifactRef 必為 ready 的精確版本。Dashboard 始終傳 expected_source_head_sha；有附件的其他 client 也必須帶它。舊 client 無此欄位且無附件，保持既有無 guard 合約。

## Store、IDs 與限制（A）

SQLite Journal 是 metadata 權威，沒有第二份 execution／task store。新 artifact ID 為 art_<32 hex>（由 upload operation ID 衍生）；revision 為正整數。ArtifactRef 固定 `{artifact_id, revision, digest}`，digest 為 64 小寫 hex SHA-256；不接受 latest。完成版本的 bytes、size、digest、type、name、來源不變。重傳相同內容不作 dedupe。

| Table | 內容 |
|---|---|
| artifacts／artifact_revisions | ID、latest ready revision；immutable metadata、storage key、建立 operation、receiving／ready／unavailable 狀態 |
| artifact_uploads | 最小的 revision／配額預留、deadline、attempt／接收事實。沒有 bytes／base64 |
| artifact_references | 精確 ref、role、owner kind／ID、建立／釋放 operation；owner 只用本包會寫的 work_item、checkpoint、operation |
| artifact_materializations | ref、parent operation、host、managed path、attempt、dispatch evidence、pending／transferring／uncertain／verified／blocked |
| checkpoint_source_confirmations | parent、確認 operation、source HEAD、actor、時間；只確認原 inputs，不換 commit／ref／target |

新 revision 必帶 expected_latest_revision；同 artifact 的未結束預留不能搶走。交易內配置 revision，錯誤回 REVISION_CONFLICT／UPLOAD_IN_PROGRESS。取消／到期後不重用已預留號，允許缺號。same actor/key/request 回同 operation；改 metadata 沿用 IDEMPOTENCY_CONFLICT。

Store setup 與 journal setup 相同，daemon 建立目錄與 marker，不是 operation。Root 預設 `<journal.parent>/artifacts`；所有 component 經共用資源政策驗證，未知既有內容不接管。

```text
<store_root>/.batc-artifact-store
<store_root>/staging/<operation_id>/a0001/content
<store_root>/revisions/<artifact_id>/r00000001/content
<store_root>/revisions/<artifact_id>/r00000001/manifest.json
```

目錄 0700、接收檔案 0600、正式內容 0400。storage key 由 IDs 生成；name 只展示／作 worktree safe name，不決定 store 路徑。bytes／manifest fsync，無覆寫發布並 fsync parent；SQLite record 之前或之後失聯均從實際 hash 與 immutable manifest 回查。

| [artifacts] 設定 | 預設 |
|---|---|
| max_file_bytes | 16 MiB |
| max_selection_count／max_selection_bytes | 20／64 MiB |
| max_store_bytes | 512 MiB，含正式、partial 與未釐清 reservation |
| mcp_max_file_bytes | 256 KiB decoded；模型必須生成每個 byte，大檔用 CLI／Dashboard |
| upload_window_s | 3600 秒，等待 content 用一次長 Wait |
| transfer_timeout_s | 300 秒 |

capabilities 回 limits／store readiness／host helper readiness。降低 quota 不刪內容；store full 拒絕新上傳，operator 調高 quota。本包無 store delete 或 TTL。terminal operation 釋放 reservation，並移除其 operation ID 的自有 staging（包括取消、失敗、到期），不清任何別人的資源。

## 輸入／輸出與 surfaces（A）

全部 mutation 是 ActionDef；HTTP／MCP／CLI 是相同服務的薄 adapter。MCP confirm=true、CLI --confirm；Dashboard 一次主按鈕是意圖，不增加重複確認。Audit 只記 metadata／hash，不記 bytes、token 或文字全文；依既有 rate limit 與 upload quota 限流。

| Action | Scope | 輸入 | 輸出 |
|---|---|---|---|
| artifact.upload | manage | target {} 或 artifact_id；display_name／media_type／size_bytes／expected_digest；新 revision expected_latest_revision | operation、content_url、預留 ID／revision；成功結果含 digest／size／type |
| checkpoint.create（擴充） | operate | artifacts: ArtifactRef[] | 凍結的 artifacts |
| work_item.create／update（擴充） | manage | attachments: refs 加 role=input/result；update expected_version | version／completion fingerprint |
| checkpoint.continue（擴充） | start | params.artifacts 或 checkpoint 集合；instructions／agent／work_item_id；pre expected_source_head_sha、可選 expected_work_item_fingerprint | 原 session／worktree 結果，加 input_manifest_digest／materializations |
| checkpoint.continue.revalidate | start | parent operation_id；observed_source_head_sha；expected_input_manifest_digest | 確認回執，既有 resume 同一 parent |

沒有 artifact.materialize action；resume parent 補傳。沒有 continuation_inputs table：immutable operation.params 與 checkpoint 是原 inputs。Canonical UTF-8 JSON（sorted keys、無多餘空白、保留 Unicode）的 SHA-256 計算 input manifest：checkpoint ID／commit／excerpt hash、完整 refs、instructions hash、agent、work item 指紋。digest 回 external_refs／result；不增加 expected_checkpoint_manifest_digest。

| HTTP（/api/v1） | MCP（最多三個） | CLI |
|---|---|---|
| POST /artifacts，JSON metadata | artifact_upload（base64，小額限制） | batc artifact upload FILE --confirm |
| POST /artifacts/uploads/{op}/content，octet-stream、固定 Content-Length | upload 工具薄轉接相同兩步流程 | FILE 只在 client 讀取，不傳絕對路徑 |
| GET /artifacts?limit=&cursor= | artifacts_list | batc artifact list |
| GET /artifacts/{id}/revisions/{revision} | artifact_get，含 materialization evidence | batc artifact show ID N |
| GET /artifacts/{id}/revisions/{revision}/content | metadata 提供下載 route，不回大型 bytes | batc artifact download ID N --output FILE |
| POST /operations，continue／revalidate | confirmation 用 operation_submit | batc checkpoint continue／revalidate；原 batc op ID --resume |

GET observe；download 為 Content-Disposition attachment、nosniff、no-store，不 inline preview。列表 1–200，預設 50，有穩定游標。materializations 在 artifact_get 與 continue result，不新增 MCP 查詢／materialize tools。

## 上傳 adapter／operation 恢復（A）

content route 在 ApiV1.handle 的 _read_body 之前匹配。先檢查 Host／Origin、bearer、manage、operation actor（同 actor／admin）、action=artifact.upload、允許的 waiting_external 狀態、revision reservation、deadline、固定 Content-Length 與 expected size。任何 body byte 讀取前拒絕 chunked／unknown type／錯誤 state／長度，保持 JSON 其他入口的 MAX_BODY。

adapter 本身寫入 operation 的 scratch attempt：先在 journal 保存 attempt intent，O_EXCL／O_NOFOLLOW 開啟 ID 衍生的 staging 檔，bounded read，累計 size／SHA-256，fsync，記實際接收結果。沒有 socket stream 交給 worker。unknown receiver 仍保留 reservation，不啟動第二個 writer。confirmed incomplete 可由同一 upload operation 再接收下一個 attempt，第一版從 byte 0 重送。

worker 沒有完整 content 時用長 Wait，delay 為 upload window 剩餘時間，不 poll 或反覆寫 transition。content 完成後 OperationService.wake(op) 只讓 waiting_external due now 並 kick，不改其他狀態。worker step verify／publish／record，先記 proven result 再在外面判斷 mismatch／NeedsAttention，避免 failed-step replay 卡死。

| Step | 恢復讀回 |
|---|---|
| upload.reserve | operation ID 對應預留，不配第二個版本 |
| upload.verify.<attempt> | 實際 bytes hash／size，不信 request 宣告 |
| upload.publish | 正式目錄 manifest／hash 符合原 operation／revision 即補記，不覆寫 |
| upload.record | 同交易 ready／refs／事件；重跑回原 result |

deadline 無完整 content 就 failed/UPLOAD_EXPIRED；任一 terminal state 釋放 reservation並移除自己的 staging。若取消／失敗前已 publish，正本仍保留；未 ready 的正本記 unavailable，仍計入 store quota，不把它當空間釋放。marker／publish ACK 不明先讀回；staging partial 不可作 ready。正式檔案被改／遺失顯示 ARTIFACT_CONTENT_UNAVAILABLE，不從 client path 自行補抓。

Scratch reaper 是獨立於 task worker／push 的背景 loop；檔案刪除在 thread，SQLite 更新在 daemon event loop。每個失敗記 operation ID 與 exception class，下一輪重試；未完成 reaping 的 rows 保留 reservation。cancel／receive／upload admission 的 reaping 為 best-effort，不能改已提交的回應或取代原 exception。

## 附件關聯與 work item（A）

work item 附件在既有交易與 management_applied 一起寫，role／ID／revision／digest 納入有附件時的 fingerprint。空集合維持舊 fingerprint，migration 不令舊 approval 失效；新增／換 result revision 重新待確認。被移除 refs 保留釋放事實，不刪 bytes。

Checkpoint create 凍結 refs；之後 immutable。continue 的 params.artifacts 若提供就是完整選擇，否則沿用 checkpoint 集合；工作詳情表單合併去重後明確提交。既有 checkpoint 接續表單新增 upload refs 掛在 operation，沒有改舊 checkpoint。建立 work item 操作連結是 handler 的第一個 idempotent step，以 operation 為 key，檢查原內容指紋；OperationService.create 不改。

## Host helper／worktree materialization（A；B 擷取另行審查）

副本固定在 `<worktree>/.batc-inputs/<artifact_id>-r<revision>/<safe name>`。safe name 來自 validated display name，無 separator／control，不能是 .、..、.git。clone common info/exclude idempotent append `/.batc-inputs/`；沒有 template 時，以 common-dir fd／no-follow 補建 info（0755）與 exclude（0644、O_CREAT／O_EXCL）。既有 info 必須是 directory，exclude 必須是 single-link regular file；拒絕 symlink／shared file。worktree.prepare 的 clean check在 materialization 前。派工 prompt 只列 worktree-relative path、ref／digest；絕無 client-local absolute path。

每個 materialization set 只屬一個 continuation operation。rows 是「T 時刻為 X verified」的派工證據，不是 live inventory。無跨 operation reuse、generation、removed state、shared cache、host flock 或 host quota reservation；傳輸前檢查 free space。dispatch 後副本是 agent 的工作副本，store immutable revision 仍是 reference。

Reviewed cleanup 只能豁免 exact verified replicas，不能把 ignored `.batc-inputs/` 全目錄視為可刪。
`artifact_cleanup.replica_evidence(ops, item)` 是預備接入 cleanup `_replica_evidence` 的唯讀投影：
只接受 proven checkpoint worktree 的 creation operation，核對 checkpoint host、prepare intent、external refs、
materialization host/path/ref/size/digest、verified receipt 與實際 transfer attempt intents。
輸出 `replica_manifest=[{path,bytes,digest}]` 與 exact `.owner`／`.attempt-N`／`.closed-N` names；
不猜 attempt range，不豁免其他內容。Store 正本也必須可讀且 hash 符合，否則副本維持一般內容，避免清掉唯一副本。
Cleanup host helper 仍逐檔檢查 no-follow、single-link、目前 size/hash、tracked 狀態及多餘／缺少內容；
edited、unknown、unverified 或跨 operation/host/path 的內容需要原本的保留／reviewed discard 規則。
此投影已接上 shared cleanup，精確副本與 resume 原件查核通過 mock／暫存 Git 整合測試；尚未安裝或實機驗收。

helper 是固定、版本化 Python 3.9+ 程式，透過既有 alias 執行；raw bytes stdin、有界 JSON stdout，沒有 general script API。每個寫入先經 resource_policy.check_checkpoint_worktree 與 artifact destination 規則，檢查建立 intent、clone marker與真實 root。所有 worktree 以下 component 以 dirfd/no-follow 開啟；拒絕 symlink、unknown final、hardlink／非 regular file、traversal／prefix escape。不以 norm 字串當 canonical 證據。

attempt file O_EXCL／no-follow，fsync；publish 用 link(attempt, final) 再 unlink attempt，final 存在時不能覆寫。然後獨立讀正式 file hash／size／path。ACK 遺失先查 final／partial；可證明未完成才下一 attempt，不盲傳。其他程序以相同 OS 身分仍可改 working copy；gate 發現變動就 blocked，不宣稱 Connector 鎖隔離所有程序。

## Dispatch／source guard／confirmation（A；B04）

不 restructure start_in_worktree；增加 keyword-only before_send async callback，await 在 send step 前。已有 send step 記錄時先 reconcile 既有 send，不因後來 source／agent commit 而改稱未派工。

1. 保存 operation 是 durable intent。handler 第一 step idempotently link work item／refs；固定 clone/worktree/session UUID5 與 message ID沿用現有 external_refs。
2. worktree.prepare 核對 selected commit 與 clean。
3. source guard 在 materialization 前唯讀比對 expected_source_head_sha；confirmed source advance 回 SOURCE_MOVED，未知回 SOURCE_UNAVAILABLE。有附件必須有 guard；舊 client 無 head／附件保持原合約。
4. 對完整 selected refs：store hash、host receive、verify、publish、獨立 readback。全部 verified 才 start_in_worktree；partial successes 留 evidence，source files／sessions 不修改。
5. 沿用 verify.start／confined session.start reconcile。
6. before_send 每次 send 尚無 step 記錄時重新讀 source guard、worktree 起點與附件 hash／path／size；先前成功 gate 不授權重啟／長等待後 send。記 proven evidence，再判 mismatch；任何不符 blocked，保留同 session、原 artifact／work／draft。
7. send 的 stable message ID／turn／transcript reconcile 沿用。已可能送出時只回查，不 duplicate execution。

confirmation action核對原 input_manifest_digest、當前 source HEAD、parent 是 SOURCE_MOVED／SOURCE_UNAVAILABLE 的 needs_attention，且沒有 send 記錄。保存 minimum confirmation HEAD／actor／time，再經既有 resume 恢復原 parent；不換 commit、refs、workspace、instructions。重跑同 confirmation 不再重複 resume。target／artifact mismatch 不可用 source confirmation 繞過。

Admission 使用與 run 共用的 input manifest lines helper，依精確 refs／immutable display names 合併 instructions。超過 MAX_PROMPT_CHARS − 1500 回 422 INVALID_PARAMS，不建立 operation／clone／worktree；run 保留相同 check 作 backstop。

回查是 observed_at 的證據，不是跨主機原子 snapshot；不鎖人的來源。B04 拒絕已觀測的前進，固定選定內容不隨 latest 改變。

## Dashboard drafts（A）

upload 在選檔時開始。沿用 `batc.draft.<scope>` localStorage，舊文字可讀入；新 JSON 存 text、uploaded refs、pending file names／upload operation／key。成功 File refs 顯示 name、type、size、revision／digest。沒有 server draft IDs／holds／generations／IndexedDB。

尚未成功 File 在記憶體提供重試。reload 後明確顯示「此檔案尚未上傳成功，請重新選取」／英文同義文字；browser 無法重開 local file，不假稱可自動重試。已 upload成功的 refs 與需求文字保留。

只有提交操作 succeeded 才 clear；202／upload成功／blocked／uncertain／failed 都不清。clear 前核對 localStorage 還是提交 snapshot，等待期間新編輯留下。重開頁面查原 upload／continue operation，不以新 key 重開 session；SOURCE_MOVED 透過 operation_submit confirmation續原 parent。

工作詳情／checkpoint continuation都有 picker；operation頁列 ref／path／verified time／逐項失敗。沿用 row／panel／secondary、fill()、holdRender、en/zh-TW、CSP。390px 顯示可換行，不長 ID 溢出。

## 保留事實（A 的 references；未來 artifact cleanup）

本包沒有 retention_facts()、TTL／retention-day settings、store delete或 sweeper。quota滿由 operator調高。集中 cleanup已把 artifact列 RESOURCE_KIND_UNSUPPORTED；不在本包繞過。

reference rows只有work_item、checkpoint、operation的writer。未完成工作、未接受成果、可接續checkpoint、active／uncertain command／execution、shared／restore流程日後仍需精確bytes；純歷史不應永久擋整理，但須有釋放／接收回執。work done／archive、ancestor／squash、host失聯都不是artifact接收證據。未來artifact-cleanup spec再定holds／期限／read model；不先為無writer的owner建立表。

## 失敗代碼（A；B/C 代碼保留於後續段落）

| Code | 處理 |
|---|---|
| INVALID_ARTIFACT_REF／ARTIFACT_NOT_FOUND | 422／404；選合法精確版本，不補 latest |
| INVALID_PARAMS | instructions＋manifest 超過 prompt budget 在 admission 回 422，沒有 operation 或 host write |
| ARTIFACT_TOO_LARGE／ARTIFACT_SELECTION_TOO_LARGE | 413／422；保留原稿 |
| ARTIFACT_STORE_FULL／UPLOAD_LIMIT | 409／429；調 quota／等 reservation 結束 |
| REVISION_CONFLICT／UPLOAD_IN_PROGRESS／IDEMPOTENCY_CONFLICT | 409；讀既有 intent，不搶 revision |
| UPLOAD_EXPIRED | failed；釋放 reservation／自己 staging |
| UPLOAD_INCOMPLETE | 等待同 operation 下一 attempt；超過 window 到期 |
| ARTIFACT_DIGEST_MISMATCH／ARTIFACT_SIZE_MISMATCH | needs_attention／逐項 blocked，沒有 first command |
| ARTIFACT_CONTENT_UNAVAILABLE | missing／corrupt 正本；不改原 ref |
| ARTIFACT_ADAPTER_UNAVAILABLE／NO_MANAGED_ROOT／NO_WORKSPACE | capability blocked；helper probe 的 Git 版本低於 2.31 回明確原因，不回 manual cwd |
| FORBIDDEN／TIER_DISABLED／DESTINATION_MANUAL／DESTINATION_UNKNOWN／BINDING_MISMATCH | shared policy拒絕；confirm／force 不繞過 |
| SOURCE_MOVED／SOURCE_UNAVAILABLE／CONTENT_CHANGED／START_MISMATCH | needs_attention；同 parent 回查／明確確認或修正，不換 input |
| UNCERTAIN／UNCERTAIN_UNRESOLVED | 查原 final／meta／turn／transcript，不盲目重送 |

## 實際副作用與預計修改檔案（A）

只寫journal／events、Connector store／operation scratch、managed worktree／common info/exclude、既有confined start／first send。無人工原檔／HEAD／index／refs或source session mutation。

| 檔案 | Part A 修改 |
|---|---|
| artifacts.py（新）／artifact_host.py／artifact_host_helper.py／artifact_client.py（新） | store、upload adapter／actions、refs、獨立 scratch reaper、固定 Python helper／Git readiness／read-back／no-follow exclude repair |
| task_journal.py | 不佔 user_version 的 idempotent additive DDL；每次 open 在 numbered migrations 後執行，舊附件空，舊字串不遷成 artifact |
| resource_policy.py | storage／materialize mutation與 shared destination checks，配合cleanup guard |
| operations.py | **只有 additive wake(operation_id)**；不改 create／resume／cancel／STATES／table／replay |
| checkpoints.py | frozen refs、handler第一step連結、共用 manifest helper 的 admission 長度檢查、materialize／guard、before_send callback／confirmation；保持其他包修改 |
| work_items.py | typed attachments／指紋／transaction；保持既有 Connector work-item／observation／cleanup 修改；無 Hub import 依賴 |
| task_verifier.py／task_daemon.py／api_v1.py／mcp_server.py／cli.py | settings／setup／actions、獨立 reaper loop／best-effort cancel、受限content route／download／三MCP tools／CLI／capabilities |
| dashboard/app.js／app.css／i18n.js | selection upload、localStorage草稿與pending提示、operation evidence，兩語系 |
| tests/test_artifacts.py 等 | 下列接受情境、HTTP/MCP/CLI契約與policy失敗 |
| api-v1.md／checkpoints.md／work-items.md／dashboard.md；README x2／CHANGELOG／兩skills | 完成部分與尚未涵蓋；計畫§08/§12/§13、B04、MCP 256KiB與大檔路徑 |

Artifact DDL 只用 CREATE TABLE IF NOT EXISTS 與依 table_info 缺欄位才 ALTER；不讀寫 user_version。
版本號屬一次性的 data migrations：1 既有 event copy、2 observation history、3 deployment history。

Reviewed cleanup 只使用 `artifact_cleanup.replica_evidence` 投影的精確副本：creation intent／worktree
binding、同 operation／host 的 verified materialization、固定 revision／digest、實際 transfer attempt
與 immutable store 原件皆須吻合。僅提供精確路徑、bytes／digest 及已記錄 helper 名稱；不豁免整個
`.batc-inputs/`。已修改、額外、tracked、symlink／hardlink 或原件缺失／損壞的內容仍受一般保留規則保護。
每個未完成的 cleanup mutation 在 host flock 的最後准入、live consumer 檢查之後，重新驗證 store
原件及 accepted evidence；改變即 PREVIEW_STALE，不換新的 manifest。已完成 step 優先重播，
未確認的 destructive step 先 read-back；resume 不因已有 preserve receipt 而省略下一個 mutation 的原件查核。
測試 `test_cleanup_resume_rechecks_original_before_removing_accepted_replica` 以實際暫存 Git、bytes 與
MockBat 驗證 preserve 後暫停，再恢復時原件缺失／損壞保留最後副本，原件未變則正常完成。
Artifacts 不佔號，也不以建表 stamp 跳過其他包的資料遷移。Migration 測試 stamp 3 只證明保留任意既有版本；
不假裝本分支已執行 deployment history migration。

## 測試計畫（A；B04）

BAT只用mockbat；git用temp repo／LocalRunner／RealGitLog。byte helper測試本機temporary worktree，fault adapter模擬ACK loss／restart，不向實機寫channel。

| 預計測試 | 覆蓋 |
|---|---|
| test_artifact_upload_is_immutable_and_idempotent | 精確版本、same key、CAS、gaps、無覆寫 |
| test_artifact_limits_include_partial_and_concurrent_reservations | 實際長度／hash、partial quota、並行、MCP小限額 |
| test_upload_publish_lost_reply_and_restart_read_back | 正式publish後重啟回查、不第二版本 |
| test_cancelled_unsettled_publish_keeps_original_and_its_quota | publish outcome 未結束後取消，保留正本且仍計 quota |
| test_B04_missing_original_blocks_then_resumes_same_parent | store missing 的 proven error 先記 step，再 blocked；還原後續同 parent |
| test_upload_window_expires_and_removes_only_its_own_staging | deadline、terminal scratch cleanup／reservation釋放 |
| test_reaper_failure_never_stops_ticks_or_fails_cancel | cleanup 的 ValueError／policy／OSError 不改 cancel／receive／admission／ticks，reservation 保守保留 |
| test_slow_reaper_never_delays_task_ticks | 慢速 rmtree 不阻塞 task worker，清理完成後才釋放 reservation |
| test_operation_wake_only_moves_waiting_external | 其他狀態不變，不poll |
| test_attachment_change_invalidates_work_item_approval | 有附件才改fingerprint、version衝突 |
| test_artifact_migration_preserves_existing_journal_and_empty_manifests | 舊IDs／approval／空附件、idempotent DDL |
| test_replica_projection_*（test_artifact_cleanup.py） | 純讀 exact creation/materialization/transfer binding；原件 missing/corrupt 保留；不猜 attempt 缺號 |
| test_replica_projection_drives_cleanup_content_contract | 真實暫存檔案 unchanged／edited／extra／missing／symlink／hardlink／tracked；已接 shared cleanup module，所有 content-contract cases 正常執行 |
| test_artifact_http_mcp_cli_contract_and_scopes | body前auth／actor／state／type／length、download headers、scope、same action |
| test_artifact_policy_refuses_escape_before_any_host_write | worktree內no-follow／traversal／hardlink／unknown拒絕 |
| test_materialized_inputs_are_git_excluded_and_inside_the_worktree | cwd內relative paths、dirty=0、exclude冪等 |
| test_continue_prompt_limit_is_checked_before_operation_or_host_write | 過長 manifest 在 admission 拒絕，零 operation／host write |
| test_artifact_probe_reports_git_floor_and_blocks_unready_admission | Git 2.30 拒絕、2.31／Apple Git 通過；capabilities 版本／原因、unready admission 拒絕 |
| test_artifact_materialization_creates_missing_git_exclude | 無 info／exclude 的 managed clone 補建後 dirty=0 |
| test_artifact_exclude_refuses_non_directory_or_shared_files | common info／exclude 的 symlink／non-directory／hardlink 拒絕，外部 bytes 不變 |
| test_B04_transfer_interrupted_before_dispatch | 中斷零first-send／start，保留work／artifact／draft，resume同parent |
| test_B04_digest_size_or_path_mismatch_blocks_first_command | 不符不verified、不派工、partial evidence留下 |
| test_B04_source_advanced_after_preview_keeps_selection | source advance拒絕、confirm同parent／session／refs |
| test_B04_resume_after_start_or_send_ack_loss_never_duplicates_execution | UUID5／message ID／turn reconcile；agent commit後不重檢起點 |
| test_B04_inputs_changed_after_start_keep_session_and_block_send | 最後fresh guard／hash阻擋，不另session |

Dashboard Playwright不進CI：390px、en／zh-TW，選檔upload、text/refs reload、failure留草稿、success只清提交snapshot、prompt無localpath。交付跑ruff全repo、pytest全suite、app.js的.mjs copy node --check。

## 人工單檔擷取（Part B1）

B1 只將明選的人工 session 檔案保存為既有 immutable ArtifactRef，不新增 snapshot、
managed-result lineage、accept、work completion 或跨 host Git 接續。來源須由目前 BAT
workspace 的完整 session ID 加現有 resource policy 正面分類為 `manual`；沒有 tab、
orphan、unknown、Connector creation record 或目前已移除的 host 一律拒絕。每次讀取前
重新讀 workspace/session metadata/root 與 registry policy，結束後再確認相同 binding。
不以路徑、client 自報 project/execution ID 或「沒有 managed 記錄」單獨推論人工來源。

| 入口 | scope／固定資料 |
| --- | --- |
| `POST /api/v1/artifact-capture-previews`、RPC/MCP `artifact_capture_preview`、CLI `artifact capture-preview` | `observe`；只接受 `host`、完整 `session_id`、`relative_path` |
| `artifact.capture`（既有 `/operations`／RPC）、MCP `artifact_capture`、CLI `artifact capture` | 同時 `manage` + `observe`；target `preview_id`、params `preview_token`、preconditions `expected_fingerprint`；沿用 operation key、cancel、readback |
| artifact list/get/content、attachment selection | 沿用既有 `observe`／各使用動作的 scope，固定 revision/digest；不擴張 task-scoped capability |

Preview 是有界、有效 10 分鐘的 signed stateless document（最多 24 KiB token），不寫
preview rows，因此 observe 請求不建立無界永久 storage，無需 preview reaper。HMAC 分隔
domain，綁定 daemon key、credential 的不可逆 identity、actor、scopes、admin flag、來源
binding、檔案 evidence 與 fingerprint；同 actor label 的另一張 token 仍不能套用。
不回傳 credential hash。過期／篡改／不同 credential 零 reservation 拒絕；新請求須重新
preview。Operation admission 保存已驗證的原 document 作中央證據，重啟後不依賴 client
重新提交 lineage；接受後的同 key 查回不因 preview 過期建立第二個 artifact。
同 key replay、resume、cancel 仍需目前的 `manage` + `observe` 與原接受時的 credential／
principal binding；不能只憑相同 actor label、另一張 manage token 或 admin fallback 接管。
這些控制查證已保存的 authority，不重新套用 preview TTL、來源讀取或 quota admission；
同一 credential 的合法重試仍查回原 operation，保留完成／不確定 step 的 readback。

Source binding 包含 host/profile、完整 session ID、目前 tab 身分欄位、已觀察的 cwd/root、
inventory 首見身分，以及 host/SSH 設定的不可逆 binding。人工 BAT 沒有 Connector creation
receipt；這只是目前觀察到的人工來源證據，不能變成 managed 所有權證明。根目錄 inode
与檔案 inode、mode、link count、size、mtime/ctime ns、HEAD 及 SHA-256 構成固定 evidence。
不因檔案同名、內容相同或 session ID 重用而跳過重新查證。

同時最多四個有界 source reads；額滿回 CAPTURE_BUSY，不建立永久等待佇列。
只接受 UTF-8 長度 ≤4096 的 POSIX relative path：不能 absolute、空 segment、`.`、`..`、
`.git`（任一 segment）、反斜線或控制字元。固定 host helper 由既有配置 alias 選擇，沒有
新 BAT channel、任意 shell、client 本機路徑或 request 提供的 host 絕對路徑 transport。
固定 helper 使用 Python `-I -S -B`，停用工作目錄／usersite／site startup 和 bytecode writes；
不是 host-account trust proof，仍沿用既有可信 SSH host 配置。檔名限制沿用 upload 的 safe_name。
從 `/` 到 observed root、再到每層父目錄都以 dirfd + `O_NOFOLLOW` 開啟；最後檔案以
read-only + no-follow + nonblock 開啟，只准 regular file、`nlink == 1`。Symlink、hardlink、
FIFO/device/socket/directory、超過既有 `max_file_bytes` 一律拒絕。HEAD 用固定 read-only
Git 命令及 `--no-optional-locks` 讀取；不讀工作目錄 status，不 chmod、stash、commit、
改 refs/index/permissions，不 start/resume。Helper 只在記憶體有界讀 bytes，不在來源建暫存。

讀取前後比較 root/file identity、路徑目前解析的同 inode、HEAD、大小及 timestamps；capture
亦必須與 preview 的 digest/evidence 完全一致，中央再對傳回 bytes 驗證 size/SHA-256。
這些檢查可拒絕可觀察到的寫入／替換／來源移動，**不是鎖住人工 writer 的 snapshot**；
不能證明讀取期間沒有無痕並行改動或 HEAD 離開後返回。保存的是經檢查的單檔 bytes 與
實際觀測證據，完整 dirty snapshot 始終 `supported=false`。

沿用 ArtifactStore quota、reservation、attempt staging、verify、publish、terminal reaper；
operation ID 決定新 artifact ID，B1 不追加既有 artifact revision、不自動建立 project link。
`artifact_capture_sources` 是 versionless additive DDL，依 operation/revision 保存中央
source proof；get 的 `source.kind=manual_capture` 來自該 proof，不接受 client 自報。
`capture.receive` 完成需同時有完整 staging bytes 與 source receipt；重啟只信兩者相符。
未完成的 read 可在同 operation 重讀原固定來源，不換最新內容；完整 receipt 後不再讀來源。
publish 回覆遺失沿用既有 readback，最多一份 immutable revision；cancel 在下個 step 停止，
接收中的 scratch 留給既有 terminal reaper，已公開的內容保留真實 receipt。

目前 API principals 是 daemon-wide scopes，沒有 per-project ACL。B1 不聲稱新增私人
artifact：與 upload 相同，獲 `observe` 的 principal 可讀已 ready 的 artifact；沒有 `observe`
或 task-local capability 不可讀。Preview/提交不能借另一個 credential，MCP 不回退 admin。
Project/work-item 附件仍走既有 authority 和固定 ArtifactRef，不因 capture 放寬其操作 scope。

### 共用人工單檔 UI

Browser 與 Tauri 使用 `desktop/src/capture.js` 的同一表單：人工 session 頁預填 host／完整
session ID，附件草稿旁亦可明填這兩個欄位及一個遠端相對檔案路徑。不是本機 OS 選檔器，
沒有目錄 listing。先讀現有 session observation 正面確認 `manual`，再呼叫中央 preview；
scope 與 capability 限制 UI 可用按鈕，中央仍重新查核當下 authority／來源。

檢閱區顯示檔名、bytes、SHA-256、host/session、來源 root／repository、HEAD 和到期時間；
使用者勾選已檢閱並按保存才提交既有 `artifact.capture`。預覽、保存或狀態讀取進行中時，
預覽、保存、加入附件與「擷取另一個檔案」維持可聚焦並標示忙碌，再按一次不會另送請求；
按鈕在該工作結束後才依範圍、輸入、意圖、過期、拒絕或進行中的擷取停用。保存結束而按鈕
關閉，或按下擷取另一個檔案之後，焦點移到下一個可用控制項。畫面已換走的晚到回覆不移動焦點。
完整 preview、固定 envelope、
operation key、接受後 operation ID 保存在既有 endpoint/server/principal namespace。
回覆遺失保留原 key 重試；已接受的意圖不因 preview 過期換新內容。重載以原 operation
readback 恢復；切換帳號／backend 不沿用別人的草稿或 key。損壞的 preview 不造成整頁失敗，
可恢復的原 operation ID／key 仍保留。事件 refresh 等待既有讀取後再讀新 evidence，失敗不 ACK。

成功必須查回 ready revision，驗證原 operation 的 action／preview ID、檢閱 digest 及中央
`manual_capture` source receipt，才出現可選用 ArtifactRef。「加入附件草稿」是獨立明確操作；
不自動存 work item／推論 project 所有權。Tauri 僅新增固定 preview POST，typed body 只准
host、session ID、relative path；沿用 trusted central/native credential，不新增 path/URL proxy。

`npm run test:capture` 以生成後 browser assets、真實中央 HTTP／固定唯讀 helper、暫存 Git
來源與 MockBat 驗證二進位內容、reload readback、原 bytes/index/refs 不變且沒有 BAT writes
或新增 project/work item。Browser/native IPC fixtures 另涵蓋 scope、過期、unknown 來源、
lost reply、帳號切換、錯誤回覆、損壞草稿、事件讀取失敗及 en/zh-TW 的 390/768/1440 排版。
這些不等於實機 host／Windows WebView 驗收。中央驗收另涵蓋 HTTP/MCP/CLI、credential、
篡改、所有 path/file 拒絕、中途改動/rebinding、quota、cancel/restart。

## Managed 成果擷取與接受（Part B2 backend）

完整界面／恢復／scope 見 [managed artifact 合約](artifacts-managed.md)。B1 保持人工来源限定；
managed preview 使用獨立 `/artifact-managed-capture-previews`，只接收完整 session、相對檔案
與 execution operation 或 task/command selector。中央保存的 accepted send/start receipts 才是 lineage，
不是 client 自報 commit／工作項目關聯。每次有界 no-follow 讀取前後重查原 binding／HEAD／bytes。
GUI tab 非必要，headless session 仍需 registry creation 與正面 live cwd/root 證据。

`artifact.capture.managed` 使用 `manage` + `observe`、原 credential 的固定 preview/key，
沿用既有 reserve/receive/publish/record。來源 proof 與實際 immutable bytes／revision/digest 一起保存。
`artifact.accept` 使用 `approve`，保存精確 revision/digest、source fingerprint、中央 lineage、
實際觀察 HEAD 及 bounded review receipt。接受的是這版內容的人工 review；不標記 work/task complete、
merged、deployed 或測試通過。已接受的 revision 不會因 latest 或 live 來源前進而換內容。

HTTP/RPC/MCP/CLI adapters 已接同一中央 action；共用 UI 已接 managed capture／固定 revision acceptance，installed/live 證據另行驗證。

## 主機與 repository 同步（Part C，依產品負責人澄清校正）

以[原始需求與後續澄清](../product/realignment-v2.md#原始需求與後續澄清)為準。正常 checkpoint
接續留在原 BAT 主機的獨立 managed 工作區。其他主機不自動接續 workspace；若使用者明確
需要另一主機取得程式碼，使用該 workspace 已綁定的 GitHub repository 與已發布 branch/commit。
中央同步使用 fetch 後驗證固定 SHA，不在人工目錄執行 pull/merge，不換成 branch 最新 SHA。

已實作明確 repository binding、numeric GitHub identity、published SHA preview 與 fresh managed carrier；
合約見 [repository-sync.md](repository-sync.md)，browser/native 共用 published-start 表單。
目標需 managed root、對應 BAT workspace、host readiness 與 caller 權限；同名路徑或 repository
名稱不能授權操作。固定 commit 不可取得時保留原選擇並停止，不讓 Agent 碰人工來源。

直接 Git pack/bundle relay 及未發布成果跨主機搬運不列交付條件。既有固定 artifact bytes
上傳、managed capture/accept、下載與 materialization 仍是產品能力；它們不會搬動或接管原 workspace。

## 尚未涵蓋

- B1 的 HTTP/MCP/CLI 與共用 browser／desktop 人工相對檔案路徑表單已實作，未宣稱 OS 檔案選擇器或實機驗收。B2 backend／共用 UI 與明確 repository 的已發布版本同步已整合；native 選檔、上傳與 Save As 有獨立 fixtures。Installed/live 驗收仍待完成，不包含直接未發布 Git pack relay。
- 完整dirty snapshot、任意目錄解壓、可執行附件／URL下載、非checkpoint的Task Service附件派工不在A。舊context_refs字串不提供readiness。
- Artifact deletion、retention期限／read model／server draft holds屬未來artifact cleanup。目前store只留內容／拒絕超額，cleanup對artifact保持RESOURCE_KIND_UNSUPPORTED。
- GitHub Actions build artifacts／deploy promotion與本store分開；若日後橋接需兩邊ID／digest證據，不能互換。
- Host Python3.9／no-follow／link／fsync readiness與容量預設須用fixture測試；本輪不安裝host環境或送實機write channel。
