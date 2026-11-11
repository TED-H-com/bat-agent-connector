import assert from 'node:assert/strict';
import {test} from 'node:test';
import {historyRecovery,historyWithdrawalForm} from '../src/pm-history-recovery.js';
import {historyRecords,historyDraftNamespace} from '../src/pm-history-records.js';
const ws='ws_'+'a'.repeat(32), wi='wi_'+'b'.repeat(20), namespace='fixture';
const row=(n=1)=>({link_id:'hxl_'+n.toString(16).padStart(32,'0'),work_item_id:wi,version:1});
const known=(n=1)=>({...row(n),workspace_id:ws,created_by:'reader',source_id:'example',entry_id:'shi_'+'c'.repeat(32)});
class MemoryStorage {
  values=new Map<string,string>();
  get length(){return this.values.size;}
  key(index:number){return [...this.values.keys()][index]??null;}
  getItem(key:string){return this.values.get(key)??null;}
  setItem(key:string,value:string){this.values.set(key,value);}
  removeItem(key:string){this.values.delete(key);}
}
const target=(r=row())=>({workspace_id:ws,work_item_id:r.work_item_id,link_id:r.link_id});
const request=(r=row())=>({action:'pm.history.unlink',target:target(r),params:{},preconditions:{expected_link_version:r.version}});
const saved=(r=row(),key='original-key')=>({version:1,values:{},intent:{key,request:request(r)}});
const draftKey=(r=row(),ns=`${namespace}.history-cleanup.${ws}`)=>`batc.pm.${ns}.pm.history.unlink.${JSON.stringify(target(r))}`;
function fixture(){const storage=new MemoryStorage();return {storage,recovery:historyRecovery(storage,namespace,ws),records:historyRecords(storage,namespace,ws)};}
function put(storage:MemoryStorage,r=row(),ns?:string,doc:any=saved(r)){const key=draftKey(r,ns);storage.setItem(key,JSON.stringify(doc));return key;}

test('new namespace and envelope need only cleanup IDs and version',()=>{
  const {storage,recovery}=fixture(),selected=recovery.resolve(row());
  assert.equal(selected.storageKey,draftKey()); assert.equal(storage.length,0);
  assert.deepEqual(selected.row,{...target(),version:1});
  recovery.beforePersist(selected,saved(),selected.storageKey,null);
  put(storage); assert.deepEqual(recovery.read()[0].intent,saved().intent);
  assert.deepEqual(Object.keys(saved().intent.request.params),[]);
  for(const field of ['title','text','digest','source_id','entry_id','attempt_id']) assert.throws(()=>recovery.resolve({...row(),[field]:'private'}));
});
test('saved seven-field records keep the legacy namespace, version and serialized bytes',()=>{
  const {storage,recovery,records}=fixture(); records.remember(known()); const before=[...storage.values];
  const selected=recovery.resolve({...row(),version:9});
  assert.equal(selected.namespace,historyDraftNamespace(namespace,known())); assert.equal(selected.row.version,1);
  assert.deepEqual([...storage.values],before);
});
test('orphan legacy intent wins over saved metadata without markers and keeps target ordering',()=>{
  const {storage,recovery,records}=fixture();records.remember({...known(),version:7});
  const original=saved(),ordered={link_id:row().link_id,workspace_id:ws,work_item_id:wi};original.intent.request.target=ordered;
  const ns=historyDraftNamespace(namespace,known()),key=`batc.pm.${ns}.pm.history.unlink.${JSON.stringify(ordered)}`;
  storage.setItem(key,JSON.stringify(original,null,2));const before=[...storage.values];
  const selected=recovery.resolve({...known(),version:9});
  assert.equal(selected.storageKey,key);assert.deepEqual(selected.target,ordered);
  assert.equal(selected.row.version,1);assert.deepEqual(selected.intent,original.intent);
  assert.deepEqual([...storage.values],before);
});
test('source inspector reuses a source-independent intent after a grant returns',()=>{
  const {storage,recovery,records}=fixture();put(storage); records.remember({...known(),version:8});
  const before=[...storage.values],selected=recovery.resolve({...known(),version:8,fixed_ref:{title:'never persisted'}});
  assert.equal(selected.storageKey,draftKey());assert.equal(selected.row.version,1);
  assert.deepEqual(selected.intent,saved().intent);assert.deepEqual([...storage.values],before);
});
test('known and pending records dedupe by link without merging distinct requests',()=>{
  const {storage,recovery,records}=fixture();records.remember(known());put(storage);
  assert.equal(recovery.read().length,1);
  put(storage,row(),historyDraftNamespace(namespace,known()));const before=[...storage.values];
  assert.throws(()=>recovery.read());assert.deepEqual([...storage.values],before);
});
test('conflicting Work IDs fail closed even when an original intent exists',()=>{
  const {storage,recovery,records}=fixture();put(storage);records.remember({...known(),work_item_id:'wi_'+'d'.repeat(20)});
  assert.throws(()=>recovery.read());
});
test('collection accepts 500, refuses 501 without eviction and recovers at capacity',()=>{
  const {storage,recovery}=fixture();const newRow=row(501),selected=recovery.resolve(newRow);
  for(let n=1;n<=500;n++)put(storage,row(n));const before=[...storage.values];
  assert.equal(recovery.read().length,500);
  assert.throws(()=>recovery.admit(selected,null),/FULL/);
  assert.throws(()=>recovery.beforePersist(selected,saved(newRow),selected.storageKey,null),/FULL/);
  const original=recovery.resolve(row());recovery.admit(original,original.intent);
  recovery.beforePersist(original,saved(),original.storageKey,original.intent);
  assert.deepEqual([...storage.values],before);
  put(storage,newRow);assert.throws(()=>recovery.read());
});
test('failed first persist cannot bypass capacity on retry or change its original key',()=>{
  const {storage,recovery}=fixture(),r=row(501),selected=recovery.resolve(r),original=saved(r,'retained-key');
  recovery.beforePersist(selected,original,selected.storageKey,null); // following setItem fails; nothing sent
  for(let n=1;n<=500;n++)put(storage,row(n));
  assert.throws(()=>recovery.admit(selected,original.intent),/FULL/);
  assert.throws(()=>recovery.beforePersist(selected,original,selected.storageKey,original.intent),/FULL/);
  storage.removeItem(draftKey(row(500)));
  recovery.beforePersist(selected,original,selected.storageKey,original.intent);
  assert.equal(original.intent.key,'retained-key');
});
test('new ownership at send time is refused before allocating another request',()=>{
  const {storage,recovery}=fixture(),selected=recovery.resolve(row());
  put(storage,row(),historyDraftNamespace(namespace,known()));
  assert.throws(()=>recovery.admit(selected,null),/CHANGED/);
  assert.equal(recovery.resolve(row()).namespace,historyDraftNamespace(namespace,known()));
});
test('a replaced stored key cannot overwrite or clear a different original intent',()=>{
  const {storage,recovery}=fixture();put(storage);const selected=recovery.resolve(row());
  put(storage,row(),undefined,saved(row(),'different-key'));const before=[...storage.values];
  assert.throws(()=>recovery.beforePersist(selected,saved(),selected.storageKey,selected.intent));
  assert.throws(()=>recovery.beforePersist(selected,{version:1,values:{}},selected.storageKey,selected.intent));
  assert.deepEqual([...storage.values],before);
});
test('documented receipt and refusal flags remain recoverable and empty envelopes do not count',()=>{
  const {storage,recovery}=fixture();
  const original={...saved(),intent:{...saved().intent,operation_id:'op_'+'d'.repeat(32),initialAdmissionRefused:true}};
  put(storage,row(),undefined,original);put(storage,row(2),undefined,{version:1,values:{}});
  assert.equal(recovery.read().length,1);assert.deepEqual(recovery.resolve(row()).intent,original.intent);
  const selected=recovery.resolve(row());recovery.beforePersist(selected,{version:1,values:{}},selected.storageKey,selected.intent);
});
for(const corrupt of [
  value=>({...value,body:'private'}),value=>({...value,values:{title:'private'}}),value=>({...value,version:2}),
  value=>({...value,intent:{...value.intent,key:''}}),value=>({...value,intent:{...value.intent,operation_id:'wrong'}}),
  value=>({...value,intent:{...value.intent,initialAdmissionRefused:'true'}}),value=>({...value,intent:{...value.intent,result:{text:'private'}}}),
  value=>({...value,intent:{...value.intent,request:{...value.intent.request,params:{source_id:'private'}}}}),
  value=>({...value,intent:{...value.intent,request:{...value.intent.request,preconditions:{expected_link_version:0}}}}),
]) test('damaged envelopes are preserved and block new persistence',()=>{
  const {storage,recovery}=fixture();put(storage,row(),undefined,corrupt(saved()));const before=[...storage.values];
  assert.throws(()=>recovery.read());assert.throws(()=>recovery.resolve(row(2)));assert.deepEqual([...storage.values],before);
});
test('unreadable enumeration or relevant storage never becomes an empty list',()=>{
  for(const method of ['key','getItem']) {
    const {storage,recovery}=fixture();put(storage);
    storage[method]=()=>{throw Error('blocked storage');};assert.throws(()=>recovery.read(),/blocked/);
  }
});
test('other account/workspace and non-unlink forms are not parsed or rewritten',()=>{
  const {storage,recovery}=fixture();
  const other='ws_'+'e'.repeat(32);
  storage.setItem(draftKey().replace('fixture.','another-account.'),'broken');
  storage.setItem(draftKey().replaceAll(ws,other),'broken');
  const old=historyDraftNamespace(namespace,known());
  storage.setItem(draftKey(row(),old).replace(ws,other),'broken');
  storage.setItem(`batc.pm.${old}.pm.history.link.${JSON.stringify({workspace_id:ws,work_item_id:wi})}`,'broken');
  const tricky=historyDraftNamespace(namespace,{...known(),source_id:'example.pm.history.unlink.test'});
  storage.setItem(`batc.pm.${tricky}.pm.history.link.${JSON.stringify({workspace_id:ws,work_item_id:wi})}`,'broken');
  const before=[...storage.values];assert.deepEqual(recovery.read(),[]);assert.deepEqual([...storage.values],before);
});
test('resolver remounts an intent appearing after render without submitting either form',async()=>{
  const {storage,recovery}=fixture(),created:any[]=[],disposed:number[]=[],resolved:any[]=[];
  const h=()=>({replaceChildren(){}}),form=options=>{
    const number=created.push(options);
    return {box:{},dispose:()=>disposed.push(number),update(){},checking:()=>false,intentRequest:()=>null};
  };
  const view=historyWithdrawalForm({h,form,recovery,row:row(),t:key=>key,errorBox:()=>({}),permitted:()=>true,onResolve:value=>resolved.push(value)});
  put(storage,row(),historyDraftNamespace(namespace,known()));
  assert.equal(created[0].permitted(),false);await Promise.resolve();
  assert.equal(created.length,2);assert.deepEqual(disposed,[1]);
  assert.equal(created[1].namespace,historyDraftNamespace(namespace,known()));
  assert.equal(resolved[1].intent.key,'original-key');view.dispose();
});
