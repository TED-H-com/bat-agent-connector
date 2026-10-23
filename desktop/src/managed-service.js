// One native Settings control for connected and deliberately stopped installations.
export async function managedServiceControls(root, {h, t, managedControl, errorBox, guard, onStarted}) {
  const box = h('section', {'data-managed-service': ''});
  const facts = h('p', {'data-managed-lifecycle': '', role: 'status'});
  const time = h('time');
  const actions = h('div', {class: 'actions'});
  const preview = h('div', {'data-managed-stop-preview': '', hidden: true});
  const errors = h('div', {'aria-live': 'polite'});
  let status, busy = false;
  const run = async action => {
    if (busy) return;
    busy = true; paint();
    try {
      guard(); status = await managedControl({action}); guard();
      errors.replaceChildren(); preview.hidden = true;
      // Reconnect only the local managed binding. Both-role external bindings stay selected.
      if (action === 'start' && status.mode === 'managed') await onStarted?.();
    } catch (error) {
      try {guard();} catch {return;}
      // Read the owner's hold after refusal/uncertainty; never repeat the effect.
      try {status = await managedControl({action: 'status'}); guard();} catch { /* retain the last observation */ }
      const code = String(error?.message || error);
      if (code.includes('SERVICE_STILL_STOPPING')) errors.replaceChildren(h('p', {}, t('managed_still_stopping')));
      else if (code === 'MANAGED_SERVICE_BUSY') errors.replaceChildren(h('p', {}, t('managed_action_busy')));
      else if (code === 'SERVICE_BUSY') errors.replaceChildren(h('p', {}, t('managed_busy')));
      else if (code.includes('SERVICE_STOPPED')) errors.replaceChildren(h('p', {}, t(status?.lifecycle?.state === 'stop_pending' ? 'managed_stopping' : 'managed_stopped')));
      else errors.replaceChildren(errorBox(error, {sourceRefresh: false, within: errors}));
    } finally {busy = false; paint();}
  };
  const stop = h('button', {type: 'button', class: 'secondary', onclick: () => {preview.hidden = false;}}, t('managed_stop'));
  const start = h('button', {type: 'button', class: 'primary', onclick: () => run('start')}, t('managed_start'));
  const open = h('button', {type: 'button', class: 'secondary', onclick: () => run('open_browser')}, t('managed_open_browser'));
  const login = h('input', {type: 'checkbox'});
  login.addEventListener('change', async () => {
    if (busy) return;
    busy = true; login.disabled = true;
    try {guard(); status = await managedControl({action: 'set_login', enabled: login.checked}); guard(); errors.replaceChildren();}
    catch (error) {try {guard(); errors.replaceChildren(errorBox(error, {within: errors}));} catch {return;}}
    finally {busy = false; paint();}
  });
  const confirm = h('button', {type: 'button', class: 'danger', onclick: () => run('stop')}, t('managed_stop_confirm'));
  preview.append(h('p', {}, t('managed_stop_local')), h('p', {}, t('managed_stop_hold')),
    h('p', {}, t('managed_stop_busy')), h('p', {}, t('managed_stop_external')),
    h('div', {class: 'actions'}, confirm, h('button', {type: 'button', class: 'secondary', onclick: () => {preview.hidden = true;}}, t('cancel'))));
  function paint() {
    const lifecycle = status?.lifecycle;
    const held = lifecycle?.state === 'stopped' || lifecycle?.state === 'stop_pending';
    facts.textContent = t(lifecycle?.state === 'stop_pending' ? 'managed_stopping' : held ? 'managed_stopped' : 'managed_running');
    const at = lifecycle?.stopped_at || lifecycle?.requested_at;
    time.textContent = at ? new Date(at * 1000).toLocaleString() : '';
    if (at) time.dateTime = new Date(at * 1000).toISOString();
    stop.hidden = lifecycle?.state === 'stopped'; start.hidden = !held; open.disabled = busy || held;
    for (const button of [stop, start, confirm]) button.disabled = busy;
    login.disabled = busy; login.checked = !!status?.login_enabled;
  }
  try {
    status = await managedControl({action: 'status'}); guard();
    if (!status.background || !status.lifecycle) return status;
    actions.append(stop, start, open, h('label', {}, login, ' ', t('managed_start_login')));
    box.append(facts, time, actions, preview, errors); paint(); root.append(box);
  } catch (error) {guard(); root.append(errorBox(error, {within: root}));}
  return status;
}
