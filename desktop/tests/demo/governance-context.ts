// Fixed meeting and follow-up records for the secondary tools. Demo reads never write.
import {digest, hexId, type DemoProject} from './project.ts';
import {snapshots} from './snapshots.ts';
import {plans} from './plans.ts';
export function governanceContext(project: DemoProject, rest: string) {
  const workspace_id = project.ids.workspaceId, actor = project.ids.actor, zh = project.locale === 'zh-TW';
  const saved = snapshots(project)[1], plan = plans(project).v3;
  const sprint_id = hexId('pms_', 1, 32), meeting_id = hexId('pmmeet_', 1, 32), context_revision_id = hexId('pmmctx_', 1, 32);
  const completion_id = hexId('pmmc_', 1, 32), intake_id = hexId('pmfu_', 1, 32), assessment_id = hexId('pmfa_', 1, 32);
  const at = Math.floor(project.now / 1000), follow_up_id = 'recovery';
  const title = zh ? '帳戶復原成果檢視' : 'Account recovery review';
  const followText = zh ? '核對帳戶復原結果' : 'Verify account recovery';
  const sprint = {workspace_id, sprint_id, version: 1, status: 'active', sequence: 1, started_at: at - 3600,
    definition: {title, owner_actor: actor, developers: [actor]}, goal: {revision_id: hexId('pmsg_', 1, 32), text: project.copy.goal}};
  const context = {meeting_id, context_revision_id, document: {sprint_id, snapshot_ref: {snapshot_id: saved.snapshot_id, snapshot_digest: saved.snapshot_digest, as_of: saved.as_of},
    sprint_definition: sprint.definition, sprint_goal: sprint.goal, selected_items: [{binding_id: project.ids.bindings.resetToken, work_item_id: project.ids.work.resetToken, title: project.copy.work.resetToken}],
    artifacts: {dod: {revision_id: hexId('pma_', 1, 32), document: {criteria: [{text: zh ? '核對實際復原流程與測試結果' : 'Check the recovery flow and test evidence'}]}}}}};
  const meeting = {workspace_id, meeting_id, sprint_id, title, kind: 'review', status: 'completed', version: 3, sequence: 1,
    context_revision_id, draft_revision_id: hexId('pmmrev_', 1, 32), completion_id};
  const decision = {id: 'evidence', title: zh ? '先核對復原證據' : 'Review recovery evidence', rationale: followText, source_refs: []};
  const draft = {meeting_id, revision_id: meeting.draft_revision_id, document: {kind: 'review', output: {goal_review: project.copy.goal, outcomes: [], feedback: [], environment_changes: [], next_steps: [followText]},
    decisions: [decision], follow_ups: [], source_refs: [], unresolved_questions: []}};
  const completion = {meeting_id, completion_id, actor, document: {discussion_summary: followText, decisions: [decision],
    follow_ups: [{id: follow_up_id, text: followText, owner_actor: actor, expected_effect: project.copy.goal, verification: followText, source_refs: []}]}};
  const detail = {workspace_id, meeting, context, snapshot: saved, draft, completion, notes: [], follow_up_events: [], history_next_cursors: {notes: null, follow_ups: null}};
  const knowledge = Object.fromEntries(['governance', 'scope', 'schedule', 'finance', 'stakeholders', 'resources', 'risk', 'quality'].map(domain => [domain, {
    baseline_inputs: domain === 'scope' ? [{input_id: hexId('pmi_', 1, 32), status: 'user_confirmed', value: project.copy.goal}] : [], supplemental_inputs: [], answer_links: [], coverage: domain === 'scope' ? 'attributed_inputs' : 'unresolved',
  }]));
  const intake = {workspace_id, intake_id, sequence: 1, version: 1, created_by: actor, created_at: at, document_digest: digest(70),
    document: {snapshot_ref: context.document.snapshot_ref, plan_ref: {document_revision: plan.revision_id, scope_revision: plan.adoption.scope_revision, document_digest: plan.document_digest},
      thread_ref: {thread_id: project.ids.threadFollow, version: 1}, knowledge, issues: [], agenda: [], changes: {}, previous_intake_id: null,
      selected_governance_refs: [], selected_meeting_follow_up_refs: [{completion_id, follow_up_id, follow_up: completion.document.follow_ups[0], original_snapshot_ref: context.document.snapshot_ref}], coverage: {}}};
  const run_id = hexId('pmr_', 1, 32), context_digest = digest(71);
  const assessment = {workspace_id, assessment_id, intake_id, intake_digest: intake.document_digest, sequence: 1, created_by: actor, created_at: at,
    thread_id: project.ids.threadFollow, operation_id: hexId('op_', 70, 32), run_id, context_digest, raw_reply_digest: digest(72), document_digest: digest(73),
    raw_reply: zh ? '需要核對帳戶復原的驗收證據。' : 'Account recovery needs acceptance evidence.',
    document: {version: 1, summary: {text: followText, source_refs: ['plan', 'thread']}, findings: [], tradeoffs: [], questions: [], recommendations: []},
    provenance: {intake_id, intake_digest: intake.document_digest, run_id, context_digest, adapter: 'fixture', actual_model: 'fixture-model',
      source_references: {plan: {pointer: '/plan_ref', digest: digest(74)}, thread: {pointer: '/thread_ref', digest: digest(75)}}},
    validation: {status: 'format_valid', errors: [], conclusions_verified: false}, assessment_basis: 'unadopted_agent_analysis', conclusions_verified: false, accepted_plan_changed: false, execution_authorized: false};
  const ok = (data: unknown) => ({status: 200, data});
  if (rest === '/sprints') return ok({sprints: [sprint], next_cursor: null});
  if (rest === '/meetings') return ok({workspace_id, meetings: [meeting], next_cursor: null});
  if (rest === '/meetings/' + meeting_id) return ok(detail);
  const history = rest.match(/^\/meetings\/([^/]+)\/history\/(notes|follow_ups|contexts|drafts)$/);
  if (history && history[1] === meeting_id) return ok({workspace_id, meeting_id, kind: history[2], records: [], next_cursor: null});
  if (rest === '/follow-ups') return ok({workspace_id, head_version: 1, intakes: [{...intake, document: undefined}], next_cursor: null});
  if (rest === '/follow-ups/latest' || rest === '/follow-ups/' + intake_id) return ok({workspace_id, head_version: 1, intake});
  if (rest === '/follow-up-assessments') {
    const {document, raw_reply, provenance, ...metadata} = assessment;
    return ok({workspace_id, assessments: [metadata], next_cursor: null});
  }
  if (rest === '/follow-up-assessments/' + assessment_id) return ok({workspace_id, assessment});
  return null;
}
