import {openThreadTab} from './pm-navigation.ts';
import {test, expect} from '@playwright/test';
import {mkdirSync} from 'node:fs';
import {mountConversation} from './conversation-fixture.ts';
import {pmFixture, pmRoute, workspaceId, threadId, inputA, inputB} from './pm-fixture.ts';

const first='pmrev_'+'a'.repeat(32), second='pmrev_'+'b'.repeat(32), binding='pmwb_'+'c'.repeat(32);
const shots=process.env.PM_DIFF_SCREENSHOTS;
test('before comparison fits the existing shared plan layout',async({page})=>{
  const f=pmFixture();f.state.plans=true;
  await mountConversation(page,false,f.dispatch);await page.goto(pmRoute);await openThreadTab(page,'plan');
  await expect(page.locator('[data-pm-plans]')).toBeVisible();
  if(shots){mkdirSync(shots,{recursive:true});for(const width of [1440,390]){
    await page.setViewportSize({width,height:900});await page.screenshot({path:`${shots}/before-${width}.png`,fullPage:true});}}
});

function fixture(){
  const base=pmFixture();base.state.plans=true;
  const chosen={binding_id:binding,workspace_id:workspaceId,work_item_id:'wi_'+'d'.repeat(32),plan_revision_id:first,requirement_ids:['login'],source_work_version:3};
  const source={input_id:inputA,value:'A password reset link expires after use.',status:'agent_assumption',source_ref:{kind:'statement',ref:'A proposed condition'},confirmed_by:null};
  const requirement={id:'login',text:'Members can sign in. <script>never execute</script>',source_input_id:inputA,source_status:'user_confirmed',priority:'must',depends_on:[],acceptance:[{text:source.value,source_status:'agent_assumption',source_input_id:inputA}]};
  const section={coverage:'unresolved',statements:[],source_input_ids:[]};
  base.state.revisions=[first,second].map((revision_id,index)=>({revision_id,workspace_id:workspaceId,thread_id:threadId,sequence:index+1,
    document:{title:'Membership',markdown:'A recorded proposal',sections:{},requirements:[]},adoption:null}));
  base.state.planHead={workspace_id:workspaceId,version:2,latest_revision_id:second,accepted_revision_id:null};
  const state={reads:[] as any[],planReads:0,failure:false,mismatch:false,defer:null as null|Promise<void>,cursor:45};
  const result=(params:URLSearchParams)=>({version:1,workspace_id:workspaceId,comparison:{
    from_revision:{revision_id:state.mismatch?second:params.get('from_revision_id'),document_digest:'1'.repeat(64)},
    to_revision:{revision_id:params.get('to_revision_id'),document_digest:'2'.repeat(64)},
    changes:{requirements:{added:[],removed:[],changed:[{id:'login',before:requirement,after:{...requirement,acceptance:[]}}],order_changed:false},
      sources:{added:[],removed:[],changed:[],unchanged:[source]},
      domains:Object.fromEntries(['governance','scope','schedule','finance','stakeholders','resources','risk','quality'].map(key=>[key,{changed:false,before:section,after:section}])),
      title:{changed:false,before:'Membership',after:'Membership'},markdown:{changed:true,replacement:{removed:'Login is required.',added:'Login is optional. <img src=x onerror=alert(1)>'}},
      unclassified_sources:{before:[inputB],after:[inputB],changed:false}},
    scope:{semantic_impact:'unassessed',structured_digest_changed:true},impact_candidates:params.has('binding_ids')?[{binding:chosen,assessment:'review_candidate',reasons:['associated_requirement_changed','plan_context_changed_impact_unclassified']}]:[],
    context:{journal_cursor:params.has('journal_cursor')?Number(params.get('journal_cursor')):state.cursor,binding_ids:params.get('binding_ids')?.split(',')||[]},
    comparison_digest:'3'.repeat(64),context_digest:'4'.repeat(64),coverage:{},execution_authorized:false}});
  const dispatch=async(input:any)=>{
    const url=new URL(input.path,'http://fixture');
    if(url.pathname===`/pm/workspaces/${workspaceId}/plans`)state.planReads++;
    if(url.pathname.endsWith('/plan-comparison')){
      state.reads.push(structuredClone(input));if(state.defer)await state.defer;
      return state.failure?{status:403,data:{error:{code:'FORBIDDEN',message:'Comparison no longer visible'}}}:{status:200,data:result(url.searchParams)};
    }
    if(url.pathname===`/pm/workspaces/${workspaceId}/work`)return {status:200,data:{workspace_id:workspaceId,bindings:[chosen],next_cursor:null}};
    return base.dispatch(input);
  };
  return {...base,dispatch,comparison:state};
}
const box=(page:any)=>page.locator('[data-pm-plan-diff]');
async function mount(page:any,native:boolean,f:any){
  await mountConversation(page,native,f.dispatch);
  await page.goto(pmRoute);await openThreadTab(page,'plan');
  await expect(page.locator('[data-pm-plans]')).toBeVisible();
  await expect(box(page)).toHaveCount(1);
  await expect(box(page)).toBeHidden();
  await page.locator('[data-pm-plan-comparison] > summary').click();
  await expect(box(page).locator('select').first().locator('option')).toHaveCount(3);
}
async function choose(page:any){await box(page).getByLabel('From version',{exact:true}).selectOption(first);await box(page).getByLabel('To version',{exact:true}).selectOption(second);}

for(const native of [false,true])test.describe(native?'native comparison bridge':'web comparison bridge',()=>{
  test('explicit versions and bindings show original acceptance, unknowns and fixed replay',async({page})=>{
    const f=fixture();await mount(page,native,f);
    await expect(box(page).getByRole('button',{name:'Compare versions',exact:true})).toBeDisabled();
    expect(f.comparison.reads).toHaveLength(0);await choose(page);
    await box(page).locator('[data-pm-diff-bindings] > summary').click();
    await box(page).locator('[data-pm-diff-binding]').check();
    await box(page).getByRole('button',{name:'Refresh work associations',exact:true}).click();
    await expect(box(page).locator('[data-pm-diff-binding]')).toBeChecked();
    await box(page).getByRole('button',{name:'Compare versions',exact:true}).click();
    const result=box(page).locator('[data-pm-diff-result]');await expect(result).toContainText('Changed · login');
    await expect(result).toContainText('Structured scope data differs. Its effect still needs review.');
    await result.getByText('Changed · login',{exact:true}).click();
    await expect(result).toContainText('A password reset link expires after use.');
    await expect(result).toContainText('No acceptance criteria recorded');
    await result.getByText('Source identities and recorded statuses',{exact:true}).click();
    await result.getByText('No recorded change · '+inputA,{exact:true}).click();
    await expect(result).toContainText('A proposed condition');await expect(result).toContainText('No confirmer recorded');
    await result.getByText('Title and Markdown',{exact:true}).click();await expect(result).toContainText('effect on scope has not been classified');
    await result.locator('summary').filter({hasText:'Review candidate'}).click();await expect(result).toContainText('Title and work state were not saved');
    await expect(result.getByRole('link',{name:'Open existing work'})).toHaveAttribute('href',/^#\/work\/wi_/);
    await expect(result.locator('script,img')).toHaveCount(0);
    expect(f.state.writes).toHaveLength(0);
    const firstRequest=new URL(f.comparison.reads[0].path,'http://fixture');expect(firstRequest.searchParams.get('binding_ids')).toBe(binding);
    expect(firstRequest.searchParams.has('journal_cursor')).toBe(false);
    f.comparison.cursor=99;await box(page).getByRole('button',{name:'Recheck this comparison',exact:true}).click();
    await expect(box(page).getByRole('status',{name:'Reading the fixed comparison…',exact:true})).toHaveCount(0);
    expect(new URL(f.comparison.reads[1].path,'http://fixture').searchParams.get('journal_cursor')).toBe('45');
    const planReads=f.comparison.planReads;
    await page.getByRole('button',{name:'Refresh',exact:true}).click();
    await expect.poll(()=>f.comparison.planReads).toBeGreaterThan(planReads);
    await expect(box(page).getByLabel('From version',{exact:true})).toHaveValue(first);
    await expect(box(page).getByLabel('To version',{exact:true})).toHaveValue(second);
    await expect(box(page).locator('[data-pm-diff-binding]')).toBeChecked();
    await expect(box(page).getByRole('button',{name:'Recheck this comparison',exact:true})).toBeEnabled();
    await box(page).getByLabel('To version',{exact:true}).selectOption(first);
    await expect(result).toBeEmpty();await box(page).getByRole('button',{name:'Compare versions',exact:true}).click();
    await expect(box(page).getByRole('button',{name:'Recheck this comparison',exact:true})).toBeEnabled();
    expect(new URL(f.comparison.reads[2].path,'http://fixture').searchParams.has('journal_cursor')).toBe(false);
  });
  test('mismatched response and lost access never leave stale differences visible',async({page})=>{
    const f=fixture();await mount(page,native,f);await choose(page);f.comparison.mismatch=true;
    await box(page).getByRole('button',{name:'Compare versions',exact:true}).click();
    await expect(box(page).getByText('The response does not match the selected comparison.',{exact:true})).toBeVisible();await expect(box(page).locator('[data-pm-diff-result]')).toBeEmpty();
    f.comparison.mismatch=false;await box(page).getByRole('button',{name:'Compare versions',exact:true}).click();
    await expect(box(page)).toContainText('Changed · login');f.comparison.failure=true;
    await box(page).getByRole('button',{name:'Recheck this comparison',exact:true}).click();
    await expect(box(page).getByText('Your token lacks this scope.',{exact:true})).toBeVisible();await expect(box(page).locator('[data-pm-diff-result]')).toBeEmpty();
    expect(f.state.writes).toHaveLength(0);
  });
  test('late read from a changed selection is discarded',async({page})=>{
    const f=fixture();let release!:()=>void;f.comparison.defer=new Promise<void>(resolve=>release=resolve);
    await mount(page,native,f);await choose(page);await box(page).getByRole('button',{name:'Compare versions',exact:true}).click();
    await expect(box(page).getByRole('status',{name:'Reading the fixed comparison…',exact:true})).toBeVisible();await box(page).getByLabel('To version',{exact:true}).selectOption(first);
    release();await page.waitForTimeout(100);await expect(box(page).locator('[data-pm-diff-result]')).toBeEmpty();
    await expect(box(page).getByRole('button',{name:'Compare versions',exact:true})).toBeEnabled();
  });
});

test('plan comparison is legible in both locales at all shared widths',async({browser,baseURL})=>{
  for(const locale of ['en-US','zh-TW']){
    const context=await browser.newContext({baseURL,locale});const page=await context.newPage();const f=fixture();await mount(page,false,f);
    const selects=box(page).locator('select');await selects.nth(0).selectOption(first);await selects.nth(1).selectOption(second);
    await box(page).getByRole('button',{name:locale==='en-US'?'Compare versions':'比較版本',exact:true}).click();
    await expect(box(page).locator('[data-pm-diff-result]')).not.toBeEmpty();
    await box(page).locator('[data-pm-diff-result] details').first().locator('summary').first().click();
    for(const width of [1440,768,390]){
      await page.setViewportSize({width,height:900});await page.evaluate(()=>window.scrollTo(0,0));
      expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
      if(shots)await page.screenshot({path:`${shots}/comparison-${locale}-${width}.png`,fullPage:true});
    }
    await context.close();
  }
});
