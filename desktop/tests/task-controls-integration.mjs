import {httpEventDelivery} from './event-delivery.mjs';
// Real central control versions/receipts with MockBat; no remote or installed effects.
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {resolve, delimiter} from 'node:path';
import {once} from 'node:events';
import {setTimeout as delay} from 'node:timers/promises';
import assert from 'node:assert/strict';
import {chromium, expect} from '@playwright/test';
const backend=resolve('..');
const python=process.env.BATC_TASK_CONTROLS_PYTHON || resolve(backend,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');
const child=spawn(python,[resolve('tests/task-controls-fixture.py')],{cwd:backend,
  env:{...process.env,PYTHONPATH:[resolve(backend,'src'),backend].join(delimiter)},stdio:['pipe','pipe','inherit']});
const stopped=once(child,'exit'), lines=createInterface({input:child.stdout})[Symbol.asyncIterator]();
const next=async()=>{const line=await lines.next();if(line.done)throw new Error('Task fixture stopped');return JSON.parse(line.value);};
const control=async command=>{child.stdin.write(JSON.stringify(command)+'\n');return next();};
const browser=await chromium.launch();
try {
  const fixture=await next(), page=await browser.newPage({locale:'en-US'}), errors=[],posts=[];
  const events = await httpEventDelivery(page);
  page.on('pageerror',e=>errors.push(e.message));
  let loseReply=true, changeVersion=false;
  await page.route('**/api/v1/operations?wait=3',async route=>{
    const request=route.request();posts.push({body:request.postDataJSON(),key:request.headers()['idempotency-key']});
    if(changeVersion){changeVersion=false;await control({action:'change-version'});}
    const response=await route.fetch();
    if(loseReply){loseReply=false;await route.abort('failed');}else await route.fulfill({response});
  });
  await page.addInitScript(token=>sessionStorage.setItem('batc.dashboard.token',token),fixture.token);
  await page.goto(`http://127.0.0.1:${fixture.port}/dashboard/#/task/${fixture.task_id}`);
  const panel=page.locator('[data-task-controls]');
  const apply=name=>name==='Prepare another control' ? panel.getByRole('button',{name,exact:true}).click()
    : events.post(panel.getByRole('button',{name,exact:true}));
  const id=async()=> (await panel.locator('a[href^="#/op/"]').getAttribute('href')).split('/').at(-1);
  await apply('Pause dispatch'); await expect(panel.locator('.error')).toBeVisible();
  await control({action:'change-version'}); await page.reload();
  await apply('Retry original request'); await expect(panel).toContainText('control operation completed',{timeout:30000});
  assert.deepEqual(posts[1],posts[0]); assert.equal(posts[0].body.preconditions.control_version,0);
  await control({action:'verify',operation_id:await id(),expected_action:'task.pause',version:2,paused:true,aborts:0,operations:1,steps:['task_pause']});
  await page.reload(); await expect(panel).toContainText('control operation completed');assert.equal(posts.length,2);
  await apply('Prepare another control'); await apply('Resume dispatch');
  await expect(panel).toContainText('control operation completed',{timeout:30000});
  await control({action:'verify',operation_id:await id(),expected_action:'task.resume',version:3,paused:false,aborts:0,operations:2,steps:['task_resume']});
  await apply('Prepare another control'); await panel.getByRole('checkbox').check(); await apply('Pause dispatch');
  await expect(panel).toContainText('control operation completed',{timeout:30000});
  await control({action:'verify',operation_id:await id(),expected_action:'task.pause',version:4,paused:true,aborts:1,operations:3,steps:['task_pause','abort_current']});
  await apply('Prepare another control');changeVersion=true;await apply('Resume dispatch');
  await expect(panel).toContainText('version changed before admission');
  const stale=posts.at(-1);await page.reload();await expect(panel).toContainText('control version 4');
  await apply('Prepare another control');await apply('Resume dispatch');
  await expect(panel).toContainText('control operation completed',{timeout:30000});
  assert.notEqual(posts.at(-1).key,stale.key);assert.equal(posts.at(-1).body.preconditions.control_version,5);
  await control({action:'verify',operation_id:await id(),expected_action:'task.resume',version:6,paused:false,aborts:1,operations:4,steps:['task_resume']});
  const history=await control({action:'history'});
  await page.goto(`http://127.0.0.1:${fixture.port}/dashboard/#/operations`);
  await expect(page.locator('[data-operation-count]')).toContainText('100 shown');
  await page.getByRole('button',{name:'Load earlier operations'}).click();
  await expect(page.locator('[data-operation-count]')).toHaveText(`${history.count} operations`);
  await expect(page.getByRole('button',{name:'Load earlier operations'})).toBeHidden();
  await expect(page.locator('[data-operation-list] [data-row-key]')).toHaveCount(history.count);
  assert.deepEqual(errors,[]);
  console.log('Real central task controls: lost reply/reload, fixed version replay, resume, single optional abort receipt, admission race and 109-row history passed.');
} finally {
  await browser.close();child.stdin.write(JSON.stringify({action:'stop'})+'\n');
  await Promise.race([stopped,delay(10000)]);if(child.exitCode===null)child.kill('SIGTERM');
}
