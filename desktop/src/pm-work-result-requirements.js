// Read only the relations resolved by the Result's original central handoff.
// Rendering never chooses an assessment or recomputes readiness/acceptance.
export function resultRequirements({h, t, section, open = new Set()}) {
  if (!section) return null; // Older servers do not provide this projection.
  const label = (value, allowed) => allowed.includes(value) ? t(`wres_req_${value}`) : t('wres_unknown');
  const verdict = value => label(value, ['accepted', 'rejected', 'unassessed', 'not_defined']);
  const text = value => typeof value === 'string' ? value : '';
  const note = value => h('p', {class: 'pm-note-text'}, text(value));
  const source = value => h('code', {class: 'pm-source'}, text(value));
  const line = (key, value) => h('p', {}, t(key), ': ', value);
  const details = (key, title, ...body) => h('details', {
    'data-result-requirement-details': key, ...(open.has(key) ? {open: true} : {}),
  }, h('summary', {'data-work-result-focus': `requirement:${key}`}, title), ...body);
  const record = (key, value) => details(key, t('wres_req_sources'),
    h('pre', {class: 'pm-note-text'}, JSON.stringify(value, null, 2)));
  const operation = (id, key) => /^op_[0-9a-f]{32}$/.test(id || '')
    ? h('a', {href: `#/op/${id}`, 'data-work-result-focus': `requirement:operation:${key}`}, t('wres_view_operation')) : null;
  const element = h('section', {'data-result-requirements': '', class: 'pm-work-result-requirements'},
    h('h4', {}, t('wres_req_heading')));
  if (section.relation !== 'explicit') {
    element.append(note(t(section.relation === 'none' ? 'wres_req_none' : 'wres_req_unavailable')));
    return element;
  }
  element.append(note(t('wres_req_selection')), line('wres_check_verification',
    ['unverified','running','verified','failed','stale','unknown'].includes(section.verification)
      ? t(`wres_verification_${section.verification}`) : t('wres_unknown')));
  if (section.coverage?.reviews_partial) element.append(note(t('wres_req_reviews_partial')));
  if (section.coverage?.packages_partial) element.append(note(t('wres_req_packages_partial')));
  element.append(record('binding', {package_ref: section.package_ref, plan_ref: section.plan_ref,
    dod_ref: section.dod_ref, candidate_commit: section.candidate_commit,
    verification_evidence: section.verification_evidence}));
  for (const item of section.items || []) {
    const key = JSON.stringify(['requirement', item.requirement_id]);
    const row = h('article', {'data-result-requirement': item.requirement_id},
      h('h5', {}, text(item.text)), source(item.requirement_id),
      note(label(item.state, ['no_conclusion','verified_no_conclusion','assessments_recorded','unavailable'])),
      record(`${key}:inputs`, {source_input_id: item.source_input_id, acceptance_input_ids: item.acceptance_input_ids, link: item.link}));
    for (const assessment of item.assessments || []) {
      const ref = assessment.reference || {}, original = assessment.original || {};
      const assessmentKey = JSON.stringify([key, ref.review_id, ref.operation_id, ref.version, ref.document_digest]);
      const summary = `${verdict(assessment.conclusion)} · ${t(assessment.applicable === true ? 'wres_req_applicable' : 'wres_req_no_longer_applies')}`;
      const reasons = (assessment.applicability_reasons || []).map(reason => {
        const translated = t(`pmq_reason_${reason}`);
        return note(translated === `pmq_reason_${reason}` ? t('wres_req_applicability_changed') : translated);
      });
      const criteria = (original.criteria || []).filter(entry => entry.criterion?.kind === 'dod'
        || entry.criterion?.requirement_id === item.requirement_id).map((entry, index) => {
        const criterion = entry.criterion || {};
        return details(`${assessmentKey}:criterion:${index}`,
          `${t(criterion.kind === 'dod' ? 'wres_req_dod' : 'wres_req_criterion')}: ${text(criterion.input_id || criterion.criterion_id)} · ${verdict(entry.status)}`,
          note(entry.rationale), line('wres_check_verification',
            label(entry.verification?.status, ['passed','failed','unavailable','unknown'])),
          record(`${assessmentKey}:proof:${index}`, {criterion, reference: entry.reference, verification: entry.verification}));
      });
      row.append(details(assessmentKey, summary,
        line('wres_req_assessed_by', text(assessment.assessed_by)),
        line('wres_req_dod', verdict(original.dod)),
        note(t(assessment.qualified === true ? 'wres_req_qualified' : 'wres_req_not_qualified')),
        ...reasons, note(original.rationale), ...criteria,
        assessment.revocation ? note(t('wres_req_revoked')) : null,
        operation(ref.operation_id, assessmentKey), record(`${assessmentKey}:source`, assessment)));
    }
    element.append(row);
  }
  if (section.dependent_work?.length) {
    const dependencies = details('dependencies', t('wres_req_dependencies'));
    for (const work of section.dependent_work) {
      const key = JSON.stringify(['dependent', work.work_item_id, work.package_id, work.document_digest]);
      const readiness = value => label(value?.status, ['ready','blocked','unavailable','unknown']);
      const body = h('article', {'data-result-dependent': work.package_id}, h('h5', {}, text(work.title)),
        line('wres_req_without_selection', readiness(work.readiness)));
      if (!work.selections_complete) body.append(note(t('wres_req_selections_partial')));
      for (const selected of work.selections || []) {
        body.append(details(`${key}:${selected.execution_operation_id}`,
          `${t('wres_req_original_selection')}: ${readiness(selected.readiness)}`,
          operation(selected.execution_operation_id, `${key}:${selected.execution_operation_id}`), record(`${key}:${selected.execution_operation_id}:source`, selected)));
      }
      body.append(record(`${key}:source`, {work_item_id: work.work_item_id, package_id: work.package_id,
        document_digest: work.document_digest, prerequisite_ids: work.prerequisite_ids, readiness: work.readiness, link: work.link}));
      dependencies.append(body);
    }
    element.append(dependencies);
  }
  if (section.related?.length) element.append(record('related', section.related));
  return element;
}
