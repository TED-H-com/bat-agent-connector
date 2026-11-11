import {test} from 'node:test';
import assert from 'node:assert/strict';
import {cleanupPage} from '../src/pm-history-discovery.js';

const work='wi_'+'a'.repeat(20),id='hxl_'+'b'.repeat(32),cursor='hxl_'+'1'.repeat(32);
const row=()=>({link_id:id,work_item_id:work,version:1});
const page=()=>({links:[row()],next_after:null});
test('cleanup projection copies only three fields and accepts an opaque hidden-row cursor',()=>{
  const source={...page(),next_after:cursor},before=JSON.stringify(source);
  assert.deepEqual(cleanupPage(source,work),source);assert.equal(JSON.stringify(source),before);
  assert.notEqual(cleanupPage(source,work).links[0],source.links[0]);
  assert.deepEqual(cleanupPage({links:[],next_after:cursor},work),{links:[],next_after:cursor});
});
for(const [name,change] of Object.entries({
  'source field':p=>{p.links[0].source_id='example';},'creator field':p=>{p.links[0].created_by='someone';},
  'extra envelope':p=>{p.workspace_id='ws_'+'a'.repeat(32);},'missing cursor':p=>{delete p.next_after;},
  'numeric cursor':p=>{p.next_after=2;},'string null cursor':p=>{p.next_after='null';},
  'wrong Work':p=>{p.links[0].work_item_id='wi_'+'c'.repeat(20);},'unsafe version':p=>{p.links[0].version=Number.MAX_SAFE_INTEGER+1;},
  'string version':p=>{p.links[0].version='1';},'zero version':p=>{p.links[0].version=0;},
  'invalid ID':p=>{p.links[0].link_id='hxl_invalid';},'duplicate ID':p=>{p.links.push(row());},
  'oversized page':p=>{p.links=Array.from({length:21},(_,n)=>({...row(),link_id:'hxl_'+n.toString(16).padStart(32,'0')}));},
  'null row':p=>{p.links=[null];},'null list':p=>{p.links=null;},
})) test(`cleanup response refuses ${name} without mutating input`,()=>{
  const value:any=page();change(value);const original=JSON.stringify(value);
  assert.throws(()=>cleanupPage(value,work),/HISTORY_CLEANUP_PAGE_INVALID/);assert.equal(JSON.stringify(value),original);
});
test('cleanup pages reject repeated IDs and cursor loops across pages',()=>{
  assert.throws(()=>cleanupPage(page(),work,new Set([id])),/HISTORY_CLEANUP_PAGE_INVALID/);
  assert.throws(()=>cleanupPage({links:[],next_after:cursor},work,new Set(),new Set([cursor])),/HISTORY_CLEANUP_PAGE_INVALID/);
});
