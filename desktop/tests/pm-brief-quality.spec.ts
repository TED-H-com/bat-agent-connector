import {test,expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {pmFixture,workspaceId} from './pm-fixture.ts';
import {mountConversation} from './conversation-fixture.ts';
import {qualityFixture,workId,candidate} from './pm-quality-reviews-fixture.ts';

const projectId='prj_'+'8'.repeat(20),snapshotId='pmsnap_'+'a'.repeat(32),evidenceId='pme_'+'b'.repeat(32);
function fixture(verdict='rejected'){
  const base=pmFixture(),quality=qualityFixture(),review=quality.save();
  quality.assess(review,quality.criteria.map(item=>({criterion:item.criterion,verdict,rationale:'Original reviewer: password recovery still rejects the valid owner.',reference:null})));
  review.acceptance=review.dod=verdict;review.original_outcome.acceptance=review.original_outcome.dod=verdict;
  const ref={review_id:review.review_id,operation_id:review.assessment.operation_id,version:review.assessment.version,document_digest:review.assessment.document_digest,candidate_commit:candidate};
  const binding={workspace_id:workspaceId,work_item_id:workId,project_id:projectId,...review.context.work_ref,plan_revision_id:review.context.plan_ref.revision_id,requirement_ids:['recovery']};
  const state:any={reads:[],writes:[],ops:new Map(),capture:null,snapshot:null,lost:false,withdraw:false};
  const saved=()=>({snapshot_id:snapshotId,workspace_id:workspaceId,title:'Review original quality',sequence:1,as_of:1791720000,snapshot_digest:'1'.repeat(64),view_digest:'2'.repeat(64),
    template_version:1,journal_cursor:8,coverage:{},visibility:{filtered:false},evidence:[{evidence_id:evidenceId,source_kind:'quality_assessment',source_observed_at:1791720000,document:{facts:{reference:ref,acceptance:verdict,dod:verdict,qualified:false}}}],
    brief:{goals:{status:'accepted_sources',items:[]},delivery:{status:'fixed_quality_assessments',quality:{qualified_requirements:0,total_requirements:1},items:[]},blockers:{status:'partial_source_states',items:[]},decisions:{status:'recorded_sources',items:[]},
      deviations:{version:1,status:'fixed_quality_deviations',coverage:{selected_source_coverage_complete:false,current_applicability:'not_evaluated',selections:[{work_item_id:workId,acceptance:verdict,dod:verdict,reason:null,reference:ref}]},
        items:verdict!=='rejected'?[]:quality.criteria.map((criterion,index)=>({kind:'quality_rejection',text:criterion.text,status:'rejected',source:{kind:'evidence',evidence_id:evidenceId},details:{
          baseline:{document_revision:review.context.plan_ref.revision_id},plan_ref:review.context.plan_ref,work_ref:review.context.work_ref,dod_ref:review.context.dod_ref,
          reference:ref,criterion,assessment:review.assessment.document.criteria[index],assessed_by:'reviewer',current_applicability:'not_evaluated',independent_semantic_failure_proven:false}}))}}});
  const dispatch=async(input:any)=>{
    state.reads.push(input.path);const path=new URL(input.path,'http://fixture').pathname,root=`/pm/workspaces/${workspaceId}`;
    if(path==='/capabilities'||path==='/bootstrap'){
      const result=await base.dispatch(input),caps=path==='/bootstrap'?result.data.capabilities:result.data;
      Object.assign(caps.features.pm_workspaces,{brief_snapshots:true,work_evidence:true,quality_reviews:{version:1}});
      caps.actions.push(...['pm.snapshot.create','pm.evidence.capture'].map(action=>({action,allowed:true})));return result;
    }
    if(path==='/projects')return {status:200,data:{projects:[{project_id:projectId,name:'Recovery',version:1}]}};
    if(path==='/projects/'+projectId)return {status:200,data:{project:{project_id:projectId,name:'Recovery',version:1},work_items:[{work_item_id:workId,title:'Recover original owner',version:1,children:[]}]}};
    if(path===root+'/projects')return {status:200,data:{workspace_id:workspaceId,bindings:[{project_id:projectId}],next_cursor:null}};
    if(path===root+'/work')return {status:200,data:{workspace_id:workspaceId,bindings:[binding],next_cursor:null}};
    if(path===root+'/plans/head')return {status:200,data:{head:{workspace_id:workspaceId,version:2}}};
    if(path===root+'/work/'+workId+'/capture')return {status:200,data:{workspace_id:workspaceId,capture:state.capture}};
    if(path.startsWith(root+'/snapshots')){
      if(path===root+'/snapshots')return {status:200,data:{workspace_id:workspaceId,snapshots:state.snapshot?[state.snapshot]:[],next_cursor:null}};
      return {status:200,data:{workspace_id:workspaceId,snapshot:state.withdraw?{...state.snapshot,evidence:[],brief:{...state.snapshot.brief,deviations:{status:'unassessed',items:[]}}}:state.snapshot}};
    }
    if(path===root+'/quality-reviews/'+review.review_id)return {status:200,data:{workspace_id:workspaceId,review:structuredClone(review)}};
    if(path==='/operations'&&input.method==='POST'){
      state.writes.push(structuredClone(input));let op=state.ops.get(input.idempotency_key);
      if(!op){
        if(input.body.action==='pm.evidence.capture')state.capture={workspace_id:workspaceId,work_item_id:workId,captured_at:1791720000,evidence:[],coverage:{links_examined:1,unavailable_sources:0,unsupported_sources:0}};
        if(input.body.action==='pm.snapshot.create')state.snapshot=saved();
        op={...input.body,actor:base.state.actor,idempotency_key:input.idempotency_key,operation_id:'op_'+String(state.ops.size+100).padStart(32,'0'),status:'succeeded',result:{snapshot_id:snapshotId}};state.ops.set(input.idempotency_key,op);
      }
      if(state.lost){state.lost=false;return {status:503,data:{error:{code:'LOST_REPLY',message:'Reply unavailable'}}};}
      return {status:200,data:{operation:op}};
    }
    if(path.includes('/evidence/'))return quality.dispatch(input);
    if(path==='/operations'||/\/(work-packages|sprint-artifacts\/dod|quality|evidence|deliveries|github-observations)$/.test(path))return quality.dispatch(input);
    return base.dispatch(input);
  };
  return {state,base,quality,review,ref,dispatch,saved};
}
for(const native of [false,true])for(const locale of ['en','zh-TW'])test(`original rejection to evidence and Brief ${native?'Tauri':'Web'} ${locale}`,async({browser,baseURL})=>{
  const context=await browser.newContext({baseURL,locale}),page=await context.newPage(),f=fixture(),zh=locale==='zh-TW';
  await mountConversation(page,native,f.dispatch);await page.goto(`/dashboard/#/pm/${workspaceId}`);
  const quality=page.locator('[data-pm-quality-reviews]');
  await expect.poll(async()=>({options:await quality.locator('select').first().locator('option').count(),reads:f.state.reads,qualityReads:f.quality.state.reads})).toMatchObject({options:2});
  await quality.getByLabel(zh?'審查工作項目':'Work item for review',{exact:true}).selectOption(workId);
  await quality.getByLabel(zh?'已保存審查':'Saved review',{exact:true}).selectOption(f.review.review_id);
  await quality.getByRole('button',{name:zh?'使用這份評估':'Use this assessment',exact:true}).click();
  await page.getByLabel(zh?'Connector 專案':'Connector project',{exact:true}).selectOption(projectId);
  await page.locator('[data-pm-work]>summary').click();
  const capture=page.locator('[data-pm-action="pm.evidence.capture"]');
  await capture.getByRole('button',{name:zh?'納入已選評估':'Include chosen assessment',exact:true}).click();
  f.state.lost=true;await capture.locator('button[type=submit]').click();await expect(capture).toContainText('Reply unavailable');
  const original=structuredClone(f.state.writes[0]);
  await page.reload();await page.getByLabel(zh?'Connector 專案':'Connector project',{exact:true}).selectOption(projectId);
  await page.locator('[data-pm-work]>summary').click();
  await capture.getByRole('button',{name:zh?'查回原請求':'Check original request',exact:true}).click();
  expect(f.state.writes[1]).toEqual(original);expect(original.body.params.quality_ref).toEqual(f.ref);
  await page.locator('[data-pm-action="pm.snapshot.create"] button[type=submit]').click();
  const deviations=page.locator('[data-pm-brief-section="deviations"]');
  await expect(deviations).toContainText(zh?'審查者否決驗收條件':'Reviewer rejected acceptance criterion');
  await deviations.locator('[data-pm-brief-fact="quality_rejection"]>summary').first().click();
  await expect(deviations).toContainText('Original reviewer: password recovery');await expect(deviations).toContainText(f.ref.document_digest);
  await expect(deviations).toContainText(zh?'目前適用性':'current applicability');
  f.quality.assess(f.review);await page.getByRole('button',{name:zh?'重新整理':'Refresh',exact:true}).click();
  await expect(deviations).toContainText(zh?'審查者否決驗收條件':'Reviewer rejected acceptance criterion');
  f.state.withdraw=true;await page.getByRole('button',{name:zh?'重新整理':'Refresh',exact:true}).click();
  await expect(deviations.locator('[data-pm-brief-fact]')).toHaveCount(0);await expect(deviations).not.toContainText(f.ref.document_digest);
  await context.close();
});
for(const verdict of ['accepted','unassessed'])for(const locale of ['en','zh-TW'])test(`selected ${verdict} stays explicit in deviation coverage ${locale}`,async({browser,baseURL})=>{
  const context=await browser.newContext({baseURL,locale}),page=await context.newPage(),f=fixture(verdict);f.state.snapshot=f.saved();
  await mountConversation(page,false,f.dispatch);await page.goto(`/dashboard/#/pm/${workspaceId}`);
  const view=page.locator('[data-pm-brief-section="deviations"]');await view.locator('[data-pm-deviation-coverage]>summary').click();
  await expect(view).toContainText(locale==='en'?(verdict==='accepted'?'Accepted':'Unassessed'):(verdict==='accepted'?'符合':'尚未評估'));
  await expect(view).toContainText(locale==='en'?'does not establish that the project has no deviations':'不代表專案沒有偏差');
  await expect(view.locator('[data-pm-brief-fact]')).toHaveCount(0);expect(f.state.writes).toHaveLength(0);await context.close();
});
for(const locale of ['en','zh-TW'])for(const width of [390,768,1440])test(`quality deviation layout ${locale} ${width}`,async({browser,baseURL},info)=>{
  const context=await browser.newContext({baseURL,locale,viewport:{width,height:1000}}),page=await context.newPage(),f=fixture();f.state.snapshot=f.saved();
  await mountConversation(page,false,f.dispatch);await page.goto(`/dashboard/#/pm/${workspaceId}`);
  const brief=page.locator('[data-pm-brief]'),deviations=brief.locator('[data-pm-brief-section="deviations"]');
  await expect(deviations).toContainText(locale==='en'?'Reviewer rejected':'審查者否決');
  await deviations.locator('[data-pm-deviation-coverage]>summary').click();
  await expect(deviations).toContainText(locale==='en'?'coverage is incomplete':'涵蓋不完整');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  const out=process.env.BATC_PM_BRIEF_QUALITY_SCREENSHOTS||info.outputPath('screenshots');await mkdir(out,{recursive:true});await page.evaluate(()=>scrollTo(0,0));await page.screenshot({fullPage:true,path:`${out}/brief-quality-${locale}-${width}.png`});
  await context.close();
});
