import {timeText} from './ui/stamps.js';
// Presentation maps for global screens. Raw ids and reason codes stay out of row text.
import {joinLabel, labelSegment} from './label-text.js';
import {t} from './i18n.js';
import {reasonLabel} from './ui/ui-strings.js';

const ACTIONS = {
  'pm.github.import': 'bgh_action_import',
  'project.github.import': 'bgh_action_import',
  'project.github.configure': 'bgh_action_configure',
  'project.github.sync': 'bgh_action_sync',
  'checkpoint.continue': 'action_checkpoint_continue',
  'checkpoint.continue.revalidate': 'action_checkpoint_revalidate',
  'artifact.upload': 'action_artifact_upload',
  'artifact.capture.managed': 'action_artifact_capture',
  'artifact.accept': 'action_artifact_accept',
  'session.start': 'action_session_start',
  'session.send': 'action_session_send',
  'session.answer': 'action_session_answer',
  'session.permissions': 'action_session_permissions',
  'session.approve_pending': 'action_session_approve',
  'repository.continue': 'action_repository_continue',
  'github.pr.merge': 'action_github_merge',
  'delivery.merge_and_deploy': 'action_delivery_merge',
  'deployment.start': 'action_deployment_start',
  'cleanup.apply': 'action_cleanup_apply',
  'task.pause': 'action_task_pause',
  'task.resume': 'action_task_resume',
  'task.verify': 'action_task_verify',
  'worktree.merge': 'action_worktree_merge',
  'integration.apply': 'action_integration_apply',
  'integration.handoff': 'action_integration_handoff',
  'project.create': 'action_project_create',
  'project.update': 'action_project_update',
  'project.order': 'action_project_order',
  'project.pin': 'action_project_pin',
  'work_item.create': 'action_work_item_create',
  'work_item.update': 'action_work_item_update',
  'work_item.link': 'action_work_item_link',
  'work_item.read': 'action_work_item_read',
  'setup.host': 'action_setup_host',
  'setup.connection.apply': 'action_setup_connection',
  'connection.reference.apply': 'action_connection_reference',
  'connection.reference.detach': 'action_connection_detach',
  'setup.repository': 'action_setup_repository',
  'setup.verification': 'action_setup_verification',
  'preferences.models.update': 'action_models_update',
  'artifact.capture': 'action_artifact_capture_item',
  'checkpoint.create': 'action_checkpoint_create',
  'deployment.rollback': 'action_deployment_rollback',
  'fanout.plan': 'action_fanout_plan',
  'fanout.start': 'action_fanout_start',
  'github.pr.publish': 'action_github_pr_publish',
  'github.pr.update': 'action_github_pr_update',
  'integration.preview': 'action_integration_preview',
  'pm.ahp.observe': 'action_pm_ahp_observe',
  'pm.backlog.revise': 'action_pm_backlog_revise',
  'pm.conversation.adopt': 'action_pm_conversation_adopt',
  'pm.overview.assess': 'action_pm_overview_assess',
  'pm.continuation.prepare': 'action_pm_continuation_prepare',
  'pm.work_input.defer': 'action_pm_work_input_defer',
  'pm.conversation.propose': 'action_pm_conversation_propose',
  'pm.dependencies.capture': 'action_pm_dependencies_capture',
  'pm.dependencies.declare': 'action_pm_dependencies_declare',
  'pm.dependencies.retract': 'action_pm_dependencies_retract',
  'pm.dod.revise': 'action_pm_dod_revise',
  'pm.evidence.capture': 'action_pm_evidence_capture',
  'pm.execution.runtime.observe': 'action_pm_execution_runtime_observe',
  'pm.session.quiescence.observe': 'action_pm_session_quiescence_observe',
  'pm.execution.verify': 'action_pm_execution_verify',
  'pm.follow_up.analyze': 'action_pm_follow_up_analyze',
  'pm.follow_up.prepare': 'action_pm_follow_up_prepare',
  'pm.github.observe': 'action_pm_github_observe',
  'pm.governance.adoption.link': 'action_pm_governance_adoption_link',
  'pm.governance.decide': 'action_pm_governance_decide',
  'pm.governance.record': 'action_pm_governance_record',
  'pm.governance.revise': 'action_pm_governance_revise',
  'pm.input.confirm': 'action_pm_input_confirm',
  'pm.input.record': 'action_pm_input_record',
  'pm.issue.cancel': 'action_pm_issue_cancel',
  'pm.issue.publish': 'action_pm_issue_publish',
  'pm.lifecycle.close': 'action_pm_lifecycle_close',
  'pm.lifecycle.pause': 'action_pm_lifecycle_pause',
  'pm.lifecycle.reopen': 'action_pm_lifecycle_reopen',
  'pm.lifecycle.retain_inflight': 'action_pm_lifecycle_retain_inflight',
  'pm.lifecycle.set_stage': 'action_pm_lifecycle_set_stage',
  'pm.maintenance.check': 'action_pm_maintenance_check',
  'pm.maintenance.repair': 'action_pm_maintenance_repair',
  'pm.maintenance.repair.evidence.record': 'action_pm_maintenance_repair_evidence_record',
  'pm.maintenance.repair.runtime.observe': 'action_pm_maintenance_repair_runtime_observe',
  'pm.maintenance_policy.adopt': 'action_pm_maintenance_policy_adopt',
  'pm.maintenance_review.candidate.record': 'action_pm_maintenance_review_candidate_record',
  'pm.maintenance_review.complete': 'action_pm_maintenance_review_complete',
  'pm.maintenance_review.follow_up.record': 'action_pm_maintenance_review_follow_up_record',
  'pm.maintenance_review.note': 'action_pm_maintenance_review_note',
  'pm.maintenance_review.prepare': 'action_pm_maintenance_review_prepare',
  'pm.meeting.complete': 'action_pm_meeting_complete',
  'pm.meeting.context.revise': 'action_pm_meeting_context_revise',
  'pm.meeting.create': 'action_pm_meeting_create',
  'pm.meeting.draft': 'action_pm_meeting_draft',
  'pm.meeting.follow_up.record': 'action_pm_meeting_follow_up_record',
  'pm.meeting.note': 'action_pm_meeting_note',
  'pm.message.append': 'action_pm_message_append',
  'pm.plan.adopt': 'action_pm_plan_adopt',
  'pm.plan.draft': 'action_pm_plan_draft',
  'pm.product_goal.revise': 'action_pm_product_goal_revise',
  'pm.project.bind': 'action_pm_project_bind',
  'pm.quality.review.create': 'action_pm_quality_review_create',
  'pm.quality.review.record': 'action_pm_quality_review_record',
  'pm.quality.review.revoke': 'action_pm_quality_review_revoke',
  'pm.release_baseline.register': 'action_pm_release_baseline_register',
  'pm.roadmap.baseline.set': 'action_pm_roadmap_baseline_set',
  'pm.roadmap.forecast.set': 'action_pm_roadmap_forecast_set',
  'pm.roadmap.observe': 'action_pm_roadmap_observe',
  'pm.run.analyze': 'action_pm_run_analyze',
  'pm.run.bat.analyze': 'action_pm_run_bat_analyze',
  'pm.run.prepare': 'action_pm_run_prepare',
  'pm.snapshot.create': 'action_pm_snapshot_create',
  'pm.spec.materialize': 'action_pm_spec_materialize',
  'pm.sprint.cancel': 'action_pm_sprint_cancel',
  'pm.sprint.close': 'action_pm_sprint_close',
  'pm.sprint.create': 'action_pm_sprint_create',
  'pm.sprint.goal.revise': 'action_pm_sprint_goal_revise',
  'pm.sprint.select': 'action_pm_sprint_select',
  'pm.sprint.start': 'action_pm_sprint_start',
  'pm.thread.create': 'action_pm_thread_create',
  'pm.thread.plan': 'action_pm_thread_plan',
  'pm.thread.plan.revise': 'action_pm_thread_plan_revise',
  'pm.thread.rename': 'action_pm_thread_rename',
  'pm.work.bind': 'action_pm_work_bind',
  'pm.work_batch.evidence.record': 'action_pm_work_batch_evidence_record',
  'pm.work_batch.start': 'action_pm_work_batch_start',
  'pm.work_package.create': 'action_pm_work_package_create',
  'pm.work_profile.save': 'action_pm_work_profile_save',
  'pm.workload.capture': 'action_pm_workload_capture',
  'pm.workspace.adapter.select': 'action_pm_workspace_adapter_select',
  'pm.workspace.create': 'action_pm_workspace_create',
  'pm.workspace.member.set': 'action_pm_workspace_member_set',
  'project.skills.update': 'action_project_skills_update',
  'repair.create': 'action_repair_create',
  'session.failover': 'action_session_failover',
  'session.interrupt': 'action_session_interrupt',
  'session.labels.set': 'action_session_labels_set',
  'session.position': 'action_session_position',
  'session.read': 'action_session_read',
  'session.record_verification': 'action_session_record_verification',
  'session.relay': 'action_session_relay',
  'task.command.reconcile': 'action_task_command_reconcile',
  'task.mark_stage': 'action_task_mark_stage',
  'task.request_ted': 'action_task_request_ted',
  'task.submit': 'action_task_submit',
  'work.start': 'action_work_start',
  'work.adopt_and_start': 'action_work_adopt_start',
  'work_item.approve': 'action_work_item_approve',
  'work_item.continue': 'action_work_item_continue',
  'work_item.order': 'action_work_item_order',
  'work_item.pin': 'action_work_item_pin',
};

const OPERATION_STATUS = {
  accepted: 'waiting',
  running: 'in_progress',
  waiting_checks: 'waiting',
  waiting_external: 'waiting',
  needs_attention: 'blocked',
  uncertain: 'unknown',
  succeeded: 'done',
  failed: 'blocked',
  cancelled: 'historical',
};

const WORK_STATUS = {
  github_closed: 'github_closed',
  github_completed: 'github_completed',
  github_done: 'github_done',
  todo: 'todo',
  doing: 'in_progress',
  waiting: 'waiting',
  done: 'done',
  awaiting_approval: 'in_review',
};

export function actionMessageKey(action) {
  return ACTIONS[action] || '';
}

export function knownActionIds() {
  return Object.keys(ACTIONS);
}

function humanizeAction(action) {
  const words = String(action || '').split(/[._]+/).filter(Boolean);
  if (!words.length) return '';
  return words.map((word, index) => index === 0 ? word.charAt(0).toUpperCase() + word.slice(1) : word).join(' ');
}

export function actionLabel(action) {
  const key = ACTIONS[action];
  if (key) {
    const label = t(key);
    if (label && label !== key) return label;
  }
  return humanizeAction(action) || t('action_unknown');
}

export function operationStatusLabel(status) {
  const label = t('op_' + status);
  return label === 'op_' + status ? t('ui_status_unknown') : label;
}
export function operationStatusKey(status) {
  return OPERATION_STATUS[status] || 'unknown';
}

export function workStatusKey(state) {
  return WORK_STATUS[state] || 'unknown';
}

function named(value, rawId) {
  const text = labelSegment(value);
  return text && text !== rawId ? text : '';
}

export function operationTargetText(op) {
  const target = op?.target;
  if (!target || typeof target !== 'object') return labelSegment(target);
  if (target.plan_id || target.revision_id || target.plan_version != null) {
    const version = [target.plan_version, target.version].map(Number).find(n => Number.isInteger(n) && n > 0 && n < 100);
    return version ? t('global_plan_version', {n: version}) : t('global_plan');
  }
  if (target.thread_id) {
    const title = named(target.thread_title || target.title, target.thread_id);
    return title ? joinLabel([t('global_discussion'), title]) : t('global_discussion');
  }
  if (target.workspace_id) {
    return named(target.workspace_name || target.name, target.workspace_id) || t('global_workspace');
  }
  return labelSegment(target.title || target.name || target.recipe || target.branch || target.repository
    || target.host || target.session_id || target.project_id || target.work_item_id || target.checkpoint_id);
}

export function operationMeta(op, whenText = '') {
  return joinLabel([operationTargetText(op), op?.actor, whenText]);
}

export function formatTime(value) { return value ? timeText(value) : ''; }

const TERMINAL_OPERATION = new Set(['succeeded', 'failed', 'cancelled', 'needs_attention', 'uncertain']);
const ACTIVE_OPERATION = new Set(['accepted', 'running', 'waiting_checks', 'waiting_external']);

// WebKitGTK has no Intl.DurationFormat. One formatter for the table and the inspector.
export function operationDuration(op, nowSeconds) {
  const start = Number(op?.created_at);
  if (!Number.isFinite(start) || start <= 0) return '';
  const end = TERMINAL_OPERATION.has(op?.status) ? Number(op?.updated_at)
    : ACTIVE_OPERATION.has(op?.status) ? Number(nowSeconds) : Number.NaN;
  if (!Number.isFinite(end) || end < start) return '';
  const seconds = Math.round(end - start);
  if (seconds < 60) return t('global_duration_s', {n: seconds});
  if (seconds < 3600) return t('global_duration_m', {n: Math.floor(seconds / 60)});
  if (seconds < 86400) {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    return minutes ? t('global_duration_hm', {n: hours, m: minutes}) : t('global_duration_h', {n: hours});
  }
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  return hours ? t('global_duration_dh', {n: days, h: hours}) : t('global_duration_d', {n: days});
}

// Loud labels are only the states that need the user. The words stay in the tooltip
// and accessible name. The rail paints an icon for those, a dot for Running, and
// no mark for idle, not started, done, and other neutral states.
export function railPresentation(state) {
  if (!state?.key) return null;
  if (state.key === 'pending_ask_user') return {labelKey: 'rail_asking', loud: true, tone: state.tone || 'stale'};
  if (state.key === 'workspace_needs_you' || state.key === 'pending_permission' || state.key === 'wi_state_awaiting_approval')
    return {labelKey: 'rail_needs_you', loud: true, tone: state.tone || 'stale'};
  if (state.key === 'sessions_stale' || state.key === 'sessions_not_seen')
    return {labelKey: 'sessions_pill_stale', loud: true, tone: state.tone || 'stale'};
  if (state.key === 'op_failed' || state.key === 'task_state_failed' || state.key === 'wi_state_failed')
    return {labelKey: 'rail_failed', loud: true, tone: 'bad'};
  if (state.key === 'obs_value_streaming') return {labelKey: 'rail_running', loud: false, tone: state.tone || 'info'};
  return null;
}

export function reasonText(code) {
  const token = labelSegment(code);
  if (!token) return '';
  return reasonLabel(t, token);
}

export function shownLabel(count) {
  return t('global_shown', {count});
}
