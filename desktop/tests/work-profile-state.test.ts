import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {workProfileStrings} from '../src/pm-work-profile-strings.js';
const golden=JSON.parse(readFileSync(new URL('../../tests/fixtures/work-profile-readiness.json',import.meta.url),'utf8'));

test('unverified configuration describes current readiness beside a past successful execution',()=>{
  assert.ok(golden.cases.owned_execution.states.last_verified_execution.verified);
  assert.match(workProfileStrings.en.wpf_reason_configured_unverified,/current .* unverified/);
  assert.match(workProfileStrings['zh-TW'].wpf_reason_configured_unverified,/目前.*尚未驗證/);
});

test('each producer state keeps its current value and its own past verification',async()=>{
  const {profileStateFacts}=await import('../src/pm-work-profile-state.js');
  for(const body of Object.values(golden.cases) as any[])for(const state of Object.values(body.states) as any[]){
    const facts=profileStateFacts(state);
    assert.equal(facts.valueKey,'wpf_'+state.value);
    assert.equal(facts.reasonCode,state.reason);
    if(state.verified){
      assert.equal(facts.past.valueKey,'wpf_'+state.verified.value);
      assert.equal(facts.past.at,state.verified.observed_at);
      assert.notEqual(facts.past.at,body.observed_at);
    }else assert.equal(facts.past,null);
  }
  const state=golden.cases.host_unreachable.states.can_observe;
  assert.equal(profileStateFacts(state).valueKey,'wpf_unavailable');
  assert.equal(profileStateFacts(state).past.valueKey,'wpf_ready');
  assert.equal(profileStateFacts(golden.cases.owned_execution.states.can_continue_own).valueKey,'wpf_unknown');
});
test('stale and failed observations have distinct reasons; unknown reasons do not change the value',async()=>{
  const {profileStateFacts}=await import('../src/pm-work-profile-state.js');
  const failed=profileStateFacts({value:'unavailable',reason:'host_unreachable'});
  const stale=profileStateFacts({value:'unavailable',reason:'host_not_refreshed'});
  assert.notEqual(failed.reasonKey,stale.reasonKey);
  for(const reason of [null,undefined,'FUTURE_PROFILE_REASON']){
    const facts=profileStateFacts({value:'unknown',reason});
    assert.equal(facts.reasonKey,'wpf_unverified');assert.equal(facts.valueKey,'wpf_unknown');
    assert.equal(facts.reasonCode,reason??null);
  }
});
test('last success comes only from current evidence, never from past verification',async()=>{
  const {profileStateFacts}=await import('../src/pm-work-profile-state.js');
  for(const name of ['host_unreachable','host_not_refreshed','action_unavailable']){
    const state=golden.cases[name].states.can_observe;
    assert.equal(state.evidence_ref,null);assert.ok(state.verified.evidence_ref.last_success_at);
    assert.equal(profileStateFacts(state).lastSuccessAt,null);
  }
});
test('a current success matching the past verification time is shown only as past verification',async()=>{
  const {profileStateFacts}=await import('../src/pm-work-profile-state.js');
  const state=golden.cases.credential_required.states.can_observe;
  assert.equal(state.evidence_ref.last_success_at,state.verified.observed_at);
  const facts=profileStateFacts(state);
  assert.equal(facts.lastSuccessAt,null);assert.equal(facts.past.at,state.verified.observed_at);
});
test('only validated operation IDs and timestamps survive evidence projection',async()=>{
  const {profileStateFacts}=await import('../src/pm-work-profile-state.js');
  const operation='op_'+'c'.repeat(32);
  const facts=profileStateFacts({value:'unknown',reason:'configured_unverified',
    evidence_ref:{operation_id:operation,last_success_at:100,host:'private.example',scan_id:'scan_private',path:'/srv/private'},
    verified:{value:'ready',observed_at:50,evidence_ref:{operation_id:operation,last_success_at:40}}});
  assert.equal(facts.operationId,operation);assert.equal(facts.lastSuccessAt,100);
  assert.equal(facts.past.operationId,operation);assert.equal(facts.past.at,50);
  assert.ok(!JSON.stringify(facts).match(/private|scan_id|\/srv\//));
  for(const at of [null,undefined,-1,Infinity,NaN,'private.example',253402300800]){
    const bad=profileStateFacts({value:'unknown',evidence_ref:{last_success_at:at,operation_id:'https://private.example'},
      verified:{value:'ready',observed_at:at,evidence_ref:{}}});
    assert.equal(bad.operationId,null);assert.equal(bad.lastSuccessAt,null);assert.equal(bad.past.at,null);
  }
});
test('untrusted free-text reasons stay out of cells while absent evidence stays unknown',async()=>{
  const {profileStateFacts}=await import('../src/pm-work-profile-state.js');
  for(const reason of ['https://private.example','/srv/private','x'.repeat(81),'<img src=x>']){
    const facts=profileStateFacts({value:'unknown',reason});
    assert.equal(facts.reasonCode,null);assert.equal(facts.reasonKey,'wpf_unverified');
  }
  assert.equal(profileStateFacts({value:'unknown',verified:null}).past,null);
});
