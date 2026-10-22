import {actionMessageKey} from './global-labels.js';

export function earlierFieldLabel(t, field) {
  if (/^reason_\d+$/.test(field)) return `${t('earlier_field_reason')} ${Number(field.slice(7)) + 1}`;
  return field.split('.').map(part => /^\d+$/.test(part) ? String(Number(part) + 1) : t('earlier_field_' + part)).join(' · ');
}

export function earlierDraftPanel({h, t, entries, current, hide, opened, emptyFocus, expanded = false}) {
  const root = h('details', {'data-earlier-drafts': '', class: 'earlier-drafts', open: expanded},
    h('summary', {}, t('earlier_title')), h('p', {class: 'muted'}, t('earlier_help')));
  const groups = new Map();
  for (const state of ['draft', 'possibly-sent']) if (entries.some(entry => entry.state === state)) {
    const group = h('section', {'data-earlier-group': state}, h('h3', {}, t(state === 'draft' ? 'earlier_draft_group' : 'earlier_pending_group')));
    groups.set(state, group); root.append(group);
  }
  let position = 0, rendering = false;
  function append(entry) {
    const name = `${t(actionMessageKey(entry.action) || entry.label)} · ${earlierFieldLabel(t, entry.field)}`;
    const addressId = 'earlier-address-' + entry.id;
    const text = h('textarea', {readonly: true, rows: 4, 'aria-label': name, 'aria-describedby': addressId}); text.value = entry.text;
    const status = h('span', {role: 'status', class: 'muted'});
    const row = h('article', {'data-earlier-draft': '', class: 'panel'}, h('h4', {}, name),
      h('p', {id: addressId, class: 'muted'}, t('earlier_address', {address: entry.address})), text);
    const actions = h('div', {class: 'actions'});
    if (entry.state === 'possibly-sent') {
      row.insertBefore(h('p', {}, t('earlier_pending_warning')), text);
      for (const href of entry.operations.length ? entry.operations.map(id => '#/op/' + id) : [entry.href])
        actions.append(h('a', {href}, t(entry.operations.length ? 'earlier_check_operation' : 'earlier_check_form')));
    } else actions.append(h('button', {type: 'button', class: 'secondary', onclick: async () => {
      if (!current()) return;
      try {
        await navigator.clipboard.writeText(entry.text);
        if (current() && row.isConnected) status.textContent = t('earlier_copied');
      } catch {
        if (current() && row.isConnected) {
          status.textContent = t('earlier_copy_manual'); text.focus(); text.select();
        }
      }
    }}, t('earlier_copy')));
    actions.append(h('button', {type: 'button', class: 'secondary', onclick: () => {
      if (!current() || !hide(entry)) return;
      const rows = [...root.querySelectorAll('[data-earlier-draft]')], index = rows.indexOf(row);
      const next = rows[index + 1]?.querySelector('button') || rows[index - 1]?.querySelector('button');
      const group = row.parentElement; row.remove();
      if (!group.querySelector('[data-earlier-draft]') && position === entries.length) group.remove();
      if (position === entries.length && !root.querySelector('[data-earlier-draft]')) {root.remove(); emptyFocus();}
      else (next || root.querySelector('summary'))?.focus({preventScroll: true});
    }}, t('earlier_hide')), status);
    row.append(actions); groups.get(entry.state).append(row);
  }
  function render() {
    if (!current() || !root.isConnected || !root.open) {rendering = false; return;}
    const end = Math.min(position + 16, entries.length);
    while (position < end) append(entries[position++]);
    if (position < entries.length) setTimeout(render, 0);
    else rendering = false;
  }
  const schedule = () => {
    if (root.open && root.isConnected && current()) opened();
    if (!rendering && root.open && position < entries.length) {rendering = true; setTimeout(render, 0);}
  };
  root.addEventListener('toggle', schedule);
  root.reveal = () => {
    root.open = true; schedule();
    root.scrollIntoView({block: 'start'}); root.querySelector('summary').focus({preventScroll: true});
  };
  root.resume = schedule;
  return root;
}
