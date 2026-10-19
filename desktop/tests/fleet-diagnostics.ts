import {test, expect, type Page} from '@playwright/test';

// Exercise both existing native IPC surfaces with the same user-visible failures.
export function fleetDiagnosticsTests(setup: (page: Page, options?: any) => Promise<void>, native: boolean) {
  const region = (page: Page) => page.locator(native ? '.fleet-desktop' : '[aria-label="Local Fleet connections"]');
  const check = (page: Page) => region(page).getByRole('button', {name: 'Read status', exact: true}).first().click();
  const machine = (page: Page) => page.locator('[data-fleet-diagnosis="node-1"]');
  const selection = native ? '[data-fleet-choice="connections:node-2"]' : '[data-fleet-name="node-2"]';
  const writes = (page: Page) => page.evaluate(() => (window as any).__fleet.calls.filter((call: any) =>
    !['overview', 'status', 'launch_status', 'migration_status'].includes(call.action)));

  test('a failed read withdraws cached success, preserves the draft and recovers without writes', async ({page}) => {
    await setup(page, {clock: true});
    await expect(machine(page)).toHaveAttribute('data-fleet-state', 'ready');
    await page.locator(selection).uncheck();
    await machine(page).locator('summary').click();
    await expect(machine(page).locator('details')).toHaveAttribute('open', '');
    await page.evaluate(() => {(window as any).__fleet.failReads = true;});
    await page.clock.fastForward(5000);
    await expect(machine(page)).toHaveAttribute('data-fleet-state', 'unavailable');
    await expect(machine(page).locator('summary')).toBeFocused();
    await expect(region(page).locator('[data-fleet-state="ready"]')).toHaveCount(0);
    await expect(region(page).locator('[data-check-state="passed"]')).toHaveCount(0);
    await expect(page.locator(selection)).not.toBeChecked();
    await expect(page.locator(selection)).toBeDisabled();
    expect(await writes(page)).toEqual([]);
    await page.evaluate(() => {(window as any).__fleet.failReads = false;});
    await check(page);
    await expect(machine(page)).toHaveAttribute('data-fleet-state', 'ready');
    await expect(page.locator(selection)).not.toBeChecked();
    await expect(page.locator(selection)).toBeEnabled();
    await expect(machine(page).locator('details')).toHaveAttribute('open', '');
    expect(await writes(page)).toEqual([]);
  });

  test('an initial read failure provides a working read-only retry', async ({page}) => {
    await setup(page, {failReads: true, clock: true});
    await expect(region(page).getByRole('button', {name: 'Read status', exact: true})).toBeEnabled();
    await expect(machine(page)).toHaveCount(0);
    await page.evaluate(() => {(window as any).__fleet.failReads = false;});
    await check(page);
    await expect(machine(page)).toHaveAttribute('data-fleet-state', 'ready');
    expect(await writes(page)).toEqual([]);
  });

  test('stopped monitoring and unapplied selection never present old ready rows as current', async ({page}) => {
    await setup(page, {clock: true});
    await page.evaluate(() => {(window as any).__fleet.doc.monitor.state = 'stopped';});
    await check(page);
    await expect(machine(page)).toHaveAttribute('data-fleet-state', 'stopped');
    await page.evaluate(() => {
      const doc = (window as any).__fleet.doc;
      doc.monitor.state = 'running'; doc.selection.applied_revision = 'old';
    });
    await check(page);
    await expect(machine(page)).toHaveAttribute('data-fleet-state', 'checking');
    await page.evaluate(() => {
      const doc = (window as any).__fleet.doc;
      doc.selection.applied_revision = doc.selection.revision; doc.readiness.state = 'stale';
    });
    await check(page);
    await expect(machine(page)).toHaveAttribute('data-fleet-state', 'stale');
    await expect(region(page).locator('[data-check-state="passed"]')).toHaveCount(0);
    expect(await writes(page)).toEqual([]);
  });

  for (const locale of ['en-US', 'zh-TW']) for (const width of [390, 1440]) {
    test(`machine diagnosis is readable and keeps details across polls ${locale} ${width}`, async ({browser}, testInfo) => {
      const context = await browser.newContext({locale, viewport: {width, height: 900}});
      const page = await context.newPage();
      await setup(page, {clock: true});
      await page.evaluate(() => {
        const rows = (window as any).__fleet.doc.readiness.hosts;
        Object.assign(rows[0], {code: 'BAT_AUTH_REFUSED', blocking: 'auth', level: 'down'});
        Object.assign(rows[1], {code: 'WORKSPACE_BINDING_MISMATCH', blocking: 'workspace', level: 'down'});
        Object.assign(rows[2], {code: 'ROUTE_UNAVAILABLE', blocking: 'tunnel', level: 'down'});
        rows[3].stale = true;
      });
      await page.clock.fastForward(5000);
      await expect(machine(page)).toHaveAttribute('data-fleet-state', 'identity');
      await expect(machine(page).getByText('BAT_AUTH_REFUSED', {exact: true})).not.toBeVisible();
      await machine(page).locator('summary').focus();
      await page.keyboard.press('Enter');
      await expect(machine(page).getByText('BAT_AUTH_REFUSED', {exact: true})).toBeVisible();
      await page.clock.fastForward(5000);
      await expect(machine(page).locator('summary')).toBeFocused();
      await expect(machine(page).locator('[data-fleet-check="identity"]')).toHaveAttribute('data-check-state', 'attention');
      await expect(machine(page).locator('[data-fleet-check="service"]')).toHaveAttribute('data-check-state', 'unchecked');
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      expect(await writes(page)).toEqual([]);
      await machine(page).scrollIntoViewIfNeeded();
      await page.screenshot({path: testInfo.outputPath(`diagnosis-${locale}-${width}.png`)});
      await context.close();
    });
  }
}
