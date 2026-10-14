import {locale} from '../i18n.js';
import {h} from './dom.js';
import {axisDate, calendarDate, compactDate, compactTime, fullTime} from './time.js';

// Central timestamps are epoch seconds; Date objects and ISO strings are instants.
const instant = (value, milliseconds) => typeof value === 'number' ? new Date(value * (milliseconds ? 1 : 1000)) : value;
export const timeText = (value, options = {}) => (options.dateOnly || calendarDate(value) ? compactDate : compactTime)(instant(value, options.milliseconds), {locale, ...options});
export const axisDateText = (value, options = {}) => axisDate(value, {locale, ...options});
export function timeStamp(value, options = {}) {
  const date = instant(value, options.milliseconds), text = timeText(value, options);
  if (!text) return options.empty ?? '—';
  const day = calendarDate(date), parsed = day || new Date(date);
  return h('time', {datetime: day ? date : parsed.toISOString(), title: fullTime(parsed, {locale}),
    'data-ui-time': options.dateOnly || day ? 'date' : 'instant', ...options.attrs}, text);
}
// Preserve translated word order while placing stamps in text without HTML parsing.
export function timeLabel(t, key, vars) {
  const nodes = [], tokens = {};
  for (const [name, value] of Object.entries(vars)) {
    if (value instanceof Node) {tokens[name] = `\uFFF0${nodes.length}\uFFF1`; nodes.push(value);}
    else tokens[name] = value;
  }
  const fragment = document.createDocumentFragment();
  for (const part of t(key, tokens).split(/(\uFFF0\d+\uFFF1)/)) {
    const match = /^\uFFF0(\d+)\uFFF1$/.exec(part);
    fragment.append(match ? nodes[Number(match[1])] : document.createTextNode(part));
  }
  return fragment;
}
export function refreshTimeStamps(root = document, now = Date.now()) {
  for (const node of root.querySelectorAll('time[data-ui-time]')) {
    const text = (node.dataset.uiTime === 'date' ? compactDate : compactTime)(node.dateTime, {locale, now});
    if (!text || node.textContent === text) continue;
    // timeStamp creates one text node. Retain it so a selection spanning this
    // stamp stays attached; restore endpoints inside the changed text as well.
    const content = node.firstChild, selection = document.getSelection();
    if (content?.nodeType !== Node.TEXT_NODE) continue;
    const anchor = selection?.anchorNode, focus = selection?.focusNode;
    const anchorOffset = selection?.anchorOffset, focusOffset = selection?.focusOffset;
    const selected = anchor === content || focus === content, length = content.length;
    content.data = text;
    if (selected) {
      const offset = (target, value) => target !== content ? value
        : value === length ? text.length : Math.min(value, text.length);
      selection.setBaseAndExtent(anchor, offset(anchor, anchorOffset), focus, offset(focus, focusOffset));
    }
  }
}
let started = false;
export function startTimeTicker() {
  if (started) return;
  started = true;
  let timer;
  const tick = () => {if (!document.hidden) {refreshTimeStamps(); timer = setTimeout(tick, 60000);}};
  document.addEventListener('visibilitychange', () => {clearTimeout(timer); tick();});
  tick();
}
