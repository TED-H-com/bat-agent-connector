import {test,expect} from '@playwright/test';
import {buildResultEnvelope} from './work-result-fixture.ts';
import {parseDiscussRefs} from '../src/pm-discuss-link.js';
import {qualityFixture,mountQuality,panel,CREATE,workspaceId,workId,packageId,childId,candidate} from './pm-quality-reviews-fixture.ts';

const otherWork='wi_'+'8'.repeat(20),otherPackage='pmwp_'+'8'.repeat(32);
const ref=(row:any,item=row.assessment)=>({kind:'quality_assessment',id:item.operation_id,review_id:row.review_id,document_digest:item.document_digest});
function fixture(){
  const f=qualityFixture(),first=f.save(true),second=structuredClone(first);
  second.review_id='pmqr_'+'8'.repeat(32);second.sequence=2;second.work_item_id=otherWork;second.context.work_ref.work_item_id=otherWork;
  second.context.package_ref.package_id=otherPackage;second.context.native_result.candidate_commit='b'.repeat(40);
  second.assessment.operation_id='op_'+'8'.repeat(32);second.history=[structuredClone(second.assessment)];
  const pkg=structuredClone(f.state.packages[0]);pkg.package_id=otherPackage;pkg.work_item_id=otherWork;pkg.document.title='Keep retry drafts after reconnect';pkg.document.work_item_id=otherWork;
  f.state.packages.push(pkg);f.state.reviews.push(second);
  f.state.resultEnvelope=buildResultEnvelope({workspace_id:workspaceId,
    identity:{workspace_id:workspaceId,work_item_id:workId,attempt_id:'att_'+'1'.repeat(32),package_id:packageId,execution_operation_id:childId},
    fixed_commit:{commit_sha:candidate,source_kind:'turn_end_observation'},
    next_allowed_actions:[{kind:'action',action:CREATE,target:{workspace_id:workspaceId,work_item_id:workId}}]});
  return {f,first,second};
}
async function open(page:any,f:any,locale='en'){
  await mountQuality(page,f,locale,true);
  await page.getByRole('button',{name:locale==='en'?'Review this result':'審閱這份成果',exact:true}).click();
  const box=panel(page).locator('[data-quality-compare]');await expect(box).toBeVisible();
  await box.locator('summary').click();return box;
}
const selectedLink=(box:any)=>box.getByRole('link');
async function choose(box:any,id=otherWork){
  await box.locator('select').nth(0).selectOption(id);
  await expect(box.locator('select').nth(1)).toBeEnabled();
  await box.locator('select').nth(1).selectOption({index:1});
}
for(const [locale,width]of [['en',1440],['en',768],['zh-TW',390]] as const)test(`two original assessments ${locale} ${width}`,async({page},info)=>{
  const {f,first,second}=fixture();await page.setViewportSize({width,height:width===390?844:900});
  const box=await open(page,f,locale);
  expect(f.state.reads.filter((path:string)=>path.includes(`/work/${otherWork}/quality`))).toHaveLength(0);
  await expect(selectedLink(box)).toBeHidden();await choose(box);
  const href=(await selectedLink(box).getAttribute('href'))!;
  expect(parseDiscussRefs(href.split('?')[1]).refs).toEqual([ref(first),{kind:'work_item',id:workId},ref(second),{kind:'work_item',id:otherWork}]);
  await expect(box).toContainText('bbbbbbbbbbbb');await expect(box).not.toContainText(otherWork);await expect(box).not.toContainText(otherPackage);
  expect(f.state.writes).toHaveLength(0);
  await box.scrollIntoViewIfNeeded();expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:info.outputPath(`comparison-${locale}-${width}.png`),fullPage:true});
  // An explicit refresh rechecks the saved identity instead of replacing it with latest.
  await page.evaluate(()=>(window as any).qualityRefresh());
  await expect(selectedLink(box)).toHaveAttribute('href',href);
  await selectedLink(box).click();await expect.poll(()=>page.evaluate(()=>location.hash)).toBe(href);
  expect(f.state.writes).toHaveLength(0);
});
test('an older assessment keeps its original receipt and same Work is deduplicated',async({page})=>{
  const {f,first}=fixture(),old=structuredClone(first.assessment);f.assess(first);
  const box=await open(page,f);await choose(box,workId);
  expect(parseDiscussRefs((await selectedLink(box).getAttribute('href'))!.split('?')[1]).refs).toEqual([ref(first),{kind:'work_item',id:workId},ref(first,old)]);
  expect(f.state.writes).toHaveLength(0);
});
for(const defect of ['workspace','work','package digest','candidate','history digest','cursor','denied'])test(`rejects ${defect} without exposing a mixed-source link`,async({page})=>{
  const {f,second}=fixture(),dispatch=f.dispatch;
  f.dispatch=async input=>{
    const reply=await dispatch(input);
    if(input.path.includes(`/work/${otherWork}/quality`)){
      if(defect==='denied')return {status:404,data:{error:{code:'NOT_FOUND',message:'Unavailable'}}};
      const data=reply.data as any,row=data.reviews[0];
      if(defect==='workspace')data.workspace_id='ws_'+'9'.repeat(32);
      if(defect==='work')row.work_item_id=workId;
      if(defect==='package digest')row.context.package_ref.document_digest='9'.repeat(64);
      if(defect==='candidate')row.context.native_result.candidate_commit='latest';
      if(defect==='history digest')row.history[0].document_digest='latest';
      if(defect==='cursor')data.next_cursor=0;
    }return reply;
  };
  const box=await open(page,f);await box.locator('select').first().selectOption(otherWork);
  await expect(box.getByRole('status')).toContainText('could not be checked');
  await expect(selectedLink(box)).toHaveCount(0);await expect(box).not.toContainText('Keep retry drafts');
  expect(f.state.writes).toHaveLength(0);expect(second.assessment).toBeTruthy();
});
test('withdrawal retires a held second-source read',async({page})=>{
  const {f}=fixture(),dispatch=f.dispatch;let release!:()=>void,entered=false;
  const gate=new Promise<void>(resolve=>{release=resolve;});
  f.dispatch=async input=>{const reply=await dispatch(input);if(input.path.includes(`/work/${otherWork}/quality`)){entered=true;await gate;}return reply;};
  const box=await open(page,f);await box.locator('select').first().selectOption(otherWork);await expect.poll(()=>entered).toBe(true);
  await page.evaluate(()=>(window as any).qualityWithdraw());release();
  await expect(page.locator('[data-quality-compare]')).toHaveCount(0);expect(f.state.writes).toHaveLength(0);
});
test('visible pagination loads the next original receipt without auto-selecting it',async({page})=>{
  const {f,second}=fixture(),dispatch=f.dispatch;let loads=0;
  f.dispatch=async input=>{const reply=await dispatch(input);if(input.path.includes(`/work/${otherWork}/quality`)){
    loads++;const data=reply.data as any;if(!input.path.includes('after=2')){data.reviews=[];data.next_cursor=2;data.visibility_filtered=true;}
  }return reply;};
  const box=await open(page,f);await box.locator('select').first().selectOption(otherWork);
  await expect(box.getByRole('button',{name:'Load more assessments'})).toBeVisible();expect(loads).toBe(1);
  await box.getByRole('button',{name:'Load more assessments'}).click();await expect(box.locator('select').nth(1)).toBeEnabled();
  await expect(selectedLink(box)).toBeHidden();await box.locator('select').nth(1).selectOption({index:1});
  expect(parseDiscussRefs((await selectedLink(box).getAttribute('href'))!.split('?')[1]).refs).toContainEqual(ref(second));expect(loads).toBe(2);
});

test('refresh preserves a later-page selection until that exact receipt is read again',async({page})=>{
  const {f,second}=fixture(),dispatch=f.dispatch;
  f.dispatch=async input=>{const reply=await dispatch(input);if(input.path.includes(`/work/${otherWork}/quality`)&&!input.path.includes('after=2')){
    const data=reply.data as any;data.reviews=[];data.next_cursor=2;
  }return reply;};
  const box=await open(page,f);await box.locator('select').first().selectOption(otherWork);
  await expect(box.getByRole('status')).toContainText('next page');
  await box.getByRole('button',{name:'Load more assessments'}).click();
  await box.locator('select').nth(1).selectOption({index:1});
  const href=(await selectedLink(box).getAttribute('href'))!;
  await page.evaluate(()=>(window as any).qualityRefresh());
  await expect(box.getByRole('button',{name:'Load more assessments'})).toBeVisible();
  await expect(selectedLink(box)).toBeHidden();
  await box.getByRole('button',{name:'Load more assessments'}).click();
  await expect(selectedLink(box)).toHaveAttribute('href',href);
  expect(parseDiscussRefs(href.split('?')[1]).refs).toContainEqual(ref(second));
  expect(f.state.writes).toHaveLength(0);
});

test('a newer source refresh retires an older held reply',async({page})=>{
  const {f}=fixture(),dispatch=f.dispatch;let release!:()=>void,entered=false;
  const gate=new Promise<void>(resolve=>{release=resolve;});
  f.dispatch=async input=>{const reply=await dispatch(input);if(input.path.includes(`/work/${otherWork}/quality`)){
    if(!entered){entered=true;await gate;}else (reply.data as any).reviews=[];
  }return reply;};
  const box=await open(page,f);await box.locator('select').first().selectOption(otherWork);await expect.poll(()=>entered).toBe(true);
  await page.evaluate(()=>(window as any).qualityRefresh());
  await expect(box.getByRole('status')).toContainText('No other saved assessment');release();
  await expect(box.locator('select').nth(1)).toBeDisabled();await expect(selectedLink(box)).toBeHidden();
  expect(f.state.writes).toHaveLength(0);
});

test('refresh never replaces a removed original choice with a later assessment',async({page})=>{
  const {f,second}=fixture(),box=await open(page,f);await choose(box);
  f.assess(second);second.history=[structuredClone(second.assessment)];
  await page.evaluate(()=>(window as any).qualityRefresh());
  await expect(box.locator('select').nth(1)).toBeEnabled();
  await expect(box.locator('select').nth(1)).toHaveValue('');await expect(selectedLink(box)).toBeHidden();
  expect(f.state.writes).toHaveLength(0);
});

test('a repeated review on a later page removes the previously readable link',async({page})=>{
  const {f}=fixture(),dispatch=f.dispatch;
  f.dispatch=async input=>{const reply=await dispatch(input);if(input.path.includes(`/work/${otherWork}/quality`)&&!input.path.includes('after=2'))(reply.data as any).next_cursor=2;return reply;};
  const box=await open(page,f);await choose(box);await expect(selectedLink(box)).toBeVisible();
  await box.getByRole('button',{name:'Load more assessments'}).click();
  await expect(box.getByRole('status')).toContainText('could not be checked');await expect(selectedLink(box)).toBeHidden();
  expect(f.state.writes).toHaveLength(0);
});
