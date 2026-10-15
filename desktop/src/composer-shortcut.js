import {errorPresentation} from './error-presentation.js';
// Presentation preference only; the existing button retains all admission guards.
export function composerShortcut({h, t, input, button, storageKey, guard}) {
  const modes = ['modified', 'enter', 'button'];
  let mode = 'modified', composing = false, ended = -Infinity;
  try {const saved = localStorage.getItem(storageKey); if (modes.includes(saved)) mode = saved;} catch { /* default */ }
  const select = h('select', {'aria-label': t('composer_shortcut')}, ...modes.map(value =>
    h('option', {value}, t(`composer_shortcut_${value}`))));
  select.value = mode;
  const status = h('span', {class: 'muted', role: 'status'});
  select.addEventListener('change', () => {
    try {guard(); localStorage.setItem(storageKey, select.value); mode = select.value; status.textContent = '';}
    catch (error) {select.value = mode; status.textContent = errorPresentation(error, t).summary;}
  });
  const start = () => {composing = true;}, end = () => {composing = false; ended = performance.now();};
  const keydown = event => {
    if (event.key !== 'Enter' || event.shiftKey || event.altKey || event.repeat || event.isComposing || composing
        || event.keyCode === 229 || performance.now() - ended < 50) return;
    const matches = mode === 'modified' ? event.ctrlKey || event.metaKey : mode === 'enter' && !event.ctrlKey && !event.metaKey;
    if (!matches || button.disabled || button.hidden) return;
    try {guard();} catch {return;}
    event.preventDefault(); button.click();
  };
  input.addEventListener('compositionstart', start); input.addEventListener('compositionend', end);
  input.addEventListener('keydown', keydown);
  const box = h('div', {class: 'actions composer-shortcut'}, h('label', {class: 'muted'}, t('composer_shortcut'), ' ', select),
    h('span', {class: 'muted'}, t('composer_newline')), status);
  return {box, dispose() {
    input.removeEventListener('compositionstart', start); input.removeEventListener('compositionend', end);
    input.removeEventListener('keydown', keydown);
  }};
}
