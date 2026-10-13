import {locale} from './i18n.js';
import {timeStamp, timeText} from './ui/stamps.js';
// Stacked daily bars. Dates and the y scale are HTML so the plot never stretches text.
// Plot geometry matches --plot-h / --line-top / --line-base in pm-agents.css.
import {disclosure} from './ui/components.js';
import {cliClass, cliName, niceCeiling, stackDailyBuckets} from './pm-agents-trend.js';

const SVG = 'http://www.w3.org/2000/svg';
const liveCharts = new Set();
const MARKER_LANE = 14;
const PLOT_H = 112;
const TOP_LINE = MARKER_LANE;
const BASELINE = TOP_LINE + PLOT_H;
const HEIGHT = BASELINE + 1;
const USABLE = PLOT_H - 2;
let hatchSerial = 0;

function svgEl(tag, attrs) {
  const el = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value == null || value === false) continue;
    el.setAttribute(key, value === true ? '' : String(value));
  }
  return el;
}

const axisDate = date => timeStamp(date, {dateOnly:true});

function compact(value) {
  return new Intl.NumberFormat(locale, {notation: 'compact', maximumFractionDigits: 1}).format(value);
}

function dayState(day, t) {
  const parts = [];
  if (day.mark === 'in_progress') parts.push(t('ui_status_in_progress'));
  if (day.mark === 'settling') parts.push(t('res_settling'));
  if (day.mark === 'partial_day') parts.push(t('res_partial_day'));
  if (day.partial && day.mark !== 'partial_day') parts.push(t('ui_status_partial'));
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

function partText(part, t) {
  return part.total == null ? `${cliName(part.cli)} · ${t('res_no_data')}` : `${cliName(part.cli)} ${part.total.toLocaleString()}`;
}

export function usageChart({h, t, buckets, now = new Date(), exactKey, window: bounds, formatDay} = {}) {
  const stacked = stackDailyBuckets(buckets, now, bounds);
  if (!stacked || !stacked.days.length) return null;
  const {clis, days} = stacked;
  const shown = clis.filter(cli => days.some(day => day.parts.some(part => part.cli === cli && part.total != null)));
  const withYear = days[0].date.slice(0, 4) !== days.at(-1).date.slice(0, 4);
  const dateText = date => typeof formatDay === 'function' ? formatDay(date, withYear) : axisDate(date);
  const peak = days.reduce((best, day) => Math.max(best, day.parts.reduce((sum, part) => sum + (part.total > 0 ? part.total : 0), 0)), 0);
  const scaleMax = niceCeiling(peak);
  const heightOf = value => scaleMax > 0 ? (value / scaleMax) * USABLE : 0;
  const hatchId = `agents-hatch-${++hatchSerial}`;
  const host = h('figure', {class: 'pm-agents-chart', 'data-resource-chart': ''});
  const yscale = h('div', {class: 'pm-agents-yscale'});
  const summary = [t('res_trend'), timeText(days[0].date, {dateOnly:true}), timeText(days.at(-1).date, {dateOnly:true}), shown.map(cliName).join(', ')].filter(Boolean).join(', ');
  const svg = svgEl('svg', {role: 'img', 'aria-label': summary, preserveAspectRatio: 'xMinYMin meet'});
  const labels = h('div', {class: 'pm-agents-xlabels'});
  const plot = h('div', {class: 'pm-agents-plot-main'}, svg, labels);
  const tip = h('div', {class: 'pm-agents-tip', role: 'tooltip', hidden: true});
  let drawn = 0;

  function show(day, event) {
    tip.hidden = false;
    tip.replaceChildren(...[
      h('div', {}, dateText(day.date)),
      dayState(day, t) ? h('div', {}, dayState(day, t)) : null,
      ...day.parts.map(part => h('div', {'data-tip-cli': part.cli}, partText(part, t))),
    ].filter(Boolean));
    const boundsBox = host.getBoundingClientRect();
    const point = Number.isFinite(event?.clientX) ? event.clientX : boundsBox.left + boundsBox.width / 2;
    const tipWidth = tip.offsetWidth || 160;
    const left = Math.min(Math.max(8, point - boundsBox.left), Math.max(8, boundsBox.width - tipWidth - 8));
    tip.style.left = left + 'px';
    tip.style.top = '4px';
  }

  function draw(width) {
    const slot = width / days.length;
    const barW = Math.max(2, Math.min(slot * 0.62, Math.max(2, slot - 4)));
    const step = labelEvery(days.length, slot);
    const maxText = scaleMax > 0 ? compact(scaleMax) : '';
    yscale.replaceChildren(...[
      h('span', {class: 'sizer', 'aria-hidden': 'true'}, maxText || '0'),
      maxText ? h('span', {class: 'max'}, maxText) : null,
      h('span', {class: 'zero'}, '0'),
    ].filter(Boolean));
    svg.setAttribute('viewBox', `0 0 ${width} ${HEIGHT}`);
    svg.setAttribute('width', String(width));
    svg.setAttribute('height', String(HEIGHT));
    const pattern = svgEl('pattern', {id: hatchId, width: 6, height: 6, patternUnits: 'userSpaceOnUse'});
    pattern.append(svgEl('path', {d: 'M0 6 L6 0', class: 'open-hatch'}));
    const defs = svgEl('defs');
    defs.append(pattern);
    svg.replaceChildren(defs, svgEl('line', {class: 'grid zero', x1: 0, x2: width, y1: BASELINE, y2: BASELINE}));
    if (scaleMax > 0) svg.append(svgEl('line', {class: 'grid max', x1: 0, x2: width, y1: TOP_LINE, y2: TOP_LINE}));
    const nodes = days.map((day, index) => {
      const showLabel = index === days.length - 1 || (index % step === 0 && days.length - 1 - index >= step);
      return h('span', {'data-date': day.date}, showLabel ? axisDate(day.date) : '');
    });
    labels.replaceChildren(...nodes);
    nodes.forEach((span, index) => {
      if (!span.textContent) {span.hidden = true; return;}
      const center = index * slot + slot / 2;
      const textWidth = span.offsetWidth || 0;
      let left = index === 0 ? 0 : index === days.length - 1 ? width - textWidth : center - textWidth / 2;
      left = Math.max(0, Math.min(left, Math.max(0, width - textWidth)));
      span.style.left = left + 'px';
    });
    days.forEach((day, index) => {
      const x0 = index * slot;
      const x = x0 + (slot - barW) / 2;
      const gaps = day.parts.filter(part => part.total == null).map(part => part.cli);
      const zeros = day.parts.filter(part => part.total === 0).map(part => part.cli);
      const open = day.mark ? ' open' : '';
      const group = svgEl('g', {
        class: 'day', 'data-date': day.date,
        'data-incomplete': day.mark || null, 'data-partial': day.partial ? 'true' : null,
        'data-gap': gaps.join(' ') || null, 'data-zero': zeros.join(' ') || null,
        'aria-label': [timeText(day.date, {dateOnly:true}), dayState(day, t), ...day.parts.map(part => partText(part, t))].filter(Boolean).join(' · '),
      });
      let top = BASELINE - 1;
      let placed = false;
      for (const part of day.parts) {
        const tone = `cli-${cliClass(part.cli)}`;
        if (part.total == null) {
          const stubH = 6;
          if (placed) {
            top -= 1;
            group.append(svgEl('rect', {class: 'seg-gap', x, y: top, width: barW, height: 1}));
          }
          top -= stubH;
          group.append(svgEl('rect', {class: 'gap', x, y: top, width: barW, height: stubH}));
          placed = true;
          continue;
        }
        if (part.total === 0) {
          group.append(svgEl('rect', {class: `tick ${tone}${open}`, x, y: BASELINE - 2, width: barW, height: 2}));
          continue;
        }
        const barH = Math.max(1, heightOf(part.total));
        if (placed) {
          top -= 1;
          group.append(svgEl('rect', {class: 'seg-gap', x, y: top, width: barW, height: 1}));
        }
        top -= barH;
        group.append(svgEl('rect', {class: `bar ${tone}${open}`, x, y: top, width: barW, height: barH}));
        if (day.mark) group.append(svgEl('rect', {class: 'open-hatch-fill', x, y: top, width: barW, height: barH, fill: `url(#${hatchId})`}));
        placed = true;
      }
      if (day.mark) {
        const radius = 3;
        const cx = Math.max(radius, Math.min(x + barW / 2, width - radius));
        if (day.mark === 'in_progress') {
          const cy = Math.max(radius, Math.min(top - 6, HEIGHT - radius));
          group.append(svgEl('circle', {class: 'marker in_progress', cx, cy, r: radius}));
        } else {
          const marker = 6;
          const mx = Math.max(0, Math.min(cx - marker / 2, width - marker));
          const my = Math.max(0, Math.min(top - 9, HEIGHT - marker));
          group.append(svgEl('rect', {class: `marker ${day.mark}`, x: mx, y: my, width: marker, height: marker, rx: 1}));
        }
      }
      group.append(svgEl('rect', {class: 'band', x: x0, y: 0, width: slot, height: HEIGHT}));
      group.addEventListener('pointerenter', event => show(day, event));
      svg.append(group);
    });
  }

  function layout() {
    if (!host.isConnected) {observer.disconnect(); liveCharts.delete(tracker); return;}
    const width = Math.round(plot.clientWidth);
    if (width < 8 || width === drawn) return;
    drawn = width;
    draw(width);
  }
  const observer = new ResizeObserver(() => layout());
  observer.observe(plot);
  const tracker = {host, observer};
  liveCharts.add(tracker);
  function dispose() {
    observer.disconnect();
    liveCharts.delete(tracker);
  }
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
    days.some(day => day.mark === 'partial_day') ? h('li', {}, h('span', {class: 'swatch marker partial_day', 'aria-hidden': 'true'}), t('res_partial_day')) : null,
    days.some(day => day.parts.some(part => part.total == null)) ? h('li', {}, h('span', {class: 'swatch gap', 'aria-hidden': 'true'}), t('res_no_data')) : null);
  const table = h('table', {},
    h('thead', {}, h('tr', {}, h('th', {scope: 'col'}, t('res_date')), ...clis.map(cli => h('th', {scope: 'col'}, cliName(cli))), h('th', {scope: 'col'}, t('res_status')))),
    h('tbody', {}, ...days.map(day => {
      const gaps = day.parts.filter(part => part.total == null).map(part => part.cli);
      const zeros = day.parts.filter(part => part.total === 0).map(part => part.cli);
      return h('tr', {
        'data-date': day.date, 'data-incomplete': day.mark || null, 'data-partial': day.partial ? 'true' : null,
        'data-gap': gaps.join(' ') || null, 'data-zero': zeros.join(' ') || null,
      }, h('td', {}, dateText(day.date)),
        ...day.parts.map(part => h('td', {'data-cli': part.cli}, part.total == null ? '—' : part.total.toLocaleString())),
        h('td', {}, dayState(day, t)));
    })));
  const exact = disclosure({label: t('res_exact'), body: h('div', {class: 'pm-agents-exact'}, table), key: exactKey});
  exact.querySelector('summary')?.setAttribute('data-focus-key', exactKey || 'agents-exact');
  host.append(h('div', {class: 'pm-agents-plot'}, yscale, plot), legend, tip, exact);
  return Object.assign(host, {dispose});
}
