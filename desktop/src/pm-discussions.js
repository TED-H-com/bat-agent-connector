import {contextChip, tabs, inspector, button, iconButton, disclosure} from './ui/components.js';
import {h} from './ui/dom.js';
import {viewState} from './ui/view-state.js';

// Unknown enums are neutral, never machine keys in the reading surface.
export function discussionLabel(t, prefix, value) {
  const key = prefix + value, label = t(key);
  return label === key ? t('ui_status_unknown') : label;
}
export function discussionContext(t, refs = [], {limit = refs.length, onMore} = {}) {
  const chips = refs.slice(0, limit).map(ref => {
    const kind = discussionLabel(t, 'pd_kind_', ref.kind);
    const text = ref.available === false ? `${kind} · ${t('pd_unavailable')}` : [kind, ref.label].filter(Boolean).join(' · ');
    const chip = contextChip({text});
    chip.dataset.discussionContext = ref.kind;
    chip.title = text; chip.setAttribute('aria-label', text);
    // A withdrawn reference contains only its kind and position. Never consult a cache.
    if (ref.available !== false && ref.bound_label && ref.bound_label !== ref.label) chip.title += '\n' + t('pd_bound_label', {label: ref.bound_label});
    return chip;
  });
  if (refs.length > limit && onMore) chips.push(button({size: 'sm', variant: 'ghost', label: '+' + (refs.length - limit),
    'aria-label': t('pd_more_context', {count: refs.length - limit}), 'data-discussion-more-context': '', onClick: onMore}));
  return chips;
}

// Reuse shared disclosures for the retained, lazily constructed PM tools too.
export const discussionDom = (create, keyPrefix) => (tag, attrs, ...children) => {
  const node = create(tag, attrs, ...children);
  const identity = Object.entries(attrs || {}).find(([name]) => ['data-pm-information', 'data-pm-add-input',
    'data-pm-planning-revision', 'data-pm-note-only', 'data-pm-prepare-context', 'data-pm-revision-editor'].includes(name));
  const key = keyPrefix && identity ? JSON.stringify([keyPrefix, ...identity]) : undefined;
  return tag === 'details' ? disclosure({element: node, key}) : node;
};

export function discussionTitle({t, field, form, value, local = false}) {
  let current = value, restoreFocus = false;
  const title = h('h2', {'data-discussion-title': '', title: value}, value);
  const pencil = iconButton({icon: 'pencil', label: t('pd_rename'), disabled: field.disabled, 'data-discussion-rename': '', onClick: () => {
    if (!form.hasDraft()) field.value = current;
    edit.hidden = false; display.hidden = true; field.focus(); field.select();
  }});
  const display = h('div', {class: 'pd-title-display'}, title, pencil);
  const cancel = button({label: t('pd_cancel'), onClick: () => {
    if (form.locked()) return;
    if (local) form.set('title', current); else {form.discard(); field.value = current;} finish(); pencil.focus();
  }});
  const edit = local ? h('form', {onsubmit: event => {
    event.preventDefault(); current = field.value.trim(); title.textContent = current; title.title = current; finish(); pencil.focus();
  }}, field, h('div', {class: 'actions'}, button({type: 'submit', variant: 'primary', label: t('pd_save')}))) : form.box;
  edit.classList.add('pd-title-edit'); edit.querySelector('.actions').append(cancel);
  edit.addEventListener('keydown', event => {if (event.key === 'Escape') {event.preventDefault(); event.stopPropagation(); cancel.click();}});
  function finish({focus = false} = {}) {restoreFocus = focus && (edit.contains(document.activeElement) || document.activeElement === document.body); edit.hidden = true; display.hidden = false;}
  finish();
  if (!local && (form.locked() || form.hasDraft() && field.value !== current)) {edit.hidden = false; display.hidden = true;}
  return {box: h('div', {class: 'pd-title'}, display, edit), finish, update(next) {
    current = next; title.textContent = next; title.title = next; pencil.disabled = field.disabled;
    cancel.disabled = form.locked();
    if (restoreFocus && !pencil.disabled) {
      if (document.activeElement === document.body || edit.contains(document.activeElement)) pencil.focus();
      restoreFocus = false;
    }
  }};
}

export function discussionComposer({t, form, analysis, details, draft = false}) {
  const actions = form.box.querySelector(':scope > .actions');
  const status = h('div', {class: 'pd-composer-status'}, form.box.querySelector(':scope > [role=status]'));
  const receipt = form.box.querySelector(':scope > .pm-receipt');
  if (details) details.append(receipt); else status.append(disclosure({label: t('pd_details'), body: receipt}));
  const recovery = actions.querySelector('button[type=button]'); if (recovery) status.append(recovery);
  if (analysis) {
    actions.append(analysis.querySelector('[data-discussion-ask]'));
    status.append(analysis);
  } else if (!draft) status.append(h('p', {class: 'muted'}, h('span', {}, t('pd_not_connected')), ' · ', h('a', {href: '#/settings'}, t('pd_setup'))));
  form.box.append(h('div', {class: 'pd-composer-row'}, status, actions));
  return h('div', {class: 'pd-composer'}, form.box);
}

export function discussionFeedback({t, request, node, eligible}) {
  const operation = request?.operation();
  const error = request?.box.querySelector(':scope > [role=status] .error');
  const failed = error || ['failed', 'cancelled', 'uncertain', 'needs_attention'].includes(operation?.status);
  const running = ['running', 'accepted', 'queued', 'pending'].includes(operation?.status);
  const checked = request?.checking() === true;
  const label = running ? t(checked ? 'pd_working' : 'pd_status_unchecked') : discussionOperation(t, operation) || error?.textContent || '';
  let status = node.querySelector('[role=status]');
  if (!status) {node.removeAttribute('role'); status = h('span', {role: 'status'}); node.append(status, h('span', {'aria-hidden': 'true', 'data-discussion-elapsed': ''}), h('span', {'data-discussion-retry': ''}));}
  // Never mutate the live region for a clock tick or an unchanged status.
  if (status.textContent !== label) status.textContent = label;
  const elapsed = node.querySelector('[data-discussion-elapsed]');
  const time = running && checked ? discussionOperation(t, operation).slice(t('pd_working').length) : '';
  if (elapsed.textContent !== time) elapsed.textContent = time;
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
export function discussionPanel({t, namespace, workspaceId, panes, onSelect = () => {}}) {
  const state = viewState(namespace, workspaceId, 'discussion-panel');
  if (!Object.hasOwn(panes, state.tab)) state.tab = Object.keys(panes)[0];
  const home = h('aside', {class: 'pd-panel-home'}), body = h('div', {class: 'pd-panel', 'data-discussion-panel': ''});
  const select = key => {state.tab = key; for (const [id, node] of Object.entries(panes)) node.hidden = id !== key; onSelect(key);};
  const controls = tabs({label: t('pd_panel'), value: state.tab || 'context',
    options: Object.keys(panes).map(value => ({value, label: t('pd_tab_' + value), panel: panes[value]})), onChange: select});
  body.append(controls, ...Object.values(panes)); home.append(body); select(state.tab || 'context');
  let overlay = null;
  const trigger = iconButton({icon: 'panel-right', label: t('pd_panel'), 'data-discussion-panel-toggle': '', onClick: () => {
    if (overlay) {overlay.close(); return;}
    overlay = inspector({title: t('pd_panel'), onClose: () => {home.append(body); overlay = null;}});
    overlay.classList.add('pd-panel-overlay'); overlay.body.append(body); document.getElementById('main').append(overlay);
  }});
  trigger.classList.add('pd-panel-toggle');
  return {home, trigger, select(key) {select(key); controls.setValue(key);}, open(key) {
    select(key); controls.setValue(key);
    if (matchMedia('(max-width: 1199px)').matches && !overlay) trigger.click();
    else controls.querySelector('[aria-selected=true]')?.focus();
  }, close() {overlay?.close();}, dispose() {overlay?.close();}};
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
