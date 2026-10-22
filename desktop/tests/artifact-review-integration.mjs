import {httpEventDelivery} from './event-delivery.mjs';
// Exact shared UI against real central managed capture and review, without live providers.
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {createInterface} from 'node:readline';
import {readFile,mkdir} from 'node:fs/promises';
import {delimiter,resolve} from 'node:path';
import {once} from 'node:events';
import {setTimeout as delay} from 'node:timers/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const backend=resolve(process.env.BATC_ARTIFACT_REVIEW_ROOT||'..');
const python=process.env.BATC_ARTIFACT_REVIEW_PYTHON||resolve('../.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');
const child=spawn(python,[resolve('tests/artifact-review-fixture.py'),...(process.argv.includes('--start')?['--start']:[])],{cwd:backend,env:{...process.env,PYTHONPATH:[resolve(backend,'src'),backend].join(delimiter)},stdio:['pipe','pipe','inherit']});
const stopped=once(child,'exit'),lines=createInterface({input:child.stdout})[Symbol.asyncIterator]();
const next=async()=>{const row=await lines.next();if(row.done)throw new Error('Managed artifact fixture stopped');return JSON.parse(row.value);};
const browser=await chromium.launch();
try {
 const fixture=await next(),origin=`http://127.0.0.1:${fixture.port}`,writes=[],errors=[];
 const sourceResponse=await fetch(`${origin}/api/v1/sessions/h1/${fixture.session_id}`,{headers:{Authorization:`Bearer ${fixture.token}`}});
 assert.equal(sourceResponse.status,200);const source=(await sourceResponse.json()).session;
 assert.equal(source.provenance,'connector_managed');assert.equal(source.api_access,'read_only');assert.equal(source.read_only_code,'FOREIGN_CONTROLLER');
 const page=await browser.newPage({locale:'en-US'});const events = await httpEventDelivery(page);
  page.on('pageerror',error=>errors.push(error.message));
 const lost=new Set();await page.route('**/api/v1/operations?wait=3',async route=>{
  const request=route.request(),body=request.postDataJSON();writes.push({body,key:request.headers()['idempotency-key']});
  const response=await route.fetch();if(!lost.has(body.action)){lost.add(body.action);await route.abort('failed');}else await route.fulfill({response});
 });
 await page.route('**/dashboard/**',async route=>{const path=new URL(route.request().url()).pathname,file=path==='/dashboard/'?'index.html':path.slice('/dashboard/'.length);if(!['index.html','app.js','app.css','i18n.js'].includes(file))return route.abort();await route.fulfill({body:await readFile(resolve('../src/bat_agent_connector/dashboard',file)),contentType:file.endsWith('html')?'text/html':file.endsWith('css')?'text/css':'text/javascript'});});
 await page.addInitScript(token=>{if(!sessionStorage.getItem('batc.dashboard.token'))sessionStorage.setItem('batc.dashboard.token',token);},fixture.token);
 await page.goto(origin+'/dashboard/#/session/h1/'+fixture.session_id);
 const sourceLink=page.getByRole('link',{name:"Capture and review this execution's file",exact:true});
 await expect(sourceLink).toBeVisible();await sourceLink.click();
 let capture=page.locator('[data-managed-capture]');
 await capture.getByRole('combobox',{name:'Connector execution evidence'}).selectOption(JSON.stringify({execution_operation_id:fixture.execution_id}));
 await capture.getByRole('textbox',{name:'Relative file path'}).fill('result.bin');await events.post(capture.getByRole('button',{name:'Preview source file',exact:true}));await expect(capture).toContainText(fixture.digest);
 await capture.getByRole('checkbox').check();await events.post(capture.getByRole('button',{name:'Save reviewed file',exact:true}));await expect(page.locator('.error')).toBeVisible();
 await page.reload();capture=page.locator('[data-managed-capture]');await events.post(capture.getByRole('button',{name:'Check original capture',exact:true}));await expect(page.locator('[data-artifact-ready]')).toBeVisible({timeout:30000});
 const captureId=(await page.locator('a[href^="#/op/"]').first().getAttribute('href')).split('/').at(-1);
 const exactRoute=await page.getByRole('link',{name:'result.bin · r1',exact:true}).getAttribute('href');
 await page.goto(origin+'/dashboard/#/settings');await page.getByRole('button',{name:'Disconnect',exact:true}).click();await page.getByPlaceholder('batc_…').fill(fixture.reviewer);await page.getByRole('button',{name:'Connect',exact:true}).click();await expect(page).toHaveURL(/#\/home$/);await page.goto(origin+'/dashboard/'+exactRoute);
 await page.getByRole('button',{name:'Preview',exact:true}).click();await expect(page.locator('.artifact-content')).toContainText('Preview is unavailable');
 const download=page.waitForEvent('download');await page.getByRole('button',{name:'Download this revision',exact:true}).click();const downloaded=await readFile(await (await download).path());assert.equal(createHash('sha256').update(downloaded).digest('hex'),fixture.digest);assert.equal(writes.length,2);
 let review=page.locator('[data-artifact-accept]');await review.getByRole('textbox').fill('Reviewed this exact binary fixture against the expected result.');await events.post(review.getByRole('button',{name:'Record review of this revision',exact:true}));await expect(page.locator('.error')).toBeVisible();
 await page.reload();review=page.locator('[data-artifact-accept]');await events.post(review.getByRole('button',{name:'Check original review submission',exact:true}));await expect(page.locator('[data-artifact-accepted]')).toBeVisible({timeout:30000});
 const ids=await page.locator('a[href^="#/op/"]').evaluateAll(nodes=>nodes.map(n=>n.getAttribute('href').split('/').at(-1)));const acceptId=ids.find(id=>id!==captureId);
 assert.ok(acceptId);await page.reload();await expect(page.locator('[data-artifact-accepted]')).toBeVisible();assert.equal(writes.length,4);assert.deepEqual(writes[1],writes[0]);assert.deepEqual(writes[3],writes[2]);assert.deepEqual(errors,[]);
 child.stdin.write(JSON.stringify({action:'verify',capture_id:captureId,accept_id:acceptId})+'\n');assert.equal((await next()).verified,true);
 await mkdir('test-results',{recursive:true});for(const width of [1440,768,390]){await page.setViewportSize({width,height:900});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:`test-results/artifact-review-real-${width}.png`,fullPage:true});}
 console.log('Real central managed capture and acceptance: exact binary revision read by an observe/approve-only reviewer, separate lost-reply replay keys, GET-only reload, one review receipt, source/index/refs and task/work state unchanged passed');
} finally {
 await browser.close();if(child.exitCode===null&&child.signalCode===null)child.stdin.end(JSON.stringify({action:'stop'})+'\n');await Promise.race([stopped,delay(3000,undefined,{ref:false}).then(()=>child.kill())]);
}
