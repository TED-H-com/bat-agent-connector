import {digest, hexId, unix, type DemoProject} from './project.ts';
import {plans} from './plans.ts';

function briefItem(kind: string, text: string, status: string, source: object, details?: object) {
  return details ? {kind, text, status, source, details} : {kind, text, status, source};
}

// Status and coverage match pm_snapshots._brief. Item text stays the demo's own wording.
function briefDocument(project: DemoProject, plan: ReturnType<typeof plans>['v3'], delivery: object[], blockers: object[], openDecisions: object[]) {
  const goals = (['governance', 'scope'] as const).flatMap(domain =>
    plan.document.sections[domain].statements.map(row => briefItem('attributed_direction', row.value, row.status, {
      kind: 'plan_input', revision_id: plan.revision_id, input_id: row.input_id, source_ref: row.source_ref,
    }, {confirmed_by: project.ids.actor, evidence_verification: 'unassessed'})));
  const adoption = briefItem('adoption', plan.adoption.rationale, 'adopted', {
    kind: 'plan_adoption', revision_id: plan.revision_id, decision_id: hexId('pmdec_', plan.sequence, 32),
  }, {adopted_by: plan.adoption.adopted_by, created_at: plan.adoption.created_at, scope_revision: plan.adoption.scope_revision});
  return {
    goals: {status: 'accepted_sources', items: goals, coverage: {basis: 'accepted_governance_and_scope_inputs', source_statuses_preserved: true}},
    delivery: {status: 'quality_unassessed', items: delivery, coverage: {basis: 'selected_capture_facts', dod: 'unassessed', qualified_deliverables: null, completion_ratio: null, current_environment: 'not_assessed'}},
    blockers: {status: 'partial_source_states', items: blockers, coverage: {basis: 'recorded_attention_states_and_missing_captures', complete_diagnosis: false}},
    deviations: {status: 'unassessed', items: [], coverage: {baseline_result_assessment: 'not_performed', old_binding_is_not_drift: true}},
    decisions: {status: 'recorded_sources', items: [adoption, ...openDecisions], coverage: {basis: 'accepted_adoption_and_fixed_open_inputs', prioritization: 'not_assessed'}},
  };
}

function snapshot(project: DemoProject, sequence: number, snapshotId: string, title: string, asOf: number, plan: ReturnType<typeof plans>['v3'], brief: object) {
  return {
    snapshot_id: snapshotId, workspace_id: project.ids.workspaceId, sequence, title, as_of: asOf,
    journal_cursor: 40 + sequence, template_version: 1, snapshot_digest: digest(100 + sequence), view_digest: digest(200 + sequence),
    created_by: project.ids.actor, operation_id: hexId('op_', 100 + sequence, 32),
    baseline: {status: 'accepted', document_revision: plan.revision_id, document: plan.document, adoption: plan.adoption},
    projects: [], work: [], evidence: [], coverage: {cost: 'unknown'}, visibility: {restricted: false}, brief,
  };
}

export function snapshots(project: DemoProject) {
  const saved = plans(project);
  const decision = (text: string, id: string) => ({kind: 'recorded_open_input', text, status: 'unresolved', source: {kind: 'plan_input', input_id: id}});
  const first = snapshot(project, 1, project.ids.snap1, project.copy.snapshotV2, unix(project.now, -21), saved.v2,
    briefDocument(project, saved.v2, [], [], [decision(project.copy.decisions.budget, hexId('pmi_', 8, 32))]));
  const second = snapshot(project, 2, project.ids.snap2, project.copy.snapshotV3, unix(project.now, -1), saved.v3,
    briefDocument(project, saved.v3, [
      briefItem('pr_merge', project.copy.deliveryMerged, 'merged', {kind: 'evidence', evidence_id: hexId('pme_', 87, 32)}),
      briefItem('deployment', project.copy.deliveryDeployed, 'deployed', {kind: 'evidence', evidence_id: hexId('pme_', 91, 32)}),
    ], [
      briefItem('recorded_attention_state', project.copy.blocker, 'unresolved', {kind: 'plan_input', revision_id: project.ids.planV3, input_id: hexId('pmi_', 9, 32), source_ref: {kind: 'statement', ref: project.copy.planningRef}}),
    ], [decision(project.copy.decisions.budget, hexId('pmi_', 8, 32)), decision(project.copy.decisions.lockout, hexId('pmi_', 10, 32))]));
  return [first, second];
}

export function snapshotListRow(row: ReturnType<typeof snapshots>[number]) {
  const {baseline, brief, evidence, coverage, visibility, view_digest, ...summary} = row;
  return summary;
}

export function pageSnapshots(project: DemoProject, url: URL) {
  const after = Number(url.searchParams.get('after') || 0);
  const limit = Number(url.searchParams.get('limit') || 20);
  const rows = snapshots(project).filter(row => row.sequence > after);
  const slice = rows.slice(0, limit);
  return {version: 1, workspace_id: project.ids.workspaceId, snapshots: slice.map(snapshotListRow),
    visibility_filtered: false, next_cursor: rows.length > slice.length && slice.length ? slice[slice.length - 1].sequence : null};
}
