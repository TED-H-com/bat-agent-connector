// One vocabulary for evidence states; callers choose a key, never translate an enum ad hoc.
export const statusVocabulary = Object.freeze({
  not_started: {en: 'Not started', 'zh-TW': '未開始', tone: 'neutral'},
  in_progress: {en: 'In progress', 'zh-TW': '進行中', tone: 'info'},
  waiting: {en: 'Waiting', 'zh-TW': '等待中', tone: 'warn'},
  in_review: {en: 'In review', 'zh-TW': '審閱中', tone: 'review'},
  accepted: {en: 'Accepted', 'zh-TW': '已驗收', tone: 'ok'},
  blocked: {en: 'Blocked', 'zh-TW': '受阻', tone: 'bad'},
  done: {en: 'Done', 'zh-TW': '完成', tone: 'neutral'},
  merged: {en: 'Merged', 'zh-TW': '已合併', tone: 'ok'},
  deployed: {en: 'Deployed', 'zh-TW': '已部署', tone: 'ok'},
  rolled_back: {en: 'Rolled back', 'zh-TW': '已回復', tone: 'warn'},
  unconfirmed: {en: 'Unconfirmed', 'zh-TW': '待確認', tone: 'neutral'},
  unknown: {en: 'Unknown', 'zh-TW': '未知', tone: 'neutral'},
  not_assessed: {en: 'Not assessed', 'zh-TW': '未評估', tone: 'neutral'},
  partial: {en: 'Partial', 'zh-TW': '部分', tone: 'neutral'},
  stale: {en: 'Stale', 'zh-TW': '過期', tone: 'warn'},
  read_only: {en: 'Read-only', 'zh-TW': '唯讀', tone: 'review'},
  draft: {en: 'Draft', 'zh-TW': '草稿', tone: 'neutral'},
  adopted: {en: 'Adopted', 'zh-TW': '已採用', tone: 'ok'},
  historical: {en: 'Historical', 'zh-TW': '歷史', tone: 'neutral'},
});
export const uiStrings = {
  en: {ui_search_work: 'Search work', ui_report_versions: 'Versions', ui_save_snapshot: 'Save snapshot', ui_compare: 'Compare', ui_print: 'Print', ui_export_brief: 'Export brief', ui_close: 'Close', ui_back: 'Back', ui_retry: 'Retry', ui_remove: 'Remove {text}', ui_loading: 'Loading',
    ui_more: 'More actions', ui_expand: 'Expand {title}', ui_collapse: 'Collapse {title}',
    ui_overview: 'Overview', ui_discussions: 'Discussions', ui_requirements: 'Requirements', ui_schedule: 'Schedule',
    ui_dependencies: 'Dependencies', ui_issues: 'Issues & changes', ui_agents: 'Agents', ui_board: 'Board',
    ui_project: 'Project', sync_actions_paused: 'Source refresh failed; actions paused until fresh records arrive.', ui_work_item: 'Work item', ui_bound_partial: 'Some linked projects are unavailable', ui_work: 'Work', ui_all_projects: 'All projects', ui_settings: 'Settings', ui_pending: 'Pending',
    ui_connected: 'Connected', ui_offline: 'Offline', ui_reconnecting: 'Reconnecting', ui_workspace: 'Workspace',
    ui_switch_workspace: 'Switch workspace', ui_new_workspace: 'New workspace', ui_report: 'Report',
    ui_change_phase: 'Change phase', ui_workspace_settings: 'Workspace settings', ui_threads_loaded: '{n} loaded{more}',
    ui_phase_idea: 'Idea', ui_phase_planning: 'Planning', ui_phase_development: 'Delivery', ui_phase_maintain: 'Maintain',
    ui_phase_paused: 'Paused', ui_phase_closed: 'Closed', ui_tools: 'Tools', ui_menu: 'Open navigation',
    ui_pending_loaded: '{n} pending loaded{more}', ui_no_bound_projects: 'No linked projects', ui_work_links_unavailable: 'Work links unavailable', ui_work_unavailable: 'Work unavailable',
    ui_bindings: 'Project bindings', ui_evidence: 'Evidence', ui_quality: 'Quality reviews', ui_packages: 'Work packages',
    ui_handoff: 'Package handoff', ui_specs: 'Spec sources', ui_materialization: 'Spec materialization', ui_publication: 'Issue publication',
    ui_meetings: 'Meetings', ui_sprints: 'Sprints', ui_follow_up: 'Follow-ups', ui_repairs: 'Maintenance repairs',
    ui_maintenance_review: 'Maintenance review', ui_policy: 'Maintenance policy', ui_baselines: 'Release baselines',
    ui_batches: 'Work batches', ui_observations: 'AHP observations'},
  'zh-TW': {ui_search_work: '搜尋工作', ui_report_versions: '版本', ui_save_snapshot: '儲存快照', ui_compare: '比較', ui_print: '列印', ui_export_brief: '匯出摘要', ui_close: '關閉', ui_back: '返回', ui_retry: '重試', ui_remove: '移除{text}', ui_loading: '載入中',
    ui_more: '更多操作', ui_expand: '展開{title}', ui_collapse: '收合{title}',
    ui_overview: '專案總覽', ui_discussions: '討論', ui_requirements: '需求', ui_schedule: '時程',
    ui_dependencies: '技術相依', ui_issues: '問題與變更', ui_agents: 'Agent 資源', ui_board: '工作看板',
    ui_project: '專案', sync_actions_paused: '資料更新失敗；取得最新紀錄前暫停操作。', ui_work_item: '工作項目', ui_bound_partial: '部分連結專案無法取得', ui_work: '工作', ui_all_projects: '所有專案', ui_settings: '設定', ui_pending: '待處理',
    ui_connected: '已連線', ui_offline: '離線', ui_reconnecting: '重新連線中', ui_workspace: '工作空間',
    ui_switch_workspace: '切換工作空間', ui_new_workspace: '新增工作空間', ui_report: '報告',
    ui_change_phase: '變更階段', ui_workspace_settings: '工作空間設定', ui_threads_loaded: '已載入 {n} 筆{more}',
    ui_phase_idea: '構想', ui_phase_planning: '規劃', ui_phase_development: '交付', ui_phase_maintain: '維護',
    ui_phase_paused: '暫停', ui_phase_closed: '結案', ui_tools: '工具', ui_menu: '開啟導覽',
    ui_pending_loaded: '已載入 {n} 筆待處理{more}', ui_no_bound_projects: '尚無連結專案', ui_work_links_unavailable: '無法取得工作連結', ui_work_unavailable: '無法取得工作資料',
    ui_bindings: '專案綁定', ui_evidence: '成果證據', ui_quality: '品質審閱', ui_packages: '工程工作包',
    ui_handoff: '工作包交接', ui_specs: '規格來源', ui_materialization: '規格產出', ui_publication: '議題發布',
    ui_meetings: '會議', ui_sprints: '衝刺', ui_follow_up: '後續追蹤', ui_repairs: '維護修復',
    ui_maintenance_review: '維護審閱', ui_policy: '維護政策', ui_baselines: '發布基準',
    ui_batches: '工作批次', ui_observations: 'AHP 觀察'},
};
for (const lang of ['en', 'zh-TW']) for (const [key, row] of Object.entries(statusVocabulary)) uiStrings[lang]['ui_status_' + key] = row[lang];

// Shared projection reasons. New server codes stay readable without leaking into body copy.
export const reasonLabels = Object.freeze({
  no_explicit_goal: ['Not set', '未設定'], goal_unconfirmed: ['Unconfirmed', '待確認'],
  no_adopted_plan: ['No adopted plan', '尚無已採用計畫'],
  source_limit: ['Partial', '部分'], partial_page: ['Partial', '部分'],
  source_incomplete: ['Sources incomplete', '來源不完整'], incomplete_sources: ['Sources incomplete', '來源不完整'],
  source_unavailable: ['Source unavailable', '無法取得來源'], unavailable: ['Not available', '無法取得'],
  not_configured: ['Not configured', '尚未設定'], not_applicable: ['Not applicable', '不適用'],
  not_recorded: ['Not recorded', '尚無紀錄'], no_roadmap_baseline: ['No milestone baseline', '尚無里程碑基準'],
  quality_revoked: ['Acceptance revoked', '驗收已撤回'], forecast_later_than_baseline: ['Later than baseline', '晚於基準日'], lifecycle_inflight: ['Pending operations', '待核對操作'],
  missing: ['Not recorded', '尚無紀錄'], context_changed: ['Context changed', '來源已變動'], stale: ['Stale', '過期'],
  stopped: ['Stopped', '已停止'], disabled: ['Disabled', '已停用'], incomplete: ['Sources incomplete', '來源不完整'],
  not_assessed: ['Not assessed', '未評估'], unknown: ['Unknown', '未知'],
});
for (const [reason, labels] of Object.entries(reasonLabels)) {
  uiStrings.en['ui_reason_' + reason] = labels[0];
  uiStrings['zh-TW']['ui_reason_' + reason] = labels[1];
}
export function reasonLabel(t, reason) {
  return t('ui_reason_' + (Object.hasOwn(reasonLabels, reason) ? reason : 'unavailable'));
}
