# Native reference review boundary / 本機關聯檢閱邊界

[`connection_reference`](../../desktop/fleet-core/src/connection_reference.rs) provides
a pure, blocked diagnostic review over captured catalog, Configuration, selection
Observation/Snapshot and original-association coverage. It calls the real catalog
`transition(Detach)` and `launch_plan`; it never reads files, probes, stops, ensures,
launches, revokes or writes. The caller supplies the original verified server binding.

The resolver interface currently represents only unproven coverage. Consistent Fleet
frozen records are committed as checked evidence, but they do not bind a server
connection to a catalog entry or prove a route/profile association. No name match or
empty result means "no Fleet". Missing evidence still produces a review, with
`LOCAL_SOURCE_ASSOCIATION_UNPROVEN`; both slot authorities remain
`LOCAL_OWNERSHIP_UNPROVEN` and runtime coverage remains `LOCAL_GATE_UNPROVEN`.

The catalog candidate retains every entry and immutable configuration, clears only
the detached selected entry, and preserves exact original bytes for an unchanged
transition. Selection after-state is explicitly **unproven**, never an empty or
unchanged selection. This result has no v2 manifest or submit conversion. Its digest
commits the full captured catalog/selection, configuration, binding, checked frozen
references and optional slot identities; these are not ownership grants.

本機檢閱使用既有目錄 transition、selection parser 與 launch_plan。輸入為已擷取資料，
不讀取或修改檔案，也不啟動、停止、探測或撤銷任何資源。原始 association 尚無正式
記錄產生器；有效的 Fleet frozen record 也不能代替 server connection 的原始綁定。
缺證時仍可檢閱，但回傳明確執行限制；名稱相符或查無資料都不能證明「沒有 Fleet」。

目錄候選保留所有歷史 ID 與設定；未變更時保留原 bytes。selection after 明確為未證實，
不是空選擇或維持不變。本介面不產生可提交的 v2 計畫，也不提供 UI 或 Tauri command。
完整選擇移除仍等待原始 association producer／resolver 與無損 selection 檢閱診斷；
執行器另需 protected slot authority、runtime gates、原始 frozen review 與 participant 整合。
共享的 [wire contract](fleet-configuration.md#connection-reference-detach) 不因本介面而改變。
