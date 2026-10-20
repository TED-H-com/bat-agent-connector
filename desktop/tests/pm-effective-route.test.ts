import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {STRINGS} from '../src/i18n.js';
import {workspacePmReady,effectivePmRoute,pmRouteReasonKey} from '../src/pm-effective-route.js';
const golden=JSON.parse(readFileSync(new URL('../../tests/fixtures/pm-effective-route.json',import.meta.url),'utf8'));

test('workspace readers use the producer facet and ignore other eligible installations',()=>{
  for(const row of Object.values(golden) as any[]){
    assert.equal(workspacePmReady(row.workspace),row.readiness.pm_analysis.pm_ready);
    assert.deepEqual(effectivePmRoute(row.workspace),row.readiness.pm_analysis.effective_pm_route);
    for(const state of row.workspace.effective_pm_route.adapters)if(state.reason)
      for(const lang of ['en','zh-TW'])assert.ok(STRINGS[lang][pmRouteReasonKey(state.reason)]);
  }
  assert.equal(workspacePmReady(golden.saved_bat_blocked.workspace),false);
});
test('only absent facets use the older server compatibility computation',()=>{
  const older=structuredClone(golden.saved_bat_blocked.workspace);delete older.effective_pm_route;
  assert.equal(workspacePmReady(older),true);
  for(const value of [null,{version:2,ready:true},{version:1,scope:'installation',ready:true}])
    assert.equal(workspacePmReady({...older,effective_pm_route:value}),false);
});
test('unrecognized reason codes use the generic explanation',()=>{
  for(const code of ['constructor','toString','future_pm_reason',null])assert.equal(pmRouteReasonKey(code),'agents_unknown');
});
