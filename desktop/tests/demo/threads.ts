// Discussion threads and one unadopted conversation proposal. List responses spread the profile catalog
// the same way pm_discussions.list_threads does, so the catalog version replaces the outer version.
import {digest, hexId, unix, type DemoProject} from './project.ts';
import {plans} from './plans.ts';
import {demoFindings} from './register.ts';

const POLICY = {context_first: true, max_questions: 3, min_questions: 0, priority: 'consequential gaps for the next decision',
  reuse_existing_answers: true, include_options_and_consequences: true, no_forced_questionnaire: false};
const RULES = ['Authenticated membership and expected thread version are required for writes.',
  'Only an authorized caller can confirm an input; a model cannot supply confirmed_by.',
  'Source references are attributed claims until independently verified.',
  'Planning drafts neither adopt a baseline nor authorize execution.'];

function slot(key: string, domain: string, purpose: string) {
  return {key, domain, purpose};
}

function profile(mode: string) {
  const executable = mode === 'idea' || mode === 'planning';
  const slots = mode === 'idea'
    ? [slot('problem', 'scope', 'Whose problem and what outcome might matter?'), slot('stakeholders', 'stakeholders', 'People affected'),
      slot('alternatives', 'governance', 'Candidate directions'), slot('unknowns', 'risk', 'Open questions')]
    : mode === 'planning'
      ? [slot('value', 'governance', 'Intended value'), slot('scope', 'scope', 'In scope and out of scope'),
        slot('schedule', 'schedule', 'Baseline versus forecast'), slot('finance', 'finance', 'Budget still open'),
        slot('stakeholders', 'stakeholders', 'Accountable owners'), slot('resources', 'resources', 'Who implements and reviews'),
        slot('risk', 'risk', 'Open risks'), slot('quality', 'quality', 'How acceptance is judged')]
      : [slot('progress', 'schedule', 'Actual progress'), slot('cost', 'finance', 'Cost so far'),
        slot('blockers', 'risk', 'Open blockers'), slot('verification', 'quality', 'What was checked')];
  return {
    profile_id: 'pm.' + mode, version: 2, mode, executable, status: executable ? 'local_commands' : 'contract_only',
    runtime_available: false, purpose: mode === 'follow_up' ? 'Compare actual evidence with commitments.' : 'Prepare the member sign-in discussion.',
    context_selection: ['selected discussion'], input_slots: slots, question_policy: {...POLICY, no_forced_questionnaire: mode === 'idea'},
    allowed_commands: executable ? ['pm.message.append', 'pm.input.record', 'pm.input.confirm', mode === 'idea' ? 'pm.thread.plan' : 'pm.thread.plan.revise'] : [],
    output_contract: ['sourced notes'], exit_intents: ['explicit next step'], validation_rules: RULES,
  };
}

function catalog() {
  return {version: 2, profiles: [profile('idea'), profile('planning'), profile('follow_up')], executable_modes: ['idea', 'planning'], runtime_available: false};
}

function message(project: DemoProject, n: number, content: string, offset: number) {
  return {message_id: hexId('pmm_', n, 32), sequence: 1, actor: project.ids.actor, content, created_at: unix(project.now, offset),
    role: 'user', operation_id: hexId('op_', 300 + n, 32)};
}

function inputRow(project: DemoProject, n: number, slotName: string, value: string, status: string, offset: number) {
  return {input_id: hexId('pmi_', n, 32), sequence: n, slot: slotName, value, status, source_ref: {kind: 'statement', ref: project.copy.planningRef},
    recorded_by: project.ids.actor, confirmed_by: status === 'user_confirmed' ? project.ids.actor : null, observed_at: unix(project.now, offset),
    evidence_verification: 'unverified_claim', operation_id: hexId('op_', 320 + n, 32)};
}

export function threadRows(project: DemoProject) {
  const stamp = (offset: number) => unix(project.now, offset);
  const findings = demoFindings(project);
  return [
    {thread_id: project.ids.threadIdea, workspace_id: project.ids.workspaceId, title: project.copy.threads.idea, mode: 'idea', version: 2,
      created_by: project.ids.actor, created_at: stamp(-30), updated_at: stamp(-28), draft_id: null, operation_id: hexId('op_', 1, 32)},
    {thread_id: project.ids.threadPlanning, workspace_id: project.ids.workspaceId, title: project.copy.threads.planning, mode: 'planning', version: 4,
      created_by: project.ids.actor, created_at: stamp(-20), updated_at: stamp(-10), draft_id: project.ids.draftId, operation_id: hexId('op_', 2, 32)},
    {thread_id: project.ids.threadFollow, workspace_id: project.ids.workspaceId, title: project.copy.threads.delivery, mode: 'follow_up', version: 3,
      created_by: project.ids.actor, created_at: stamp(-2), updated_at: stamp(-1), draft_id: null, operation_id: hexId('op_', 3, 32),
      context: [{kind: 'finding', id: findings.alert.id, observation_id: findings.observation_id,
        document_digest: findings.document_digest, position: 0, available: true, label: '#14', bound_label: '#14',
        finding: findings.alert.finding}]},
  ];
}

function planningInputs(project: DemoProject) {
  return [
    inputRow(project, 1, 'value', project.copy.goal, 'user_confirmed', -20),
    inputRow(project, 2, 'scope', project.copy.scope, 'user_confirmed', -20),
    inputRow(project, 3, 'schedule', project.copy.schedule, 'user_confirmed', -10),
    inputRow(project, 4, 'stakeholders', project.copy.stakeholders, 'user_confirmed', -20),
    inputRow(project, 5, 'resources', project.copy.resources, 'user_confirmed', -10),
    inputRow(project, 6, 'risk', project.copy.smtp, 'unresolved', -2),
    inputRow(project, 7, 'quality', project.copy.quality, 'user_confirmed', -10),
    inputRow(project, 8, 'finance', project.copy.finance, 'unresolved', -10),
  ];
}

function draft(project: DemoProject, inputs: ReturnType<typeof planningInputs>) {
  const selected = inputs.filter(row => row.slot !== 'finance');
  const finance = inputs.find(row => row.slot === 'finance');
  return {
    draft_id: project.ids.draftId, status: 'draft', title: project.copy.planTitle, rationale: project.copy.draftRationale,
    selected_inputs: selected, candidate_inputs: finance ? [finance] : [], selected_input_ids: selected.map(row => row.input_id),
    unknowns: [{slot: 'finance', domain: 'finance', status: 'unresolved'}], scope_revision: null, execution_authorized: false,
    adopted: false, candidates_truncated: false,
  };
}

export function threadList(project: DemoProject, url: URL) {
  const after = url.searchParams.get('after');
  const limit = Number(url.searchParams.get('limit') || 50);
  const rows = threadRows(project).filter(row => !after || row.thread_id > after);
  const slice = rows.slice(0, limit);
  return {version: 1, workspace_id: project.ids.workspaceId, threads: slice, visibility_filtered: false,
    next_cursor: rows.length > slice.length ? slice[slice.length - 1]?.thread_id ?? null : null,
    capabilities: {write: false, decide: false, runtime: false, adopt: false, dispatch: false}, ...catalog(),
    runtime: {available: false, adapter: 'bat', legacy: true, reason: 'external_tool_policy_unverified'}};
}

export function threadDetail(project: DemoProject, threadId: string) {
  const thread = threadRows(project).find(row => row.thread_id === threadId);
  if (!thread) return null;
  const coverage = {messages_truncated: false, inputs_truncated: false, history_truncated: false};
  const capabilities = {write: false, decide: false, runtime: false, adopt: false, dispatch: false};
  if (thread.mode === 'idea') {
    return {version: 1, thread, profile: profile('idea'), messages: [message(project, 1, project.copy.ideaNote, -30)],
      inputs: [inputRow(project, 11, 'problem', project.copy.ideaNote, 'user_confirmed', -30)], input_history: [], draft: null,
      next_message_cursor: null, next_input_cursor: null, coverage, capabilities};
  }
  if (thread.mode === 'planning') {
    const inputs = planningInputs(project);
    return {version: 1, thread, profile: profile('planning'), messages: [message(project, 2, project.copy.planningNote, -18)],
      inputs, input_history: [], draft: draft(project, inputs), next_message_cursor: null, next_input_cursor: null, coverage, capabilities};
  }
  return {version: 1, thread, profile: profile('follow_up'), messages: [message(project, 3, project.copy.deliveryNote, -1)],
    inputs: [], input_history: [], draft: null, next_message_cursor: null, next_input_cursor: null, coverage, capabilities};
}

function proposalDocument(project: DemoProject) {
  const candidates = project.requirements.flatMap(row => {
    const key = row.id.replace('membership.', '');
    return [
      {key, text: row.text, kind: 'proposal', source_refs: ['note.owner']},
      {key: key + '_acceptance', text: row.acceptance, kind: 'proposal', source_refs: ['note.owner']},
    ];
  });
  candidates.push(
    {key: 'decision_recovery_budget', text: project.copy.finance, kind: 'unknown', source_refs: ['note.owner']},
    {key: 'decision_lockout', text: project.copy.decisions.lockout, kind: 'unknown', source_refs: ['note.owner']},
  );
  return {
    version: 1, title: project.copy.proposalTitle, summary: project.copy.proposalSummary, candidates,
    requirements: project.requirements.map(row => ({
      id: row.id, source_key: row.id.replace('membership.', ''), acceptance_keys: [row.id.replace('membership.', '') + '_acceptance'],
      priority: row.priority, depends_on: [],
    })),
    questions: [{question: project.copy.proposalQuestion, why_now: project.copy.proposalWhy, source_refs: ['note.owner']}],
    removed_requirement_ids: [],
  };
}

export function proposalDetail(project: DemoProject) {
  const saved = plans(project);
  const document = proposalDocument(project);
  return {
    version: 1, workspace_id: project.ids.workspaceId,
    proposal: {
      workspace_id: project.ids.workspaceId, proposal_id: project.ids.proposalId, thread_id: project.ids.threadPlanning,
      operation_id: hexId('op_', 5, 32), run_id: project.ids.runId, sequence: 1, document, document_digest: digest(71),
      raw_reply: project.copy.proposalSummary, validation: {status: 'format_valid', errors: []}, created_by: project.ids.actor,
      created_at: unix(project.now, -1), accepted_plan_changed: false, execution_authorized: false,
      assessment_basis: 'unadopted_agent_proposal', conclusions_verified: false, adoptable: true, adoptable_reason: null, adopted_revision_id: null,
      context: {
        thread_version: 4, plan_head: {version: saved.head.version, accepted_revision_id: saved.head.accepted_revision_id},
        accepted_plan: saved.v3, source_references: {'note.owner': {quote: project.copy.planningNote, id: hexId('pmm_', 2, 32), digest: digest(9), kind: 'message'}},
      },
    },
  };
}

export function proposalList(project: DemoProject, url: URL) {
  const threadId = url.searchParams.get('thread_id');
  const after = Number(url.searchParams.get('after') || 0);
  const limit = Number(url.searchParams.get('limit') || 20);
  const detail = proposalDetail(project).proposal;
  const row = {sequence: detail.sequence, proposal_id: detail.proposal_id, thread_id: detail.thread_id, operation_id: detail.operation_id,
    run_id: detail.run_id, document_digest: detail.document_digest, created_by: detail.created_by, created_at: detail.created_at, validation: detail.validation, title: detail.document?.title ?? null, adopted_revision_id: detail.adopted_revision_id, adoptable: detail.adoptable, adoptable_reason: detail.adoptable_reason};
  const rows = !threadId || threadId === project.ids.threadPlanning ? [row].filter(item => item.sequence > after) : [];
  const slice = rows.slice(0, limit);
  return {version: 1, workspace_id: project.ids.workspaceId, proposals: slice,
    next_cursor: rows.length > slice.length && slice.length ? slice[slice.length - 1].sequence : null,
    coverage: {visibility_filtered: false, scanned: slice.length}};
}

export function workspaceCapabilities(project: DemoProject) {
  return {workspace_id: project.ids.workspaceId, plan_drafting: true, plan_adoption: true, plan_adoption_available: false,
    plan_adoption_reason: 'pm_worktree_required', worktree_selection: null, worktrees: [],
    conversation_proposals: {version: 1, adoption_available: false}};
}
