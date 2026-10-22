import {healthLabel, healthAge, healthWaiting} from './health-fact.js';

export function healthSummaryContent(projection, {h, t}) {
  const summary = projection.summary;
  const pair = (label, value) => t('health_pair', {label: t(label), value});
  const join = values => values.reduce((left, right) => t('health_join', {left, right}));
  const last = summary?.facts.filter(row => row.activity.freshness === 'stale')
    .sort((a, b) => (b.activity.ageSeconds || 0) - (a.activity.ageSeconds || 0))[0];
  const refresh = projection.capabilities.refresh;
  const checking = summary?.stale || summary?.unknown || healthWaiting(summary?.connection)
    || healthWaiting(last?.activity) || healthWaiting(projection.connection) || healthWaiting(projection.other)
    || projection.capabilities.freshness === 'invalidated' && refresh?.state === 'loading';
  return [summary ? h('span', {'data-health-key': 'selected', 'data-health-selected': '', class: 'health-selected'},
    h('span', {}, pair(summary.selected.workspaceId ? 'health_selected_workspace' : 'health_selected_work', summary.selected.label || summary.selected.host)),
    h('span', {class: 'ui-pill', 'data-health-compact': summary.compact}, t('health_' + summary.compact)),
    summary.connection ? h('span', {class: 'health-installation-connection'}, healthLabel({...summary.connection,
      key: summary.connection.value === 'connected' ? 'ui_connected' : summary.connection.value === 'not_connected' ? 'health_connection_problem' : 'health_unknown'}, t, {checking: false})) : null,
    h('span', {class: 'health-facets'}, join([
      pair('health_attention_label', t(summary.attention ? 'health_attention' : 'health_none')),
      pair('health_activity', t('health_' + summary.activity)),
      pair('health_freshness', t(summary.stale ? 'health_stale' : summary.unknown ? 'health_unknown' : 'health_current'))])),
    last ? h('span', {class: 'health-last-value'}, healthLabel({...last.activity,
      key: ['streaming', 'starting'].includes(last.activity.value) ? 'health_working'
        : last.activity.value === 'not_streaming' ? 'health_idle' : 'health_unknown'}, t, {checking: false})) : null) : null,
    projection.connection.freshness === 'stale' ? h('span', {'data-health-key': 'connection-age', 'data-health-age': '', 'aria-live': 'off'},
      healthAge(projection.connection.ageSeconds, t)) : null,
    projection.capabilities.freshness === 'invalidated' ? h('span', {'data-health-key': 'capabilities'},
      t(refresh?.state === 'loading' ? 'health_capabilities_changed' : 'health_capabilities_reload', {reason: refresh?.reason || 'IDENTITY_UNVERIFIED'})) : null,
    projection.capabilities.freshness === 'invalidated' && refresh?.state !== 'loading'
      ? h('button', {'data-health-key': 'reload', onclick: () => location.reload()}, t('health_reload')) : null,
    projection.otherAttention ? h(projection.other.href ? 'a' : 'span', {href: projection.other.href, 'data-health-key': 'other', 'data-health-other': ''},
      healthLabel({...projection.other, key: projection.otherAttention === 1 ? 'health_other_machines_one' : 'health_other_machines_many'},
        (key, vars) => t(key, {count: projection.otherAttention, ...vars}), {checking: false})) : null,
    checking ? h('span', {'data-health-key': 'checking', 'data-health-checking': ''}, t('health_checking')) : null].filter(Boolean);
}
