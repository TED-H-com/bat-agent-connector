import {iso, unix, type DemoProject} from './project.ts';

function metric(value: number | null, observed: number | null, covered: number) {
  return {value, observed, covered};
}

function usable(project: DemoProject, sourceId: string, adapter: string, role: string, quota: string | null, metrics: object, observations: number, calls: number | null, configurations: object[]) {
  const at = iso(project.now);
  return {
    source_id: sourceId, adapter, role, host_id: project.ids.serverId, host_kind: 'central_local', status: 'current',
    checked_at: at, successful_at: at, quota_pool: quota, capacity: null, forecast: null, metrics, observations,
    configurations, calls, first_at: iso(project.now, -10), last_at: iso(project.now, -1),
  };
}

export function resources(project: DemoProject, url: URL) {
  const since = url.searchParams.get('since') || iso(project.now, -7);
  const until = url.searchParams.get('until') || iso(project.now);
  const codex = usable(project, project.ids.sources.codex, 'codex-rollout-v1', 'implementer', project.ids.poolId, {
    input: metric(1200, 1200, 4), cache_read: metric(300, 300, 4), cache_write: metric(null, null, 0),
    output: metric(480, 480, 4), reasoning: metric(80, 80, 4), total: metric(1680, 1680, 4),
  }, 4, null, [{requested_model: 'example-codex', actual_model: null, effort: 'medium'}]);
  const claude = usable(project, project.ids.sources.claude, 'claude-transcript-v1', 'reviewer', null, {
    input: metric(400, 400, 6), cache_read: metric(150, 150, 6), cache_write: metric(40, 40, 6),
    output: metric(220, 220, 6), reasoning: metric(null, null, 0), total: metric(810, 810, 6),
  }, 6, 6, [{requested_model: 'example-claude', actual_model: 'example-claude', effort: 'medium'}]);
  const unavailable = {
    source_id: project.ids.sources.unavailable, adapter: 'codex-rollout-v1', role: 'investigator', host_id: project.ids.serverId,
    host_kind: 'central_local', status: 'source_unavailable', checked_at: iso(project.now, -2), successful_at: null,
    quota_pool: null, capacity: null, forecast: null, metrics: null, observations: null, configurations: [], calls: null,
  };
  return {
    workspace_id: project.ids.workspaceId, as_of: iso(project.now), window: {since, until}, interval_s: 3600,
    rows: [codex, claude, unavailable],
    quota_pools: [{
      pool_id: project.ids.poolId, limit_id: 'codex', identity_basis: 'operator_binding', sources: [project.ids.sources.codex],
      status: 'reported', snapshot: {observed_at: iso(project.now), limit_id: 'codex',
        windows: [{window: 'primary', used_percent: 40, window_minutes: 300, resets_at: unix(project.now, 1)}]},
    }],
    capabilities: {version: 1, adapters: ['codex-rollout-v1', 'claude-transcript-v1'], collection: 'explicit_local_files',
      recurring: true, remote_collection: false, forecast: false, dispatch: false},
    coverage: 'configured_local_files_only', forecast: {status: 'insufficient_evidence'},
  };
}
