import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {buildDiagnostics, buildLine} from '../src/diagnostics.js';
import {diagnosticsStrings} from '../src/diagnostics-strings.js';
import {versionFactText} from '../src/version-facts.js';
import {frontendBuild} from '../src/frontend-identity.js';
const golden = name => JSON.parse(readFileSync(new URL(`../../tests/fixtures/${name}.json`, import.meta.url), 'utf8'));
const bootstrap = golden('managed-bootstrap'), runtime = golden('managed-runtime-identity');
const adapter = golden('pm-version-facts');
const at = '2026-10-20T12:00:00.000Z';
const input = name => ({caps:bootstrap.capabilities, managedRuntime:runtime[name], desktop:true,
  capabilitiesReadAt:at, managedReadAt:at, online:true, exportedAt:at});
const row = (bundle, id) => bundle.versions?.rows.find(value => value.id === id);
test('UI compatibility fields and rows share the producer digest and their own clocks', () => {
  const loadedAt='2026-10-20T11:00:00.000Z';
  const digest=bootstrap.capabilities.frontend.digest;
  const value={...input('differs'),desktop:false,frontendBuild:{frontend_digest:digest},frontendLoadedAt:loadedAt};
  const bundle=buildDiagnostics(value);
  assert.equal(row(bundle,'loaded_ui').digest,digest);
  assert.equal(row(bundle,'loaded_ui').source,'loaded_ui');
  assert.equal(row(bundle,'loaded_ui').observed_at,loadedAt);
  assert.equal(row(bundle,'served_ui').digest,digest);
  assert.equal(row(bundle,'served_ui').source,'capabilities');
  assert.equal(row(bundle,'served_ui').observed_at,at);
  assert.equal(bundle.client.frontend_digest,digest);
  assert.equal(bundle.client.served_frontend_digest,digest);
  const offline=buildDiagnostics({...value,online:false});
  assert.equal(row(offline,'loaded_ui').digest,digest);
  assert.equal(offline.client.frontend_digest,digest);
  assert.equal(row(offline,'served_ui').digest,null);
  assert.equal(offline.client.served_frontend_digest,null);
});
test('default loaded UI is the module snapshot, never the capabilities digest', () => {
  const bundle=buildDiagnostics(input('differs'));
  assert.equal(bundle.client.frontend_digest,frontendBuild.frontend_digest);
  assert.equal(row(bundle,'loaded_ui').digest,frontendBuild.frontend_digest);
  assert.equal(bundle.client.served_frontend_digest,bootstrap.capabilities.frontend.digest);
});
for (const name of Object.keys(runtime)) test(`real managed producer keeps independent version observations: ${name}`, () => {
  const bundle = buildDiagnostics(input(name));
  for (const [id, field] of [['bundled_runtime','bundled_runtime'], ['running_runtime','running_runtime']]) {
    assert.equal(row(bundle,id)?.version, runtime[name][field].version);
    assert.equal(row(bundle,id)?.commit, runtime[name][field].commit);
    assert.equal(row(bundle,id)?.observed_at, new Date(runtime[name][field].observed_at*1000).toISOString());
    assert.equal(row(bundle,id)?.period, 'launch');
  }
  assert.equal(bundle.versions.runtime_difference, runtime[name].runtime_difference);
  assert.equal(row(bundle,'connector').version, bootstrap.capabilities.build.version);
  assert.equal(row(bundle,'connector').source, 'capabilities');
  assert.equal(row(bundle,'connector').observed_at, at);
  assert.equal(row(bundle,'desktop').version, null); // never borrow runtime_version
});
test('offline and older service never borrow an installed or cached build', () => {
  const older = input('older_service');
  older.caps = {...bootstrap.capabilities}; delete older.caps.build;
  assert.equal(row(buildDiagnostics(older),'connector')?.commit, null);
  // A caller can still hold the previous successful capabilities while offline.
  const offline=buildDiagnostics({...input('differs'),online:false});
  assert.equal(row(offline,'connector')?.version, null);
  assert.equal(row(offline,'served_ui')?.digest, null);
  assert.equal(row(offline,'running_runtime')?.period, 'launch');
  assert.equal(offline.versions.runtime_difference, 'differs');
});
test('browser export cannot identify a Desktop app by borrowing the Connector build', () => {
  const out=buildDiagnostics({...input('differs'),desktop:false});
  assert.equal(out.client.app_version,null);assert.equal(out.client.app_commit,null);
  assert.equal(row(out,'desktop').version,null);
  assert.equal(out.client.connector_version,bootstrap.capabilities.build.version);
});
test('unread rows do not claim that a load, launch or successful read occurred', () => {
  for(const row of buildDiagnostics({}).versions.rows){
    assert.equal(row.source,'not_reported');assert.equal(row.period,'unknown');
    assert.equal(row.observed_at,null);
  }
});
for(const locale of ['en','zh-TW']) test(`projection renders the same build line and facts in ${locale}`, () => {
  const bundle=buildDiagnostics(input('differs'));
  const t=(key,vars={})=>diagnosticsStrings[locale][key]?.replace(/\{(\w+)\}/g,(_,k)=>vars[k]??'') ?? key;
  assert.ok(bundle.versions, 'About, summary, export and Updates need the same projection');
  assert.ok(buildLine(bundle.client,t).includes(bootstrap.capabilities.build.version));
  assert.equal(buildLine(bundle.versions,t),buildLine(bundle.client,t));
  for(const value of bundle.versions.rows) {
    assert.ok(Object.hasOwn(value,'source'));assert.ok(Object.hasOwn(value,'observed_at'));
    for(const key of ['diag_version_'+value.id,'diag_version_source_'+value.source,'diag_version_period_'+value.period])
      assert.equal(typeof diagnosticsStrings[locale][key], 'string', key);
    for(const fact of [value.version,value.commit,value.digest].filter(Boolean))assert.ok(versionFactText(value,t).includes(fact));
  }
});
for(const locale of ['en','zh-TW']) test(`a known runtime version cannot hide its unknown commit in ${locale}`, () => {
  const bundle=buildDiagnostics(input('unknown_running'));
  const t=(key,vars={})=>diagnosticsStrings[locale][key]?.replace(/\{(\w+)\}/g,(_,k)=>vars[k]??'') ?? key;
  const text=versionFactText(row(bundle,'running_runtime'),t);
  assert.ok(text.includes(runtime.unknown_running.running_runtime.version));
  assert.ok(text.includes(diagnosticsStrings[locale].diag_unknown));
});
test('real adapter installation producer supplies a requirement, never an observed version', () => {
  const bundle=buildDiagnostics({...input('differs'),adapterRead:{document:adapter.native.installation,at}});
  assert.equal(row(bundle,'adapter_required').version,adapter.native.installation.native_analysis.required_version);
  assert.equal(row(bundle,'adapter_required').observed_at,at);
  assert.equal(row(bundle,'adapter_observed').version,null);
  assert.equal(row(bundle,'adapter_observed').observed_at,null);
  // The real producer rejects this older executable without returning its
  // numeric version. Keep that coverage gap explicit instead of inventing one.
  assert.equal(adapter.native.older_executable_error,'PM_NATIVE_VERSION_UNSUPPORTED');
});
test('a read of immutable BAT run provenance stays a past run with its own clock', () => {
  const bundle=buildDiagnostics({...input('differs'),adapterRunRead:{run:adapter.bat_run,at}});
  const observed=row(bundle,'adapter_bat_observed');
  assert.equal(observed.commit,adapter.bat_run.bat_runtime_provenance.codex_source_revision);
  assert.equal(observed.version,null);assert.equal(observed.period,'run');
  assert.equal(observed.recorded_at,new Date(adapter.bat_run.updated_at*1000).toISOString());
  assert.equal(observed.observed_at,at); // the client read, not a new run
  assert.notEqual(observed.recorded_at,at);
  assert.equal(row(bundle,'adapter_observed').version,null);
});
test('a projection never exports extra fields from producer envelopes', () => {
  const source=input('differs');
  source.managedRuntime=structuredClone(source.managedRuntime);
  source.managedRuntime.running_runtime.token='secret';
  const out=buildDiagnostics(source);
  for(const raw of [source.managedRuntime.endpoint,source.managedRuntime.token,source.managedRuntime.actor,'secret'])
    assert.ok(!JSON.stringify(out.versions).includes(raw));
});
test('all version strings have both locales and matching placeholders', () => {
  for(const key of Object.keys(diagnosticsStrings.en).filter(key=>/^diag_(?:version|runtime|adapter)/.test(key))) {
    assert.equal(typeof diagnosticsStrings['zh-TW'][key],'string',key);
    assert.deepEqual(diagnosticsStrings.en[key].match(/\{\w+\}/g),diagnosticsStrings['zh-TW'][key].match(/\{\w+\}/g),key);
  }
});
