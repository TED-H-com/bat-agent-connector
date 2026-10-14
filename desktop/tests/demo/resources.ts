import {hexId, iso, unix, type DemoProject} from './project.ts';
import {zonedMidnight} from '../../src/pm-agents-trend.js';

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

function shiftDate(isoDate: string, delta: number) {
  const [y, m, d] = isoDate.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d + delta)).toISOString().slice(0, 10);
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

// Days follow the backend: a complete Codex total is input + output and is never null.
// A gap is an incomplete day. Today stays open. Yesterday is still settling.
function planDay(date: string, today: string) {
  const yesterday = shiftDate(today, -1);
  if (date === today) return {codex: 420, claude: 180, codexComplete: false, claudeComplete: false};
  if (date === yesterday) return {codex: 260, claude: 90, codexComplete: false, claudeComplete: false};
  if (date === '2026-10-10') return {codex: null, claude: 150, codexComplete: false, claudeComplete: true};
  if (date === '2026-10-09') return {codex: 0, claude: 40, codexComplete: true, claudeComplete: true};
  const day = Number(date.slice(-2)) || 0;
  return {codex: 100 + day, claude: 20 + day, codexComplete: true, claudeComplete: true};
}

function dayRecord(date: string, total: number | null, complete: boolean) {
  const settled = total != null && complete;
  return {date, complete: settled, tokens: {
    // A gap keeps component samples so a client that re-adds them cannot draw a bar.
    input: total == null ? 50 : total, cache_read: null, cache_write: null, output: total == null ? 25 : null, reasoning: null, total,
  }};
}

function codexMetrics(total: number) {
  const output = Math.round(total * 0.3);
  const input = total - output;
  const cache = Math.min(300, input);
  const reasoning = Math.min(80, output);
  return {
    input: metric(input, input, 6), cache_read: metric(cache, cache, 6), cache_write: metric(null, null, 0),
    output: metric(output, output, 6), reasoning: metric(reasoning, reasoning, 6), total: metric(total, total, 6),
  };
}

function claudeMetrics(total: number) {
  const cacheRead = Math.round(total * 0.2);
  const cacheWrite = Math.round(total * 0.08);
  const output = Math.round(total * 0.3);
  const input = total - cacheRead - cacheWrite - output;
  return {
    input: metric(input, input, 6), cache_read: metric(cacheRead, cacheRead, 6), cache_write: metric(cacheWrite, cacheWrite, 6),
    output: metric(output, output, 6), reasoning: metric(null, null, 0), total: metric(total, total, 6),
  };
}

function usable(project: DemoProject, sourceId: string, adapter: string, role: string, quota: string | null, metrics: object, observations: number, calls: number | null, configurations: object[], firstAt: string | null, lastAt: string | null) {
  const at = iso(project.now);
  return {
    source_id: sourceId, adapter, role, host_id: project.ids.serverId, host_kind: 'central_local', status: 'current',
    checked_at: at, successful_at: at, quota_pool: quota, capacity: null, forecast: null, metrics, observations,
    configurations, calls, first_at: firstAt, last_at: lastAt, configurations_partial: false,
  };
}

export function resources(project: DemoProject, url: URL) {
  const since = url.searchParams.get('since') || iso(project.now, -7);
  const until = url.searchParams.get('until') || iso(project.now);
  const tz = url.searchParams.get('tz') || 'UTC';
  const dates = localDates(since, until, tz);
  const sinceMs = Date.parse(since), untilMs = Date.parse(until);
  // The synthetic observations have absolute timestamps. A totals-only read
  // counts the same samples as a bucket read, regardless of its grouping zone.
  const sampleZone = 'Asia/Taipei';
  const today = localDate(new Date(project.now), sampleZone);
  const samples = localDates(since, until, sampleZone).map(date => ({
    at: zonedMidnight(date, sampleZone).getTime() + 9 * 60 * 60 * 1000,
    ...planDay(date, today),
  })).filter(sample => sample.at >= sinceMs && sample.at < untilMs);
  const sum = (key: 'codex' | 'claude') => samples.reduce((total, sample) => total + (typeof sample[key] === 'number' ? sample[key] : 0), 0);
  const plans = dates.map(date => {
    const daySamples = samples.filter(sample => localDate(new Date(sample.at), tz) === date);
    const value = (key: 'codex' | 'claude') => daySamples.some(sample => sample[key] == null) ? null
      : daySamples.reduce((total, sample) => total + (sample[key] || 0), 0);
    const covered = date < localDate(new Date(project.now - 86400000), tz);
    return {date, codex: daySamples.length || covered ? value('codex') : null,
      claude: daySamples.length || covered ? value('claude') : null,
      codexComplete: covered && daySamples.every(sample => sample.codexComplete),
      claudeComplete: covered && daySamples.every(sample => sample.claudeComplete)};
  });
  const firstAt = samples.length ? new Date(samples[0].at).toISOString() : null;
  const lastAt = samples.length ? new Date(samples[samples.length - 1].at).toISOString() : null;
  const codex = usable(project, project.ids.sources.codex, 'codex-rollout-v1', 'implementer', project.ids.poolId, codexMetrics(sum('codex')), 4, null,
    [{requested_model: 'example-codex', actual_model: null, effort: 'medium', cli_version: '0.120.0'}], firstAt, lastAt);
  const claude = usable(project, project.ids.sources.claude, 'claude-transcript-v1', 'reviewer', null, claudeMetrics(sum('claude')), 6, 6,
    [{requested_model: 'example-claude', actual_model: 'example-claude', effort: 'medium', cli_version: '2.1.0'}], firstAt, lastAt);
  // An unavailable source contributes no buckets and no counts, with or without the chart.
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
        windows: [
          {window: 'primary', used_percent: 40, window_minutes: 300, resets_at: unix(project.now, 1)},
          {window: 'weekly', used_percent: 12.345678, window_minutes: 10080, resets_at: unix(project.now, 2)},
        ]},
    }, {
      pool_id: hexId('pool_', 2, 32), limit_id: 'notes', identity_basis: 'operator_binding', sources: [project.ids.sources.unavailable],
      status: 'unavailable', snapshot: null,
    }],
    capabilities: {version: 1, adapters: ['codex-rollout-v1', 'claude-transcript-v1'], collection: 'explicit_local_files',
      recurring: true, remote_collection: false, forecast: false, dispatch: false, buckets: {granularity: 'day'}},
    coverage: 'configured_local_files_only', forecast: {status: 'insufficient_evidence'},
  };
  if (url.searchParams.get('buckets') === 'day') {
    body.buckets = {granularity: 'day', tz, series: [
      {source_id: project.ids.sources.codex, adapter: 'codex-rollout-v1', cli: 'codex', days: plans.map(day => dayRecord(day.date, day.codex, day.codexComplete))},
      {source_id: project.ids.sources.claude, adapter: 'claude-transcript-v1', cli: 'claude', days: plans.map(day => dayRecord(day.date, day.claude, day.claudeComplete))},
    ]};
  }
  return body;
}
