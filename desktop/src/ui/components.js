import {h} from './dom.js';
import {discussHref} from '../pm-discuss-link.js';
import {t} from '../i18n.js';
import {icon as drawIcon} from './icons.js';
import {statusVocabulary} from './ui-strings.js';
export {statusVocabulary};
let sequence = 0;
const uid = () => `ui-${++sequence}`;
const nodeIcon = icon => typeof icon === 'string' ? drawIcon(icon) : icon;
// Keyboard moves on a segmented control or tab list rebuild the view. Remember the
// group label long enough for the replacement to take focus; a click does not.
let choiceFocus = null;
let choiceSerial = 0;
export function button({variant = 'secondary', size = 'md', icon, iconPosition = 'start', label, onClick, ...attrs} = {}) {
  return h('button', {type: 'button', class: `ui-button ${variant} ui-${size}`, onclick: onClick, ...attrs}, iconPosition === 'end' ? null : nodeIcon(icon), label, iconPosition === 'end' ? nodeIcon(icon) : null);
}
export function discussLink(context, attrs = {}) {
  return h('a', {class:'ui-button secondary ui-sm', href:discussHref(context), ...attrs}, drawIcon('message-square'), t('ui_discuss'));
}
export function iconButton({icon, label, ...attrs}) {
  return button({variant: 'ghost', icon, 'aria-label': label, title: label, ...attrs, class: 'ui-button ghost ui-icon-button'});
}
export function searchField(input, attrs = {}) {
  input.type = 'search'; input.placeholder = t('ui_search');
  return h('div', {...attrs, class: `ui-search ${attrs.class || ''}`}, drawIcon('search'), input);
}
// One observer manages connected controls. View replacements release their parents,
// so switching pages does not accumulate per-view resize subscriptions.
let choiceObserver, choiceMounts;
const choiceFits = new WeakMap(), choiceParents = new Map();
function watchChoice(root, measure) {
  choiceFits.set(root, measure); root.dataset.choiceAdaptive = '';
  if (!choiceObserver) {
    choiceObserver = new ResizeObserver(entries => {
      for (const {target} of entries) for (const control of choiceParents.get(target) || []) choiceFits.get(control)?.();
    });
    const reconcile = () => {
      for (const [parent, controls] of choiceParents) {
        for (const control of controls) if (!control.isConnected || control.parentElement !== parent) controls.delete(control);
        if (!controls.size) {choiceObserver.unobserve(parent); choiceParents.delete(parent);}
      }
      for (const control of document.querySelectorAll('[data-choice-adaptive]')) {
        const parent = control.parentElement;
        if (!choiceFits.has(control) || choiceParents.get(parent)?.has(control)) continue;
        if (!choiceParents.has(parent)) {choiceParents.set(parent, new Set()); choiceObserver.observe(parent);}
        choiceParents.get(parent).add(control); choiceFits.get(control)();
      }
    };
    choiceMounts = new MutationObserver(reconcile);
    choiceMounts.observe(document.documentElement, {childList: true, subtree: true});
    document.fonts?.ready.then(reconcile);
  }
}
function choices({options, value, onChange = () => {}, label}, isTabs) {
  const root = h('div', {class: isTabs ? 'ui-tabs' : 'ui-segmented', role: isTabs ? 'tablist' : 'radiogroup', 'aria-label': label});
  const buttons = options.map(option => h('button', {type: 'button', role: isTabs ? 'tab' : 'radio',
    id: option.id || uid(), 'aria-controls': isTabs && typeof option.panel === 'string' ? option.panel : null, disabled: option.disabled,
    onclick: () => select(option.value)}, option.label));
  const fallback = isTabs ? null : h('select', {'aria-label':label, 'data-choice-select':'', hidden:true,
    onchange: () => select(options[fallback.selectedIndex].value)},
    ...options.map((option, index) => h('option', {value:String(index), disabled:option.disabled}, option.label)));
  if (isTabs) options.forEach((option, index) => {
    if (!option.panel || typeof option.panel === 'string') return;
    const panel = option.panel;
    panel.id ||= uid(); panel.setAttribute('role', 'tabpanel');
    panel.setAttribute('aria-labelledby', buttons[index].id);
    buttons[index].setAttribute('aria-controls', panel.id);
  });
  function select(next, notify = true) {
    value = next;
    if (fallback) fallback.value = String(options.findIndex(option => option.value === value));
    buttons.forEach((node, i) => {const active = options[i].value === value;
      node.tabIndex = active ? 0 : -1; node.setAttribute(isTabs ? 'aria-selected' : 'aria-checked', String(active));});
    if (notify) onChange(value);
  }
  root.addEventListener('keydown', event => {
    if (event.target === fallback) return;
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const enabled = buttons.filter(node => !node.disabled), current = enabled.indexOf(document.activeElement);
    const index = event.key === 'Home' ? 0 : event.key === 'End' ? enabled.length - 1
      : (current + (['ArrowLeft', 'ArrowUp'].includes(event.key) ? -1 : 1) + enabled.length) % enabled.length;
    const next = enabled[index]; if (!next) return;
    const ticket = {label, id: ++choiceSerial};
    choiceFocus = ticket;
    next.focus(); next.click();
    setTimeout(() => { if (choiceFocus === ticket) choiceFocus = null; }, 80);
  });
  root.append(...buttons, ...(fallback ? [fallback] : [])); select(options.some(o => o.value === value && !o.disabled) ? value : options.find(o => !o.disabled)?.value, false);
  if (fallback) watchChoice(root, () => {
    if (!root.isConnected) return;
    const focused = document.activeElement, parent = root.parentElement, css = getComputedStyle(parent);
    fallback.hidden = true; buttons.forEach(node => node.hidden = false); root.removeAttribute('data-choice-overflow');
    const needed = root.scrollWidth, available = parent.clientWidth - parseFloat(css.paddingLeft) - parseFloat(css.paddingRight);
    const overflow = needed > available + 1;
    fallback.hidden = !overflow; buttons.forEach(node => node.hidden = overflow);
    root.toggleAttribute('data-choice-overflow', overflow); root.setAttribute('role', overflow ? 'group' : 'radiogroup');
    if (overflow && (buttons.includes(focused) || focused === fallback)) fallback.focus({preventScroll:true});
    else if (!overflow && focused === fallback) buttons.find(node => node.getAttribute('aria-checked') === 'true')?.focus({preventScroll:true});
  });
  if (choiceFocus?.label === label) {
    choiceFocus = null;
    const focusChecked = () => {
      if (!root.isConnected) return;
      const checked = buttons.find(node => node.getAttribute(isTabs ? 'aria-selected' : 'aria-checked') === 'true');
      checked?.focus();
    };
    queueMicrotask(focusChecked);
    setTimeout(focusChecked, 50);
  }
  root.setValue = next => select(next, false);
  return root;
}
export const segmented = options => choices(options, false);
export const tabs = options => choices(options, true);
export function statusPill(status) {
  const key = Object.hasOwn(statusVocabulary, status) ? status : 'unknown';
  return h('span', {class: `ui-pill tone-${statusVocabulary[key].tone}`, 'data-status': key}, t('ui_status_' + key));
}
export function pageHeader({title, mark, pill, meta, actions = [], overflow} = {}) {
  const actionRow = h('div', {class:'ui-header-actions'}, ...actions, overflow);
  actionRow.addEventListener('focusin', event => {
    const target = event.target.closest('button, a, select');
    if (!target || actionRow.scrollWidth <= actionRow.clientWidth) return;
    const box = target.getBoundingClientRect(), row = actionRow.getBoundingClientRect();
    if (box.right > row.right) actionRow.scrollLeft += box.right - row.right;
    if (box.left < row.left) actionRow.scrollLeft -= row.left - box.left;
  });
  const heading = h('h1', {}, title);
  const nameHeading = () => {heading.title = heading.textContent; heading.setAttribute('aria-label', heading.textContent);};
  nameHeading();
  new MutationObserver(nameHeading).observe(heading, {childList: true, characterData: true, subtree: true});
  return h('header', {class: 'ui-page-header'}, h('div', {class: 'ui-page-heading'},
    h('div', {class: 'ui-title-row'}, mark, heading, pill),
    meta ? (meta instanceof Node ? meta : h('p', {class: 'ui-meta'}, meta)) : null),
  actionRow);
}
export function sectionCard({title, actions = [], body} = {}) {
  return h('section', {class: 'ui-section-card'}, title ? h('header', {}, h('h2', {}, title), h('div', {class: 'ui-header-actions'}, ...actions)) : null, body);
}
export function metric({label, value, context, bar, pill} = {}) {
  const unknown = value === null || value === undefined || typeof value === 'number' && !Number.isFinite(value);
  const track = bar && !unknown ? h('div', {class: 'ui-metric-bar', 'aria-hidden': 'true'}, ...bar.map(part => {
    const span = h('span', {class: `tone-${['ok', 'review', 'info', 'bad', 'warn'].includes(part.tone) ? part.tone : 'neutral'}`});
    span.style.flexGrow = String(Number.isFinite(part.value) && part.value > 0 ? part.value : 0); return span;
  })) : null;
  return h('div', {class: 'ui-metric'}, h('div', {class: 'ui-meta'}, label),
    h('div', {class: 'ui-metric-value'}, unknown ? '—' : value, unknown ? statusPill('unknown') : pill || null), context ? h('div', {class: 'ui-metric-context'}, context) : null, track);
}
export function emptyState({icon = 'inbox', title, text, action}) {
  return h('div', {class: 'ui-empty'}, nodeIcon(icon), h('h2', {}, title), text ? h('p', {}, text) : null, action);
}
export function skeleton(rows = 3, {label = t('ui_loading')} = {}) {
  return h('div', {class: 'ui-skeleton', role: 'status', 'aria-label': label},
    ...Array.from({length: Math.min(100, Math.max(0, rows))}, () => h('div', {'aria-hidden': 'true'})));
}
const disclosures = new Map();
export function disclosure({label, body, open = false, key, element}) {
  // Existing lazy/durable content can opt into the shared disclosure without
  // replacing its nodes, listeners, focus or open state.
  const root = element || h('details', {open}, h('summary', {}, label), body);
  if (root.classList.contains('ui-disclosure')) return root;
  root.classList.add('ui-disclosure');
  root.querySelector(':scope > summary')?.prepend(drawIcon('chevron-right'));
  if (key && disclosures.has(key)) root.open = disclosures.get(key);
  root.addEventListener('toggle', () => {if (!key || !root.isConnected) return;
    disclosures.delete(key); disclosures.set(key, root.open); if (disclosures.size > 200) disclosures.delete(disclosures.keys().next().value);});
  return root;
}
export function inlineError({error, retry}) {
  return h('div', {class: 'ui-inline-error', role: 'alert'}, drawIcon('circle-alert'), h('span', {}, error?.message || String(error)),
    retry ? button({size: 'sm', variant: 'ghost', label: t('ui_retry'), onClick: retry}) : null);
}
export function contextChip({icon, text, onRemove}) {
  return h('span', {class: 'ui-context-chip', role:'group', title:text, 'aria-label':text}, nodeIcon(icon), h('span', {class:'ui-chip-label'}, text),
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
  const controls = items.map(item => {
    if (item.content) return h('div', {class: 'ui-menu-group', role: 'group', 'aria-label': item.label, 'data-menu-key': item.key}, item.content);
    let node;
    node = h(item.href ? 'a' : 'button', {type: item.href ? null : 'button', href: item.href, role: 'menuitem', 'data-menu-key': item.key,
      tabindex: -1, title: item.title, disabled: item.href ? null : item.disabled, 'aria-disabled': item.disabled ? 'true' : null,
      onclick: event => {
        // Read the live control. An item created disabled and enabled later must still run.
        if (node.disabled || node.getAttribute('aria-disabled') === 'true') {event.preventDefault(); return;}
        close(); item.onSelect?.();
      }}, nodeIcon(item.icon), item.label);
    return node;
  });
  panel.append(...controls);
  const enabledControls = () => [...panel.querySelectorAll('[role="menuitem"], select:not(:disabled)')].filter(node => !node.disabled && node.getAttribute('aria-disabled') !== 'true' && node.getClientRects().length && !node.closest('[hidden], [inert]'));
  function close(restore = true) {panel.hidden = true; trigger.setAttribute('aria-expanded', 'false');
    document.removeEventListener('pointerdown', outside);
    if (restore && root.isConnected) trigger.focus();}
  function outside(event) {if (!root.contains(event.target)) close(false);}
  function keys(event) {
    if (event.key === 'Escape') {event.preventDefault(); event.stopPropagation(); close();}
    else if (event.key === 'Tab') close(false);
    else if (!event.target?.matches?.('select, input, textarea') && ['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
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
export function inspector({title, onClose = () => {}, returnFocus, dock = false} = {}) {
  const opener = document.activeElement, media = matchMedia('(min-width: 1200px)'), titleId = uid();
  const body = h('div', {class: 'ui-inspector-body'});
  const closeButton = iconButton({icon: 'x', label: t('ui_close'), onClick: () => close()});
  const back = button({variant: 'ghost', icon: 'chevron-left', label: t('ui_back'), onClick: () => close(), class: 'ui-button ghost ui-inspector-back'});
  const root = h('aside', {class: dock ? 'ui-inspector ui-inspector-dock' : 'ui-inspector', tabindex: -1, 'aria-labelledby': titleId},
    h('header', {}, back, h('h2', {id: titleId}, title), closeButton), body);
  const scrim = h('div', {class: 'ui-inspector-scrim', onclick: () => close()});
  const inerted = [];
  function release() {for (const node of inerted.splice(0)) node.inert = false;}
  function resize() {
    release();
    if (dock && !media.matches && root.hidden) {scrim.remove(); root.removeAttribute('aria-modal'); return;}
    root.setAttribute('role', media.matches ? 'complementary' : 'dialog');
    if (media.matches || root.hidden) {root.removeAttribute('aria-modal'); scrim.remove();}
    else {root.setAttribute('aria-modal', 'true'); if (!scrim.isConnected) root.before(scrim);
      // Inert siblings up to the document; never inert the sheet's own ancestors.
      let node = root;
      while (node.parentElement && node.parentElement !== document.documentElement) {
        for (const sibling of node.parentElement.children) if (sibling !== node && sibling !== scrim && !sibling.inert) {sibling.inert = true; inerted.push(sibling);}
        node = node.parentElement;
      }
    }
  }
  function keys(event) {
    if (dock && (root.hidden || media.matches)) return;
    if (event.key === 'Escape') {
      if (!root.contains(event.target) && event.target.closest?.('input, textarea, select, [contenteditable]:not([contenteditable=false])')) return;
      event.preventDefault(); close();
    }
    else if (!media.matches) trapFocus(root, event);
  }
  let closed = false;
  function restoreFocus() {
    let target = null;
    try { target = typeof returnFocus === 'function' ? returnFocus() : returnFocus; } catch { target = null; }
    const rendered = node => node?.isConnected && node.getClientRects().length && !node.closest('[hidden], [inert]');
    if (rendered(target)) target.focus();
    else if (rendered(opener)) opener.focus();
  }
  function destroy() {
    if (closed) return; closed = true; release(); scrim.remove(); root.remove();
    document.removeEventListener('keydown', keys); media.removeEventListener('change', onMedia);
  }
  function close() {
    if (closed) return;
    if (dock && !media.matches) {
      root.hidden = true; release(); scrim.remove(); root.removeAttribute('aria-modal');
      onClose(); restoreFocus();
      return;
    }
    if (dock && media.matches) return;
    destroy(); onClose(); restoreFocus();
  }
  function open() {
    if (closed) return;
    root.hidden = false; resize();
    (matchMedia('(max-width: 799px)').matches ? back : closeButton).focus();
  }
  function onMedia() {
    if (dock && !media.matches) root.hidden = true;
    if (dock && media.matches) root.hidden = false;
    resize();
  }
  queueMicrotask(() => {
    if (closed || !root.isConnected) return;
    if (dock && !media.matches) root.hidden = true;
    resize();
    if (!dock) (matchMedia('(max-width: 799px)').matches ? back : closeButton).focus();
    document.addEventListener('keydown', keys); media.addEventListener('change', onMedia);
  });
  return Object.assign(root, {body, close, open, dispose: dock ? destroy : close});
}
export function dataTable({columns, rows, rowKey, selected, onSelect = () => {}, expandable, expanded = new Set(), onToggle = () => {}, compactRow, compactBelow = 0, rowAttrs} = {}) {
  const compact = () => compactRow && (matchMedia('(max-width: 799px)').matches || root.clientWidth < compactBelow);
  const keyFor = row => typeof rowKey === 'function' ? rowKey(row) : row[rowKey];
  const hasSelection = rows.some(row => keyFor(row) === selected);
  const tbody = h('tbody'), table = h('table', {}, h('thead', {}, h('tr', {}, expandable ? h('th', {'aria-hidden': 'true'}) : null,
    ...columns.map(column => h('th', {scope: 'col', 'data-column': column.key, 'data-secondary': column.secondary || null, class: column.numeric ? 'numeric' : ''}, column.label)))), tbody);
  const root = h('div', {class: 'ui-data-table' + (compactRow ? ' ui-data-table-responsive' : ''), tabindex: compactRow ? null : 0}, table), controls = [];
  rows.forEach((row, i) => {
    const key = keyFor(row), active = key === selected;
    const extra = typeof rowAttrs === 'function' ? rowAttrs(row) || {} : {};
    let toggleExpansion;
    const tr = h('tr', {...extra, 'data-row-key': key, tabindex: active || !hasSelection && i === 0 ? 0 : -1,
      'aria-selected': String(active), onclick: event => {if (!event.target.closest('button, a, input, select')) {
        if (compact() && toggleExpansion) {tr.focus(); toggleExpansion();} else select();
      }}});
    function select() {controls.forEach(node => {node.tabIndex = node === tr ? 0 : -1; node.setAttribute('aria-selected', String(node === tr));}); onSelect(key, row);}
    if (expandable) {
      const open = expanded.has(key);
      const detail = h('tr', {hidden: !open, class: 'ui-data-table-detail'}, h('td', {colspan: columns.length + 1}, typeof expandable === 'function' ? expandable(row) : ''));
      toggleExpansion = (open = detail.hidden) => {
        detail.hidden = !open;
        toggle.setAttribute('aria-expanded', String(open));
        if (compactRow) tr.setAttribute('aria-expanded', String(open));
        toggle.setAttribute('aria-label', t(open ? 'ui_collapse' : 'ui_expand', {title: row.title || key})); onToggle(key, open);
      };
      const toggle = iconButton({icon: 'chevron-right', label: t(open ? 'ui_collapse' : 'ui_expand', {title: row.title || key}), 'aria-expanded': String(open), tabindex: -1, onClick: () => toggleExpansion()});
      if (compactRow) tr.setAttribute('aria-expanded', String(open));
      tr.append(h('td', {}, toggle)); tbody.append(tr, detail);
    } else tbody.append(tr);
    tr.append(...columns.map(column => h('td', {'data-column': column.key, 'data-secondary': column.secondary || null, class: column.numeric ? 'numeric' : ''}, column.render ? column.render(row) : row[column.key] ?? '—')));
    if (compactRow) tr.append(h('td', {class: 'ui-data-table-compact', colspan: columns.length + (expandable ? 1 : 0)},
      expandable ? drawIcon('chevron-right') : null, compactRow(row)));
    tr.addEventListener('keydown', event => {
      if (event.target !== tr) return;
      if (['Enter', ' '].includes(event.key)) {event.preventDefault(); if (compact() && toggleExpansion) toggleExpansion(); else select();}
      if (toggleExpansion && ['ArrowRight', 'ArrowLeft'].includes(event.key)) {event.preventDefault(); toggleExpansion(event.key === 'ArrowRight');}
      if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
        event.preventDefault(); const next = event.key === 'Home' ? 0 : event.key === 'End' ? controls.length - 1
          : Math.max(0, Math.min(controls.length - 1, i + (event.key === 'ArrowUp' ? -1 : 1)));
        controls.forEach((node, n) => {node.tabIndex = n === next ? 0 : -1;}); controls[next]?.focus();
      }
    });
    tr.addEventListener('focus', () => controls.forEach(node => {node.tabIndex = node === tr ? 0 : -1;}));
    controls.push(tr);
  });
  for (const cell of table.querySelectorAll('th, td:not(.ui-data-table-compact)')) if (!cell.title) cell.title = cell.textContent;
  root.addEventListener('keydown', event => {
    if (event.target !== root || !controls.length) return;
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const index = event.key === 'End' || event.key === 'ArrowUp' ? controls.length - 1 : 0;
    controls.forEach((node, n) => { node.tabIndex = n === index ? 0 : -1; });
    controls[index].focus();
  });
  return root;
}
