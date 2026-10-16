import {buildLine} from './diagnostics.js';
import {button} from './ui/components.js';
export function diagnosticsDialog({h, t, load, save, onClose}) {
  const origin = document.activeElement;
  const summary = h('div', {'data-diagnostics-summary':''});
  const pre = h('pre', {class:'diagnostics-json', tabindex:0, 'aria-label':t('diag_json'), 'data-diagnostics-json':''});
  const status = h('p', {class:'muted',role:'status'}, t('diag_loading'));
  let content = '', filename = '', serial = 0, saving = false;
  const close = () => {serial++; dialog.close(); dialog.remove(); onClose(); if (origin?.isConnected) origin.focus();};
  const saved = button({variant:'primary',label:t('diag_save'),disabled:true,onClick:async () => {
    if (saving || !dialog.isConnected) return;
    const current=serial;
    saving=true; saved.disabled=false; saved.setAttribute('aria-disabled','true'); status.setAttribute('aria-busy','true');
    try {const result=await save(content,filename); if (current===serial && dialog.isConnected && result) status.textContent=t(result === true ? 'diag_download_started' : 'diag_saved');}
    catch {if(current===serial && dialog.isConnected)status.textContent=t('diag_save_failed');}
    finally {
      if (current!==serial || !dialog.isConnected) return;
      saving=false; saved.removeAttribute('aria-disabled'); status.removeAttribute('aria-busy');
      saved.disabled=!content || new TextEncoder().encode(content).length>256*1024;
      if (!saved.disabled && (document.activeElement===document.body || document.activeElement===dialog)) saved.focus();
    }
  }});
  const copy = button({label:t('diag_copy'),disabled:true,onClick:async () => {
    const current=serial;
    try {await navigator.clipboard.writeText(content); if(current===serial)status.textContent=t('diag_copied');}
    catch {if(current===serial){status.textContent=t('diag_copy_failed');pre.focus();}}
  }});
  const closing=button({label:t('diag_close'),onClick:close});
  const dialog=h('dialog', {class:'diagnostics-dialog','aria-labelledby':'diagnostics-title','data-diagnostics-dialog':''},
    h('h2',{id:'diagnostics-title'},t('diag_title')),h('p',{class:'muted'},t('diag_privacy')),summary,pre,status,
    h('div',{class:'actions'},saved,copy,closing));
  dialog.addEventListener('cancel',event=>{event.preventDefault();close();});
  // Explicit wrap supplements showModal's inert background on each WebView.
  dialog.addEventListener('keydown',event=>{
    if(event.key!=='Tab')return;
    const nodes=[pre,saved,copy,closing].filter(node=>!node.hidden && (!node.disabled || node.getAttribute('aria-disabled')==='true')), first=nodes[0],last=nodes.at(-1);
    if(!first||!last)return;
    if(event.shiftKey && (document.activeElement===first || document.activeElement===dialog)){event.preventDefault();last.focus();}
    else if(!event.shiftKey && document.activeElement===last){event.preventDefault();first.focus();}
  });
  document.body.append(dialog);dialog.showModal();closing.focus();
  const withdraw=(force=false)=>{
    if(!content && !force)return;
    serial++;content='';summary.replaceChildren();pre.textContent='';
    const focused=document.activeElement, losing=focused===saved||focused===copy;
    saved.disabled=copy.disabled=true;saved.removeAttribute('aria-disabled');status.removeAttribute('aria-busy');
    status.textContent=t('diag_withdrawn');
    if(losing || document.activeElement===document.body)closing.focus({preventScroll:true});
  };
  const current=serial;
  load().then(bundle=>{
    if(current!==serial || !dialog.isConnected)return;
    content=JSON.stringify(bundle,null,2)+'\n';
    filename='batc-diagnostics-'+bundle.exported_at.slice(0,10).replaceAll('-','')+'-'+bundle.exported_at.slice(11,16).replace(':','')+'.json';
    summary.append(h('div',{},h('p',{},buildLine(bundle.client,t)),
      bundle.sources.hosts==='not_available' ? h('p',{},t('diag_hosts_unavailable')) : null,
      ...bundle.hosts.map(host=>h('p',{},`${host.alias} · ${t('diag_'+(host.connection_state==='unknown'?'state_unknown':host.connection_state))}${host.stale === true && host.connection_state !== 'stale' ? ' · ' + t('diag_stale') : ''}`)),
      h('p',{},t('diag_counts',{errors:t(bundle.recent_errors.length===1?'diag_errors_one':'diag_errors_many',{count:bundle.recent_errors.length}),operations:bundle.operations===null?t('diag_operations_unavailable'):t(bundle.operations.length===1?'diag_operations_one':'diag_operations_many',{count:bundle.operations.length})})),
      h('p',{class:'muted'},t('diag_sample'))));
    pre.textContent=content;
    const tooLarge=new TextEncoder().encode(content).length>256*1024;
    saved.disabled=tooLarge;copy.disabled=false;status.textContent=tooLarge?t('diag_too_large'):'';
  }).catch(()=>{if(current===serial)withdraw(true);});
  return {close,withdraw};
}
