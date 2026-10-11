import {nativeDesktop,nativeFixedViewExportSupport,nativeBriefSave} from './transport/index.ts';

// The preview renders structured literal fields; exported markup never enters app DOM.
export function fixedSnapshotViews({h,t,api,alive,workspaceId,caps,snapshot,readable,onUnavailable,errorBox}){
  const actor=caps()?.actor,views=['roadmap','kanban','sprint','governance'];
  let serial=0,identity='',model=null,busy=false,receipt=null;
  const view=h('select',{'aria-label':t('pm_fixed_view')},...views.map(value=>h('option',{value},t('pm_fixed_'+value))));
  const locale=h('select',{'aria-label':t('pm_export_language')},h('option',{value:'en'},'English'),h('option',{value:'zh-TW'},'繁體中文'));
  locale.value=navigator.language.toLowerCase().startsWith('zh')?'zh-TW':'en';
  const format=h('select',{'aria-label':t('pm_export_format')},h('option',{value:'svg'},'SVG'),h('option',{value:'html'},'HTML'));
  const note=h('p',{class:'muted'}),status=h('div',{role:'status'}),content=h('div',{'data-pm-fixed-preview':''});
  const preview=h('button',{type:'button',class:'secondary',onclick:()=>void read()},t('pm_fixed_preview'));
  const save=h('button',{type:'button',class:'secondary',onclick:()=>void download()},t(nativeDesktop?'pm_export_save':'pm_export_download'));
  const element=h('details',{'data-pm-fixed-views':''},h('summary',{},t('pm_fixed_title')),note,
    h('div',{class:'pm-export-fields'},h('label',{},t('pm_fixed_view'),view),h('label',{},t('pm_export_language'),locale),h('label',{},t('pm_export_format'),format)),
    h('div',{class:'actions'},preview,save),status,content);
  const current=()=>JSON.stringify([actor,snapshot()?.snapshot_id,snapshot()?.snapshot_digest,snapshot()?.view_digest,view.value,locale.value]);
  const canRead=()=>readable()&&caps()?.actor===actor&&caps()?.scopes?.includes('observe')&&snapshot()?.management_views?.version===1
    &&caps()?.features?.pm_workspaces?.fixed_view_exports?.views?.includes(view.value);
  const canSave=()=>canRead()&&Boolean(model)&&(!nativeDesktop||nativeFixedViewExportSupport);
  function update(){
    const next=current();if(identity!==next){identity=next;++serial;model=null;content.replaceChildren();status.replaceChildren();}
    element.hidden=!snapshot();preview.disabled=busy||!canRead();save.disabled=busy||!canSave();
    view.disabled=locale.disabled=format.disabled=busy;
    note.textContent=t(!snapshot()?.management_views?'pm_fixed_old':nativeDesktop&&!nativeFixedViewExportSupport?'pm_fixed_native':'pm_fixed_help');
    if(receipt?.identity===identity)status.replaceChildren(receipt.error?errorBox(receipt.error):h('p',{class:'muted'},receipt.text));
  }
  function valid(expected,ticket){alive();if(ticket!==serial||current()!==expected||!canRead())throw Object.assign(Error(t('pm_export_changed')),{code:'PM_EXPORT_VIEW_CHANGED'});}
  function check(value,source,chosen,language){
    if(value?.version!==1||value.workspace_id!==workspaceId||value.snapshot_id!==source.snapshot_id||value.snapshot_digest!==source.snapshot_digest
      ||value.view_digest!==source.view_digest||value.view!==chosen||value.locale!==language||value.template_version!==1||value.view_template_version!==1
      ||value.execution_authorized!==false||value.review_completed!==false||value.published!==false||value.coverage?.projection!==chosen||value.coverage.content_truncated!==false)
      throw Error(t('pm_export_changed'));
    return value;
  }
  function render(){
    content.replaceChildren(h('p',{class:'muted'},t('pm_fixed_historical')),h('h3',{},model.title));
    if(model.coverage.visibility?.filtered||Object.entries(model.coverage.view||{}).some(([key,value])=>['truncated','byte_limited','visibility_filtered'].includes(key)&&value))
      content.append(h('p',{class:'muted'},t('pm_fixed_partial')));
    if(model.quality)content.append(h('p',{},t('pq_count',{qualified:model.quality.qualified_requirements,total:model.quality.total_requirements})));
    for(const group of model.groups){
      const items=h('div',{class:'pm-brief-values'});
      if(!group.items.length)items.append(h('p',{class:'muted'},t('pm_fixed_empty')));
      for(const item of group.items){
        const details=h('details',{'data-pm-fixed-record':item.id},h('summary',{},item.title));
        for(const field of item.fields)details.append(h('div',{},h('strong',{},field.label),h('pre',{class:'pm-brief-proof'},field.value)));
        items.append(details);
      }
      content.append(h('section',{class:'pm-brief-row'},h('h3',{},group.title),items));
    }
    content.append(h('details',{},h('summary',{},t('pm_fixed_sources')),h('pre',{class:'pm-brief-proof'},JSON.stringify({snapshot_id:model.snapshot_id,
      snapshot_digest:model.snapshot_digest,view_digest:model.view_digest,as_of:model.as_of,journal_cursor:model.journal_cursor,scope_revision:model.scope_revision,
      counts:model.counts,sources:model.sources,coverage:model.coverage},null,2))));
  }
  function failure(error,expected,ticket){
    try{alive();}catch{return;}if(ticket!==serial||current()!==expected||caps()?.actor!==actor)return;
    model=null;content.replaceChildren();status.replaceChildren(errorBox(error));
    if([401,403,404].includes(error?.status)||['FORBIDDEN','NOT_FOUND','PM_SNAPSHOT_VIEW_SOURCE_UNAVAILABLE'].includes(error?.code))onUnavailable(error);
  }
  async function read(){
    if(busy||!canRead())return;const source=snapshot(),chosen=view.value,language=locale.value,expected=current(),ticket=++serial;
    busy=true;receipt=null;update();status.replaceChildren(h('p',{class:'muted'},t('pm_export_reading')));
    try{
      const value=await api('GET',`/pm/workspaces/${workspaceId}/snapshots/${source.snapshot_id}/view?view=${chosen}&locale=${language}`);
      valid(expected,ticket);check(value,source,chosen,language);
      if(!Array.isArray(value.groups)||value.groups.some(group=>typeof group.title!=='string'||!Array.isArray(group.items)
        ||group.items.some(item=>typeof item.id!=='string'||typeof item.title!=='string'||!Array.isArray(item.fields)
          ||item.fields.some(field=>typeof field.label!=='string'||typeof field.value!=='string'))))throw Error(t('pm_export_changed'));
      model=value;status.replaceChildren();render();
    }catch(error){failure(error,expected,ticket);}finally{busy=false;update();}
  }
  async function download(){
    if(busy||!canSave())return;const source=snapshot(),chosen=view.value,language=locale.value,kind=format.value,expected=current(),ticket=++serial;
    busy=true;receipt=null;update();status.replaceChildren(h('p',{class:'muted'},t('pm_export_reading')));
    try{
      const report=await api('GET',`/pm/workspaces/${workspaceId}/snapshots/${source.snapshot_id}/view-export?view=${chosen}&format=${kind}&locale=${language}`);
      valid(expected,ticket);check(report,source,chosen,language);
      const media=kind==='svg'?'image/svg+xml; charset=utf-8':'text/html; charset=utf-8';
      if(report.format!==kind||report.renderer_version!==1||report.media_type!==media||typeof report.content!=='string'
        ||!Number.isSafeInteger(report.byte_length)||report.byte_length<1||report.byte_length>1500000||!(/^[0-9a-f]{64}$/).test(report.output_digest)
        ||(kind==='svg'?!Number.isInteger(report.page_count)||report.page_count<1||report.page_count>32:report.page_count!==null))throw Error(t('pm_export_changed'));
      const bytes=new TextEncoder().encode(report.content),digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),v=>v.toString(16).padStart(2,'0')).join('');
      valid(expected,ticket);if(bytes.length!==report.byte_length||digest!==report.output_digest)throw Error(t('pm_export_changed'));
      if(nativeDesktop){
        const result=await nativeBriefSave({workspace_id:workspaceId,snapshot_id:source.snapshot_id,view:chosen,format:kind,locale:language,
          expected_view_digest:report.view_digest,expected_output_digest:report.output_digest});alive();if(caps()?.actor!==actor)return;
        if(result!==null&&(result.stage!=='saved'||result.workspace_id!==workspaceId||result.snapshot_id!==source.snapshot_id||result.view!==chosen
          ||result.format!==kind||result.locale!==language||result.view_digest!==report.view_digest||result.output_digest!==digest
          ||result.byte_length!==bytes.length||typeof result.display_name!=='string'))throw Error(t('pm_export_changed'));
        receipt={identity:expected,text:result===null?t('pm_export_cancelled'):`${t('pm_export_saved')}: ${result.display_name}`};
      }else{
        const url=URL.createObjectURL(new Blob([bytes],{type:media}));
        try{const link=document.createElement('a');link.href=url;link.download=`pm-${chosen}-${source.snapshot_id}-${language}.${kind}`;link.click();}
        finally{setTimeout(()=>URL.revokeObjectURL(url),1000);}
        receipt={identity:expected,text:t('pm_export_requested')};
      }
    }catch(error){
      const code=String(error?.code||error?.message||error),key=Object.entries({DESTINATION_EXISTS:'pm_export_exists',DESTINATION_UNAVAILABLE:'pm_export_destination',BRIEF_EXPORT_SAVE_INTERRUPTED:'pm_export_interrupted'})
        .find(([prefix])=>code.includes(prefix))?.[1];
      if(key)receipt={identity:expected,error:Error(t(key))};else failure(error,expected,ticket);
    }finally{busy=false;update();}
  }
  view.addEventListener('change',update);locale.addEventListener('change',update);format.addEventListener('change',update);
  update();return {element,update};
}
