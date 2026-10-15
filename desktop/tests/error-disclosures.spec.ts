import {test,expect} from '@playwright/test';
import {diagnosticsFixture} from './diagnostics-fixture.ts';
import {expectErrorDetails,expectNoNestedErrorDetails} from './api-error-assertions.ts';

for(const locale of ['en','zh-TW'])test.describe(`${locale} error disclosures`,()=>{
 test.use({locale});
 test('navigation read failure uses one Details with a ghost export action',async({page})=>{
  const state=await diagnosticsFixture(page);state.projectsStatus=503;
  await page.goto('/dashboard/#/sessions');
  const panel=page.locator('[data-work-read-error]');
  await panel.locator(':scope > details > summary').click();
  await expectErrorDetails(panel,'Fixture project read failed','BAT_ERROR');
  await expect(panel.locator('details')).toHaveCount(1);
  const error=panel.locator('[data-api-error]');await expectNoNestedErrorDetails(error);
  await expect(error.locator('[data-error-summary]')).toHaveCount(1);
  await error.locator('[data-export-diagnostics]').click();
  await expect(page.getByRole('dialog')).toBeVisible();
  expect(state.reads.every(path=>!path.startsWith('/managed/setup'))).toBe(true);
 });
 test('session failure exposes one sentence and one Details',async({page})=>{
  await diagnosticsFixture(page);await page.goto('/dashboard/#/session/private-box.internal/private-session');
  const error=page.locator('[data-session-error]').first();
  await expect(error.locator('.session-error-line')).toHaveCount(1);
  await error.locator(':scope > details > summary').click();
  await expectNoNestedErrorDetails(error);await expect(error.locator('details')).toHaveCount(1);
  await expect(error.locator('[data-export-diagnostics]')).toHaveText(locale==='en'?'Export diagnostics':'匯出診斷資料');
 });
});
