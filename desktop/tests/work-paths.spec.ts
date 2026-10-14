import {test, expect} from '@playwright/test';
import {pathsFixture} from './paths-fixture.ts';
import {go, consoleNoise} from './session-fixture.ts';
import {eligibilityReasons} from '../src/settings-agents.js';
import {workPathsStrings} from '../src/work-paths-strings.js';
for (const locale of ['en', 'zh-TW'] as const) for (const width of [1440, 390]) {
  const zh = locale === 'zh-TW', copy = workPathsStrings[locale];
  test(`manual work paths ${locale} ${width}`, async ({browser}) => {
    const s = await pathsFixture(browser, {locale, width}); const {page, ids} = s;
    await page.goto(`/dashboard/#/session/${ids.host}/${ids.sessions.manual}`);
    const card = page.locator('[data-session-readonly]');
    await expect(card.getByRole('heading', {name: copy.paths_view_only})).toBeVisible();
    expect(await card.getByRole('button').count() + await card.locator('a.ui-button').count()).toBeLessThanOrEqual(2);
    await expect(page.locator('.workspace-composer')).toBeHidden();
    await expect(page.getByRole('button', {name: zh ? '送出' : 'Send', exact: true})).toHaveCount(0);
    await card.getByRole('button', {name: copy.paths_continue}).click();
    const record = page.locator('.session-checkpoint-record');
    await expect(record).toBeVisible(); await expect(record.getByRole('combobox')).toBeFocused();
    expect(s.writes).toHaveLength(0);
    // Close the mobile sheet before using the card link.
    if (width === 390) await page.keyboard.press('Escape');
    await card.getByRole('link', {name: copy.paths_start}).click();
    await expect(page.locator('[data-session-start] select').first()).toHaveValue(ids.host);
    const how = page.locator('details').filter({has: page.getByText(copy.paths_how, {exact: true})});
    await expect(how).not.toHaveAttribute('open'); await how.locator('summary').click();
    await expect(how.locator('li')).toHaveCount(3); await expect(how.locator('li').last()).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    expect(s.writes).toHaveLength(0); expect(s.errors.filter(e => !consoleNoise(e))).toEqual([]);
    await s.context.close();
  });
  test(`unavailable paths and start grant ${locale} ${width}`, async ({browser}) => {
    const s = await pathsFixture(browser, {locale, width, checkpoints: false}); const {page, ids} = s;
    await page.goto(`/dashboard/#/session/${ids.host}/${ids.sessions.manual}`);
    const card = page.locator('[data-session-readonly]');
    await expect(card.getByRole('button', {name: copy.paths_continue})).toHaveCount(0);
    await expect(card.locator('[data-checkpoint-reason]')).toContainText(copy.paths_host_unavailable);
    await expect(card.getByRole('link', {name: copy.paths_setup})).toHaveAttribute('href', '#/settings?section=connection');
    expect(await page.locator('body').innerText()).not.toMatch(/managed_roots|ssh_alias|write \/ orchestrate/);
    s.state.checkpoints = true; s.state.start = false; await page.reload();
    await expect(card.getByRole('link', {name: copy.paths_start})).toHaveCount(0);
    await expect(card.locator('[data-checkpoint-reason]')).toContainText(copy.paths_start_required);
    await go(page, '#/sessions');
    await expect(page.getByRole('link', {name: copy.paths_start, exact: true})).toHaveCount(0);
    await expect(page.locator('.session-heading')).toContainText(copy.paths_first);
    expect(s.writes).toHaveLength(0); await s.context.close();
  });
  test(`Agents setup and eligibility ${locale} ${width}`, async ({browser}) => {
    test.setTimeout(60_000);
    const s = await pathsFixture(browser, {locale, width}); const {page} = s;
    await s.context.grantPermissions(['clipboard-read', 'clipboard-write']);
    await page.goto('/dashboard/#/settings?section=agents');
    await expect(page.locator('[data-agents-heading]')).toBeFocused();
    await expect(page.locator('[data-pm-eligibility]')).toHaveText(copy.agents_ready);
    await expect(page.locator('[data-mcp-config]')).toContainText('<SCOPED_TOKEN>');
    await page.locator('[data-settings-mcp]').getByRole('button', {name: copy.agents_copy, exact: true}).click();
    const copied = JSON.parse(await page.evaluate(() => navigator.clipboard.readText()));
    expect(copied.mcpServers.bat.env.BATC_API_TOKEN).toBe('<SCOPED_TOKEN>');
    expect(copied.mcpServers.bat.args).toEqual(['--principal-only']);
    const check = page.locator('[data-pm-eligibility-check]');
    for (const [reason, key] of Object.entries(eligibilityReasons)) {
      s.state.native = {action_registered: true, eligible_for_preflight: false, reason};
      const reads = s.reads('/pm/installation-capabilities');
      await check.click();
      await expect(page.locator('[data-pm-eligibility]')).toHaveText(copy.agents_unavailable);
      await expect(page.locator('[data-pm-eligibility-reason]')).toHaveText(copy[key as keyof typeof copy]);
      expect(s.reads('/pm/installation-capabilities')).toBe(reads + 1);
      expect(await page.locator('body').innerText()).not.toContain(reason);
      const details = page.locator('[data-settings-pm-agent] details');
      await details.locator('summary').click(); await expect(details.locator('code')).toHaveText(reason); await details.locator('summary').click();
    }
    s.state.native = null; await check.click();
    await expect(page.locator('[data-pm-eligibility]')).toHaveText(copy.agents_unchecked);
    s.state.native = {action_registered: true, eligible_for_preflight: true, reason: null}; await check.click();
    await expect(page.locator('[data-pm-eligibility]')).toHaveText(copy.agents_ready);
    s.state.status = 403; await check.click();
    await expect(page.locator('[data-pm-eligibility]')).toHaveText(copy.agents_unchecked);
    await expect(page.locator('[data-settings-pm-agent] code')).toHaveCount(0);
    expect(s.writes).toHaveLength(0); await s.context.close();
  });
  test(`Agents return route ${locale} ${width}`, async ({browser}) => {
    const s = await pathsFixture(browser, {locale, width}); const {page, ids} = s;
    const discussion = `#/pm/${ids.workspaceId}/discussions/${ids.threadIdea}`;
    await page.goto('/dashboard/#/settings?section=agents&from=' + encodeURIComponent(discussion));
    await expect(page.locator('[data-agents-heading]')).toBeFocused();
    await page.getByRole('link', {name: copy.agents_back}).click();
    await expect(page).toHaveURL(new RegExp('/discussions/' + ids.threadIdea + '$'));
    for (const from of ['https://example.com', '#//example.com', '#/pm/%ZZ', '#/sessions/../settings', '#/session/%0a/session', '#/unknown']) {
      await go(page, '#/settings?section=agents&from=' + encodeURIComponent(from));
      await expect(page.locator('[data-agents-heading]')).toBeFocused();
      await expect(page.locator('[data-agents-back]')).toHaveCount(0);
    }
    expect(s.writes).toHaveLength(0); await s.context.close();
  });
}
