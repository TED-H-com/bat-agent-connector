// Session-only inputs. Callers supply completed reads, never persisted identities.
// Each observation retains its own clock; reading a launch receipt is not a new
// observation of the process that happens to be answering now.
export const version = value => typeof value === 'string' && value === value.trim() && /^\d+(?:\.\d+){1,3}(?:-(?:alpha|beta|rc|dev)\.?\d{0,4})?$/i.test(value) ? value : null;
export const commit = value => typeof value === 'string' && /^(?:[0-9a-f]{40}|[0-9a-f]{64})$/.test(value) && [40,64].includes(value.length) ? value : null;
export const digest = value => typeof value === 'string' && value.length === 64 && /^[a-f0-9]{64}$/.test(value) ? value : null;
export const time = value => {
  if(typeof value === 'number' && Number.isFinite(value) && value >= 0 && value < 253402300800) return new Date(value * 1000).toISOString();
  return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value)) ? value : null;
};
const buildSource = value => ['vcs','package','bundled'].includes(value) ? value : null;
const fact = (id, value, source, at, period, extra = {}) => ({id,
  version:version(value?.version), commit:commit(value?.commit), digest:digest(value?.digest),
  source, observed_at:time(at), period, build_source:buildSource(value?.source), ...extra});
export function versionProjection(input = {}) {
  const caps = input.online === false ? null : input.caps;
  const managed = input.managedRuntime;
  const descriptor = input.adapterRead?.document?.native_analysis;
  const run = input.adapterRunRead?.run;
  const rows = [
    fact('desktop', input.desktop ? input.appBuild : null, 'native_app_build', input.nativeReadAt, 'loaded'),
    fact('loaded_ui', {digest:input.frontendBuild?.frontend_digest}, 'loaded_ui', input.frontendLoadedAt, 'loaded'),
    fact('served_ui', {digest:caps?.frontend?.digest}, 'capabilities', caps ? input.capabilitiesReadAt : null, 'read'),
    fact('connector', caps ? {...caps.build,version:caps.build?.version ?? caps.connector} : null,
      'capabilities', caps ? input.capabilitiesReadAt : null, 'read'),
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
  return {platform:input.desktop ? 'desktop' : 'browser', rows,
    runtime_difference: ['same','differs'].includes(managed?.runtime_difference) ? managed.runtime_difference : 'unknown'};
}
export const versionRow = (projection, id) => projection.rows.find(row => row.id === id);
// Compatibility fields for batc.diagnostics.v1 and its existing build-line
// renderer. No surface resolves versions independently of these rows.
export function projectionBuild(projection) {
  const connector=versionRow(projection,'connector');
  const app=versionRow(projection,'desktop');
  return {platform:projection.platform,app_version:app.version,app_commit:app.commit,
    connector_version:connector.version,connector_commit:connector.commit,connector_source:connector.build_source};
}
