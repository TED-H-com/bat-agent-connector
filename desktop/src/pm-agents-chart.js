// Stacked daily bars. Dates and the y scale are HTML so the plot never stretches text.
import {disclosure} from './ui/components.js';
import {cliClass, cliName, stackDailyBuckets} from './pm-agents-trend.js';

const SVG = 'http://www.w3.org/2000/svg';
const liveCharts = new Set();

function svgEl(tag, attrs) {
  const el = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value == null || value === false) continue;
    el.setAttribute(key, value === true ? '' : String(value));
  }
  return el;
}

function dayState(day, t) {
  const parts = [];
  if (day.mark === 'in_progress') parts.push(t('ui_status_in_progress'));
  if (day.mark === 'settling') parts.push(t('res_settling'));
  if (day.partial) parts.push(t('ui_status_partial'));
  return parts.join(' · ');
}

function labelEvery(count, slot) {
  const fitted = Math.max(1, Math.ceil(32 / Math.max(slot, 1)));
  let step = 1;
  if (count > 12) step = 3;
  if (count > 24) step = 7;
  if (count > 60) step = 14;
  return Math.max(step, fitted);
}

function niceMax(value) {
  if (!(value > 0)) return 0;
  if (value < 1000) {
    const step = 10 ** Math.floor(Math.log10(value));
    return Math.ceil(value / step) * step;
  }
  return Math.ceil(value / 100) * 100;
}

function axisLabel(value) {
  if (value >= 1000) {
    const thousands = value / 1000;
    const rounded = Math.round(thousands * 10) / 10;
    const text = Math.abs(rounded - Math.round(rounded)) < 1e-6 ? String(Math.round(rounded)) : rounded.toFixed(1);
    return text + 'k';
  }
  return String(Math.round(value));
}

function partText(part, t) {
  return part.total == null ? `${cliName(part.cli)} · ${t('res_no_data')}` : `${cliName(part.cli)} ${part.total.toLocaleString()}`;
}

export function usageChart({h, t, buckets, now = new Date(), exactKey} = {}) {
  const stacked = stackDailyBuckets(buckets, now);
  if (!stacked || !stacked.days.length) return null;
  const {clis, days} = stacked;
  const shown = clis.filter(cli => days.some(day => day.parts.some(part => part.cli === cli && part.total != null)));
  const markerLane = 14;
  const plotH = 112;
  const topLine = markerLane;
  const baseline = topLine + plotH;
  const height = baseline + 1;
  const usable = plotH - 2;
  const peak = days.reduce((best, day) => Math.max(best, day.parts.reduce((sum, part) => sum + (part.total > 0 ? part.total : 0), 0)), 0);
  const scaleMax = niceMax(peak);
  const heightOf = value => scaleMax > 0 ? (value / scaleMax) * usable : 0;
  const host = h('figure', {class: 'pm-agents-chart', 'data-resource-chart': ''});
  host.style.setProperty('--plot-h', height + 'px');
  host.style.setProperty('--line-top', topLine + 'px');
  host.style.setProperty('--line-base', baseline + 'px');
  const yscale = h('div', {class: 'pm-agents-yscale'});
  const svg = svgEl('svg', {role: 'img', 'aria-label': t('res_trend'), preserveAspectRatio: 'xMinYMin meet'});
  const labels = h('div', {class: 'pm-agents-xlabels'});
  const plot = h('div', {class: 'pm-agents-plot-main'}, svg, labels);
  const tip = h('div', {class: 'pm-agents-tip', role: 'tooltip', hidden: true});
  let drawn = 0;

  function show(day, event) {
    tip.hidden = false;
    tip.replaceChildren(...[
      h('div', {}, day.date),
      dayState(day, t) ? h('div', {}, dayState(day, t)) : null,
      ...day.parts.map(part => h('div', {'data-tip-cli': part.cli}, partText(part, t))),
    ].filter(Boolean));
    const bounds = host.getBoundingClientRect();
    const point = Number.isFinite(event?.clientX) ? event.clientX : bounds.left + bounds.width / 2;
    const tipWidth = tip.offsetWidth || 160;
    const left = Math.min(Math.max(8, point - bounds.left), Math.max(8, bounds.width - tipWidth - 8));
    tip.style.left = left + 'px';
    tip.style.top = '4px';
  }

  function draw(width) {
    const slot = width / days.length;
    const barW = Math.max(2, Math.min(slot * 0.62, Math.max(2, slot - 4)));
    const step = labelEvery(days.length, slot);
    const maxText = scaleMax > 0 ? axisLabel(scaleMax) : '';
    yscale.replaceChildren(...[
      h('span', {class: 'sizer', 'aria-hidden': 'true'}, maxText || '0'),
      maxText ? h('span', {class: 'max'}, maxText) : null,
      h('span', {class: 'zero'}, '0'),
    ].filter(Boolean));
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    svg.setAttribute('width', String(width));
    svg.setAttribute('height', String(height));
    svg.style.width = width + 'px';
    svg.style.height = height + 'px';
    svg.replaceChildren(svgEl('line', {class: 'grid zero', x1: 0, x2: width, y1: baseline, y2: baseline}));
    if (scaleMax > 0) svg.append(svgEl('line', {class: 'grid max', x1: 0, x2: width, y1: topLine, y2: topLine}));
    labels.replaceChildren(...days.map((day, index) => {
      const showLabel = index === days.length - 1 || (index % step === 0 && days.length - 1 - index >= step);
      const [, month, date] = day.date.split('-');
      return h('span', {}, showLabel ? `${Number(month)}/${Number(date)}` : '');
    }));
    days.forEach((day, index) => {
      const x0 = index * slot;
      const x = x0 + (slot - barW) / 2;
      const gaps = day.parts.filter(part => part.total == null).map(part => part.cli);
      const zeros = day.parts.filter(part => part.total === 0).map(part => part.cli);
      const group = svgEl('g', {
        class: 'day', tabindex: '-1', 'data-date': day.date,
        'data-incomplete': day.mark || null, 'data-partial': day.partial ? 'true' : null,
        'data-gap': gaps.join(' ') || null, 'data-zero': zeros.join(' ') || null,
        'aria-label': [day.date, dayState(day, t), ...day.parts.map(part => partText(part, t))].filter(Boolean).join(' · '),
      });
      if (gaps.length) group.append(svgEl('rect', {class: 'gap', x: x0 + 1, y: baseline - 9, width: Math.max(2, slot - 2), height: 8}));
      let y = baseline - 1;
      for (const part of day.parts) {
        if (part.total == null) continue;
        const tone = `cli-${cliClass(part.cli)}`;
        const open = day.mark ? ' open' : '';
        if (part.total === 0) {
          group.append(svgEl('rect', {class: `tick ${tone}${open}`, x, y: baseline - 2, width: barW, height: 2}));
          continue;
        }
        const barH = Math.max(1, heightOf(part.total));
        y -= barH;
        group.append(svgEl('rect', {class: `bar ${tone}${open}`, x, y, width: barW, height: barH}));
      }
      if (day.mark) {
        const cx = x + barW / 2;
        const markerBottom = y - 3;
        if (day.mark === 'settling') group.append(svgEl('rect', {class: 'marker settling', x: cx - 3, y: markerBottom - 6, width: 6, height: 6, rx: 1}));
        else group.append(svgEl('circle', {class: 'marker in_progress', cx, cy: markerBottom - 3, r: 3}));
      }
      group.append(svgEl('rect', {class: 'band', x: x0, y: 0, width: slot, height}));
      group.addEventListener('pointerenter', event => show(day, event));
      group.addEventListener('focus', event => show(day, event));
      svg.append(group);
    });
  }

  function layout() {
    if (!host.isConnected) return;
    const width = Math.round(plot.clientWidth);
    if (width < 8 || width === drawn) return;
    drawn = width;
    draw(width);
  }
  const observer = new ResizeObserver(() => layout());
  observer.observe(plot);
  const tracker = {host, observer};
  liveCharts.add(tracker);
  queueMicrotask(() => {
    for (const item of [...liveCharts]) {
      if (!item.host.isConnected) {item.observer.disconnect(); liveCharts.delete(item);}
    }
    layout();
  });

  svg.addEventListener('pointerleave', () => {tip.hidden = true;});
  const legend = h('ul', {class: 'pm-agents-legend'},
    ...shown.map(cli => h('li', {}, h('span', {class: `swatch cli-${cliClass(cli)}`, 'aria-hidden': 'true'}), cliName(cli))),
    days.some(day => day.mark === 'in_progress') ? h('li', {}, h('span', {class: 'swatch marker in_progress', 'aria-hidden': 'true'}), t('ui_status_in_progress')) : null,
    days.some(day => day.mark === 'settling') ? h('li', {}, h('span', {class: 'swatch marker settling', 'aria-hidden': 'true'}), t('res_settling')) : null,
    days.some(day => day.parts.some(part => part.total == null)) ? h('li', {}, h('span', {class: 'swatch gap', 'aria-hidden': 'true'}), t('res_no_data')) : null);
  const table = h('table', {},
    h('thead', {}, h('tr', {}, h('th', {scope: 'col'}, t('res_seen')), ...clis.map(cli => h('th', {scope: 'col'}, cliName(cli))), h('th', {scope: 'col'}, t('res_status')))),
    h('tbody', {}, ...days.map(day => {
      const gaps = day.parts.filter(part => part.total == null).map(part => part.cli);
      const zeros = day.parts.filter(part => part.total === 0).map(part => part.cli);
      return h('tr', {
        'data-date': day.date, 'data-incomplete': day.mark || null, 'data-partial': day.partial ? 'true' : null,
        'data-gap': gaps.join(' ') || null, 'data-zero': zeros.join(' ') || null,
      }, h('td', {}, day.date),
        ...day.parts.map(part => h('td', {'data-cli': part.cli}, part.total == null ? '—' : part.total.toLocaleString())),
        h('td', {}, dayState(day, t)));
    })));
  host.append(h('div', {class: 'pm-agents-plot'}, yscale, plot), legend, tip,
    disclosure({label: t('res_exact'), body: h('div', {class: 'pm-agents-exact'}, table), key: exactKey}));
  return host;
}
