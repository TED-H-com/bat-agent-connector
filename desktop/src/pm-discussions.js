import {contextChip, tabs, inspector, button, iconButton, disclosure} from './ui/components.js';
import {h} from './ui/dom.js';
import {viewState} from './ui/view-state.js';
import {sheetHistory} from './ui/sheet-history.js';
import {locale} from './i18n.js';
import {findingName} from './ui/finding-name.js';
import {timeStamp, timeText, timeLabel} from './ui/stamps.js';

// Unknown enums are neutral, never machine keys in the reading surface.
export function discussionLabel(t, prefix, value) {
  const key = prefix + value, label = t(key);
  return label === key ? t('ui_status_unknown') : label;
}
export function discussionContext(t, refs = [], {limit = refs.length, onMore, title, onOpen} = {}) {
  const chips = refs.slice(0, limit).map(ref => {
    const kind = ['risk','issue','action','change','decision'].includes(ref.record_type) ? t('reg_type_' + ref.record_type) : discussionLabel(t, 'pd_kind_', ref.kind);
    const named = ref.available !== false && ref.kind === 'finding' ? findingName(ref.finding, locale, '') : '';
    const text = named || `${kind} · ${ref.available === false ? t('pd_unavailable') : ref.label || t('pd_unavailable')}`;
    const chip = onOpen ? button({variant: 'ghost', label: h('span', {class: 'ui-chip-label'}, text), class: 'ui-context-chip pd-context-trigger', onClick: event => onOpen(event.currentTarget)})
      : ref.kind === 'work_input' && ref.available !== false && /^op_[0-9a-f]{32}$/.test(ref.id || '')
        ? h('a', {href: `#/op/${ref.id}`, class: 'ui-context-chip'}, text) : contextChip({text});
    chip.dataset.discussionContext = ref.kind;
    chip.title = text; chip.setAttribute('aria-label', text);
    // A withdrawn reference contains only its kind and position. Never consult a cache.
    if (ref.available !== false && ref.bound_label && ref.bound_label !== ref.label) chip.title += '\n' + t('pd_bound_label', {label: ref.bound_label});
    return chip;
  });
  if (!refs.length && title) {
    const chip = onOpen ? button({variant: 'ghost', label: h('span', {class: 'ui-chip-label'}, title), class: 'ui-context-chip pd-context-trigger', onClick: event => onOpen(event.currentTarget)}) : contextChip({text: title});
    chip.dataset.discussionContextTitle = ''; chip.setAttribute('aria-label', title); chip.title = title; chips.push(chip);
  }
  if (refs.length > limit && onMore) chips.push(button({size: 'sm', variant: 'ghost', label: '+' + (refs.length - limit),
    'aria-label': t('pd_more_context', {count: refs.length - limit}), 'data-discussion-more-context': '', onClick: onMore}));
  return chips;
}

// Reuse shared disclosures for the retained, lazily constructed PM tools too.
export const discussionDom = (create, keyPrefix) => (tag, attrs, ...children) => {
  const node = tag === 'button' && ['primary', 'secondary'].includes(attrs?.class)
    ? button({...Object.fromEntries(Object.entries(attrs).filter(([key]) => key !== 'class')), variant: attrs.class, label: children}) : create(tag, attrs, ...children);
  const identity = Object.entries(attrs || {}).find(([name]) => ['data-pm-information', 'data-pm-add-input',
    'data-pm-planning-revision', 'data-pm-note-only', 'data-pm-prepare-context', 'data-pm-revision-editor'].includes(name));
  const key = keyPrefix && identity ? JSON.stringify([keyPrefix, ...identity]) : undefined;
  return tag === 'details' ? disclosure({element: node, key}) : node;
};

export function discussionTitle({t, field, form, value, local = false, resume = false, onEditing = () => {}}) {
  let current = value, restoreFocus = false;
  const title = h('h2', {'data-discussion-title': '', title: value}, value);
  const remember = () => {if (!edit.hidden) onEditing(true, field.selectionStart ?? 0, field.selectionEnd ?? field.selectionStart ?? 0);};
  const pencil = iconButton({icon: 'pencil', label: t('pd_rename'), disabled: field.disabled, 'data-discussion-rename': '', onClick: () => {
    if (!form.hasDraft()) field.value = current;
    edit.hidden = false; display.hidden = true; field.focus(); field.select(); remember();
  }});
  const display = h('div', {class: 'pd-title-display'}, title, pencil);
  const cancel = button({label: t('pd_cancel'), onClick: () => {
    if (form.locked()) return;
    if (local) form.set('title', current); else {form.discard(); field.value = current;} finish({focus: true}); pencil.focus();
  }});
  const edit = local ? h('form', {onsubmit: event => {
    event.preventDefault(); if (field.disabled) return;
    current = field.value.trim(); title.textContent = current; title.title = current; finish(); pencil.focus();
  }}, field, h('div', {class: 'actions'}, button({type: 'submit', variant: 'primary', label: t('pd_save')}))) : form.box;
  edit.classList.add('pd-title-edit'); edit.querySelector('.actions').append(cancel);
  edit.addEventListener('keydown', event => {if (event.key === 'Escape' && !event.isComposing && event.keyCode !== 229) {event.preventDefault(); event.stopPropagation(); cancel.click();}});
  for (const eventName of ['input', 'keyup', 'mouseup', 'select']) field.addEventListener(eventName, remember);
  function finish({focus = false, notify = true} = {}) {
    restoreFocus = focus && (edit.contains(document.activeElement) || document.activeElement === document.body);
    edit.hidden = true; display.hidden = false;
    if (notify) onEditing(false, 0, 0);
  }
  function updateState() {
    pencil.disabled = field.disabled || field.readOnly; cancel.disabled = form.locked();
    if (local) edit.querySelector('button[type=submit]').disabled = field.disabled;
  }
  // Construction always closes first. A rebuilt page must not treat that as Cancel.
  finish({notify: false});
  updateState();
  if (!local && (form.locked() || form.hasDraft() && field.value !== current)) {edit.hidden = false; display.hidden = true;}
  if (local && resume) {edit.hidden = false; display.hidden = true;}
  return {box: h('div', {class: 'pd-title'}, display, edit), finish, updateState, editing: () => !edit.hidden, focus(start, end) {
    if (edit.hidden) return;
    field.focus();
    if (!Number.isInteger(start)) return;
    const point = Math.max(0, Math.min(start, field.value.length));
    const stop = Number.isInteger(end) ? Math.max(point, Math.min(end, field.value.length)) : point;
    field.setSelectionRange(point, stop);
  }, update(next) {
    current = next; title.textContent = next; title.title = next; updateState();
    if (restoreFocus && !pencil.disabled) {
      if (document.activeElement === document.body || edit.contains(document.activeElement)) pencil.focus();
      restoreFocus = false;
    }
  }};
}

export const discussionSettingsHref = () => '#/settings?section=agents&from=' + encodeURIComponent(location.hash);
export function discussionComposer({t, form, analysis, reply, details, draft = false, pmReady = false, availability = null}) {
  const actions = form.box.querySelector(':scope > .actions');
  const status = h('div', {class: 'pd-composer-status'}, form.box.querySelector(':scope > [role=status]'));
  const receipt = form.box.querySelector(':scope > .pm-receipt');
  receipt.dataset.discussionReceipt = draft ? 'create' : 'message';
  if (details) details.append(receipt); else status.append(disclosure({label: t('pd_details'), body: receipt}));
  if (analysis) {
    actions.append(analysis.querySelector('[data-discussion-ask]'));
    status.append(analysis);
  } else if (draft && pmReady) status.append(h('p', {class: 'muted', 'data-discussion-draft-ready': ''}, t('pd_draft_ready')));
  else if (availability) status.append(availability);
  if (reply) {
    actions.querySelector('button[type=submit]').classList.replace('primary', 'secondary');
    actions.prepend(reply.querySelector('[data-discussion-reply]'));
    status.prepend(reply);
  }
  form.box.append(h('div', {class: 'pd-composer-row'}, status, actions));
  if (details) form.box.append(details);
  return h('div', {class: 'pd-composer'}, form.box);
}

export function discussionFeedback({t, request, node, eligible}) {
  const operation = request?.operation();
  const error = request?.box.querySelector(':scope > [role=status] .error');
  const failed = error || ['failed', 'cancelled', 'uncertain', 'needs_attention'].includes(operation?.status);
  const running = ['running', 'accepted', 'queued', 'pending'].includes(operation?.status);
  const checked = request?.checking() === true;
  const outcome = discussionOperation(t, operation);
  const label = running ? t(checked ? 'pd_working' : 'pd_status_unchecked') : outcome
    ? t('pd_last_run', {outcome}) : error?.textContent || '';
  let status = node.querySelector('[role=status]');
  if (!status) {node.removeAttribute('role'); status = h('span', {role: 'status'}); node.append(status, h('span', {'aria-hidden': 'true', 'data-discussion-elapsed': ''}), h('span', {'data-discussion-retry': ''}));}
  // Never mutate the live region for a clock tick or an unchanged status.
  if (status.textContent !== label) status.textContent = label;
  const elapsed = node.querySelector('[data-discussion-elapsed]');
  const time = running && checked ? discussionOperation(t, operation).slice(t('pd_working').length) : '';
  const updated = !running ? operation?.updated_at : null;
  const stamp = updated != null && timeText(updated) ? timeStamp(updated) : null;
  const timeSignature = JSON.stringify([time, stamp?.outerHTML]);
  if (elapsed.dataset.time !== timeSignature) {
    elapsed.dataset.time = timeSignature;
    elapsed.removeAttribute('aria-hidden');
    elapsed.replaceChildren(...(stamp ? [' · ', timeLabel(t, 'pd_run_updated', {when: stamp})] : [time]));
    if (running) elapsed.setAttribute('aria-hidden', 'true');
  }
  const retry = node.querySelector('[data-discussion-retry]'), disabled = request?.replaceable() ? !eligible : !request?.canSubmit();
  const signature = JSON.stringify([Boolean(failed), disabled]);
  if (retry.dataset.state !== signature) {
    retry.dataset.state = signature; retry.replaceChildren();
    if (failed) retry.append(' · ', button({size: 'sm', variant: 'ghost', label: t('pd_retry'), disabled, onClick: () => {
      // Unknown admission and uncertain operations retain the original key.
      if (request.replaceable()) request.edit();
      void request.submit();
    }}));
  }
}
export function discussionPanel({t, namespace, workspaceId, threadId = 'new', panes, onSelect = () => {}, canRestore = () => false}) {
  // Only cosmetic choices live in this page-session map, never source records.
  const state = viewState(namespace, workspaceId, 'discussion-panel.' + threadId);
  if (!Object.hasOwn(panes, state.tab)) state.tab = 'context';
  const home = h('aside', {class: 'pd-panel-home', hidden: true}), body = h('div', {class: 'pd-panel', 'data-discussion-panel': ''});
  let overlay = null, opener = null, panelFocus = null, navigation = null, disposed = false, scroll = 0;
  const media = matchMedia('(min-width: 1200px)');
  home.addEventListener('scroll', () => {if (media.matches && overlay) scroll = home.scrollTop;});
  const select = key => {
    if (!Object.hasOwn(panes, key)) return;
    state.tab = key;
    for (const [id, node] of Object.entries(panes)) node.hidden = id !== key;
    onSelect(overlay ? key : null);
  };
  const controls = tabs({label: t('pd_panel'), value: state.tab,
    options: Object.keys(panes).map(value => ({value, label: t('pd_tab_' + value), panel: panes[value]})), onChange: key => {state.explicit = true; select(key);}});
  body.append(controls, ...Object.values(panes)); home.append(body); select(state.tab);
  const trigger = iconButton({icon: 'panel-right', label: t('pd_tab_context'), 'data-discussion-panel-toggle': '',
    'aria-expanded': 'false', onClick: () => overlay ? close(true) : open('context', {explicit: true, returnFocus: trigger})});
  trigger.classList.add('pd-panel-toggle');
  function locate() {
    if (!overlay) return;
    // Responsive CSS can hide the old dock before the media event runs.
    // Remember focus while it is inside the panel, before that layout change.
    const focused = overlay.contains(document.activeElement) ? document.activeElement : panelFocus;
    const panel = overlay;
    (media.matches ? home : document.getElementById('main')).append(overlay);
    requestAnimationFrame(() => {if (overlay === panel && panel.contains(focused)) focused.focus({preventScroll: true});});
  }
  function close(explicit = false) {
    if (explicit) {state.explicit = true; state.open = false;}
    // Withdrawal and unmount invalidate Forward without navigating the user.
    if (!explicit) {navigation?.dispose(); navigation = null;}
    overlay?.close();
  }
  function conceal() {
    if (!overlay) return;
    // Read before the inspector removes its dock: removing it collapses home's
    // scroll range, and a queued scroll event may not have fired yet.
    scroll = media.matches ? home.scrollTop : overlay.body.scrollTop;
    overlay.close();
  }
  function open(key = 'context', {explicit = true, returnFocus, focus = explicit} = {}) {
    if (disposed) return;
    state.open = true;
    if (explicit) state.explicit = true;
    if (returnFocus) opener = returnFocus;
    select(key); controls.setValue(key);
    if (explicit) {
      navigation ??= sheetHistory({show: ({restoring}) => reveal(true, restoring), hide: conceal,
        canRestore: () => !disposed && home.isConnected && canRestore()});
      navigation.open();
      if (focus) queueMicrotask(() => {if (overlay) controls.querySelector('[aria-selected=true]')?.focus({preventScroll: true});});
    } else reveal(focus);
  }
  function reveal(focus, restoring = false) {
    const savedScroll = scroll;
    if (!overlay) {
      home.hidden = false;
      overlay = inspector({title: t('pd_panel'), returnFocus: () => (typeof opener === 'function' ? opener() : opener) || trigger, onClose: () => {
        home.append(body); home.hidden = true; overlay = null; trigger.setAttribute('aria-expanded', 'false');
        if (!disposed) {state.open = false; state.explicit = true;}
        onSelect(null);
        // An explicit X / Back / Escape consumes the entry. During browser Back
        // the history helper is already hidden, so this cannot navigate twice.
        navigation?.close();
      }});
      overlay.addEventListener('focusin', event => {panelFocus = event.target;});
      const scroller = overlay.body;
      scroller.addEventListener('scroll', () => {if (!media.matches && overlay?.body === scroller) scroll = scroller.scrollTop;});
      overlay.classList.add('pd-panel-overlay'); overlay.body.append(body); locate();
      if (!focus && media.matches) {const previous = document.activeElement; queueMicrotask(() => {if (previous?.isConnected && !previous.closest('[hidden], [inert]')) previous.focus({preventScroll: true});});}
      trigger.setAttribute('aria-expanded', 'true');
    }
    state.open = true;
    onSelect(state.tab);
    const panel = overlay;
    if (focus) queueMicrotask(() => {if (overlay === panel) controls.querySelector('[aria-selected=true]')?.focus({preventScroll: true});});
    // History traversal restores browser scroll after popstate/microtasks. Apply
    // the retained panel position after that and the inspector's initial focus.
    if (restoring) requestAnimationFrame(() => {
      if (overlay !== panel) return;
      (media.matches ? home : panel.body).scrollTop = savedScroll;
    });
  }
  media.addEventListener('change', locate);
  return {home, trigger, select(key) {select(key); controls.setValue(key);}, open(key, options) {open(key, options);},
    sync({proposalId = null, pending = false, inputs = false, inline = false} = {}) {
      const plan = !inline && (pending || Boolean(proposalId));
      const arrived = !inline && proposalId && state.proposalId !== proposalId;
      if (arrived) {state.proposalId = proposalId; state.explicit = false;}
      if (arrived || !state.explicit && (plan || inputs)) open(inputs && !plan ? 'inputs' : 'plan', {explicit: false});
      else if (!overlay && state.open) open(state.tab, {explicit: false});
    }, close() {close();}, dispose() {disposed = true; media.removeEventListener('change', locate); close();}};
}

export function discussionOperation(t, operation) {
  if (!operation) return '';
  if (operation.status === 'succeeded') return t('pd_finished');
  if (!['running', 'accepted', 'queued', 'pending'].includes(operation.status)) return discussionLabel(t, 'pd_op_', operation.status);
  const at = operation.started_at || operation.created_at;
  const start = typeof at === 'number' ? at : Date.parse(at) / 1000;
  const seconds = Number.isFinite(start) ? Math.max(0, Math.floor(Date.now() / 1000 - start)) : null;
  return t('pd_working') + (seconds === null ? '' : ` · ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`);
}
