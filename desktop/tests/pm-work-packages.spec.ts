import {openPmSection} from './pm-navigation.ts';
import {test,expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {pmFixture,workspaceId} from './pm-fixture.ts';
import {mountConversation} from './conversation-fixture.ts';
const packageId='pmwp_'+'6'.repeat(32),workId='wi_'+'7'.repeat(20),hash='a'.repeat(64),route=`/dashboard/#/pm/${workspaceId}`;
function fixture(){
  const base=pmFixture();
  const document={package_id:packageId,workspace_id:workspaceId,work_item_id:workId,title:'Restore member access',execution_authorized:false,quality_assessment:'unassessed',
    context:{plan_ref:{revision_id:'pmplan_'+ '8'.repeat(32),scope_revision:'pmscope_'+'9'.repeat(32)},
      work_ref:{binding_id:'pmwb_'+'3'.repeat(32),work_version:7},dod_ref:{revision_id:'pmsa_'+'4'.repeat(32),document_digest:'5'.repeat(64)}},
    requirements:[{id:'member.recovery',text:'Members can recover their account.',source_status:'user_confirmed',
      acceptance:[{text:'The reset link expires after use.',source_input_id:'pmi_'+'b'.repeat(32),source_status:'user_confirmed'}]}],
    design:{status:'described',text:'Keep the existing authentication module; store one-time reset tokens.',source_ids:['pmssrc_'+'0'.repeat(64)]},rationale:'Preserve the adopted account-recovery scope.',
    tasks:[{key:'reset_tokens',description:'Implement single-use recovery tokens.',requirement_ids:['member.recovery'],depends_on:[]}],
    verification:[{key:'expired_token',requirement_id:'member.recovery',acceptance_input_ids:['pmi_'+'b'.repeat(32)],method:'Exercise token reuse in integration tests.',expected_evidence:'A test report at the exact code commit.'}],
    delivery:{repository:'example/library',base_ref:'refs/heads/main',goal:'pull_request',...Object.fromEntries(['migration','rollout','rollback','observation'].map(key=>[key,{status:'described',text:'Documented '+key+' consideration.'}]))},
    dod:{criteria:[{text:'No account enumeration is exposed.'}]},unresolved:[],code_source:{repository:'example/library',source_sha:'c'.repeat(40)},
    evidence_refs:[{evidence_id:'pme_'+'2'.repeat(32),fact_digest:'1'.repeat(64)}],
    sources:{documents:[{path:'openspec/specs/recovery/spec.md',source_id:'pmssrc_'+'0'.repeat(64),content:'# Account recovery\n\n<script>window.untrustedRan=true</script>',commit_sha:'d'.repeat(40),sha256:hash}]}};
  const row={sequence:1,created_at:1791720000,actor:'reader',package_id:packageId,workspace_id:workspaceId,work_item_id:workId,previous_package_id:null,document_digest:hash,operation_id:'op_'+'e'.repeat(32),document};
  const state={rows:[row] as any[],calls:[] as any[],hidden:false,denied:false,mismatch:false,gate:null as Promise<void>|null,filtered:false,ready:true};
  const dispatch=async(input:any)=>{
    const url=new URL(input.path,'http://fixture'),p=url.pathname;
    if(p==='/bootstrap'||p==='/capabilities'){
      const result=await base.dispatch(input),caps=p==='/bootstrap'?result.data.capabilities:result.data;caps.features.pm_workspaces.work_package_prepare=true;return result;
    }
    if(p==='/events'){const result=await base.dispatch(input);for(const event of result.data.events)event.resource_type='pm_work_package';return result;}
    if(p.startsWith(`/pm/workspaces/${workspaceId}/work-packages`)){
      state.calls.push(structuredClone(input));
      if(state.denied)return{status:403,data:{error:{code:'FORBIDDEN',message:'Access withdrawn'}}};
      if(p.endsWith('/work-packages')){
        const after=Number(url.searchParams.get('after'));
        return{status:200,data:{version:1,workspace_id:workspaceId,packages:state.hidden?[]:state.filtered&&after===0?[]:structuredClone(state.rows),
          next_cursor:state.filtered&&after===0?1:null,coverage:{visibility_filtered:state.hidden||state.filtered}}};
      }
      if(state.gate)await state.gate;
      if(state.hidden)return{status:404,data:{error:{code:'PM_PACKAGE_SOURCE_UNAVAILABLE',message:'Original source unavailable'}}};
      const selected=state.rows.find(row=>p.split('/').includes(row.package_id));
      if(p.endsWith('/readiness'))return{status:200,data:{version:1,workspace_id:workspaceId,package_id:selected.package_id,document_digest:state.mismatch?'f'.repeat(64):hash,
        status:state.ready?'ready':'blocked',ready_for:'engineering_handoff_review',execution_available:false,execution_authorized:false,dispatch_readiness:'not_implemented',
        checks:[{check:'scope',status:state.ready?'passed':'blocked'},...(state.ready?[]:[{check:'dependency:member.recovery',status:'blocked',assessment:'unknown',
          requirement_ids:['member.email_delivery'],detail:'Required outcomes have not been evaluated; work claims and checkboxes do not prove readiness.'}])]}};
      return{status:200,data:{version:1,workspace_id:workspaceId,package:structuredClone(selected)}};
    }
    return base.dispatch(input);
  };
  return{state,base,dispatch};
}
const section=(page:any)=>page.locator('[data-pm-work-packages]'),detail=(page:any)=>page.locator('[data-pm-wp-detail]');
async function review(page:any){await section(page).getByRole('button',{name:'Restore member access · Preparation 1',exact:true}).click();await expect(detail(page)).toContainText('Ready for engineering review');}
for(const native of [false,true])test.describe(native?'native engineering packages':'web engineering packages',()=>{
  test('fixed requirements and distinct code/spec sources are readable without execution',async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page,'requirements','ui_packages');await review(page);
    await expect(detail(page)).toContainText('Members can recover their account.');
    await expect(detail(page)).toContainText('Execution, quality acceptance and delivery have not been authorized or verified.');
    await detail(page).getByText('Fixed sources and versions',{exact:true}).click();await expect(detail(page)).toContainText('c'.repeat(40));
    await expect(detail(page)).toContainText(packageId);await expect(detail(page)).toContainText('pmwb_'+'3'.repeat(32));
    await expect(detail(page)).toContainText('pmsa_'+'4'.repeat(32));await expect(detail(page)).toContainText('5'.repeat(64));
    await expect(detail(page)).toContainText('pme_'+'2'.repeat(32));await expect(detail(page)).toContainText('pmssrc_'+'0'.repeat(64));
    await detail(page).getByText('Original source documents',{exact:true}).click();await detail(page).getByText('openspec/specs/recovery/spec.md',{exact:true}).click();
    await expect(detail(page)).toContainText('d'.repeat(40));await expect(detail(page)).toContainText('<script>');
    expect(await page.evaluate(()=>(window as any).untrustedRan)).toBeUndefined();
    expect(f.state.calls.every(call=>call.method==='GET')).toBe(true);expect(f.base.state.writes).toHaveLength(0);await expect(page.getByRole('tab')).toHaveCount(0);
  });
  test('filtered empty pages retain the forward cursor',async({page})=>{
    const f=fixture();f.state.filtered=true;await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page,'requirements','ui_packages');
    await expect(section(page)).toContainText('No readable work packages on this page.');
    await section(page).getByRole('button',{name:'Load more packages'}).click();await review(page);
    expect(f.state.calls.some(call=>call.path.includes('after=1&'))).toBe(true);
  });
  test('source withdrawal clears details and titles, then readable packages can be selected again',async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page,'requirements','ui_packages');await review(page);
    f.state.hidden=true;f.base.state.cursor++;await expect(detail(page)).not.toContainText('Members can recover',{timeout:10000});
    await expect(section(page)).not.toContainText('Restore member access');
    f.state.hidden=false;await section(page).getByRole('button',{name:'Refresh work packages'}).click();await review(page);
  });
  test('mismatched readiness cannot attach to the displayed fixed package',async({page})=>{
    const f=fixture();f.state.mismatch=true;await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page,'requirements','ui_packages');
    await section(page).getByRole('button',{name:'Restore member access · Preparation 1',exact:true}).click();
    await expect(section(page)).toContainText('The response does not match');await expect(detail(page)).not.toContainText('Members can recover');
  });
  test('late selected response cannot repopulate a page after leaving it',async({page})=>{
    const f=fixture();let release!:()=>void;f.state.gate=new Promise<void>(resolve=>release=resolve);
    await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page,'requirements','ui_packages');await section(page).getByRole('button',{name:'Restore member access · Preparation 1',exact:true}).click();
    await page.goto('/dashboard/#/settings');release();await expect(section(page)).toHaveCount(0);
    await expect(page.locator('main')).not.toContainText('Members can recover');expect(f.base.state.writes).toHaveLength(0);
  });
  test('current scope changes update review checks while fixed requirements remain intact',async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page,'requirements','ui_packages');await review(page);
    f.state.ready=false;f.base.state.cursor++;await expect(detail(page)).toContainText('Engineering review needs attention',{timeout:10000});
    await expect(detail(page)).toContainText('Members can recover their account.');
    await expect(detail(page)).toContainText('member.email_delivery');await expect(detail(page)).toContainText('A changed scope needs explicit replanning');
  });
  test('same-title revisions have distinct preparation identities',async({page})=>{
    const f=fixture(),second=structuredClone(f.state.rows[0]);second.sequence=2;second.package_id='pmwp_'+'f'.repeat(32);
    second.document.package_id=second.package_id;second.document.design.text='A separate design for the next preparation.';f.state.rows.push(second);
    await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page,'requirements','ui_packages');
    await section(page).getByRole('button',{name:'Restore member access · Preparation 2',exact:true}).click();
    await expect(detail(page)).toContainText('A separate design for the next preparation.');
  });
});
for(const locale of ['en-US','zh-TW'])for(const width of [390,768,1440])test(`engineering layout ${locale} ${width}`,async({browser,baseURL})=>{
  const context=await browser.newContext({baseURL,locale,viewport:{width,height:900}}),page=await context.newPage(),f=fixture();
  await mountConversation(page,false,f.dispatch);await page.goto(route);await openPmSection(page,'requirements','ui_packages');await section(page).getByRole('button',{name:/Restore member access/}).click();
  await expect(detail(page).locator('[data-pm-wp-readiness]')).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  const out=process.env.PM_WORK_PACKAGE_SCREENSHOTS;if(out){await mkdir(out,{recursive:true});await page.screenshot({path:`${out}/${locale}-${width}.png`,fullPage:true});}
  await context.close();
});

for(const native of [false,true])test.describe(native?'native saved engineering delivery':'web saved engineering delivery',()=>{
  test('saved delivery facts stay separate, paginate filtered pages and clear after source withdrawal',async({page})=>{
    const f=fixture();let withdrawn=false;const deliveryCalls:string[]=[];
    const row={operation_id:'op_'+'a'.repeat(32),action:'delivery.merge_and_deploy',status:'succeeded',target:{repository:'example/library',pull_number:12},
      requirement_acceptance:'not_evaluated_by_delivery',merge:{merged:true,verified:false,merged_sha:'b'.repeat(40)},
      deployment:{environment:'staging',state:'verified',verified:true,is_current:false,run_id:55,source_sha:'b'.repeat(40)}};
    const dispatch=async(input:any)=>{
      const url=new URL(input.path,'http://fixture'),p=url.pathname;
      if(p.endsWith('/deliveries')){
        deliveryCalls.push(input.path);
        if(withdrawn)return{status:404,data:{error:{code:'NOT_FOUND',message:'Source withdrawn'}}};
        const first=!url.searchParams.get('after')||url.searchParams.get('after')==='0';
        return{status:200,data:{workspace_id:workspaceId,work_item_id:workId,contract:'pm-engineering-delivery/v1',
          original_packages:[{workspace_id:workspaceId,package_id:packageId,document_digest:hash,operation_id:'op_'+'f'.repeat(32)}],
          deliveries:first?[]:[row],next_after:first?12:null,source_visibility_filtered:first,requirement_acceptance:'not_evaluated_by_delivery'}};
      }
      const response=await f.dispatch(input);
      if(p==='/bootstrap'||p==='/capabilities')(p==='/bootstrap'?response.data.capabilities:response.data).features.pm_workspaces.engineering_delivery={contract:'pm-engineering-delivery/v1',read_only:true};
      return response;
    };
    await mountConversation(page,native,dispatch);await page.goto(route);await openPmSection(page,'requirements','ui_packages');await review(page);
    await openPmSection(page,'requirements','ui_deliveries');
    const box=page.locator('[data-pm-work-deliveries]');await expect(box).toBeVisible();await box.locator('summary').click();
    await expect(box).toContainText('This page is incomplete.');await box.getByRole('button',{name:'More delivery records',exact:true}).click();
    await expect(box).toContainText('Merge observed: Yes');await expect(box).toContainText('Merge verification: No');
    await expect(box).toContainText('Runtime verification: Yes');await expect(box).toContainText('Current deployment: No');
    await expect(box).toContainText('Requirements and Definition of Done still need their own assessment.');
    expect(deliveryCalls.some(path=>path.includes('after=12'))).toBe(true);
    withdrawn=true;await box.getByRole('button',{name:'Refresh delivery records',exact:true}).click();
    await expect(box).toContainText('Delivery evidence is unavailable');await expect(box).not.toContainText('staging');
    await expect(box.locator('a')).toHaveCount(0);
  });
});
