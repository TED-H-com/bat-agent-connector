import {runtimeStale} from './state/sessions.js';
import {versionProjection, projectionBuild, version, commit, time} from './version-projection.js';
// An explicit projection. Never spread server/client objects into an export.
const code = value => typeof value === 'string' && /^[A-Z][A-Z0-9_]{0,79}$/.test(value) ? value : null;
const reason = value => typeof value === 'string' && /^[a-z][a-z0-9_]{0,79}$/.test(value) ? value : null;
const action = value => typeof value === 'string' && /^(?:session|checkpoint|integration|artifact|repository|project|work_item|task|delivery|deployment|setup|pm)[._][a-z_.]+$/.test(value) ? value : null;
const protocols = ['bat-remote/v2','bat-remote/legacy-v1'];
const contractVersion = value => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(value)) ? value : null;
const bool = value => typeof value === 'boolean' ? value : null;
const operationId = value => typeof value === 'string' && /^op_[0-9a-f]{32}$/.test(value) ? value : null;
const select = (value, values) => values.includes(value) ? value : null;
const routeWords = new Set(('v1 hosts sessions projects work-items tasks operations capabilities bootstrap events managed setup secrets observation status pending attention reading permissions labels checkpoints checkpoint-preview instructions artifacts uploads content delivery receipts discovery pm workspaces plans threads messages snapshots brief-export resources roadmap dependencies registers governance').split(' '));
const resourceNames = {hosts:'host',projects:'project','work-items':'work_item',tasks:'task',operations:'operation',workspaces:'workspace',threads:'thread',snapshots:'snapshot',artifacts:'artifact',uploads:'operation',checkpoints:'checkpoint'};
export function pathTemplate(path) {
  const parts = String(path).replace(/^\/api(?=\/v1)/, '').split(/[?#]/)[0].split('/').filter(Boolean);
  if (parts[0] !== 'v1') parts.unshift('v1');
  return '/' + parts.map((part, i) => {
    // The current sessions API uses /sessions/:host/:session; also support nested host routes.
    const si = parts.indexOf('sessions');
    if (si >= 0 && i === si + 1) return parts[si - 2] === 'hosts' ? ':session' : ':host';
    if (si >= 0 && i === si + 2 && parts[si - 2] !== 'hosts') return ':session';
    if (resourceNames[parts[i - 1]]) return ':' + resourceNames[parts[i - 1]];
    return routeWords.has(part) ? part : ':id';
  }).join('/');
}
export function failedCall({at, method, path, status, code: errorCode, admissionRefused, operation}) {
  return {at: time(at), method: select(method, ['GET','POST','PUT','PATCH','DELETE','HEAD']) || 'GET',
    path_template: pathTemplate(path), status: Number.isInteger(status) && status >= 100 && status <= 599 ? status : null,
    code: code(errorCode) || (status>=100?'HTTP_ERROR':'NETWORK_ERROR'), admission_refused: admissionRefused === true,
    operation_id: operationId(operation?.operation_id)};
}
export function errorRing(limit = 50) {
  const entries = [];
  return {record(input) {entries.push(failedCall(input)); entries.splice(0, Math.max(0, entries.length - Math.min(50, limit)));},
    entries: () => entries.map(entry => ({...entry})), clear() {entries.length = 0;}};
}
export function clientIdentity(input = {}, projection = versionProjection(input)) {
  const {caps, desktop = false, os = 'unknown', locale = 'en', width = 0} = input;
  const build = projectionBuild(projection);
  return {...build, api_version: Number.isInteger(caps?.api_version) ? caps.api_version : null,
    contract_version: contractVersion(caps?.contract_version),
    platform: desktop ? 'desktop' : 'browser', os_family: select(os,['windows','macos','linux']) || 'unknown',
    locale: locale === 'zh-TW' ? 'zh-TW' : 'en', viewport_class: width > 0 ? width < 800 ? 'small' : width < 1200 ? 'medium' : 'large' : 'unknown'};
}
export function buildLine(projection, t) {
  const client=projection.rows ? projectionBuild(projection) : projection;
  const unknown = t('diag_unknown');
  const frontend = client.platform !== 'desktop' || client.frontend_digest
    ? t('diag_frontend_build', {digest: client.frontend_digest?.slice(0,12) || unknown}) : null;
  const connector = t('diag_connector_build', {version: client.connector_version || unknown, commit: client.connector_commit?.slice(0,7) || unknown, source: client.connector_source || unknown});
  const desktop = client.platform === 'desktop' ? t('diag_desktop_build', {version: client.app_version || unknown, commit: client.app_commit?.slice(0,7) || unknown}) : null;
  const line = [desktop, frontend, connector].filter(Boolean).join(' · ');
  return client.platform === 'browser' && client.frontend_digest && client.served_frontend_digest && client.frontend_digest !== client.served_frontend_digest
    ? line + ' · ' + t('diag_frontend_stale') : line;
}
const APP = 'io.betteragent.dashboard';
export function logLocations(os, managed) {
  const root = managed ? {linux:`~/.local/share/${APP}/central`,macos:`~/Library/Application Support/${APP}/central`,windows:`%LOCALAPPDATA%\\${APP}\\central`}[os]
    : {linux:'~/.local/state/bat-agent-connector',macos:'~/.local/state/bat-agent-connector',windows:'~\\.local\\state\\bat-agent-connector'}[os];
  return {state_database: root ? root + (os === 'windows' ? '\\' : '/') + (managed ? (os === 'windows' ? 'state\\' : 'state/') : '') + 'tasks.sqlite3' : null,
    runtime_stderr: managed ? 'not_persisted_by_desktop' : 'external_service_configuration', location: 'default_template_only'};
}
const databaseTemplates = managed => Object.fromEntries(['linux','macos','windows'].map(os => [os,logLocations(os,managed).state_database]));
export function scrubDiagnostics(text) {
  // Parse the serialized JSON so values can be replaced in full, never partially.
  // Only module-owned formats/templates survive this independent final gate.
  const safeLogs = new Set(['linux','macos','windows'].flatMap(os => [false,true].map(managed => logLocations(os,managed).state_database)));
  return JSON.stringify(JSON.parse(text, (_key, input) => {
    if(typeof input !== 'string')return input;
    let value = input;
    try {value=decodeURIComponent(value);} catch { /* Malformed escapes still face the format gate. */ }
    const safeRoute = /^\/v1(?:\/(?:[a-z-]+|:[a-z_]+))*$/.test(value) && value.split('/').filter(Boolean).every(x => x.startsWith(':') || routeWords.has(x));
    if(safeRoute || safeLogs.has(value) || time(value) || contractVersion(value) || protocols.includes(value))return value;
    value = value.replace(/(?:authorization\s*:\s*(?:basic|bearer)\s+\S+|bearer\s+\S+|batc_[\w-]+|gh[pousr]_[\w-]+|github_pat_[\w-]+|sk-[\w-]+|(?:token|secret|password|passwd|api[_-]?key|fp)\s*[=:]\s*\S+)/gi, '[redacted]')
      .replace(/SHA256:[^\s"',;]+/gi, '[redacted]')
      .replace(/\b[a-z][a-z0-9+.-]*:\/\/[^\s"<>]+/gi, '[redacted]')
      .replace(/\b[\w.+-]+@[\w.-]+\b/gi, '[redacted]')
      .replace(/\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|net|org|io|dev|app|internal|local|lan|corp|example|invalid)\b(?::\d+)?(?:[/?#]\S*)?/gi, '[redacted]')
      .replace(/\b(?:\d{1,3}\.){3}\d{1,3}\b/g, '[redacted]')
      .replace(/(?:\b(?:[a-f0-9]{1,4}:){2,}[a-f0-9:]+\b|\[?[a-f0-9]*::[a-f0-9:]*\]?)/gi, '[redacted]')
      .replace(/(?:[a-z]:[\\/]|\\\\|~(?:[\w.-]+)?[\\/]|\/)[^\r\n]*/gi, '[redacted]');
    return code(value) || reason(value) || version(value) || commit(value) || operationId(value) || action(value)
      || /^host-[1-9]\d*$/.test(value) || ['batc.diagnostics.v1','zh-TW'].includes(value) ? value : '[redacted]';
  }));
}
export function sessionFacts(session) {
  return {loaded: bool(session.loaded), streaming: bool(session.streaming),
    unknown_activity: ![true,false].includes(session.streaming), stale: runtimeStale(session) ? true
      : [session.stale, session.fields_stale, session.state?.evidence?.activity?.stale].some(value => typeof value === 'boolean') ? false : null,
    pending: session.pending !== undefined ? session.pending != null : bool(session.pending_interaction),
    history_reasons: Array.isArray(session.reading?.incomplete_reasons) ? [...new Set(session.reading.incomplete_reasons.map(reason).filter(Boolean))] : null};
}
// One classification for every source; read/decision time is client evidence.
export function diagnosticsSource(read) {
  const result = {status:'not_attempted', http_status:null, code:null, read_at:time(read?.at)};
  if (!read || read.attempted === false) return result;
  if (read.offered === false) return {...result, status:'not_offered'};
  if (read.permitted === false) return {...result, status:'not_permitted'};
  if (read.error) {
    const status = read.error.status;
    const http = Number.isInteger(status) && status >= 100 && status <= 599 ? status : null;
    return {...result, status:'failed', http_status:http, code:code(read.error.code) || (http ? 'HTTP_ERROR' : 'NETWORK_ERROR')};
  }
  if (read.discarded) return {...result, status:'failed', code:'SOURCE_ACCESS_CHANGED'};
  return read.completed === true ? {...result, status:'available'} : result;
}
export function buildDiagnostics(input) {
  const hosts = Array.isArray(input.hosts) ? [...input.hosts].sort((a,b) => String(a.host).localeCompare(String(b.host), 'en')) : [];
  const flags = (source, keys) => Object.fromEntries(keys.map(key => [key, bool(source?.[key])]));
  const sessions = Array.isArray(input.sessions) ? input.sessions.map(sessionFacts) : null;
  const count = key => sessions?.length === 0 ? 0 : sessions?.some(s => typeof s[key] === 'boolean') ? sessions.filter(s => s[key] === true).length : null;
  const history = sessions?.some(s => s.history_reasons !== null) ? Object.create(null) : null;
  if (history) for (const session of sessions) for (const why of session.history_reasons || []) history[why] = (history[why] || 0) + 1;
  const sourceReads = Object.fromEntries(['capabilities','hosts','operations','managed_setup','observation'].map(key => [key,
    diagnosticsSource(input.sourceReads?.[key])]));
  // Additive v1 compatibility: old consumers retain their binary source map.
  const sources = Object.fromEntries(Object.entries(sourceReads).map(([key, read]) => [key,
    read.status === 'available' ? 'available' : 'not_available']));
  const versions=versionProjection(input);
  const bundle = {schema:'batc.diagnostics.v1', exported_at: time(input.exportedAt),
    client: clientIdentity(input,versions), versions, sources, source_reads:sourceReads,
    hosts: hosts.map((host,i) => {
      const policy = input.caps?.hosts?.find(h => h.host === host.host) || {};
      const policyFlags = flags(host.policy || policy, ['enabled','writes','orchestrate','shared_clone_worktrees','managed_roots_configured','ssh_configured','checkpoint_available']);
      if(policyFlags.managed_roots_configured === null && Array.isArray(policy.managed_roots)) policyFlags.managed_roots_configured = policy.managed_roots.length > 0;
      const setupHost = Array.isArray(input.setup?.hosts) ? input.setup.hosts.find(item => item.name === host.host) : null;
      if(policyFlags.ssh_configured === null && setupHost && (typeof setupHost.ssh_alias === 'string' || setupHost.ssh_alias === null)) policyFlags.ssh_configured = Boolean(setupHost.ssh_alias);
      const discovery = Array.isArray(host.discovery) && policy.profile_id != null ? host.discovery.find(scan => scan.profile_id === policy.profile_id) : null;
      return {alias:`host-${i+1}`, connection_state: select(host.connection_state, ['connected','disconnected','reconnecting','never_connected','stale']) || (host.reachable === true ? 'reachable' : host.reachable === false ? 'unreachable' : 'unknown'),
        last_attempt_at:time(host.last_attempt_at), last_success_at:time(host.last_success_at),
        stale: bool(host.stale), stale_reason: reason(host.stale_reason), last_error_code: code(host.last_error?.code ?? discovery?.error_code),
        bat: {status: select(host.bat?.status,['ready','unavailable','unknown']), version: version(host.bat?.version ?? host.server_version), protocol: select(host.bat?.protocol, protocols),
          capabilities: flags(host.bat?.capabilities ?? discovery?.authority?.capabilities, ['claude','codex','terminal','git'])},
        coverage: {basis:'configured_profile_enumeration', complete:bool(discovery?.complete_enumeration), reasons:Array.isArray(discovery?.outside_scan) ? [...new Set(discovery.outside_scan.map(item => reason(item.reason)).filter(Boolean))] : null},
        policy: policyFlags};
    }),
    sessions: {basis:'client_loaded_reads', sampled: sessions?.length ?? null, loaded: count('loaded'), streaming:count('streaming'), unknown_activity:count('unknown_activity'), stale:count('stale'), pending:count('pending'), history_reasons: history, unreported: Object.fromEntries(['loaded','streaming','stale','pending'].map(key => [key, sessions ? sessions.filter(s => s[key] === null).length : null]))},
    operations: Array.isArray(input.operations) ? input.operations.filter(op => ['failed','uncertain'].includes(op.status)).slice(0,50).map(op => ({id:operationId(op.operation_id),
      action: action(op.action),
      status:op.status, error_code:code(op.error_code ?? op.error?.code), created_at:time(op.created_at), updated_at:time(op.updated_at)})) : null,
    coverage: {complete:bool(input.observation?.coverage?.complete), reasons:Array.isArray(input.observation?.coverage?.reasons) ? input.observation.coverage.reasons.map(reason).filter(Boolean) : null},
    operations_basis:'loaded_page', recent_errors:(input.recentErrors || []).slice(-50).map(e => failedCall({at:e.at,method:e.method,path:e.path_template,status:e.status,code:e.code,admissionRefused:e.admission_refused,operation:{operation_id:e.operation_id}})),
    log_locations:{basis:'default_templates_only', state_database:{managed_desktop:databaseTemplates(true),standalone:databaseTemplates(false)},
      runtime_stderr:{managed_desktop:'not_persisted_by_desktop',standalone:'external_service_configuration'}}};
  return JSON.parse(scrubDiagnostics(JSON.stringify(bundle)));
}
