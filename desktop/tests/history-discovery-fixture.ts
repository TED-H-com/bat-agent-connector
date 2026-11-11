import {expect,type Page} from '@playwright/test';
import {STRINGS} from '../src/i18n.js';
import {mountConversation} from './conversation-fixture.ts';
import {revealPmView} from './pm-navigation.ts';
import {route,workspaceId,workId} from './pm-workbench-fixture.ts';
import {maintenanceFixture} from './history-maintenance-fixture.ts';
export {workspaceId,workId};
export const cleanup=(page:Page)=>page.locator('[data-history-cleanup]');
export const discovery=(page:Page)=>page.locator('[data-history-discovery]');
export function discoveryFixture() {
  const f=maintenanceFixture(),dispatch=f.dispatch;
  f.maintenance.hiddenSource=true;
  const state={capability:true,requests:[] as any[],reads:[] as string[],pages:[] as any[],hold:null as Promise<void>|null,
    transform:null as ((data:any)=>void)|null,deny:0};
  f.dispatch=async input=>{
    state.requests.push(structuredClone(input));const url=new URL(input.path,'http://fixture'),path=url.pathname;
    if(path.endsWith('/external-links/cleanup')) {
      state.reads.push(input.path);
      const work=path.split('/').at(-3),links=f.state.links.filter(row=>row.work_item_id===work);
      const anchor=url.searchParams.get('after'),start=anchor?links.findIndex(row=>row.link_id===anchor)+1:0;
      const slice=links.slice(start,start+20),next=links.length>start+20?slice.at(-1)!.link_id:null;
      const data=structuredClone(state.pages.length?state.pages.shift():{links:slice.map(({link_id,work_item_id,version})=>({link_id,work_item_id,version})),next_after:next});
      state.transform?.(data);const denied=state.deny;if(state.hold)await state.hold;
      return denied?{status:denied,data:{error:{code:'PM_HISTORY_SOURCE_UNAVAILABLE',message:'External history source is unavailable'}}}:{status:200,data};
    }
    const result=await dispatch(input);
    if(path==='/bootstrap'||path==='/capabilities') {
      const caps=path==='/bootstrap'?result.data.capabilities:result.data;
      if(state.capability&&caps.features.external_history.relationships) caps.features.external_history.relationships.cleanup_discovery={version:1};
    }
    return result;
  };
  return Object.assign(f,{discovery:state});
}
export async function openWorkspace(page:Page,native:boolean,f:ReturnType<typeof discoveryFixture>,locale:'en'|'zh-TW'='en') {
  await page.addInitScript(locale=>localStorage.setItem('batc.lang',locale),locale);
  await mountConversation(page,native,f.dispatch);await page.goto(route);
  if((page.viewportSize()?.width||1440)<1000)await page.locator('#workspace-menu').click();
  await (await revealPmView(page,'agents')).click();
}
export async function open(page:Page,native:boolean,f:ReturnType<typeof discoveryFixture>,locale:'en'|'zh-TW'='en') {
  await openWorkspace(page,native,f,locale);await expect(cleanup(page)).toBeVisible();
  await cleanup(page).locator('summary').click();
}
export async function choose(page:Page,locale:'en'|'zh-TW'='en',work=workId(1)) {
  await discovery(page).getByRole('button',{name:STRINGS[locale].exh_cleanup_discover,exact:true}).click();
  await expect(discovery(page).getByRole('combobox').locator(`option[value="${work}"]`)).toHaveCount(1);
  await discovery(page).getByRole('combobox').selectOption(work);
}
