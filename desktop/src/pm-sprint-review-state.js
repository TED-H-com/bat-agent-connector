// These are projections of server records, never a client workflow or stored cursor.
export const hasSprintReview = caps => caps?.features?.pm_workspaces?.guided_sprint_review?.version === 1;
export function reviewStep(detail, governance = []) {
  if (!detail?.context) return 1;
  if (detail.completion) return 6;
  const draft = detail.draft?.document;
  if (!draft?.guided_review) return 2;
  if (!draft.guided_review.feedback.length) return 3;
  if (!draft.output.next_steps.length || (draft.decisions||[]).some(decision => !governance.some(row => row.revision?.action === 'pm.governance.decide'
    && Object.entries(reviewProposal(detail, decision.id)).every(([key, value]) => row.record.context.review_proposal_ref?.[key] === value)))) return 4;
  return 5;
}
export const reviewPrompts = gaps => ({shown: gaps.slice(0, 3), remaining: Math.max(0, gaps.length - 3)});
export function reviewProposal(detail, decisionId) {
  if (!detail?.draft?.document.guided_review || !detail.draft.document.decisions.some(row => row.id === decisionId)) return null;
  return {meeting_id: detail.meeting.meeting_id, draft_revision_id: detail.draft.revision_id,
    draft_digest: detail.draft.document_digest, decision_id: decisionId};
}
export function reviewInputSource(detail, decisionId) {
  if (!detail?.completion?.document.guided_review || !detail.completion.document.decisions.some(row => row.id === decisionId) || !detail.completion.document.governance_decisions?.some(row => row.decision_id === decisionId)) return null;
  return {kind: 'sprint_review_decision', meeting_id: detail.meeting.meeting_id,
    completion_id: detail.completion.completion_id, completion_digest: detail.completion.document_digest, decision_id: decisionId};
}
export const emptyReviewBasis = () => ({version: 1, results: [], feedback_items: [], governance_revision_ids: [], follow_up_ref: null});

// Typed references contain fixed scalar identities. Object property order is not identity.
export const reviewReferenceKey = reference => JSON.stringify(reference === null ? null
  : Object.fromEntries(Object.keys(reference).sort().map(key => [key, reference[key]])));
export function reviewPromptKeys(detail, governance = []) {
  if(detail?.completion)return [];
  const draft=detail?.draft?.document, fixed=draft?.guided_review;
  return [
    !fixed || fixed.gaps?.length ? 'pm_review_outcomes_prompt' : null,
    !fixed?.feedback?.length ? 'pm_review_feedback_prompt' : null,
    !fixed || !draft.output.next_steps.length || (draft.decisions||[]).some(decision=>!governance.some(row=>row.revision?.action==='pm.governance.decide'
      && Object.entries(reviewProposal(detail,decision.id)||{missing:true}).every(([key,value])=>row.record.context.review_proposal_ref?.[key]===value))) ? 'pm_review_direction_prompt' : null,
  ].filter(Boolean);
}

export function reviewRequirementLabel(snapshot, requirement, fallback) {
  const baseline=snapshot?.baseline;
  if(!requirement.plan_revision_id || requirement.plan_revision_id!==baseline?.document_revision)return fallback;
  return baseline.document?.requirements?.find(row=>row.id===requirement.requirement_id)?.text || fallback;
}
