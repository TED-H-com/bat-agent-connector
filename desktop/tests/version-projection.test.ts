import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {buildDiagnostics, buildLine} from '../src/diagnostics.js';
import {diagnosticsStrings} from '../src/diagnostics-strings.js';
import {versionFactText, versionFacts} from '../src/version-facts.js';
import {frontendBuild} from '../src/frontend-identity.js';
import {time, versionProjection, versionRow} from '../src/version-projection.js';
const bootstrap = JSON.parse(readFileSync(new URL('../../tests/fixtures/managed-bootstrap.json', import.meta.url), 'utf8'));
const runtime = JSON.parse(readFileSync(new URL('../../tests/fixtures/managed-runtime-identity.json', import.meta.url), 'utf8'));
const adapter = JSON.parse(readFileSync(new URL('../../tests/fixtures/pm-version-facts.json', import.meta.url), 'utf8'));
const connectorUpdates = JSON.parse(readFileSync(new URL('../../tests/fixtures/connector-update-facts.json', import.meta.url), 'utf8'));
const desktopCandidate = JSON.parse(readFileSync(new URL('../../tests/fixtures/desktop-update-candidate.json', import.meta.url), 'utf8'));
const at = '2026-10-20T12:00:00.000Z';
const input = name => ({caps:bootstrap.capabilities, managedRuntime:runtime[name], desktop:true,
  capabilitiesReadAt:at, managedReadAt:at, online:true, exportedAt:at});
const row = (bundle, id) => bundle.versions?.rows.find(value => value.id === id);
test('updater datetime.isoformat timestamps retain their microseconds',()=>{
  assert.equal(time('2026-10-22T12:00:00.123456+00:00'),'2026-10-22T12:00:00.123456+00:00');
});
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
test('an older Connector leaves the next-launch installation explicitly unknown', () => {
  const caps=structuredClone(bootstrap.capabilities);delete caps.connector_update;
  const bundle=buildDiagnostics({...input('differs'),caps});
  assert.deepEqual(row(bundle,'connector_installed'),{
    id:'connector_installed',version:null,commit:null,digest:null,source:'not_reported',
    observed_at:null,period:'unknown',build_source:null,state:'not_reported',code:null,
    enabled:null,channel:null,pending:null,last:null,installed_at:null,
  });
  assert.equal(bundle.versions.connector_installation_difference,'unknown');
});
for(const [name,caps] of Object.entries(connectorUpdates) as [string,any][])test(`real updater capability projection: ${name}`,()=>{
  const value={...input('differs'),caps};
  const bundle=buildDiagnostics(value),installed=row(bundle,'connector_installed');
  const reported=caps.connector_update.state==='reported';
  assert.equal(installed.source,reported?'connector_update':'not_reported');
  assert.equal(installed.period,reported?'next_launch':'unknown');
  assert.equal(installed.observed_at,reported?at:null);
  assert.equal(installed.version,reported?caps.connector_update.installed.version:null);
  assert.equal(installed.commit,reported?caps.connector_update.installed.commit:null);
  assert.equal(bundle.versions.connector_installation_difference,reported?(['differs','rollback_differs','released','publication_unknown'].includes(name)?'differs':'same'):'unknown');
  assert.equal(installed.channel,reported?'preview':null);
  assert.equal(installed.last?.status??null,reported?caps.connector_update.last.status:null);
  assert.equal(installed.last?.at??null,reported?'2026-10-22T12:00:00.123456+00:00':null);
  const without={...caps};delete without.connector_update;
  assert.equal(JSON.stringify(bundle.client),JSON.stringify(buildDiagnostics({...value,caps:without}).client));
  assert.equal(row(buildDiagnostics({...value,online:false}),'connector_installed').source,'not_reported');
});
for(const locale of ['en','zh-TW'])for(const desktop of [false,true])test(`literal next-launch details ${locale} ${desktop}`,()=>{
  const t=(key,vars={})=>diagnosticsStrings[locale][key]?.replace(/\{(\w+)\}/g,(_,k)=>vars[k]??'')??key;
  const render=name=>versionFacts({h:(_tag,_props,...children)=>children.flat(Infinity).filter(value=>value!==null).join(' '),t,
    projection:buildDiagnostics({...input('differs'),desktop,caps:connectorUpdates[name]}).versions});
  const expected=locale==='en'?[
    'Connector installed for the next launch','Source: Connector update status','Installed for the next launch',
    'Version: 0.2.8','Update channel: Preview','Last update result: Installed for the next launch · 2026-10-22T12:00:00.123456+00:00',
    'A different Connector installation waits for the next launch; updating does not restart the service.',
  ]:[
    '供下次啟動的 Connector 安裝','來源：Connector 更新狀態','已安裝，供下次啟動使用',
    '版本：0.2.8','更新頻道：預覽版','上次更新結果：已安裝，供下次啟動使用 · 2026-10-22T12:00:00.123456+00:00',
    '另一個不同的 Connector 安裝正等待下次啟動；更新不會重新啟動服務。',
  ];
  for(const text of expected)assert.ok(render('differs').includes(text),text);
  assert.ok(render('failed').includes(locale==='en'?'Last update result: Failed':'上次更新結果：失敗'));
  assert.ok(render('failed').includes('fixture_install_failed'));
  assert.ok(render('same').includes(locale==='en'?'The next-launch installation matches the answering Connector.':'供下次啟動的安裝與回應中的 Connector 相同。'));
  assert.ok(render('not_enrolled').includes(locale==='en'?'Connector automatic updates are not enrolled.':'Connector 尚未登記自動更新。'));
  assert.ok(render('outside_root').includes(locale==='en'?'This Connector was not identified as running from the updater-managed installation.':'尚未確認此 Connector 是從更新器管理的安裝啟動。'));
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


test('released slots and the unobserved MCP process are explicitly unknown by default',()=>{
  const bundle=buildDiagnostics(input('differs'));
  for(const id of ['desktop_released','connector_released','mcp']) {
    assert.ok(row(bundle,id),id);
    assert.equal(row(bundle,id).version,null);
    assert.equal(row(bundle,id).observed_at,null);
    assert.equal(row(bundle,id).source,'not_reported');
  }
});


test('released, installed and answering Connector facts retain their independent values and clocks',()=>{
  const caps=connectorUpdates.released;
  const bundle=buildDiagnostics({...input('differs'),caps});
  const released=row(bundle,'connector_released'),installed=row(bundle,'connector_installed'),answering=row(bundle,'connector');
  assert.equal(released.version,'0.2.9');assert.equal(released.commit,'c'.repeat(40));
  assert.equal(installed.version,'0.2.8');assert.equal(answering.version,'0.2.7');
  assert.equal(released.observed_at,caps.connector_update.release_check.completed_at);
  assert.equal(released.published_at,caps.connector_update.release_check.candidate.published_at);
  assert.equal(installed.installed_at,caps.connector_update.last.at);
  assert.equal(answering.observed_at,at);
  assert.equal(new Set([released.observed_at,installed.installed_at,answering.observed_at]).size,3);
  const later=row(buildDiagnostics({...input('differs'),caps,capabilitiesReadAt:'2026-10-23T00:00:00Z'}),'connector_released');
  assert.deepEqual(later,released);
  for(const caps of [connectorUpdates.same,connectorUpdates.failed,connectorUpdates.outside_root]) {
    assert.equal(row(buildDiagnostics({...input('differs'),caps}),'connector_released').source,'not_reported');
  }
  assert.equal(row(buildDiagnostics({...input('differs'),caps,online:false}),'connector_released').version,null);
});
for(const locale of ['en','zh-TW'])test(`released Connector facts agree with diagnostics in ${locale}`,()=>{
  const projection=buildDiagnostics({...input('differs'),caps:connectorUpdates.released}).versions;
  const t=(key,vars={})=>diagnosticsStrings[locale][key]?.replace(/\{(\w+)\}/g,(_,k)=>vars[k]??'')??key;
  const text=versionFacts({h:(_tag,_props,...children)=>children.flat(Infinity).filter(value=>value!==null).join(' '),t,projection});
  for(const value of ['0.2.9','c'.repeat(40),'2026-10-22T13:00:00.123456+00:00','2026-10-17T00:00:00Z'])assert.ok(text.includes(value),value);
  assert.ok(text.includes(locale==='en'?'Released Connector':'已發布的 Connector'));
  assert.ok(text.includes(locale==='en'?'Release check completed at':'發布檢查完成時間'));
});


test('native completed candidate stays independent of status reads and incomplete or failed reads',()=>{
  const value={...input('differs'),updater:{candidate:desktopCandidate,current_version:'0.1.0'},updaterReadAt:at};
  const first=row(buildDiagnostics(value),'desktop_released');
  assert.equal(first.version,desktopCandidate.version);assert.equal(first.commit,desktopCandidate.source_sha);
  assert.equal(first.observed_at,time(desktopCandidate.checked_at));
  assert.equal(row(buildDiagnostics(value),'desktop_update').version,'0.1.0');
  assert.deepEqual(row(buildDiagnostics({...value,updaterReadAt:'2026-10-23T00:00:00Z'}),'desktop_released'),first);
  for(const candidate of [null,{...desktopCandidate,checked_at:undefined}]) {
    const unknown=row(buildDiagnostics({...value,updater:{...value.updater,candidate}}),'desktop_released');
    assert.equal(unknown.version,null);assert.equal(unknown.observed_at,null);assert.equal(unknown.source,'not_reported');
  }
  assert.equal(row(buildDiagnostics({...value,updaterReadFailed:true}),'desktop_released').source,'not_reported');
  assert.deepEqual(row(buildDiagnostics({...value,updater:{...value.updater,phase:'check_failed'}}),'desktop_released'),first);
});


for(const locale of ['en','zh-TW'])test(`malformed publication time keeps installation and leaves release unknown in ${locale}`,()=>{
  const value={...input('differs'),caps:connectorUpdates.publication_unknown};
  const projection=versionProjection(value),bundle=buildDiagnostics(value);
  const released=versionRow(projection,'connector_released');
  assert.deepEqual(released,row(bundle,'connector_released'));
  for(const key of ['version','commit','observed_at','published_at'])assert.equal(released[key],null,key);
  assert.equal(released.source,'not_reported');
  assert.equal(versionRow(projection,'connector_installed').version,'0.2.9');
  assert.equal(versionRow(projection,'connector').version,'0.2.7');
  const t=(key,vars={})=>diagnosticsStrings[locale][key]?.replace(/\{(\w+)\}/g,(_,k)=>vars[k]??'')??key;
  assert.ok(versionFactText(released,t).includes(t('diag_unknown')));
  const screen=versionFacts({h:(_tag,_props,...children)=>children.flat(Infinity).filter(value=>value!==null).join(' '),t,projection});
  assert.ok(screen.includes(locale==='en'?'Released Connector':'已發布的 Connector'));
  assert.ok(!screen.includes(locale==='en'?'Release published at':'發布時間：'));
});
