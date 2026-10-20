import {readFileSync} from 'node:fs';
import {nativeFixture} from './pm-analysis-fixture.ts';
import {mountConversation} from './conversation-fixture.ts';

export const effectiveGolden=JSON.parse(readFileSync(new URL('../../tests/fixtures/pm-effective-route.json',import.meta.url),'utf8'));
export const effectiveExpected={
  saved_bat_blocked:{ready:false,adapter:'bat-pm-linux-v1',reason:'PM_WORKTREE_REQUIRED'},
  saved_native_ready:{ready:true,adapter:'codex-native-linux',reason:null},
  saved_native_blocked:{ready:false,adapter:'codex-native-linux',reason:'PM_NATIVE_PLATFORM_UNSUPPORTED'},
  unset_native_ready:{ready:true,adapter:null,reason:null},
  unset_blocked:{ready:false,adapter:null,reason:'no_eligible_adapter'},
  choice_unreadable:{ready:false,adapter:null,reason:'original_sources_unavailable'},
  scope_missing:{ready:false,adapter:'codex-native-linux',reason:'scope_required'},
};
export const effectiveReasonKeys={
  saved_bat_blocked:['agents_worktree'],saved_native_ready:[],saved_native_blocked:['agents_platform'],
  unset_native_ready:[],unset_blocked:['agents_platform','agents_worktree'],
  choice_unreadable:['pm_effective_sources'],scope_missing:['agents_scope'],
};

// Mount the actual structured gate with the producer's saved choice/descriptors.
export async function mountEffectiveGate(page:any,row:any,locale:string){
  return page.evaluate(async({row,locale})=>{
    const {structuredAnalysis}=await import('/src/pm-structured-analysis.js');
    const {h}=await import('/src/ui/dom.js');const {t,setLocale}=await import('/src/i18n.js');
    setLocale(locale==='en-US'?'en':locale);
    const runtime=structuredAnalysis({h,t,labelled:(key,field)=>h('label',{},t(key),field),
      action:'pm.conversation.propose',capability:()=>row.workspace,
      legacyEligible:()=>false,authorized:()=>['observe','manage','start'].every(scope=>row.capabilities.scopes.includes(scope))});
    const request={locked:()=>false,set:(_key,value)=>{runtime.field.value=value;},update:()=>runtime.update(request)};
    runtime.update(request);
    (document.getElementById('fixture')||document.getElementById('main'))!.append(runtime.box);
    return {ready:runtime.eligible(),adapter:runtime.field.value,reason:runtime.box.querySelector('[data-structured-analysis-reason]')!.textContent};
  },{row,locale});
}

export async function mountEffectiveDiscussion(page:any,row:any){
  const f=nativeFixture();f.state.actor=row.profile.actor;
  f.state.manage=row.capabilities.scopes.includes('manage');f.state.start=row.capabilities.scopes.includes('start');
  const requests:any[]=[];
  await mountConversation(page,false,async(input:any)=>{
    requests.push(structuredClone(input));
    const path=new URL(input.path,'http://fixture').pathname;
    if(path.startsWith('/pm/')&&path.endsWith('/capabilities'))return {status:200,data:structuredClone(row.workspace)};
    const response=await f.dispatch(input);
    if(path==='/capabilities'||path==='/bootstrap'){
      const caps=path==='/bootstrap'?response.data.capabilities:response.data;
      caps.scopes=row.capabilities.scopes;
      for(const key of ['version','native_analysis','bat_analysis','installation_eligibility','workspace_adapter'])
        caps.features.pm_workspaces[key]=row.capabilities.features.pm_workspaces[key];
      caps.actions=[...caps.actions.filter(actual=>!row.capabilities.actions.some(item=>item.action===actual.action)),...row.capabilities.actions];
    }
    return response;
  });
  await page.goto(`/dashboard/#/pm/${row.workspace.workspace_id}/discussions/new`);
  await page.locator('[data-discussion-composer]').waitFor();
  return requests;
}
