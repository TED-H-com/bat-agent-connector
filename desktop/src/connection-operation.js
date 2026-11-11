// Presentation of the original operation only. Receipt references and dispatch
// fences cannot establish local success; the central step/decision owns it.
export function connectionOperationRecovery(op) {
  if (!['setup.connection.apply', 'connection.reference.detach'].includes(op.action)) return null;
  const steps = op.steps || [], refs = op.external_refs || {};
  const native = steps.find(step => step.name === 'native_effects');
  const open = steps.filter(step => ['started', 'uncertain'].includes(step.status)).at(-1);
  const terminal = ['succeeded', 'failed', 'cancelled'].includes(op.status);
  let kind;
  if (op.status === 'cancelled' && native?.error?.code === 'LOCAL_EFFECT_NEVER_PROVEN') kind = 'unknown_closed';
  else if (op.status === 'cancelled' && refs.projection_decision?.code === 'CONNECTION_PROJECTION_NOT_SAVED') kind = 'projection_closed';
  else if (op.status === 'needs_attention' && op.error_code === 'CONNECTION_PROJECTION_CHANGED'
    && native?.status === 'succeeded') kind = 'projection_conflict';
  else if (!terminal && native?.status === 'succeeded') kind = 'completion_pending';
  else if (op.status === 'needs_attention' && open?.name === 'native_effects') kind = 'unknown';
  else if (!terminal && refs.native_effects_sent === true) kind = 'dispatched';
  else if (!terminal && open?.name === 'native_effects' && refs.native_effects_sent === false) kind = 'waiting';
  else kind = 'original';
  return {kind, terminal,
    cancelKey: kind === 'unknown' ? 'cop_close_unknown' : kind === 'projection_conflict' ? 'cop_close_projection' : 'cancel',
    cancelBlocked: ['dispatched', 'completion_pending'].includes(kind),
    resumeKey: 'cop_resume',
  };
}

export function connectionOperationNotice(recovery, {h, t, refresh, busy, readback}) {
  if (!recovery) return null;
  return h('section', {class: 'note', 'data-connection-recovery': recovery.kind},
    h('h2', {}, t('cop_title')),
    h('p', {}, t('cop_' + recovery.kind)),
    ['unknown', 'unknown_closed', 'projection_conflict', 'projection_closed'].includes(recovery.kind)
      ? h('p', {}, t('cop_preserved')) : null,
    h('p', {role: 'status', 'data-connection-readback': '', hidden: !readback}, t('cop_readback')),
    h('button', {class: 'secondary', 'data-operation-control': 'refresh',
      'aria-disabled': busy ? 'true' : null, onclick: refresh}, t('cop_refresh')));
}
