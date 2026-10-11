export const specSourceStrings = {
  en: {
    spec_title: 'Specification sources', spec_intro: 'Read OpenSpec documents from the existing PM repository’s original fixed commit. Working copies remain untouched.',
    spec_limit: 'This reader supports the original pinned source only. Other commits, custom schemas and OpenSpec CLI commands are not available here.',
    spec_repo: 'Repository', spec_commit: 'Fixed source commit', spec_paths: 'Exact artifact paths (optional)',
    spec_paths_help: 'One path per line, up to 100. Leave blank to list supported standard artifacts.',
    spec_read: 'Read fixed sources', spec_reading: 'Reading fixed sources…', spec_refresh: 'Check availability', spec_checking: 'Checking source availability…',
    spec_ready: 'Source binding is configured. Git availability is verified when you read.',
    spec_unavailable: 'This source is unavailable.', spec_no_binding: 'No prepared PM source repository is bound to this workspace.',
    spec_invalid: 'The response does not match this fixed specification source.', spec_bad_paths: 'Use at most 100 distinct, nonempty paths of up to 800 characters.',
    spec_long_selection: 'Select fewer or shorter paths so this request fits the shared desktop read limit.',
    spec_empty: 'No supported OpenSpec documents exist at this commit.', spec_partial: 'Some documents could not be read. Their source identities and reasons remain visible.',
    spec_ignored: 'Unsupported paths omitted from this standard catalog: {count}', spec_scope: 'Scope adoption, implementation verification, merge and deployment remain unknown.',
    spec_schema: 'Schema validation has not run. The installed CLI version and effective schema are unknown.', spec_digest: 'Document set digest', spec_tree: 'Git tree',
    spec_file_id: 'Logical file ID', spec_source_id: 'Fixed source ID', spec_blob: 'Git blob', spec_hash: 'Content SHA-256',
    spec_source_details: 'Source identity and checks', spec_original: 'Original document text', spec_tasks: 'Task claims in this document',
    spec_task_note: 'A checked box is an author’s report. It is not verification, merge, deployment or a completed WorkItem.',
    spec_checked: 'Reported checked', spec_unchecked: 'Not reported checked', spec_example: 'Inside a fenced example', spec_line: 'Line {line}',
    spec_archive: 'Archived source; archive does not establish acceptance or delivery.', spec_reason: 'Unavailable reason', spec_unknown: 'Unknown',
    spec_reason_missing_at_commit: 'Not present at this commit.', spec_reason_symlink_or_nonregular_blob: 'The source is a link or unsupported Git object.',
    spec_reason_document_too_large: 'This document exceeds the read size limit.', spec_reason_selection_byte_budget: 'Read this document in a smaller selection.',
    spec_reason_non_utf8_document: 'This document is not UTF-8 text.', spec_reason_binary_document: 'This document contains binary data.',
    spec_schema_name: 'Declared schema name', spec_observation_limit: 'Structural observations exceed the display limit; original text remains available.',
    spec_role_main_spec: 'Main specification', spec_role_delta_spec: 'Change delta', spec_role_proposal: 'Proposal', spec_role_design: 'Design', spec_role_tasks: 'Task document',
    spec_role_project_metadata: 'Project metadata', spec_role_change_metadata: 'Change metadata',
  },
  'zh-TW': {
    spec_title: '規格來源', spec_intro: '從既有 PM 儲存庫的原始固定 commit 讀取 OpenSpec 文件。工作目錄保持原樣。',
    spec_limit: '目前只支援原始固定來源；此處尚未支援其他 commit、自訂 schema 或 OpenSpec CLI 指令。',
    spec_repo: '儲存庫', spec_commit: '固定來源 commit', spec_paths: '精確文件路徑（選填）',
    spec_paths_help: '每行一個路徑，最多 100 個。留白會列出支援的標準文件。',
    spec_read: '讀取固定來源', spec_reading: '正在讀取固定來源…', spec_refresh: '檢查可用性', spec_checking: '正在檢查來源可用性…',
    spec_ready: '已有來源綁定；實際讀取時才會核對 Git 可用性。',
    spec_unavailable: '目前無法讀取這個來源。', spec_no_binding: '此工作區尚未綁定已準備好的 PM 來源儲存庫。',
    spec_invalid: '回應與選定的固定規格來源不符。', spec_bad_paths: '請填入最多 100 個不重複的路徑，每個不超過 800 字元。',
    spec_long_selection: '請減少路徑數量或縮小選取範圍，讓請求符合共用桌面讀取上限。',
    spec_empty: '這個 commit 沒有支援的 OpenSpec 文件。', spec_partial: '部分文件無法讀取；仍保留其來源識別與原因。',
    spec_ignored: '標準目錄未納入的不支援路徑：{count}', spec_scope: '是否採用範圍、實作驗證、合併與部署仍未知。',
    spec_schema: '尚未執行 schema 驗證；已安裝 CLI 版本與有效 schema 仍未知。', spec_digest: '文件集合摘要', spec_tree: 'Git tree',
    spec_file_id: '邏輯文件 ID', spec_source_id: '固定來源 ID', spec_blob: 'Git blob', spec_hash: '內容 SHA-256',
    spec_source_details: '來源識別與核對', spec_original: '原始文件文字', spec_tasks: '文件中的任務回報',
    spec_task_note: '勾選只代表作者回報，不代表通過驗證、已合併、已部署或工作項目完成。',
    spec_checked: '已回報勾選', spec_unchecked: '未回報勾選', spec_example: '位於程式碼範例區塊', spec_line: '第 {line} 行',
    spec_archive: '封存來源；封存不代表已驗收或已交付。', spec_reason: '無法讀取原因', spec_unknown: '未知',
    spec_reason_missing_at_commit: '這個 commit 沒有此文件。', spec_reason_symlink_or_nonregular_blob: '來源是連結或不支援的 Git 物件。',
    spec_reason_document_too_large: '這份文件超過讀取大小上限。', spec_reason_selection_byte_budget: '請縮小選取範圍後讀取這份文件。',
    spec_reason_non_utf8_document: '這份文件不是 UTF-8 文字。', spec_reason_binary_document: '這份文件含有二進位資料。',
    spec_schema_name: '宣告的 schema 名稱', spec_observation_limit: '結構觀察超過顯示上限；仍可查看原始文字。',
    spec_role_main_spec: '主規格', spec_role_delta_spec: '變更差異規格', spec_role_proposal: '提案', spec_role_design: '設計', spec_role_tasks: '任務文件',
    spec_role_project_metadata: '專案中繼資料', spec_role_change_metadata: '變更中繼資料',
  },
};

export function specSourceText(t, key, values = {}) {
  const supplied = t(key, values);
  if (supplied !== key) return supplied;
  const lang = (navigator.language || 'zh-TW').toLowerCase().startsWith('zh') ? 'zh-TW' : 'en';
  return (specSourceStrings[lang][key] || key).replace(/\{(\w+)\}/g, (_, name) => String(values[name] ?? ''));
}
