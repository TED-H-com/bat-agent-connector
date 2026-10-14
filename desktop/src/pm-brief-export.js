import {disclosure} from './ui/components.js';
import {nativeDesktop,nativeBriefExportSupport,nativeBriefSave} from './transport/index.ts';

// Read a selected report again for each download. Report text is never live markup.
export function briefExport({h,t,api,alive,workspaceId,caps,snapshot,readable,onUnavailable,errorBox}) {
  const actor=caps()?.actor,formats={markdown:['md','text/markdown; charset=utf-8'],html:['html','text/html; charset=utf-8'],svg:['svg','image/svg+xml; charset=utf-8']};
  let busy=false,selection='',pendingReceipt=null;
  const format=h('select',{'aria-label':t('pm_export_format')},...Object.keys(formats).map(key=>h('option',{value:key},key==='markdown'?'Markdown':key.toUpperCase())));
  const locale=h('select',{'aria-label':t('pm_export_language')},h('option',{value:'en'},'English'),h('option',{value:'zh-TW'},'繁體中文'));
  locale.value=navigator.language.toLowerCase().startsWith('zh')?'zh-TW':'en';
  const status=h('div',{role:'status'}),note=h('p',{class:'muted'},t('pm_export_help'));
  const button=h('button',{type:'button',class:'secondary',onclick:()=>void save()},t(nativeDesktop?'pm_export_save':'pm_export_download'));
  const element=disclosure({label:t('pm_export_title'),body:h('div',{},note,
    h('div',{class:'pm-export-fields'},h('label',{},t('pm_export_format'),format),h('label',{},t('pm_export_language'),locale)),
    h('div',{class:'actions'},button),status)}); element.dataset.pmBriefExport='';
  const allowed=()=>readable()&&caps()?.actor===actor&&caps()?.scopes?.includes('observe')&&snapshot()?.snapshot_id
    &&caps()?.features?.pm_workspaces?.brief_export?.formats?.includes(format.value)
    &&caps()?.features?.pm_workspaces?.brief_export?.locales?.includes(locale.value)&&(!nativeDesktop||nativeBriefExportSupport);
  const identity=()=>JSON.stringify([snapshot()?.snapshot_id,snapshot()?.snapshot_digest,snapshot()?.view_digest]);
  function update(){
    const next=identity();if(selection!==next){selection=next;status.replaceChildren();}
    element.hidden=!snapshot();button.disabled=busy||!allowed();format.disabled=locale.disabled=busy;
    note.textContent=t(nativeDesktop&&!nativeBriefExportSupport?'pm_export_native_unavailable':'pm_export_help');
    if(pendingReceipt&&caps()?.actor===actor){
      if(pendingReceipt.error){status.replaceChildren(errorBox(pendingReceipt.error));return;}
      const value=pendingReceipt.receipt;
      status.replaceChildren(h('p',{class:'muted'},value===null?t('pm_export_cancelled'):
        allowed()&&identity()===pendingReceipt.expected?`${t('pm_export_saved')}: ${value.display_name}`:t('pm_export_prior_saved')));
    }
  }
  function live(expected){alive();if(caps()?.actor!==actor||!allowed()||identity()!==expected)throw Object.assign(Error(t('pm_export_changed')),{code:'PM_EXPORT_VIEW_CHANGED'});}
  async function save(){
    if(busy||!allowed())return;busy=true;pendingReceipt=null;update();const expected=identity(),source=snapshot(),kind=format.value,language=locale.value;
    let nativeSaveStarted=false;
    status.replaceChildren(h('p',{class:'muted'},t('pm_export_reading')));
    try{
      const report=await api('GET',`/pm/workspaces/${encodeURIComponent(workspaceId)}/snapshots/${encodeURIComponent(source.snapshot_id)}/brief-export?format=${kind}&locale=${language}`);
      live(expected);
      if(report?.version!==1||report.workspace_id!==workspaceId||report.snapshot_id!==source.snapshot_id||report.format!==kind||report.locale!==language
        ||report.snapshot_digest!==source.snapshot_digest||report.view_digest!==source.view_digest||report.media_type!==formats[kind][1]
        ||report.template_version!==1||report.renderer_version!==(source.brief?.deviations?.version===1?4:source.brief?.delivery?.quality?3:source.maintenance?2:1)
        ||report.coverage?.projection!==(source.maintenance?'project_brief_with_maintenance_review':'project_brief_only')||report.coverage.content_truncated!==false
        ||report.execution_authorized!==false||report.review_completed!==false||report.published!==false||typeof report.content!=='string'
        ||!Number.isSafeInteger(report.byte_length)||report.byte_length<0||report.byte_length>1500000||!(/^[0-9a-f]{64}$/).test(report.output_digest))throw Error(t('pm_export_changed'));
      const bytes=new TextEncoder().encode(report.content);
      if(bytes.length!==report.byte_length)throw Error(t('pm_export_changed'));
      const digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),value=>value.toString(16).padStart(2,'0')).join('');
      live(expected);if(digest!==report.output_digest)throw Error(t('pm_export_changed'));
      if(nativeDesktop){
        nativeSaveStarted=true;
        const receipt=await nativeBriefSave({workspace_id:workspaceId,snapshot_id:source.snapshot_id,format:kind,locale:language,
          expected_view_digest:report.view_digest,expected_output_digest:report.output_digest});alive();
        if(caps()?.actor!==actor)return;
        if(receipt!==null&&(receipt?.stage!=='saved'||receipt.workspace_id!==workspaceId||receipt.snapshot_id!==source.snapshot_id||receipt.format!==kind||receipt.locale!==language
          ||receipt.view_digest!==report.view_digest||receipt.output_digest!==digest||receipt.byte_length!==bytes.length||typeof receipt.display_name!=='string'))throw Error(t('pm_export_changed'));
        pendingReceipt={expected,receipt};update();
      }else{
        const url=URL.createObjectURL(new Blob([bytes],{type:report.media_type}));
        try{const link=document.createElement('a');link.href=url;link.download=`pm-brief-${source.snapshot_id}-${language}.${formats[kind][0]}`;link.click();}
        finally{setTimeout(()=>URL.revokeObjectURL(url),1000);}
        status.replaceChildren(h('p',{class:'muted'},t('pm_export_requested')));
      }
    }catch(error){
      try{alive();}catch{return;}
      if(caps()?.actor!==actor)return;
      const code=String(error?.code||error?.message||error);
      const key=Object.entries({BRIEF_EXPORT_CHANGED:'pm_export_changed',BRIEF_EXPORT_UNAVAILABLE:'pm_export_unavailable',BRIEF_EXPORT_SAVE_INTERRUPTED:'pm_export_interrupted',
        DESTINATION_EXISTS:'pm_export_exists',DESTINATION_UNAVAILABLE:'pm_export_destination'}).find(([prefix])=>code.includes(prefix))?.[1];
      const rendered=key?Error(t(key)):error;
      if(nativeSaveStarted&&['pm_export_exists','pm_export_destination','pm_export_interrupted'].includes(key)){
        pendingReceipt={expected,error:rendered};update();return;
      }
      if(identity()!==expected)return;
      onUnavailable(rendered);
      status.replaceChildren(errorBox(rendered));
    }finally{busy=false;update();}
  }
  format.addEventListener('change',update);locale.addEventListener('change',update);update();
  return {element,update};
}
