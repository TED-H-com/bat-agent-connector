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
    await expect(page.locator('.session-entry').first()).toBeVisible();
    await expect(page.locator('[data-session-paths]')).toBeHidden();
    expect(s.writes).toHaveLength(0); await s.context.close();
  });
  test(`Agents setup and eligibility ${locale} ${width}`, async ({browser}) => {
    test.setTimeout(60_000);
    const s = await pathsFixture(browser, {locale, width}); const {page} = s;
    await s.context.grantPermissions(['clipboard-read', 'clipboard-write']);
    await page.goto('/dashboard/#/settings?section=agents');
    await expect(page.locator('[data-agents-heading]')).toBeFocused();
    await expect(page.locator('[data-agents-heading]')).toHaveCSS('outline-style', 'none');
    await page.keyboard.press('Tab');
    await expect(page.locator('[data-pm-eligibility-check]')).toBeFocused();
    await expect(page.locator('[data-pm-eligibility-check]')).toHaveCSS('outline-style', 'solid');
    await expect(page.locator('[data-pm-eligibility-check]')).toHaveCSS('outline-width', '2px');
    await expect(page.locator('[data-pm-eligibility]')).toHaveText(copy.agents_ready);
    const config = page.locator('[data-mcp-config]');
    await expect(config).toContainText('<SCOPED_TOKEN>');
    await expect(config).toHaveCSS('font-family', /monospace/);
    expect(await config.evaluate(node => {
      const style = getComputedStyle(node);
      return style.backgroundColor !== getComputedStyle(node.parentElement!).backgroundColor &&
        parseFloat(style.paddingLeft) > 0 && parseFloat(style.borderRadius) > 0 && node.scrollWidth <= node.clientWidth;
    })).toBe(true);
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
  test(`Agents Tab during initial eligibility check ${locale} ${width}`, async ({browser}) => {
    const s = await pathsFixture(browser, {locale, width}); const {page} = s;
    let release!: () => void;
    const held = new Promise<void>(resolve => { release = resolve; });
    s.overrides.unshift(async (_input, url) => {
      if (url.pathname !== '/pm/installation-capabilities') return;
      await held;
      return undefined;
    });
    await page.goto('/dashboard/#/settings?section=agents');
    await expect.poll(() => s.reads('/pm/installation-capabilities')).toBe(1);
    await expect(page.locator('[data-agents-heading]')).toBeFocused();
    await page.keyboard.press('Tab');
    const check = page.locator('[data-pm-eligibility-check]');
    await expect(check).toBeFocused();
    release();
    await expect(page.locator('[data-pm-eligibility]')).toHaveText(copy.agents_ready);
    await expect(check).toBeFocused();
    expect(s.writes).toHaveLength(0); await s.context.close();
  });
  test(`Agents Enter on check again ${locale} ${width}`, async ({browser}) => {
    const s = await pathsFixture(browser, {locale, width}); const {page} = s;
    let holdNext = false;
    let release!: () => void;
    const held = new Promise<void>(resolve => { release = resolve; });
    s.overrides.unshift(async (_input, url) => {
      if (url.pathname !== '/pm/installation-capabilities') return;
      if (!holdNext) return;
      await held;
      return undefined;
    });
    await page.goto('/dashboard/#/settings?section=agents');
    const check = page.locator('[data-pm-eligibility-check]');
    await expect(page.locator('[data-pm-eligibility]')).toHaveText(copy.agents_ready);
    await check.focus();
    await expect(check).toBeFocused();
    holdNext = true;
    const readsBefore = s.reads('/pm/installation-capabilities');
    await page.keyboard.press('Enter');
    await expect.poll(() => s.reads('/pm/installation-capabilities')).toBe(readsBefore + 1);
    await expect(check).toBeFocused();
    release();
    await expect(page.locator('[data-pm-eligibility]')).toHaveText(copy.agents_ready);
    await expect(check).toBeFocused();
    expect(s.reads('/pm/installation-capabilities')).toBe(readsBefore + 1);
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

for (const locale of ['en', 'zh-TW'] as const) {
  const copy = workPathsStrings[locale];
  test(`bundled MCP guidance ${locale}`, async ({browser}) => {
    const s = await pathsFixture(browser, {locale, managed: true});
    await s.page.goto('/dashboard/#/settings?section=agents');
    const mcp = s.page.locator('[data-settings-mcp]');
    await expect(mcp).toContainText(copy.agents_bundle);
    await expect(mcp.locator('[data-mcp-config]')).toHaveCount(0);
    await expect(mcp.getByRole('button')).toHaveCount(0);
    await expect(mcp).not.toContainText('batc api-token');
    await expect(mcp.getByRole('link', {name: copy.agents_mcp_docs})).toHaveAttribute('href', /#cli-and-agent-access$/);
    expect(s.writes).toHaveLength(0); await s.context.close();
  });
  test(`workspace BAT eligibility ${locale}`, async ({browser}) => {
    const s = await pathsFixture(browser, {locale, native: null, bat: {action_registered: true, authorized: true, eligible_for_preflight: true, reason: null}});
    const {page, ids} = s, discussion = `#/pm/${ids.workspaceId}/discussions/${ids.threadIdea}`;
    const path = `/pm/workspaces/${ids.workspaceId}/capabilities`;
    await page.goto('/dashboard/#/settings?section=agents&from=' + encodeURIComponent(discussion));
    await expect(page.locator('[data-pm-eligibility]')).toHaveText(copy.agents_ready);
    expect(s.reads('/pm/installation-capabilities')).toBe(0);
    for (const code of ['PM_WORKTREE_REQUIRED', 'REPOSITORY_BINDING_CHANGED', 'REPOSITORY_NOT_BOUND', 'REPOSITORY_HOST_UNAVAILABLE', 'HOST_READ_ONLY', 'TIER_DISABLED']) {
      s.state.bat = {action_registered: true, authorized: true, eligible_for_preflight: false, reason: code};
      const reads = s.reads(path);
      await page.locator('[data-pm-eligibility-check]').click();
      await expect(page.locator('[data-pm-eligibility]')).toHaveText(copy.agents_unavailable);
      await expect(page.locator('[data-pm-eligibility-reason]')).toHaveText(copy[eligibilityReasons[code] as keyof typeof copy]);
      expect(s.reads(path)).toBe(reads + 1);
    }
    expect(s.writes).toHaveLength(0); await s.context.close();
  });
}

test('eligibility response cannot restore an abandoned Settings view', async ({browser}) => {
  const s = await pathsFixture(browser), {page} = s;
  let release!: () => void;
  const held = new Promise<void>(resolve => {release = resolve;});
  s.overrides.unshift(async (_input, url) => {
    if (url.pathname !== '/pm/installation-capabilities') return;
    await held;
    return {status: 200, data: {native_analysis: {action_registered: true, eligible_for_preflight: false, reason: 'PM_NATIVE_AUTH_UNAVAILABLE'}}};
  });
  await page.goto('/dashboard/#/settings?section=agents');
  await expect.poll(() => s.reads('/pm/installation-capabilities')).toBe(1);
  await go(page, '#/sessions'); await expect(page.locator('.session-heading')).toBeVisible();
  release();
  await expect(page.locator('[data-pm-eligibility]')).toHaveCount(0);
  await expect(page.locator('body')).not.toContainText(workPathsStrings.en.agents_auth);
  expect(s.writes).toHaveLength(0); await s.context.close();
});

for (const locale of ['en', 'zh-TW'] as const) for (const width of [1440, 390]) {
  test(`only empty sessions name the paths ${locale} ${width}`, async ({browser}) => {
    const copy = workPathsStrings[locale], s = await pathsFixture(browser, {locale, width}), {page} = s;
    let inventory = 'populated';
    s.overrides.unshift((_input, url) => {
      if (url.pathname !== '/sessions' || inventory === 'populated') return;
      return inventory === 'unavailable' ? {status: 503, data: {error: {code: 'UNAVAILABLE', message: 'Inventory unavailable'}}}
        : {status: 200, data: {sessions: [], next_cursor: inventory === 'partial' ? 'next-page' : null}};
    });
    await page.goto('/dashboard/#/sessions');
    const header = page.locator('.session-heading');
    await expect(page.locator('.session-entry').first()).toBeVisible();
    await expect(page.locator('[data-session-paths]')).toBeHidden();
    // A client-side search with no matches is not an empty inventory.
    await page.locator('.session-filters input[type=search]').fill('no-such-session');
    await expect(page.locator('.session-entry')).toHaveCount(0);
    await expect(page.locator('[data-session-paths]')).toBeHidden();
    await page.locator('.session-filters input[type=search]').fill('');
    inventory = 'empty'; await page.reload();
    await expect(page.locator('[data-session-paths]')).toBeVisible();
    await expect(page.locator('.session-entry')).toHaveCount(0);
    await expect(header).toContainText(copy.paths_first);
    await expect(header.getByRole('link', {name: copy.paths_start})).toHaveAttribute('href', '#/start');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    // Registration alone does not grant a start; keep start scope but withdraw action allowance.
    s.state.startAllowed = false; await page.reload();
    await expect(header.getByRole('link', {name: copy.paths_start})).toHaveCount(0);
    inventory = 'populated'; await page.reload();
    await expect(page.locator('.session-entry').first()).toBeVisible();
    await expect(page.locator('[data-session-paths]')).toBeHidden();
    for (inventory of ['partial', 'unavailable']) {
      const reads = s.reads('/sessions'); await page.reload();
      await expect.poll(() => s.reads('/sessions')).toBeGreaterThan(reads);
      if (inventory === 'partial') await expect(page.locator('.session-pagination button')).toBeVisible();
      else await expect(page.locator('#main [data-api-error]')).toContainText('UNAVAILABLE');
      await expect(page.locator('[data-session-paths]')).toBeHidden();
    }
    expect(s.writes).toHaveLength(0); await s.context.close();
  });
}
