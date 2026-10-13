import {openPmSection} from './pm-navigation.ts';
import {test,expect} from '@playwright/test';
import {repairFixture,workspaceId} from './pm-maintenance-repairs-fixture.ts';
import {mountConversation} from './conversation-fixture.ts';
async function mount(page:any,native:boolean,dispatch:(input:any)=>Promise<any>){
  if(!native)return mountConversation(page,false,dispatch);
  await page.exposeFunction('maintenanceConnector',dispatch);
  await page.addInitScript(()=>Object.assign(window,{isTauri:true,__TAURI_INTERNALS__:{invoke:async(command:string,args:any)=>{
    if(command==='native_status')return{endpoint:'https://central.example/',credential_available:true};
    if(command==='connector_connect')return(await(window as any).maintenanceConnector({method:'GET',path:'/capabilities'})).data;
    if(command==='connector_disconnect')return null;
    if(command==='connector_request')return(window as any).maintenanceConnector(args.input);
    throw new Error(`Unexpected native command ${command}`);
  }}}));
  await page.route('**/api/v1/**',()=>{throw new Error('Native must use IPC');});
}
for(const native of [false,true])for(const locale of ['en','zh-TW'])test(`mounted maintenance repair ${native?'IPC':'browser'} ${locale}`,async({browser,baseURL})=>{
  const context=await browser.newContext({baseURL,locale,viewport:{width:locale==='en'?1440:390,height:900}}),page=await context.newPage(),f=repairFixture();
  await mount(page,native,f.dispatch);await page.goto(`/dashboard/#/pm/${workspaceId}`);await openPmSection(page, 'issues' , 'ui_maintenance_review');
  const panel=page.locator('[data-pm-maintenance-repairs]'),form=panel.locator('[data-pm-action="pm.maintenance.repair"]');
  await expect(panel).toHaveCount(1);
  await form.getByLabel(locale==='en'?'Observed Issue':'觀察到的 Issue',{exact:true}).selectOption('pmfn_'+'f'.repeat(32));
  await form.getByLabel(locale==='en'?'Adopted repair rule':'已採用的修復規則',{exact:true}).selectOption('docs_fix');
  await form.locator('button[type=submit]').click();await expect.poll(()=>f.state.writes.length).toBe(1);
  expect(f.state.writes[0].body.params.rule_key).toBe('docs_fix');
  expect(f.state.writes[0].body.params.finding_ref).toEqual({...f.state.findings.observation_ref,finding_id:'pmfn_'+'f'.repeat(32)});
  expect(await panel.innerText()).not.toMatch(/pmr_[a-z_]+|\bundefined\b/);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await context.close();
});
