import {h} from './ui/dom.js';
import {timeStamp} from './ui/stamps.js';

// Original-turn evidence never establishes current quiescence, acceptance or death.
export function runtimeCompletion(t, attempt) {
  const value = attempt.runtime_completion;
  if (value == null) return null;
  const operationId = id => /^op_[0-9a-f]{32}$/.test(id || '');
  if (value.runtime_status !== 'original_turn_completed' || value.process_death_proven !== false
    || !operationId(value.observation_operation_id) || !operationId(value.execution_operation_id)
    || value.execution_operation_id !== attempt.operation_id || !/^[0-9a-f]{64}$/.test(value.document_digest || '')
    || !Number.isFinite(value.recorded_at)) throw Error(t('pm_kanban_scope_error'));
  return value;
}

export function runtimeReceipt(t, attempt) {
  const value = runtimeCompletion(t, attempt);
  return value ? h('div', {'data-runtime-completion': value.execution_operation_id},
    h('p', {}, t('pm_kanban_runtime_completed')),
    h('p', {class: 'ui-meta'}, t('pm_kanban_runtime_limits')),
    h('p', {}, t('pm_kanban_runtime_recorded_at'), ' · ', timeStamp(value.recorded_at)),
    h('a', {href: `#/op/${encodeURIComponent(value.observation_operation_id)}`}, t('pm_kanban_runtime_receipt')),
    h('p', {class: 'mono'}, value.observation_operation_id), h('p', {class: 'mono'}, value.document_digest)) : null;
}
