import {test, expect, type Page} from '@playwright/test';
import {managedSetupFixture, openSetupAdvanced, applySetupReview} from './managed-setup-fixture.ts';

async function existingForm(page: Page, locale: string, profile = 'default') {
  const state = await managedSetupFixture(page, false, locale); state.saved = true;
  state.configuredProfile = profile;
  await page.goto('/dashboard/#/settings'); await openSetupAdvanced(page);
  await page.locator('[data-setup-connect] > summary').click();
  await page.locator('[data-setup-manual] > summary').click();
  await page.locator('[data-setup-field=host]').fill('demo');
  await page.locator('[data-setup-field=url]').fill(state.configuredUrl);
  await page.locator('[data-setup-field=fingerprint]').fill('A'.repeat(64));
  return state;
}

for (const locale of ['en', 'zh-TW']) test.describe(`${locale} existing connection`, () => {
  const review = locale === 'en' ? 'Review connection' : '預覽連線';
  const credential = locale === 'en' ? 'To choose another BAT profile for this connection, paste its BAT connection token, then find profiles and workspaces.'
    : '若要為此連線選擇其他 BAT 設定檔，請貼上 BAT 連線權杖，再尋找設定檔與工作區。';
  test('reviews and applies existing credentials with zero discovery requests', async ({page}) => {
    const state = await existingForm(page, locale);
    await page.locator('[data-setup-field=managed_roots]').locator('xpath=ancestor::details[1]/summary').click();
    await page.locator('[data-setup-field=managed_roots]').fill('/srv/new-managed');
    const button = page.locator('[data-setup-host]').getByRole('button', {name: review, exact: true});
    await expect(button).toBeEnabled(); await button.click();
    await expect(page.locator('[data-setup-review]')).toBeVisible();
    expect(state.previews[0].body.params).not.toHaveProperty('secret_ref');
    expect(state.previews[0].body.params).not.toHaveProperty('source_digest');
    await applySetupReview(page);
    expect(state.discoveries).toEqual([]); expect(state.staged).toEqual([]); expect(state.posts).toHaveLength(1);
  });
  test('a changed profile draft requires a pasted token and explains the disabled action', async ({page}) => {
    const state = await existingForm(page, locale);
    state.discoveryProfiles = [{id:'default',name:'Default'}, {id:'work',name:'Work'}];
    await expect(page.locator('[data-setup-discovery-reason]')).toHaveText(credential);
    await page.locator('[data-setup-field=bat_secret]').fill('synthetic-bat-secret');
    await page.locator('[data-setup-discover]').click();
    await page.locator('[data-setup-discovered-profile]').selectOption('work');
    // Discovery applies the profile and persists the draft in the same synchronous block.
    await expect(page.locator('[data-setup-field=profile_id]')).toHaveValue('work');
    await page.reload(); await openSetupAdvanced(page);
    await page.locator('[data-setup-connect] > summary').click();
    await expect(page.locator('[data-setup-review-reason]')).toBeVisible();
    await expect(page.locator('[data-setup-review-reason]')).toHaveText(credential);
    await expect(page.locator('[data-setup-host]').getByRole('button', {name: review, exact:true})).toBeDisabled();
    expect(state.previews).toEqual([]); expect(state.posts).toEqual([]);
  });
  test('keeps a configured nondefault profile when the form has no profile draft', async ({page}) => {
    const state = await existingForm(page, locale, 'work');
    const button = page.locator('[data-setup-host]').getByRole('button', {name:review,exact:true});
    await expect(button).toBeEnabled(); await button.click();
    await expect(page.locator('[data-setup-review]')).toContainText('work');
    expect(state.previews[0].body.params.profile_id).toBe('work');
    await applySetupReview(page);
    expect(state.discoveries).toEqual([]); expect(state.staged).toEqual([]); expect(state.posts).toHaveLength(1);
  });
  test('discovery offers a profile-specific placeholder', async ({page}) => {
    const state = await managedSetupFixture(page, false, locale);
    state.discoveryProfiles = [{id:'default',name:'Default'}, {id:'work',name:'Work'}];
    await page.goto('/dashboard/'); await page.locator('.setup-automatic').click();
    await expect(page.locator('[data-setup-discovered-profile] option[value=""]')).toHaveText(locale === 'en' ? 'Choose a profile' : '請選擇設定檔');
    expect(state.discoveries).toHaveLength(1);
  });
  for (const bad of [{workspace_id:'ws-1',name:{}}, {workspace_id:'ws-1',name:'x'.repeat(257)}, {workspace_id:'ws-1',name:'Workspace',extra:true}])
    test(`refuses malformed workspace ${JSON.stringify(bad).slice(0,45)}`, async ({page}) => {
      const state = await managedSetupFixture(page, false, locale); state.discoveryWorkspaces = [bad];
      await page.goto('/dashboard/'); await page.locator('.setup-automatic').click();
      await expect(page.locator('[data-error-summary]')).toHaveText(locale === 'en'
        ? 'The receipt does not match the original configuration request.' : '回執與原設定請求不一致。');
      await expect(page.locator('[data-setup-discovery]')).toBeHidden();
      expect(state.previews).toEqual([]); expect(state.posts).toEqual([]);
    });
});
