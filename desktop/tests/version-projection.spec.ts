import {test, expect} from '@playwright/test';
import {readFileSync} from 'node:fs';
import {diagnosticsFixture} from './diagnostics-fixture.ts';
const bootstrap=JSON.parse(readFileSync(new URL('../../tests/fixtures/managed-bootstrap.json',import.meta.url),'utf8'));
const runtimes=JSON.parse(readFileSync(new URL('../../tests/fixtures/managed-runtime-identity.json',import.meta.url),'utf8'));
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
          if(!native&&['desktop','desktop_update'].includes(row.id)){await expect(rendered).toHaveCount(0);continue;}
          for(const value of [row.version,row.commit,row.digest,row.observed_at,row.recorded_at].filter(Boolean)){
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
