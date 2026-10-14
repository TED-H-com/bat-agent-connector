// Register pages for the member sign-in demo. Shapes follow pm_register._view / get_register.
// The unfiltered list ends after three rows, the way a lineage-root budget ends a page early.
// The cursor is opaque. Callers pass it back unchanged.
import {digest, hexId, iso, type DemoProject} from './project.ts';

export const REGISTER_PAGE_CURSOR = 'cmVnMQ';

const TYPES = ['risk', 'issue', 'action', 'change', 'decision'] as const;

export function demoFindings(project: DemoProject) {
  const observation_id = hexId('pmgh_', 1, 32);
  const document_digest = digest(1);
  const issue = hexId('pmfn_', 1, 32);
  const pull = hexId('pmfn_', 2, 32);
  const alert = hexId('pmfn_', 3, 32);
  const bare = hexId('pmfn_', 4, 32);
  return {
    observation_id, document_digest,
    issue: {id: issue, finding: {source: 'issues', number: 87, labels: ['dependency']}, native: {kind: 'issues', number: 87}, facts: {labels: ['dependency']}},
    pull: {id: pull, finding: {source: 'dependabot_pull_requests', number: 12, manifests: ['package.json']}, native: {kind: 'dependabot_pull_requests', number: 12}, facts: {manifests: ['package.json']}},
    alert: {id: alert, finding: {source: 'dependabot_alerts', number: 14, package: 'lodash', ecosystem: 'npm', ghsa_id: 'GHSA-35jh-r3h4-6jhm', severity: 'high'}, native: {kind: 'dependabot_alerts', number: 14}, facts: {package: 'lodash', ecosystem: 'npm', ghsa_id: 'GHSA-35jh-r3h4-6jhm', severity: 'high'}},
    bare: {id: bare, finding: {source: 'dependabot_alerts', number: 5}, native: {kind: 'dependabot_alerts', number: 5}, facts: {}},
  };
}
function riskId() {
  return hexId('pmgov_', 1, 32);
}
function followId() {
  return hexId('pmfu_', 1, 32);
}

function labels(project: DemoProject) {
  const zh = project.locale === 'zh-TW';
  return {
    finding: zh ? '過時的相依套件' : 'Outdated dependency',
    risk: zh ? 'SMTP 寄信速率' : 'SMTP rate limit',
    action: zh ? '確認鎖定的後續' : 'Confirm the lockout follow-up',
    reason: zh ? '預算尚未確認' : 'Budget is not confirmed',
    impact: zh ? '上線' : 'Launch',
    priority: zh ? '高' : 'High',
    next: zh ? '把發現轉成工作' : 'Turn the finding into work',
  };
}

export function registerIds(project: DemoProject) {
  const findings = demoFindings(project);
  return {
    finding: `finding.${findings.issue.id}`,
    findingSource: findings.issue.id,
    findingRef: {kind: 'finding' as const, id: findings.issue.id, observation_id: findings.observation_id, document_digest: findings.document_digest},
    pull: `finding.${findings.pull.id}`,
    alert: `finding.${findings.alert.id}`,
    bare: `finding.${findings.bare.id}`,
    decision: `plan_revision.${project.ids.planV3}`,
    change: `proposal.${project.ids.proposalId}`,
    mapping: `issue_mapping.${project.ids.issueMapping}`,
    risk: `governance.${riskId()}`,
    action: `follow_up.${followId()}`,
  };
}

function row(project: DemoProject, spec: {
  row_id: string, type: string, basis: string, title: string | null, status: string | null, owner: string | null,
  impact?: string | null, priority?: string | null, next_step?: string | null, due?: string | null,
  source: {kind: string, id: string}, requirements?: string[], work?: string[], prs?: {repository: string, number: number}[],
  actions?: {action: string, target: Record<string, unknown>, required: Record<string, unknown>}[],
  unknowns?: Record<string, string>, finding?: Record<string, unknown>, created_at?: string | null,
}) {
  const value: Record<string, unknown> = {
    row_id: spec.row_id, type: spec.type, type_basis: spec.basis, title: spec.title, status: spec.status,
    owner: spec.owner, due: spec.due ?? null, impact: spec.impact ?? null, priority: spec.priority ?? null,
    next_step: spec.next_step ?? null, created_at: spec.created_at ?? null, updated_at: null, source: spec.source,
    related: {requirement_ids: spec.requirements || [], work_item_ids: spec.work || [], prs: spec.prs || []},
    available_actions: spec.actions || [], unknowns: spec.unknowns || {},
  };
  if (spec.finding) value.finding = spec.finding;
  return value;
}

function rowsFor(project: DemoProject) {
  const text = labels(project);
  const ids = registerIds(project);
  const findings = demoFindings(project);
  const observed = {type: 'issue', basis: 'neutral', title: null, status: 'observed_open', owner: null, next_step: null};
  return [
    row(project, {row_id: ids.finding, ...observed, impact: text.impact, priority: text.priority, created_at: iso(project.now, 0), source: {kind: 'finding', id: findings.issue.id},
      requirements: [project.ids.requirements.email], work: [project.ids.work.email],
      prs: [{repository: project.ids.repository, number: 87}],
      actions: [{action: 'pm.maintenance.repair', target: {workspace_id: project.ids.workspaceId}, required: {
        policy_id: hexId('pmmp_', 1, 32), rule_key: 'dependency',
        finding_ref: {observation_id: ids.findingRef.observation_id, document_digest: ids.findingRef.document_digest, finding_id: findings.issue.id},
      }}],
      unknowns: {owner: 'not_recorded'}, finding: findings.issue.finding}),
    row(project, {row_id: ids.decision, type: 'decision', basis: 'explicit', title: project.copy.decisions.budget, status: 'open', owner: project.ids.actor,
      due: '2026-10-20T12:00:00.000+00:00', created_at: iso(project.now, -1), source: {kind: 'plan_revision', id: project.ids.planV3}}),
    row(project, {row_id: ids.change, type: 'change', basis: 'explicit', title: project.copy.proposalTitle, status: 'proposed', owner: project.ids.actor,
      created_at: iso(project.now, -12), source: {kind: 'proposal', id: project.ids.proposalId}}),
    row(project, {row_id: ids.mapping, type: 'issue', basis: 'explicit', title: null, status: 'tracking', owner: null,
      source: {kind: 'issue_mapping', id: project.ids.issueMapping}, unknowns: {title: 'not_recorded', owner: 'not_recorded'}}),
    row(project, {row_id: ids.risk, type: 'risk', basis: 'explicit', title: text.risk, status: 'blocked', owner: project.ids.actor,
      created_at: iso(project.now, -40), source: {kind: 'governance', id: riskId()},
      actions: [{action: 'pm.governance.revise', target: {workspace_id: project.ids.workspaceId}, required: {}}]}),
    row(project, {row_id: ids.action, type: 'action', basis: 'explicit', title: text.action, status: 'in_progress', owner: project.ids.actor,
      created_at: iso(project.now, -100), source: {kind: 'follow_up', id: followId()}}),
    row(project, {...observed, row_id: ids.pull, created_at: iso(project.now, -15), source: {kind: 'finding', id: findings.pull.id}, finding: findings.pull.finding, unknowns: {title: 'not_recorded'}}),
    row(project, {...observed, row_id: ids.alert, created_at: iso(project.now, -50), source: {kind: 'finding', id: findings.alert.id}, finding: findings.alert.finding, unknowns: {title: 'not_recorded'}}),
    row(project, {...observed, row_id: ids.bare, created_at: iso(project.now, -120), source: {kind: 'finding', id: findings.bare.id}, finding: findings.bare.finding,
      unknowns: {title: 'not_recorded', 'finding.package': 'not_recorded'}}),
  ];
}

function weekStarts(now: number) {
  const date = new Date(now);
  const monday = Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate() - ((date.getUTCDay() + 6) % 7));
  return Array.from({length: 12}, (_, index) => new Date(monday - (11 - index) * 7 * 86400000).toISOString().slice(0, 10));
}

function ages(counts: Record<string, number>) {
  return ['0-7d', '8-30d', '31-90d', '90d+'].map(bucket => ({bucket, count: counts[bucket] || 0}));
}

function summary(project: DemoProject, selected: ReturnType<typeof rowsFor>, reasons: {reason: string, count: number}[]) {
  const open = selected.filter(item => ['open', 'monitoring', 'observed_open', 'blocked', 'tracking', 'needs_review', 'proposed', 'in_progress', 'draft'].includes(item.status || ''));
  const counts = {'0-7d': 0, '8-30d': 0, '31-90d': 0, '90d+': 0};
  for (const item of open) {
    if (typeof item.created_at !== 'string') continue;
    const days = (project.now - Date.parse(item.created_at)) / 86400000;
    if (!Number.isFinite(days) || days < 0) continue;
    const bucket = days < 8 ? '0-7d' : days < 31 ? '8-30d' : days < 91 ? '31-90d' : '90d+';
    counts[bucket]++;
  }
  const weeks = weekStarts(project.now);
  const monday = (value: unknown) => {
    if (typeof value !== 'string') return null;
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return null;
    return new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate() - (date.getUTCDay() + 6) % 7)).toISOString().slice(0, 10);
  };
  return {
    open_by_age: ages(counts),
    weekly: weeks.map(week_start => ({week_start, opened: selected.filter(item => monday(item.created_at) === week_start).length, resolved: 0})),
    blocking_reasons: reasons,
  };
}

function coverage(project: DemoProject, complete: boolean, selected: ReturnType<typeof rowsFor>, blocked: number | null) {
  return {
    complete, partial_reason: complete ? null : 'incomplete', sources: complete ? [] : ['finding'], time: 'iso8601_utc',
    open_statuses: ['blocked', 'draft', 'in_progress', 'monitoring', 'needs_review', 'observed_open', 'open', 'proposed', 'tracking'],
    resolved_statuses: ['adopted', 'cancelled', 'rejected', 'reported_done', 'reported_resolved', 'succeeded'],
    unclassified_status: complete ? 0 : null, unknown_status: complete ? 0 : null,
    open_without_known_age: complete ? selected.filter(item => item.title == null && item.status === 'tracking').length : null,
    blocked_without_reason: complete ? blocked : null,
    blocking_reasons: 'recorded_reason_or_follow_up_result', findings: 'current_visible_observation', scan_limit_per_source: 200,
    workspace_id: project.ids.workspaceId,
  };
}

function view(project: DemoProject, page: ReturnType<typeof rowsFor>, all: ReturnType<typeof rowsFor>, next: string | null, complete: boolean, reasons: {reason: string, count: number}[] | null) {
  const blocked = complete ? (reasons && reasons.length ? (all.some(item => item.status === 'blocked') ? 1 : 0) : all.filter(item => item.status === 'blocked').length) : null;
  const totals = Object.fromEntries(TYPES.map(type => [type, all.filter(item => item.type === type).length]));
  return {
    workspace_id: project.ids.workspaceId,
    as_of: new Date(project.now).toISOString().replace('Z', '+00:00'),
    rows: page, next_cursor: next,
    total_by_type: complete ? totals : null,
    summary: complete && reasons ? summary(project, all, reasons) : null,
    coverage: coverage(project, complete, all, blocked),
  };
}

function bad(status: number, code: string, message: string) {
  return {status, data: {error: {code, message}}};
}

export function registerList(project: DemoProject, url: URL) {
  const type = url.searchParams.get('type');
  const status = url.searchParams.get('status');
  const owner = url.searchParams.get('owner');
  const q = url.searchParams.get('q');
  const after = url.searchParams.get('after');
  if (q === '' || status === '' || owner === '') return bad(400, 'INVALID_REQUEST', 'Empty register filters are invalid');
  // These owners are coverage stand-ins. The register API has no separate fixture flag.
  if (owner === 'unavailable-source') {
    const partial = rowsFor(project).slice(0, 2);
    return {status: 200, data: view(project, partial, partial, null, false, null)};
  }
  if (owner === 'empty-source') return {status: 200, data: view(project, [], [], null, false, null)};
  let selected = rowsFor(project);
  if (type) selected = selected.filter(item => item.type === type);
  if (status) selected = selected.filter(item => item.status === status);
  if (owner) selected = selected.filter(item => item.owner === owner);
  if (q) selected = selected.filter(item => typeof item.title === 'string' && item.title.toLocaleLowerCase().includes(q.toLocaleLowerCase()));
  const text = labels(project);
  const reasons = type === 'change' ? [] : selected.some(item => item.type === 'decision') ? [{reason: text.reason, count: 1}] : [];
  const unfiltered = !type && !status && !owner && !q;
  let page = selected;
  let next: string | null = null;
  if (unfiltered) {
    if (after === REGISTER_PAGE_CURSOR) page = selected.slice(3);
    else {page = selected.slice(0, 3); next = selected.length > 3 ? REGISTER_PAGE_CURSOR : null;}
  }
  return {status: 200, data: view(project, page, selected, next, true, reasons)};
}

function step(name: string, project: DemoProject, spec: {title?: string | null, actor?: string | null, status?: string | null, ref: {kind: string, id: string}, at?: string | null}) {
  const value: Record<string, unknown> = {step: name, at: spec.at ?? new Date(project.now).toISOString(), title: spec.title ?? null, ref: spec.ref, status: spec.status ?? null};
  if (spec.actor) value.actor = spec.actor;
  return value;
}

export function registerDetail(project: DemoProject, rowId: string) {
  const row = rowsFor(project).find(item => item.row_id === rowId);
  if (!row) return bad(404, 'NOT_FOUND', 'Register row not found');
  let timeline;
  if (rowId === registerIds(project).finding) {
    const at = (offset: number) => new Date(project.now + offset).toISOString();
    timeline = [
      step('finding', project, {title: null, status: 'observed_open', ref: row.source, at: at(0)}),
      step('action', project, {title: null, actor: project.ids.actor, status: 'open', ref: {kind: 'repair', id: hexId('pmrp_', 1, 32)}, at: at(60_000)}),
      step('work', project, {title: project.copy.work.email, actor: project.ids.actor, status: 'in_progress', ref: {kind: 'work_item', id: project.ids.work.email}, at: at(120_000)}),
    ];
  } else if (row.type === 'decision') {
    timeline = [step('decision', project, {title: row.title, actor: project.ids.actor, status: row.status, ref: row.source})];
  } else if (row.type === 'change') {
    timeline = [step('proposal', project, {title: row.title, actor: project.ids.actor, status: row.status, ref: row.source})];
  } else if (row.type === 'risk') {
    timeline = [step('finding', project, {title: row.title, actor: project.ids.actor, status: row.status, ref: row.source})];
  } else if (row.type === 'action') {
    timeline = [step('action', project, {title: row.title, actor: project.ids.actor, status: row.status, ref: row.source})];
  } else {
    timeline = [step('finding', project, {title: row.title, status: row.status, ref: row.source})];
  }
  return {status: 200, data: {
    workspace_id: project.ids.workspaceId, as_of: new Date(project.now).toISOString().replace('Z', '+00:00'), row, timeline,
    timeline_coverage: {complete: true, partial_reason: null, sources: []},
    coverage: coverage(project, true, [row], row.status === 'blocked' ? 1 : 0),
  }};
}

export function registerLabels(project: DemoProject) {
  return labels(project);
}

export function registerMaintenanceRoutes(project: DemoProject, rest: string) {
  const workspace_id = project.ids.workspaceId;
  if (rest === '/maintenance-policies/current') return {status: 200, data: {workspace_id, policy: null, visibility_filtered: false}};
  if (rest === '/release-baselines/current') return {status: 200, data: {workspace_id, baseline: null, visibility_filtered: false}};
  if (rest === '/lifecycle') return {status: 200, data: {workspace_id, lifecycle: {revision: 1}}};
  if (rest === '/maintenance-findings/current') {
    const findings = demoFindings(project);
    const item = (entry: {id: string, native: Record<string, unknown>, facts: Record<string, unknown>}) => ({
      finding_id: entry.id, native: entry.native, facts: entry.facts, existing_pull_request: null, existing_work: [],
    });
    return {status: 200, data: {workspace_id, visibility_filtered: false,
      observation_ref: {observation_id: findings.observation_id, document_digest: findings.document_digest},
      sources: {
        issues: {status: 'complete', findings: [item(findings.issue)]},
        dependabot_pull_requests: {status: 'complete', findings: [item(findings.pull)]},
        dependabot_alerts: {status: 'complete', findings: [item(findings.alert), item(findings.bare)]},
      }}};
  }
  if (rest === '/maintenance-repairs') return {status: 200, data: {workspace_id, repairs: [], next_after: null}};
  return null;
}
