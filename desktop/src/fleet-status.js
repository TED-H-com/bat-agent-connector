import {displayFact, healthLabel} from './health-fact.js';

// Presentation of existing native evidence only. A check never starts work or
// changes trust, credentials, selection, ownership or a pending receipt.
const codeReason = {
  TAILSCALE_WAKING: 'waking', ROUTE_UNAVAILABLE: 'network', TUNNEL_DOWN: 'network',
  TLS_PIN_MISSING: 'trust', TLS_IDENTITY_MISMATCH: 'trust', TLS_FAILED: 'trust',
  CREDENTIAL_MISSING: 'identity', CREDENTIAL_CHANGED: 'identity', BAT_AUTH_REFUSED: 'identity',
  CONNECTOR_AUTH_REFUSED: 'identity', CONNECTOR_SCOPE_MISSING: 'identity',
  BAT_VERSION_UNSUPPORTED: 'version', CONNECTOR_CONTRACT_UNSUPPORTED: 'version',
  BAT_PROTOCOL_INVALID: 'service', CONNECTOR_UNAVAILABLE: 'service', CONNECTOR_CAPABILITY_MISSING: 'service',
  WORKSPACE_UNREADABLE: 'workspace', WORKSPACE_INVALID: 'workspace', WORKSPACE_BINDING_MISMATCH: 'workspace',
  INVENTORY_INVALID: 'configuration', OWNER_UNPROVEN: 'ownership', STOP_UNCONFIRMED: 'ownership',
  TUNNEL_START_UNSETTLED: 'ownership', TUNNEL_RECORD_CHANGED: 'ownership', OTHER_LOGIN_OWNER: 'ownership',
  ENDPOINT_IN_USE: 'local_endpoint', SSH_UNAVAILABLE: 'local_ssh',
  PROBE_PENDING: 'checking', PROBE_TIMEOUT: 'timeout', PROBE_STALE: 'stale',
};
const layerReason = {inventory: 'configuration', tunnel: 'network', tls: 'trust', auth: 'identity',
  bat: 'service', connector: 'service', contract: 'version', workspace: 'workspace', stale: 'stale'};
const layerStep = {tunnel: 'network', tls: 'identity', auth: 'identity', bat: 'service',
  connector: 'service', contract: 'service', workspace: 'workspace'};
const lookup = (table, key) => Object.hasOwn(table, key) ? table[key] : undefined;
const routeRequirement = {
  DIRECT_PEER_UNKNOWN: 'peer_unknown', DIRECT_NOT_CONFIRMED: 'direct_unconfirmed',
  DIRECT_TCP_UNREACHABLE: 'tcp_unconfirmed', CONFIGURED_ROUTES_EXHAUSTED: 'exhausted',
};

export function fleetDiagnosis(row, {snapshot, readable, kind = 'host'} = {}) {
  const layers = row?.layers || {};
  const reason = row?.code || row?.blocking;
  let state;
  // Unsaved checkboxes are not evidence. Cached rows cannot stay green after
  // failed reads, stopped monitoring or unapplied selection changes.
  if (!readable) state = 'unavailable';
  else if (reason === 'STOP_UNCONFIRMED') state = 'ownership';
  else if (row?.selected === false || row?.level === 'off') state = 'off';
  // An unsettled process outcome survives selection/shutdown transitions.
  else if (lookup(codeReason, reason) === 'ownership') state = 'ownership';
  else if (snapshot.monitor.state === 'stopped') state = 'stopped';
  else if (snapshot.monitor.state !== 'running' || snapshot.readiness.state === 'unavailable') state = 'checking';
  else if (snapshot.selection.applied_revision !== snapshot.selection.revision) state = 'selection_pending';
  else if (snapshot.readiness.state !== 'fresh'
    || row?.stale || row?.blocking === 'stale') state = 'stale';
  else if (!row || row.selected !== true) state = 'checking';
  else if (reason) state = lookup(codeReason, reason) || lookup(layerReason, row.blocking) || 'unknown';
  else if (row.level === 'ready' && layers.tunnel === true
    && (kind === 'connector' ? !['unknown', 'refused'].includes(layers.auth)
      : layers.tls === true && layers.auth === 'ok' && layers.bat === true && layers.workspace === true)) state = 'ready';
  else state = 'checking';

  const current = !['unavailable', 'configuration', 'off', 'stopped', 'stale', 'checking', 'selection_pending', 'unknown', 'ownership'].includes(state);
  // Malformed BAT protocol is a service failure even during authentication.
  const blockedAt = (reason === 'BAT_PROTOCOL_INVALID' ? 'service' : lookup(layerStep, row?.blocking)) || {network: 'network', waking: 'network', trust: 'identity',
    identity: 'identity', service: 'service', version: 'service', workspace: 'workspace',
    local_endpoint: 'network', local_ssh: 'network'}[state];
  const steps = [
    ['network', layers.tunnel === true],
    ['identity', layers.auth === 'ok' && (kind === 'connector' || layers.tls === true)],
    ['service', kind === 'connector' ? state === 'ready' : layers.bat === true],
    ...(kind === 'connector' ? [] : [['workspace', layers.workspace === true]]),
  ];
  let blocked = false;
  return {state, kind, steps: steps.map(([id, passed]) => {
    if (id === blockedAt) blocked = true;
    return {id, state: !current ? 'unchecked' : blocked
      ? id === blockedAt && !['waking', 'timeout'].includes(state) ? 'attention' : 'unchecked'
      : passed ? 'passed' : 'unchecked'};
  })};
}

export function rememberFleetDetails(content, detailsState) {
  // Read synchronously before replacement: the browser's queued toggle event
  // can arrive after a status refresh has detached the original disclosure.
  for (const details of content.querySelectorAll('details[data-fleet-details-key]')) {
    detailsState.set(details.dataset.fleetDetailsKey, details.open);
  }
}

// Native identity stays in this snapshot. No central host, address or workspace
// lookup can upgrade a native row. The original diagnosis owns reasons/steps;
// the shared fact projection owns currentness and its localized wording.
export function fleetHealth(row, options = {}) {
  const diagnosis = fleetDiagnosis(row, options);
  // A probe time cannot establish a value while the diagnosis is still unknown.
  const observedAt = ['checking', 'unknown'].includes(diagnosis.state) ? null : row?.observed_at;
  const fact = displayFact({key: ['checking', 'unknown', 'stale'].includes(diagnosis.state) ? 'health_unknown'
    : diagnosis.state === 'ready' ? 'ui_connected' : 'fleet_check_title_' + diagnosis.state,
    observed_at: observedAt, stale: diagnosis.state === 'stale'}, {now: options.now});
  const state = diagnosis.state === 'ready' && fact.freshness !== 'current'
    ? fact.freshness === 'stale' ? 'stale' : 'checking' : diagnosis.state;
  return {...diagnosis, state, fact, steps: fact.freshness === 'current' ? diagnosis.steps
    : diagnosis.steps.map(step => ({...step, state: 'unchecked'}))};
}

function diagnosisPresentation(row, {t, snapshot, readable, entry, now}) {
  const health = fleetHealth(row, {snapshot, readable, kind: entry.kind, now});
  const {state, kind} = health;
  const helpKey = ['ready', 'identity', 'service', 'version'].includes(state) ? `${state}_${kind === 'connector' ? 'connector' : 'bat'}` : state;
  return {...health, title: healthLabel(health.fact, t), help: t('fleet_check_help_' + helpKey)};
}

export function fleetStatus(row, {h, t, snapshot, readable, entry, detailsState, now = Date.now()}) {
  const {state, steps, fact, title, help} = diagnosisPresentation(row, {t, snapshot, readable, entry, now});
  const key = `${snapshot.configuration.binding}:machine:${entry.name}`;
  const details = h('details', {open: detailsState?.get(key) === true, 'data-fleet-details-key': key},
  h('summary', {'data-fleet-details': entry.name}, t('fleet_status_details')),
  h('dl', {class: 'fleet-checks'}, ...steps.flatMap(step => [
    h('dt', {}, t('fleet_check_' + step.id)),
    h('dd', {'data-fleet-check': step.id, 'data-check-state': step.state}, t('fleet_check_' + step.state)),
  ])),
  ...[row?.code, row?.blocking].filter((value, index, all) => value && all.indexOf(value) === index)
    .map(value => h('code', {}, value)));
  return h('div', {class: 'fleet-diagnosis', 'data-fleet-diagnosis': entry.name, 'data-fleet-state': state, 'data-freshness': fact.freshness},
    h('p', {class: 'fleet-diagnosis-title'}, title),
    h('p', {class: 'muted fleet-diagnosis-help'}, help), details,
    ...fleetRequirement(row, {h, t, snapshot, readable, entry}),
    ...fleetRoute(row, {h, t, snapshot, readable, entry, now}));
}

function fleetRequirement(row, {h, t, snapshot, readable, entry}) {
  const reason = lookup(routeRequirement, row?.route_requirement);
  if (!reason || !readable || row.selected !== true || snapshot.monitor.state !== 'running'
    || snapshot.readiness.state === 'unavailable'
    || snapshot.selection.applied_revision !== snapshot.selection.revision) return [];
  return [h('p', {class: 'muted', 'data-fleet-route-requirement': entry.name},
    t('fleet_route_requirement_' + reason))];
}

function paintDiagnosis(node, {state, steps, fact, title, help}, t) {
  const text = (selector, value) => {
    const target = node.querySelector(selector);
    if (target.textContent !== value) target.textContent = value;
  };
  node.dataset.fleetState = state;
  node.dataset.freshness = fact.freshness;
  text('.fleet-diagnosis-title', title);
  text('.fleet-diagnosis-help', help);
  for (const step of steps) {
    const selector = `[data-fleet-check="${step.id}"]`;
    node.querySelector(selector).dataset.checkState = step.state;
    text(selector, t('fleet_check_' + step.state));
  }
}

// Clock-only paint: retain every control, disclosure and message node. The
// status/action renders still own replacing snapshots and their full content.
export function paintFleetHealth(content, {t, snapshot, readable, now = Date.now()}) {
  const ready = new Map([...snapshot.readiness.hosts, snapshot.readiness.connector].filter(Boolean).map(row => [row.name, row]));
  const entries = new Map(snapshot.configuration.connections.map(entry => [entry.name, entry]));
  for (const node of content.querySelectorAll('[data-fleet-diagnosis]')) {
    const entry = entries.get(node.dataset.fleetDiagnosis);
    if (!entry) continue;
    const row = ready.get(entry.name), options = {t, snapshot, readable, entry, now};
    paintDiagnosis(node, diagnosisPresentation(row, options), t);
    // Presence depends only on the snapshot; its full render owns adding or
    // removing this independent fact. Clock ticks retain the existing node.
    const routeNode = node.querySelector('[data-fleet-route]');
    const route = routeNode && routePresentation(row, options);
    if (route) {
      routeNode.dataset.freshness = route.freshness;
      if (routeNode.textContent !== route.label) routeNode.textContent = route.label;
    }
  }
}

// Route is an independent secondary fact. It cannot change diagnosis or steps,
// and a down tunnel must not be accompanied by a reassuring last route.
function routePresentation(row, {t, snapshot, readable, now}) {
  const route = row?.route;
  if (!route || !['direct', 'lan', 'cf', 'configured'].includes(route.kind)
    || !readable || row.selected !== true || ['off', 'down'].includes(row.level)
    || snapshot.monitor.state !== 'running' || snapshot.readiness.state === 'unavailable'
    || snapshot.selection.applied_revision !== snapshot.selection.revision) return null;
  const fact = displayFact({key: 'fleet_route_' + route.kind, observed_at: route.observed_at}, {now});
  return {...fact, label: healthLabel(fact, t)};
}

function fleetRoute(row, {h, t, snapshot, readable, entry, now}) {
  const route = routePresentation(row, {t, snapshot, readable, now});
  return route ? [h('p', {class: 'muted', 'data-fleet-route': entry.name, 'data-freshness': route.freshness}, route.label)] : [];
}
