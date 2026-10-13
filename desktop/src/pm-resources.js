import {locale} from './i18n.js';
import {timeStamp, timeLabel} from './ui/stamps.js';
import {viewState} from './ui/view-state.js';
import {discussLink, agentMark, button, dataTable, disclosure, emptyState, inspector, metric, sectionCard, segmented, skeleton, statusPill} from './ui/components.js';
import {icon} from './ui/icons.js';
import {reasonLabel, reasonLabels} from './ui/ui-strings.js';
import {discussHref} from './pm-discuss-link.js';
import {usageChart} from './pm-agents-chart.js';
import {browserTimeZone, cliClass, cliName, dayKey, isBucketPayload, periodWindow, stackDailyBuckets} from './pm-agents-trend.js';

const PERIODS = ['1', '7', '30', '90'];
const READABLE = new Set(['current', 'catching_up', 'stale']);
const TREND_TTL = 15 * 60 * 1000;
const EM = '—';
const STATUS_KEY = {
  current: 'res_current', catching_up: 'res_catching_up', pending: 'res_pending',
  source_unavailable: 'res_unavailable', unavailable: 'res_unavailable',
  error: 'res_error', binding_changed: 'res_error',
};
const STATUS_TONE = {
  current: 'ok', catching_up: 'info', pending: 'warn', source_unavailable: 'neutral', unavailable: 'neutral',
  error: 'bad', binding_changed: 'warn',
};
const SOURCE_KIND = {
  catching_up: 'catching_up', stale: 'stale', pending: 'pending',
  source_unavailable: 'unavailable', unavailable: 'unavailable',
  error: 'error', binding_changed: 'error',
};

// Reads the central collector projection. Opening the view never scans logs or calls a model.
export function workspaceResources({h, t, api, alive, namespace, workspaceId, errorBox, bucketsSupported = false}) {
  const preference = viewState(namespace, workspaceId, 'agents');
  let period = PERIODS.includes(preference.period) ? preference.period : '7';
  let filter = savedFilter(preference.filter);
  let serial = 0, active = false, busy = false, paused = Boolean(preference.paused), timer = null;
  let data = null, selected = preference.selected || null, signature = '';
  let trendBuckets = null, trendReadAt = 0, trendSourceKey = '', trendUntilDay = '';
  let bucketsStopped = false, requestZone = browserTimeZone();
  let inspectorPanel = null, closing = false, chartNode = null, chartTrendKey = '';
  const meta = h('p', {class: 'ui-meta', 'data-resource-meta': ''});
  const status = h('div', {role: 'status'});
  const main = h('div', {class: 'pm-agents-main'});
  const layout = h('div', {class: 'pm-agents-layout'}, main);
  const periodControl = segmented({
    label: t('res_window'), value: period,
    options: PERIODS.map(value => ({value, label: t('res_period_' + value)})),
    onChange(value) {
      period = value; preference.period = period;
      clearTimeout(timer);
      serial++; busy = false;
      trendBuckets = null; trendReadAt = 0; trendSourceKey = ''; trendUntilDay = '';
      data = null; signature = '';
      dropChart();
      main.replaceChildren(skeleton(4));
      void refresh({user: true, trend: bucketsSupported && !bucketsStopped});
    },
  });
  const pause = button({variant: 'ghost', size: 'sm', onclick: () => {
    paused = !paused; preference.paused = paused;
    paintPause();
    if (paused) {clearTimeout(timer); if (data) updateMeta();}
    else void refresh({user: true, trend: wantsTrend()});
  }});
  function paintPause() {
    const label = t(paused ? 'res_resume' : 'res_pause');
    pause.replaceChildren(icon(paused ? 'play' : 'pause'), label);
    pause.removeAttribute('aria-pressed');
    pause.setAttribute('aria-label', label);
  }
  paintPause();
  const listMedia = window.matchMedia('(max-width: 799px)');
  function onListChange() {if (active && data) render(true);}
  listMedia.addEventListener('change', onListChange);
  const toolbar = h('div', {class: 'pm-agents-toolbar'}, periodControl, pause);
  const element = h('section', {'data-pm-resources': '', class: 'pm-agents'}, toolbar, status, layout);

  function finite(value) {return typeof value === 'number' && Number.isFinite(value) ? value : null;}
  function cssEscape(value) {return window.CSS?.escape ? CSS.escape(String(value)) : String(value).replace(/"/g, '\\"');}
  function cliOf(row) {
    const adapter = typeof row?.adapter === 'string' ? row.adapter : '';
    return adapter.split('-')[0] || '';
  }
  function isReadable(row) {return READABLE.has(row?.status) && Boolean(row?.metrics);}
  const clock = value => timeStamp(value, {empty:EM});
  function seenCell(row) {return isReadable(row) ? clock(row.last_at) : EM;}
  function roleLabel(role) {return role === 'unassigned' ? t('wb_unassigned') : (role || EM);}
  function effortLabel(value) {
    if (value === 'low' || value === 'medium' || value === 'high') return t('res_effort_' + value);
    return value || t('res_unknown');
  }
  function statusText(code) {
    if (code === 'stale' || code === 'unknown' || code === 'partial' || code === 'unconfirmed') return t('ui_status_' + code);
    if (STATUS_KEY[code]) return t(STATUS_KEY[code]);
    return reasonBits(code).text;
  }
  function statusNode(row) {
    const code = row?.status || 'unknown';
    if (code === 'stale' || code === 'unknown' || code === 'partial' || code === 'unconfirmed') return statusPill(code);
    if (STATUS_KEY[code]) return h('span', {class: `ui-pill tone-${STATUS_TONE[code] || 'neutral'}`, 'data-status': code}, statusText(code));
    return h('span', {class: 'ui-pill tone-bad', 'data-status': code}, statusText(code));
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
    render(true);
  }
  function filterSelect() {
    const select = h('select', {'data-resource-filter': '', 'aria-label': t('res_filter')});
    select.append(h('option', {value: 'all'}, t('res_filter_all')));
    for (const [kind, label] of [['cli', t('res_filter_group_cli')], ['role', t('res_filter_group_role')], ['model', t('res_filter_group_model')]]) {
      const options = filterOptions().filter(option => option.kind === kind);
      if (!options.length) continue;
      const group = h('optgroup', {label});
      for (const option of options) group.append(h('option', {value: option.kind + '\u0000' + option.value}, option.label));
      select.append(group);
    }
    const current = filter.kind === 'all' || !filter.value ? 'all' : filter.kind + '\u0000' + filter.value;
    select.value = [...select.options].some(option => option.value === current) ? current : 'all';
    select.addEventListener('change', () => {
      if (select.value === 'all') setFilter({kind: 'all', value: ''});
      else {
        const split = select.value.indexOf('\u0000');
        setFilter({kind: select.value.slice(0, split), value: select.value.slice(split + 1)});
      }
    });
    return h('div', {class: 'pm-agents-list-tools'}, select);
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
  function partText(metricValue) {
    if (finite(metricValue?.value) != null) return finite(metricValue.value).toLocaleString();
    if (finite(metricValue?.observed) != null) return t('res_partial', {count: finite(metricValue.observed).toLocaleString()});
    return t('res_not_reported');
  }
  function tokenHeadline(row) {
    const total = row.metrics?.total;
    if (finite(total?.value) != null) return finite(total.value).toLocaleString();
    if (finite(total?.observed) != null) return t('res_partial', {count: finite(total.observed).toLocaleString()});
    return EM;
  }
  function tokenBreakdown(row) {
    const metrics = row.metrics || {};
    return [[t('res_input'), partText(metrics.input)], [t('res_cache_read'), partText(metrics.cache_read)],
      [t('res_cache_write'), partText(metrics.cache_write)], [t('res_output'), partText(metrics.output)],
      [t('res_reasoning'), partText(metrics.reasoning)]];
  }
  function tokenTitle(row) {return tokenBreakdown(row).map(([label, value]) => `${label} ${value}`).join(' · ');}
  function tokensCell(row) {
    if (!isReadable(row)) return EM;
    return h('span', {class: 'tabular', title: tokenTitle(row)}, tokenHeadline(row));
  }
  function modelName(row) {
    const first = (row.configurations || [])[0];
    if (!first) return t('res_unknown');
    return first.actual_model || first.requested_model || t('res_unknown');
  }
  function sourceCell(row) {
    const cli = cliOf(row);
    return h('div', {class: 'pm-agents-source'},
      isReadable(row) && cli ? agentMark(cli) : null,
      h('div', {class: 'pm-agents-source-id'}, row.source_id,
        isReadable(row) && cli ? h('div', {class: 'ui-meta'}, cliName(cli)) : null));
  }
  function metaZone() {
    return (isBucketPayload(data?.buckets) && data.buckets.tz) || requestZone || 'UTC';
  }
  function updateMeta() {
    if (!data) {meta.replaceChildren(); meta.hidden = true; mountMeta(); return;}
    if (paused) {
      const time = clock(data.as_of);
      meta.replaceChildren(timeLabel(t, 'res_paused_meta', {time}));
      meta.hidden = false;
      mountMeta();
      return;
    }
    const zone = metaZone();
    let start = '', end = '';
    const since = Date.parse(data.window?.since), until = Date.parse(data.window?.until);
    if (Number.isFinite(since) && Number.isFinite(until) && until >= since) {
      const first = dayKey(new Date(since), zone), last = dayKey(new Date(until), zone);
      start = timeStamp(first, {dateOnly:true}); end = timeStamp(last, {dateOnly:true});
    }
    const parsed = Date.parse(data.as_of);
    const ago = Number.isFinite(parsed) ? clock(data.as_of) : '';
    if (!(start && end)) {meta.replaceChildren(); meta.hidden = true; mountMeta(); return;}
    meta.replaceChildren(timeLabel(t, ago ? 'res_observed' : 'res_observed_range', {start, end, zone:h('span', {'data-tz':zone}, zone), ago}));
    meta.hidden = false;
    mountMeta();
  }
  function mountMeta() {
    if (!active) {meta.remove(); return;}
    const heading = element.closest('.pm-main')?.querySelector('.ui-page-heading');
    if (heading && meta.parentNode !== heading) heading.append(meta);
  }
  function needsPartial(row) {
    if (row?.status === 'pending' || row?.status === 'binding_changed' || row?.status === 'catching_up' || row?.status === 'stale') return true;
    if (!isReadable(row)) return true;
    return finite(row.metrics?.total?.value) == null;
  }
  function tokenMetric() {
    const rows = data?.rows || [];
    const readable = rows.filter(isReadable);
    const known = readable.map(row => finite(row.metrics?.total?.value)).filter(value => value != null);
    const partial = rows.some(needsPartial);
    if (!known.length) return metric({label: t('res_tokens'), value: readable.length ? EM : null, pill: readable.length && partial ? statusPill('partial') : null});
    return metric({label: t('res_tokens'), value: known.reduce((sum, value) => sum + value, 0).toLocaleString(), pill: partial ? statusPill('partial') : null});
  }
  function sourcesContext(rows) {
    const counts = new Map();
    for (const row of rows) {
      if (row?.status === 'current') continue;
      const kind = SOURCE_KIND[row?.status] || 'unknown';
      counts.set(kind, (counts.get(kind) || 0) + 1);
    }
    if (!counts.size) return '';
    return [...counts].map(([kind, count]) => t('res_src_' + kind, {count})).join(' · ');
  }
  function sourcesMetric() {
    const rows = data?.rows || [];
    const current = rows.filter(row => row.status === 'current').length;
    const context = sourcesContext(rows);
    return metric({label: t('res_sources'), value: t('res_sources_value', {current, total: rows.length}), context: context || null});
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
    return metric({label: t('res_next_reset'), value: earliest == null ? null : timeStamp(earliest, {milliseconds:true})});
  }
  function quotaNodes() {
    const ids = new Set((data?.rows || []).map(row => row.source_id));
    const blocks = [];
    for (const pool of data?.quota_pools || []) {
      if (!pool?.sources?.some(id => ids.has(id))) continue;
      const windows = pool.status === 'reported' && pool.snapshot?.windows?.length ? pool.snapshot.windows : [null];
      for (const item of windows) blocks.push(quotaBlock(pool, item));
    }
    if (!blocks.length) return [h('p', {class: 'ui-meta'}, t('res_quota_unknown'))];
    return [sectionCard({title: t('res_quota_heading'), body: h('div', {class: 'pm-agents-quotas'}, ...blocks)})];
  }
  function formatPercent(value) {
    const digits = Math.abs(value) > 0 && Math.abs(value) < 10 ? 1 : 0;
    return new Intl.NumberFormat(locale, {minimumFractionDigits: digits, maximumFractionDigits: digits}).format(value) + '%';
  }
  function windowLabel(minutes) {
    const value = finite(minutes);
    if (value == null) return '';
    if (value >= 1440 && value % 1440 === 0) return t('res_window_days', {days: value / 1440});
    if (value >= 60 && value % 60 === 0) return t('res_window_hours', {hours: value / 60});
    return t('res_window_short', {minutes: value});
  }
  function poolLabel(pool) {
    const rows = (pool?.sources || []).map(id => (data?.rows || []).find(row => row.source_id === id)).filter(Boolean);
    const names = [...new Set(rows.filter(isReadable).map(row => cliClass(cliOf(row)) === cliOf(row) ? cliName(cliOf(row)) : '').filter(Boolean))];
    if (names.length === 1) return names[0];
    if (rows.length === 1) return rows[0].source_id;
    const only = (pool?.sources || []).filter(Boolean);
    return only.length === 1 ? only[0] : EM;
  }
  function quotaBlock(pool, item) {
    const used = item ? finite(item.used_percent) : null;
    const confirmed = pool.status === 'reported' && item && used != null;
    const percent = confirmed ? formatPercent(used) : '';
    const usedText = confirmed ? t('res_used') + ' ' + percent : '';
    const bar = h('div', {class: 'pm-agents-used', role: 'img', 'aria-label': confirmed ? usedText : t('ui_status_unconfirmed')}, h('span'));
    if (confirmed) bar.querySelector('span').style.width = Math.max(0, Math.min(100, used)) + '%';
    const windowText = windowLabel(item?.window_minutes);
    let reset = '';
    if (finite(item?.resets_at) != null) {
      const at = item.resets_at * 1000;
      reset = timeLabel(t, 'res_resets_in', {when: timeStamp(at, {milliseconds:true})});
    }
    const details = disclosure({label: t('res_details'), key: `agents-pool:${workspaceId}:${pool.pool_id || poolLabel(pool)}:${item?.window || 'none'}`,
      body: h('p', {class: 'pm-agents-pool-id'}, h('code', {class: 'mono'}, pool.pool_id || EM))});
    return h('article', {class: 'pm-agents-quota', 'data-resource-pool': pool.pool_id || ''},
      h('div', {class: 'pm-agents-quota-head'},
        h('div', {}, h('div', {class: 'pm-agents-pool-name'}, poolLabel(pool)), windowText ? h('div', {class: 'ui-meta'}, windowText) : null),
        confirmed ? h('div', {class: 'tabular'}, usedText) : statusPill('unconfirmed')),
      confirmed ? bar : null,
      reset ? h('div', {class: 'ui-meta'}, reset) : null,
      details);
  }
  function choose(id) {
    selected = id; preference.selected = selected; signature = currentSignature();
    for (const node of layout.querySelectorAll('.pm-agents-list-row')) {
      if (node.getAttribute('data-resource-row') === id) node.setAttribute('aria-current', 'true');
      else node.removeAttribute('aria-current');
    }
    renderInspector();
  }
  function listRow(row) {
    const readable = isReadable(row);
    const cli = cliOf(row);
    const on = row.source_id === selected;
    return h('button', {
      type: 'button', class: 'pm-agents-list-row', role: 'listitem',
      'aria-current': on ? 'true' : null,
      'data-resource-row': row.source_id, 'data-resource-select': row.source_id,
      onclick: () => choose(row.source_id),
    }, h('div', {class: 'pm-agents-list-mark'}, readable && cli ? agentMark(cli) : null),
      h('div', {class: 'pm-agents-list-id'}, h('span', {class: 'pm-agents-list-name'}, row.source_id),
        readable && cli ? h('span', {class: 'pm-agents-list-cli'}, cliName(cli)) : null),
      h('div', {class: 'pm-agents-list-tokens tabular', title: readable ? tokenTitle(row) : null}, readable ? tokenHeadline(row) : EM),
      h('div', {class: 'pm-agents-list-meta'}, readable ? `${modelName(row)} · ${roleLabel(row.role)}` : roleLabel(row.role)),
      h('div', {class: 'pm-agents-list-status'}, statusNode(row)));
  }
  function tableFor(visible) {
    if (!data?.rows?.length) return emptyState({title: t('res_empty')});
    if (!visible.length) return h('p', {class: 'ui-meta'}, t('res_no_match'));
    if (listMedia.matches) return h('div', {class: 'pm-agents-list', role: 'list'}, ...visible.map(listRow));
    return dataTable({
      rows: visible, rowKey: 'source_id', selected,
      rowAttrs: row => ({'data-resource-row': row.source_id, 'data-resource-select': row.source_id}),
      onSelect(key) {selected = key; preference.selected = selected; signature = currentSignature(); renderInspector();},
      columns: [
        {key:'source', label: t('res_source'), render: sourceCell},
        {key:'model', secondary:true, label: t('res_model_col'), render: modelCell},
        {key:'role', secondary:true, label: t('res_role'), render: row => roleLabel(row.role)},
        {key:'tokens', label: h('span', {title: t('res_tokens_hint')}, t('res_tokens_col')), numeric: true, render: tokensCell},
        {key:'messages', secondary:true, label: t('res_calls_col'), numeric: true, render: row => isReadable(row) && finite(row.calls) != null ? finite(row.calls).toLocaleString() : EM},
        {key:'seen', label: t('res_seen'), render: seenCell},
        {key:'status', label: t('res_status'), render: statusNode},
      ],
    });
  }
  function readableKey(rows) {
    return (rows || []).filter(isReadable).map(row => `${row.source_id}\u0000${row.status}`).sort().join('\n');
  }
  function filteredBuckets() {
    if (!isBucketPayload(trendBuckets)) return null;
    const readable = new Set((data?.rows || []).filter(isReadable).map(row => row.source_id));
    const series = trendBuckets.series.filter(item => item && readable.has(item.source_id));
    if (!series.length) return null;
    return {...trendBuckets, series};
  }
  function trendKey(buckets) {
    const stacked = stackDailyBuckets(buckets, new Date(), data?.window);
    if (!stacked) return '';
    return JSON.stringify([stacked.tz, stacked.days.map(day => [day.date, day.mark, day.partial, day.parts.map(part => [part.cli, part.total])])]);
  }
  function dropChart() {
    const node = chartNode;
    chartNode = null; chartTrendKey = '';
    node?.dispose?.();
  }
  function chartFor() {
    const buckets = filteredBuckets();
    const key = trendKey(buckets);
    if (!key) {dropChart(); return null;}
    if (chartNode && chartTrendKey === key && chartNode.isConnected) return chartNode;
    dropChart();
    chartNode = usageChart({h, t, buckets, now: new Date(), exactKey: `agents-exact:${workspaceId}`, window: data?.window});
    chartTrendKey = key;
    return chartNode;
  }
  function wantsTrend(now = new Date()) {
    if (!bucketsSupported || bucketsStopped) return false;
    if (!trendBuckets) return true;
    if (readableKey(data?.rows) !== trendSourceKey) return true;
    const zone = trendBuckets.tz || requestZone;
    const today = dayKey(now, zone);
    if (trendUntilDay && today > trendUntilDay) return true;
    if (now.getTime() - trendReadAt >= TREND_TTL) return true;
    return false;
  }
  function paint(visible) {
    const activeEl = document.activeElement;
    const focusKey = activeEl?.closest?.('[data-focus-key]')?.getAttribute('data-focus-key') || '';
    const focusRow = activeEl?.closest?.('[data-resource-row]')?.getAttribute('data-resource-row') || '';
    const scroller = main.querySelector('.ui-data-table');
    if (scroller) preference.scroll = scroller.scrollLeft;
    const chart = chartFor();
    main.replaceChildren(...[
      h('div', {class: 'pm-agents-metrics', 'data-resource-metrics': ''}, tokenMetric(), sourcesMetric(), resetMetric()),
      chart ? sectionCard({title: t('res_trend'), body: chart}) : null,
      filterSelect(),
      tableFor(visible),
      h('div', {'data-resource-quota': ''}, ...quotaNodes()),
      disclosure({label: t('res_coverage'), key: `agents-scope:${workspaceId}`, body: h('div', {class: 'pm-agents-scope'},
        coverageLine(data.coverage), h('p', {}, t('res_local')), h('p', {}, t('res_scope')), h('p', {}, t('res_setup')),
        h('p', {}, t('res_quota_scope')), h('p', {}, t('res_forecast')))}),
    ].filter(Boolean));
    if (main.parentNode !== layout) layout.prepend(main);
    const row = selected ? data.rows.find(item => item.source_id === selected) : null;
    if (row && inspectorPanel?.isConnected) {
      inspectorPanel.dataset.source = row.source_id;
      const title = inspectorPanel.querySelector('h2');
      if (title) title.textContent = row.source_id;
      fillInspector(row);
    } else if (row) renderInspector();
    else if (inspectorPanel) clearInspector();
    const table = main.querySelector('.ui-data-table');
    if (table) {
      table.scrollLeft = preference.scroll || 0;
      table.addEventListener('scroll', () => {preference.scroll = table.scrollLeft;}, {passive: true});
    }
    if (focusKey) layout.querySelector(`[data-focus-key="${cssEscape(focusKey)}"]`)?.focus({preventScroll: true});
    else if (focusRow && document.activeElement === document.body) layout.querySelector(`[data-resource-row="${cssEscape(focusRow)}"]`)?.focus({preventScroll: true});
  }
  function reasonBits(code) {
    if (typeof code === 'string' && Object.hasOwn(reasonLabels, code)) return {text: reasonLabel(t, code), raw: ''};
    return {text: t('ui_reason_generic'), raw: typeof code === 'string' ? code : ''};
  }
  function coverageLine(code) {
    const reason = reasonBits(code);
    return h('p', {}, reason.text, reason.raw ? h('code', {class: 'mono'}, reason.raw) : null);
  }
  function fillInspector(row) {
    if (!inspectorPanel || !row) return;
    const nodes = [h('div', {}, statusNode(row)),
      h('p', {}, discussLink({workspaceId, refs:[], title:row.source_id, from:'agents'}, {'data-resource-discuss':''}))];
    if (isReadable(row)) {
      const models = row.configurations || [];
      nodes.push(h('h3', {class: 'pm-agents-group'}, t('res_model_col')));
      nodes.push(...(models.length ? models : [{}]).map(item => facts([
        [t('res_actual'), item.actual_model || t('res_unknown')],
        [t('res_requested'), item.requested_model || t('res_unknown')],
        [t('res_effort'), effortLabel(item.effort)],
        [t('res_version'), item.cli_version || t('res_unknown')],
      ])));
      nodes.push(h('h3', {class: 'pm-agents-group'}, t('res_tokens_col')), facts([
        [t('res_tokens_col'), tokenHeadline(row)],
        ...tokenBreakdown(row),
        [t('res_calls'), finite(row.calls) == null ? t('res_unknown') : finite(row.calls).toLocaleString()],
        [t('res_observations'), finite(row.observations) == null ? t('res_unknown') : finite(row.observations).toLocaleString()],
      ]));
    }
    const reason = reasonBits(row.status);
    const known = Boolean(STATUS_KEY[row.status]) || ['stale', 'unknown', 'partial', 'unconfirmed'].includes(row.status);
    const details = disclosure({
      label: t('res_details'), key: `agents-coverage:${workspaceId}:${row.source_id}`,
      body: h('div', {class: 'pm-agents-scope'},
        isReadable(row) ? h('p', {}, t(row.adapter === 'codex-rollout-v1' ? 'res_codex_units' : 'res_claude_units')) : null,
        h('p', {}, t('res_adapter'), ' ', h('code', {class: 'mono'}, row.adapter || t('res_unknown'))),
        isReadable(row) ? facts([[t('res_first'), clock(row.first_at) === EM ? t('res_unknown') : clock(row.first_at)],
          [t('res_last'), clock(row.last_at) === EM ? t('res_unknown') : clock(row.last_at)],
          [t('res_collected'), clock(row.successful_at) === EM ? t('res_unknown') : clock(row.successful_at)]]) : null,
        h('p', {}, t('res_status'), ' ', statusText(row.status), !known && reason.raw ? h('code', {class: 'mono'}, reason.raw) : null),
        row.configurations_partial ? h('p', {}, t('res_configurations_more')) : null),
    });
    details.querySelector('summary')?.setAttribute('data-focus-key', `agents-coverage:${workspaceId}:${row.source_id}`);
    nodes.push(details);
    inspectorPanel.body.replaceChildren(...nodes);
  }
  function renderInspector() {
    const row = data?.rows?.find(item => item.source_id === selected);
    if (!row) {clearInspector(); return;}
    if (inspectorPanel?.isConnected) {
      inspectorPanel.dataset.source = row.source_id;
      const title = inspectorPanel.querySelector('h2');
      if (title) title.textContent = row.source_id;
      fillInspector(row);
      return;
    }
    const panel = inspector({title: row.source_id, onClose() {
      if (closing) return;
      const id = selected;
      selected = null; preference.selected = null; inspectorPanel = null;
      signature = currentSignature();
      const opener = stageRow(id);
      if (opener?.getAttribute('role') === 'listitem') opener.removeAttribute('aria-current');
      else if (opener) opener.setAttribute('aria-selected', 'false');
      queueMicrotask(() => stageRow(id)?.focus());
    }});
    panel.dataset.resourceDetail = '';
    panel.dataset.source = row.source_id;
    inspectorPanel = panel;
    fillInspector(row);
    layout.append(panel);
  }
  function stageRow(id) {return id ? layout.querySelector(`[data-resource-row="${cssEscape(id)}"]`) : null;}
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
  function currentSignature(visible = visibleRows()) {
    return JSON.stringify([visible.map(stableRow), selected, filter, period, trendKey(filteredBuckets()),
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
    if (active && !paused) timer = setTimeout(() => {
      if (!active || paused || busy) return;
      void refresh({user: false, trend: wantsTrend()});
    }, Math.max(10000, Number(data?.interval_s || 60) * 1000));
  }
  function rememberTrend(result, until, zoneUsed) {
    if (!isBucketPayload(result?.buckets)) {
      trendBuckets = null; trendReadAt = 0; trendSourceKey = ''; trendUntilDay = '';
      return;
    }
    trendBuckets = result.buckets;
    trendReadAt = Date.now();
    trendSourceKey = readableKey(result.rows);
    const untilMs = Date.parse(result.window?.until || until);
    trendUntilDay = dayKey(new Date(Number.isFinite(untilMs) ? untilMs : Date.now()), result.buckets.tz || zoneUsed);
  }
  function withdraw({preserveSelection = false, keepSerial = false} = {}) {
    if (!keepSerial) serial++;
    busy = false; data = null; signature = '';
    trendBuckets = null; trendReadAt = 0; trendSourceKey = ''; trendUntilDay = '';
    clearTimeout(timer);
    dropChart();
    if (!preserveSelection) selected = null;
    clearInspector();
    main.replaceChildren();
    meta.replaceChildren();
    meta.hidden = true;
    status.replaceChildren();
  }
  function savePreference() {
    const scroller = main.querySelector('.ui-data-table');
    Object.assign(preference, {selected, paused, filter, period, scroll: scroller ? scroller.scrollLeft : preference.scroll || 0});
  }
  async function refresh({user = false, trend = false} = {}) {
    if (!active || (paused && !user)) return;
    if (!user && busy) return;
    const ticket = ++serial;
    busy = true;
    clearTimeout(timer);
    if (!data) main.replaceChildren(skeleton(4));
    const until = new Date();
    let zoneUsed = requestZone;
    let asked = Boolean(trend) && bucketsSupported && !bucketsStopped;
    let win = periodWindow(until, Number(period), zoneUsed);
    const path = `/pm/workspaces/${encodeURIComponent(workspaceId)}/resources?`;
    const query = (zone, withBuckets, range) => {
      const params = new URLSearchParams({since: range.since, until: range.until});
      if (withBuckets) {params.set('buckets', 'day'); params.set('tz', zone);}
      return params.toString();
    };
    let follow = false;
    try {
      let result;
      try {result = await api('GET', path + query(zoneUsed, asked, win));}
      catch (error) {
        const zoneReject = asked && error?.status === 422 && error?.code === 'INVALID_PARAMS' && zoneUsed !== 'UTC';
        if (!zoneReject) throw error;
        zoneUsed = 'UTC';
        requestZone = 'UTC';
        win = periodWindow(until, Number(period), 'UTC');
        try {result = await api('GET', path + query('UTC', true, win));}
        catch (again) {
          if (again?.status !== 422) throw again;
          bucketsStopped = true;
          asked = false;
          result = await api('GET', path + query('UTC', false, win));
        }
      }
      alive(); if (ticket !== serial) return;
      if (result?.workspace_id !== workspaceId || !Array.isArray(result.rows) || !Array.isArray(result.quota_pools)) throw Error(t('pm_scope_mismatch'));
      const previousKey = trendSourceKey;
      if (asked) rememberTrend(result, win.until, zoneUsed);
      else if (trendBuckets) result = {...result, buckets: trendBuckets};
      data = result;
      follow = !asked && bucketsSupported && !bucketsStopped && readableKey(result.rows) !== previousKey;
      render();
    } catch (error) {
      try {alive(); if (ticket !== serial) return;} catch {return;}
      withdraw({keepSerial: true});
      status.replaceChildren(errorBox(error));
      follow = false;
    } finally {
      if (ticket !== serial) return;
      busy = false;
      schedule();
      if (follow && active && !paused) void refresh({user: false, trend: true});
    }
  }
  return {element, refresh, withdraw, setActive(value) {
    const opening = Boolean(value) && !active;
    active = value; clearTimeout(timer);
    if (active) {
      if (opening) {bucketsStopped = false; requestZone = browserTimeZone();}
      mountMeta();
      if (data) updateMeta();
      if (paused) return;
      if (!busy) void refresh({user: true, trend: wantsTrend()});
    } else {serial++; busy = false; meta.remove();}
  }, dispose() {savePreference(); active = false; clearTimeout(timer); listMedia.removeEventListener('change', onListChange); meta.remove(); dropChart(); withdraw();}};
}

function savedFilter(value) {
  if (value && typeof value === 'object' && ['all', 'cli', 'role', 'model'].includes(value.kind)) return {kind: value.kind, value: value.value || ''};
  return {kind: 'all', value: ''};
}
