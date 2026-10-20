import {test, expect} from '@playwright/test';
import {readFileSync} from 'node:fs';
import {diagnosticsFixture} from './diagnostics-fixture.ts';
const bootstrap=JSON.parse(readFileSync(new URL('../../tests/fixtures/managed-bootstrap.json',import.meta.url),'utf8'));
const runtimes=JSON.parse(readFileSync(new URL('../../tests/fixtures/managed-runtime-identity.json',import.meta.url),'utf8'));
for(const locale of ['en-US','zh-TW'])for(const width of [390,1440])for(const native of [false,true]) {
  test(`one version projection ${native?'desktop':'browser'} ${locale} ${width}`,async({browser},info)=>{
    const context=await browser.newContext({locale,viewport:{width,height:1000}}),page=await context.newPage();
    const state=await diagnosticsFixture(page,native,true,structuredClone(bootstrap));
    if(native)await page.addInitScript(runtime=>{
      const invoke=(window as any).__TAURI_INTERNALS__.invoke;
      (window as any).__TAURI_INTERNALS__.invoke=async(command,args)=>{
        if(command==='managed_control')return {mode:'managed',ready:true,background:true,runtime_version:runtime.runtime_version,
          server_id:runtime.server_id,principal_id:runtime.principal_id,error:null,login_enabled:false,
          bundled_runtime:runtime.bundled_runtime,running_runtime:runtime.running_runtime,runtime_difference:runtime.runtime_difference};
        if(command==='fleet_availability')return {configured:false,platform_supported:false};
        return invoke(command,args);
      };
    },runtimes.differs);
    try {
      await page.goto('/dashboard/#/settings?section=about');
      await expect(page.locator('[data-build-line]')).toBeVisible();
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
      await page.locator('[data-export-diagnostics]').click();
      await expect(page.locator('[data-diagnostics-json]')).toContainText('batc.diagnostics.v1');
      const bundle=JSON.parse((await page.locator('[data-diagnostics-json]').textContent())!);
      expect.soft(bundle.versions?.rows).toBeDefined();
      if(bundle.versions){
        for(const row of bundle.versions.rows){
          const rendered=page.locator(`[data-diagnostics-summary] [data-version-row=${row.id}]`);
          for(const value of [row.version,row.commit,row.digest,row.observed_at].filter(Boolean))await expect(rendered).toContainText(value);
          if(!row.version&&!row.commit&&!row.digest)await expect(rendered).toContainText(locale==='en-US'?'unknown':'未知');
        }
      }
      await page.screenshot({path:info.outputPath('diagnostics.png'),fullPage:true});
      expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
      expect(state.reads.filter(path=>path.includes('restart')||path.includes('stop'))).toEqual([]);
    }finally{await context.close();}
  });
}
