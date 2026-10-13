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
  let disposed = false, busy = false, pending = false, readable = false, snapshot, intent, receipt, damaged = false;
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
  const render = () => {
    if (!alive()) return;
    const native = nativeDesktop && snapshot?.supported === true;
    const state = !nativeDesktop ? 'browser' : snapshot?.supported === false ? 'unsupported' : !readable ? 'unknown'
      : snapshot.installation !== 'available' ? snapshot.installation : snapshot.login;
    const unknown = intent && (!receipt || receipt.phase === 'uncertain');
    content.replaceChildren(...[
      h('p', {}, t('tailscale_'+state)),
      native ? h('p', {class:'muted'}, t('tailscale_help')) : null,
      intent ? h('p', {class:'muted'}, t('tailscale_'+(receipt?.phase || 'uncertain'))) : null,
      intent ? h('details', {}, h('summary', {}, t('tailscale_request')), h('code', {}, intent)) : null,
      damaged ? h('p', {class:'error'}, t('tailscale_damaged')) : null,
      nativeDesktop ? h('div', {class:'actions'},
        h('button', {class:'secondary', disabled:busy || !native || !readable || !snapshot.can_open || damaged || !!intent,
          onclick:()=>run(async()=>{intent=crypto.randomUUID().replaceAll('-','');receipt=null;save();await open();})},t('tailscale_open')),
        unknown && !receipt && !damaged ? h('button',{class:'secondary',disabled:busy || !readable || !snapshot?.can_open,onclick:()=>run(open)},t('tailscale_retry')):null,
        receipt && receipt.phase !== 'uncertain' ? h('button',{class:'secondary',disabled:busy,onclick:()=>run(async()=>{
          const previous=intent;intent=null;try{save();}catch(error){intent=previous;throw error;}receipt=null;
        })},t('tailscale_new')):null,
        h('button',{class:'secondary',disabled:busy,onclick:refresh},t('tailscale_refresh'))) : null,
    ].filter(Boolean));
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
  const run = async action => {
    if (!alive() || busy) return;
    busy=true;message.textContent='';render();
    try {await action();}
    catch {if(alive()){readable=false;message.textContent=t('tailscale_read_failed');}}
    finally {busy=false;render();if(pending&&alive()){pending=false;refresh();}}
  };
  const refresh = () => {if(busy){pending=true;return;}return run(read);};
  const returned = () => {if(document.visibilityState==='visible'&&alive())refresh();};
  render();
  if(nativeDesktop){window.addEventListener('focus',returned);document.addEventListener('visibilitychange',returned);refresh();}
  return () => {disposed=true;window.removeEventListener('focus',returned);document.removeEventListener('visibilitychange',returned);panel.remove();};
}
