import {digest, hexId, unix, type DemoProject} from './project.ts';

function milestone(project: DemoProject, id: string, title: string, baseline: string, requirements: string[], bindings: string[], depends: string[]) {
  return {
    milestone_id: id, title, plan_revision_id: project.ids.planV3, plan_document_digest: digest(23), scope_revision: project.ids.scopeV3,
    requirement_ids: requirements, work_binding_ids: bindings, baseline_date: baseline, depends_on: depends,
    original_baseline_date: baseline, first_baseline_revision_id: project.ids.roadmapBaseline,
  };
}

export function roadmap(project: DemoProject) {
  const req = project.ids.requirements;
  const bind = project.ids.bindings;
  const dates = project.dates;
  const milestones = [
    milestone(project, 'design_review', project.locale === 'zh-TW' ? '設計審查' : 'Design review', dates.designDate,
      [req.email, req.rate, req.a11y, req.l10n], [bind.email, bind.rate, bind.a11y, bind.l10n], []),
    milestone(project, 'recovery_review', project.locale === 'zh-TW' ? '復原審查' : 'Recovery review', dates.recoveryBaseline,
      [req.resetEmail, req.resetToken, req.audit], [bind.resetEmail, bind.resetToken, bind.audit], ['design_review']),
    milestone(project, 'launch', project.locale === 'zh-TW' ? '上線' : 'Launch', dates.launchDate,
      [req.unlock], [bind.unlock], ['recovery_review']),
  ];
  const created = unix(project.now, -12);
  const baseline = {
    sequence: 1, revision_id: project.ids.roadmapBaseline, workspace_id: project.ids.workspaceId, kind: 'baseline',
    previous_revision_id: null, baseline_revision_id: null, document_digest: digest(61), created_by: project.ids.actor,
    created_at: created, operation_id: hexId('op_', 61, 32),
    document: {
      title: project.copy.planTitle, rationale: project.copy.schedule, plan_head_at_creation: project.ids.planV3,
      changes: {added: [], removed: [], moved: []}, status: 'accepted_scheduling_baseline', git_materialization: 'not_recorded',
      execution_authorized: false, milestones,
    },
  };
  const forecast = {
    sequence: 2, revision_id: project.ids.roadmapForecast, workspace_id: project.ids.workspaceId, kind: 'forecast',
    previous_revision_id: project.ids.roadmapBaseline, baseline_revision_id: project.ids.roadmapBaseline, document_digest: digest(62),
    created_by: project.ids.actor, created_at: unix(project.now, -1), operation_id: hexId('op_', 62, 32),
    document: {
      baseline_revision_id: project.ids.roadmapBaseline, rationale: project.copy.schedule,
      milestones: [
        {milestone_id: 'design_review', forecast_date: dates.designDate},
        {milestone_id: 'recovery_review', forecast_date: dates.recoveryForecast},
        {milestone_id: 'launch', forecast_date: dates.launchDate},
      ],
      updated_milestone_ids: ['recovery_review'],
    },
  };
  const head = {workspace_id: project.ids.workspaceId, version: 2, baseline_revision_id: baseline.revision_id,
    forecast_revision_id: forecast.revision_id, observation_revision_id: null};
  return {version: 1, workspace_id: project.ids.workspaceId, head, baseline, forecast, observation: null, revisions: [baseline, forecast]};
}

// GET /roadmap does not include the history list. Revisions stay on the builder for the history endpoints.
export function roadmapView(project: DemoProject) {
  const {revisions, ...body} = roadmap(project);
  return body;
}

export function roadmapRevisionList(project: DemoProject, url: URL) {
  const current = roadmap(project);
  const after = Number(url.searchParams.get('after') || 0);
  const limit = Number(url.searchParams.get('limit') || 20);
  const rows = current.revisions.filter(row => row.sequence > after);
  const slice = rows.slice(0, limit).map(({document, ...row}) => row);
  return {version: 1, workspace_id: project.ids.workspaceId, head: current.head, revisions: slice,
    next_cursor: rows.length > slice.length && slice.length ? slice[slice.length - 1].sequence : null};
}

export function workBindings(project: DemoProject, url: URL) {
  const after = Number(url.searchParams.get('after') || 0);
  const limit = Number(url.searchParams.get('limit') || 100);
  const filtered = project.work.filter(item => item.n > after);
  const rows = filtered.slice(0, limit);
  return {
    version: 1, workspace_id: project.ids.workspaceId,
    next_cursor: filtered.length > rows.length && rows.length ? rows[rows.length - 1].n : null,
    bindings: rows.map(item => ({
      binding_id: item.bindingId, workspace_id: project.ids.workspaceId, work_item_id: item.id, project_binding_id: project.ids.projectBindingId,
      project_id: project.ids.projectId, work_version: item.version, previous_binding_id: null, plan_revision_id: project.ids.planV3,
      requirement_ids: [item.requirementId], rationale: project.copy.bindingRationale, actor: project.ids.actor,
      created_at: unix(project.now, -12), operation_id: `op_${item.n.toString(16).padStart(32, '0')}`, sequence: item.n,
      association_basis: 'explicit_owner_association', execution_spec: 'not_assigned',
    })),
  };
}
