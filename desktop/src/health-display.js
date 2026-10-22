import {centralServiceStatus} from './central-service-status.js';
import {sessionFacets} from './state/sessions.js';

const time = value => typeof value === 'number' && Number.isFinite(value) ? value
  : typeof value === 'string' && value ? Date.parse(value) : NaN;
const revision = caps => JSON.stringify([caps?.connector, caps?.build?.version, caps?.build?.commit,
  caps?.build?.source, caps?.contract_version]);

// Display evidence only. Values, identity and producer deadlines survive expiry;
// neither this projection nor its clock participates in an action gate.
export function displayFact(fact = {}, {now = Date.now(), bootstrap, version} = {}) {
  const observed = time(fact.observed_at), deadline = time(fact.valid_until);
  const ageSeconds = Number.isFinite(observed) ? Math.max(0, Math.floor((now - observed) / 1000)) : null;
  const invalidated = fact.static && (fact.bootstrap !== bootstrap || fact.version !== version);
  const freshness = invalidated ? 'invalidated' : fact.static ? 'current'
    : fact.stale ? 'stale' : Number.isFinite(deadline) ? now >= deadline ? 'stale' : 'current'
    : !Number.isFinite(observed) ? 'unknown' : now - observed >= 60_000 ? 'stale' : 'current';
  return {...fact, freshness, ageSeconds};
}

export function compactHealth({attention, stale, unknown, activity}) {
  return attention ? 'attention' : stale ? 'stale' : unknown || activity === 'unknown' ? 'unknown'
    : activity === 'working' ? 'working' : 'idle';
}

function runtimeSummary(selected, sessions, complete, now) {
  const rows = selected ? sessions.filter(row => row.host === selected.host &&
    (selected.workspaceId ? row.workspace_id === selected.workspaceId : row.session_id === selected.sessionId)) : [];
  if (!selected) return null;
  const facts = rows.map(row => {
    const facets = sessionFacets(row);
    const evidence = row.state?.evidence?.activity;
    const activity = displayFact({value: row.state?.activity ?? facets.activity,
      observed_at: evidence ? evidence.observed_at : row.observed_at,
      valid_until: evidence?.valid_until,
      stale: Object.values(facets.staleness).some(Boolean)}, {now});
    return {attention: facets.attention !== 'none' || row.api_access === 'managed' &&
      ['blocked', 'failed', 'error', 'needs_attention', 'awaiting_approval'].includes(row.runtime_status), activity,
      working: ['streaming', 'starting'].includes(activity.value),
      idle: ['not_streaming', 'ended'].includes(activity.value)};
  });
  const connections = rows.filter(row => row.state?.connection).map(row => displayFact({value: row.state.connection,
    ...row.state.evidence?.connection}, {now}));
  const connection = connections.sort((a, b) => (time(b.observed_at) || 0) - (time(a.observed_at) || 0))[0] || null;
  const attention = facts.some(row => row.attention) || connection?.value === 'not_connected';
  const stale = facts.some(row => row.activity.freshness === 'stale') || connection?.freshness === 'stale';
  const unknown = !facts.length || Boolean(selected.workspaceId && !complete) || facts.some(row =>
    row.activity.freshness === 'unknown' || !row.idle && !row.working) || connection?.freshness === 'unknown';
  const activity = facts.some(row => row.working && row.activity.freshness === 'current') ? 'working'
    : !facts.length || selected.workspaceId && !complete || facts.some(row => !row.idle || row.activity.freshness !== 'current') ? 'unknown' : 'idle';
  return {selected, attention, stale, unknown, activity, connection, compact: compactHealth({attention, stale, unknown, activity}), facts};
}

function otherMachines(health, now) {
  // The two inventories have different identity domains. Prefer central inventory
  // when loaded; never deduplicate native aliases against central host names.
  if (health.hostsLoaded) {
    const rows = health.hosts.filter(row => row.host !== health.selected?.host &&
      (row.error || row.reachable === false && row.last_attempt_at));
    return countFact(rows.map(row => ({observed_at: row.last_attempt_at, stale: row.stale})), now);
  }
  // No producer binds a native alias to this selected central host. Omit an
  // ambiguous count rather than counting the selected machine as "other".
  const rows = health.selected ? [] : health.fleet?.readiness?.hosts || [];
  return countFact(rows.filter(row => row.selected === true && row.level !== 'off' && row.code &&
    !['PROBE_PENDING', 'PROBE_STALE', 'TAILSCALE_WAKING'].includes(row.code) &&
    displayFact({observed_at: row.observed_at}, {now}).freshness !== 'unknown')
    .map(row => ({observed_at: row.observed_at, stale: row.stale || health.fleet.readiness.state !== 'fresh'})), now);
}

function countFact(rows, now) {
  const observed = rows.map(row => time(row.observed_at));
  return displayFact({value: rows.length, observed_at: observed.length ? Math.min(...observed) : null,
    stale: rows.some(row => row.stale)}, {now});
}

export function healthDisplay(input, now = Date.now()) {
  const health = input.health || {};
  const status = centralServiceStatus(input);
  const pollAt = typeof input.pollHealth === 'object' ? input.pollHealth?.at
    : input.pollHealth === 'connected' && input.lastPoll?.namespace === input.namespace ? input.lastPoll.at
    : health.bootstrapAt;
  const connection = {...status, ...displayFact({value: status.key,
    observed_at: status.recoveryKey ? input.managedObservedAt : pollAt,
    valid_until: typeof input.pollHealth === 'object' ? input.pollHealth?.valid_until : input.lastPoll?.valid_until}, {now})};
  // No credential is a configuration state, not an aged reachability observation.
  if (!input.token && !status.recoveryKey) connection.freshness = 'unconfigured';
  const receipt = health.capabilities;
  const capabilities = displayFact({value: receipt?.data, static: true,
    bootstrap: receipt?.bootstrap, version: revision(receipt?.data)}, {now, bootstrap: health.bootstrap, version: revision(health.currentCaps || input.caps)});
  if (!receipt) capabilities.freshness = 'unknown';
  const other = otherMachines(health, now);
  return {connection, capabilities, summary: runtimeSummary(health.selected, health.sessions || [], health.sessionsComplete, now),
    otherAttention: other.value, other};
}

// Reuse reads the client already performs. Receipt time never refreshes an
// inventory observation, and these in-memory facts never cross a bootstrap.
export function rememberHealthRead(health, path, data) {
  const pathname = path.split('?')[0];
  if (Array.isArray(data.hosts) && (pathname === '/hosts' || pathname === '/sessions')) {
    health.hosts = data.hosts; health.hostsLoaded = true;
  }
  if (/^\/sessions(?:\/[^/]+\/[^/]+)?$/.test(pathname)) {
    const rows = data.sessions || (data.session ? [data.session] : []);
    const params = new URLSearchParams(path.split('?')[1] || '');
    const complete = Array.isArray(data.sessions) && data.next_cursor === null && [...params.keys()].every(key => key === 'limit');
    if (Array.isArray(data.sessions)) health.sessionsComplete = complete;
    const sessions = new Map((complete ? [] : health.sessions || []).map(row => [`${row.host}/${row.session_id}`, row]));
    for (const row of rows) sessions.set(`${row.host}/${row.session_id}`, row);
    health.sessions = [...sessions.values()].slice(-500);
    const selected = health.selected;
    const row = selected && health.sessions.find(row => row.host === selected.host && row.session_id === selected.sessionId);
    if (row) health.selected = {...selected, workspaceId: row.workspace_id || undefined, label: row.workspace || row.host};
  }
}

export function healthLabel(fact, t) {
  const label = t(fact.key || fact.value);
  if (fact.freshness === 'stale') return t('health_stale_value', {value: label,
    age: fact.ageSeconds === null ? t('health_age_unknown') : t('health_age', {seconds: fact.ageSeconds}), checking: t('health_checking')});
  if (['unknown', 'invalidated'].includes(fact.freshness) && fact.key !== 'ui_connecting')
    return t('health_unknown_value', {value: label, checking: t('health_checking')});
  return label;
}
