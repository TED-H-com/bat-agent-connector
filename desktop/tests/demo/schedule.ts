// Exact GET /schedule shape: no phase rows, session dates, or totals.
import {iso, hexId, type DemoProject} from './project.ts';
import {roadmap} from './roadmap.ts';
export function scheduleRows(project: DemoProject) {
  const source = roadmap(project), rows: any[] = [];
  const base = {status: 'unknown', blocked: null, waiting: null, depends_on: []};
  for (const m of source.baseline.document.milestones) {
    const forecast = source.forecast.document.milestones.find(value => value.milestone_id === m.milestone_id)!;
    rows.push({...base, id: `milestone:${m.milestone_id}`, parent_id: null, kind: 'milestone', title: m.title,
      depends_on: m.depends_on.map(id => `milestone:${id}`), refs: {milestone_id: m.milestone_id, plan_revision_id: project.ids.planV3, requirement_ids: m.requirement_ids},
      baseline: {start: m.baseline_date, end: m.baseline_date}, forecast: {start: forecast.forecast_date, end: forecast.forecast_date}, actual: null, date_kind: 'milestone'});
    for (const req of project.requirements.filter(value => m.requirement_ids.includes(value.id))) {
      const id = `requirement:${project.ids.planV3}:${req.id}`;
      rows.push({...base, id, parent_id: `milestone:${m.milestone_id}`, kind: 'requirement', title: req.text,
        refs: {requirement_id: req.id, plan_revision_id: project.ids.planV3, milestone_id: m.milestone_id}});
      for (const work of project.work.filter(value => value.requirementId === req.id)) {
        const status = {backlog: 'not_started', in_progress: 'in_progress', waiting: 'waiting', review: 'in_review', accepted: 'accepted'}[work.column];
        const actual = status === 'not_started' ? null : {start: iso(project.now, -8 + work.n / 5), end: status === 'accepted' ? iso(project.now, -2) : null, open: status !== 'accepted'};
        rows.push({...base, id: `work:${work.id}`, parent_id: id, kind: 'work', title: work.title, status,
          waiting: status === 'waiting' ? {reason: 'work_state', since: iso(project.now, -5)} : null,
          refs: {work_item_id: work.id, binding_id: work.bindingId, plan_revision_id: project.ids.planV3, requirement_ids: [req.id]},
          ...(actual ? {baseline: null, forecast: null, actual} : {})});
      }
    }
  }
  const sessions = [
    {work: project.ids.work.email, id: project.ids.sessions.implement, title: project.copy.sessionImplement, status: 'in_progress', managed: true},
    {work: project.ids.work.rate, id: project.ids.sessions.review, title: project.copy.sessionReview, status: 'unknown', managed: true},
    {work: project.ids.work.resetEmail, id: project.ids.sessions.manual, title: project.copy.manualTitle, status: 'unknown', managed: false},
  ];
  for (const s of sessions) rows.push({...base, id: `attempt:session:${s.work}:${project.ids.host}/${s.id}`, parent_id: `work:${s.work}`,
    kind: 'attempt', title: s.title, status: s.status, refs: {session: {host: project.ids.host, session_id: s.id, read_only: !s.managed},
      recorded_state: s.status === 'in_progress' ? 'running' : 'idle', agent: {model: 'example-codex'}}});
  const work = project.ids.work.resetEmail, operation = hexId('op_', 300, 32);
  rows.push({...base, id: `attempt:operation:${work}:${operation}`, parent_id: `work:${work}`, kind: 'attempt', title: 'session.start', status: 'done',
    refs: {operation_id: operation, agent: {cli: 'codex'}}, baseline: null, forecast: null,
    actual: {start: iso(project.now, -7), end: iso(project.now, -6.9), open: false}});
  const order = ['milestone', 'requirement', 'issue', 'work', 'attempt'];
  return rows.sort((a, b) => order.indexOf(a.kind) - order.indexOf(b.kind) || a.id.localeCompare(b.id));
}
export function schedulePage(project: DemoProject, url: URL, pageSize = 100) {
  const rows = scheduleRows(project), token = url.searchParams.get('after');
  const after = token ? Buffer.from(token, 'base64url').toString() : null;
  const index = after ? rows.findIndex(row => row.id === after) + 1 : 0;
  if (after && !index) throw Error('Unknown schedule cursor');
  const selected = rows.slice(index, index + Math.min(pageSize, Number(url.searchParams.get('limit') || 100)));
  const scheduled = selected.filter(row => row.baseline || row.forecast || row.actual), unscheduled = selected.filter(row => !scheduled.includes(row));
  const days = scheduled.flatMap(row => [row.baseline, row.forecast, row.actual].flatMap(span => span ? [span.start, span.end].filter(Boolean).map(value => value.slice(0, 10)) : [])).sort();
  const partial = index + selected.length < rows.length;
  return {version: 1, workspace_id: project.ids.workspaceId, as_of: iso(project.now),
    baseline: {revision_id: project.ids.roadmapBaseline, set_at: iso(project.now, -12), set_by: project.ids.actor},
    forecast: {revision_id: project.ids.roadmapForecast, set_at: iso(project.now, -1)},
    range: days.length ? {start: days[0], end: days.at(-1)} : null, range_scope: partial ? 'returned_page' : 'complete',
    rows: scheduled, unscheduled, unknown: [
      {field: 'blocked', reason: 'no_explicit_blocked_record'}, {field: 'phase', reason: 'no_explicit_phase_records'},
      {field: 'ci_wait', reason: 'no_explicit_check_binding'}, {field: 'token_usage', reason: 'no_explicit_attribution'},
      {field: 'milestone.actual', reason: 'no_explicit_finish_evidence'}, {field: 'work.depends_on', reason: 'no_work_dependency_contract'},
    ], partial, truncated: partial, next_cursor: partial ? Buffer.from(selected.at(-1)!.id).toString('base64url') : null,
    capabilities: {version: 1, read_only: true, forecast: 'recorded_revision_only', critical_path: false, token_attribution: false, phases: false, drag_proposal: false},
    coverage: {basis: 'explicit_roadmap_scope_and_links', visibility_filtered: false, attempts_truncated: false, attempts_unavailable: false,
      work_bindings_truncated: false, issues_truncated: false, network_checked: false, forecast: 'recorded_revision_only', critical_path: false, token_attribution: false},
  };
}
