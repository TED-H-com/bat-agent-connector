// Synthetic setup evidence only. Run against the generated browser build.
import {chromium, expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {managedSetupFixture} from './managed-setup-fixture.ts';
const stage = process.argv[2] || 'after', output = process.env.BATC_SETUP_SHOTS;
if (!output) throw Error('Set BATC_SETUP_SHOTS to a disk evidence directory');
await mkdir(output, {recursive:true});
const browser = await chromium.launch();
try {
  for (const locale of ['en','zh-TW']) for (const width of (stage === 'before' ? [1440,390] : [1440,768,390])) {
    const context = await browser.newContext({locale, viewport:{width,height:width===390?844:900}}), page = await context.newPage();
    const state = await managedSetupFixture(page,false,locale), errors=[];
    page.on('pageerror', error=>errors.push(error.message));
    await page.goto(`http://127.0.0.1:${process.env.BATC_UI_TEST_PORT}/dashboard/`);
    const panel=page.locator('[data-managed-setup]'), form=page.locator('[data-setup-host]'); await expect(form).toBeVisible();
    const shot = async name => {await page.evaluate(()=>scrollTo(0,0)); await page.screenshot({path:`${output}/${stage}-${name}-${locale}-${width}.png`,fullPage:true});};
    await shot('first-run');
    if (stage==='before') {
      await form.getByLabel(locale==='en'?'BAT connection profile':'BAT 連線 profile',{exact:true}).selectOption('profile-demo');
      await form.locator('input').first().fill('local');
      await form.locator('input[type=password]').fill('synthetic-bat-secret');
    } else await form.locator('[data-setup-field=connection_url]').fill(`ws://127.0.0.1?token=synthetic-bat-secret&fp=${'a'.repeat(64)}`);
    await shot('url-filled');
    state.refused=true;
    if(stage==='after') {state.refusalCode='INVALID_TARGET';state.admissionRefused=true;state.refusalStatus=422;}
    await form.locator('button[type=submit]').click(); await expect.poll(()=>state.posts.length).toBe(1); await shot('refusal');
    state.refused=false;
    if(stage==='before') await panel.locator('.actions').first().locator('button').last().click();
    if(stage==='after') await form.locator('[data-setup-field=host]').fill('local');
    await form.locator('button[type=submit]').click(); await expect(panel.locator('a[href^="#/op/"]')).toBeVisible();
    await shot('connected'); await shot('settings');
    if(stage==='after' && width===1440 && locale==='en') {await page.emulateMedia({colorScheme:'dark'});await shot('settings-dark');}
    if (await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)) throw Error('Horizontal overflow');
    if(errors.length)throw Error('Page errors: '+errors.join('; '));
    await context.close();
  }
} finally {await browser.close();}
