// Requested identities only. None of these fields grants access or applicability.
const digest = /^[0-9a-f]{64}$/;
const fields = {
  partition: /^[^\u0000-\u001f\u007f]{1,2048}$/,
  workspace_id: /^ws_[0-9a-f]{32}$/, work_item_id: /^wi_[0-9a-f]{20}$/,
  review_id: /^pmqr_[0-9a-f]{32}$/, operation_id: /^op_[0-9a-f]{32}$/,
  return_operation_id: /^op_[0-9a-f]{32}$/, return_review_id: /^pmqr_[0-9a-f]{32}$/, return_document_digest: digest,
  document_digest: digest, origin: /^(result|overview)$/,
  attempt_id: /^att_[0-9a-f]{32}$/, result_id: /^res_[0-9a-f]{32}$/,
  result_digest: /^sha256:[0-9a-f]{64}$/, package_id: /^pmwp_[0-9a-f]{32}$/,
  package_digest: digest, execution_operation_id: /^op_[0-9a-f]{32}$/,
  candidate_commit: /^[0-9a-f]{40}$/, requirement_id: /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$/,
};
const optional = new Set(['return_operation_id','return_review_id','return_document_digest','attempt_id','result_id','result_digest','package_id','package_digest','execution_operation_id','candidate_commit','requirement_id']);
export const assessmentQueryKeys = [...Object.keys(fields), 'version'].map(key => 'qa_' + key);
export function assessmentSelection(value, partition, {complete = false} = {}) {
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).some(key => !(key in fields) && key !== 'version')) return null;
  const result = {};
  for (const [key, pattern] of Object.entries(fields)) {
    if (value[key] == null && optional.has(key) && (!complete || key === 'requirement_id' || key.startsWith('return_'))) continue;
    if (typeof value[key] !== 'string' || !pattern.test(value[key])) return null;
    result[key] = value[key];
  }
  if (result.partition !== partition || !Number.isSafeInteger(value.version) || value.version < 2 || value.version > 33) return null;
  result.version = value.version;
  return result;
}
export function parseAssessmentSelection(params, partition) {
  const value = {};
  for (const key of params.keys()) if (key.startsWith('qa_')) {
    if (!assessmentQueryKeys.includes(key) || params.getAll(key).length !== 1) return null;
    value[key.slice(3)] = params.get(key);
  }
  if (!/^(?:[2-9]|[12][0-9]|3[0-3])$/.test(value.version || '')) return null;
  value.version = Number(value.version);
  return assessmentSelection(value, partition);
}
export function assessmentQuery(value, partition) {
  const selected = assessmentSelection(value, partition);
  if (!selected) throw Error('ASSESSMENT_SELECTION_MISMATCH');
  return new URLSearchParams(Object.entries(selected).map(([key, item]) => ['qa_' + key, String(item)]));
}
export function matchesAssessment(value, data) {
  const context = data?.context, record = data?.assessment;
  return data?.workspace_id === value.workspace_id && data.review_id === value.review_id
    && data.semantics === 'immutable_original_assessment' && data.current_applicability === 'not_evaluated'
    && digest.test(data.context_digest || '') && record?.kind === 'assessment'
    && record.operation_id === value.operation_id && record.version === value.version && record.document_digest === value.document_digest
    && Array.isArray(record.document?.criteria) && context?.work_ref?.work_item_id === value.work_item_id
    && context.package_ref?.workspace_id === value.workspace_id
    && (!value.package_id || context.package_ref.package_id === value.package_id && context.package_ref.document_digest === value.package_digest)
    && (!value.execution_operation_id || context.execution_operation_id === value.execution_operation_id)
    && (!value.candidate_commit || context.native_result?.candidate_commit === value.candidate_commit);
}
export function matchesAttempt(value, data) {
  const a = data?.attempt;
  return data?.workspace_id === value.workspace_id && a?.workspace_id === value.workspace_id
    && a.work_item_id === value.work_item_id && a.attempt_id === value.attempt_id
    && a.package_id === value.package_id && a.execution_operation_id === value.execution_operation_id;
}
export function matchesResult(value, data, serverId) {
  const id = data?.identity;
  return typeof serverId === 'string' && serverId.length > 0 && id?.server_id === serverId
    && data?.contract === 'pm-work-attempt-result/v1' && data.workspace_id === value.workspace_id
    && data.attempt_id === value.attempt_id && id?.workspace_id === value.workspace_id
    && id.work_item_id === value.work_item_id && id.attempt_id === value.attempt_id
    && id.package_id === value.package_id && id.execution_operation_id === value.execution_operation_id
    && data.sources?.result_id === value.result_id && data.sources.digest === value.result_digest
    && data.changes?.fixed_result_commit?.commit_sha === value.candidate_commit
    && data.requirements?.package_ref?.workspace_id === value.workspace_id
    && data.requirements.package_ref.package_id === value.package_id
    && data.requirements.package_ref.document_digest === value.package_digest;
}
export function selectionFromResult({partition, envelope, reference, origin = 'result', requirement_id}) {
  return assessmentSelection({partition, origin, workspace_id: envelope.workspace_id, work_item_id: envelope.identity?.work_item_id,
    attempt_id: envelope.attempt_id, result_id: envelope.sources?.result_id, result_digest: envelope.sources?.digest,
    package_id: envelope.identity?.package_id, package_digest: envelope.requirements?.package_ref?.document_digest,
    execution_operation_id: envelope.identity?.execution_operation_id, candidate_commit: envelope.changes?.fixed_result_commit?.commit_sha,
    review_id: reference.review_id, operation_id: reference.operation_id, version: reference.version,
    document_digest: reference.document_digest,
    ...Object.fromEntries(Object.entries(reference).filter(([key]) => key.startsWith('return_'))), ...(requirement_id ? {requirement_id} : {})}, partition, {complete: true});
}
// Only the selected Result's central projection can supply applicability.
export function contextualAssessment(value, envelope, serverId) {
  if (!matchesResult(value, envelope, serverId)) return null;
  return (envelope.requirements.items || []).filter(item => !value.requirement_id || item.requirement_id === value.requirement_id)
    .flatMap(item => item.assessments || []).find(item => {
      const ref = item.reference;
      return ref?.review_id === value.review_id && ref.operation_id === value.operation_id
        && ref.version === value.version && ref.document_digest === value.document_digest;
    }) || null;
}
