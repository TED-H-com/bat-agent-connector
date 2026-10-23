# 固定版本技術相依清冊（中央 API v1）

共用前端的關係圖保留捲動、拖曳與原縮放控制，並明示捲動／方向鍵操作。
節點顯示完整換行名稱；同名節點另加可見編號，避免不同節點只有相同的截斷前綴。
編號只依已載入清冊中同名群組的 ID 排序為 `[1]` 至 `[k]`，其他名稱的節點不影響編號；
圖中文字、提示、無障礙名稱、聚焦名稱列、矩陣與手機清單共用同一命名函式。
英文優先在空白、`-`、`_`、`/`、`.` 換行，單一片段超過欄寬才逐字拆開；中日韓文字維持逐字換行。
鍵盤聚焦會將節點捲至可見位置，並在圖旁顯示完整名稱（含其他儲存庫名稱）；
無障礙名稱與 SVG 原文提示保留同樣資訊。這些呈現不改變相依或節點識別。

此後端支援[技術相依視圖](pm-project-control-center.md#5-技術相依與維護使用相依詞彙不套人的-raci-字母)。
矩陣與圖形由另一個前端工作提供。清冊不代表已建置、測試、發布或取得維護授權。

## 來源與擷取

`features.pm_dependencies.version = 1`。沿用 `pm_spec_sources` 的 PM 自有 carrier、
原 Git commit、repository/config digest、讀取前後的 BAT workspace guard。
僅讀 Git tree/blob；不 checkout、fetch、執行 manifest／workflow、呼叫套件管理器，亦不讀人工草稿。
Git blob SHA-1、大小及 SHA-256 在中央重驗。最後一次 guard 到 journal CAS 之間不再 await。

固定 allowlist 支援 `pyproject.toml`、`uv.lock`、`requirements*.txt`、`package.json`、
`package-lock.json`、`Cargo.toml`、`Cargo.lock`、`rust-toolchain`／`.toml`、`go.mod`、
`go.sum`、`.nvmrc`、`.python-version`、`.tool-versions` 和根目錄 `.github/workflows/*.yml`／`.yaml`。
為配合既有 runner 的 1 MiB 輸出界線，wire envelope 上限 1,000,000 bytes（包含 JSON escaping）；超量檔案保留 too_large／selection_wire_budget。
上限為深度 8、200 trees、10,000 entries、128 files、每檔 512,000 bytes、合計 640,000 bytes、
最多 2,000 captured edge observations（仍受下述 bytes 預算限制）；略過 `node_modules`、`.venv`、`venv`、`target`、`vendor`、`vendored`、
`.git`、`dist`、`build`。超限 catalog 帶原因；不宣稱完整。無法安全讀取的 carrier 拒絕整次擷取。

每檔保留 `path/blob_sha/size/sha256/status/reason`。`status` 為 `read`、`missing`、
`too_large`、`unparsable`；不存在的值為 null。對已發現 manifest 補列缺少的慣用 lockfile；
同層沒有 lockfile 時可沿用上層；同層檔案存在但無法讀取或解析時，不跳過它借用上層版本。
超量、非 UTF-8、symlink、格式／結構不支援都不能當成空檔。
單一無法讀取的 blob 保留 files.unparsable/blob_unavailable；超過 512 KB 或無法列舉的 tree 不阻擋其他 tree。
unreadable_paths（最多 128 筆）列出 tree path/object_sha/status/reason；無法表示的檔名使用 path=null、parent_path、name_bytes_sha256。
這些問題與超量都有 catalog reason，capture 必須為 partial。非 UTF-8 的一般檔名不可能符合 ASCII allowlist，直接略過，不造成 partial；非 UTF-8 目錄可能藏有 manifest，仍記錄缺口。Host 的 PM_SPEC_／REPOSITORY_ 安全代碼保留；未知 host 錯誤為 PM_DEPENDENCIES_SOURCE_UNAVAILABLE，與 identity invalid 分開。
解析使用 Python stdlib，Python 3.10 沿用既有 tomli；不新增相依套件。純 bytes 解析／裁切使用 asyncio.to_thread；guard 與 journal 仍在原 event loop。Workspace glob 先合併連續 **，每次 capture 只編譯一次並快取 path match／lock coverage，沒有遞迴回溯。

## 操作契約

全部經 `POST /api/v1/operations`／`operation_submit`、原 `Idempotency-Key` 和 OperationService。
三個 action 都要求 `manage` + `observe`。Capture 需要 workspace 寫入會員；宣告／撤回需要 owner。
Target 為 `{workspace_id}`。三者 preconditions 相同：

```json
{
  "expected_version": 0,
  "expected_workspace_version": 1,
  "expected_binding_digest": "<source.binding_digest>"
}
```

`expected_version` 是 Dependencies projection 的 `version`，非 workspace version。
每次成功的 capture／declare／retract 都加一；相同 key 重播原結果，不再讀 Git。
版本或綁定改變必須重新閱讀，不能換 key 重送未知操作。

- `pm.dependencies.capture` params：`{repository, commit_sha}`，必須等於 projection 的 `source`。
  原子記錄 `pmdep_…` 不可變 revision、來源及 receipt。失敗不留下成功清冊。
- `pm.dependencies.declare` params：`{component_id, dependency, relation, evidence_level, source_ref, note?}`。
  `dependency = {name, ecosystem, kind, version}`；version 未知可為 null。
  `source_ref` 必須是固定 bound commit 的 `{path, commit_sha}` 或不含 credentials 的 HTTPS URL 字串。
  宣告只記錄來源引用，不抓外部 URL，也不宣稱文件內容已驗證。
  `evidence_level=declared` 只接受 `contract/reference`；`inferred` 接受五種 relation，保留 PM 待確認候選。
  `component_id` 必須存在於目前清冊；回傳 `pmdecl_…`。最多 256 筆有效宣告。
- `pm.dependencies.retract` params：`{declaration_id}`。追加撤回紀錄，保留原宣告；不修改 captured edges。

新增 tables/indexes/triggers 為 idempotent DDL，不使用 `user_version` 或新的 data step。

## 相依語意

Component 身分由 repository、manifest path、declared name 決定。`published_name` 表示 manifest
宣告可作為套件身分的名稱；private npm／禁止發布的 Cargo 為 null，**不代表已實際發布**。
沒有 name 的 npm app 以 manifest path 顯示，published_name=null，name_reason=package_name_not_declared；檔案仍為 read，不因此造成 partial。
Dependency key 為 ecosystem + package name（Python 依 PEP 503 將大小寫與 `-_.` 統一；顯示保留原拼法） 的穩定 `pmdepkey_…`；不從相似名稱或共同語言推論。

| relation | 來源 |
| --- | --- |
| runtime | Python project dependencies 與 optional-dependencies extras、npm dependencies、Cargo dependencies（含 target）、Go require |
| build | build-system requires、Cargo build-dependencies、runtime/toolchain 版本及具名建置 script／CI |
| test | Cargo dev-dependencies（tests/examples/benchmarks 語意）、有可辨識測試 binary／script／CI 證據的 Python dev groups、npm devDependencies |
| contract/reference | owner 有來源宣告；不由掃描猜測 |

Python extras 與 dependency groups 使用獨立 declaration_group（extra:name／group:name），同名不覆寫。
Extras 是 runtime_library／runtime，帶 optional=true 與 extras 名稱清單；同一版本也有必要 runtime 宣告時 optional=false，仍保留所有 extra refs。
Dev groups 支援 dependency-groups、tool.uv.dev-dependencies、tool.pdm.dev-dependencies、tool.poetry.group.<name>.dependencies 與舊 tool.poetry.dev-dependencies；皆按 script／CI 證據分類。
無法解析的工具欄位保留 parser_reasons=unsupported_field，unsupported_fields 以 path／field／commit 指出欄位，不靜默省略。
Dynamic dependencies 僅明確的 setuptools dynamic file 引用可建立 runtime edges。
未指定來源時 runtime_dependencies_status=unknown；未讀到指定檔案時為 partial，兩者皆帶 python_dynamic_dependencies parser reason。
全部指定檔案可讀時為 declared_dynamic_files，python_dynamic_dependencies 移至 parser_notes，不因此造成 partial。
已引用的 requirements 檔不重複算作 dev；其他同層 requirements 檔保留 dev_unclassified。
若 dynamic source 完全不明，不推定同層 requirements 的角色，parser_notes= requirements_excluded_dynamic_source_unknown 明示排除原因。
npm peerDependencies／optionalDependencies 尚未投影為五種 relation；有內容時明列對應 parser reason，capture 為 partial。
Cargo workspace version、rust-version、publish 與 dependencies 僅從明確會員的 workspace root 繼承；來源缺少時保留 component／edge、null 與 workspace_inheritance_unavailable。繼承 publish=false 或未知 publish 時 published_name=null；未知者另附 published_name_reason。虛擬 Cargo workspace root 只記 parser_notes=manifest_without_package，不造成 partial。

未分類 dev 依賴保留 `relation=build,basis=dev_unclassified`。Npm binaries 來自 lockfile bin
宣告或固定已知工具對照；只辨識實際 command 起始詞，不把 echo／註解中的工具名稱當呼叫。
Workflow 為保守的逐行 reader，只處理 jobs、steps、literal run/uses、working-directory 及 defaults；
不解析 YAML anchors、任意 expression／matrix 或執行 shell。無法確認 component 工作目錄時不建立關聯。
Workflow/job defaults 僅接受 defaults.run.working-directory，讀完全文後解析繼承，不受 jobs／steps 前後順序影響；支援尾端註解與 run: {working-directory: web}。
Step 的目錄只影響該 step；同一步內依序追蹤 literal cd，後續 command 使用更新目錄；&&／; 不要求兩旁空白，引號或跳脫內的 separator 不建立額外 command。未知 cd（變數、-、subshell）或不支援的 defaults／目錄形式不當成 repo root，以 workflow_directory_unknown 明示缺口且不建立該 command 的 component 關聯。
同一 command 的 build 與 test 都保留；辨識 npx、pnpm exec、uv run、poetry run 包裝。
不因共用 CI job 就將 uses action 掛為 component dependency。
每筆 evidence command、script、job_id、declaration_group 最多 512 UTF-8 bytes；分類仍使用原始內容，顯示截斷時附 <field>_truncated=true 與完整 <field>_sha256，parser reason=evidence_<field>_truncated。群組名稱不再無界複製至 basis；Python dependency-groups 的 basis 為 python_group，身分在 declaration_group。
Lone surrogate 名稱／版本／script 視為無法解析的 manifest，不能造成 INTERNAL 或輸出無效 JSON。parser_notes 只提供說明，與代表實際缺口的 parser_reasons 分開。
CI hints 帶 workflow path、job id、原始 1-based line、command、commit 和 blob；不代表 CI 已成功。

`version` 保存 manifest requirement；未宣告的 workflow tool 版本為 null，搭配 `version_reason`。
`resolved` 未提供時附 `resolution_reason`，維護負責人未提供時附 `maintenance_owner_reason`。`resolved` 只在對應 lockfile 有不歧義的版本時填值，
`evidence_level=verified` 表示該 commit 的 lockfile 解出版本，不表示相容性、測試或安全驗證。
上層 lockfile 必須有該 component 的涵蓋證據：uv workspace path members／editable 或 virtual source path（manifest.members 的名稱不是證據）；
Cargo workspace path members，已知 workspace 的非會員／exclude 不可借用其 lock；沒有 workspace root 時，lock 中本機 package 的 name+version 必須唯一，而且 captured Cargo manifests 中身分也必須唯一。npm 使用 lock packages[relative_directory]。
無法證明涵蓋時 resolved=null、resolution_reason=lockfile_does_not_cover_component；同層 lockfile 為該 component 的根。
多版本無法唯一對應時保留 null／declared。`go.sum` 是 checksum catalog，**不是 lockfile**，
因此 Go requirement 不因出現在 go.sum 就升為 verified。Requirement include/hash directives、
不支援的 manifest／toolchain 結構會明列 unparsable，不能執行補齊。

`kind` 為 `runtime_library/api_schema/build_test_toolchain/external_service`。
Owner 欄位獨立：僅引用可見、已採用 maintenance policy 明列的 maintenance_owner／policy ID，否則 null。
目前政策未提供具名 maintenance_owner 欄位，所以回傳 null；採用政策的 actor／controller 不自動成為維護負責人。

## Read projection

HTTP 三個路由都需要 observe + current workspace membership；RPC/MCP 同契約：

| HTTP，prefix `/api/v1/pm/workspaces/{id}` | RPC/MCP |
| --- | --- |
| `GET /dependencies` | `pm_dependencies_get(workspace_id)` |
| `GET /dependencies/revisions?after=0&limit=20` | `pm_dependencies_revisions_list(workspace_id, after?, limit?)` |
| `GET /dependencies/impact?dependency=pmdepkey_…` | `pm_dependencies_impact_get(workspace_id, dependency)` |

Native bridge 只放行上述 GET、精確 query 和既有 `/operations` 寫入入口。
Bridge 的 `limit` 僅接受 1–3 位數字並拒絕所有控制字元（含 C1），比 HTTP 更嚴格；schedule 的 `after` 與 HTTP 一樣最多 2,048 字元，dependency impact 必須提供 `dependency` key。
Read 不連 host、不掃檔、不改 journal；response 上限 2 MB。
新的 capture document 上限 750,000 bytes；parser 先保留 32,768 bytes 給來源 metadata，加入 files／peers 後再保留 8,192 bytes 給 capture envelope。有效 owner declarations 合計上限 250,000 bytes，保留 grouping、owner 和 peers 的顯示空間。
大型合法 manifest 不因 byte 預算拒絕整次 capture：依 stable ID 順序保留完整記錄的前綴，先裁切重複的 CI hints，再裁切 edges／未使用 dependencies，必要時裁切 components；持久化 partial revision，parser_reasons 包含 capture_budget。
omitted_edges／omitted_hints／omitted_components 是已觀察但省略的筆數（edge 上限在去重前計算），不當作完整 repo 總數；counts 只計回傳 capture 的列。最多 2,000 edge observations，實際保留數由 bytes 決定，沒有保證每筆都放得下。
Declaration 超限或無法在固定 metadata 預算內表示的資料仍使用 PM_DEPENDENCIES_READ_TOO_LARGE（EN／zh-TW 訊息），不產生成功寫入。
GET 先保留本 revision，再依剩餘 response bytes 選取固定 peer 集合；link 建立時也逐筆計入 byte budget，避免先膨脹整張圖。
放不下的 peer 或 link 使 partial=true、cross_repository.reason=projection_byte_budget。
舊 revision 若本身已超過投影預算，回傳 capture 摘要、counts=null、partial=true、reason=projection_byte_budget，並以 projection_omitted_collections 明列省略集合；不因 peer 膨脹而永久回 413。Impact 查到存在於舊 document、但被投影省略的 dependency 回 reason=dependency_omitted；新 capture 有 omitted_edges 且未找到 key 時回 capture_budget，不誤報 dependency_not_observed。
Impact 以完整 component rows 的有界前綴回傳，counts.basis=returned_components，partial=true，不能當全圖總數。
`after` 是 signed-64-bit 非負 sequence，limit 1–100；最後一頁 `next_cursor=null`。
Revisions 在 capture 同一 transaction 寫入獨立不可變 summary table，頁面只讀小型 summary、revision_id 與 sequence，不讀取／rehash inventory document，也不選取大型 row 尾端的 created_at。
沒有資料搬移或新 user_version；舊 revision 尚無 summary 時 captured_at／commit_sha=null、status=unknown、partial=true、reason=summary_not_recorded。
來源不可用時整頁 reason=source_unavailable。Revisions 沒有把歷史頁筆數當總數。
Impact 的 `dependency` 必須是 GET 回傳的 `pmdepkey_…`（`dependencies.<kind>[].key`、`edges[].dependency_key`
或 `declared[].dependency_key`）；套件名稱回 422 INVALID_PARAMS。尚無 capture 時，impact 與 GET 一樣回
`reason=source_unavailable`（來源撤回或無法存取）或 `not_captured`，並帶 `cross_repository`；counts=null。
沒有 observe scope 的讀取回 403 FORBIDDEN，訊息說明讀取需要 observe（不是變更所需的 manage）。

Latest 包含 `version/workspace_version/source/capture/stale/partial/reason`、`components`、
依 kind 分組的 `dependencies`、`edges`、獨立 `declared`、`files`、`verification_hints`、
`maintenance_owner`、`counts`。`capture={revision_id,commit_sha,captured_at,status}`；
時間使用鄰近 PM endpoint 的 UTC epoch seconds。沒有 carrier 或未擷取時 status=unknown、counts=null；
已完整掃描的空 catalog 才可報 0。Captured status 為 complete／partial；counts 明示只涵蓋該 revision。
PM carrier binding 固定且目前沒有 repository head 的觀察證據，因此 latest／history／impact／peer components 一律
`stale=null, stale_reason=repository_head_not_observed`；不把 pinned commit 相等說成對 repository head 而言「最新」。

Capture 同時固定最多 32 個、合計最多 1 MB 的可見其他 carrier revisions 作為跨 repo 比較集合。
候選依 `(workspace_id, revision_id)` 分頁掃描，不用 SQL `LIMIT 32`：先過可見性（目前 binding 與原 source
權限）、排除與本 repository 相同者、同一 repository 只取第一筆、再檢查 1 MB 預算，通過的才佔名額，
滿 32 個即停止。隱藏、同 repository 或重複的列不佔名額。每次 capture 最多檢查 128 個候選，限制 event loop 上的同步讀取；
掃描上限在仍有候選時停止，記 `peer_scan_limit`；可見 peer 因 1 MB 預算略過時記 `peer_byte_budget`。
這些原因隨 capture 固定，GET／impact 放在 `cross_repository.selection_reasons`，並使 `partial=true`
（`counts.partial` 仍只描述 captured catalog）。原因不含候選數，不洩漏隱藏來源的數量。
只有 ecosystem + published package name（Python 使用 PEP 503 正規化）對應才建立 `cross_repository_edges`，證據同時包含兩個
manifest、repository 和 commit；跨 component links 上限 2,000，始終標示有界；可見 peer components 放 `external_components`。
`cross_repository={status:bounded,complete:false}` 永遠不代表全站 repo 總圖。
後續 peer 擷取不會改寫既有 capture 的圖；需要新的 capture 才更新比較集合。
Impact 只沿此有界 captured graph 反向遍歷，處理循環與去重，保留 direct／distance、relation、version、
resolved、兩端 manifest 和 verification hints；owner 宣告另列，不冒充 captured 路徑。

來源 membership／repository binding／config 或原 operation source 權限撤回時，相關列、統計、
文件、版本、peer 圖及 impact 一起撤回。Peer 選取固定，read 過濾後不補入其他資料；
隱藏來源不能佔用新的頁面位置、改變可見圖的排序或洩漏清單長度。原始 evidence 保留在 journal，
不因失去讀取權限刪除。實作不新增前端，也不宣稱 installed/live 驗收。
