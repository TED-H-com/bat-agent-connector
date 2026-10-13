import {contextChip, tabs, inspector, button} from './ui/components.js';
import {h} from './ui/dom.js';
import {viewState} from './ui/view-state.js';

// Unknown enums are neutral, never machine keys in the reading surface.
export function discussionLabel(t, prefix, value) {
  const key = prefix + value, label = t(key);
  return label === key ? t('ui_status_unknown') : label;
}
export function discussionContext(t, refs = []) {
  return refs.map(ref => {
    const kind = discussionLabel(t, 'pd_kind_', ref.kind);
    const chip = contextChip({text: ref.available === false ? `${kind} · ${t('pd_unavailable')}` : [kind, ref.label].filter(Boolean).join(' · ')});
    chip.dataset.discussionContext = ref.kind;
    // A withdrawn reference contains only its kind and position. Never consult a cache.
    if (ref.available !== false && ref.bound_label && ref.bound_label !== ref.label) chip.title = t('pd_bound_label', {label: ref.bound_label});
    return chip;
  });
}
export function discussionPanel({t, namespace, workspaceId, panes}) {
  const state = viewState(namespace, workspaceId, 'discussion-panel');
  const home = h('aside', {class: 'pd-panel-home'}), body = h('div', {class: 'pd-panel', 'data-discussion-panel': ''});
  const select = key => {state.tab = key; for (const [id, node] of Object.entries(panes)) node.hidden = id !== key;};
  const controls = tabs({label: t('pd_panel'), value: state.tab || 'context',
    options: Object.keys(panes).map(value => ({value, label: t('pd_tab_' + value)})), onChange: select});
  body.append(controls, ...Object.values(panes)); home.append(body); select(state.tab || 'context');
  let overlay = null;
  const trigger = button({label: t('pd_panel'), class: 'ui-button secondary pd-panel-toggle', 'data-discussion-panel-toggle': '', onClick: () => {
    if (overlay) {overlay.close(); return;}
    overlay = inspector({title: t('pd_panel'), onClose: () => {home.append(body); overlay = null;}});
    overlay.classList.add('pd-panel-overlay'); overlay.body.append(body); document.getElementById('main').append(overlay);
  }});
  return {home, trigger, select(key) {select(key); controls.setValue(key);}, close() {overlay?.close();}, dispose() {overlay?.close();}};
}

export function discussionOperation(t, operation) {
  if (!operation) return '';
  if (!['running', 'accepted', 'queued', 'pending'].includes(operation.status)) return discussionLabel(t, 'pd_op_', operation.status);
  const at = operation.started_at || operation.created_at;
  const start = typeof at === 'number' ? at : Date.parse(at) / 1000;
  const seconds = Number.isFinite(start) ? Math.max(0, Math.floor(Date.now() / 1000 - start)) : null;
  return t('pd_working') + (seconds === null ? '' : ` · ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`);
}
