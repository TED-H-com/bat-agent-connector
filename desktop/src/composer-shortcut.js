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
    catch (error) {select.value = mode; status.textContent = t('composer_shortcut_storage_failed');}
  });
  const start = () => {composing = true;}, end = () => {composing = false; ended = performance.now();};
  let currentInput = input, currentButton = button;
  const keydown = event => {
    if (!currentButton) return;
    if (event.key !== 'Enter' || event.shiftKey || event.altKey || event.repeat || event.isComposing || composing
        || event.keyCode === 229 || performance.now() - ended < 50) return;
    const matches = mode === 'modified' ? event.ctrlKey || event.metaKey : mode === 'enter' && !event.ctrlKey && !event.metaKey;
    if (!matches || currentButton.disabled || currentButton.hidden) return;
    try {guard();} catch {return;}
    event.preventDefault(); currentButton.click();
  };
  const listen = node => {
    if (!node) return;
    node.addEventListener('compositionstart', start);
    node.addEventListener('compositionend', end);
    node.addEventListener('keydown', keydown);
  };
  const unlisten = node => {
    if (!node) return;
    node.removeEventListener('compositionstart', start);
    node.removeEventListener('compositionend', end);
    node.removeEventListener('keydown', keydown);
  };
  listen(currentInput);
  const box = h('div', {class: 'actions composer-shortcut'}, h('label', {class: 'muted'}, t('composer_shortcut'), ' ', select),
    h('span', {class: 'muted'}, t('composer_newline')), status);
  return {box, rebind(nextInput, nextButton) {
    unlisten(currentInput);
    currentInput = nextInput || null;
    currentButton = nextButton || null;
    listen(currentInput);
  }, dispose() {unlisten(currentInput); currentInput = null; currentButton = null;}};
}
