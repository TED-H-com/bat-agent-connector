import {displayFact} from './health-fact.js';
export {displayFact, healthAge, healthLabel} from './health-fact.js';
import {centralServiceStatus} from './central-service-status.js';
import {sessionFacets} from './state/sessions.js';
import {fleetDiagnosis} from './fleet-status.js';

const time = value => typeof value === 'number' && Number.isFinite(value) ? value
  : typeof value === 'string' && value ? Date.parse(value) : NaN;
export const healthRevision = caps => JSON.stringify([caps?.connector, caps?.build?.version, caps?.build?.commit,
  caps?.build?.source, caps?.contract_version]);
const rowId = row => JSON.stringify([row.host, row.session_id]);
const gone = row => Boolean(row.gone_at) || row.state?.enumeration === 'gone';
const selectedRows = health => (health.sessions || []).filter(row => !gone(row) && row.host === health.selected?.host &&
  (health.selected.workspaceId ? row.workspace_id === health.selected.workspaceId : row.session_id === health.selected.sessionId));

export function compactHealth({attention, stale, unknown, activity}) {
  return attention ? 'attention' : stale ? 'stale' : unknown || activity === 'unknown' ? 'unknown'
    : activity === 'working' ? 'working' : 'idle';
}

function runtimeSummary(health, now) {
  const selected = health.selected;
  if (!selected) return null;
  const rows = selectedRows(health);
  const complete = health.sessionsComplete || health.completeHosts?.has(selected.host);
  const facts = rows.map(row => {
    const facets = sessionFacets(row), evidence = row.state?.evidence?.activity;
    const activity = displayFact({value: row.state?.activity ?? facets.activity,
      observed_at: evidence ? evidence.observed_at : row.observed_at, valid_until: evidence?.valid_until,
      error: health.readErrors?.get(health.rowSources?.get(rowId(row))),
      stale: Object.values(facets.staleness).some(Boolean)}, {now});
    return {attention: facets.attention !== 'none' || row.api_access === 'managed' &&
      ['blocked', 'failed', 'error', 'needs_attention', 'awaiting_approval'].includes(row.runtime_status), activity,
      working: ['streaming', 'starting'].includes(activity.value),
      idle: ['not_streaming', 'ended'].includes(activity.value)};
  });
  const connections = rows.filter(row => row.state?.connection).map(row => displayFact({value: row.state.connection,
    ...row.state.evidence?.connection}, {now}));
  const connection = connections.sort((a, b) => (time(b.observed_at) || 0) - (time(a.observed_at) || 0))[0] || null;
  const attention = facts.some(row => row.attention);
  const stale = facts.some(row => row.activity.freshness === 'stale') || connection?.freshness === 'stale';
  const unknown = !facts.length || Boolean(selected.workspaceId && !complete) || facts.some(row =>
    row.activity.freshness === 'unknown' || !row.idle && !row.working) || connection?.freshness === 'unknown';
  const activity = facts.some(row => row.working && row.activity.freshness === 'current') ? 'working'
    : !facts.length || selected.workspaceId && !complete || facts.some(row => !row.idle || row.activity.freshness !== 'current') ? 'unknown' : 'idle';
  return {selected, attention, stale, unknown, activity, connection, compact: compactHealth({attention, stale, unknown, activity}), facts};
}

function countFact(rows, now, href = null) {
  const facts = rows.map(row => displayFact(row, {now}));
  return {...displayFact({value: rows.length, observed_at: rows.length ? Math.min(...rows.map(row => time(row.observed_at))) : null,
    stale: facts.some(row => row.freshness === 'stale')}, {now}), href,
    // Each producer's deadline has already been evaluated above.
    stale_reason: rows.length && rows.every(row => row.stale_reason === 'host_unreachable') ? 'host_unreachable' : null,
    freshness: facts.some(row => row.freshness === 'stale') ? 'stale' : facts.some(row => row.freshness === 'unknown') ? 'unknown' : 'current'};
}

function otherMachines(health, now) {
  if (health.hostsLoaded) {
    const rows = (health.hosts || []).filter(row => row.host !== health.selected?.host &&
      (row.error || row.reachable === false && row.last_attempt_at));
    // No existing Fleet view lists central Inventory host identities.
    return countFact(rows.map(row => ({observed_at: row.last_attempt_at, valid_until: row.valid_until, stale: row.stale, stale_reason: row.stale_reason})), now);
  }
  const snapshot = health.fleet;
  const rows = health.selected ? [] : snapshot?.readiness?.hosts || [];
  return countFact(rows.filter(row => {
    const diagnosis = fleetDiagnosis(row, {snapshot, readable: true});
    return diagnosis.steps.some(step => step.state === 'attention');
  }).map(row => ({observed_at: row.observed_at, stale: row.stale})), now, '#/settings?section=fleet');
}

export function healthDisplay(input, now = Date.now()) {
  const health = input.health || {}, status = centralServiceStatus(input);
  const pollAt = typeof input.pollHealth === 'object' ? input.pollHealth?.at
    : input.pollHealth === 'connected' && input.lastPoll?.namespace === input.namespace ? input.lastPoll.at : health.bootstrapAt;
  const connection = {...status, ...displayFact({value: status.key,
    observed_at: status.recoveryKey ? input.managedObservedAt : pollAt,
    valid_until: typeof input.pollHealth === 'object' ? input.pollHealth?.valid_until : input.lastPoll?.valid_until}, {now})};
  if (!input.token && !status.recoveryKey) connection.freshness = 'unconfigured';
  const receipt = health.capabilities;
  const capabilities = displayFact({value: receipt?.data, static: true,
    bootstrap: receipt?.bootstrap, version: healthRevision(receipt?.data)}, {now, bootstrap: health.bootstrap, version: healthRevision(health.currentCaps || input.caps)});
  if (!receipt) capabilities.freshness = 'unknown';
  capabilities.refresh = health.capabilityRefresh;
  const other = otherMachines(health, now);
  return {connection, capabilities, summary: runtimeSummary(health, now), otherAttention: other.value, other};
}

export function selectHealthSession(health, host, sessionId) {
  if (health.selected?.host !== host || health.selected?.sessionId !== sessionId) health.selected = {host, sessionId};
  const row = health.sessions?.find(row => row.host === host && row.session_id === sessionId);
  if (row) health.selected = {...health.selected, workspaceId: row.workspace_id || undefined, label: row.workspace || row.host};
}

export function resetHealthBootstrap(previous = {}, {sameScope, caps, now = Date.now()}) {
  previous ||= {};
  const bootstrap = (previous.bootstrap || 0) + 1;
  const result = {bootstrap, bootstrapAt: now, capabilities: {data: caps, bootstrap}, currentCaps: caps,
    selected: sameScope ? previous.selected : null, sessions: [], hosts: [], fleet: previous.fleet};
  if (sameScope) Object.assign(result, {
    sessions: (previous.sessions || []).map(row => ({...row, stale: true})),
    hosts: (previous.hosts || []).map(row => ({...row, stale: true})), hostsLoaded: previous.hostsLoaded,
    rowSources: new Map(previous.rowSources), hostSources: new Map(previous.hostSources),
    readers: new Map(previous.readers), bootstrapRenewal: true,
  });
  return result;
}

// Cache source reads, not receipt timestamps. A complete chain may be global or
// host-only; pagination controls do not filter, attention/orchestration do.
export function rememberHealthRead(health, path, data) {
  if (!data || typeof data !== 'object') return;
  path = path.replace(/\?$/, '');
  const [pathname, query = ''] = path.split('?'), params = new URLSearchParams(query);
  health.readErrors?.delete(path);
  if (Array.isArray(data.hosts) && ['/hosts', '/sessions'].includes(pathname)) {
    const full = pathname === '/sessions' || ![...params.keys()].some(key => key !== 'limit');
    const hosts = new Map((full ? [] : health.hosts || []).map(row => [row.host, row]));
    health.hostSources ||= new Map();
    for (const row of data.hosts) {hosts.set(row.host, row); health.hostSources.set(row.host, path);}
    health.hosts = [...hosts.values()];
    if (full) health.hostsLoaded = true;
  }
  if (!/^\/sessions(?:\/[^/]+\/[^/]+)?$/.test(pathname)) return;
  const rows = Array.isArray(data.sessions) ? data.sessions : data.session ? [data.session] : [];
  const sessions = new Map((health.sessions || []).map(row => [rowId(row), row]));
  health.rowSources ||= new Map();
  for (const row of rows) {sessions.set(rowId(row), row); health.rowSources.set(rowId(row), path);}
  if (Array.isArray(data.sessions) && [...params.keys()].every(key => ['order', 'include_gone', 'limit', 'cursor', 'host'].includes(key))) {
    health.chains ||= new Map(); health.completeHosts ||= new Set();
    const chainParams = new URLSearchParams(params); chainParams.delete('cursor'); chainParams.sort();
    const key = chainParams.toString(), scope = params.get('host');
    let chain = health.chains.get(key);
    if (!params.has('cursor')) chain = {next: null, ids: new Set()};
    if (chain && (!params.has('cursor') || params.get('cursor') === chain.next)) {
      for (const row of rows) chain.ids.add(rowId(row));
      chain.next = data.next_cursor;
      health.chains.set(key, chain);
      if (data.next_cursor === null) {
        for (const [id, row] of sessions) if ((!scope || row.host === scope) && !chain.ids.has(id)) sessions.delete(id);
        if (scope) {
          if (chain.ids.size <= 500) health.completeHosts.add(scope); else health.completeHosts.delete(scope);
        } else health.sessionsComplete = chain.ids.size <= 500;
      }
    }
  }
  const all = [...sessions.values()];
  // Pin the selected row. Eviction must withdraw completeness for affected hosts.
  const selected = all.find(row => row.host === health.selected?.host && row.session_id === health.selected?.sessionId);
  const kept = all.filter(row => row !== selected).slice(selected ? -499 : -500);
  if (selected) kept.push(selected);
  if (all.length > kept.length) {
    health.sessionsComplete = false;
    const ids = new Set(kept.map(rowId));
    for (const row of all) if (!ids.has(rowId(row))) {health.completeHosts?.delete(row.host); health.rowSources.delete(rowId(row));}
  }
  health.sessions = kept;
  const retained = new Set(kept.map(rowId));
  for (const id of health.rowSources.keys()) if (!retained.has(id)) health.rowSources.delete(id);
  if (health.selected) selectHealthSession(health, health.selected.host, health.selected.sessionId);
}

export function withdrawHealthRead(health, method, path, error) {
  path = path.replace(/\?$/, '');
  const pathname = path.split('?')[0], inventoryRead = method === 'GET' && /^\/(hosts|sessions)(?:\/|$)/.test(pathname);
  if (error.status === 401 || error.status === 403 && inventoryRead) {
    Object.assign(health, {sessions: [], hosts: [], hostsLoaded: false, sessionsComplete: false,
      inventoryRevision: (health.inventoryRevision || 0) + 1, readers: new Map(),
      completeHosts: new Set(), chains: new Map(), rowSources: new Map(), hostSources: new Map(), readErrors: new Map()});
  } else if (error.status === 404 && method === 'GET' && health.selected && pathname ===
    `/sessions/${encodeURIComponent(health.selected.host)}/${encodeURIComponent(health.selected.sessionId)}`) {
    health.sessions = (health.sessions || []).filter(row => row.host !== health.selected.host || row.session_id !== health.selected.sessionId);
  } else if (inventoryRead) {
    health.readErrors ||= new Map(); health.readErrors.set(path, {status: error.status, code: error.code});
  }
}

// One reread of each already-used source at its producer deadline. Failed or
// unchanged observations never start a retry loop. New validity allows renewal.
export function takeHealthRenewal(health, now = Date.now()) {
  const sources = new Map();
  const add = (path, deadline) => {
    const at = time(deadline);
    if (path && Number.isFinite(at)) sources.set(path, Math.min(sources.get(path) ?? Infinity, at));
  };
  for (const row of selectedRows(health)) {
    const path = health.rowSources?.get(rowId(row));
    add(path, row.state?.evidence?.activity?.valid_until);
    add(path, row.state?.evidence?.connection?.valid_until);
  }
  for (const row of health.hosts || []) add(health.hostSources?.get(row.host), row.valid_until);
  health.renewed ||= new Map();
  for (const [path, deadline] of sources) {
    const key = `${health.bootstrap || 0}:${deadline}`;
    if ((now >= deadline || health.bootstrapRenewal) && health.renewed.get(path) !== key) {
      health.renewed.set(path, key); return path;
    }
  }
  health.bootstrapRenewal = false;
  return null;
}
