import {frontendBuild as loadedFrontendBuild, frontendDigest} from './frontend-identity.js';

// Session-only inputs. Callers supply completed reads, never persisted identities.
// Each observation retains its own clock; reading a launch receipt is not a new
// observation of the process that happens to be answering now.
export const version = value => typeof value === 'string' && value === value.trim() && /^\d+(?:\.\d+){1,3}(?:-(?:alpha|beta|rc|dev)\.?\d{0,4})?$/i.test(value) ? value : null;
export const commit = value => typeof value === 'string' && /^(?:[0-9a-f]{40}|[0-9a-f]{64})$/.test(value) && [40,64].includes(value.length) ? value : null;
export const digest = frontendDigest;
// A client load clock, not a Connector observation or a release timestamp.
// Keep it with the captured module identity across capabilities refreshes.
const frontendLoadedAt = new Date().toISOString();
export const time = value => {
  if(typeof value === 'number' && Number.isFinite(value) && value >= 0 && value < 253402300800) return new Date(value * 1000).toISOString();
  return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value)) ? value : null;
};
const buildSource = value => ['vcs','package','bundled'].includes(value) ? value : null;
const updateCode = value => typeof value === 'string' && /^[a-z][a-z0-9_]{0,95}$/.test(value) ? value : null;
const updateStates = ['reported','not_enrolled','not_managed','unsupported','unreadable'];
const updateResults = ['enrolled_for_next_launch','installed_for_next_launch','rolled_back_updates_disabled','no_newer_release','up_to_date','failed'];
const fact = (id, value, source, at, period, extra = {}) => {
  const selected={version:version(value?.version),commit:commit(value?.commit),digest:digest(value?.digest)};
  const observed_at=time(at), observed=Object.values(selected).some(Boolean)||observed_at!==null;
  return {id,...selected,source:observed?source:'not_reported',observed_at,
    period:observed?period:'unknown',build_source:buildSource(value?.source),...extra};
};
// A candidate without its producer's completed-check clock is no release fact.
// Neither a later status read nor an installed/loaded value can fill this slot.
const released = (id, candidate, source, at) => {
  const known = version(candidate?.version) && commit(candidate?.commit) && time(at);
  return fact(id, known ? candidate : null, source, known ? at : null, 'release_check',
    {published_at:known ? time(candidate.published_at) : null});
};
export function versionProjection(input = {}) {
  const caps = input.online === false ? null : input.caps;
  const managed = input.managedRuntime;
  const descriptor = input.adapterRead?.document?.native_analysis;
  const run = input.adapterRunRead?.run;
  const frontend = input.frontendBuild === undefined ? loadedFrontendBuild : input.frontendBuild;
  const update = caps?.connector_update;
  const reported = update?.state === 'reported';
  const releaseCheck = reported ? update.release_check : null;
  const candidate = input.updaterReadFailed ? null : input.updater?.candidate;
  const last = reported && updateResults.includes(update.last?.status)
    ? {status:update.last.status,code:updateCode(update.last.code),at:time(update.last.at)} : null;
  const loadedAt = input.frontendLoadedAt === undefined && frontend === loadedFrontendBuild && digest(frontend?.frontend_digest)
    ? frontendLoadedAt : input.frontendLoadedAt;
  const rows = [
    released('desktop_released', input.desktop && candidate ? {version:candidate.version,commit:candidate.source_sha} : null,
      'desktop_updater', input.desktop ? candidate?.checked_at : null),
    released('connector_released', releaseCheck?.candidate, 'connector_release_check', releaseCheck?.completed_at),
    // Dashboard has no MCP observation transport. Central or installed files
    // cannot identify an external MCP process.
    fact('mcp', null, 'not_reported', null, 'unknown'),
    fact('desktop', input.desktop ? input.appBuild : null, 'native_app_build', input.nativeReadAt, 'loaded'),
    fact('loaded_ui', {digest:frontend?.frontend_digest}, 'loaded_ui', loadedAt, 'loaded'),
    fact('served_ui', {digest:caps?.frontend?.digest}, 'capabilities', caps ? input.capabilitiesReadAt : null, 'read'),
    fact('connector', caps ? {...caps.build,version:caps.build?.version ?? caps.connector} : null,
      'capabilities', caps ? input.capabilitiesReadAt : null, 'read'),
    fact('connector_installed', reported ? update.installed : null, 'connector_update',
      reported ? input.capabilitiesReadAt : null, 'next_launch', {
        installed_at:reported ? time(update.installed_at) : null,
        state:updateStates.includes(update?.state) ? update.state : 'not_reported',
        code:update?.state === 'unreadable' ? updateCode(update.code) : null,
        enabled:reported && typeof update.enabled === 'boolean' ? update.enabled : null,
        channel:reported && ['stable','preview'].includes(update.channel) ? update.channel : null,
        pending:reported && typeof update.pending === 'boolean' ? update.pending : null,last,
      }),
    ...['bundled_runtime','running_runtime'].map(id => fact(id, managed?.[id] ? {
      version:managed[id].version, commit:managed[id].commit, source:managed[id].build_source,
    } : null, id === 'bundled_runtime' ? 'managed_launch_process' : 'managed_launch_capabilities', managed?.[id]?.observed_at, 'launch')),
    fact('adapter_required', {version:descriptor?.required_version}, 'pm_capabilities', input.adapterRead?.at, 'read'),
    // Installation hints do not report an observed executable version. Native
    // preflight rejects a mismatch without exposing that value; do not borrow
    // required_version or an earlier run's receipt to fill this unknown.
    fact('adapter_observed', null, 'not_reported', null, 'unknown'),
    fact('adapter_bat_observed', {commit:run?.bat_runtime_provenance?.codex_source_revision},
      'pm_run_provenance', input.adapterRunRead?.at, 'run', {recorded_at:time(run?.updated_at)}),
    fact('bat_observed', {commit:run?.bat_runtime_provenance?.bat_source_revision},
      'pm_run_provenance', input.adapterRunRead?.at, 'run', {recorded_at:time(run?.updated_at)}),
    fact('desktop_update', input.updaterReadFailed ? null : {version:input.updater?.current_version},
      'desktop_updater', input.updaterReadFailed ? null : input.updaterReadAt, 'read'),
  ];
  const installedCommit = rows.find(row => row.id === 'connector_installed').commit;
  const answeringCommit = rows.find(row => row.id === 'connector').commit;
  return {platform:input.desktop ? 'desktop' : 'browser', rows,
    connector_installation_difference:installedCommit && answeringCommit ? (installedCommit === answeringCommit ? 'same' : 'differs') : 'unknown',
    runtime_difference: ['same','differs'].includes(managed?.runtime_difference) ? managed.runtime_difference : 'unknown'};
}
export const versionRow = (projection, id) => projection.rows.find(row => row.id === id);
// Compatibility fields for batc.diagnostics.v1 and its existing build-line
// renderer. No surface resolves versions independently of these rows.
export function projectionBuild(projection) {
  const connector=versionRow(projection,'connector');
  const app=versionRow(projection,'desktop');
  return {platform:projection.platform,app_version:app.version,app_commit:app.commit,
    frontend_digest:versionRow(projection,'loaded_ui').digest,served_frontend_digest:versionRow(projection,'served_ui').digest,
    connector_version:connector.version,connector_commit:connector.commit,connector_source:connector.build_source};
}
