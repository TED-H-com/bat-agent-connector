import {matchingLegacySources, managedStorageScope, rebase, type StorageIdentity} from './storage-identity.ts';

export interface EarlierDraft {id: string; text: string; field: string; label: string; action?: string; address: string;
  state: 'draft' | 'possibly-sent'; href: string; operations: string[]}
type Diagnostic = (code: string) => void;
const diagnostic: Diagnostic = code => console.debug(code);
const problem = 'draft_recovery_unreadable';
const record = (value: unknown): value is Record<string, any> => !!value && typeof value === 'object' && !Array.isArray(value);

// Editable text fields from the audited writers can include server-loaded defaults.
// Never walk an intent/request, arbitrary JSON, hidden selectors or attachment metadata.
export const pmText: Record<string, string[]> = {};
function forms(actions: string, fields: string) {
  for (const action of actions.split(' ')) pmText[action] = fields.split(' ');
}
forms('session.send', 'text');
forms('work.start', 'instruction');
forms('pm.message.append', 'content');
forms('pm.thread.create', 'title content');
forms('pm.thread.rename pm.snapshot.create', 'title');
forms('pm.workspace.create', 'name');
forms('pm.input.record', 'value reference');
forms('pm.plan.draft', 'title markdown');
forms('pm.run.prepare pm.run.analyze pm.run.bat.analyze pm.follow_up.analyze pm.conversation.propose pm.continuation.prepare', 'purpose');
forms('pm.thread.plan pm.thread.plan.revise pm.input.confirm pm.plan.adopt pm.conversation.adopt pm.workspace.adapter.select pm.work.bind pm.project.bind', 'rationale');
forms('pm.work_input.defer', 'reason');
forms('github.pr.publish github.pr.update', 'public_title public_body');
forms('pm.issue.publish', 'title body');
forms('pm.issue.cancel', 'reason publicReason');
forms('pm.dependencies.declare', 'name ecosystem version path url note');
forms('pm.roadmap.baseline.set', 'title rationale');
forms('pm.roadmap.forecast.set', 'date rationale');
forms('pm.product_goal.revise pm.dod.revise', 'rationale');
forms('pm.backlog.revise pm.sprint.select pm.sprint.start pm.sprint.close pm.sprint.cancel', 'rationale');
forms('pm.sprint.create', 'title goal capacity_note rationale');
forms('pm.sprint.goal.revise', 'goal rationale');
forms('pm.meeting.create', 'title rationale');
forms('pm.meeting.note pm.maintenance_review.note', 'content rationale');
forms('pm.meeting.complete', 'discussion_summary rationale');
forms('pm.meeting.context.revise', 'rationale');
forms('pm.meeting.follow_up.record pm.maintenance_review.follow_up.record', 'result rationale');
forms('pm.governance.record pm.governance.revise', 'title description rationale trigger response alternatives distinction');
forms('pm.governance.decide pm.governance.adoption.link', 'rationale');
forms('pm.lifecycle.set_stage pm.lifecycle.pause pm.lifecycle.close pm.lifecycle.reopen pm.lifecycle.retain_inflight', 'reason');
forms('pm.maintenance_review.prepare', 'title');
forms('pm.maintenance_review.candidate.record', 'title problem goal_relation expected_effect options hypotheses rationale');
forms('pm.maintenance_review.complete', 'discussion_summary direction_rationale next_observation_focus rationale');
forms('pm.release_baseline.register', 'rationale limitations');
forms('pm.work_batch.start pm.work_batch.evidence.record pm.quality.review.create pm.quality.review.record pm.maintenance_policy.adopt', 'rationale');
forms('pm.quality.review.revoke', 'reason');
forms('repository.continue', 'basis prompt title');
forms('pm.meeting.draft', 'why delivery_approach capacity_discussion dependency_notes goal_review feedback environment_changes next_steps observations previous_improvement_review no_improvement_rationale unresolved_questions rationale');

export const collections: Record<string, Record<string, string[]>> = {
  'pm.meeting.draft': {decisions: ['text', 'rationale'], follow_ups: ['text', 'expected_effect', 'verification'], outcomes: ['observation']},
  'pm.maintenance_review.complete': {follow_ups: ['text', 'expected_effect', 'verification']},
  'pm.maintenance_review.candidate.record': {public_sources: ['url', 'title', 'claim']},
  'pm.roadmap.baseline.set': {milestones: ['title', 'baseline_date']},
};

export const formText = {
  session: ['text'], answers: ['answers'], work: ['title', 'goal', 'request', 'acceptance'],
  attachment: ['text'], discussion: ['content'], project: ['name', 'description', 'task_project'],
  createWork: ['title', 'goal', 'request', 'acceptance', 'steps'], start: ['title', 'prompt'],
  orchestration: ['message'], items: ['title', 'prompt'], labels: ['text'], review: ['reviewText'],
};
const formTypes = ['draft', 'pm', 'create', 'start', 'published', 'dispatch', 'orchestrate', 'labels', 'artifact-review'];
const pmAction = (suffix: string) => suffix.match(/(?:^|\.)(pm\.[a-z_.]+|session\.send|work\.start|repository\.continue|github\.pr\.(?:publish|update))\.\{/);

type TextField = {field: string; text: string; label: string; action?: string};
export function draftText(family: string, suffix: string, raw: string, report: Diagnostic = diagnostic): TextField[] {
  const result: TextField[] = [];
  if ((suffix === 'shortcut' || suffix.endsWith('.shortcut')) || family === 'draft' && /^continue\.[^.]+\.[^.]+$/.test(suffix)
    || family === 'dispatch' && /^prj_[0-9a-f]{20}\.wi_[0-9a-f]{20}$/.test(suffix)) return result;
  const add = (field: string, value: unknown, label: string, action?: string) => {
    if (typeof value === 'string' && value.trim()) result.push({field, text: value, label, action});
  };
  if (family === 'draft' && /^send\..+/.test(suffix) && !suffix.endsWith('.queue')) {
    add(formText.session[0], raw, 'earlier_session'); return result;
  }
  if (!formTypes.includes(family)) return result;
  const action = family === 'pm' ? pmAction(suffix)?.[1] : undefined;
  if (family === 'pm' && !action && !/^discussion-send\./.test(suffix)) return result;
  const value = JSON.parse(raw);
  if (!record(value)) return result;
  const fields = (names: string[], source: any, label: string, action?: string) => {
    if (record(source)) for (const name of names) add(name, source[name], label, action);
  };
  if (family === 'draft') {
    if (/^answer\./.test(suffix) && Array.isArray(value.answers))
      value.answers.forEach((answer: unknown, index: number) => add(`${formText.answers[0]}.${index}`, answer, 'earlier_answers'));
    else if (/^wi\.edit\./.test(suffix)) fields(formText.work, value.fields, 'earlier_work');
    else if (/^continue\.[^.]+$/.test(suffix)) fields(formText.attachment, value, 'continue_from_checkpoint');
    else if (suffix === 'published' || /^dispatch\./.test(suffix)) fields(formText.attachment, value, 'earlier_start');
  } else if (family === 'pm') {
    if (/^discussion-send\./.test(suffix)) fields(formText.discussion, value, 'earlier_discussion');
    else if (value.version === 1 && record(value.values)) {
      if (action && Object.hasOwn(pmText, action)) {
        const names = action === 'pm.thread.create' && suffix.startsWith('overview-assessment.')
          ? pmText[action].filter(name => name !== 'title') : pmText[action];
        fields(names, value.values, 'earlier_pm', action);
        if (action === 'pm.quality.review.record') fields(Object.keys(value.values).filter(name => /^reason_\d+$/.test(name)), value.values, 'earlier_pm', action);
        for (const [name, names] of Object.entries(collections[action] || {})) {
          if (!value.values[name]) continue;
          try {
            const rows = JSON.parse(value.values[name]);
            if (Array.isArray(rows)) rows.forEach((row: unknown, index: number) => {
              if (record(row)) for (const field of names) add(`${name}.${index}.${field}`, row[field], 'earlier_pm', action);
            });
          } catch {report(problem);}
        }
      }
    }
  } else if (family === 'create' && value.version === 1) {
    if (/^project\./.test(suffix)) fields(formText.project, value.values, 'earlier_project');
    else if (/^work_item\./.test(suffix)) fields(formText.createWork, value.values, 'earlier_work');
  } else if (['start', 'published', 'dispatch'].includes(family)) fields(formText.start, value, 'earlier_start');
  else if (family === 'orchestrate') {
    fields(formText.orchestration, value, 'earlier_orchestration');
    if (Array.isArray(value.items)) value.items.forEach((item: any, index: number) => {
      if (record(item)) for (const field of formText.items) add(`items.${index}.${field}`, item[field], 'earlier_orchestration');
    });
  } else if (family === 'labels') fields(formText.labels, value, 'earlier_labels');
  else if (family === 'artifact-review') fields(formText.review, value, 'earlier_review');
  return result;
}

const marker = (identity: StorageIdentity, id: string) => `batc.earlier-drafts.${managedStorageScope(identity)}.${id}`;
async function entryId(key: string, field: string): Promise<string> {
  const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify([key, field])));
  return Array.from(new Uint8Array(hash), byte => byte.toString(16).padStart(2, '0')).join('');
}

const operationId = (value: unknown): value is string => typeof value === 'string' && /^op_[0-9a-f]{32}$/.test(value);
function evidence(value: unknown) {
  // Only artifact.accept carries reviewText as params.receipt. The separate
  // artifact.capture.managed request sends a preview token, never that text.
  const intents = record(value) ? [value.intent, value.submission, value.accept].filter(item => item != null) : [];
  return {pending: intents.length > 0, operations: intents.flatMap(intent => operationId(intent?.operation_id) ? [intent.operation_id] : [])};
}
function formHref(family: string, suffix: string): string {
  const part = (value: unknown) => encodeURIComponent(typeof value === 'string' ? value : '');
  let target: Record<string, any> = {};
  try {
    const match = family === 'pm' ? pmAction(suffix) : null;
    const json = match ? match.index! + match[0].length - 1 : suffix.indexOf('{');
    if (json >= 0) target = JSON.parse(suffix.slice(json));
  } catch { /* use the form landing */ }
  if (family === 'pm') {
    const workspace = target.workspace_id || (suffix.startsWith('discussion-send.') ? suffix.slice('discussion-send.'.length) : '');
    return '#/pm' + (workspace ? '/' + part(workspace) : '') + (target.thread_id ? '/discussions/' + part(target.thread_id) : '');
  }
  if (family === 'draft') {
    const session = suffix.split('.');
    if (session[0] === 'send' && session.length === 3 || session[0] === 'answer' && session.length === 4)
      return `#/session/${part(session[1])}/${part(session[2])}`;
    if (suffix.startsWith('wi.edit.')) return '#/item/' + part(suffix.slice('wi.edit.'.length));
    if (suffix.startsWith('dispatch.')) return '#/dispatch/' + part(suffix.slice('dispatch.'.length));
    if (suffix === 'published') return '#/published';
  }
  if (family === 'labels') {
    try {const [host, sid] = JSON.parse(suffix); return `#/session/${part(host)}/${part(sid)}`;} catch {return '#/sessions';}
  }
  if (family === 'create') return suffix.startsWith('work_item.') ? '#/project/' + part(suffix.slice('work_item.'.length)) : '#/projects';
  if (family === 'dispatch') return '#/dispatch/' + part(suffix);
  if (family === 'orchestrate') return '#/orchestrate/' + part(suffix.split('.')[0]);
  if (family === 'artifact-review') {
    const route = target.kind === 'session' ? [target.kind, target.host, target.session_id]
      : ['artifact', 'operation', 'task'].includes(target.kind) ? [target.kind, target.artifact_id || target.operation_id || target.task_id, ...(target.kind === 'artifact' ? [String(target.revision)] : [])] : [];
    return '#/artifact-review' + route.map(value => '/' + part(value)).join('');
  }
  return ['start', 'published'].includes(family) ? '#/' + family : '#/sessions';
}

// The caller supplies only the identity accepted by native managed bootstrap.
// Yield between reads; the scan is optional and never holds up a view mount.
export async function earlierDrafts(identity: StorageIdentity, local: Storage, report: Diagnostic = diagnostic): Promise<EarlierDraft[]> {
  let keys: string[], migrated: string | undefined;
  try {
    keys = Array.from({length: local.length}, (_, index) => local.key(index)!).filter(Boolean).sort();
    const receipt = JSON.parse(local.getItem(`batc.storage-migration.${managedStorageScope(identity)}`) || 'null');
    if (receipt?.version === 1 && receipt.target === managedStorageScope(identity) && receipt.status === 'complete') migrated = receipt.source;
  } catch {report(problem); return [];}
  const sources = matchingLegacySources(identity, keys).filter(row => !row.ambiguous && row.scope !== migrated).map(row => row.scope);
  const entries: EarlierDraft[] = [];
  for (const [index, key] of keys.entries()) {
    if (index % 32 === 0) await new Promise(resolve => setTimeout(resolve, 0));
    const source = sources.find(source => rebase(key, source, source) !== null);
    if (!source) continue;
    const boundary = key.indexOf(source), family = key.slice('batc.'.length, boundary - 1), suffix = key.slice(boundary + source.length + 1);
    if (!formTypes.includes(family) || (suffix === 'shortcut' || suffix.endsWith('.shortcut'))) continue;
    try {
      const raw = local.getItem(key); if (raw === null) continue;
      const fields = draftText(family, suffix, raw, report); if (!fields.length) continue;
      const isMessage = family === 'draft' && suffix.startsWith('send.');
      const retained = evidence(isMessage ? null : JSON.parse(raw));
      if (family === 'draft') {
        const sibling = `batc.key.${source}.${suffix}`;
        // Current writers append a request digest; older writers used the bare scope.
        for (const key of keys.filter(key => key === sibling || key.startsWith(sibling + '.') && /^[0-9a-f]{24}$/.test(key.slice(sibling.length + 1)))) {
          retained.pending = true; // A missing/unreadable result never proves no send.
          try {
            const saved = JSON.parse(local.getItem(key) || 'null');
            if (operationId(saved?.op)) retained.operations.push(saved.op);
          } catch {report(problem);}
        }
      }
      // Mirrored attachment prompts may hold the only submission evidence.
      let mirror: any = null;
      const mirrorKey = family === 'draft' && suffix === 'published' ? `batc.published.${source}`
        : family === 'draft' && suffix.startsWith('dispatch.') ? `batc.dispatch.${source}.${suffix.slice('dispatch.'.length)}`
        : family === 'published' && !suffix ? `batc.draft.${source}.published`
        : family === 'dispatch' ? `batc.draft.${source}.dispatch.${suffix}` : null;
      if (mirrorKey && keys.includes(mirrorKey)) {
        try {mirror = JSON.parse(local.getItem(mirrorKey) || 'null');}
        catch {retained.pending = true; report(problem);}
        const mirrored = evidence(mirror);
        retained.pending ||= mirrored.pending; retained.operations.push(...mirrored.operations);
      }
      const operations = [...new Set(retained.operations)].sort();
      for (const field of fields) {
        if (family === 'draft' && mirror?.prompt === field.text) continue;
        const id = await entryId(key, field.field);
        if (local.getItem(marker(identity, id)) !== null) continue;
        entries.push({...field, id, address: decodeURIComponent(source.split(':')[0]),
          state: retained.pending ? 'possibly-sent' : 'draft', operations,
          href: operations.length ? '#/op/' + operations[0] : formHref(family, suffix)});
      }
    } catch {report(problem);}
  }
  return entries;
}

export function hideEarlierDraft(identity: StorageIdentity, local: Storage, entry: EarlierDraft, report: Diagnostic = diagnostic): boolean {
  try {local.setItem(marker(identity, entry.id), 'hidden'); return true;}
  catch {report(problem); return false;}
}

export function firstEarlierDraftNotice(identity: StorageIdentity, local: Storage, report: Diagnostic = diagnostic): boolean {
  try {return local.getItem(marker(identity, 'noticed')) === null;}
  catch {report(problem); return false;}
}
export function markEarlierDraftNotice(identity: StorageIdentity, local: Storage, report: Diagnostic = diagnostic): void {
  try {local.setItem(marker(identity, 'noticed'), 'opened');}
  catch {report(problem);}
}
