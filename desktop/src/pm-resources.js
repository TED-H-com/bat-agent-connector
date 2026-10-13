import {viewState} from './ui/view-state.js';
import {agentMark, button, dataTable, disclosure, emptyState, iconButton, inspector, menu, metric, sectionCard, segmented, skeleton, statusPill} from './ui/components.js';
import {reasonPresentation} from './ui/ui-strings.js';
import {discussHref} from './pm-discuss-link.js';
import {usageChart} from './pm-agents-chart.js';
import {browserTimeZone, cliName, dayKey, isBucketPayload, stackDailyBuckets} from './pm-agents-trend.js';

const PERIODS = ['1', '7', '30', '90'];
const READABLE = new Set(['current', 'catching_up', 'stale']);
const EM = '—';

// Reads the central collector projection. Opening the view never scans logs or calls a model.
export function workspaceResources({h, t, api, alive, namespace, workspaceId, errorBox}) {
  const preference = viewState(namespace, workspaceId, 'agents');
  let period = PERIODS.includes(preference.period) ? preference.period : '7';
  let filter = savedFilter(preference.filter);
  let serial = 0, active = false, busy = false, paused = Boolean(preference.paused), timer = null;
  let data = null, selected = preference.selected || null, signature = '', trendBuckets = null;
  let inspectorPanel = null, closing = false, menuSignature = '';
  const meta = h('p', {class: 'ui-meta', 'data-resource-meta': ''});
  const status = h('div', {role: 'status'});
  const main = h('div', {class: 'pm-agents-main'});
  const layout = h('div', {class: 'pm-agents-layout'}, main);
  const periodControl = segmented({
    label: t('res_window'), value: period,
    options: PERIODS.map(value => ({value, label: t('res_period_' + value)})),
    onChange(value) {
      period = value; preference.period = period;
      const keep = selected;
      withdraw({preserveSelection: true});
      selected = keep;
      void refresh({trend: true});
    },
  });
  const pause = button({variant: 'ghost', size: 'sm', label: t(paused ? 'res_resume' : 'res_pause'), 'aria-pressed': String(paused), onclick: () => {
    paused = !paused; preference.paused = paused;
    pause.textContent = t(paused ? 'res_resume' : 'res_pause');
    pause.setAttribute('aria-pressed', String(paused));
    if (paused) clearTimeout(timer); else void refresh({trend: false});
  }});
  const refreshButton = iconButton({icon: 'refresh-cw', label: t('res_refresh'), class: 'pm-agents-refresh', onclick: () => void refresh({trend: true})});
  // iconButton overwrites class, so the refresh pin is applied on the returned node.
  refreshButton.classList.add('pm-agents-refresh');
  const toolbar = h('div', {class: 'pm-agents-toolbar'}, periodControl, h('div', {class: 'pm-agents-filter-host'}), pause, refreshButton);
  const element = h('section', {'data-pm-resources': '', class: 'pm-agents'}, meta, toolbar, status, layout);
  syncFilterMenu();

  function finite(value) {return typeof value === 'number' && Number.isFinite(value) ? value : null;}
  function uiLocale() {return (navigator.language || 'en').toLowerCase().startsWith('zh') ? 'zh-TW' : 'en-US';}
  function cssEscape(value) {return window.CSS?.escape ? CSS.escape(String(value)) : String(value).replace(/"/g, '\\"');}
  function cliOf(row) {
    const adapter = typeof row?.adapter === 'string' ? row.adapter : '';
    return adapter.split('-')[0] || '';
  }
  function isReadable(row) {return READABLE.has(row?.status) && Boolean(row?.metrics);}
  function formatDay(date, withYear) {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date || '');
    if (!match) return '';
    const instant = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]), 12));
    const options = {timeZone: 'UTC', month: 'short', day: 'numeric'};
    if (withYear) options.year = 'numeric';
    return new Intl.DateTimeFormat(uiLocale(), options).format(instant);
  }
  function relativePast(ms) {
    if (!Number.isFinite(ms) || ms < 60000) return t('res_just_now');
    const min = Math.floor(ms / 60000);
    if (min < 60) return t('res_min_ago', {n: min});
    const hr = Math.floor(min / 60);
    if (hr < 24) return t('res_hr_ago', {n: hr});
    return t('res_day_ago', {n: Math.floor(hr / 24)});
  }
  function relativeFuture(ms) {
    const min = Math.max(1, Math.ceil(ms / 60000));
    if (min < 60) return t('res_in_min', {n: min});
    const hr = Math.ceil(min / 60);
    if (hr < 24) return t('res_in_hr', {n: hr});
    return t('res_in_day', {n: Math.max(1, Math.round(hr / 24))});
  }
  function clock(value) {
    const parsed = Date.parse(value);
    if (!Number.isFinite(parsed)) return EM;
    return new Intl.DateTimeFormat(uiLocale(), {month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'}).format(parsed);
  }
  function seenCell(row) {
    if (!isReadable(row)) return EM;
    const parsed = Date.parse(row.last_at);
    if (!Number.isFinite(parsed)) return EM;
    const when = new Date(parsed);
    const locale = uiLocale();
    const date = new Intl.DateTimeFormat(locale, {month: 'short', day: 'numeric'}).format(when);
    const time = new Intl.DateTimeFormat(locale, {hour: 'numeric', minute: '2-digit'}).format(when);
    return h('span', {class: 'pm-agents-seen'}, date, h('span', {}, time));
  }
  function metricText(value) {
    if (finite(value?.value) != null) return finite(value.value).toLocaleString();
    if (finite(value?.observed) != null) return t('res_partial', {count: finite(value.observed).toLocaleString()});
    return t('res_unknown');
  }
  function roleLabel(role) {return role === 'unassigned' ? t('wb_unassigned') : (role || EM);}
  function filterLabel() {
    if (filter.kind === 'cli') return t('res_filter_cli', {name: cliName(filter.value)});
    if (filter.kind === 'role') return t('res_filter_role', {name: roleLabel(filter.value)});
    if (filter.kind === 'model') return t('res_filter_model', {name: filter.value});
    return t('res_filter_all');
  }
  function statusNode(row) {
    const known = {
      current: ['ok', 'res_current'], catching_up: ['info', 'res_catching_up'], stale: ['warn', 'res_stale'],
      pending: ['warn', 'res_pending'], source_unavailable: ['neutral', 'res_unavailable'], binding_changed: ['warn', 'res_error'],
    };
    if (known[row.status]) {
      const [tone, key] = known[row.status];
      return h('span', {class: `ui-pill tone-${tone}`, 'data-status': row.status}, t(key));
    }
    return h('span', {class: 'ui-pill tone-bad', 'data-status': row.status || 'unknown'}, t(reasonPresentation(row.status).key));
  }
  function visibleRows() {return (data?.rows || []).filter(matches);}
  function matches(row) {
    if (!filter.value || filter.kind === 'all') return true;
    if (filter.kind === 'cli') return cliOf(row) === filter.value;
    if (filter.kind === 'role') return row.role === filter.value;
    if (filter.kind === 'model') return (row.configurations || []).some(item => item?.actual_model === filter.value || item?.requested_model === filter.value);
    return true;
  }
  function filterOptions() {
    const options = [], seen = new Set();
    const add = (kind, value, label) => {
      if (!value || seen.has(kind + '\0' + value)) return;
      seen.add(kind + '\0' + value);
      options.push({kind, value, label});
    };
    for (const row of data?.rows || []) add('cli', cliOf(row), t('res_filter_cli', {name: cliName(cliOf(row))}));
    for (const row of data?.rows || []) add('role', row.role, t('res_filter_role', {name: roleLabel(row.role)}));
    for (const row of data?.rows || []) for (const item of row.configurations || []) {
      if (item?.actual_model) add('model', item.actual_model, t('res_filter_model', {name: item.actual_model}));
      if (item?.requested_model) add('model', item.requested_model, t('res_filter_model', {name: item.requested_model}));
    }
    return options;
  }
  function setFilter(next) {
    filter = {kind: next.kind || 'all', value: next.value || ''};
    preference.filter = filter;
    labelFilterButton();
    render(true);
  }
  function labelFilterButton() {
    const trigger = element.querySelector('[data-resource-filter]');
    if (!trigger) return;
    const label = filterLabel();
    trigger.textContent = label;
    trigger.setAttribute('aria-label', label);
  }
  function syncFilterMenu() {
    const sig = JSON.stringify(filterOptions());
    labelFilterButton();
    if (sig === menuSignature && element.querySelector('.pm-agents-filter-host .ui-menu')) return;
    menuSignature = sig;
    const host = element.querySelector('.pm-agents-filter-host');
    host.querySelector('.ui-menu')?.dispose?.();
    const label = filterLabel();
    const trigger = button({variant: 'ghost', size: 'sm', label, 'aria-label': label, title: t('res_filter'), 'data-resource-filter': ''});
    const items = [{key: 'all', label: t('res_filter_all'), onSelect: () => setFilter({kind: 'all'})},
      ...filterOptions().map(option => ({key: option.kind + ':' + option.value, label: option.label, onSelect: () => setFilter(option)}))];
    host.replaceChildren(menu({button: trigger, items}));
  }
  function facts(pairs) {
    return h('dl', {class: 'pm-agents-facts'}, ...pairs.flatMap(([label, value]) => [h('dt', {}, label), h('dd', {}, value)]));
  }
  function modelCell(row) {
    if (!isReadable(row)) return EM;
    const items = row.configurations || [];
    const first = items[0];
    if (!first) return t('res_unknown');
    const requested = first.requested_model && first.requested_model !== first.actual_model
      ? h('div', {class: 'pm-agents-requested'}, t('res_requested_short', {model: first.requested_model})) : null;
    const actual = first.actual_model || (!requested ? t('res_unknown') : null);
    return h('div', {class: 'pm-agents-model'}, actual, requested,
      items.length > 1 ? h('div', {class: 'pm-agents-requested'}, t('res_more_models', {count: items.length - 1})) : null);
  }
  function tokensCell(row) {
    if (!isReadable(row)) return EM;
    const metrics = row.metrics || {};
    const read = finite(metrics.cache_read?.value), write = finite(metrics.cache_write?.value);
    const cache = read == null || write == null ? EM : (read + write).toLocaleString();
    const part = value => finite(value?.value) == null ? EM : finite(value.value).toLocaleString();
    const total = metrics.total;
    const headline = finite(total?.value) != null ? finite(total.value).toLocaleString()
      : finite(total?.observed) != null ? t('res_partial', {count: finite(total.observed).toLocaleString()}) : EM;
    return h('div', {}, h('div', {class: 'tabular'}, headline),
      h('div', {class: 'pm-agents-tokens-detail'}, [part(metrics.input), cache, part(metrics.output), part(metrics.reasoning)].join(' · ')));
  }
  function sourceCell(row) {
    const cli = cliOf(row);
    return h('div', {class: 'pm-agents-source'},
      isReadable(row) && cli ? agentMark(cli) : null,
      h('div', {class: 'pm-agents-source-id'}, row.source_id,
        isReadable(row) && cli ? h('div', {class: 'ui-meta'}, cliName(cli)) : null));
  }
  function updateMeta() {
    const stacked = stackDailyBuckets(data?.buckets, new Date());
    const zone = stacked?.tz || 'UTC';
    let start = '', end = '';
    if (stacked?.days.length) {
      const first = stacked.days[0].date, last = stacked.days.at(-1).date;
      const withYear = first.slice(0, 4) !== last.slice(0, 4);
      start = formatDay(first, withYear); end = formatDay(last, withYear);
    } else if (data?.window?.since && data?.window?.until) {
      const since = Date.parse(data.window.since), until = Date.parse(data.window.until);
      if (Number.isFinite(since) && Number.isFinite(until) && until > since) {
        const first = dayKey(new Date(since), zone), last = dayKey(new Date(until - 1), zone);
        const withYear = first.slice(0, 4) !== last.slice(0, 4);
        start = formatDay(first, withYear); end = formatDay(last, withYear);
      }
    }
    const parsed = Date.parse(data?.as_of);
    const ago = Number.isFinite(parsed) ? relativePast(Date.now() - parsed) : '';
    meta.textContent = start && end ? (ago ? t('res_observed', {start, end, ago}) : t('res_observed_range', {start, end})) : '';
  }
  function tokenMetric() {
    const readable = (data?.rows || []).filter(isReadable);
    const values = readable.map(row => finite(row.metrics?.total?.value));
    const known = values.filter(value => value != null);
    const stacked = stackDailyBuckets(data?.buckets, new Date());
    const partial = values.some(value => value == null) || Boolean(stacked?.days.some(day => day.partial || day.mark));
    if (!known.length) return metric({label: t('res_tokens'), value: readable.length ? EM : null, pill: readable.length ? statusPill('partial') : null});
    return metric({label: t('res_tokens'), value: known.reduce((sum, value) => sum + value, 0).toLocaleString(), pill: partial ? statusPill('partial') : null});
  }
  function resetMetric() {
    const now = Date.now();
    let earliest = null;
    for (const pool of data?.quota_pools || []) {
      if (pool?.status !== 'reported') continue;
      for (const item of pool.snapshot?.windows || []) {
        const at = Number(item?.resets_at) * 1000;
        if (!Number.isFinite(at) || at <= now) continue;
        if (earliest == null || at < earliest) earliest = at;
      }
    }
    return metric({label: t('res_next_reset'), value: earliest == null ? null : relativeFuture(earliest - now)});
  }
  function quotaNodes(visible) {
    const ids = new Set(visible.map(row => row.source_id));
    const blocks = [];
    for (const pool of data?.quota_pools || []) {
      if (!pool?.sources?.some(id => ids.has(id))) continue;
      const windows = pool.status === 'reported' && pool.snapshot?.windows?.length ? pool.snapshot.windows : [null];
      for (const item of windows) blocks.push(quotaBlock(pool, item));
    }
    if (!blocks.length) return [h('p', {class: 'ui-meta'}, t('res_quota_unknown'))];
    return [sectionCard({title: t('res_quota_heading'), body: h('div', {class: 'pm-agents-quotas'}, ...blocks)})];
  }
  function quotaBlock(pool, item) {
    const used = item ? finite(item.used_percent) : null;
    const confirmed = pool.status === 'reported' && item && used != null;
    const bar = h('div', {class: 'pm-agents-used', role: 'img', 'aria-label': confirmed ? t('res_used') + ' ' + used + '%' : t('res_unconfirmed')}, h('span'));
    if (confirmed) bar.querySelector('span').style.width = Math.max(0, Math.min(100, used)) + '%';
    const bits = [];
    if (finite(item?.window_minutes) != null) bits.push(t('res_window_short', {minutes: item.window_minutes}));
    if (finite(item?.resets_at) != null) {
      const at = item.resets_at * 1000;
      bits.push(t('res_resets_in', {when: at > Date.now() ? relativeFuture(at - Date.now()) : clock(new Date(at).toISOString())}));
    }
    return h('article', {class: 'pm-agents-quota', 'data-resource-pool': pool.pool_id || ''},
      h('div', {class: 'pm-agents-quota-head'},
        h('div', {}, h('div', {class: 'pm-agents-pool-id'}, pool.pool_id || EM), pool.limit_id ? h('div', {class: 'ui-meta'}, pool.limit_id) : null),
        confirmed ? h('div', {class: 'tabular'}, t('res_used') + ' ' + used + '%') : h('span', {class: 'ui-pill tone-neutral'}, t('res_unconfirmed'))),
      confirmed ? bar : null,
      bits.length ? h('div', {class: 'ui-meta'}, bits.join(' · ')) : null);
  }
  function tableFor(visible) {
    if (!data?.rows?.length) return emptyState({title: t('res_empty')});
    if (!visible.length) return h('p', {class: 'ui-meta'}, t('res_no_match'));
    return dataTable({
      rows: visible, rowKey: 'source_id', selected,
      rowAttrs: row => ({'data-resource-row': row.source_id, 'data-resource-select': row.source_id}),
      onSelect(key) {selected = key; preference.selected = selected; signature = currentSignature(); renderInspector();},
      columns: [
        {label: t('res_source'), render: sourceCell},
        {label: t('res_model_col'), render: modelCell},
        {label: t('res_role'), render: row => roleLabel(row.role)},
        {label: h('span', {title: t('res_tokens_hint')}, t('res_tokens_col')), numeric: true, render: tokensCell},
        {label: t('res_calls_col'), numeric: true, render: row => isReadable(row) && finite(row.calls) != null ? finite(row.calls).toLocaleString() : EM},
        {label: t('res_seen'), render: seenCell},
        {label: t('res_status'), render: statusNode},
      ],
    });
  }
  function paint(visible) {
    const focusId = document.activeElement?.closest?.('[data-resource-row]')?.getAttribute('data-resource-row') || '';
    const scroller = main.querySelector('.ui-data-table');
    if (scroller) preference.scroll = scroller.scrollLeft;
    const kept = inspectorPanel;
    const chart = usageChart({h, t, buckets: data.buckets, now: new Date(), exactKey: `agents-exact:${workspaceId}`});
    const current = (data.rows || []).filter(row => row.status === 'current').length;
    // replaceChildren stringifies null, so an omitted chart must be left out of the list.
    main.replaceChildren(...[
      h('div', {class: 'pm-agents-metrics', 'data-resource-metrics': ''}, tokenMetric(),
        metric({label: t('res_sources'), value: t('res_sources_value', {current, total: data.rows.length})}), resetMetric()),
      chart ? sectionCard({title: t('res_trend'), body: chart}) : null,
      tableFor(visible),
      h('div', {'data-resource-quota': ''}, ...quotaNodes(visible)),
      disclosure({label: t('res_coverage'), key: `agents-scope:${workspaceId}`, body: h('div', {class: 'pm-agents-scope'},
        coverageLine(data.coverage), h('p', {}, t('res_local')), h('p', {}, t('res_scope')), h('p', {}, t('res_setup')),
        h('p', {}, t('res_quota_scope')), h('p', {}, t('res_forecast')))}),
    ].filter(Boolean));
    layout.replaceChildren(main);
    if (kept && kept.dataset.source === selected) {layout.append(kept); fillInspector(data.rows.find(row => row.source_id === selected));}
    else {if (kept) {inspectorPanel = kept; clearInspector();} renderInspector();}
    const table = main.querySelector('.ui-data-table');
    if (table) {
      table.scrollLeft = preference.scroll || 0;
      table.addEventListener('scroll', () => {preference.scroll = table.scrollLeft;}, {passive: true});
    }
    if (focusId) layout.querySelector(`[data-resource-row="${cssEscape(focusId)}"]`)?.focus({preventScroll: true});
    syncFilterMenu();
  }
  function coverageLine(code) {
    const reason = reasonPresentation(code);
    return h('p', {}, t(reason.key), reason.raw ? h('code', {class: 'mono'}, reason.raw) : null);
  }
  function fillInspector(row) {
    if (!inspectorPanel || !row) return;
    const nodes = [h('div', {}, statusNode(row)),
      h('p', {}, h('a', {href: discussHref({workspaceId, refs: [], title: row.source_id, from: 'agents'}), 'data-resource-discuss': ''}, t('res_discuss')))];
    if (isReadable(row)) {
      const models = row.configurations || [];
      nodes.push(...(models.length ? models : [{}]).map(item => facts([
        [t('res_actual'), item.actual_model || t('res_unknown')],
        [t('res_requested'), item.requested_model || t('res_unknown')],
        [t('res_effort'), item.effort || t('res_unknown')],
        [t('res_version'), item.cli_version || t('res_unknown')],
      ])));
      nodes.push(facts([
        ['input', 'cache_read', 'cache_write', 'output', 'reasoning', 'total'].map(key => [t('res_' + key), metricText(row.metrics?.[key])]),
        [t('res_calls'), finite(row.calls) == null ? t('res_unknown') : finite(row.calls).toLocaleString()],
        [t('res_observations'), finite(row.observations) == null ? t('res_unknown') : finite(row.observations).toLocaleString()],
      ].flat()));
    }
    const reason = reasonPresentation(row.status);
    nodes.push(disclosure({
      label: t('res_details'), key: `agents-coverage:${workspaceId}:${row.source_id}`,
      body: h('div', {class: 'pm-agents-scope'},
        isReadable(row) ? h('p', {}, t(row.adapter === 'codex-rollout-v1' ? 'res_codex_units' : 'res_claude_units')) : null,
        h('p', {}, t('res_adapter'), ' ', h('code', {class: 'mono'}, row.adapter || t('res_unknown'))),
        isReadable(row) ? facts([[t('res_first'), clock(row.first_at) === EM ? t('res_unknown') : clock(row.first_at)],
          [t('res_last'), clock(row.last_at) === EM ? t('res_unknown') : clock(row.last_at)],
          [t('res_collected'), clock(row.successful_at) === EM ? t('res_unknown') : clock(row.successful_at)]]) : null,
        h('p', {}, t('res_status'), ' ', t(reason.key), reason.raw ? h('code', {class: 'mono'}, reason.raw) : null),
        row.configurations_partial ? h('p', {}, t('res_configurations_more')) : null),
    }));
    inspectorPanel.body.replaceChildren(...nodes);
  }
  function renderInspector() {
    const row = data?.rows?.find(item => item.source_id === selected);
    if (!row) {clearInspector(); return;}
    if (inspectorPanel?.isConnected && inspectorPanel.dataset.source === row.source_id) {fillInspector(row); return;}
    clearInspector();
    const panel = inspector({title: row.source_id, onClose() {
      if (closing) return;
      const id = selected;
      selected = null; preference.selected = null; inspectorPanel = null;
      signature = currentSignature();
      const opener = stageRow(id);
      if (opener) opener.setAttribute('aria-selected', 'false');
    }});
    panel.dataset.resourceDetail = '';
    panel.dataset.source = row.source_id;
    inspectorPanel = panel;
    fillInspector(row);
    layout.append(panel);
  }
  function stageRow(id) {return layout.querySelector(`[data-resource-row="${cssEscape(id)}"]`);}
  function clearInspector() {
    if (!inspectorPanel) return;
    const panel = inspectorPanel;
    inspectorPanel = null;
    closing = true;
    panel.close();
    closing = false;
  }
  function stableRow(row) {
    const {checked_at, successful_at, as_of, ...rest} = row || {};
    return rest;
  }
  function trendKey(buckets) {
    const stacked = stackDailyBuckets(buckets, new Date());
    if (!stacked) return '';
    return JSON.stringify([stacked.tz, stacked.days.map(day => [day.date, day.mark, day.partial, day.parts.map(part => part.total)])]);
  }
  function currentSignature(visible = visibleRows()) {
    return JSON.stringify([visible.map(stableRow), selected, filter, period, trendKey(data?.buckets),
      (data?.quota_pools || []).map(pool => ({...pool, snapshot: pool.snapshot ? {...pool.snapshot, observed_at: undefined} : pool.snapshot}))]);
  }
  function render(force = false) {
    if (!data) return;
    updateMeta();
    const visible = visibleRows();
    if (selected && !visible.some(row => row.source_id === selected)) selected = null;
    preference.selected = selected;
    const next = currentSignature(visible);
    if (!force && next === signature) return;
    signature = next;
    paint(visible);
  }
  function schedule() {
    clearTimeout(timer);
    if (active && !paused) timer = setTimeout(() => void refresh({trend: false}), Math.max(10000, Number(data?.interval_s || 60) * 1000));
  }
  function withdraw({preserveSelection = false} = {}) {
    serial++; busy = false; data = null; signature = ''; trendBuckets = null;
    if (!preserveSelection) selected = null;
    clearInspector();
    main.replaceChildren();
    meta.textContent = '';
    status.replaceChildren();
    refreshButton.disabled = false;
  }
  function savePreference() {
    const scroller = main.querySelector('.ui-data-table');
    Object.assign(preference, {selected, paused, filter, period, scroll: scroller ? scroller.scrollLeft : preference.scroll || 0});
  }
  async function refresh({trend = false} = {}) {
    if (!active) return;
    const ticket = ++serial;
    busy = true; refreshButton.disabled = true;
    if (!data) main.replaceChildren(skeleton(4));
    const until = new Date(), since = new Date(until.getTime() - Number(period) * 86400000);
    const params = new URLSearchParams({since: since.toISOString(), until: until.toISOString()});
    let sentTz = '';
    if (trend) {params.set('buckets', 'day'); sentTz = browserTimeZone(); params.set('tz', sentTz);}
    const path = `/pm/workspaces/${encodeURIComponent(workspaceId)}/resources?`;
    try {
      let result;
      try {result = await api('GET', path + params);}
      catch (error) {
        // A rejected zone is retried once as UTC. The next user request may try the browser zone again.
        if (!(trend && error?.status === 422 && error?.code === 'INVALID_PARAMS' && sentTz !== 'UTC')) throw error;
        params.set('tz', 'UTC');
        result = await api('GET', path + params);
      }
      alive(); if (ticket !== serial) return;
      if (result?.workspace_id !== workspaceId || !Array.isArray(result.rows) || !Array.isArray(result.quota_pools)) throw Error(t('pm_scope_mismatch'));
      if (trend) trendBuckets = isBucketPayload(result.buckets) ? result.buckets : null;
      else if (trendBuckets) result = {...result, buckets: trendBuckets};
      data = result;
      render();
    } catch (error) {
      try {alive(); if (ticket !== serial) return;} catch {return;}
      withdraw(); status.replaceChildren(errorBox(error));
    } finally {
      if (ticket === serial || !data) {busy = false; refreshButton.disabled = false; schedule();}
    }
  }
  return {element, refresh, withdraw, setActive(value) {
    active = value; clearTimeout(timer);
    if (active) {if (!busy) void refresh({trend: true});}
    else {serial++; busy = false; refreshButton.disabled = false;}
  }, dispose() {savePreference(); active = false; clearTimeout(timer); withdraw();}};
}

function savedFilter(value) {
  if (value && typeof value === 'object' && ['all', 'cli', 'role', 'model'].includes(value.kind)) return {kind: value.kind, value: value.value || ''};
  return {kind: 'all', value: ''};
}
