import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {parse} from 'acorn';
import {gateTimeline, instrumentPmGates} from './pm-gate-trace.mjs';

test('gate diagnostics keep only bounded monotonic scalar transitions, never text or error messages', () => {
  let time = 0;
  const trace = gateTimeline(() => ++time, 3);
  trace.capture('inputs', 1, {ready:true, text:'private draft', document:{body:'private'}});
  trace.capture('inputs', 1, {ready:true, text:'another private draft'});
  assert.equal(trace.read().entries.length, 1);
  assert.equal(trace.value('allowed',1,undefined), undefined);
  assert.equal(trace.value('allowed',1,false), false);
  trace.error('errorBox', {status:0,code:'NETWORK_ERROR',message:'private exception',operation:{id:'private'}});
  trace.capture('inputs',1,{ready:false});
  const {entries,dropped} = trace.read();
  assert.equal(entries.length,3);assert.equal(dropped,1);
  assert.ok(entries.every((row,index)=>!index || row.at>entries[index-1].at));
  assert.ok(entries.every(row=>row.source.length<=6));
  assert.doesNotMatch(JSON.stringify(entries),/private|exception/);
  assert.equal(entries[1].inputs.networkError,true);
  assert.equal(trace.form('other.action'),-1);
});

test('observing app and refresh-cycle writes preserves their exact values', () => {
  const trace=gateTimeline(),state:any={online:true,viewReady:true,nativeBusy:false,epoch:1,token:'private token',refreshCycle:null};
  trace.observeState(state);
  const failure={code:'NETWORK_ERROR',status:0,message:'private exception'},cycle={error:null};
  state.refreshCycle=cycle;cycle.error=failure;state.online=false;
  assert.equal(state.refreshCycle,cycle);assert.equal(cycle.error,failure);assert.equal(state.online,false);
  const rows=trace.read().entries;
  assert.equal(rows.find(row=>row.channel==='refreshCycle.error').inputs.networkError,true);
  assert.equal(rows.at(-1).inputs.online,false);
  assert.doesNotMatch(JSON.stringify(rows),/private/);
});

test('the real committed bundle supports test-only gate observations and remains untouched', async () => {
  const source=await readFile(new URL('../../src/bat_agent_connector/dashboard/app.js',import.meta.url),'utf8');
  const observed=instrumentPmGates(source);
  assert.doesNotThrow(()=>parse(observed,{ecmaVersion:'latest',sourceType:'module'}));
  assert.equal(await readFile(new URL('../../src/bat_agent_connector/dashboard/app.js',import.meta.url),'utf8'),source);
  assert.match(observed,/form\.recoverPermitted/);
  assert.match(observed,/resolution\.inputs/);
  assert.throws(()=>instrumentPmGates('export const unrelated = true;'),/expected one durableForm/);
});
