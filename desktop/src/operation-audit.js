// Warnings describe individual effects, independently of the operation status.
export function operationAuditWarnings(op) {
  return Object.entries(op.external_refs?.audit_results || {})
    .filter(([id, warning]) => /^[0-9a-f]{64}$/.test(id) && warning?.state === 'failed'
      && warning.code === 'AUDIT_RESULT_WRITE_FAILED'
      && ['accepted', 'error', 'unknown'].includes(warning.effect_outcome))
    .map(([id, warning]) => ({id, outcome: warning.effect_outcome}));
}

export function operationAuditNotice(op, {h, t}) {
  const warnings = operationAuditWarnings(op);
  if (!warnings.length) return null;
  return h('section', {class: 'note', 'data-operation-audit': ''},
    h('h2', {}, t('audit_result_title')),
    ...warnings.map(warning => h('p', {'data-audit-outcome': warning.outcome},
      t('audit_result_' + warning.outcome))),
    h('p', {}, t('audit_result_repair')));
}
