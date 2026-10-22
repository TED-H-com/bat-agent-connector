import {httpEventDelivery} from './event-delivery.mjs';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {resolve,delimiter} from 'node:path';
import {once} from 'node:events';
import {setTimeout as delay} from 'node:timers/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const backend=resolve('..'),python=process.env.BATC_LABELS_PYTHON||resolve(backend,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');
const child=spawn(python,[resolve('tests/session-labels-fixture.py')],{cwd:backend,env:{...process.env,PYTHONPATH:[resolve(backend,'src'),backend].join(delimiter)},stdio:['pipe','pipe','inherit']});
const stopped=once(child,'exit'),lines=createInterface({input:child.stdout})[Symbol.asyncIterator]();
const next=async()=>{const line=await lines.next();if(line.done)throw Error('Labels fixture stopped');return JSON.parse(line.value);};
const control=async command=>{child.stdin.write(JSON.stringify(command)+'\n');return next();};
const browser=await chromium.launch();
try{
 const fixture=await next(),page=await browser.newPage({locale:'en-US'}),posts=[],errors=[];let lost=true;
 const events = await httpEventDelivery(page);
  page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/v1/operations?wait=3',async route=>{
  const request=route.request();posts.push({body:request.postDataJSON(),key:request.headers()['idempotency-key']});
  const response=await route.fetch();if(lost){lost=false;await route.abort('failed');}else await route.fulfill({response});
 });
 await page.addInitScript(token=>sessionStorage.setItem('batc.dashboard.token',token),fixture.token);
 const open=async()=>{await page.goto(`http://127.0.0.1:${fixture.port}/dashboard/#/session/h1/${fixture.sid}`);const panel=page.locator('[data-session-labels]');await panel.locator('summary').click();return panel;};
 let panel=await open();await panel.getByRole('textbox').fill('待確認\nUI');await events.post(panel.getByRole('button',{name:'Save labels',exact:true}));
 await expect(panel.locator('.error')).toBeVisible();await control({action:'verify',labels:['待確認','UI'],version:1});
 await control({action:'other-edit'});await page.reload();panel=page.locator('[data-session-labels]');await panel.locator('summary').click();
 await events.post(panel.getByRole('button',{name:'Retry original request'}));await expect(panel).toContainText('This label change was saved');
 assert.deepEqual(posts[0],posts[1]);await control({action:'verify',labels:['Other edit'],version:2});
 await panel.getByRole('button',{name:'Review current version and prepare change'}).click();await panel.getByRole('textbox').fill('Final labels');
 await events.post(panel.getByRole('button',{name:'Save labels',exact:true}));await expect(panel).toContainText('This label change was saved');
 await control({action:'verify',labels:['Final labels'],version:3});assert.notEqual(posts[2].key,posts[0].key);assert.equal(posts[2].body.preconditions.expected_version,2);
 await page.goto(`http://127.0.0.1:${fixture.port}/dashboard/#/sessions`);await page.locator('#main input[type=search]').fill('Final labels');
 await expect(page.locator('.session-entry')).toHaveCount(1);assert.deepEqual(errors,[]);
 console.log('A03 real central labels passed: manual source bytes/HEAD/index/refs unchanged, zero BAT mutations, original key replay after later edit/reload, CAS version and loaded-list search.');
}finally{
 await browser.close();child.stdin.write(JSON.stringify({action:'stop'})+'\n');await Promise.race([stopped,delay(10000)]);if(child.exitCode===null)child.kill('SIGTERM');
}
