export const ahpObserverStrings={
  en:{
    ahp_decoder_profile:'Recorded decoder profile',ahp_unknown_source_kind:'This original snapshot has no supported source kind. Read its preserved metadata in the original document; the URI does not establish its type.',
    ahp_snapshot_details:'Snapshot details',ahp_from_sequence:'Snapshot sequence',ahp_later_events:'Later events recorded',ahp_yes:'Yes',ahp_no:'No',ahp_provider:'Provider',ahp_native_lifecycle:'Reported lifecycle (not a completion proof)',ahp_file_count:'File metadata records',
    ahp_title:'AHP observations',ahp_intro:'Read a configured host catalog and selected snapshots. Manual and foreign sessions stay read-only; these observations do not establish completion, cost or management authority.',
    ahp_adapter:'Configured adapter',ahp_refresh:'Reload saved observations',ahp_history:'Saved observation history',ahp_more:'Next saved observations',ahp_invalid:'The observation does not match its original workspace or source.',
    ahp_saved_detail:'Saved observation',ahp_captured:'Captured at',ahp_coverage:'Capture coverage',ahp_observed:'Bounded observation saved',ahp_incomplete:'Incomplete observation',
    ahp_catalog_coverage:'Catalog coverage',ahp_catalog_complete:'Advertised catalog pagination complete',ahp_catalog_partial:'Advertised catalog incomplete',ahp_reason:'Recorded reason',ahp_snapshots:'Selected snapshots (including root)',ahp_events:'Event metadata records',ahp_gaps:'Recorded replay gaps',
    ahp_evidence_limits:'This is dated, bounded evidence. A snapshot may have later events; gaps and missing sources remain in the original document. No transcript, file contents or completion proof is collected.',
    ahp_original:'Original source document and receipt',ahp_hidden:'Some original observations are unavailable with current source access.',ahp_empty:'No saved observations.',
    ahp_need_catalog:'Capture a catalog before selecting sessions.',ahp_selection_help:'Select up to 8 sessions from this saved catalog. The host catalog is checked again during capture.',
    ahp_selection_unavailable:'The original catalog selection is unavailable. Recover any pending request first, or capture a new catalog for a new selection.',ahp_select:'Select',ahp_no_sessions:'No sessions were listed in this catalog.',
    ahp_changesets:'Snapshot contents',ahp_sessions_only:'Session metadata only',ahp_include_changesets:'Include advertised static changesets',ahp_changesets_help:'Optional static changeset snapshots include file paths and diff counts, up to 16 changesets. Dynamic templates and file contents are excluded.',
    ahp_capture_selected:'Capture selected snapshots',ahp_capture_catalog:'Capture host catalog',ahp_catalog_help:'Explicitly reads the configured host. A pending selection keeps its original request and key.',
    ahp_capabilities:'Adapter capabilities and limits',ahp_protocol:'Protocol',ahp_capability_limits:'AHP read subset: catalog and selected snapshots only. Configured protocol support is separate from live host validation; check the original capability document. No session creation, messaging, stopping, writer control or adapter migration.',
    ahp_no_adapters:'No AHP adapters are configured for this workspace. An operator must configure the central service before observations are available.',ahp_unavailable:'Observation sources could not be reread. Previous source content has been hidden; original request keys are preserved.'
  },
  'zh-TW':{
    ahp_decoder_profile:'原紀錄 decoder profile',ahp_unknown_source_kind:'這份原快照未記錄可支援的來源種類。請從原文件查看保存的 metadata；不以 URI 推定種類。',
    ahp_snapshot_details:'快照詳情',ahp_from_sequence:'快照序號',ahp_later_events:'已記錄後續事件',ahp_yes:'是',ahp_no:'否',ahp_provider:'Provider',ahp_native_lifecycle:'原生回報 lifecycle（非完成證明）',ahp_file_count:'檔案 metadata 紀錄',
    ahp_title:'AHP 唯讀觀察',ahp_intro:'讀取已設定主機的 catalog 與選定快照。人工及外部 session 維持唯讀；這些觀察不能證明完成、成本或管理權。',
    ahp_adapter:'已設定的 adapter',ahp_refresh:'重讀已保存觀察',ahp_history:'已保存觀察歷史',ahp_more:'下一頁已保存觀察',ahp_invalid:'觀察與原 workspace 或來源不一致。',
    ahp_saved_detail:'已保存的觀察',ahp_captured:'擷取時間',ahp_coverage:'擷取涵蓋範圍',ahp_observed:'已保存有界觀察',ahp_incomplete:'觀察不完整',
    ahp_catalog_coverage:'Catalog 涵蓋範圍',ahp_catalog_complete:'已列出 catalog 的分頁讀取完整',ahp_catalog_partial:'已列出 catalog 不完整',ahp_reason:'原紀錄原因',ahp_snapshots:'選定快照（含 root）',ahp_events:'事件 metadata 紀錄',ahp_gaps:'已記錄的重播缺口',
    ahp_evidence_limits:'這是有時間與範圍限制的證據。快照之後可能已有事件；缺口及缺失來源保留在原文件中。不擷取逐字稿、檔案內容或完成證明。',
    ahp_original:'原始來源文件與收據',ahp_hidden:'目前來源權限無法讀取部分原觀察。',ahp_empty:'尚無已保存觀察。',
    ahp_need_catalog:'先擷取 catalog，再選取 session。',ahp_selection_help:'從這份已保存 catalog 選取最多 8 個 session。擷取時會再次查核主機 catalog。',
    ahp_selection_unavailable:'原 catalog 選取目前無法讀取。如有待查回請求，先恢復原請求；否則可擷取新 catalog 重新選取。',ahp_select:'選取',ahp_no_sessions:'這份 catalog 未列出 session。',
    ahp_changesets:'快照內容',ahp_sessions_only:'僅 session metadata',ahp_include_changesets:'包含已列出的靜態 changeset',ahp_changesets_help:'可選的靜態 changeset 快照包含檔案路徑與 diff 數量，最多 16 份。不讀取動態範本或檔案內容。',
    ahp_capture_selected:'擷取選定快照',ahp_capture_catalog:'擷取主機 catalog',ahp_catalog_help:'明確讀取已設定主機。待查回的選取請求保留原內容與 key。',
    ahp_capabilities:'Adapter 能力與限制',ahp_protocol:'協定',ahp_capability_limits:'AHP 唯讀子集：僅 catalog 與選定快照。設定支援的協定與真實主機驗證不同；請查看原能力文件。不提供建立 session、傳訊、停止、writer 控制或 adapter 遷移。',
    ahp_no_adapters:'此 workspace 尚未設定 AHP adapter。需先由操作人員設定中央服務，才能擷取觀察。',ahp_unavailable:'無法重新讀取觀察來源。已隱藏先前來源內容；原請求 key 仍保留。'
  }
};
