// All surfaces render the same explicit projection, including unknown rows.
export function versionFactText(row, t) {
  const unknown=t('diag_unknown');
  if(['desktop','desktop_released','connector','connector_released','mcp','connector_installed','bundled_runtime','running_runtime'].includes(row.id))
    return t('diag_version_number',{version:row.version||unknown})+' · '+t('diag_version_commit',{commit:row.commit||unknown});
  if(['loaded_ui','served_ui'].includes(row.id))return t('diag_version_digest',{digest:row.digest||unknown});
  if(['adapter_bat_observed','bat_observed'].includes(row.id))return t('diag_version_commit',{commit:row.commit||unknown});
  return t('diag_version_number',{version:row.version||unknown});
}
export function versionFacts({h, t, projection}) {
  const rows=projection.rows.filter(row=>projection.platform==='desktop'||!['desktop','desktop_update','desktop_released'].includes(row.id));
  return h('section', {class:'version-facts','data-version-facts':''},
    h('h3', {}, t('diag_versions')),
    h('dl', {}, ...rows.map(row => h('div', {'data-version-row':row.id},
      h('dt', {}, t('diag_version_'+row.id)),
      h('dd', {}, h('p', {class:'mono'}, versionFactText(row,t)),
        h('p', {class:'muted'}, t('diag_version_source_'+row.source), ' · ', t('diag_version_period_'+row.period),
          ' · ', row.observed_at || t('diag_version_time_unknown')),
        row.id === 'connector_installed' ? h('div', {},
          row.state !== 'reported' ? h('p', {class:'muted'}, t('diag_version_update_'+row.state), row.code ? ' · '+row.code : '') : null,
          row.installed_at ? h('p',{class:'muted'},t('diag_version_installed_at',{time:row.installed_at})) : null,
          row.channel ? h('p', {class:'muted'}, t('diag_version_update_channel',{channel:t('diag_version_channel_'+row.channel)})) : null,
          row.enabled !== null ? h('p', {class:'muted'}, t('diag_version_update_'+(row.enabled?'enabled':'disabled'))) : null,
          row.pending === true ? h('p', {class:'muted'}, t('diag_version_update_pending')) : null,
          row.last ? h('p', {class:'muted'}, t('diag_version_update_last',{result:t('diag_version_result_'+row.last.status),at:row.last.at||t('diag_version_time_unknown')}), row.last.code ? ' · '+row.last.code : '') : null,
          h('p', {'data-connector-installation-difference':''}, t('diag_version_installation_'+projection.connector_installation_difference))) : null,
        row.published_at ? h('p',{class:'muted'},t('diag_version_published',{time:row.published_at})) : null,
        row.recorded_at ? h('p',{class:'muted'},t('diag_version_run_updated',{time:row.recorded_at})) : null)))),
    h('p', {'data-runtime-difference':''}, t('diag_runtime_'+projection.runtime_difference)),
    h('p', {class:'muted'}, t('diag_adapter_observed_unknown')));
}
