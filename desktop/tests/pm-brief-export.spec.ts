import {test,expect,type Page} from '@playwright/test';
import {createHash} from 'node:crypto';
import {readFile,mkdir} from 'node:fs/promises';
import {pmFixture,pmRoute,workspaceId} from './pm-fixture.ts';
import {mountConversation} from './conversation-fixture.ts';

const sid='pmsnap_'+'a'.repeat(32),older='pmsnap_'+'b'.repeat(32),media:any={markdown:'text/markdown; charset=utf-8',html:'text/html; charset=utf-8',svg:'image/svg+xml; charset=utf-8'};
function fixture(){
  const base=pmFixture(),state:any={enabled:true,reads:[],nativeSaves:[],nativeSupport:true,cancel:false,nativeError:null,reportError:null,
    change:null,snapshotError:null,holdReport:false,held:[],viewDigest:'2'.repeat(64),latest:sid,failWorkspace:false,holdNative:false,nativeHeld:[],quality:false,maintenance:false};
  base.state.manage=false;base.state.role='reader';
  const snapshot=(id=sid)=>({snapshot_id:id,workspace_id:workspaceId,title:id===older?'Earlier Brief':'Current Brief',as_of:1791720000,sequence:id===older?1:2,
    snapshot_digest:'1'.repeat(64),view_digest:state.viewDigest,journal_cursor:4,template_version:1,coverage:{},visibility:{},evidence:[],
    ...(state.maintenance?{maintenance:{version:1}}:{}),
    brief:Object.fromEntries(['goals','delivery','blockers','deviations','decisions'].map(key=>[key,{status:'unassessed',...(key==='delivery'&&state.quality?{quality:{qualified_requirements:1,total_requirements:2}}:{}),items:key==='goals'?[{kind:'goal',text:id===older?'Original selected goal':'Current selected goal',source:{}}]:[]}]))});
  const report=(id:string,format:string,locale:string)=>{
    const content=`${format} · ${locale} · ${id}\n繁體中文 source\n<script>window.reportExecuted=true</script>\n`;
    const value:any={version:1,workspace_id:workspaceId,snapshot_id:id,snapshot_digest:'1'.repeat(64),view_digest:state.viewDigest,format,locale,
      media_type:media[format],template_version:1,renderer_version:state.quality?3:state.maintenance?2:1,content,byte_length:Buffer.byteLength(content),output_digest:createHash('sha256').update(content).digest('hex'),
      coverage:{projection:state.maintenance?'project_brief_with_maintenance_review':'project_brief_only',content_truncated:false},execution_authorized:false,review_completed:false,published:false};
    if(state.change==='renderer')value.renderer_version=4;if(state.change==='downgrade')value.renderer_version=1;
    if(state.change==='projection')value.coverage.projection='unknown_quality_view';
    if(state.change==='digest')value.output_digest='0'.repeat(64);if(state.change==='bytes')value.byte_length=content.length;
    if(state.change==='scope')value.workspace_id='ws_'+'9'.repeat(32);if(state.change==='view')value.view_digest='3'.repeat(64);
    return value;
  };
  const dispatch=async(input:any)=>{
    const url=new URL(input.path,'http://fixture'),path=url.pathname,endpoint=`/pm/workspaces/${workspaceId}`;
    if(path==='/capabilities'||path==='/bootstrap'){
      const result=await base.dispatch(input),caps=path==='/bootstrap'?result.data.capabilities:result.data;
      caps.features.pm_workspaces.brief_snapshots=true;
      if(state.enabled)caps.features.pm_workspaces.brief_export={formats:['markdown','html','svg'],locales:['en','zh-TW'],renderer_version:1};return result;
    }
    if(path===endpoint+'/plans/head')return {status:200,data:{head:{workspace_id:workspaceId,version:1}}};
    if(path===endpoint&&state.failWorkspace)return {status:403,data:{error:{code:'FORBIDDEN',message:'Workspace authority withdrawn'}}};
    if(path.startsWith(endpoint+'/snapshots')){
      state.reads.push(input.path);
      if(path.endsWith('/brief-export')){
        const result=state.reportError?{status:state.reportError,data:{error:{code:'FORBIDDEN',message:'Report source withdrawn'}}}
          :{status:200,data:report(path.split('/').at(-2)!,url.searchParams.get('format')!,url.searchParams.get('locale')!)};
        if(state.holdReport){state.holdReport=false;return new Promise(resolve=>state.held.push(()=>resolve(result)));}return result;
      }
      if(state.snapshotError)return {status:state.snapshotError,data:{error:{code:'FORBIDDEN',message:'Snapshot source withdrawn'}}};
      if(path===endpoint+'/snapshots')return {status:200,data:{workspace_id:workspaceId,snapshots:[snapshot(older),snapshot(sid)],next_cursor:null}};
      const id=path.endsWith('/latest')?state.latest:path.split('/').at(-1)!;return {status:200,data:{workspace_id:workspaceId,snapshot:snapshot(id)}};
    }
    return base.dispatch(input);
  };
  return {base,state,dispatch,report};
}
async function mount(page:Page,native:boolean,f:ReturnType<typeof fixture>){
  if(!native)await mountConversation(page,false,f.dispatch);
  else{
    await page.exposeFunction('briefNativeCommand',async(command:string,args:any)=>{
      if(command==='native_status')return {endpoint:'https://central.example/',credential_available:true,brief_exports:f.state.nativeSupport};
      if(command==='connector_connect')return (await f.dispatch({method:'GET',path:'/capabilities'})).data;
      if(command==='connector_request')return f.dispatch(args.input);
      if(command==='native_brief_save'){
        f.state.nativeSaves.push(structuredClone(args.reference));
        const ref=args.reference,report=f.report(ref.snapshot_id,ref.format,ref.locale);
        const receipt=f.state.cancel?null:{stage:'saved',workspace_id:workspaceId,snapshot_id:ref.snapshot_id,format:ref.format,locale:ref.locale,view_digest:report.view_digest,
          output_digest:report.output_digest,byte_length:report.byte_length,display_name:'selected-report.'+(ref.format==='markdown'?'md':ref.format)};
        const nativeError=f.state.nativeError;
        if(f.state.holdNative){f.state.holdNative=false;return new Promise((resolve,reject)=>f.state.nativeHeld.push(()=>nativeError?reject(Error(nativeError)):resolve(receipt)));}
        if(nativeError)throw Error(nativeError);return receipt;
      }
      throw Error('Unexpected native command '+command);
    });
    await page.addInitScript(()=>Object.assign(window,{isTauri:true,__TAURI_INTERNALS__:{invoke:(command:string,args:any)=>(window as any).briefNativeCommand(command,args)}}));
    await page.route('**/api/v1/**',()=>{throw Error('Native report must use IPC');});
  }
  await page.goto(pmRoute);await expect(page.locator('[data-pm-brief-section]')).toHaveCount(5);
}
const brief=(page:Page)=>page.locator('[data-pm-brief]'),exports=(page:Page)=>page.locator('[data-pm-brief-export]');
async function open(page:Page){await exports(page).locator(':scope > summary').click();}

for(const format of ['markdown','html','svg'])test(`browser downloads exact authorized ${format} bytes without executing content`,async({page})=>{
  const f=fixture();await mount(page,false,f);await open(page);await page.getByLabel('File format',{exact:true}).selectOption(format);
  await page.getByLabel('Report language',{exact:true}).selectOption('zh-TW');const downloaded=page.waitForEvent('download');
  await page.getByRole('button',{name:'Download report',exact:true}).click();const download=await downloaded;
  expect(await readFile((await download.path())!,'utf8')).toBe(f.report(sid,format,'zh-TW').content);
  expect(download.suggestedFilename()).toBe(`pm-brief-${sid}-zh-TW.${format==='markdown'?'md':format}`);
  expect(await page.evaluate(()=>(window as any).reportExecuted)).toBeUndefined();
  expect(f.state.reads.filter((path:string)=>path.includes('brief-export'))).toEqual([`/pm/workspaces/${workspaceId}/snapshots/${sid}/brief-export?format=${format}&locale=zh-TW`]);
  expect(f.base.state.writes).toHaveLength(0);
});

for(const format of ['markdown','html','svg'])test(`native saves ${format} using only fixed references and matching digests`,async({page})=>{
  const f=fixture();await mount(page,true,f);await open(page);await page.getByLabel('File format',{exact:true}).selectOption(format);
  await page.getByRole('button',{name:'Save report…',exact:true}).click();await expect(exports(page)).toContainText('Saved: selected-report');
  expect(f.state.nativeSaves).toEqual([{workspace_id:workspaceId,snapshot_id:sid,format,locale:'en',expected_view_digest:'2'.repeat(64),expected_output_digest:f.report(sid,format,'en').output_digest}]);
  expect(f.base.state.writes).toHaveLength(0);
});

for(const native of [false,true])for(const maintenance of [false,true])for(const format of ['markdown','html','svg'])test(`quality renderer 3 downloads fixed ${format} ${native?'native':'browser'} ${maintenance?'maintenance':'ordinary'} snapshot`,async({page})=>{
  const f=fixture();f.state.quality=true;f.state.maintenance=maintenance;
  await mount(page,native,f);await open(page);await page.getByLabel('File format',{exact:true}).selectOption(format);
  if(native){
    await page.getByRole('button',{name:'Save report…',exact:true}).click();
    await expect(exports(page)).toContainText('Saved: selected-report');
    expect(f.state.nativeSaves).toEqual([{workspace_id:workspaceId,snapshot_id:sid,format,locale:'en',expected_view_digest:'2'.repeat(64),expected_output_digest:f.report(sid,format,'en').output_digest}]);
  }else{
    const downloaded=page.waitForEvent('download');await page.getByRole('button',{name:'Download report',exact:true}).click();
    expect(await readFile((await (await downloaded).path())!,'utf8')).toBe(f.report(sid,format,'en').content);
  }
  expect(f.base.state.writes).toHaveLength(0);
});

for(const change of ['renderer','downgrade','projection'])test(`quality report ${change} mismatch still refuses both transports`,async({page})=>{
  const f=fixture();f.state.quality=true;f.state.change=change;await mount(page,true,f);await open(page);
  await page.getByRole('button',{name:'Save report…',exact:true}).click();
  await expect(brief(page)).toContainText('The report or its readable sources changed');
  expect(f.state.nativeSaves).toHaveLength(0);await expect(page.locator('[data-pm-brief-section]')).toHaveCount(0);
});

for(const change of ['digest','bytes','scope','view'])test(`report ${change} mismatch prevents download and clears the old Brief`,async({page})=>{
  const f=fixture();f.state.change=change;let downloads=0;page.on('download',()=>downloads++);await mount(page,false,f);await open(page);
  await page.getByRole('button',{name:'Download report',exact:true}).click();await expect(brief(page)).toContainText('The report or its readable sources changed');
  await expect(page.locator('[data-pm-brief-section]')).toHaveCount(0);expect(downloads).toBe(0);expect(f.state.nativeSaves).toHaveLength(0);
});

test('report permission loss removes old private facts and does not request a file',async({page})=>{
  const f=fixture();await mount(page,true,f);await open(page);f.state.reportError=403;await page.getByRole('button',{name:'Save report…',exact:true}).click();
  await expect(brief(page)).toContainText('Your token lacks this scope');await expect(brief(page)).not.toContainText('Current selected goal');expect(f.state.nativeSaves).toHaveLength(0);
});

test('native cancellation and existing destinations do not imply a saved file',async({page})=>{
  const f=fixture();await mount(page,true,f);await open(page);f.state.cancel=true;await page.getByRole('button',{name:'Save report…',exact:true}).click();
  await expect(exports(page)).toContainText('Save cancelled');f.state.cancel=false;f.state.nativeError='DESTINATION_EXISTS';
  await page.getByRole('button',{name:'Save report…',exact:true}).click();await expect(exports(page)).toContainText('Choose a new filename');
  await expect(brief(page)).toContainText('Current selected goal');await expect(exports(page)).not.toContainText('Saved:');
});

test('old desktop versions preserve Brief reading and disclose unavailable report saving',async({page})=>{
  const f=fixture();f.state.nativeSupport=false;await mount(page,true,f);await open(page);
  await expect(exports(page)).toContainText('does not support saving Brief reports');await expect(page.getByRole('button',{name:'Save report…',exact:true})).toBeDisabled();
  await expect(brief(page)).toContainText('Current selected goal');expect(f.state.nativeSaves).toHaveLength(0);
});

test('an older selected snapshot remains the download target after a failed refresh',async({page})=>{
  const f=fixture();await mount(page,false,f);await page.getByText('Earlier project snapshots',{exact:true}).click();
  await brief(page).getByRole('button',{name:/^Earlier Brief ·/}).click();await expect(brief(page)).toContainText('Original selected goal');
  f.state.snapshotError=403;await page.getByRole('button',{name:'Refresh',exact:true}).click();await expect(brief(page)).not.toContainText('Original selected goal');
  f.state.snapshotError=null;await page.getByRole('button',{name:'Refresh',exact:true}).click();await expect(brief(page)).toContainText('Original selected goal');
  await open(page);const downloaded=page.waitForEvent('download');await page.getByRole('button',{name:'Download report',exact:true}).click();await downloaded;
  expect(f.state.reads.at(-1)).toContain(`/snapshots/${older}/brief-export`);
});

test('a delayed report cannot download after a different snapshot is selected',async({page})=>{
  const f=fixture();await mount(page,false,f);await open(page);f.state.holdReport=true;let downloads=0;page.on('download',()=>downloads++);
  await page.getByRole('button',{name:'Download report',exact:true}).click();await expect.poll(()=>f.state.held.length).toBe(1);
  await page.getByText('Earlier project snapshots',{exact:true}).click();await brief(page).getByRole('button',{name:/^Earlier Brief ·/}).click();
  await expect(brief(page)).toContainText('Original selected goal');await f.state.held[0]();await expect(page.getByRole('button',{name:'Download report',exact:true})).toBeEnabled();
  expect(downloads).toBe(0);await expect(brief(page)).toContainText('Original selected goal');
});

for(const native of [false,true])test(`parent authority failure blocks a late report ${native?'native':'web'}`,async({page})=>{
  const f=fixture();await mount(page,native,f);await open(page);f.state.holdReport=true;let downloads=0;page.on('download',()=>downloads++);
  await page.getByRole('button',{name:native?'Save report…':'Download report',exact:true}).click();await expect.poll(()=>f.state.held.length).toBe(1);
  f.state.failWorkspace=true;await page.getByRole('button',{name:'Refresh',exact:true}).click();await expect(brief(page)).toBeHidden();
  await f.state.held[0]();await expect(page.locator('[data-pm-brief-section]')).toHaveCount(0);
  expect(downloads).toBe(0);expect(f.state.nativeSaves).toHaveLength(0);
});

for(const cancelled of [false,true])test(`native ${cancelled?'cancel':'saved'} receipt survives an unrelated PM event`,async({page})=>{
  const f=fixture();await mount(page,true,f);await open(page);f.state.holdNative=true;f.state.cancel=cancelled;
  await page.getByRole('button',{name:'Save report…',exact:true}).click();await expect.poll(()=>f.state.nativeHeld.length).toBe(1);
  const before=f.state.reads.length;f.base.state.cursor++;await expect.poll(()=>f.state.reads.length).toBeGreaterThan(before);
  await expect(brief(page)).toContainText('Current selected goal');await f.state.nativeHeld[0]();
  await expect(exports(page)).toContainText(cancelled?'Save cancelled':'Saved: selected-report.md');expect(f.state.nativeSaves).toHaveLength(1);
});

for(const [code,message] of [['DESTINATION_EXISTS','Choose a new filename'],['DESTINATION_UNAVAILABLE','Choose another location'],['BRIEF_EXPORT_SAVE_INTERRUPTED','Check the selected destination']])test(`native ${code} remains visible after an unrelated PM event`,async({page})=>{
  const f=fixture();await mount(page,true,f);await open(page);f.state.holdNative=true;f.state.nativeError=code;
  await page.getByRole('button',{name:'Save report…',exact:true}).click();await expect.poll(()=>f.state.nativeHeld.length).toBe(1);
  const before=f.state.reads.length;f.base.state.cursor++;await expect.poll(()=>f.state.reads.length).toBeGreaterThan(before);
  await expect(brief(page)).toContainText('Current selected goal');f.state.nativeHeld[0]();
  await expect(exports(page)).toContainText(message);await expect(brief(page)).toContainText('Current selected goal');
});

test('a same-snapshot refresh does not hide a delayed report permission failure',async({page})=>{
  const f=fixture();await mount(page,false,f);await open(page);f.state.holdReport=true;f.state.reportError=403;
  await page.getByRole('button',{name:'Download report',exact:true}).click();await expect.poll(()=>f.state.held.length).toBe(1);
  const before=f.state.reads.length;await page.getByRole('button',{name:'Refresh',exact:true}).click();
  await expect.poll(()=>f.state.reads.length).toBeGreaterThan(before);await expect(page.getByRole('button',{name:'Refresh',exact:true})).toBeEnabled();
  await expect(brief(page)).toContainText('Current selected goal');
  f.state.held[0]();await expect(brief(page)).toContainText('Your token lacks this scope');await expect(brief(page)).not.toContainText('Current selected goal');
});

for(const locale of ['en','zh-TW'])for(const width of [390,768,1440])test(`Brief download controls stay compact ${locale} ${width}`,async({browser,baseURL},info)=>{
  const context=await browser.newContext({locale,baseURL,viewport:{width,height:1000}}),page=await context.newPage();const f=fixture();await mount(page,false,f);await open(page);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  const rows=page.locator('.pm-inspector .pm-brief-row');for(let i=0;i<await rows.count();i++){
    const heading=await rows.nth(i).locator('h3').boundingBox(),values=await rows.nth(i).locator('.pm-brief-values').boundingBox();expect(heading!.y+heading!.height).toBeLessThanOrEqual(values!.y);
  }
  const out=process.env.BATC_PM_EXPORT_SCREENSHOTS||info.outputPath('screenshots');await mkdir(out,{recursive:true});await page.screenshot({path:`${out}/brief-export-${locale}-${width}.png`,fullPage:true});
  await context.close();
});
