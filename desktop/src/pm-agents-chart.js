// Stacked daily bars. Null totals are gaps; a covered zero is a baseline tick.
import {disclosure} from './ui/components.js';
import {cliClass, cliName, stackDailyBuckets} from './pm-agents-trend.js';

const SVG = 'http://www.w3.org/2000/svg';

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

function slotFor(count) {
  if (count > 48) return 12;
  if (count > 20) return 18;
  return 36;
}

function labelStep(count) {
  if (count > 60) return 14;
  if (count > 24) return 7;
  if (count > 12) return 3;
  return 1;
}

export function usageChart({h, t, buckets, now = new Date(), exactKey} = {}) {
  const stacked = stackDailyBuckets(buckets, now);
  if (!stacked || !stacked.days.length) return null;
  const {tz, clis, days} = stacked;
  const shown = clis.filter(cli => days.some(day => day.parts.some(part => part.cli === cli && part.total != null)));
  const padTop = 18;
  const plotH = 112;
  const padBottom = 22;
  const height = padTop + plotH + padBottom;
  const baseline = padTop + plotH;
  const slot = slotFor(days.length);
  const width = Math.max(days.length * slot, slot);
  const barW = Math.max(2, slot - (slot > 16 ? 10 : 4));
  const max = days.reduce((peak, day) => Math.max(peak, day.parts.reduce((sum, part) => sum + (part.total > 0 ? part.total : 0), 0)), 0);
  const heightOf = value => max > 0 ? (value / max) * plotH : 0;
  const host = h('figure', {class: 'pm-agents-chart', 'data-resource-chart': ''});
  host.style.setProperty('--chart-min', width + 'px');
  const scroll = h('div', {class: 'pm-agents-chart-scroll'});
  const svg = svgEl('svg', {viewBox: `0 0 ${width} ${height}`, preserveAspectRatio: 'none', role: 'img', 'aria-label': t('res_trend')});
  svg.append(svgEl('line', {class: 'grid', x1: 0, x2: width, y1: baseline, y2: baseline}));
  const step = labelStep(days.length);
  const tip = h('div', {class: 'pm-agents-tip', role: 'tooltip', hidden: true});
  function show(day, event) {
    tip.hidden = false;
    tip.replaceChildren(...[
      h('div', {}, day.date),
      dayState(day, t) ? h('div', {}, dayState(day, t)) : null,
      ...day.parts.map(part => h('div', {'data-tip-cli': part.cli}, `${cliName(part.cli)} ${part.total == null ? '—' : part.total.toLocaleString()}`)),
    ].filter(Boolean));
    const bounds = host.getBoundingClientRect();
    const point = Number.isFinite(event?.clientX) ? event.clientX : bounds.left + bounds.width / 2;
    const tipWidth = tip.offsetWidth || 160;
    const left = Math.min(Math.max(8, point - bounds.left), Math.max(8, bounds.width - tipWidth - 8));
    tip.style.left = left + 'px';
    tip.style.top = '4px';
  }
  days.forEach((day, index) => {
    const x0 = index * slot;
    const x = x0 + (slot - barW) / 2;
    const gaps = day.parts.filter(part => part.total == null).map(part => part.cli);
    const zeros = day.parts.filter(part => part.total === 0).map(part => part.cli);
    const group = svgEl('g', {
      class: 'day', tabindex: '-1', 'data-date': day.date,
      'data-incomplete': day.mark || null, 'data-partial': day.partial ? 'true' : null,
      'data-gap': gaps.join(' ') || null, 'data-zero': zeros.join(' ') || null,
      'aria-label': [day.date, dayState(day, t), ...day.parts.map(part => `${cliName(part.cli)} ${part.total == null ? '—' : part.total}`)].filter(Boolean).join(' · '),
    });
    let y = baseline;
    for (const part of day.parts) {
      if (part.total == null) continue;
      const tone = `cli-${cliClass(part.cli)}`;
      if (part.total === 0) {
        group.append(svgEl('rect', {class: `tick ${tone}`, x, y: baseline - 2, width: barW, height: 2}));
        continue;
      }
      const barH = Math.max(1, heightOf(part.total));
      y -= barH;
      group.append(svgEl('rect', {class: `bar ${tone}`, x, y, width: barW, height: barH}));
    }
    if (day.mark) group.append(svgEl('circle', {class: `mark ${day.mark}`, cx: x + barW / 2, cy: 8, r: 2.5}));
    if (day.partial) group.append(svgEl('rect', {class: 'mark partial', x: x + barW / 2 + (day.mark ? 4 : -2), y: day.mark ? 11 : 4, width: 4, height: 4}));
    if (index % step === 0 || index === days.length - 1) {
      const [, month, date] = day.date.split('-');
      const label = svgEl('text', {class: 'axis', x: x0 + slot / 2, y: height - 6, 'text-anchor': 'middle'});
      label.textContent = `${Number(month)}/${Number(date)}`;
      group.append(label);
    }
    group.append(svgEl('rect', {class: 'band', x: x0, y: 0, width: slot, height}));
    group.addEventListener('pointerenter', event => show(day, event));
    group.addEventListener('focus', event => show(day, event));
    svg.append(group);
  });
  svg.addEventListener('pointerleave', () => {tip.hidden = true;});
  scroll.append(svg);
  const legend = h('ul', {class: 'pm-agents-legend'},
    ...shown.map(cli => h('li', {}, h('span', {class: `swatch cli-${cliClass(cli)}`, 'aria-hidden': 'true'}), cliName(cli))),
    days.some(day => day.mark === 'in_progress') ? h('li', {}, h('span', {class: 'swatch mark-open', 'aria-hidden': 'true'}), t('ui_status_in_progress')) : null,
    days.some(day => day.mark === 'settling') ? h('li', {}, h('span', {class: 'swatch mark-settle', 'aria-hidden': 'true'}), t('res_settling')) : null,
    days.some(day => day.partial) ? h('li', {}, h('span', {class: 'swatch mark-partial', 'aria-hidden': 'true'}), t('ui_status_partial')) : null);
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
  host.append(scroll, h('p', {class: 'ui-meta', 'data-tz': tz}, tz), legend, tip,
    disclosure({label: t('res_exact'), body: h('div', {class: 'pm-agents-exact'}, table), key: exactKey}));
  return host;
}
