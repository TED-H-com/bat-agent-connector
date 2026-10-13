import {hexId, iso, unix, type DemoProject} from './project.ts';

function metric(value: number | null, observed: number | null, covered: number) {
  return {value, observed, covered};
}

function part(type: string, instant: Date, tz: string) {
  return new Intl.DateTimeFormat('en-US', {timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit'}).formatToParts(instant)
    .find(item => item.type === type)?.value || '';
}

function localDate(instant: Date, tz: string) {
  try {return `${part('year', instant, tz)}-${part('month', instant, tz)}-${part('day', instant, tz)}`;}
  catch {return instant.toISOString().slice(0, 10);}
}

function localDates(sinceIso: string, untilIso: string, tz: string) {
  const since = Date.parse(sinceIso), until = Date.parse(untilIso);
  if (!Number.isFinite(since) || !Number.isFinite(until) || until <= since) return [];
  const end = until - 1;
  const dates: string[] = [];
  const seen = new Set<string>();
  for (let cursor = since; cursor <= end && dates.length < 92; cursor += 12 * 60 * 60 * 1000) {
    const key = localDate(new Date(cursor), tz);
    if (key && !seen.has(key)) {seen.add(key); dates.push(key);}
  }
  const last = localDate(new Date(end), tz);
  if (last && !seen.has(last) && dates.length < 92) dates.push(last);
  dates.sort();
  return dates;
}

// Fixed dates keep the screenshots honest: today is still being read, yesterday is settling,
// one covered zero stays zero, and one null total is a gap rather than a zero bar.
function planDay(date: string) {
  if (date === '2026-10-13') return {codex: 420, claude: 180, codexComplete: false, claudeComplete: false};
  if (date === '2026-10-12') return {codex: 260, claude: 90, codexComplete: false, claudeComplete: false};
  if (date === '2026-10-10') return {codex: null, claude: 150, codexComplete: true, claudeComplete: true};
  if (date === '2026-10-09') return {codex: 0, claude: 40, codexComplete: true, claudeComplete: true};
  const day = Number(date.slice(-2)) || 0;
  return {codex: 100 + day, claude: 20 + day, codexComplete: true, claudeComplete: true};
}

function dayRecord(date: string, total: number | null, complete: boolean) {
  return {date, complete, tokens: {
    // A gap keeps component samples so a client that re-adds them cannot draw a bar.
    input: total == null ? 50 : total, cache_read: null, cache_write: null, output: total == null ? 25 : null, reasoning: null, total,
  }};
}

function usable(project: DemoProject, sourceId: string, adapter: string, role: string, quota: string | null, metrics: object, observations: number, calls: number | null, configurations: object[]) {
  const at = iso(project.now);
  return {
    source_id: sourceId, adapter, role, host_id: project.ids.serverId, host_kind: 'central_local', status: 'current',
    checked_at: at, successful_at: at, quota_pool: quota, capacity: null, forecast: null, metrics, observations,
    configurations, calls, first_at: iso(project.now, -10), last_at: iso(project.now, -1), configurations_partial: false,
  };
}

export function resources(project: DemoProject, url: URL) {
  const since = url.searchParams.get('since') || iso(project.now, -7);
  const until = url.searchParams.get('until') || iso(project.now);
  const dates = localDates(since, until, url.searchParams.get('tz') || 'UTC');
  const plans = dates.map(date => ({date, ...planDay(date)}));
  const sum = (key: 'codex' | 'claude') => plans.reduce((total, day) => total + (typeof day[key] === 'number' ? day[key] : 0), 0);
  const codexTotal = sum('codex'), claudeTotal = sum('claude');
  const codex = usable(project, project.ids.sources.codex, 'codex-rollout-v1', 'implementer', project.ids.poolId, {
    input: metric(1200, 1200, 4), cache_read: metric(300, 300, 4), cache_write: metric(null, null, 0),
    output: metric(480, 480, 4), reasoning: metric(80, 80, 4), total: metric(codexTotal, codexTotal, plans.length),
  }, 4, null, [{requested_model: 'example-codex', actual_model: null, effort: 'medium', cli_version: '0.120.0'}]);
  const claude = usable(project, project.ids.sources.claude, 'claude-transcript-v1', 'reviewer', null, {
    input: metric(400, 400, 6), cache_read: metric(150, 150, 6), cache_write: metric(40, 40, 6),
    output: metric(220, 220, 6), reasoning: metric(null, null, 0), total: metric(claudeTotal, claudeTotal, plans.length),
  }, 6, 6, [{requested_model: 'example-claude', actual_model: 'example-claude', effort: 'medium', cli_version: '2.1.0'}]);
  const unavailable = {
    source_id: project.ids.sources.unavailable, adapter: 'codex-rollout-v1', role: 'investigator', host_id: project.ids.serverId,
    host_kind: 'central_local', status: 'source_unavailable', checked_at: iso(project.now, -2), successful_at: null,
    quota_pool: null, capacity: null, forecast: null, metrics: null, observations: null, configurations: [], calls: null,
    configurations_partial: false,
  };
  const body: Record<string, unknown> = {
    workspace_id: project.ids.workspaceId, as_of: iso(project.now), window: {since, until}, interval_s: 60,
    rows: [codex, claude, unavailable],
    quota_pools: [{
      pool_id: project.ids.poolId, limit_id: 'codex', identity_basis: 'operator_binding', sources: [project.ids.sources.codex],
      status: 'reported', snapshot: {observed_at: iso(project.now), limit_id: 'codex',
        windows: [{window: 'primary', used_percent: 40, window_minutes: 300, resets_at: unix(project.now, 1)}]},
    }, {
      pool_id: hexId('pool_', 2, 32), limit_id: 'notes', identity_basis: 'operator_binding', sources: [project.ids.sources.unavailable],
      status: 'unavailable', snapshot: null,
    }],
    capabilities: {version: 1, adapters: ['codex-rollout-v1', 'claude-transcript-v1'], collection: 'explicit_local_files',
      recurring: true, remote_collection: false, forecast: false, dispatch: false, buckets: {granularity: 'day'}},
    coverage: 'configured_local_files_only', forecast: {status: 'insufficient_evidence'},
  };
  if (url.searchParams.get('buckets') === 'day') {
    const tz = url.searchParams.get('tz') || 'UTC';
    body.buckets = {granularity: 'day', tz, series: [
      {source_id: project.ids.sources.codex, adapter: 'codex-rollout-v1', cli: 'codex', days: plans.map(day => dayRecord(day.date, day.codex, day.codexComplete))},
      {source_id: project.ids.sources.claude, adapter: 'claude-transcript-v1', cli: 'claude', days: plans.map(day => dayRecord(day.date, day.claude, day.claudeComplete))},
    ]};
  }
  return body;
}
