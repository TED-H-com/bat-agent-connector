import {digest, hexId, sha40, unix, type DemoProject} from './project.ts';

const DOMAINS = ['governance', 'scope', 'schedule', 'finance', 'stakeholders', 'resources', 'risk', 'quality'] as const;

function statement(value: string, inputId: string, status: string, ref: string) {
  return {input_id: inputId, value, status, source_ref: {kind: 'statement', ref}};
}

function requirement(project: DemoProject, row: DemoProject['requirements'][number], inputId: string) {
  return {
    id: row.id, text: row.text, source_input_id: inputId, source_status: 'user_confirmed',
    source_ref: {kind: 'statement', ref: project.copy.planningRef}, confirmed_by: project.ids.actor,
    priority: row.priority, priority_status: 'user_confirmed', depends_on: [],
    acceptance: [{text: row.acceptance, source_input_id: inputId + '-acceptance', source_status: 'user_confirmed'}],
  };
}

function sections(entries: Record<string, ReturnType<typeof statement> | null>, ref: string) {
  return Object.fromEntries(DOMAINS.map(domain => {
    const row = entries[domain];
    return [domain, {source_input_ids: row ? [row.input_id] : [], coverage: row ? 'attributed_inputs' : 'unresolved', statements: row ? [row] : []}];
  }));
}

function planShell(project: DemoProject, revisionId: string, sequence: number, previous: string | null, base: string | null, current: boolean, title: string, markdown: string, documentSections: object, requirements: object[], scopeId: string, commit: string, rationale: string, createdOffset: number) {
  const created = unix(project.now, createdOffset);
  return {
    workspace_id: project.ids.workspaceId, revision_id: revisionId, thread_id: project.ids.threadPlanning,
    planning_draft_id: project.ids.draftId, previous_revision_id: previous, base_accepted_revision_id: base,
    document_digest: digest(sequence + 20), structured_scope_digest: digest(sequence + 40), sequence,
    created_by: project.ids.actor, created_at: created, status: 'adopted', is_current_accepted: current,
    document: {
      schema_version: 1, revision_id: revisionId, workspace_id: project.ids.workspaceId, title, markdown,
      content_status_at_creation: 'proposal', planning_draft_id: project.ids.draftId, thread_id: project.ids.threadPlanning,
      source_thread_version: 4, profile_version: 2, selected_inputs: [], sections: documentSections, requirements,
      unclassified_source_input_ids: [], adoption_status_source: 'workspace_journal', execution_authorized: false,
      scope_coverage: {structured: 'selected_sources_only', markdown: 'unclassified'},
    },
    adoption: {adopted_by: project.ids.actor, created_at: unix(project.now, createdOffset + 1), rationale, commit_sha: commit,
      scope_revision: scopeId, execution_authorized: false},
  };
}

export function plans(project: DemoProject) {
  const ref = project.copy.planningRef;
  const input = (n: number) => hexId('pmi_', n, 32);
  const v3Sections = sections({
    governance: statement(project.copy.goal, input(1), 'user_confirmed', ref),
    scope: statement(project.copy.scope, input(2), 'user_confirmed', ref),
    schedule: statement(project.copy.schedule, input(3), 'user_confirmed', ref),
    finance: statement(project.copy.finance, input(8), 'unresolved', ref),
    stakeholders: statement(project.copy.stakeholders, input(4), 'user_confirmed', ref),
    resources: statement(project.copy.resources, input(5), 'user_confirmed', ref),
    risk: statement(project.copy.smtp, input(6), 'unresolved', ref),
    quality: statement(project.copy.quality, input(7), 'user_confirmed', ref),
  }, ref);
  const v2Sections = sections({
    governance: statement(project.copy.goalV2, input(1), 'user_confirmed', ref),
    scope: statement(project.copy.scope, input(2), 'user_confirmed', ref),
    schedule: statement(project.copy.scheduleV2, input(3), 'user_confirmed', ref),
    finance: null, stakeholders: statement(project.copy.stakeholders, input(4), 'user_confirmed', ref),
    resources: null, risk: null, quality: statement(project.copy.quality, input(7), 'user_confirmed', ref),
  }, ref);
  const v3Requirements = project.requirements.map((row, index) => requirement(project, row, input(20 + index)));
  const v2Requirements = project.requirements.slice(0, 4).map((row, index) => requirement(project, row, input(20 + index)));
  // The charter renders markdown and the structured requirement list. Keep markdown to the goal
  // so the acceptance criteria are not printed twice.
  const markdown = (title: string, lead: string) => `# ${title}\n\n${lead}\n`;
  const v2 = planShell(project, project.ids.planV2, 2, null, null, false, project.copy.planTitleV2,
    markdown(project.copy.planTitleV2, project.copy.goalV2), v2Sections, v2Requirements, project.ids.scopeV2, sha40(2),
    project.copy.adoptionRationaleV2, -20);
  const v3 = planShell(project, project.ids.planV3, 3, project.ids.planV2, project.ids.planV2, true, project.copy.planTitle,
    markdown(project.copy.planTitle, project.copy.goal), v3Sections, v3Requirements, project.ids.scopeV3, sha40(3),
    project.copy.adoptionRationale, -10);
  const head = {workspace_id: project.ids.workspaceId, version: 3, latest_revision_id: project.ids.planV3, accepted_revision_id: project.ids.planV3};
  return {v2, v3, head, all: [v2, v3]};
}

export function planSummary(plan: ReturnType<typeof plans>['v2']) {
  const {document, adoption, status, is_current_accepted, base_accepted_revision_id, ...summary} = plan;
  return summary;
}

export function pagePlans(project: DemoProject, url: URL) {
  const saved = plans(project);
  const after = Number(url.searchParams.get('after') || 0);
  const limit = Number(url.searchParams.get('limit') || 20);
  const rows = saved.all.filter(plan => plan.sequence > after);
  const slice = rows.slice(0, limit);
  return {version: 1, workspace_id: project.ids.workspaceId, head: saved.head, revisions: slice.map(planSummary),
    visibility_filtered: false, next_cursor: rows.length > slice.length && slice.length ? slice[slice.length - 1].sequence : null};
}
