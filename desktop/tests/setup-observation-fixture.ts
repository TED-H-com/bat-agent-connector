import {expect, type Page} from '@playwright/test';

// Mount the actual Settings reader. All facts/reasons/receipts are Python golden
// output; only the HTTP peer and shell's existing tick are represented here.
export async function mountSetupObservation(page: Page, body: any, operation: any, locale: string,
  onRead?: (route: any, count: number) => Promise<void>) {
  const reads: string[] = [], posts: string[] = [];
  page.on('request', request => {if (request.method() === 'POST') posts.push(request.url());});
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname;
    reads.push(path);
    expect(route.request().method()).toBe('GET');
    if (path.endsWith('/managed/setup') && onRead) {
      await onRead(route, reads.filter(path => path.endsWith('/managed/setup')).length); return;
    }
    await route.fulfill({status: 200, json: path.includes('/operations/') ? {operation} : body});
  });
  await page.goto('/ui-components.html');
  await page.evaluate(async ({operation, locale}) => {
    const {managedSetupPanel} = await import('/src/managed-setup.js');
    const {h} = await import('/src/ui/dom.js');
    const {t,setLocale} = await import('/src/i18n.js');
    setLocale(locale === 'en-US' ? 'en' : locale);
    const {action,target,params,preconditions,operation_id,idempotency_key:key} = operation;
    localStorage.setItem('batc.managed.setup.observation-fixture', JSON.stringify({host:'retained-draft',
      intent:{key,operation_id,request:{action,target,params,preconditions}}}));
    const panel = managedSetupPanel({h,t,namespace:'observation-fixture',guard:()=>{},
      caps:()=>({actor:operation.actor,scopes:['manage'],actions:['host','repository','verification'].map(name=>({action:`setup.${name}`,allowed:true}))}),
      refreshButton:h('button'),errorBox:error=>h('p',{},String(error)),api:async(method,path)=>{
        if(method!=='GET')throw Error('Observation must only read');
        const response=await fetch('/api/v1'+path),data=await response.json();
        if(!response.ok)throw Object.assign(Error('Read failed'),{status:response.status,code:data.error?.code});
        return data;
      }});
    (window as any).__setupObservation=panel;
    document.getElementById('fixture')!.replaceChildren(panel.box);
    // Same event as app.js tickHealth; the product adds no timer.
    setInterval(()=>{if(!document.hidden)document.dispatchEvent(new Event('health-display-tick'));},1000);
    await panel.load();
    for(const node of panel.box.querySelectorAll('details'))node.open=true;
  }, {operation,locale});
  return {reads,posts,panel:page.locator('[data-managed-setup]'),
    fact:(name:string)=>page.locator(`[data-setup-facet="${name}"] [data-setup-fact-value]`)};
}
