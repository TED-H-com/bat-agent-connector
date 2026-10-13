// Presentation maps for global screens. Raw ids and reason codes stay out of row text.
import {joinLabel, labelSegment} from './label-text.js';
import {t} from './i18n.js';

const ACTIONS = {
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
  'setup.repository': 'action_setup_repository',
  'setup.verification': 'action_setup_verification',
  'preferences.models.update': 'action_models_update',
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
  todo: 'not_started',
  doing: 'in_progress',
  waiting: 'waiting',
  done: 'done',
  awaiting_approval: 'in_review',
};

export function actionLabel(action) {
  const key = ACTIONS[action];
  if (!key) return t('action_unknown');
  const label = t(key);
  return label === key ? t('action_unknown') : label;
}

export function operationStatusKey(status) {
  return OPERATION_STATUS[status] || 'unknown';
}

export function workStatusKey(state) {
  return WORK_STATUS[state] || 'unknown';
}

export function operationTargetText(op) {
  const target = op?.target;
  if (!target || typeof target !== 'object') return labelSegment(target);
  return labelSegment(target.title || target.name || target.recipe || target.branch || target.repository
    || target.host || target.session_id || target.project_id || target.work_item_id || target.checkpoint_id);
}

export function operationMeta(op, whenText = '') {
  return joinLabel([operationTargetText(op), op?.actor, whenText]);
}

export function formatTime(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value <= 0) return '';
  const date = new Date(value * 1000);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString();
}

export function operationDuration(op) {
  const start = Number(op?.created_at);
  const end = Number(op?.finished_at ?? op?.completed_at);
  if (!Number.isFinite(start) || start <= 0 || !Number.isFinite(end) || end < start) return '';
  const seconds = Math.round(end - start);
  if (seconds < 60) return t('global_duration_s', {n: seconds});
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return rest ? t('global_duration_m', {n: minutes, s: rest}) : t('global_duration_m_only', {n: minutes});
}

// Loud labels are only the states that need the user. The words stay in the tooltip
// and accessible name. The rail paints an icon for those, a dot for Running, and
// no mark for idle, not started, done, and other neutral states.
export function railPresentation(state) {
  if (!state?.key) return null;
  if (state.key === 'pending_ask_user') return {labelKey: 'rail_asking', loud: true, tone: state.tone || 'stale'};
  if (state.key === 'workspace_needs_you' || state.key === 'pending_permission' || state.key === 'wi_state_awaiting_approval')
    return {labelKey: 'rail_needs_you', loud: true, tone: state.tone || 'stale'};
  if (state.key === 'op_failed' || state.key === 'task_state_failed' || state.key === 'wi_state_failed')
    return {labelKey: 'rail_failed', loud: true, tone: 'bad'};
  if (state.key === 'obs_value_streaming') return {labelKey: 'rail_running', loud: false, tone: state.tone || 'info'};
  return {labelKey: state.key, loud: false, tone: state.tone || ''};
}

export function reasonText(code) {
  const token = labelSegment(code);
  if (!token) return '';
  const key = 'reason_' + token;
  const label = t(key);
  return label === key ? t('reason_unavailable') : label;
}

export function shownLabel(count) {
  return t('global_shown', {count});
}
