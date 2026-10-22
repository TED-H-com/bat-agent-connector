import {test, expect, type Page} from "@playwright/test";
import {selectedReceipt} from './native-receipt-fixture';
import {STRINGS} from '../src/i18n.js';
import {serveGateBaseline} from './action-gate-baseline';
const wid="wi_"+"d".repeat(20), oid="op_"+"a".repeat(32), aid="art_"+"b".repeat(32), digest="c".repeat(64);
const ref={artifact_id:aid,revision:1,digest};
async function fixture(page:Page) {
  const state={actor:"native-file-person",available:true,calls:[] as any[],receipts:[] as any[],errors:[] as string[],held:false,release:null as null|(()=>void),readback:false};
  const caps=()=>({actor:state.actor,scopes:["observe","manage"],api_version:1,contract_version:"2026-10-08",hosts:[],actions:[{action:"artifact.upload",allowed:true}],artifacts:{limits:{max_file_bytes:16*1024*1024,max_selection_count:20,max_selection_bytes:64*1024*1024}}});
  await page.exposeFunction("nativeFileFixture",async(command:string,args:any)=>{
    state.calls.push({command,args});
    if(command==="native_status")return{endpoint:"https://central.example/",credential_available:true,file_transfers:true};
    if(command==="connector_connect")return caps();
    if(command==="connector_disconnect")return null;
    if(command==="native_files_status")return{supported:true,transfers:state.available?state.receipts.filter(r=>r.owner===state.actor):[],drop_error:null};
    if(command==="native_files_drop_target")return null;
    if(command==="native_files_pick"){
      const receipt={...selectedReceipt(args.draftId, {
        transfer_id:"file_"+String(state.receipts.length+1).padStart(32,"0"),
        display_name:"review-notes-檔案.html", digest, media_type:"text/plain",
      }), owner:state.actor};
      state.receipts.push(receipt);
      if(state.held)await new Promise<void>(resolve=>{state.release=resolve;});
      return[receipt];
    }
    if(command==="native_files_upload"){
      const r=state.receipts.find(r=>r.transfer_id===args.handleId);r.stage="uploading";r.transferred_bytes=1024;return r;
    }
    if(command==="native_files_control"){
      const r=state.receipts.find(r=>r.transfer_id===args.transferId);
      if(args.action==="stop"){r.stage="stopped";r.error="Reply unavailable; original operation retained";}
      if(args.action==="retry"){if(r.direction==="download")r.stage="downloading";else{r.stage="verifying";r.operation_id=oid;r.operation_status="waiting_external";}}
      if(args.action==="check"&&state.readback){r.stage="ready";r.operation_id=oid;r.operation_status="succeeded";r.artifact={...ref};r.error=null;}
      if(args.action==="discard_local")state.receipts=state.receipts.filter(x=>x!==r);
      if(args.action==="cancel_upload"){r.stage="cancelled";r.operation_status="cancelled";}
      return null;
    }
    if(command==="native_files_save"){
      const r={transfer_id:"file_"+"e".repeat(32),direction:"download",draft_id:"",display_name:"review-notes-檔案.html",size_bytes:1024,digest,
        stage:"downloading",transferred_bytes:512,error:null,operation_id:null,operation_status:null,artifact:args.reference,intent_key:"",owner:state.actor};state.receipts.push(r);return r;
    }
    if(command==="native_files_preview")return{media_type:"text/plain",text:"<script>window.previewExecuted=true</script><svg onload='alert(1)'/># plain text",base64:null};
    if(command==="connector_request"){
      const input=args.input, path=new URL(input.path,"http://fixture").pathname, proof={cursor:0,token:"native-files-proof"};
      if(input.method!=="GET")throw new Error("Unexpected JS central mutation");
      return{status:200,data:path==="/capabilities"?caps():path==="/bootstrap"?{capabilities:caps(),sync:{version:1,server_id:"file-server",principal_id:state.actor,checkpoint:proof}}
        :path==="/events"?{events:[],next_cursor:0,head_cursor:0,sync:{checkpoint:proof}}
        :path==="/work-items/"+wid?{work_item:{work_item_id:wid,title:"Release evidence",version:1,state:"todo",goal:"",request:"",acceptance:"",steps:[],attachments:[],completion:{display_state:"todo",fingerprint:"fixed"}},project:{project_id:"prj_fixture",name:"Connector"},links:[],path:[],children:[],derived:[],events:[]}
        :path==="/artifacts"?{artifacts:[{revision:{...ref,state:"ready",display_name:"Existing evidence"}}]}
        :path.startsWith("/artifacts/")?{artifact:{...ref,state:"ready",display_name:"Existing evidence"}}
        :{messages:[],sessions:[],operations:[],hosts:[],work_items:[]}};
    }
    throw new Error(`Forbidden or unexpected command: ${command}`);
  });
  await page.addInitScript(()=>Object.assign(window,{isTauri:true,__TAURI_INTERNALS__:{invoke:(command:string,args:any)=>(window as any).nativeFileFixture(command,args)}}));
  page.on("pageerror",error=>state.errors.push(error.message));
  await page.route("**/api/v1/**",()=>{throw new Error("Native WebView must not fetch central");});
  return state;
}
async function open(page:Page,locale="en-US"){
  await page.goto("/dashboard/#/item/"+wid);
  await page.getByRole("button",{name:locale==="en-US"?"More":"更多",exact:true}).click();
  await expect(page.getByRole("button",{name:locale==="en-US"?"Choose files":"選擇檔案",exact:true})).toBeVisible();
}
async function choose(page:Page){await page.getByRole("button",{name:"Choose files",exact:true}).click();await expect(page.getByRole("progressbar")).toBeVisible();}

for (const locale of ['en-US', 'zh-TW']) test(`native picker rechecks the view reason before uploading ${locale}`, async ({browser}) => {
  const context = await browser.newContext({locale}), page = await context.newPage();
  const copy = STRINGS[locale === 'zh-TW' ? 'zh-TW' : 'en'];
  await serveGateBaseline(page);
  const state = await fixture(page);
  state.held = true;
  try {
    await open(page, locale);
    await page.getByRole('button', {name: locale === 'zh-TW' ? '選擇檔案' : 'Choose files', exact: true}).click();
    await expect.poll(() => Boolean(state.release)).toBe(true);
    await page.evaluate(() => {
      const bridge = (window as any).__TAURI_INTERNALS__, invoke = bridge.invoke;
      const releases: (() => void)[] = [];
      (window as any).releaseGatePolls = () => {bridge.invoke = invoke; releases.forEach(release => release());};
      bridge.invoke = async (command: string, args: any) => {
        if (command === 'connector_request' && args.input.path.startsWith('/events'))
          await new Promise<void>(resolve => releases.push(resolve));
        return invoke(command, args);
      };
      window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}));
    });
    state.release!();
    await expect(page.locator('.native-files').getByText(copy.error_code_CLIENT_ACTION_VIEW_NOT_READY, {exact: true})).toBeVisible();
    await expect(page.locator('#live')).toHaveAccessibleName(copy.ui_connector_status.replace('{status}', copy.ui_connected));
    expect(state.calls.filter(c => c.command === 'native_files_upload')).toHaveLength(0);
    expect(state.errors).toEqual([]);
  } finally {
    await page.evaluate(() => (window as any).releaseGatePolls?.()).catch(() => {});
    await context.close();
  }
});

test("opaque upload retains original receipt across stop and reload; full progress is not completion",async({page})=>{
  const state=await fixture(page);await open(page);await choose(page);
  await expect(page.getByRole("progressbar")).toHaveAttribute("value","100");await expect(page.locator(".attachment-list")).not.toContainText(aid);
  await page.getByRole("button",{name:"Stop transfer",exact:true}).click();await expect(page.getByText("Stopped; original result retained",{exact:false})).toBeVisible();
  const first={...state.receipts[0]};await page.reload();await page.getByRole("button",{name:"More",exact:true}).click();
  await expect(page.getByRole("button",{name:"Retry original transfer",exact:true})).toBeVisible();
  expect(state.calls.filter(c=>c.command==="native_files_upload")).toHaveLength(1);
  await page.getByRole("button",{name:"Retry original transfer",exact:true}).click();await expect(page.getByText("Verifying",{exact:false})).toBeVisible();
  await page.getByRole("button",{name:"Stop transfer",exact:true}).click();state.readback=true;
  await page.getByRole("button",{name:"Check result",exact:true}).click();await expect(page.locator(".attachment-list")).toContainText(aid);
  expect(state.receipts[0].intent_key).toBe(first.intent_key);expect(state.receipts[0].draft_id).toBe(first.draft_id);
  expect(state.calls.some(c=>c.command==="connector_upload_artifact")).toBe(false);expect(state.errors).toEqual([]);
});

test("native selection delayed across account switch cannot submit under the new account",async({page})=>{
  const state=await fixture(page);state.held=true;await open(page);await page.getByRole("button",{name:"Choose files",exact:true}).click();
  await expect.poll(()=>Boolean(state.release)).toBe(true);await page.locator("#live").click();
  await page.getByRole("button",{name:"Disconnect",exact:true}).click();state.actor="replacement-person";
  await page.getByRole("button",{name:"Connect",exact:true}).click();await expect(page.getByText("Nothing needs you right now.")).toBeVisible();state.release!();
  await open(page);await expect(page.locator(".native-transfers")).toBeEmpty();await expect(page.locator(".attachment-list")).toBeEmpty();
  expect(state.calls.filter(c=>c.command==="native_files_upload")).toHaveLength(0);expect(state.errors).toEqual([]);
});

test("wrong ready digest is refused without adopting a reference",async({page})=>{
  const state=await fixture(page);await open(page);await choose(page);
  Object.assign(state.receipts[0],{stage:"ready",operation_id:oid,operation_status:"succeeded",artifact:{...ref,digest:"d".repeat(64)}});
  await expect(page.getByText("The transfer receipt does not match the original file. Attachment was not added.")).toBeVisible();
  await expect(page.locator(".attachment-list")).not.toContainText(aid);expect(state.errors).toEqual([]);
});

test("save and literal preview use immutable refs; clearing a selected drop unlocks its draft",async({page})=>{
  const state=await fixture(page);await open(page);
  await page.locator(".attachments select").first().selectOption(aid+":1");await page.getByRole("button",{name:"Add attachment",exact:true}).click();
  await page.getByRole("button",{name:"Preview",exact:true}).click();await expect(page.locator(".file-preview pre")).toContainText("<script>");
  expect(await page.evaluate(()=>(window as any).previewExecuted)).toBeUndefined();expect(await page.locator(".file-preview script, .file-preview svg").count()).toBe(0);
  await page.getByRole("button",{name:"Save As",exact:true}).click();await expect(page.getByText("Downloading",{exact:false})).toBeVisible();
  expect(state.calls.find(c=>c.command==="native_files_save").args).toEqual({reference:ref});
  expect(state.calls.find(c=>c.command==="native_files_preview").args).toEqual({reference:ref});
  await page.getByRole("button",{name:"Enable file drop",exact:true}).click();
  await expect.poll(() => state.calls.filter(c => c.command === "native_files_drop_target" && c.args.enabled).length).toBe(1);
  const draftId=state.calls.filter(c=>c.command==="native_files_drop_target"&&c.args.enabled).at(-1).args.draftId;
  expect(Object.keys(state.calls.filter(c=>c.command==="native_files_drop_target").at(-1).args).sort()).toEqual(["draftId","enabled"]);
  state.receipts.push({transfer_id:"file_"+"f".repeat(32),direction:"upload",draft_id:draftId,display_name:"dropped.txt",size_bytes:5,digest,
    media_type:"text/plain",stage:"selected",transferred_bytes:0,error:null,operation_id:null,operation_status:null,artifact:null,intent_key:"drop-key",owner:state.actor});
  await expect(page.getByRole("button",{name:"Upload",exact:true})).toBeVisible();
  await page.getByRole("button",{name:"Clear local receipt",exact:true}).click();await expect(page.getByText("dropped.txt",{exact:true})).toHaveCount(0);
  expect(state.errors).toEqual([]);
});

test("unfinished download recovers after reload with its original handle and reference",async({page})=>{
  const state=await fixture(page);await open(page);await page.locator(".attachments select").first().selectOption(aid+":1");await page.getByRole("button",{name:"Add attachment",exact:true}).click();
  await page.getByRole("button",{name:"Save As",exact:true}).click();await expect(page.getByRole("button",{name:"Stop transfer",exact:true})).toBeVisible();
  await page.getByRole("button",{name:"Stop transfer",exact:true}).click();const download=structuredClone(state.receipts[0]);
  await page.reload();await page.getByRole("button",{name:"More",exact:true}).click();await page.getByRole("button",{name:"Retry original transfer",exact:true}).click();
  await expect(page.getByText("Downloading",{exact:false})).toBeVisible();
  expect(state.calls.filter(c=>c.command==="native_files_control").at(-1).args).toEqual({transferId:download.transfer_id,action:"retry"});
  expect(state.receipts[0].artifact).toEqual(download.artifact);expect(state.calls.filter(c=>c.command==="native_files_save")).toHaveLength(1);expect(state.errors).toEqual([]);
});

test("explicit central cancellation keeps the original upload distinct from local stop",async({page})=>{
  const state=await fixture(page);await open(page);await choose(page);await page.getByRole("button",{name:"Stop transfer",exact:true}).click();
  state.receipts[0].operation_id=oid;state.receipts[0].operation_status="waiting_external";
  await page.getByRole("button",{name:"Cancel upload",exact:true}).click();await expect(page.getByText("Cancelled",{exact:false})).toBeVisible();
  await expect(page.getByRole("button",{name:"Retry original transfer",exact:true})).toHaveCount(0);
  expect(state.calls.filter(c=>c.command==="native_files_upload")).toHaveLength(1);
  expect(state.calls.filter(c=>c.command==="native_files_control").map(c=>c.args.action)).toEqual(["stop","cancel_upload"]);expect(state.errors).toEqual([]);
});

test("same-principal unavailable native receipt stays visible without retargeting or losing its key",async({page})=>{
  const state=await fixture(page);await open(page);await choose(page);await page.getByRole("button",{name:"Stop transfer",exact:true}).click();
  const original=structuredClone(state.receipts[0]);state.available=false;await page.reload();await page.getByRole("button",{name:"More",exact:true}).click();
  await expect(page.getByText("The original transfer is unavailable on this connection. Removing it from the draft does not cancel the upload.")).toBeVisible();
  expect(state.calls.filter(c=>c.command==="native_files_upload")).toHaveLength(1);
  const saved=await page.evaluate(()=>Object.entries(localStorage).filter(([key])=>key.startsWith("batc.draft.")).map(([,value])=>JSON.parse(value)));
  expect(saved[0].attachments[0].native_receipt.intent_key).toBe(original.intent_key);
  await page.getByRole("button",{name:"Remove",exact:true}).click();await expect(page.locator(".attachment-list")).toBeEmpty();
  expect(state.receipts[0].intent_key).toBe(original.intent_key);expect(state.calls.some(c=>c.command==="native_files_control"&&c.args.action==="cancel_upload")).toBe(false);expect(state.errors).toEqual([]);
});

for(const locale of ["en-US","zh-TW"])for(const width of [390,768,1440])test(`native transfers ${locale} ${width}px`,async({browser}, testInfo)=>{
  const context=await browser.newContext({locale,viewport:{width,height:900}});const page=await context.newPage();
  try{
    const state=await fixture(page);await open(page,locale);await page.getByRole("button",{name:locale==="en-US"?"Choose files":"選擇檔案",exact:true}).click();
    await expect(page.getByRole("progressbar")).toBeVisible();
    await page.locator(".attachments select").first().selectOption(aid+":1");await page.getByRole("button",{name:locale==="en-US"?"Add attachment":"加入附件",exact:true}).click();
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await page.evaluate(()=>scrollTo(0,0));await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    await page.screenshot({path:testInfo.outputPath(`native-${locale}-${width}.png`),fullPage:true});expect(state.errors).toEqual([]);
  }finally{await context.close();}
});
