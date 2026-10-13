import {viewState} from './ui/view-state.js';
// Reads the central collector projection; opening a view never scans logs or calls a model.
export function workspaceResources({h, t, api, alive, namespace, workspaceId, errorBox}) {
  const preference = viewState(namespace, workspaceId, 'agents');
  let serial = 0, active = false, busy = false, paused = Boolean(preference.paused), timer = null, data = null, selected = preference.selected || null, signature = '';
  const body = h('tbody'), status = h('div', {role: 'status'}), count = h('p', {class: 'muted'}), observed = h('p', {class: 'muted'});
  const detail = h('aside', {class: 'pm-rtm-detail', 'data-resource-detail': '', hidden: true});
  const quota = h('div', {'data-resource-quota': ''});
  const filter = h('input', {type: 'search', 'aria-label': t('res_filter'), placeholder: t('res_filter'), oninput: () => render(true)});
  const windowSelect = h('select', {'aria-label': t('res_window'), onchange: () => {withdraw(); void refresh();}},
    ...[7, 1, 30, 90].map(days => h('option', {value: String(days)}, t('res_days', {days}))));
  const update = h('button', {type: 'button', class: 'secondary', onclick: () => void refresh()}, t('res_refresh'));
  const pause = h('button', {type: 'button', class: 'secondary', 'aria-pressed': 'false', onclick: () => {
    paused = !paused; pause.textContent = t(paused ? 'res_resume' : 'res_pause'); pause.setAttribute('aria-pressed', String(paused));
    if (paused) clearTimeout(timer); else void refresh();
  }}, t('res_pause'));
  filter.value = preference.filter || ''; windowSelect.value = preference.period || '7';
  pause.textContent = t(paused ? 'res_resume' : 'res_pause'); pause.setAttribute('aria-pressed', String(paused));
  const table = h('div', {class: 'pm-rtm-table', role: 'region', tabindex: '0', 'aria-label': t('res_title')}, h('table', {},
    h('thead', {}, h('tr', {}, ...['res_source', 'res_model', 'res_role', 'res_total', 'res_state'].map(key => h('th', {scope: 'col'}, t(key))))), body));
  const element = h('section', {'data-pm-resources': ''}, h('header', {class: 'pm-heading'}, h('h2', {}, t('res_title')),
    h('div', {class: 'actions'}, pause, update)), h('div', {class: 'pm-workbench-toolbar'}, windowSelect, filter, count),
    observed, status, h('div', {class: 'pm-rtm-layout'}, table, detail),
    h('h3', {}, t('res_quota')), quota, h('p', {class: 'muted'}, t('res_forecast')),
    h('details', {}, h('summary', {}, t('res_coverage')), h('p', {}, t('res_local')), h('p', {}, t('res_scope')),
      h('p', {}, t('res_setup')), h('p', {}, t('res_quota_scope'))));
  const number = value => Number.isFinite(value) ? value.toLocaleString() : t('res_unknown');
  const time = value => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString() : t('res_unknown');
  const metric = value => value?.value != null ? number(value.value) : value?.observed != null
    ? t('res_partial', {count: number(value.observed)}) : t('res_unknown');
  const cli = row => row.adapter === 'codex-rollout-v1' ? 'Codex' : row.adapter === 'claude-transcript-v1' ? 'Claude Code' : row.adapter;
  const stateLabel = row => t(['current', 'catching_up', 'stale', 'pending'].includes(row.status) ? 'res_' + row.status
    : row.status === 'source_unavailable' ? 'res_unavailable' : 'res_error');
  const facts = rows => h('dl', {class: 'pm-resource-facts'}, ...rows.flatMap(([label, value]) => [h('dt', {}, t(label)), h('dd', {}, value)]));
  function schedule() {clearTimeout(timer); if (active && !paused) timer = setTimeout(() => void refresh(), Math.max(10000, Number(data?.interval_s || 60) * 1000));}
  function withdraw() {
    serial++; busy = false; data = null; signature = ''; selected = null; update.disabled = false;
    body.replaceChildren(); detail.replaceChildren(); detail.hidden = true; quota.replaceChildren(); count.replaceChildren(); observed.replaceChildren(); status.replaceChildren();
  }
  function modelRecords(row) {
    if (!row.configurations?.length) return [h('span', {class: 'muted'}, t('res_unknown'))];
    return (row.configurations || []).map(c => h('div', {}, c.actual_model || c.requested_model || t('res_unknown'),
      h('div', {class: 'muted'}, t(c.actual_model ? 'res_actual' : 'res_requested'))));
  }
  function render(force = false) {
    if (!data) return;
    const term = filter.value.trim().toLocaleLowerCase();
    const visible = data.rows.filter(row => [row.source_id, row.role, cli(row), ...(row.configurations || []).flatMap(c => [c.actual_model, c.requested_model])]
      .filter(Boolean).join(' ').toLocaleLowerCase().includes(term));
    count.textContent = t('res_count', {count: visible.length}); observed.textContent = t('res_as_of', {time: time(data.as_of)});
    if (selected && !visible.some(row => row.source_id === selected)) selected = null;
    const next = JSON.stringify([visible.map(({checked_at, successful_at, ...row}) => row), selected, data.quota_pools]);
    if (!force && next === signature) return;
    signature = next;
    const focused = element.contains(document.activeElement) ? document.activeElement?.getAttribute('data-resource-select') : null;
    const detailFocus = detail.contains(document.activeElement) ? (document.activeElement?.tagName === 'SUMMARY' ? 'summary' : 'button') : null;
    const scroll = table.scrollLeft;
    body.replaceChildren(...visible.map(row => h('tr', {'data-resource-row': row.source_id},
      h('th', {scope: 'row'}, h('button', {type: 'button', class: 'secondary', 'data-resource-select': row.source_id,
        'aria-expanded': String(selected === row.source_id), onclick: () => {selected = row.source_id; void refresh();}}, row.source_id), h('div', {class: 'muted'}, cli(row))),
      h('td', {}, ...modelRecords(row)), h('td', {}, row.role === 'unassigned' ? t('wb_unassigned') : row.role),
      h('td', {}, metric(row.metrics?.total)), h('td', {}, stateLabel(row)))));
    if (!visible.length) body.append(h('tr', {}, h('td', {colspan: 5}, t(data.rows.length ? 'res_no_match' : 'res_empty'))));
    table.scrollLeft = scroll;
    if (focused) [...body.querySelectorAll('[data-resource-select]')].find(node => node.getAttribute('data-resource-select') === focused)?.focus({preventScroll: true});
    renderDetail(); renderQuota(visible);
    if (detailFocus) detail.querySelector(detailFocus)?.focus({preventScroll: true});
  }
  function renderDetail() {
    const row = data?.rows.find(value => value.source_id === selected);
    const open = detail.querySelector('details')?.open;
    detail.replaceChildren(); detail.hidden = !row;
    if (detail.hidden) return;
    const close = h('button', {type: 'button', class: 'secondary', onclick: () => {
      const id = selected; selected = null; render(true);
      [...body.querySelectorAll('[data-resource-select]')].find(node => node.getAttribute('data-resource-select') === id)?.focus({preventScroll: true});
    }}, t('wb_close'));
    detail.append(h('header', {class: 'pm-heading'}, h('h3', {}, row.source_id), close),
      facts([['res_source', cli(row)], ['res_state', stateLabel(row)], ['res_observations', number(row.observations)], ['res_calls', number(row.calls)],
        ...['input', 'cache_read', 'cache_write', 'output', 'reasoning', 'total'].map(key => ['res_' + key, metric(row.metrics?.[key])])]),
      h('p', {class: 'muted'}, t(row.adapter === 'codex-rollout-v1' ? 'res_codex_units' : 'res_claude_units')),
      h('h3', {}, t('res_model')),
      ...(row.configurations || []).map(c => facts([['res_actual', c.actual_model || t('res_unknown')],
        ['res_requested', c.requested_model || t('res_unknown')], ['res_effort', c.effort || t('res_unknown')], ['res_version', c.cli_version || t('res_unknown')]])));
    if (row.configurations_partial) detail.append(h('p', {}, t('res_configurations_more')));
    const sources = h('details', {}, h('summary', {}, t('res_coverage')), facts([
      ['res_first', time(row.first_at)], ['res_last', time(row.last_at)], ['res_collected', time(row.successful_at)]]),
      h('code', {class: 'pm-source'}, row.adapter + ' · ' + row.status));
    sources.open = Boolean(open); detail.append(sources);
  }
  function renderQuota(visible) {
    // One row per explicit pool and limit. Never sum balances across sources.
    const pools = data.quota_pools.filter(pool => pool.snapshot && pool.status === 'reported' && pool.sources.some(id => visible.some(row => row.source_id === id)));
    quota.replaceChildren();
    if (!pools.length) {quota.append(h('p', {class: 'muted'}, t('res_quota_unknown'))); return;}
    const rows = pools.flatMap(pool => pool.snapshot.windows.map(w => h('tr', {},
      h('th', {scope: 'row'}, h('code', {class: 'pm-source'}, pool.pool_id), h('div', {}, pool.limit_id),
        h('div', {class: 'muted'}, t('res_minutes', {minutes: w.window_minutes}))),
      h('td', {}, number(Math.round((100 - w.used_percent) * 100) / 100) + '%'),
      h('td', {}, time(new Date(w.resets_at * 1000).toISOString()),
        h('div', {class: 'muted'}, t('res_quota_at', {time: time(pool.snapshot.observed_at)}))))));
    quota.append(h('div', {class: 'pm-rtm-table', tabindex: '0', role: 'region', 'aria-label': t('res_quota')}, h('table', {},
      h('thead', {}, h('tr', {}, ...['res_pool', 'res_remaining', 'res_reset'].map(key => h('th', {scope: 'col'}, t(key))))), h('tbody', {}, ...rows))));
  }
  async function refresh() {
    if (!active) return;
    const ticket = ++serial; busy = true; update.disabled = true; status.replaceChildren();
    const until = new Date(), since = new Date(until.getTime() - Number(windowSelect.value) * 86400000);
    try {
      const result = await api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}/resources?` + new URLSearchParams({since: since.toISOString(), until: until.toISOString()}));
      alive(); if (ticket !== serial) return;
      if (result?.workspace_id !== workspaceId || !Array.isArray(result.rows) || !Array.isArray(result.quota_pools)) throw Error(t('pm_scope_mismatch'));
      data = result; render();
    } catch (error) {
      try {alive(); if (ticket !== serial) return;} catch {return;}
      withdraw(); status.replaceChildren(errorBox(error));
    } finally {
      if (ticket === serial || !data) {busy = false; update.disabled = false; schedule();}
    }
  }
  return {element, refresh, withdraw, setActive(value) {
    active = value; clearTimeout(timer);
    if (active) {if (!busy) void refresh();} else {serial++; busy = false; update.disabled = false;}
  }, dispose() {Object.assign(preference, {selected, paused, filter: filter.value, period: windowSelect.value}); active = false; clearTimeout(timer); withdraw();}};
}
