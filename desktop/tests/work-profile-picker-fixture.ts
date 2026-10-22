// Shared real picker renderer with producer profile/readiness inputs.
import {expect} from '@playwright/test';
export async function mountProfilePicker(page:any,body:any,profile:any,locale:string,onRead?: (route:any, count:number)=>Promise<void>){
  const reads:string[]=[],posts:string[]=[];
  await page.addInitScript(()=>{
    (window as any).__csp=[];
    document.addEventListener('securitypolicyviolation',event=>(window as any).__csp.push(event.violatedDirective));
  });
  page.on('request',(request:any)=>{if(request.method()==='POST')posts.push(request.url());});
  await page.route('**/api/v1/**',async(route:any)=>{
    const request=route.request(),path=new URL(request.url()).pathname;reads.push(path);
    expect(request.method()).toBe('GET');
    if(path.endsWith('/readiness')&&onRead){await onRead(route,reads.filter(path=>path.endsWith('/readiness')).length);return;}
    const data=path.endsWith('/readiness')?body:path.endsWith('/work-profiles')
      ?{profiles:[profile],count:1,default_profile_id:null,next_cursor:null}:profile;
    await route.fulfill({status:200,json:data});
  });
  await page.goto('/ui-components.html');
  await page.evaluate(async({body,locale})=>{
    const {workProfilePicker}=await import('/src/pm-work-profile-picker.js');
    const {t,setLocale}=await import('/src/i18n.js');const {h}=await import('/src/ui/dom.js');
    setLocale(locale==='en-US'?'en':locale);
    const field=document.createElement('input');field.type='hidden';
    location.hash=`#/pm/${body.workspace_id}/board?select=wi_${'1'.repeat(20)}`;
    const picker=workProfilePicker({t,api:async(method,path)=>{
      if(method!=='GET')throw Error('Picker must only read');
      const response=await fetch('/api/v1'+path),data=await response.json();
      if(!response.ok)throw Object.assign(Error(data.error?.message||'Read failed'),{status:response.status,code:data.error?.code});
      return data;
    },alive:()=>{},namespace:'fixture',workspaceId:body.workspace_id,workId:'wi_'+'1'.repeat(20),
    caps:()=>({scopes:['observe'],features:{pm_workspaces:{work_profiles:{version:1,available:true}}}}),field,
    request:()=>({locked:()=>false,set:(_key,value)=>{field.value=value;},update:()=>{}}),active:()=>true,onChange:()=>{},
    errorBox:error=>h('p',{},String(error))});
    (window as any).__profilePicker=picker;
    // Mirror app.js's existing display-only clock in this isolated component mount.
    setInterval(()=>{if(!document.hidden)document.dispatchEvent(new Event('health-display-tick'));},1000);
    document.getElementById('fixture')!.append(picker.box,field);await picker.load();
  },{body,locale});
  await page.locator('[data-work-profile-picker] select').selectOption(body.profile_id);
  return {reads,posts,facts:page.locator('[data-work-profile-readiness]')};
}
