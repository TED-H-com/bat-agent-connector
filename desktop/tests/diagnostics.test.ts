import {test} from 'node:test';
import assert from 'node:assert/strict';
import {buildDiagnostics, pathTemplate, errorRing, scrubDiagnostics, clientIdentity, buildLine} from '../src/diagnostics.js';
import {diagnosticsStrings} from '../src/diagnostics-strings.js';
const at='2026-10-14T12:34:56.123Z';
const fixture=()=>({exportedAt:at,os:'linux',managed:true,locale:'en',width:1440,
 caps:{connector:'0.2.5',api_version:1,contract_version:'2026-10-08',build:{version:'0.2.5',commit:'a'.repeat(40),source:'vcs'},actor:'private-actor',token:'batc_planted_secret',hosts:[{host:'private-box.internal',managed_roots:['/home/example/private'],writes:true}]},
 hosts:[{host:'private-box.internal',connection_state:'connected',stale:false,bat:{status:'ready',version:'1.2.3',protocol:'1',capabilities:{claude:true,token:'batc_planted_secret'}},policy:{writes:true,managed_roots:['C:\\Users\\example\\private']},endpoint:'wss://192.0.2.5',tls_fingerprint:'SHA256:plantedfingerprint',ssh_alias:'private-ssh'}],
 sessions:[{title:'Planted session title',messages:[{text:'Planted message text'}],loaded:true,streaming:false,stale:false,pending:null,reading:{incomplete_reasons:['message_ids_incomplete']}}],
 operations:[{operation_id:'op_'+'a'.repeat(32),action:'session.send',status:'uncertain',actor:'planted@example.invalid',params:{prompt:'Planted prompt'},branch:'private-branch',repo:'private-repository',created_at:at}],
 recentErrors:[{at,method:'GET',path_template:'/v1/sessions/private-box.internal/private-session',status:403,code:'FORBIDDEN'}]});
test('allow-list excludes planted private data and preserves named facts',()=>{
 const out=buildDiagnostics(fixture()),text=JSON.stringify(out);
 for(const value of ['batc_planted_secret','SHA256:plantedfingerprint','private-box.internal','192.0.2.5','/home/example/private','C:\\Users\\example\\private','planted@example.invalid','private-session','Planted session title','Planted message text','Planted prompt','private-actor','private-ssh','private-branch','private-repository'])assert.ok(!text.includes(value),value);
 assert.equal(out.schema,'batc.diagnostics.v1');assert.equal(out.exported_at,at);assert.equal(buildDiagnostics({...fixture(),exportedAt:'2026-10-14T192.0.2.5'}).exported_at,null);assert.equal(out.hosts[0].policy.writes,true);assert.equal(out.hosts[0].policy.managed_roots_configured,true);
 assert.equal(out.sessions.loaded,1);assert.equal(out.sessions.history_reasons.message_ids_incomplete,1);
 assert.equal(out.operations[0].id,'op_'+'a'.repeat(32));assert.equal(out.recent_errors[0].path_template,'/v1/sessions/:host/:session');
});
test('aliases use stable sorted order',()=>{
 const input=fixture();input.hosts.push({...input.hosts[0],host:'alpha',connection_state:'disconnected'});
 assert.deepEqual(buildDiagnostics(input).hosts,buildDiagnostics({...input,hosts:[...input.hosts].reverse()}).hosts);
 assert.equal(buildDiagnostics(input).hosts[0].connection_state,'disconnected');
});
test('ring retains only allowed fields and the last 50 failures',()=>{
 const ring=errorRing();for(let i=0;i<55;i++)ring.record({at,method:'POST',path:'/operations?token=secret',status:500,code:'INTERNAL',operation:{operation_id:'op_'+i.toString(16).padStart(32,'0')},message:'private'});
 assert.equal(ring.entries().length,50);assert.equal(ring.entries()[0].operation_id,'op_'+'5'.padStart(32,'0'));assert.equal(ring.entries()[0].path_template,'/v1/operations');
 assert.ok(!JSON.stringify(ring.entries()).includes('private'));ring.clear();assert.equal(ring.entries().length,0);
});
test('templates remove IDs, queries and unknown segments',()=>{
 for(const [raw,expected] of [
 ['/api/v1/hosts/private/sessions/secret/permissions?token=secret','/v1/hosts/:host/sessions/:session/permissions'],
 ['/sessions/private/secret/messages','/v1/sessions/:host/:session/messages'],
 ['/projects/private/work-items/secret','/v1/projects/:project/work-items/:work_item'],
 ['/pm/workspaces/ws_secret/threads/th_secret','/v1/pm/workspaces/:workspace/threads/:thread'],
 ['/operations/op_secret','/v1/operations/:operation'],['/secret/%2Fhome%2Fexample','/v1/:id/:id']])assert.equal(pathTemplate(raw),expected);
});
test('serialized scrubber removes tokens, fingerprints, URLs, IPs, paths and emails',()=>{
 const planted=['Bearer plain-secret','batc_secret123','github_pat_secret123','ghp_secret123','sk-secret123','SHA256:abcdef','https://private-box.internal/project','192.0.2.5','2001:db8::1','::1','/arbitrary/location/private','C:\\Users\\example\\private','\\\\server\\share\\private','person@example.invalid']; // gitleaks:allow — synthetic redaction fixtures only
 const result=scrubDiagnostics(JSON.stringify({message:planted.join(' ')}));assert.doesNotThrow(()=>JSON.parse(result));
 for(const value of planted)assert.ok(!result.includes(value),value);
});
test('final scrubber rejects every review probe as a whole string value',()=>{
 const probes=['C:\\Users\\Example Person\\AppData\\x.log','/home/Example Person/p','~jane/work','devbox.corp.internal',
  'host.example.com:7777?token=synthetic','wss%3A%2F%2Fhost.example.com%3Ftoken%3Dsynthetic',
  'fp=synthetic','Authorization: Basic c3ludGhldGlj','api_key=synthetic','passwd=synthetic','user@devbox',
  'unrecognized free text','malformed%escape wss%3A%2F%2Fhost.example.com'];
 for(const probe of probes)assert.equal(JSON.parse(scrubDiagnostics(JSON.stringify({probe}))).probe,'[redacted]',probe);
 const out=buildDiagnostics(fixture());
 assert.deepEqual(out.client,clientIdentity(fixture()));
 assert.equal(out.schema,'batc.diagnostics.v1');assert.equal(out.operations[0].action,'session.send');
 assert.equal(out.hosts[0].alias,'host-1');assert.equal(out.recent_errors[0].path_template,'/v1/sessions/:host/:session');
});
test('host versions cannot carry names and protocols come only from the fixed list',()=>{
 for(const value of ['2.1.0-jane-macbook.corp-lan','1.0+ted-devbox','2.1.0\n']){
  const input=fixture();input.hosts[0].bat.version=value;input.hosts[0].bat.protocol=value;
  assert.equal(buildDiagnostics(input).hosts[0].bat.version,null);assert.equal(buildDiagnostics(input).hosts[0].bat.protocol,null);
  const fallback={...input,hosts:[{...input.hosts[0],server_version:value,bat:{...input.hosts[0].bat,version:undefined}}]};
  assert.equal(buildDiagnostics(fallback).hosts[0].bat.version,null);
 }
 for(const value of ['2.1','2.1.0','2.1.0-rc.2','2.1.0-dev12']){
  const input=fixture();input.hosts[0].bat.version=value;assert.equal(buildDiagnostics(input).hosts[0].bat.version,value);
 }
 const input=fixture();input.hosts[0].bat.protocol='1.0';assert.equal(buildDiagnostics(input).hosts[0].bat.protocol,null);
});
test('frontend commits use the same complete hash format as Connector and native',()=>{
 for(const length of [40,64])assert.equal(clientIdentity({caps:{build:{commit:'a'.repeat(length)}}}).connector_commit,'a'.repeat(length));
 for(const length of [7,39,41,63,65])assert.equal(clientIdentity({caps:{build:{commit:'a'.repeat(length)}}}).connector_commit,null);
 assert.equal(clientIdentity({caps:{build:{commit:'A'.repeat(40)}}}).connector_commit,null);
 for(const length of [40,64])assert.equal(clientIdentity({caps:{build:{commit:'a'.repeat(length)+'\n'}}}).connector_commit,null);
});
test('real session history reasons each count once per loaded read',()=>{
 const reading={incomplete_reasons:['archive_unconfirmed','archive_budget','message_ids']};
 const out=buildDiagnostics({sessions:[{loaded:true,reading},{loaded:false,reading:{incomplete_reasons:['message_ids']}}]});
 assert.deepEqual(out.sessions.history_reasons,{archive_unconfirmed:1,archive_budget:1,message_ids:2});
});
test('missing sources and unreported metrics stay unknown; reasons are optional',()=>{
 const out=buildDiagnostics({});assert.equal(out.sessions.loaded,null);assert.equal(out.operations,null);assert.deepEqual(out.hosts,[]);assert.equal(out.sources.hosts,'not_available');
 const locations=buildDiagnostics({os:'windows'}).log_locations;
 assert.equal(locations.basis,'default_templates_only');assert.deepEqual(Object.keys(locations.state_database.managed_desktop),['linux','macos','windows']);
 assert.equal(locations.state_database.managed_desktop.linux,'~/.local/share/io.betteragent.dashboard/central/state/tasks.sqlite3');
 assert.equal(locations.state_database.managed_desktop.windows,'%LOCALAPPDATA%\\io.betteragent.dashboard\\central\\state\\tasks.sqlite3');
 assert.equal(locations.state_database.standalone.macos,'~/.local/state/bat-agent-connector/tasks.sqlite3');
 assert.equal(buildDiagnostics({sessions:[{}]}).sessions.loaded,null);assert.equal(buildDiagnostics({sessions:[{}]}).sessions.history_reasons,null);assert.equal(buildDiagnostics({sessions:[{}]}).sessions.pending,null);assert.equal(buildDiagnostics({sessions:[{stale:false,fields_stale:true}]}).sessions.stale,1);assert.equal(buildDiagnostics({sessions:[{}]}).sessions.unreported.loaded,1);
});
test('identity shows known and unknown commits in both languages',()=>{
 for(const lang of ['en','zh-TW']){
 const t=(key,vars={})=>diagnosticsStrings[lang][key].replace(/\{(\w+)\}/g,(_,k)=>vars[k]??'');
 const browser=clientIdentity({caps:fixture().caps});assert.equal(browser.app_commit,'a'.repeat(40));assert.ok(buildLine(browser,t).includes('aaaaaaa'));
 const desktop=clientIdentity({desktop:true,appBuild:{version:'0.1.1',commit:null},caps:fixture().caps});assert.ok(buildLine(desktop,t).includes(diagnosticsStrings[lang].diag_unknown));assert.ok(buildLine(desktop,t).includes('0.1.1'));
 }
});

test('fixed discovery coverage, protocol and numeric operation times are projected without source identities',()=>{
 const input=fixture();input.caps.hosts[0].profile_id='private-profile';input.hosts[0].bat.protocol='bat-remote/v2';
 input.hosts[0].discovery=[{profile_id:'private-profile',complete_enumeration:true,error_code:'SOURCE_UNAVAILABLE',authority:{capabilities:{git:true},credential_ref:'private-secret-ref'},outside_scan:[{scope:'private-scope',reason:'not_enumerable'}]}];
 input.operations[0].created_at=0;const out=buildDiagnostics(input);
 assert.equal(out.hosts[0].bat.protocol,'bat-remote/v2');assert.equal(out.hosts[0].last_error_code,'SOURCE_UNAVAILABLE');
 assert.deepEqual(out.hosts[0].coverage,{basis:'configured_profile_enumeration',complete:true,reasons:['not_enumerable']});assert.equal(out.operations[0].created_at,'1970-01-01T00:00:00.000Z');
 assert.ok(!JSON.stringify(out).includes('private-profile'));assert.deepEqual(out,buildDiagnostics(input));
});
