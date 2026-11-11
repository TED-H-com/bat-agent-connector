import assert from 'node:assert/strict';
import {test} from 'node:test';
import {historyRecords,historyRecord} from '../src/pm-history-records.js';
const ws='ws_'+'a'.repeat(32), wi='wi_'+'b'.repeat(20);
const row=(n=1)=>({workspace_id:ws,work_item_id:wi,link_id:'hxl_'+n.toString(16).padStart(32,'0'),version:1,created_by:'reader',source_id:'example',entry_id:'shi_'+'c'.repeat(32)});
function fixture(){
  const map=new Map<string,string>();
  const storage={getItem:key=>map.get(key)??null,setItem:(key,value)=>map.set(key,value),removeItem:key=>map.delete(key)};
  return {map,records:historyRecords(storage,'fixture',ws)};
}
test('known records retain only identifiers, version and creator, never text or digests',()=>{
  const {map,records}=fixture(); records.remember({...row(),text:'private text',title:'private title',fixed_ref:{source_digest:'secret'},session:{}});
  assert.deepEqual(records.read(),[row()]); assert(![...map.values()][0].includes('private')); assert(![...map.values()][0].includes('digest'));
});
test('validated central fixed references supply the source identity',()=>{
  const {source_id,entry_id,...rest}=row(); assert.deepEqual(historyRecord({...rest,fixed_ref:{source_id,entry_id}}),row());
});
for(const field of ['workspace_id','work_item_id','link_id','entry_id','source_id','created_by','version']) test(`invalid ${field} cannot enter the recovery index`,()=>{
  const {map,records}=fixture();
  for(const value of ['',true,[row()[field]]]) assert.throws(()=>records.remember({...row(),[field]:value}));
  assert.equal(map.size,0);
});
test('wrong workspace, unexpected saved fields and duplicate identities fail closed',()=>{
  const {map,records}=fixture(); assert.throws(()=>records.remember({...row(),workspace_id:'ws_'+'d'.repeat(32)}));
  records.remember(row()); const key=[...map.keys()][0];
  for(const recordsValue of [[{...row(),plaintext:'private'}],[row(),row()],[{...row(),workspace_id:'ws_'+'d'.repeat(32)}]]) {
    map.set(key,JSON.stringify({version:1,records:recordsValue})); assert.throws(()=>records.read());
  }
});
test('bounded storage never evicts original records and still updates a known identity at the limit',()=>{
  const {records}=fixture(); for(let i=1;i<=500;i++) assert.equal(records.remember(row(i)),true);
  assert.equal(records.remember(row(501)),false); assert.equal(records.read().length,500);
  assert.equal(records.remember({...row(),version:2}),true); assert.equal(records.read()[0].version,2);
  records.remove(row(3).link_id); assert.equal(records.remember(row(501)),true);
  assert.equal(records.read().find(r=>r.link_id===row().link_id)?.version,2);
});
test('removing the last known record removes the index without touching another key',()=>{
  const {map,records}=fixture(); map.set('other','unchanged'); records.remember(row()); records.remove(row().link_id);
  assert.deepEqual([...map],[['other','unchanged']]);
});
