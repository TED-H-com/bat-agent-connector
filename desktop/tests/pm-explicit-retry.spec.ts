import {test, expect, type Page} from '@playwright/test';
import {readFile} from 'node:fs/promises';
import {governanceFixture, workspaceId} from './pm-governance-fixture.ts';
import {unavailableBriefTransport} from './pm-source-transport-fixture.ts';
import {STRINGS} from '../src/i18n.js';

// Component-level tests: real durableForm, DOM, error renderer and ApiError;
// only its API and authority/time interleavings are controlled by the fixture.
async function mountForm(page: Page, locale: string) {
  const f = governanceFixture();
  const record = f.state.records[0];
  const document = record.revision.document;
  const params = {...Object.fromEntries(['title','description','owner_actor','affected_requirement_ids','trigger','response','review_at','dimension','alternatives','disambiguation'].map(key => [key,document[key]])),
    source_refs: document.sources.map(({kind,id}: any) => ({kind,id})), proposed_revision_id:null,status:'reported_resolved'};
  await page.route('**/__retry_src/**', async route => {
    const file = new URL(route.request().url()).pathname.slice('/__retry_src/'.length);
    await route.fulfill({contentType:'text/javascript',body:await readFile(new URL('../src/'+file,import.meta.url),'utf8')});
  });
  await unavailableBriefTransport(page,'__retry_src');
  await page.goto('/ui-components.html');
  await page.evaluate(async ({locale,caps,target,params}) => {
    const {h} = await import('/__retry_src/ui/dom.js');
    const {t,setLocale} = await import('/__retry_src/i18n.js');setLocale(locale);
    const {ApiError} = await import('/__retry_src/error-presentation.js');
    const {createErrorBox} = await import('/__retry_src/test-error-box.js');
    const {pmDurableForm} = await import('/__retry_src/pm-workspace.js');
    const win = window as any;
    const state = win.retryState = {writes:[] as any[],guardCode:null as string|null,sendReady:true,permitted:true,mode:'lost',held:null as any,resolve:null as any};
    const guard = () => {if(state.guardCode)throw new ApiError(0,state.guardCode,'View changed during refresh');};
    const api = async (method:string,path:string,body:any,key:string) => {
      const row = {method,path,body:structuredClone(body),key};state.writes.push(row);
      if(state.mode==='lost')throw new ApiError(0,'NETWORK_ERROR','Failed to fetch');
      if(state.held)await state.held;
      const original=state.writes.find(row=>row.method==='POST');
      return {operation:{operation_id:'op_'+'8'.repeat(32),actor:caps.actor,idempotency_key:method==='GET'?original.key:key,
        ...(method==='GET'?original.body:body),status:state.mode==='pending'?'running':'succeeded',result:null}};
    };
    const reason = h('textarea',{'aria-label':t('pcp_resolve_rationale'),required:true});reason.value='Preserve the reviewed original request';
    const options = win.retryOptions = {h,t,api,caps:()=>caps,guard,namespace:'explicit-retry',action:'pm.governance.revise',target,
      fields:{rationale:reason},params:values=>({...params,rationale:values.rationale}),preconditions:()=>({expected_version:1}),
      permitted:()=>state.permitted,canSend:()=>state.sendReady,onSaved:()=>{},label:'pcp_resolve_issue',extra:reason,errorBox:createErrorBox(h,t,locale)};
    const ui = win.retryForm = pmDurableForm(options);
    document.getElementById('fixture')!.append(ui.box);
  }, {locale,caps:f.config().caps,target:{workspace_id:workspaceId,record_id:record.record.record_id},params});
  const form = page.locator('[data-pm-action="pm.governance.revise"]');
  await form.locator('button[type=submit]').click();
  await expect(form).toContainText(STRINGS[locale].error_code_NETWORK_ERROR);
  await expect(form.locator('button[type=submit]')).toHaveText(STRINGS[locale].pm_check_original);
  await expect(form.locator('button[type=submit]')).toBeEnabled();
  expect(await page.evaluate(() => (window as any).retryState.writes.length)).toBe(1);
  return form;
}

for(const locale of ['en','zh-TW']) {
  for(const code of ['VIEW_CHANGED','CONNECTION_CHANGED'])test(`an explicit retry explains a ${code} guard refusal ${locale}`,async({page})=>{
    const form = await mountForm(page,locale);
    const before=await page.evaluate(()=>Object.fromEntries(Object.entries(localStorage).filter(([key])=>key.startsWith('batc.pm.explicit-retry.'))));
    await page.evaluate(code=>{(window as any).retryState.guardCode=code;},code);
    await form.getByRole('button',{name:STRINGS[locale].pm_check_original,exact:true}).click();
    await expect(form.locator(':scope > [role=status]')).toContainText(STRINGS[locale].pm_stale);
    expect(await page.evaluate(()=>(window as any).retryState.writes.length)).toBe(1);
    expect(await page.evaluate(()=>Object.fromEntries(Object.entries(localStorage).filter(([key])=>key.startsWith('batc.pm.explicit-retry.'))))).toEqual(before);
  });
  for(const interruption of ['none','connection flip','reconnect','receipt notification','scroll'])test(`explicit original retry survives ${interruption} ${locale}`,async({page})=>{
    const form=await mountForm(page,locale);
    const before=await page.evaluate(()=>(window as any).retryState.writes[0]);
    await page.evaluate(interruption=>{
      const win=window as any,ui=win.retryForm,state=win.retryState;
      state.mode='success';
      if(interruption==='connection flip'){state.sendReady=false;ui.update();state.sendReady=true;ui.update();}
      if(interruption==='reconnect'){const parent=ui.box.parentNode;ui.box.remove();parent.append(ui.box);}
      if(interruption==='receipt notification'){
        // Reconnecting the observer rereads storage. No receipt ID exists,
        // so this must not schedule an automatic POST.
        const child=ui.box.querySelector('batc-pm-form-connection');child.remove();ui.box.append(child);
      }
      if(interruption==='scroll')window.scrollTo(0,document.body.scrollHeight);
    },interruption);
    expect(await page.evaluate(()=>(window as any).retryState.writes.length)).toBe(1);
    await form.getByRole('button',{name:STRINGS[locale].pm_check_original,exact:true}).click();
    await expect(form.locator('[data-pm-saved]')).toBeVisible();
    const writes=await page.evaluate(()=>(window as any).retryState.writes);
    expect(writes).toHaveLength(2);expect(writes[1]).toEqual(before);
  });
  test(`a gate closed after actionability explains refusal without a new key ${locale}`,async({page})=>{
    const form=await mountForm(page,locale);
    await page.evaluate(()=>{(window as any).retryState.permitted=false;});
    await form.getByRole('button',{name:STRINGS[locale].pm_check_original,exact:true}).click();
    await expect(form.locator(':scope > [role=status]')).toContainText(STRINGS[locale].pm_action_unavailable);
    expect(await page.evaluate(()=>(window as any).retryState.writes.length)).toBe(1);
  });
}

for(const locale of ['en','zh-TW'])for(const resumed of [false,true])test(`a read gate interrupted the press, resumed=${resumed} ${locale}`,async({page})=>{
 const form=await mountForm(page,locale),retry=form.getByRole('button',{name:STRINGS[locale].pm_check_original,exact:true});
 const original=await page.evaluate(()=>(window as any).retryState.writes[0]);
 await retry.hover();await page.mouse.down();
 await page.evaluate(()=>{const w=window as any;w.retryState.sendReady=false;w.retryForm.update();});
 await expect(retry).toBeDisabled();
 if(resumed)await page.evaluate(()=>{const w=window as any;w.retryState.mode='success';w.retryState.sendReady=true;w.retryForm.update();});
 await page.mouse.up();
 if(resumed){await expect(form.locator('[data-pm-saved]')).toBeVisible();const writes=await page.evaluate(()=>(window as any).retryState.writes);expect(writes).toHaveLength(2);expect(writes[1]).toEqual(original);}
 else {
  await page.evaluate(()=>{const w=window as any;w.retryState.sendReady=true;w.retryForm.update();});
  await expect(retry).toBeEnabled();expect(await page.evaluate(()=>(window as any).retryState.writes.length)).toBe(1);
  await expect(form.locator(':scope > [role=status]')).toContainText(STRINGS[locale].pm_click_interrupted);
 }
});

for(const locale of ['en','zh-TW'])test(`a receipt subscriber switches the explicit recovery to the original GET ${locale}`,async({page})=>{
 await page.clock.install();const form=await mountForm(page,locale);await page.clock.pauseAt(await page.evaluate(()=>Date.now()+100));
 await page.evaluate(async()=>{
  const w=window as any,{h}=await import('/__retry_src/ui/dom.js'),{pmDurableForm}=await import('/__retry_src/pm-workspace.js');
  const field=h('textarea');field.value='Preserve the reviewed original request';
  const peer=pmDurableForm({...w.retryOptions,fields:{rationale:field},extra:field});document.getElementById('fixture').append(peer.box);
  w.retryState.mode='pending';await peer.submit();peer.dispose();peer.box.remove();w.retryState.mode='success';
 });
 const writes=await page.evaluate(()=>(window as any).retryState.writes);expect(writes).toHaveLength(2);expect(writes[1]).toEqual(writes[0]);
 await form.getByRole('button',{name:STRINGS[locale].pm_check_original,exact:true}).click();await expect(form.locator('[data-pm-saved]')).toBeVisible();
 const settled=await page.evaluate(()=>(window as any).retryState.writes);expect(settled).toHaveLength(3);
 expect(settled[2].method).toBe('GET');expect(settled[2].path).toBe('/operations/op_'+'8'.repeat(32));
});

for(const locale of ['en','zh-TW'])test(`a retry checked before pointerdown explains the closed gate ${locale}`,async({page})=>{
 const form=await mountForm(page,locale),retry=form.getByRole('button',{name:STRINGS[locale].pm_check_original,exact:true});
 await expect(retry).toBeEnabled();
 await page.evaluate(()=>document.addEventListener('pointerdown',()=>{
  const w=window as any;w.retryState.sendReady=false;w.retryForm.update();
 },{once:true,capture:true}));
 await retry.click();
 await page.evaluate(()=>{const w=window as any;w.retryState.sendReady=true;w.retryForm.update();});
 await expect(retry).toBeEnabled();expect(await page.evaluate(()=>(window as any).retryState.writes.length)).toBe(1);
 await expect(form.locator(':scope > [role=status]')).toContainText(STRINGS[locale].pm_click_interrupted);
});
