import {actionMessageKey} from './global-labels.js';

export function earlierDraftPanel({h, t, entries, current, hide, expanded = false}) {
  const root = h('details', {'data-earlier-drafts': '', class: 'earlier-drafts', open: expanded},
    h('summary', {}, t('earlier_title')), h('p', {class: 'muted'}, t('earlier_help')));
  let position = 0, rendering = false;
  function append(entry) {
    const name = t(actionMessageKey(entry.action) || entry.label);
    const text = h('textarea', {readonly: true, rows: 4, 'aria-label': name}); text.value = entry.text;
    const status = h('span', {role: 'status', class: 'muted'});
    const row = h('article', {'data-earlier-draft': '', class: 'panel'}, h('h3', {}, name),
      h('p', {class: 'muted'}, t('earlier_address', {address: entry.address})), text);
    row.append(h('div', {class: 'actions'},
      h('button', {type: 'button', class: 'secondary', onclick: async () => {
        if (!current()) return;
        try {
          await navigator.clipboard.writeText(entry.text);
          if (current() && row.isConnected) status.textContent = t('earlier_copied');
        } catch {
          if (current() && row.isConnected) {
            status.textContent = t('earlier_copy_manual'); text.focus(); text.select();
          }
        }
      }}, t('earlier_copy')),
      h('button', {type: 'button', class: 'secondary', onclick: () => {
        if (!current() || !hide(entry)) return;
        const next = row.nextElementSibling?.querySelector('button') || row.previousElementSibling?.querySelector('button') || root.querySelector('summary');
        row.remove(); next?.focus();
        if (position === entries.length && !root.querySelector('[data-earlier-draft]')) root.remove();
      }}, t('earlier_hide')), status));
    root.append(row);
  }
  function render() {
    if (!current() || !root.isConnected || !root.open) {rendering = false; return;}
    const first = position === 0;
    const end = Math.min(position + 16, entries.length);
    while (position < end) append(entries[position++]);
    if (first && expanded) {
      root.scrollIntoView({block: 'start'});
      root.querySelector('summary').focus({preventScroll: true});
    }
    if (position < entries.length) setTimeout(render, 0);
    else rendering = false;
  }
  const schedule = () => {
    if (!rendering && root.open && position < entries.length) {rendering = true; setTimeout(render, 0);}
  };
  root.addEventListener('toggle', schedule);
  schedule();
  return root;
}
