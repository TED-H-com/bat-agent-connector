// Fixed central review facts for the three requirements accepted in the demo overview.
import {plans} from './plans.ts';
import {hexId, type DemoProject} from './project.ts';
export function qualityReviews(project: DemoProject, url: URL) {
  const plan = plans(project).v3;
  const rows = [project.ids.requirements.email, project.ids.requirements.l10n, project.ids.requirements.unlock].map((id, index) => {
    const requirement = plan.document.requirements.find(row => row.id === id)!;
    return {review_id: hexId('pmqr_', index + 1, 32), sequence: index + 1, workspace_id: project.ids.workspaceId,
      qualified: true, applicable: true, created_at: project.now / 1000,
      context: {plan_ref: {revision_id: plan.revision_id, document_digest: plan.document_digest, scope_revision: plan.adoption.scope_revision}},
      assessment: {document: {qualified: true, criteria: requirement.acceptance.map(row => ({criterion: {kind: 'acceptance', requirement_id: id, input_id: row.source_input_id}, status: 'accepted'}))}}};
  });
  const after = Number(url.searchParams.get('after') || 0), limit = Number(url.searchParams.get('limit') || 20), remaining = rows.filter(row => row.sequence > after), reviews = remaining.slice(0, limit);
  return {workspace_id: project.ids.workspaceId, reviews, next_cursor: remaining.length > reviews.length ? reviews.at(-1)!.sequence : null, visibility_filtered: false};
}
