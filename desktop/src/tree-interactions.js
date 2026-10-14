// Reordering submits one complete sibling scope through the existing operation.
// The in-memory drag source rejects foreign payloads and cross-parent drops.
import {iconButton} from './ui/components.js';
let drag = null;
const ids = (rows, key) => rows.map(row => row[key]);
const same = (a, b) => a.length === b.length && a.every((value, i) => value === b[i]);
export function treeInteractions({h, t, row, scope, siblings, index, key, run, open, guard, editing}) {
  const me = siblings[index];
  const handle = iconButton({icon: 'grip-vertical', label: t('tree_drag', {name: me.name || me.title}),
    draggable: 'true', title: t('tree_drag_help')});
  handle.classList.add('tree-drag-handle');
  const clear = () => {
    if (drag) {drag.editing(false); drag = null;}
    for (const target of document.querySelectorAll('.tree-drop-target')) target.classList.remove('tree-drop-target');
  };
  const allowed = () => drag && drag.row.isConnected && row.isConnected && drag.scope === scope
    && drag.pinned === me.pinned && same(drag.before, ids(siblings, key));
  handle.addEventListener('dragstart', event => {
    if (!event.dataTransfer) {event.preventDefault(); return;}
    try {guard();} catch {event.preventDefault(); return;}
    clear();
    drag = {row, scope, index, pinned: me.pinned, before: ids(siblings, key),
      versions: Object.fromEntries(siblings.map(value => [value[key], value.version])), editing};
    editing(true);
    event.dataTransfer.setData('text/plain', me[key]); event.dataTransfer.effectAllowed = 'move';
  });
  handle.addEventListener('dragend', clear);
  row.addEventListener('dragover', event => {
    if (!allowed()) return;
    event.preventDefault(); event.dataTransfer.dropEffect = 'move'; row.classList.add('tree-drop-target');
  });
  row.addEventListener('dragleave', event => {if (!row.contains(event.relatedTarget)) row.classList.remove('tree-drop-target');});
  row.addEventListener('drop', event => {
    if (!allowed()) return;
    event.preventDefault();
    const intent = drag, order = intent.before.slice();
    order.splice(index, 0, order.splice(intent.index, 1)[0]);
    clear();
    if (same(order, intent.before)) return;
    try {guard(); run('order', intent.before, order, intent.versions);} catch { /* retired view */ }
  });
  row.addEventListener('contextmenu', event => {
    if (event.shiftKey || event.target.closest('input,textarea,select')) return;
    try {guard();} catch {return;}
    event.preventDefault(); open();
  });
  row.addEventListener('keydown', event => {
    if (event.key !== 'ContextMenu' && !(event.key === 'F10' && event.shiftKey)) return;
    if (event.target.closest('input,textarea,select')) return;
    try {guard();} catch {return;}
    event.preventDefault(); open();
  });
  return handle;
}
