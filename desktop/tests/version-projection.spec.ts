import {test, expect} from '@playwright/test';
import {readFileSync} from 'node:fs';
import {diagnosticsFixture} from './diagnostics-fixture.ts';
const bootstrap=JSON.parse(readFileSync(new URL('../../tests/fixtures/managed-bootstrap.json',import.meta.url),'utf8'));
const runtimes=JSON.parse(readFileSync(new URL('../../tests/fixtures/managed-runtime-identity.json',import.meta.url),'utf8'));
const connectorUpdates=JSON.parse(readFileSync(new URL('../../tests/fixtures/connector-update-facts.json',import.meta.url),'utf8'));
for(const locale of ['en-US','zh-TW'])for(const scenario of ['differs','outside_root','absent'])for(const native of [false,true])
test(`next-launch installation ${scenario} ${native?'desktop':'browser'} ${locale}`,async({browser})=>{
  const context=await browser.newContext({locale}),page=await context.newPage();
  const at=Date.UTC(2026,9,22);
  // A single fixed Date avoids racing two clock calls before navigation.
  await page.clock.setFixedTime(at);
  const producer=structuredClone(bootstrap);
  if(scenario==='absent')delete producer.capabilities.connector_update;
  else Object.assign(producer.capabilities,connectorUpdates[scenario]);
  if(native)delete producer.capabilities.managed_installation;
  const state=await diagnosticsFixture(page,native,true,producer);
  try {
    await page.goto('/dashboard/#/settings?section=about');
    const about=page.locator('#settings-panel-about [data-version-row=connector_installed]');
    await expect(about).toContainText(locale==='en-US'?'Connector installed for the next launch':'供下次啟動的 Connector 安裝');
    const differs=scenario==='differs',en=locale==='en-US';
    const sentence=differs
      ? (en?'A different Connector installation waits for the next launch; updating does not restart the service.':'另一個不同的 Connector 安裝正等待下次啟動；更新不會重新啟動服務。')
      : (en?'The next-launch installation comparison is unknown.':'供下次啟動的安裝比較結果未知。');
    await expect(about).toContainText(differs?(en?'Source: Connector update status':'來源：Connector 更新狀態'):(en?'Source: not reported':'來源：未回報'));
    await expect(about).toContainText(sentence);
    await page.locator('[data-export-diagnostics]').click();
    const summary=page.locator('[data-diagnostics-summary] [data-version-row=connector_installed]');
    await expect(summary).toContainText(sentence);
    const bundle=JSON.parse((await page.locator('[data-diagnostics-json]').textContent())!);
    expect(bundle.versions.rows.find(row=>row.id==='connector_installed').source).toBe(differs?'connector_update':'not_reported');
    expect(bundle.versions.connector_installation_difference).toBe(differs?'differs':'unknown');
    for(const surface of [about,summary]){
      if(differs){
        await expect(surface).toContainText('0.2.8');
        await expect(surface).toContainText('b'.repeat(40));
        await expect(surface).toContainText(en?'Update channel: Preview':'更新頻道：預覽版');
        await expect(surface).toContainText(en?'Last update result: Installed for the next launch':'上次更新結果：已安裝，供下次啟動使用');
        await expect(surface).toContainText('2026-10-22T12:00:00.123456+00:00');
      }else{
        await expect(surface).toContainText(en?'unknown':'未知');
        await expect(surface).toContainText(scenario==='absent'
          ? (en?'This Connector did not report its update status.':'此 Connector 未回報更新狀態。')
          : (en?'This Connector was not identified as running from the updater-managed installation.':'尚未確認此 Connector 是從更新器管理的安裝啟動。'));
      }
    }
    expect(state.reads).toContain('/capabilities');
    expect(state.reads.filter(path=>path.includes('restart')||path.includes('stop'))).toEqual([]);
  } finally {await context.close();}
});
for(const locale of ['en-US','zh-TW'])for(const width of [390,1440])for(const native of [false,true]) {
  test(`one version projection ${native?'desktop':'browser'} ${locale} ${width}`,async({browser},info)=>{
    const context=await browser.newContext({locale,viewport:{width,height:1000}}),page=await context.newPage();
    const at=Date.UTC(2026,9,22);
    await page.clock.install({time:at});
    await page.clock.pauseAt(at);
    const producer=structuredClone(bootstrap);
    // Compatibility variant: an external Connector need not advertise managed
    // installation. Preserve the real producer build/capability fields.
    if(native)delete producer.capabilities.managed_installation;
    const state=await diagnosticsFixture(page,native,true,producer);
    if(native)await page.addInitScript(runtime=>{
      const invoke=(window as any).__TAURI_INTERNALS__.invoke;
      (window as any).__versionCommands=[];
      (window as any).__TAURI_INTERNALS__.invoke=async(command,args)=>{
        if(['managed_control','desktop_update'].includes(command))(window as any).__versionCommands.push({command,input:args.input});
        if(command==='native_status')return {...await invoke(command,args),updates:true};
        if(command==='desktop_update')return {current_version:'0.1.1',available:false,phase:'idle',candidate:null,
          code:'UPDATE_SIGNING_NOT_CONFIGURED',installation:null};
        if(command==='managed_control')return {mode:'managed',ready:true,background:true,runtime_version:runtime.runtime_version,
          server_id:runtime.server_id,principal_id:runtime.principal_id,error:null,login_enabled:false,lifecycle:{state:'running',requested_at:null,stopped_at:null},
          bundled_runtime:runtime.bundled_runtime,running_runtime:runtime.running_runtime,runtime_difference:runtime.runtime_difference};
        if(command==='fleet_availability')return {configured:false,platform_supported:false};
        return invoke(command,args);
      };
    },runtimes.differs);
    try {
      await page.goto('/dashboard/#/settings?section=about');
      await expect(page.locator('[data-build-line]')).toBeVisible();
      if(native)await expect(page.locator('#settings-tab-updates')).toBeVisible();
      await page.screenshot({path:info.outputPath('about.png'),fullPage:true});
      const about=page.locator('#settings-panel-about [data-version-facts]');
      expect.soft(await about.count()).toBe(1);
      if(await about.count()) {
      if(native){
        await expect.soft(about.locator('[data-version-row=bundled_runtime]')).toContainText(runtimes.differs.bundled_runtime.commit);
        await expect.soft(about.locator('[data-version-row=running_runtime]')).toContainText(runtimes.differs.running_runtime.commit);
        await expect.soft(about).toContainText(locale==='en-US'?'Launch observation':'啟動時的觀察');
        await expect.soft(about).toContainText(locale==='en-US'?'differ':'不同');
      }else await expect.soft(about.locator('[data-version-row=running_runtime]')).toContainText(locale==='en-US'?'unknown':'未知');
      await expect.soft(about.locator('[data-version-row=adapter_observed]')).toContainText(locale==='en-US'?'unknown':'未知');
      }
      expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
      // A later real capabilities-shaped response may identify a different
      // answering build. It must update every surface, without rewriting either
      // launch receipt. Values come from the managed producer golden.
      const answering=runtimes.differs.running_runtime;
      state.producer.bootstrap.capabilities.build={version:answering.version,commit:answering.commit,source:answering.build_source};
      // Re-enter Settings so the app reads the changed capabilities before export.
      await page.locator('#live').click();
      await page.locator('#settings-tab-about').click();
      const connector=about.locator('[data-version-row=connector]');
      await expect(connector).toBeVisible();
      await expect(connector).toContainText(answering.commit);
      // tickHealth verifies the change through /bootstrap. Its completed read
      // advances this visible timestamp; keep the clock paused after that barrier
      // so activation cannot withdraw the preview during the assertions below.
      await page.clock.runFor(1000);
      await expect(connector).toContainText(new Date(at+1000).toISOString());
      await page.locator('[data-export-diagnostics]').click();
      await expect(page.locator('[data-diagnostics-json]')).toContainText('batc.diagnostics.v1');
      const bundle=JSON.parse((await page.locator('[data-diagnostics-json]').textContent())!);
      expect.soft(bundle.versions?.rows).toBeDefined();
      if(bundle.versions){
        expect(bundle.versions.rows.find(row=>row.id==='connector').commit).toBe(answering.commit);
        for(const row of bundle.versions.rows){
          const rendered=page.locator(`[data-diagnostics-summary] [data-version-row=${row.id}]`);
          if(!native&&['desktop','desktop_update','desktop_released'].includes(row.id)){await expect(rendered).toHaveCount(0);continue;}
          for(const value of [row.version,row.commit,row.digest,row.observed_at,row.recorded_at,row.installed_at].filter(Boolean)){
            await expect(rendered).toContainText(value);
            await expect(about.locator(`[data-version-row=${row.id}]`)).toContainText(value);
          }
          if(!row.version&&!row.commit&&!row.digest)await expect(rendered).toContainText(locale==='en-US'?'unknown':'未知');
        }
      }
      await page.screenshot({path:info.outputPath('diagnostics.png'),fullPage:true});
      expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
      if(native){
        await page.getByRole('dialog').getByRole('button',{name:locale==='en-US'?'Close':'關閉',exact:true}).click();
        await page.locator('#settings-tab-updates').click();
        const updates=page.locator('#settings-panel-updates [data-version-facts]');
        expect.soft(await updates.count()).toBe(1);
        if(bundle.versions)for(const row of bundle.versions.rows){
          for(const value of [row.version,row.commit,row.digest].filter(Boolean))
            await expect(updates.locator(`[data-version-row=${row.id}]`)).toContainText(value);
        }
        await page.screenshot({path:info.outputPath('updates.png'),fullPage:true});
        expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
        expect(await page.evaluate(()=>(window as any).__versionCommands.every(call=>call.input.action==='status'))).toBe(true);
      }
      expect(state.reads.filter(path=>path.includes('restart')||path.includes('stop'))).toEqual([]);
    }finally{await context.close();}
  });
}


for(const locale of ['en-US','zh-TW'])test(`released Connector and unknown MCP agree across screen and export ${locale}`,async({browser})=>{
  const context=await browser.newContext({locale}),page=await context.newPage();
  await page.clock.setFixedTime(Date.UTC(2026,9,23));
  const producer=structuredClone(bootstrap);
  Object.assign(producer.capabilities,connectorUpdates.released);
  const state=await diagnosticsFixture(page,false,true,producer);
  try {
    await page.goto('/dashboard/#/settings?section=about');
    const about=page.locator('#settings-panel-about');
    const released=about.locator('[data-version-row=connector_released]');
    const en=locale==='en-US';
    await expect(released).toContainText(en?'Released Connector':'已發布的 Connector');
    for(const value of ['0.2.9','c'.repeat(40),'2026-10-22T13:00:00.123456+00:00','2026-10-17T00:00:00Z'])await expect(released).toContainText(value);
    await expect(about.locator('[data-version-row=connector_installed]')).toContainText('0.2.8');
    await expect(about.locator('[data-version-row=connector]')).toContainText('0.2.7');
    const mcp=about.locator('[data-version-row=mcp]');
    await expect(mcp).toContainText(en?'not observed by Dashboard':'Dashboard 尚無觀察資料');
    await expect(mcp).toContainText(en?'Source: not reported':'來源：未回報');
    await page.locator('[data-export-diagnostics]').click();
    const summary=page.locator('[data-diagnostics-summary]');
    await expect(page.locator('[data-diagnostics-json]')).toContainText('batc.diagnostics.v1');
    const bundle=JSON.parse((await page.locator('[data-diagnostics-json]').textContent())!);
    for(const id of ['connector_released','connector_installed','connector','mcp']){
      const fact=bundle.versions.rows.find(row=>row.id===id);
      for(const value of [fact.version,fact.commit,fact.observed_at,fact.published_at,fact.installed_at].filter(Boolean)) {
        await expect(about.locator(`[data-version-row=${id}]`)).toContainText(value);
        await expect(summary.locator(`[data-version-row=${id}]`)).toContainText(value);
      }
    }
    const facts=Object.fromEntries(bundle.versions.rows.map(row=>[row.id,row]));
    expect(new Set([facts.connector_released.observed_at,facts.connector_installed.installed_at,facts.connector.observed_at]).size).toBe(3);
    expect(facts.mcp.source).toBe('not_reported');
    expect(state.reads.some(path=>path.includes('identity')||path.includes('mcp'))).toBe(false);
  }finally{await context.close();}
});


for(const locale of ['en-US','zh-TW'])test(`publication display failure preserves next launch and stays unknown in export ${locale}`,async({browser})=>{
  const context=await browser.newContext({locale}),page=await context.newPage();
  const producer=structuredClone(bootstrap);
  Object.assign(producer.capabilities,connectorUpdates.publication_unknown);
  await diagnosticsFixture(page,false,true,producer);
  try{
    await page.goto('/dashboard/#/settings?section=about');
    const about=page.locator('#settings-panel-about');
    const released=about.locator('[data-version-row=connector_released]');
    await expect(released).toContainText(locale==='en-US'?'unknown':'未知');
    await expect(released).toContainText(locale==='en-US'?'Source: not reported':'來源：未回報');
    await expect(about.locator('[data-version-row=connector_installed]')).toContainText('0.2.9');
    await expect(about.locator('[data-version-row=connector]')).toContainText('0.2.7');
    await page.locator('[data-export-diagnostics]').click();
    await expect(page.locator('[data-diagnostics-json]')).toContainText('batc.diagnostics.v1');
    const bundle=JSON.parse((await page.locator('[data-diagnostics-json]').textContent())!);
    const fact=bundle.versions.rows.find(row=>row.id==='connector_released');
    expect(fact).toMatchObject({version:null,commit:null,published_at:null,observed_at:null,source:'not_reported'});
    await expect(page.locator('[data-diagnostics-summary] [data-version-row=connector_released]')).toContainText(locale==='en-US'?'unknown':'未知');
    expect(bundle.versions.rows.find(row=>row.id==='connector_installed').version).toBe('0.2.9');
  }finally{await context.close();}
});
