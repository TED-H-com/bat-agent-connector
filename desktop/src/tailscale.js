import {iconButton} from './ui/components.js';
import {nativeDesktop, tailscaleControl} from './transport/index.ts';

// Local login-scoped evidence; never mixed with central actor, operations or event cursors.
export function mountTailscale(main, {h, t, presentation} = {}) {
  // Browser builds only explain the local app. Keep the named region and do not call IPC.
  if (!nativeDesktop && presentation === 'note') {
    const note = h('section', {class: 'global-tailscale-note', 'aria-label': t('tailscale_title')},
      h('p', {}, t('tailscale_browser')));
    main.append(note);
    return () => note.remove();
  }
  const panel = h('section', {class: 'panel', 'aria-label': t('tailscale_title')});
  const content = h('div'), message = h('p', {class: 'muted', role: 'status'});
  panel.append(h('h2', {}, t('tailscale_title')), content, message); main.append(panel);
  let disposed = false, busy = false, pending = false, readable = false, snapshot, intent, receipt, damaged = false, pressed = null;
  const alive = () => !disposed && panel.isConnected;
  const hex = (value, size) => typeof value === 'string' && new RegExp(`^[0-9a-f]{${size}}$`).test(value);
  const validReceipt = value => value && hex(value.request_id, 32) && ['uncertain','started','not_started'].includes(value.phase);
  const key = () => `batc.desktop.tailscale.${snapshot.scope}`;
  const accept = (value, scope, id) => {
    if (value?.version !== 1 || value.scope !== scope || value.receipt !== null && (!validReceipt(value.receipt) || value.receipt.request_id !== id)) throw Error('invalid');
    receipt = value.receipt;
  };
  const save = () => {
    if (intent) localStorage.setItem(key(), JSON.stringify({request_id:intent}));
    else localStorage.removeItem(key());
  };
  const placeFocus = key => {
    if (!alive() || !key) return;
    const nodes = [...content.querySelectorAll('[data-tailscale-action]')];
    const find = name => nodes.find(node => node.getAttribute('data-tailscale-action') === name);
    const usable = node => node && !node.disabled && !node.closest('[hidden]');
    const current = find(key);
    if (usable(current)) { current.focus({preventScroll: true}); return; }
    const named = key === 'open' || key === 'retry' ? (usable(find('new')) ? find('new') : find('refresh'))
      : key === 'new' ? (usable(find('open')) ? find('open') : find('refresh')) : null;
    if (usable(named)) { named.focus({preventScroll: true}); return; }
    const group = [...content.querySelectorAll('.actions button')].filter(usable);
    if (group.length) { group[0].focus({preventScroll: true}); return; }
    const any = nodes.filter(usable);
    if (any.length) { any[0].focus({preventScroll: true}); return; }
    message.tabIndex = -1; message.focus({preventScroll: true});
  };
  const render = () => {
    if (!alive()) return;
    const active = document.activeElement;
    const key = active instanceof Element && content.contains(active) ? active.getAttribute('data-tailscale-action') : null;
    const native = nativeDesktop && snapshot?.supported === true;
    const state = !nativeDesktop ? 'browser' : snapshot?.supported === false ? 'unsupported' : !readable ? 'unknown'
      : snapshot.installation !== 'available' ? snapshot.installation : snapshot.login;
    const unknown = intent && (!receipt || receipt.phase === 'uncertain');
    let flying = false;
    const gate = (name, staticBlocked) => {
      const blocked = Boolean(staticBlocked) && pressed !== name;
      const soft = busy && !blocked;
      if (soft) flying = true;
      return {disabled: blocked || null, 'aria-disabled': soft ? 'true' : null};
    };
    const action = (name, label, staticBlocked, onclick) => h('button', {
      type: 'button', class: 'secondary', 'data-tailscale-action': name, ...gate(name, staticBlocked), onclick,
    }, label);
    content.replaceChildren(...[
      h('p', {}, t('tailscale_'+state)),
      native ? h('p', {class:'muted'}, t('tailscale_help')) : null,
      intent ? h('p', {class:'muted'}, t('tailscale_'+(receipt?.phase || 'uncertain'))) : null,
      intent ? h('details', {}, h('summary', {'data-tailscale-action': 'request'}, t('tailscale_request')), h('code', {}, intent)) : null,
      damaged ? h('p', {class:'error'}, t('tailscale_damaged')) : null,
      nativeDesktop ? h('div', {class:'actions'},
        action('open', t('tailscale_open'), !native || !readable || !snapshot.can_open || damaged || !!intent,
          () => run('open', async () => {intent=crypto.randomUUID().replaceAll('-','');receipt=null;save();await open();})),
        unknown && !receipt && !damaged ? action('retry', t('tailscale_retry'), !readable || !snapshot?.can_open, () => run('retry', open)) : null,
        receipt && receipt.phase !== 'uncertain' ? action('new', t('tailscale_new'), false, () => run('new', async () => {
          const previous=intent;intent=null;try{save();}catch(error){intent=previous;throw error;}receipt=null;
        })) : null,
        iconButton({icon:'refresh-cw', label:t('tailscale_refresh'), 'data-tailscale-action':'refresh', ...gate('refresh', false),
          onclick: () => { if (busy || !alive()) return; refresh(); }})) : null,
    ].filter(Boolean));
    if (flying) message.setAttribute('aria-busy', 'true'); else message.removeAttribute('aria-busy');
    if (key) placeFocus(key);
  };
  const read = async () => {
    const value = await tailscaleControl({action:'status'});
    if (!alive()) return;
    if (value?.version !== 1 || typeof value.supported !== 'boolean' || typeof value.can_open !== 'boolean'
      || !['missing','incomplete','available','unknown'].includes(value.installation)
      || !['unknown','needs_login','needs_approval','stopped','starting','running'].includes(value.login)
      || value.latest !== null && !validReceipt(value.latest) || value.supported && !hex(value.scope,64)) throw Error('invalid');
    if (value.scope !== snapshot?.scope) {
      intent=receipt=null;damaged=false;snapshot=value;
      if (value.supported) {
        try {
          const saved = JSON.parse(localStorage.getItem(key()) || 'null');
          if (saved && hex(saved.request_id,32)) intent=saved.request_id;
          else if (saved) damaged=true;
        } catch {damaged=true;}
      }
    }
    snapshot=value;
    if (value.latest?.phase === 'uncertain' && (!intent || intent !== value.latest.request_id)) {
      intent=value.latest.request_id;receipt=value.latest;save();
    }
    if (intent) {
      const result=await tailscaleControl({action:'receipt',request_id:intent});
      if (!alive()) return;
      accept(result,value.scope,intent);
    }
    readable=true;
  };
  const open = async () => {
    if (!intent || !snapshot?.supported || damaged) return;
    save(); // A later explicit retry must also prove the original ID is persisted.
    const scope=snapshot.scope,id=intent;
    const value=await tailscaleControl({action:'open',request_id:id});
    if (!alive()) return;
    accept(value,scope,id);
    pending=true; // only diagnosis after an explicit launch; never a second launch.
  };
  const run = async (name, action) => {
    if (!alive() || busy) return;
    busy=true;pressed=name;message.textContent='';render();
    try {await action();}
    catch {if(alive()){readable=false;message.textContent=t('tailscale_read_failed');}}
    finally {busy=false;pressed=null;if(!alive())return;render();if(pending&&alive()){pending=false;refresh();}}
  };
  const refresh = () => {if(busy){pending=true;return;}return run('refresh', read);};
  const returned = () => {if(document.visibilityState==='visible'&&alive())refresh();};
  render();
  if(nativeDesktop){window.addEventListener('focus',returned);document.addEventListener('visibilitychange',returned);refresh();}
  return () => {disposed=true;window.removeEventListener('focus',returned);document.removeEventListener('visibilitychange',returned);panel.remove();};
}
