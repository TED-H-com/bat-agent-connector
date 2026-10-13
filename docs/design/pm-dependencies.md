# 固定版本技術相依清冊（中央 API v1）

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
2,000 captured edges；略過 `node_modules`、`.venv`、`venv`、`target`、`vendor`、`vendored`、
`.git`、`dist`、`build`。超限 catalog 帶原因；不宣稱完整。無法安全讀取的 carrier 拒絕整次擷取。

每檔保留 `path/blob_sha/size/sha256/status/reason`。`status` 為 `read`、`missing`、
`too_large`、`unparsable`；不存在的值為 null。對已發現 manifest 補列缺少的慣用 lockfile；
同層沒有 lockfile 時可沿用上層；同層檔案存在但無法讀取或解析時，不跳過它借用上層版本。
超量、非 UTF-8、symlink、格式／結構不支援都不能當成空檔。
解析使用 Python stdlib，Python 3.10 沿用既有 tomli；不新增相依套件。

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
沒有 name 的 npm app 以 manifest path 顯示，published_name=null，name_reason=package_name_not_declared；檔案仍為 read。
Dependency key 為 ecosystem + package name（Python 依 PEP 503 將大小寫與 `-_.` 統一；顯示保留原拼法） 的穩定 `pmdepkey_…`；不從相似名稱或共同語言推論。

| relation | 來源 |
| --- | --- |
| runtime | Python project dependencies、npm dependencies、Cargo dependencies（含 target）、Go require |
| build | build-system requires、Cargo build-dependencies、runtime/toolchain 版本及具名建置 script／CI |
| test | Cargo dev-dependencies（tests/examples/benchmarks 語意）、有可辨識測試 binary／script／CI 證據的 Python groups/extras、npm devDependencies |
| contract/reference | owner 有來源宣告；不由掃描猜測 |

Python extras 與 dependency groups 使用獨立 declaration_group（extra:name／group:name），同名不覆寫。
Dynamic dependencies 一律帶 python_dynamic_dependencies，capture 為 partial；僅明確的 setuptools dynamic file 引用可建立 runtime edges。
未指定來源時 runtime_dependencies_status=unknown；未讀到指定檔案時為 partial；不把同層 requirements 檔推定為 runtime 或 dev。
npm peerDependencies／optionalDependencies 尚未投影為五種 relation；有內容時明列對應 parser reason，capture 為 partial。
Cargo workspace version、rust-version 與 dependencies 僅從明確會員的 workspace root 繼承；來源缺少時保留 component／edge、null 與 workspace_inheritance_unavailable。

未分類 dev 依賴保留 `relation=build,basis=dev_unclassified`。Npm binaries 來自 lockfile bin
宣告或固定已知工具對照；只辨識實際 command 起始詞，不把 echo／註解中的工具名稱當呼叫。
Workflow 為保守的逐行 reader，只處理 jobs、steps、literal run/uses、working-directory 及 defaults；
不解析 YAML anchors、任意 expression／matrix 或執行 shell。無法確認 component 工作目錄時不建立關聯。
Workflow/job defaults 僅接受 defaults.run.working-directory；step 的目錄只影響該 step。
同一 command 的 build 與 test 都保留；辨識 npx、pnpm exec、uv run、poetry run 包裝。
不因共用 CI job 就將 uses action 掛為 component dependency。
CI hints 帶 workflow path、job id、原始 1-based line、command、commit 和 blob；不代表 CI 已成功。

`version` 保存 manifest requirement；未宣告的 workflow tool 版本為 null，搭配 `version_reason`。
`resolved` 未提供時附 `resolution_reason`，維護負責人未提供時附 `maintenance_owner_reason`。`resolved` 只在對應 lockfile 有不歧義的版本時填值，
`evidence_level=verified` 表示該 commit 的 lockfile 解出版本，不表示相容性、測試或安全驗證。
上層 lockfile 必須有該 component 的涵蓋證據：uv workspace members／editable 或 virtual source／manifest.members；
Cargo workspace members 或 lock 中同名同版本的本機 package；npm lock packages[relative_directory]。
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
Read 不連 host、不掃檔、不改 journal；response 上限沿用 PM 2 MB。
`after` 是 signed-64-bit 非負 sequence，limit 1–100；最後一頁 `next_cursor=null`。
Revisions 回傳摘要，沒有把歷史頁筆數當總數。

Latest 包含 `version/workspace_version/source/capture/stale/partial/reason`、`components`、
依 kind 分組的 `dependencies`、`edges`、獨立 `declared`、`files`、`verification_hints`、
`maintenance_owner`、`counts`。`capture={revision_id,commit_sha,captured_at,status}`；
時間使用鄰近 PM endpoint 的 UTC epoch seconds。沒有 carrier 或未擷取時 status=unknown、counts=null；
已完整掃描的空 catalog 才可報 0。Captured status 為 complete／partial；counts 明示只涵蓋該 revision。
原 bound source commit 移動時 `stale=true`，不替換 captured commit。

Capture 同時固定最多 32 個、合計最多 1 MB 的可見其他 carrier revisions 作為跨 repo 比較集合。
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
