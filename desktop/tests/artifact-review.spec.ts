import {test,expect,type Page} from '@playwright/test';
import {createHash} from 'node:crypto';
import {readFile} from 'node:fs/promises';
const captureId='op_'+'a'.repeat(32),acceptId='op_'+'b'.repeat(32),executionId='op_'+'c'.repeat(32),artifactId='art_'+'d'.repeat(32);
const taskId='11111111-2222-4333-8444-555555555555',ref={artifact_id:artifactId,revision:1,digest:'e'.repeat(64)},selector={execution_operation_id:executionId};
const source={host:'demo',session_id:'managed-session',root:'/managed/work',repository_root:'/managed/work',provenance:'connector_managed',selector,lineage:{kind:'execution_operation',execution_operation_id:executionId}};
const evidence={size_bytes:20,digest:ref.digest,head_sha:'1'.repeat(40)};
const proof={kind:'managed_capture',operation_id:captureId,fingerprint:'f'.repeat(64),source,evidence,relative_path:'result.txt'};
const row={...ref,size_bytes:20,state:'ready',display_name:'result.txt',operation_id:captureId,source:proof,acceptances:[]};
async function fixture(page:Page,native:boolean,options:any={}) {
 const state={actor:'reviewer',principal:'principal-one',server:'review-server',scopes:['observe','manage','approve'],writes:[] as any[],previews:[] as any[],reads:[] as string[],errors:[] as string[],events:[] as any[],after:0,contentRequests:[] as any[],fileCalls:[] as any[],downloads:[] as any[],captureStatus:'succeeded',acceptStatus:'succeeded',sourceGone:false,lostCapture:false,lostAccept:false,captureError:null as any,failRead:false,badResult:null as any,badPreview:false,captured:null as any,accepted:null as any,...options};
 const operation=(kind:string)=>{
  const intent=kind==='capture'?state.captured:state.accepted;if(!intent)return null;
  const op={operation_id:kind==='capture'?captureId:acceptId,actor:state.actor,idempotency_key:intent.idempotency_key,...intent.body,status:kind==='capture'?state.captureStatus:state.acceptStatus,result:kind==='capture'?ref:{...ref,operation_id:acceptId,actor:state.actor,meaning:'artifact_revision_review',source_fingerprint:proof.fingerprint,receipt:intent.body.params.receipt,capture_operation_id:captureId,source_commit:evidence.head_sha,lineage:source.lineage},steps:[]};
  if(state.badResult==='key')op.idempotency_key='wrong-key';if(state.badResult==='actor')op.actor='other';if(state.badResult==='params')op.params={...op.params,unexpected:true};if(state.badResult==='ref')op.result={...op.result,digest:'0'.repeat(64)};if(state.badResult==='accept-receipt'&&kind==='accept')op.result={...op.result,receipt:'not your review'};return op;
 };
 const execution=()=>({operation_id:executionId,action:state.executionAction||'checkpoint.continue',status:state.executionStatus||'succeeded',result:state.executionAction==='session.start'?{...source,started:true,prompt_sent:state.promptSent!==false,message_id:state.promptSent===false?null:'batc-'+executionId}:state.executionAction==='repository.continue'?{...source,source_sha:'f'.repeat(40),message_id:state.promptSent===false?null:'batc-'+executionId}:source,steps:[],target:{},params:{},preconditions:{},external_refs:{}});
 const dispatch=async(input:any)=>{
  const url=new URL(input.path,'http://fixture'),path=url.pathname;
  const caps={actor:state.actor,scopes:state.scopes,api_version:1,contract_version:'2026-10-08',hosts:[{host:'demo',writes:true}],features:{checkpoints:[]},artifacts:{capture:{managed_single_file:true},limits:{max_file_bytes:1024}},actions:[{action:'artifact.capture.managed',allowed:state.scopes.includes('manage')},{action:'artifact.accept',allowed:state.scopes.includes('approve')}]};
  if(input.method==='POST'){
   if(path==='/artifact-managed-capture-previews'){
    state.previews.push(input);if(state.holdPreview)await new Promise(resolve=>{state.releasePreview=resolve;});
    return {status:200,data:{preview:{preview_id:'acpv_'+'2'.repeat(32),preview_token:'fixture-managed-preview',fingerprint:proof.fingerprint,source:{...source,selector:input.body.task_id?{task_id:input.body.task_id,command_id:input.body.command_id}:selector,session_id:state.badPreview?'other-source':source.session_id},evidence,relative_path:input.body.relative_path,snapshot:false,expires_at:state.expired?1:Date.now()/1000+600}}};
   }
   state.writes.push(structuredClone(input));const kind=input.body.action==='artifact.capture.managed'?'capture':'accept';
   if(kind==='capture'&&state.captureError)return {status:403,data:{error:{code:state.captureError,message:'Original credential required'}}};
   if(kind==='capture')state.captured||=structuredClone(input);else state.accepted||=structuredClone(input);
   if(kind==='capture'&&state.lostCapture||kind==='accept'&&state.lostAccept){state.lostCapture=state.lostAccept=false;return {status:503,data:{error:{code:'LOST',message:'Reply lost'}}};}
   return {status:200,data:{operation:operation(kind)}};
  }
  state.reads.push(input.path);
  if(path==='/sessions/demo/managed-session')return state.sourceGone?{status:404,data:{error:{code:'NOT_FOUND',message:'Source removed'}}}:{status:200,data:{session:{host:'demo',session_id:'managed-session',provenance:state.manual?'manual':'connector_managed',api_access:state.manual||state.foreign?'read_only':'managed',...(state.foreign?{read_only_code:'FOREIGN_CONTROLLER'}:{})},relations_summary:[]}};
  if(path==='/operations/'+captureId||path==='/operations/'+acceptId){if(state.failRead)return {status:503,data:{error:{code:'READ_FAILED',message:'Read unavailable'}}};if(state.holdRead){state.holdRead=false;await new Promise(resolve=>{state.releaseRead=resolve;});}return {status:200,data:{operation:operation(path.endsWith(captureId)?'capture':'accept')}};}
  if(path==='/operations/'+executionId)return {status:200,data:{operation:execution()}};
  if(path==='/operations')return {status:200,data:{operations:url.searchParams.get('action')===execution().action?[execution()]:[],next_before:null}};
  if(path==='/tasks/'+taskId)return {status:200,data:{task:{task_id:taskId,host:'demo',session_id:'managed-session',commands:[{task_id:taskId,session_id:'managed-session',command_id:'cmd-accepted',kind:'send',status:'accepted'},{task_id:taskId,session_id:'managed-session',command_id:'cmd-unknown',kind:'send',status:'uncertain'}]}}};
  if(path.startsWith('/artifacts/'))return state.artifactMissing?{status:404,data:{error:{code:'ARTIFACT_NOT_FOUND',message:'Artifact unavailable'}}}:{status:200,data:{artifact:state.changedArtifactRevision?{...(state.row||row),revision:2}:state.badArtifact?{...(state.row||row),source:{...(state.row||row).source,fingerprint:'0'.repeat(64)}}:(state.row||row)}};
  if(path==='/artifacts'){if(state.catalogFail)return{status:503,data:{error:{code:'READ_FAILED',message:'Catalog read unavailable'}}};const i=url.searchParams.get('cursor')==='second'?1:0;return {status:200,data:{artifacts:(state.catalogPages?.[i]||[state.row||row]).map((revision:any)=>({revision})),next_cursor:state.catalogPages?.[i+1]?'second':null}};}
  if(path==='/events')state.after=Number(url.searchParams.get('after'));const events=state.events.filter((e:any)=>e.seq>state.after),cursor=events.at(-1)?.seq||state.after;
  return {status:200,data:path==='/capabilities'?caps:path==='/bootstrap'?{capabilities:caps,sync:{version:1,server_id:state.server,principal_id:state.principal,checkpoint:{cursor:0,token:'proof-0'}}}:path==='/events'?{events,next_cursor:cursor,head_cursor:cursor,sync:{checkpoint:{cursor,token:'proof-'+cursor}}}:{sessions:[],operations:[],messages:[],hosts:[],work_items:[],checkpoints:[]}};
 };
 page.on('pageerror',error=>state.errors.push(error.message));
 if(native){await page.exposeFunction('artifactReviewFixture',dispatch);await page.exposeFunction('artifactContentFixture',async(command:string,args:any)=>{
  state.fileCalls.push({command,args});if(command==='native_files_status')return{transfers:state.downloads};
  if(command==='native_files_preview'){if(state.holdContent)await new Promise(resolve=>{state.releaseContent=resolve;});return state.nativePreview||{media_type:'text/plain',text:state.content.toString('utf8')};}
  if(command==='native_files_save'){const record={transfer_id:'file_'+'6'.repeat(32),direction:'download',display_name:'result.txt',size_bytes:state.row.size_bytes,digest:state.row.digest,artifact:state.badDownload?{...args.reference,digest:'0'.repeat(64)}:args.reference,stage:'downloading',transferred_bytes:0};state.downloads.push(record);return record;}
  if(command==='native_files_control'){const entry=state.downloads.find((r:any)=>r.transfer_id===args.transferId);if(args.action==='stop')entry.stage='stopped';if(args.action==='retry')entry.stage='downloading';if(args.action==='discard_local')state.downloads=[];return;}
  throw new Error(command);
 });await page.addInitScript(()=>Object.assign(window,{isTauri:true,__TAURI_INTERNALS__:{invoke:async(command:string,args:any)=>{if(command==='native_status')return {endpoint:'https://fixture.example',credential_available:true,file_transfers:true};if(command==='connector_connect')return (await (window as any).artifactReviewFixture({method:'GET',path:'/capabilities'})).data;if(command==='connector_disconnect')return;if(command==='connector_request')return (window as any).artifactReviewFixture(args.input);if(command.startsWith('native_files_'))return (window as any).artifactContentFixture(command,args);if(command==='fleet_availability')return {configured:false,platform_supported:false};throw new Error(command);}}}));}
 else await page.addInitScript(()=>sessionStorage.setItem('batc.dashboard.token','fixture-token'));
 await page.route('**/api/v1/**',async route=>{if(native)throw new Error('Native managed artifact UI used browser HTTP');const r=route.request(),url=new URL(r.url());if(url.pathname.endsWith('/content')){state.contentRequests.push({url:r.url(),headers:r.headers()});if(state.holdContent)await new Promise(resolve=>{state.releaseContent=resolve;});if(state.contentRedirect)return route.fulfill({status:302,headers:{location:'https://other.invalid/file'}});return route.fulfill({status:200,headers:{'content-length':String(state.wrongLength??state.content.length),'content-type':'application/octet-stream'},body:state.wrongBytes||state.content});}const reply=await dispatch({method:r.method(),path:url.pathname.slice('/api/v1'.length)+url.search,body:r.postDataJSON(),idempotency_key:r.headers()['idempotency-key']});await route.fulfill({status:reply.status,json:reply.data});});return state;
}

async function contentFixture(page:Page,native:boolean,options:any={}){
 const content=options.content||Buffer.from('<script>window.readExecuted=true</script><svg onload="alert(1)"/> Literal file.'),digest=createHash('sha256').update(content).digest('hex');
 const exact={...row,digest,size_bytes:content.length,source:{...proof,evidence:{...evidence,digest,size_bytes:content.length}}};
 return fixture(page,native,{scopes:['observe','approve'],content,row:exact,...options});
}

async function captureForm(page:Page){await page.goto('/dashboard/#/artifact-review/session/demo/managed-session');const box=page.locator('[data-managed-capture]');await box.getByRole('combobox',{name:'Central execution evidence'}).selectOption(JSON.stringify(selector));await box.getByRole('textbox',{name:'Relative file path'}).fill('result.txt');await box.getByRole('button',{name:'Preview source file',exact:true}).click();await expect(box).toContainText(ref.digest);await box.getByRole('checkbox').check();return box;}
for(const native of [false,true]){
 const mode=native?'native':'browser';
 test(`foreign managed source permits reviewed capture without session control (${mode})`,async({page})=>{
  const state=await fixture(page,native,{foreign:true,scopes:['observe','manage']});
  const box=await captureForm(page);await box.getByRole('button',{name:'Save reviewed file',exact:true}).click();
  await expect(page.locator('[data-artifact-ready]')).toBeVisible();
  expect(state.previews).toHaveLength(1);expect(state.writes.map((request:any)=>request.body.action)).toEqual(['artifact.capture.managed']);
  expect(state.errors).toEqual([]);
 });
 test(`reviewer reads exact bytes without creating a review (${mode})`,async({page})=>{
  const state=await contentFixture(page,native);await page.goto('/dashboard/#/artifact-review/artifact/'+artifactId+'/1');await page.getByRole('button',{name:'Preview',exact:true}).click();await expect(page.locator('.artifact-content pre')).toContainText('<script>');expect(await page.evaluate(()=>(window as any).readExecuted)).toBeUndefined();await expect(page.locator('.artifact-content script,.artifact-content svg,.artifact-content iframe')).toHaveCount(0);
  if(native){await page.getByRole('button',{name:'Save As',exact:true}).click();await expect(page.locator('.artifact-content')).toContainText('Downloading');expect(state.fileCalls.find((c:any)=>c.command==='native_files_preview').args).toEqual({reference:{...ref,digest:state.row.digest}});expect(state.fileCalls.find((c:any)=>c.command==='native_files_save').args).toEqual({reference:{...ref,digest:state.row.digest}});}
  else{const download=page.waitForEvent('download');await page.getByRole('button',{name:'Download this revision',exact:true}).click();expect(await readFile((await (await download).path())!)).toEqual(state.content);expect(state.contentRequests).toHaveLength(2);for(const r of state.contentRequests){expect(r.url).toMatch(new RegExp('/artifacts/'+artifactId+'/revisions/1/content$'));expect(r.url).not.toContain('fixture-token');expect(r.headers.authorization).toBe('Bearer fixture-token');}}
  expect(state.writes).toHaveLength(0);expect(state.errors).toEqual([]);
 });
 test(`changed metadata blocks file reading and later preview cannot cross navigation (${mode})`,async({page})=>{
  const state=await contentFixture(page,native);await page.goto('/dashboard/#/artifact-review/artifact/'+artifactId+'/1');await expect(page.locator('[data-artifact-ready]')).toBeVisible();state.badArtifact=true;await page.getByRole('button',{name:'Preview',exact:true}).click();await expect(page.locator('.artifact-content')).toContainText('does not match');expect(state.fileCalls.filter((c:any)=>c.command==='native_files_preview')).toHaveLength(0);expect(state.contentRequests).toHaveLength(0);
  state.badArtifact=false;state.holdContent=true;await page.getByRole('button',{name:'Preview',exact:true}).click();await expect.poll(()=>typeof state.releaseContent).toBe('function');await page.goto('/dashboard/#/artifact-review');state.releaseContent();await expect(page.locator('.artifact-content pre')).toHaveCount(0);expect(state.writes).toHaveLength(0);expect(state.errors).toEqual([]);
 });
 test(`catalog artifact events refresh loaded pages without duplicates and failures hold cursor (${mode})`,async({page})=>{
  const second={...row,artifact_id:'art_'+'7'.repeat(32)},added={...row,artifact_id:'art_'+'8'.repeat(32),display_name:'New ready result'};
  const state=await fixture(page,native,{catalogPages:[[row],[second]]});await page.goto('/dashboard/#/artifact-review');const catalog=page.getByRole('heading',{name:'Saved results from managed sources'}).locator('..');await catalog.getByRole('button',{name:'More',exact:true}).click();await expect(catalog.getByRole('link')).toHaveCount(2);
  state.catalogPages=[[added,row],[row,second]];state.catalogFail=true;state.events=[{seq:1,kind:'artifact.ready',resource_type:'artifact',resource_id:added.artifact_id}];await expect(page.locator('.live')).toHaveAttribute('data-connection','connected');await expect(page.locator('.live')).toHaveAttribute('title',/paused/);expect(state.after).toBe(0);await expect(catalog.getByRole('link')).toHaveCount(2);
  state.catalogFail=false;await expect(catalog.getByRole('link',{name:'New ready result · r1'})).toBeVisible();await expect.poll(()=>state.after).toBe(1);await expect(catalog.getByRole('link')).toHaveCount(3);expect(state.writes).toHaveLength(0);
 });
 test(`fixed artifact event recovers initial read failure without replacing draft (${mode})`,async({page})=>{
  const state=await fixture(page,native);await page.goto('/dashboard/#/artifact-review/artifact/'+artifactId+'/1');await page.locator('[data-artifact-accept] textarea').fill('Keep my review draft');state.artifactMissing=true;await page.reload();await expect(page.locator('.error').first()).toContainText('Artifact unavailable');state.artifactMissing=false;state.events=[{seq:1,kind:'artifact.ready',resource_type:'artifact',resource_id:artifactId}];await expect(page.locator('[data-artifact-ready]')).toBeVisible();await expect.poll(()=>state.after).toBe(1);await expect(page.locator('[data-artifact-accept] textarea')).toHaveValue('Keep my review draft');expect(state.writes).toHaveLength(0);
 });
 test(`published-version execution is a capture candidate and operation entrypoint (${mode})`,async({page})=>{
  const state=await fixture(page,native,{executionAction:'repository.continue'});await page.goto('/dashboard/#/op/'+executionId);await page.getByRole('link',{name:"Capture and review this execution's file",exact:true}).click();
  await expect(page.getByRole('combobox',{name:'Central execution evidence'})).toHaveValue(JSON.stringify(selector));await expect(page.getByRole('combobox',{name:'Central execution evidence'}).locator('option').filter({hasText:'Continue from published version'})).toHaveCount(1);expect(state.writes).toHaveLength(0);
 });
 for(const excluded of [{promptSent:false},{executionStatus:'uncertain'}])test(`unproven published-version execution stays ineligible ${JSON.stringify(excluded)} (${mode})`,async({page})=>{
  const state=await fixture(page,native,{executionAction:'repository.continue',...excluded});await page.goto('/dashboard/#/artifact-review/session/demo/managed-session');await expect(page.getByRole('combobox',{name:'Central execution evidence'}).locator('option').filter({hasText:executionId})).toHaveCount(0);expect(state.writes).toHaveLength(0);
 });
 test(`successful prompted start is a capture candidate and operation entrypoint (${mode})`,async({page})=>{
  const state=await fixture(page,native,{executionAction:'session.start'});await page.goto('/dashboard/#/op/'+executionId);await page.getByRole('link',{name:"Capture and review this execution's file",exact:true}).click();
  await expect(page.getByRole('combobox',{name:'Central execution evidence'})).toHaveValue(JSON.stringify(selector));await expect(page.getByRole('combobox',{name:'Central execution evidence'}).locator('option').filter({hasText:'Start session'})).toHaveCount(1);expect(state.writes).toHaveLength(0);
 });
 for(const excluded of [{promptSent:false},{executionStatus:'uncertain'}])test(`prompt-free or unknown start stays ineligible ${JSON.stringify(excluded)} (${mode})`,async({page})=>{
  const state=await fixture(page,native,{executionAction:'session.start',...excluded});await page.goto('/dashboard/#/artifact-review/session/demo/managed-session');await expect(page.getByRole('combobox',{name:'Central execution evidence'}).locator('option').filter({hasText:executionId})).toHaveCount(0);
  await page.goto('/dashboard/#/op/'+executionId);await expect(page.getByRole('link',{name:"Capture and review this execution's file",exact:true})).toHaveCount(0);await page.goto('/dashboard/#/artifact-review/operation/'+executionId);await expect(page.locator('.error')).toContainText('Connector-managed');await expect(page.getByRole('button',{name:'Preview source file',exact:true})).toBeDisabled();expect(state.previews).toHaveLength(0);expect(state.writes).toHaveLength(0);
 });
 test(`managed capture and acceptance keep separate exact intents (${mode})`,async({page})=>{
  const state=await fixture(page,native),box=await captureForm(page);await box.getByRole('button',{name:'Save reviewed file',exact:true}).click();await expect(page.locator('[data-artifact-ready]')).toContainText(artifactId);
  const review=page.locator('[data-artifact-accept]');await review.getByRole('textbox').fill('Reviewed the exact report.');await review.getByRole('button',{name:'Record review of this revision',exact:true}).click();await expect(page.locator('[data-artifact-accepted]')).toBeVisible();
  expect(state.writes.map((x:any)=>x.body.action)).toEqual(['artifact.capture.managed','artifact.accept']);expect(state.writes[1].body).toEqual({action:'artifact.accept',target:{artifact_id:artifactId,revision:1},params:{digest:ref.digest,source_fingerprint:proof.fingerprint,receipt:'Reviewed the exact report.'},preconditions:{}});
  await page.reload();await expect(page.locator('[data-artifact-accepted]')).toBeVisible();expect(state.writes).toHaveLength(2);expect(state.errors).toEqual([]);await expect(page.getByRole('combobox',{name:'Central execution evidence'})).toHaveValue(JSON.stringify(selector));
 });
 test(`lost capture keeps original credential key after source cleanup (${mode})`,async({page})=>{
  const state=await fixture(page,native,{lostCapture:true}),box=await captureForm(page);await box.getByRole('button',{name:'Save reviewed file',exact:true}).click();await expect(page.locator('.error')).toContainText('Reply lost');
  state.sourceGone=true;state.captureError='FORBIDDEN';await page.reload();await page.getByRole('button',{name:'Check original capture',exact:true}).click();await expect(page.getByText('Capture recovery requires the original credential', {exact:false})).toBeVisible();expect(state.writes[1]).toEqual(state.writes[0]);await expect(page.getByRole('button',{name:'Capture another file'})).toBeDisabled();
  state.captureError=null;await page.reload();await page.getByRole('button',{name:'Check original capture',exact:true}).click();await expect(page.locator('[data-artifact-ready]')).toBeVisible();expect(state.writes[2]).toEqual(state.writes[0]);await page.reload();await expect(page.locator('[data-artifact-ready]')).toBeVisible();expect(state.writes).toHaveLength(3);expect(state.errors).toEqual([]);
 });
 test(`approve-only reviewer opens an exact saved revision (${mode})`,async({page})=>{
  const state=await fixture(page,native,{scopes:['observe','approve']});await page.goto('/dashboard/#/artifact-review/artifact/'+artifactId+'/1');
  const review=page.locator('[data-artifact-accept]');await expect(review).toContainText(ref.digest);await expect(page.locator('[data-managed-capture]')).toBeHidden();
  await review.getByRole('textbox').fill('Read and reviewed this exact revision.');await review.getByRole('button',{name:'Record review of this revision',exact:true}).click();
  await expect(page.locator('[data-artifact-accepted]')).toBeVisible();expect(state.writes.map((w:any)=>w.body.action)).toEqual(['artifact.accept']);expect(state.errors).toEqual([]);
 });
 test(`lost acceptance keeps text, key and fixed ref on reload (${mode})`,async({page})=>{
  const state=await fixture(page,native,{lostAccept:true});await page.goto('/dashboard/#/artifact-review/artifact/'+artifactId+'/1');const review=page.locator('[data-artifact-accept]');
  await review.getByRole('textbox').fill('Original review receipt');await review.getByRole('button',{name:'Record review of this revision',exact:true}).click();await expect(page.locator('.error')).toContainText('Reply lost');
  await page.reload();await expect(review.getByRole('textbox')).toHaveValue('Original review receipt');await expect(review.getByRole('textbox')).toBeDisabled();
  await review.getByRole('button',{name:'Check original review submission',exact:true}).click();await expect(page.locator('[data-artifact-accepted]')).toBeVisible();expect(state.writes[1]).toEqual(state.writes[0]);
  state.artifactMissing=true;await page.reload();await expect(page.locator('[data-artifact-accepted]')).toBeVisible();await expect(page.getByRole('button',{name:'Record another review',exact:true})).toBeDisabled();expect(state.writes).toHaveLength(2);
 });
 test(`new capture rereads task/session context before choosing a later intent (${mode})`,async({page})=>{
  const state=await fixture(page,native),box=await captureForm(page);await box.getByRole('button',{name:'Save reviewed file',exact:true}).click();await expect(page.locator('[data-artifact-ready]')).toBeVisible();state.sourceGone=true;await page.reload();await expect(page.locator('[data-artifact-ready]')).toBeVisible();await page.getByRole('button',{name:'Capture another file',exact:true}).click();await expect(page.locator('.error')).toContainText('Source removed');await expect(page.getByRole('button',{name:'Preview source file',exact:true})).toBeDisabled();expect(state.writes).toHaveLength(1);
 });

 for(const mismatch of ['key','actor','params','ref']) test(`mismatched capture ${mismatch} cannot enable review (${mode})`,async({page})=>{
  const state=await fixture(page,native,{badResult:mismatch}),box=await captureForm(page);await box.getByRole('button',{name:'Save reviewed file',exact:true}).click();
  await expect(page.locator('.error')).toContainText('does not match');await expect(page.locator('[data-artifact-ready]')).toHaveCount(0);await expect(page.getByRole('button',{name:'Capture another file'})).toBeDisabled();
  expect(state.writes).toHaveLength(1);expect(state.errors).toEqual([]);
 });
 test(`mismatched acceptance receipt never reports reviewed (${mode})`,async({page})=>{
  const state=await fixture(page,native,{badResult:'accept-receipt'});await page.goto('/dashboard/#/artifact-review/artifact/'+artifactId+'/1');const box=page.locator('[data-artifact-accept]');
  await box.getByRole('textbox').fill('My exact review');await box.getByRole('button',{name:'Record review of this revision',exact:true}).click();await expect(page.locator('.error')).toContainText('does not match');
  await expect(page.locator('[data-artifact-accepted]')).toHaveCount(0);await expect(page.getByRole('button',{name:'Record another review',exact:true})).toBeDisabled();expect(state.writes).toHaveLength(1);
 });
 test(`task selector offers accepted commands and rejects unsafe file paths (${mode})`,async({page})=>{
  const state=await fixture(page,native);await page.goto('/dashboard/#/artifact-review/task/'+taskId);const box=page.locator('[data-managed-capture]'),select=box.getByRole('combobox',{name:'Central execution evidence'});
  await expect(select.locator('option')).toContainText(['Choose an accepted execution or command','Task Service command']);await expect(select.locator('option').filter({hasText:'cmd-unknown'})).toHaveCount(0);
  await select.selectOption(JSON.stringify({command_id:'cmd-accepted',task_id:taskId}));await box.getByRole('textbox',{name:'Relative file path'}).fill('../unsafe');await expect(box.getByRole('button',{name:'Preview source file',exact:true})).toBeDisabled();
  await box.getByRole('textbox',{name:'Relative file path'}).fill('result.txt');await box.getByRole('button',{name:'Preview source file',exact:true}).click();await expect(box).toContainText(ref.digest);expect(state.previews[0].body).toEqual({host:'demo',session_id:'managed-session',relative_path:'result.txt',task_id:taskId,command_id:'cmd-accepted'});expect(state.writes).toHaveLength(0);
 });
 test(`failed operation event read blocks acknowledgment and preserves fixed draft (${mode})`,async({page})=>{
  const state=await fixture(page,native,{captureStatus:'running'}),box=await captureForm(page);await box.getByRole('button',{name:'Save reviewed file',exact:true}).click();await expect(page.getByRole('link',{name:captureId,exact:true})).toBeVisible();
  state.failRead=true;state.events=[{seq:1,kind:'operation.updated',resource_type:'operation',resource_id:captureId}];await expect(page.locator('.live')).toHaveAttribute('data-connection','connected');await expect(page.locator('.live')).toHaveAttribute('title',/paused/);expect(state.after).toBe(0);
  state.failRead=false;state.captureStatus='succeeded';await expect(page.locator('[data-artifact-ready]')).toBeVisible();await expect.poll(()=>state.after).toBe(1);expect(state.writes).toHaveLength(1);expect(state.errors).toEqual([]);
 });
 test(`read-only manual context cannot capture or claim managed lineage (${mode})`,async({page})=>{
  const state=await fixture(page,native,{manual:true});await page.goto('/dashboard/#/artifact-review/session/demo/managed-session');await expect(page.locator('.error')).toContainText('Connector-managed');await expect(page.getByRole('button',{name:'Preview source file',exact:true})).toBeDisabled();expect(state.previews).toHaveLength(0);expect(state.writes).toHaveLength(0);
 });

 test(`preview mismatch and expiry cannot submit managed capture (${mode})`,async({page})=>{
  const state=await fixture(page,native,{badPreview:true});await page.goto('/dashboard/#/artifact-review/session/demo/managed-session');const box=page.locator('[data-managed-capture]');
  await box.getByRole('combobox',{name:'Central execution evidence'}).selectOption(JSON.stringify(selector));await box.getByRole('textbox',{name:'Relative file path'}).fill('result.txt');await box.getByRole('button',{name:'Preview source file',exact:true}).click();await expect(page.locator('.error')).toContainText('does not match');
  state.badPreview=false;state.expired=true;await box.getByRole('button',{name:'Preview source file',exact:true}).click();await expect(box).toContainText('Preview expired');await expect(box.getByRole('button',{name:'Save reviewed file',exact:true})).toBeDisabled();expect(state.writes).toHaveLength(0);
 });
 test(`late preview cannot follow navigation into another principal (${mode})`,async({page})=>{
  const state=await fixture(page,native,{holdPreview:true});await page.goto('/dashboard/#/artifact-review/session/demo/managed-session');const box=page.locator('[data-managed-capture]');await box.getByRole('combobox',{name:'Central execution evidence'}).selectOption(JSON.stringify(selector));await box.getByRole('textbox',{name:'Relative file path'}).fill('result.txt');await box.getByRole('button',{name:'Preview source file',exact:true}).click();await expect.poll(()=>typeof state.releasePreview).toBe('function');
  state.actor='other-reviewer';state.principal='other-principal';await page.goto('/dashboard/#/settings');await page.getByRole('button',{name:'Disconnect',exact:true}).click();if(!native)await page.getByPlaceholder('batc_…').fill('different-fixture-token');await page.getByRole('button',{name:'Connect',exact:true}).click();await expect(page).toHaveURL(/#\/home$/);state.releasePreview();await page.goto('/dashboard/#/artifact-review/session/demo/managed-session');
  await expect(page.getByRole('textbox',{name:'Relative file path'})).toHaveValue('');expect(state.writes).toHaveLength(0);expect(state.errors).toEqual([]);expect(await page.evaluate(()=>Object.entries(localStorage).filter(([k])=>k.startsWith('batc.artifact-review.')).some(([,v])=>v.includes('result.txt')))).toBe(true);
 });
 test(`exact artifact event keeps focused review text and refuses changed revision (${mode})`,async({page})=>{
  const state=await fixture(page,native);await page.goto('/dashboard/#/artifact-review/artifact/'+artifactId+'/1');const text=page.locator('[data-artifact-accept] textarea');await text.fill('Review text remains focused');state.events=[{seq:1,kind:'artifact.accepted',resource_type:'artifact',resource_id:artifactId}];await expect.poll(()=>state.after).toBe(1);await expect(text).toHaveValue('Review text remains focused');await expect(text).toBeFocused();expect(state.writes).toHaveLength(0);state.changedArtifactRevision=true;state.events.push({seq:2,kind:'artifact.updated',resource_type:'artifact',resource_id:artifactId});await expect(page.locator('.live')).toHaveAttribute('data-connection','connected');await expect(page.locator('.live')).toHaveAttribute('title',/paused/);expect(state.after).toBe(1);await expect(page.getByRole('button',{name:'Record review of this revision',exact:true})).toBeDisabled();await expect(text).toHaveValue('Review text remains focused');state.changedArtifactRevision=false;await expect.poll(()=>state.after).toBe(2);
 });

}
for(const bad of ['digest','length','redirect'])test(`browser content refuses ${bad} without download or preview`,async({page})=>{
 const state=await contentFixture(page,false);await page.goto('/dashboard/#/artifact-review/artifact/'+artifactId+'/1');await expect(page.locator('[data-artifact-ready]')).toBeVisible();let downloads=0;page.on('download',()=>downloads++);let foreign=0;await page.route('https://other.invalid/**',route=>{foreign++;return route.abort();});if(bad==='digest')state.wrongBytes=Buffer.alloc(state.content.length,120);if(bad==='length')state.wrongLength=state.content.length+1;if(bad==='redirect')state.contentRedirect=true;
 await page.getByRole('button',{name:'Download this revision',exact:true}).click();await expect(page.locator('.artifact-content [role=status]')).not.toContainText('Reading');expect(downloads).toBe(0);expect(foreign).toBe(0);expect(state.writes).toHaveLength(0);await expect(page.locator('.artifact-content pre,.artifact-content img')).toHaveCount(0);
});
test('binary content refuses text preview but supports verified download',async({page})=>{
 const state=await contentFixture(page,false,{content:Buffer.from([0,255,1,2])});await page.goto('/dashboard/#/artifact-review/artifact/'+artifactId+'/1');await page.getByRole('button',{name:'Preview',exact:true}).click();await expect(page.locator('.artifact-content')).toContainText('Preview is unavailable');const pending=page.waitForEvent('download');await page.getByRole('button',{name:'Download this revision',exact:true}).click();expect(await readFile((await (await pending).path())!)).toEqual(state.content);expect(state.writes).toHaveLength(0);
});
test('browser PNG preview decodes bounded pixels without source markup',async({page})=>{
 const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/iZk9HQAAAABJRU5ErkJggg==','base64');const state=await contentFixture(page,false,{content:png});await page.goto('/dashboard/#/artifact-review/artifact/'+artifactId+'/1');await page.getByRole('button',{name:'Preview',exact:true}).click();await expect(page.locator('.artifact-content canvas')).toBeVisible();expect(await page.locator('.artifact-content canvas').evaluate((img:any)=>[img.width,img.height,...img.getContext('2d').getImageData(0,0,1,1).data])).toEqual([1,1,255,0,0,255]);expect(state.writes).toHaveLength(0);
});
test('native download reload retains original ref and handle without a second Save As',async({page})=>{
 const state=await contentFixture(page,true);await page.goto('/dashboard/#/artifact-review/artifact/'+artifactId+'/1');await page.getByRole('button',{name:'Save As',exact:true}).click();await page.getByRole('button',{name:'Stop transfer',exact:true}).click();await expect(page.locator('.artifact-content')).toContainText('Stopped');const saved=structuredClone(state.downloads[0]);await page.reload();await page.getByRole('button',{name:'Retry original transfer',exact:true}).click();await expect(page.locator('.artifact-content')).toContainText('Downloading');expect(state.fileCalls.filter((c:any)=>c.command==='native_files_save')).toHaveLength(1);expect(state.fileCalls.filter((c:any)=>c.command==='native_files_control').at(-1).args).toEqual({transferId:saved.transfer_id,action:'retry'});expect(state.downloads[0].artifact).toEqual(saved.artifact);expect(state.writes).toHaveLength(0);
});
test('native download receipt with a different digest is refused',async({page})=>{
 const state=await contentFixture(page,true,{badDownload:true});await page.goto('/dashboard/#/artifact-review/artifact/'+artifactId+'/1');await page.getByRole('button',{name:'Save As',exact:true}).click();await expect(page.locator('.artifact-content')).toContainText('does not match');await expect(page.getByRole('button',{name:'Stop transfer',exact:true})).toHaveCount(0);expect(state.writes).toHaveLength(0);
});
test('content size and PNG pixel bounds refuse before unsafe allocation',async({page})=>{
 const state=await contentFixture(page,false);const oversized=16*1024*1024+1;state.row={...state.row,size_bytes:oversized,source:{...state.row.source,evidence:{...state.row.source.evidence,size_bytes:oversized}}};await page.goto('/dashboard/#/artifact-review/artifact/'+artifactId+'/1');await page.getByRole('button',{name:'Download this revision',exact:true}).click();await expect(page.locator('.artifact-content')).toContainText('16 MiB read limit');expect(state.contentRequests).toHaveLength(0);
 const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/iZk9HQAAAABJRU5ErkJggg==','base64');png.writeUInt32BE(2000000,16);state.content=png;const digest=createHash('sha256').update(png).digest('hex');state.row={...row,digest,size_bytes:png.length,source:{...proof,evidence:{...evidence,digest,size_bytes:png.length}}};await page.reload();await page.getByRole('button',{name:'Preview',exact:true}).click();await expect(page.locator('.artifact-content')).toContainText('Preview is unavailable');await expect(page.locator('.artifact-content canvas')).toHaveCount(0);expect(state.writes).toHaveLength(0);
});
test('native sanitized PNG preview uses exact ref without browser content requests',async({page})=>{
 const state=await contentFixture(page,true,{nativePreview:{media_type:'image/png',base64:'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/iZk9HQAAAABJRU5ErkJggg=='}});await page.goto('/dashboard/#/artifact-review/artifact/'+artifactId+'/1');await page.getByRole('button',{name:'Preview',exact:true}).click();await expect(page.locator('.artifact-content canvas')).toBeVisible();expect(await page.locator('.artifact-content canvas').evaluate((canvas:any)=>[...canvas.getContext('2d').getImageData(0,0,1,1).data])).toEqual([255,0,0,255]);expect(state.contentRequests).toHaveLength(0);expect(state.writes).toHaveLength(0);
});
for(const malformed of ['second-IHDR','after-IEND','missing-IEND','PLTE-after-IDAT','split-IDAT'])test(`malformed PNG ${malformed} is refused before decoding`,async({page})=>{
 const good=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/iZk9HQAAAABJRU5ErkJggg==','base64');let content:Buffer;
 if(malformed==='second-IHDR'){const duplicate=Buffer.from(good.subarray(8,33));duplicate.writeUInt32BE(2000000,8);content=Buffer.concat([good.subarray(0,33),duplicate,good.subarray(33)]);}
 else if(malformed==='after-IEND')content=Buffer.concat([good,good.subarray(8,33)]);
 else if(malformed==='missing-IEND')content=good.subarray(0,-12);
 else if(malformed==='PLTE-after-IDAT'){const chunk=Buffer.alloc(15);chunk.writeUInt32BE(3);chunk.write('PLTE',4);content=Buffer.concat([good.subarray(0,-12),chunk,good.subarray(-12)]);}
 else{const gap=Buffer.alloc(12);gap.write('tEXt',4);content=Buffer.concat([good.subarray(0,-12),gap,good.subarray(33,-12),good.subarray(-12)]);}
 await page.addInitScript(()=>{const original=window.createImageBitmap;(window as any).decodeCalls=0;window.createImageBitmap=((...args:any[])=>{(window as any).decodeCalls++;return (original as any)(...args);}) as any;});
 const state=await contentFixture(page,false,{content});await page.goto('/dashboard/#/artifact-review/artifact/'+artifactId+'/1');await page.getByRole('button',{name:'Preview',exact:true}).click();await expect(page.locator('.artifact-content')).toContainText('Preview is unavailable');expect(await page.evaluate(()=>(window as any).decodeCalls)).toBe(0);await expect(page.locator('.artifact-content canvas')).toHaveCount(0);expect(state.writes).toHaveLength(0);
});
for(const locale of ['en-US','zh-TW']) test.describe(`managed artifact review ${locale}`,()=>{
 test.use({locale});
 for(const width of [390,768,1440]) test(`fits ${width} with exact source evidence`,async({page})=>{
  const state=await fixture(page,false,{scopes:['observe','manage']});await page.setViewportSize({width,height:900});await page.goto('/dashboard/#/artifact-review/artifact/'+artifactId+'/1');
  const review=page.locator('[data-artifact-accept]');await expect(review).toContainText(ref.digest);
  await review.getByRole('textbox').fill(locale==='zh-TW'?'保留這份成果的審閱草稿。':'Keep the review draft.');
  await expect(review.getByRole('button',{name:locale==='zh-TW'?'記錄此版本的審閱':'Record review of this revision',exact:true})).toBeDisabled();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);expect(state.writes).toHaveLength(0);expect(state.errors).toEqual([]);
  await page.screenshot({path:`test-results/artifact-review-${locale}-${width}.png`,fullPage:true});
 });
});
