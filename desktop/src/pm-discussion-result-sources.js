import {discussHref} from './pm-discuss-link.js';
import {sessionOrigin} from './session-navigation.js';

// Select immutable assessment receipts before creating a discussion. Existing
// thread origins remain unchanged; this component only reads central sources.
export function discussionResultSources({h,t,api,alive,workspaceId,labelled,available}){
  const work=h('select',{'aria-label':t('pmq_compare_work')}),assessment=h('select',{'aria-label':t('pmq_compare_assessment')});
  const status=h('p',{class:'muted',role:'status'}),link=h('a',{},t('pmq_compare_open'));
  const more=h('button',{type:'button',class:'secondary',hidden:true,onclick:()=>void load(true)},t('pmq_compare_more'));
  const element=h('details',{'data-quality-compare':''},h('summary',{},t('pmq_compare_heading')),
    h('p',{class:'muted'},t('pmq_compare_help')),h('div',{class:'capture-fields'},labelled('pmq_compare_work',work),labelled('pmq_compare_assessment',assessment)),status,more,h('p',{},link));
  let serial=0,primary=null,catalog=[],choices=new Map(),seen=new Set(),cursor=null,pages=0,filtered=false,busy=false;
  let sourceKey='',workId='',assessmentId='';
  const op=/^op_[0-9a-f]{32}$/,digest=/^[0-9a-f]{64}$/,sha=/^[0-9a-f]{40}$/;
  const ref=(row,item)=>({kind:'quality_assessment',id:item.operation_id,review_id:row.review_id,document_digest:item.document_digest});
  const key=value=>JSON.stringify(value);
  const valid=ticket=>{try{alive();return ticket===serial&&primary&&available();}catch{return false;}};
  const choose=field=>field.replaceChildren(h('option',{value:''},t('pmq_choose')));
  function update(){
    const selected=choices.get(assessmentId);
    work.disabled=!primary||busy;assessment.disabled=!primary||busy||!choices.size;more.disabled=busy;
    link.hidden=busy||!primary||!selected||!available();link.removeAttribute('href');
    if(!link.hidden)link.href=discussHref({workspaceId,title:t('pmq_compare_title'),from:sessionOrigin(location.hash)||'board',
      refs:[primary.ref,{kind:'work_item',id:primary.work},selected.ref,{kind:'work_item',id:workId}]});
  }
  function invalidate(){serial++;primary=null;catalog=[];choices.clear();seen.clear();cursor=null;pages=0;filtered=busy=false;
    choose(work);choose(assessment);status.textContent='';more.hidden=true;update();}
  function withdraw(){invalidate();sourceKey=workId=assessmentId='';element.open=false;}
  function refresh(){
    // A second Work can change without changing this Result's projection.
    // Retire its prior response and link on every parent source refresh.
    serial++;busy=false;choices.clear();seen.clear();cursor=null;pages=0;filtered=false;
    choose(assessment);status.textContent='';more.hidden=true;update();
    if(element.open&&primary&&workId)void load();
  }
  function setSources(row,packages){
    invalidate();if(!row?.assessment||!available())return;
    const first=ref(row,row.assessment),signature=key(first);
    if(sourceKey!==signature){workId=assessmentId='';sourceKey=signature;}
    primary={ref:first,work:row.work_item_id};catalog=packages;
    const works=new Map(packages.map(pkg=>[pkg.work_item_id,pkg.document.title]));
    for(const [id,title]of works)work.append(h('option',{value:id},title));
    if(!works.has(workId))workId=assessmentId='';work.value=workId;update();
    if(element.open&&workId)void load();
  }
  async function load(append=false){
    if(!primary||!workId||busy||!available())return;
    const ticket=++serial,id=workId,after=append?cursor:0,selected=assessmentId;
    if(!append){choices.clear();seen.clear();cursor=null;pages=0;filtered=false;choose(assessment);}
    busy=true;status.textContent=t('pmq_loading');more.hidden=true;update();
    try{
      const value=await api('GET',`/pm/workspaces/${encodeURIComponent(workspaceId)}/work/${id}/quality?after=${after}&limit=20`);
      if(!valid(ticket))return;
      if(value.workspace_id!==workspaceId||!Array.isArray(value.reviews)||value.next_cursor!==null&&(!Number.isSafeInteger(value.next_cursor)||value.next_cursor<=after))throw Error();
      for(const row of value.reviews){
        const pkg=catalog.find(item=>item.package_id===row.context?.package_ref?.package_id&&item.work_item_id===id);
        if(row.workspace_id!==workspaceId||row.work_item_id!==id||!/^pmqr_[0-9a-f]{32}$/.test(row.review_id)||seen.has(row.review_id)
          ||row.context?.work_ref?.work_item_id!==id||!pkg||row.context.package_ref.workspace_id!==workspaceId||row.context.package_ref.document_digest!==pkg.document_digest
          ||!Number.isSafeInteger(row.sequence)||row.sequence<1||!sha.test(row.context?.native_result?.candidate_commit)||!Array.isArray(row.history))throw Error();
        seen.add(row.review_id);
        for(const item of row.history){
          if(item.kind!=='assessment')continue;
          if(!op.test(item.operation_id)||!digest.test(item.document_digest)||!Number.isSafeInteger(item.version)||item.version<1)throw Error();
          const original=ref(row,item),identity=key(original);if(identity===key(primary.ref))continue;
          if(choices.has(identity))throw Error();
          const label=`${row.context.native_result.candidate_commit.slice(0,12)} · ${t('pmq_compare_version',{review:row.sequence,version:item.version})}`;
          choices.set(identity,{ref:original,label});
        }
      }
      cursor=value.next_cursor;pages++;filtered||=!!value.visibility_filtered;
      choose(assessment);for(const [id,item]of choices)assessment.append(h('option',{value:id},item.label));
      // A refresh may not have reached the selected receipt's page yet. Keep its
      // exact identity pending; only a readable choice can expose the draft link.
      assessmentId=choices.has(selected)||cursor!==null?selected:'';assessment.value=choices.has(assessmentId)?assessmentId:'';
      status.textContent=cursor!==null&&pages>=20?t('pmq_history_bound'):filtered?t('pmq_filtered'):cursor!==null?t('pmq_compare_partial'):choices.size?t('pmq_compare_originals'):t('pmq_compare_empty');
      more.hidden=cursor===null||pages>=20;
    }catch{
      if(!valid(ticket))return;
      invalidate();assessmentId='';
      status.textContent=t('pmq_compare_unavailable');
    }finally{if(valid(ticket)){busy=false;update();}}
  }
  work.addEventListener('change',()=>{serial++;busy=false;workId=work.value;assessmentId='';choices.clear();choose(assessment);status.textContent='';more.hidden=true;update();if(workId)void load();});
  assessment.addEventListener('change',()=>{assessmentId=assessment.value;update();});
  element.addEventListener('toggle',()=>{if(element.open&&primary&&workId&&pages===0)void load();});
  link.addEventListener('click',event=>{if(!primary||busy||!choices.has(assessmentId)||!available())event.preventDefault();});
  invalidate();return {element,setSources,invalidate,withdraw,refresh};
}
