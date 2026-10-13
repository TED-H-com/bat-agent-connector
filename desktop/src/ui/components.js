import {h} from './dom.js';
import {t} from '../i18n.js';
import {icon as drawIcon} from './icons.js';
import {statusVocabulary} from './ui-strings.js';
export {statusVocabulary};
let sequence = 0;
const uid = () => `ui-${++sequence}`;
const nodeIcon = icon => typeof icon === 'string' ? drawIcon(icon) : icon;
export function button({variant = 'secondary', size = 'md', icon, label, onClick, ...attrs} = {}) {
  return h('button', {type: 'button', class: `ui-button ${variant} ui-${size}`, onclick: onClick, ...attrs}, nodeIcon(icon), label);
}
export function iconButton({icon, label, ...attrs}) {
  return button({variant: 'ghost', icon, 'aria-label': label, title: label, ...attrs, class: 'ui-button ghost ui-icon-button'});
}
function choices({options, value, onChange = () => {}, label}, isTabs) {
  const root = h('div', {class: isTabs ? 'ui-tabs' : 'ui-segmented', role: isTabs ? 'tablist' : 'radiogroup', 'aria-label': label});
  const buttons = options.map(option => h('button', {type: 'button', role: isTabs ? 'tab' : 'radio',
    id: option.id || uid(), 'aria-controls': isTabs ? option.panel : null, disabled: option.disabled,
    onclick: () => select(option.value)}, option.label));
  function select(next, notify = true) {
    value = next;
    buttons.forEach((node, i) => {const active = options[i].value === value;
      node.tabIndex = active ? 0 : -1; node.setAttribute(isTabs ? 'aria-selected' : 'aria-checked', String(active));});
    if (notify) onChange(value);
  }
  root.addEventListener('keydown', event => {
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const enabled = buttons.filter(node => !node.disabled), current = enabled.indexOf(document.activeElement);
    const index = event.key === 'Home' ? 0 : event.key === 'End' ? enabled.length - 1
      : (current + (['ArrowLeft', 'ArrowUp'].includes(event.key) ? -1 : 1) + enabled.length) % enabled.length;
    const next = enabled[index]; if (!next) return; next.focus(); next.click();
  });
  root.append(...buttons); select(options.some(o => o.value === value && !o.disabled) ? value : options.find(o => !o.disabled)?.value, false);
  root.setValue = next => select(next, false);
  return root;
}
export const segmented = options => choices(options, false);
export const tabs = options => choices(options, true);
export function statusPill(status) {
  const key = Object.hasOwn(statusVocabulary, status) ? status : 'unknown';
  return h('span', {class: `ui-pill tone-${statusVocabulary[key].tone}`, 'data-status': key}, t('ui_status_' + key));
}
export function pageHeader({title, pill, meta, actions = [], overflow} = {}) {
  return h('header', {class: 'ui-page-header'}, h('div', {class: 'ui-page-heading'},
    h('div', {class: 'ui-title-row'}, h('h1', {}, title), pill), meta ? h('p', {class: 'ui-meta'}, meta) : null),
  h('div', {class: 'ui-header-actions'}, ...actions, overflow));
}
export function sectionCard({title, actions = [], body} = {}) {
  return h('section', {class: 'ui-section-card'}, title ? h('header', {}, h('h2', {}, title), h('div', {class: 'ui-header-actions'}, ...actions)) : null, body);
}
export function metric({label, value, context, bar} = {}) {
  const unknown = value === null || value === undefined || typeof value === 'number' && !Number.isFinite(value);
  const track = bar && !unknown ? h('div', {class: 'ui-metric-bar', 'aria-hidden': 'true'}, ...bar.map(part => {
    const span = h('span', {class: `tone-${['ok', 'review', 'info'].includes(part.tone) ? part.tone : 'neutral'}`});
    span.style.flexGrow = String(Number.isFinite(part.value) && part.value > 0 ? part.value : 0); return span;
  })) : null;
  return h('div', {class: 'ui-metric'}, h('div', {class: 'ui-meta'}, label),
    h('div', {class: 'ui-metric-value'}, unknown ? '—' : value, unknown ? statusPill('unknown') : null), context ? h('div', {class: 'ui-metric-context'}, context) : null, track);
}
export function emptyState({icon, title, text, action}) {
  return h('div', {class: 'ui-empty'}, nodeIcon(icon), h('h2', {}, title), text ? h('p', {}, text) : null, action);
}
export function skeleton(rows = 3) {
  return h('div', {class: 'ui-skeleton', role: 'status', 'aria-label': t('ui_loading')},
    ...Array.from({length: Math.min(100, Math.max(0, rows))}, () => h('div', {'aria-hidden': 'true'})));
}
const disclosures = new Map();
export function disclosure({label, body, open = false, key}) {
  const root = h('details', {class: 'ui-disclosure', open: key && disclosures.has(key) ? disclosures.get(key) : open},
    h('summary', {}, drawIcon('chevron-right'), label), body);
  root.addEventListener('toggle', () => {if (!key || !root.isConnected) return;
    disclosures.delete(key); disclosures.set(key, root.open); if (disclosures.size > 200) disclosures.delete(disclosures.keys().next().value);});
  return root;
}
export function inlineError({error, retry}) {
  return h('div', {class: 'ui-inline-error', role: 'alert'}, drawIcon('circle-alert'), h('span', {}, error?.message || String(error)),
    retry ? button({size: 'sm', variant: 'ghost', label: t('ui_retry'), onClick: retry}) : null);
}
export function contextChip({icon, text, onRemove}) {
  return h('span', {class: 'ui-context-chip'}, nodeIcon(icon), text,
    onRemove ? iconButton({icon: 'x', label: t('ui_remove', {text}), onClick: onRemove}) : null);
}
export function agentMark(cli) {
  const marks = {codex: ['CX', 'Codex'], claude: ['CL', 'Claude'], grok: ['GK', 'Grok'], gemini: ['GM', 'Gemini'], bat: ['BT', 'BAT'], pm: ['PM', 'PM']};
  const [initials, label] = marks[String(cli).toLowerCase()] || ['?', t('ui_status_unknown')];
  return h('span', {class: 'ui-agent-mark', role: 'img', 'aria-label': label, title: label}, initials);
}
export function menu({button: trigger, items}) {
  const panel = h('div', {class: 'ui-menu-panel', role: 'menu', id: uid(), hidden: true});
  const root = h('div', {class: 'ui-menu'}, trigger, panel);
  trigger.setAttribute('aria-haspopup', 'menu'); trigger.setAttribute('aria-controls', panel.id); trigger.setAttribute('aria-expanded', 'false');
  const controls = items.map(item => h(item.href ? 'a' : 'button', {type: item.href ? null : 'button', href: item.href, role: 'menuitem', 'data-menu-key': item.key,
    tabindex: -1, disabled: item.href ? null : item.disabled, 'aria-disabled': item.disabled ? 'true' : null,
    onclick: event => {if (item.disabled) {event.preventDefault(); return;} close(); item.onSelect?.();}}, nodeIcon(item.icon), item.label));
  panel.append(...controls);
  const enabledControls = () => controls.filter(node => node.getAttribute('aria-disabled') !== 'true');
  function close(restore = true) {panel.hidden = true; trigger.setAttribute('aria-expanded', 'false');
    document.removeEventListener('pointerdown', outside);
    if (restore && root.isConnected) trigger.focus();}
  function outside(event) {if (!root.contains(event.target)) close(false);}
  function keys(event) {
    if (event.key === 'Escape') {event.preventDefault(); event.stopPropagation(); close();}
    else if (event.key === 'Tab') close(false);
    else if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
      event.preventDefault(); const enabled = enabledControls(), at = enabled.indexOf(document.activeElement);
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? enabled.length - 1 : (at + (event.key === 'ArrowUp' ? -1 : 1) + enabled.length) % enabled.length;
      enabled[next]?.focus();
    }
  }
  panel.addEventListener('keydown', keys);
  function open(last = false) {panel.hidden = false; trigger.setAttribute('aria-expanded', 'true'); enabledControls().at(last ? -1 : 0)?.focus();
    document.addEventListener('pointerdown', outside);}
  trigger.addEventListener('click', () => panel.hidden ? open() : close());
  trigger.addEventListener('keydown', event => {if (['ArrowDown', 'ArrowUp'].includes(event.key)) {event.preventDefault(); open(event.key === 'ArrowUp');}});
  root.close = close; root.dispose = () => close(false); return root;
}
const focusables = root => [...root.querySelectorAll('a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary, [tabindex="0"]')].filter(node => node.getClientRects().length && !node.closest('[hidden], [inert]'));
export function trapFocus(root, event) {
  if (event.key !== 'Tab') return;
  const nodes = focusables(root), first = nodes[0], last = nodes.at(-1);
  if (!first) {event.preventDefault(); root.focus();}
  else if (event.shiftKey && (document.activeElement === first || !root.contains(document.activeElement))) {event.preventDefault(); last.focus();}
  else if (!event.shiftKey && (document.activeElement === last || !root.contains(document.activeElement))) {event.preventDefault(); first.focus();}
}
export function inspector({title, onClose = () => {}}) {
  const opener = document.activeElement, media = matchMedia('(min-width: 1200px)'), titleId = uid();
  const body = h('div', {class: 'ui-inspector-body'});
  const closeButton = iconButton({icon: 'x', label: t('ui_close'), onClick: close});
  const back = button({variant: 'ghost', icon: 'chevron-left', label: t('ui_back'), onClick: close, class: 'ui-button ghost ui-inspector-back'});
  const root = h('aside', {class: 'ui-inspector', tabindex: -1, 'aria-labelledby': titleId},
    h('header', {}, back, h('h2', {id: titleId}, title), closeButton), body);
  const scrim = h('div', {class: 'ui-inspector-scrim', onclick: close});
  const inerted = [];
  function release() {for (const node of inerted.splice(0)) node.inert = false;}
  function resize() {
    release(); root.setAttribute('role', media.matches ? 'complementary' : 'dialog');
    if (media.matches) {root.removeAttribute('aria-modal'); scrim.remove();}
    else {root.setAttribute('aria-modal', 'true'); root.before(scrim);
      // Inert siblings up to the document; never inert the sheet's own ancestors.
      let node = root;
      while (node.parentElement && node.parentElement !== document.documentElement) {
        for (const sibling of node.parentElement.children) if (sibling !== node && sibling !== scrim && !sibling.inert) {sibling.inert = true; inerted.push(sibling);}
        node = node.parentElement;
      }
    }
  }
  function keys(event) {if (event.key === 'Escape') {event.preventDefault(); close();} else if (!media.matches) trapFocus(root, event);}
  let closed = false;
  function close() {if (closed) return; closed = true; release(); scrim.remove(); root.remove();
    document.removeEventListener('keydown', keys); media.removeEventListener('change', resize); onClose(); if (opener?.isConnected) opener.focus();}
  queueMicrotask(() => {if (closed || !root.isConnected) return; resize(); (matchMedia('(max-width: 799px)').matches ? back : closeButton).focus();
    document.addEventListener('keydown', keys); media.addEventListener('change', resize);});
  return Object.assign(root, {body, close, dispose: close});
}
export function dataTable({columns, rows, rowKey, selected, onSelect = () => {}, expandable, expanded = new Set(), onToggle = () => {}}) {
  const tbody = h('tbody'), table = h('table', {}, h('thead', {}, h('tr', {}, expandable ? h('th', {'aria-label': t('ui_more')}) : null,
    ...columns.map(column => h('th', {scope: 'col', class: column.numeric ? 'numeric' : ''}, column.label)))), tbody);
  const root = h('div', {class: 'ui-data-table', tabindex: 0}, table), controls = [];
  rows.forEach((row, i) => {
    const key = typeof rowKey === 'function' ? rowKey(row) : row[rowKey], active = key === selected;
    const tr = h('tr', {'data-row-key': key, tabindex: active || selected == null && i === 0 ? 0 : -1,
      'aria-selected': String(active), onclick: event => {if (!event.target.closest('button, a, input, select')) select();}});
    function select() {controls.forEach(node => {node.tabIndex = node === tr ? 0 : -1; node.setAttribute('aria-selected', String(node === tr));}); onSelect(key, row);}
    if (expandable) {
      const open = expanded.has(key);
      const detail = h('tr', {hidden: !open}, h('td', {colspan: columns.length + 1}, typeof expandable === 'function' ? expandable(row) : ''));
      const toggle = iconButton({icon: 'chevron-right', label: t(open ? 'ui_collapse' : 'ui_expand', {title: row.title || key}), 'aria-expanded': String(open), onClick: () => {
        detail.hidden = !detail.hidden; toggle.setAttribute('aria-expanded', String(!detail.hidden));
        toggle.setAttribute('aria-label', t(detail.hidden ? 'ui_expand' : 'ui_collapse', {title: row.title || key})); onToggle(key, !detail.hidden);}});
      tr.append(h('td', {}, toggle)); tbody.append(tr, detail);
    } else tbody.append(tr);
    tr.append(...columns.map(column => h('td', {class: column.numeric ? 'numeric' : ''}, column.render ? column.render(row) : row[column.key] ?? '—')));
    tr.addEventListener('keydown', event => {
      if (event.target !== tr) return;
      if (['Enter', ' '].includes(event.key)) {event.preventDefault(); select();}
      if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
        event.preventDefault(); const next = event.key === 'Home' ? 0 : event.key === 'End' ? controls.length - 1
          : Math.max(0, Math.min(controls.length - 1, i + (event.key === 'ArrowUp' ? -1 : 1)));
        controls.forEach((node, n) => {node.tabIndex = n === next ? 0 : -1;}); controls[next]?.focus();
      }
    }); controls.push(tr);
  });
  return root;
}
