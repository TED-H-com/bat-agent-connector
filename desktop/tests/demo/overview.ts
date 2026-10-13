import {hexId, digest, unix, type DemoProject} from './project.ts';
import {plans} from './plans.ts';
import {snapshots} from './snapshots.ts';
import {roadmap} from './roadmap.ts';

// GET /overview: same nullable counts, ready-made refs, epoch times and bounded lists
// as pm_overview.Projection.read. The state field is the backend delivery contract.
export function overview(project: DemoProject) {
  const {ids, copy} = project, plan = plans(project).v3;
  const milestones = roadmap(project).revisions[0].document.milestones.map((row: any) => ({
    milestone_id: row.milestone_id, title: row.title, baseline_date: row.baseline_date,
    forecast_date: row.milestone_id === 'recovery_review' ? project.dates.recoveryForecast : row.baseline_date,
    delta_days: row.milestone_id === 'recovery_review' ? 3 : 0,
    status: row.milestone_id === 'design_review' ? 'accepted' : row.milestone_id === 'recovery_review' ? 'in_progress' : 'not_started',
    ref: {kind: 'milestone', id: row.milestone_id, revision_id: ids.roadmapBaseline},
  }));
  const row = (kind: string, title: string, n: number, days: number, requirement?: string) => ({
    key: `${kind}:governance:${hexId('pmgov_', n, 32)}`, kind, title, severity: kind === 'blocker' ? 'high' : 'medium',
    since: unix(project.now, -days), context: {requirement_ids: requirement ? [requirement] : [], revision_id: ids.planV3},
    ref: {kind: 'governance', id: hexId('pmgov_', n, 32)},
  });
  const next = milestones.find((row: any) => row.milestone_id === 'recovery_review');
  return {version: 1, workspace_id: ids.workspaceId, as_of: unix(project.now), lifecycle: {stage: 'development', since: unix(project.now, -9)},
    plan: {revision_id: plan.revision_id, number: plan.sequence, title: plan.document.title, goal: plan.document.selected_inputs.find(row => row.slot === 'goal' && row.status === 'user_confirmed')?.value ?? null, adopted_at: plan.adoption.created_at, adopted_by: ids.actor},
    progress: {basis: {revision_id: ids.planV3}, requirements: 8, accepted: 3, in_review: 1, in_progress: 2, blocked: 1, not_started: 1, complete: true, unknown_reason: null},
    next_milestone: next, milestones, milestones_truncated: false,
    attention: [row('blocker', copy.blocker, 1, 5, ids.requirements.audit), row('decision', copy.decisions.budget, 2, 4), row('decision', copy.decisions.lockout, 3, 3),
      {key: 'deviation:milestone:recovery_review', kind: 'deviation', title: next.title, severity: 'medium', since: unix(project.now, -2),
        context: {milestone_id: 'recovery_review'}, ref: next.ref, delta_days: 3, reason: 'forecast_later_than_baseline'}, row('risk', copy.smtp, 4, 1)],
    attention_counts: {blocker: 1, decision: 2, deviation: 1, risk: 1}, attention_truncated: false,
    recent_deliveries: [{work_item_id: ids.work.email, title: copy.work.email, state: 'deployed', at: unix(project.now, -1), operation_id: ids.deployOp},
      {work_item_id: ids.work.l10n, title: copy.work.l10n, state: 'merged', at: unix(project.now, -2), operation_id: ids.mergeOp},
      {work_item_id: ids.work.unlock, title: copy.work.unlock, state: 'accepted', at: unix(project.now, -3)}], recent_deliveries_complete: true,
    coverage: {complete: true, sources: Object.fromEntries(['plan', 'work', 'governance', 'quality', 'roadmap', 'forecast', 'proposals', 'findings'].map(key => [key, {complete: true, next_cursor: null}])), basis: 'current_visible_central_records', network_checked: false}, unknown: [],
  };
}

// Related governance reads for navigating from the overview to the existing inspector.
export function overviewGovernance(project: DemoProject) {
  const saved = snapshots(project)[1], plan = plans(project).v3;
  return overview(project).attention.filter(row => row.ref.kind === 'governance').map((row, index) => {
    const revision = hexId('pmgrev_', index + 1, 32), kind = row.kind === 'blocker' ? 'dependency' : row.kind === 'decision' ? 'change' : 'risk';
    return {record: {workspace_id: project.ids.workspaceId, record_id: row.ref.id, revision_id: revision, sequence: index + 1, version: 1, kind,
      root_cause_key: `demo-${index + 1}`, context_digest: digest(20 + index), created_by: project.ids.actor, created_at: row.since,
      operation_id: hexId('op_', index + 20, 32), context: {snapshot_ref: {snapshot_id: saved.snapshot_id, snapshot_digest: saved.snapshot_digest, as_of: saved.as_of, journal_cursor: saved.journal_cursor},
        plan_revision_id: plan.revision_id, scope_revision: plan.adoption.scope_revision, plan_document_digest: plan.document_digest, coverage: saved.coverage}},
    revision: {record_id: row.ref.id, revision_id: revision, sequence: index + 1, version: 1, actor: project.ids.actor, created_at: row.since, previous_revision_id: null,
      operation_id: hexId('op_', index + 20, 32), action: 'pm.governance.record', document_digest: digest(40 + index), document: {
        title: row.title, description: row.title, owner_actor: project.ids.actor, affected_requirement_ids: row.context.requirement_ids || [], trigger: null, response: null, review_at: null,
        status: 'open', dimension: null, alternatives: [], proposed_revision: null, sources: [], rationale: row.title, assessment_basis: 'attributed_management_finding',
        verification_status: 'unassessed', decision: null, adoption: null, disambiguation: null, scope_applied: false, execution_authorized: false}}};
  });
}
