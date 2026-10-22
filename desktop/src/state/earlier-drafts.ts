import {legacyIdentity, managedStorageScope, rebase, type StorageIdentity} from './storage-identity.ts';

export interface EarlierDraft {id: string; text: string; label: string; action?: string; address: string}
type Diagnostic = (code: string) => void;
const diagnostic: Diagnostic = code => console.debug(code);
const problem = 'draft_recovery_unreadable';
const record = (value: unknown): value is Record<string, any> => !!value && typeof value === 'object' && !Array.isArray(value);

// Only authored fields from the audited writers. In particular, never walk an
// intent/request, arbitrary JSON, hidden selectors or attachment metadata.
const pmText: Record<string, string[]> = {};
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
forms('pm.issue.publish pm.issue.update', 'title body');
forms('pm.issue.cancel', 'reason publicReason');
forms('pm.dependencies.declare', 'name ecosystem version path url note');
forms('pm.roadmap.baseline.set', 'title rationale');
forms('pm.roadmap.forecast.set', 'date rationale');
forms('pm.product_goal.revise pm.dod.revise', 'text rationale');
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

const collections: Record<string, Record<string, string[]>> = {
  'pm.meeting.draft': {decisions: ['text', 'rationale'], follow_ups: ['text', 'expected_effect', 'verification'], outcomes: ['observation']},
  'pm.maintenance_review.complete': {follow_ups: ['text', 'expected_effect', 'verification']},
  'pm.maintenance_review.candidate.record': {public_sources: ['url', 'title', 'claim']},
  'pm.roadmap.baseline.set': {milestones: ['title', 'baseline_date']},
};

type TextField = {field: string; text: string; label: string; action?: string};
export function draftText(family: string, suffix: string, raw: string): TextField[] {
  const result: TextField[] = [];
  const add = (field: string, value: unknown, label: string, action?: string) => {
    if (typeof value === 'string' && value.trim()) result.push({field, text: value, label, action});
  };
  if (family === 'draft' && /^send\..+/.test(suffix) && !suffix.endsWith('.queue')) {
    add('text', raw, 'earlier_session'); return result;
  }
  const eligible = ['draft', 'pm', 'create', 'start', 'published', 'dispatch', 'orchestrate', 'labels', 'artifact-review'];
  if (!eligible.includes(family)) return result;
  const action = family === 'pm' ? suffix.match(/(?:^|\.)(pm\.[a-z_.]+|session\.send|work\.start|repository\.continue|github\.pr\.(?:publish|update))\.\{/)?.[1] : undefined;
  if (family === 'pm' && !action && !/^discussion-send\./.test(suffix)) return result;
  const value = JSON.parse(raw);
  if (!record(value)) return result;
  const fields = (names: string[], source: any, label: string, action?: string) => {
    if (record(source)) for (const name of names) add(name, source[name], label, action);
  };
  if (family === 'draft') {
    if (/^answer\./.test(suffix) && Array.isArray(value.answers))
      value.answers.forEach((answer: unknown, index: number) => add(`answers.${index}`, answer, 'earlier_answers'));
    else if (/^wi\.edit\./.test(suffix)) fields(['title', 'goal', 'request', 'acceptance'], value.fields, 'earlier_work');
    else if (/^continue\./.test(suffix)) add('text', value.text, 'earlier_continue');
    else if (suffix === 'published' || /^dispatch\./.test(suffix)) add('text', value.text, 'earlier_start');
  } else if (family === 'pm') {
    if (/^discussion-send\./.test(suffix)) add('content', value.content, 'earlier_discussion');
    else if (value.version === 1 && record(value.values)) {
      if (action && Object.hasOwn(pmText, action)) {
        fields(pmText[action], value.values, 'earlier_pm', action);
        if (action === 'pm.quality.review.record') fields(Object.keys(value.values).filter(name => /^reason_\d+$/.test(name)), value.values, 'earlier_pm', action);
        for (const [name, names] of Object.entries(collections[action] || {})) {
          if (!value.values[name]) continue;
          const rows = JSON.parse(value.values[name]);
          if (Array.isArray(rows)) rows.forEach((row: unknown, index: number) => {
            if (record(row)) for (const field of names) add(`${name}.${index}.${field}`, row[field], 'earlier_pm', action);
          });
        }
      }
    }
  } else if (family === 'create' && value.version === 1) {
    if (/^project\./.test(suffix)) fields(['name', 'description', 'task_project'], value.values, 'earlier_project');
    else if (/^work_item\./.test(suffix)) fields(['title', 'goal', 'request', 'acceptance', 'steps'], value.values, 'earlier_work');
  } else if (['start', 'published', 'dispatch'].includes(family)) fields(['title', 'prompt'], value, 'earlier_start');
  else if (family === 'orchestrate') {
    fields(['message'], value, 'earlier_orchestration');
    if (Array.isArray(value.items)) value.items.forEach((item: any, index: number) => {
      if (record(item)) for (const field of ['title', 'prompt']) add(`items.${index}.${field}`, item[field], 'earlier_orchestration');
    });
  } else if (family === 'labels') add('text', value.text, 'earlier_labels');
  else if (family === 'artifact-review') add('reviewText', value.reviewText, 'earlier_review');
  return result;
}

const marker = (identity: StorageIdentity, id: string) => `batc.earlier-drafts.${managedStorageScope(identity)}.${id}`;
async function entryId(key: string, field: string): Promise<string> {
  const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify([key, field])));
  return Array.from(new Uint8Array(hash), byte => byte.toString(16).padStart(2, '0')).join('');
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
  const candidates = keys.filter(key => key.startsWith('batc.sync.')).map(key => key.slice('batc.sync.'.length));
  const sources = candidates.filter(source => {
    const old = legacyIdentity(source);
    return source !== migrated && old?.server_id === identity.server_id && old.principal_id === identity.principal_id && old.actor === identity.actor
      && !candidates.some(other => other !== source && (other.startsWith(source + '.') || source.startsWith(other + '.')));
  });
  const entries: EarlierDraft[] = [];
  for (const [index, key] of keys.entries()) {
    if (index % 32 === 0) await new Promise(resolve => setTimeout(resolve, 0));
    const source = sources.find(source => rebase(key, source, source) !== null);
    if (!source) continue;
    const boundary = key.indexOf(source), family = key.slice('batc.'.length, boundary - 1), suffix = key.slice(boundary + source.length + 1);
    try {
      const raw = local.getItem(key); if (raw === null) continue;
      for (const field of draftText(family, suffix, raw)) {
        // These two writers mirror the same editable prompt. Retain an orphan
        // attachment draft, but show an identical form prompt only once.
        if (family === 'draft' && (suffix === 'published' || suffix.startsWith('dispatch.'))) {
          const formKey = suffix === 'published' ? `batc.published.${source}` : `batc.dispatch.${source}.${suffix.slice('dispatch.'.length)}`;
          let mirrored = false;
          try {mirrored = JSON.parse(local.getItem(formKey) || 'null')?.prompt === field.text;} catch {report(problem);}
          if (mirrored) continue;
        }
        const id = await entryId(key, field.field);
        if (local.getItem(marker(identity, id)) !== null) continue;
        entries.push({id, text: field.text, label: field.label, action: field.action, address: decodeURIComponent(source.split(':')[0])});
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
  try {
    const key = marker(identity, 'noticed');
    if (local.getItem(key) !== null) return false;
    local.setItem(key, 'shown'); return true;
  } catch {report(problem); return false;}
}
