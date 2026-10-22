import {test, expect} from '@playwright/test';
import {bootstrap, setup, changeIdentity} from './managed-storage-fixture';
import {storageSnapshot, assertStoragePreserved} from './storage-snapshot';

for (const locale of ['en-US', 'zh-TW']) {
  test(`managed port change and app relaunch restore the verified identity storage (${locale})`, async ({browser}) => {
    const context = await browser.newContext({locale});
    const page = await context.newPage();
    await setup(page);
    await page.locator('textarea').first().fill('Keep my original managed draft');
    await expect.poll(() => page.evaluate(() => Object.keys(localStorage).some(key => key.startsWith('batc.sync.')))).toBe(true);
    const before = await page.evaluate(() => {
      const namespace = Object.keys(localStorage).find(key => key.startsWith('batc.sync.'))!.slice('batc.sync.'.length);
      localStorage.setItem(`batc.key.${namespace}.fixture-original`, JSON.stringify({key: 'original-fixture-key', op: 'op_' + 'a'.repeat(32)}));
      const target = {workspace_id: 'ws_' + 'c'.repeat(32), work_item_id: 'wi_' + 'd'.repeat(20)};
      const request = {action: 'session.send', target, params: {text: 'Keep this request', schedule: 'after_current'},
        preconditions: {expected_attempt_id: 'att_' + 'e'.repeat(32)}};
      localStorage.setItem(`batc.pm.${namespace}.session.send.${JSON.stringify(target)}`,
        JSON.stringify({version: 1, values: {text: 'Keep this request'},
          intent: {key: 'original-pm-key', operation_id: 'op_' + 'b'.repeat(32), request}}));
      return {namespace, entries: Object.entries(localStorage).sort()};
    });
    await changeIdentity(page, {endpoint: 'http://127.0.0.1:41002/'});
    await page.reload();
    await expect(page.locator('textarea').first()).toHaveValue('Keep my original managed draft');
    await expect.poll(() => page.evaluate(() => (window as any).__storage.events.length)).toBeGreaterThan(0);
    const after = await page.evaluate(() => ({entries: Object.entries(localStorage).sort(), fixture: (window as any).__storage}));
    expect(after.entries).toEqual(before.entries);
    expect(new URL(after.fixture.events[0], 'https://fixture.example').searchParams.get('after')).toBe('1');
    expect(new URL(after.fixture.events[0], 'https://fixture.example').searchParams.get('checkpoint')).toBe('fixture-saved-checkpoint-proof');
    expect(after.fixture.writes).toBe(0);
    await context.close();
  });

  test(`one old managed port restores drafts, pending keys and exact targets without replay (${locale})`, async ({browser}) => {
    const context = await browser.newContext({locale});
    const page = await context.newPage();
    await setup(page, {legacy: true});
    await expect(page.locator('textarea').first()).toHaveValue('Recovered legacy managed draft');
    const result = await page.evaluate(() => {
      const markerKey = Object.keys(localStorage).find(key => key.startsWith('batc.storage-migration.'))!;
      const marker = JSON.parse(localStorage.getItem(markerKey)!);
      const originals = Object.entries(localStorage).filter(([key]) => key.includes(marker.source));
      return {marker, originals, copies: originals.map(([key]) => localStorage.getItem(key.replace(marker.source, marker.target))),
        oldTree: sessionStorage.getItem(`batc.tree.${marker.source}`), newTree: sessionStorage.getItem(`batc.tree.${marker.target}`),
        writes: (window as any).__storage.writes};
    });
    expect(result.marker.status).toBe('complete');
    expect(result.originals).toHaveLength(4);
    expect(result.copies).toEqual(result.originals.map(([, value]) => value));
    expect(result.newTree).toBe(result.oldTree);
    expect(result.writes).toBe(0);
    await context.close();
  });

  test(`managed relaunch A to B to A across ports isolates principal storage (${locale})`, async ({browser}) => {
    const context = await browser.newContext({locale});
    const page = await context.newPage();
    await setup(page);
    await page.locator('textarea').first().fill('Managed principal A draft');
    await changeIdentity(page, {principal: 'fixture-principal-b', endpoint: 'http://127.0.0.1:41002/'});
    await page.reload();
    await expect(page.locator('textarea').first()).toHaveValue('');
    await page.locator('textarea').first().fill('Managed principal B draft');
    await changeIdentity(page, {principal: bootstrap.sync.principal_id, endpoint: 'http://127.0.0.1:41003/'});
    await page.reload();
    await expect(page.locator('textarea').first()).toHaveValue('Managed principal A draft');
    expect(await page.evaluate(() => (window as any).__storage.writes)).toBe(0);
    await context.close();
  });

  for (const cursors of [[1, 1], [2, 1]]) {
    test(`multiple old managed ports retain all drafts without ranking checkpoint cursors ${cursors} (${locale})`, async ({browser}) => {
      const context = await browser.newContext({locale});
      const page = await context.newPage();
      // Equal cursors cannot order local edits. A higher cursor can also belong
      // to an older runtime before a same-server journal restore (backend B02).
      await setup(page, {legacy: true, legacyCursors: cursors});
      await expect(page.locator('textarea').first()).toHaveValue('');
      const result = await page.evaluate(() => ({
        originals: Object.entries(localStorage).filter(([key]) => key.includes('http%3A%2F%2F')).sort(),
        tabs: Object.entries(sessionStorage).filter(([key]) => key.startsWith('batc.tree.http%3A%2F%2F')).sort(),
        receipts: Object.keys(localStorage).filter(key => key.startsWith('batc.storage-migration.')),
        copiedRequests: Object.keys(localStorage).filter(key => /^batc\.(?:draft|key|pm)\.managed:v1:/.test(key)),
        fixture: (window as any).__storage,
      }));
      expect(result.originals).toEqual(result.fixture.legacyEntries);
      expect(result.tabs).toEqual(result.fixture.legacyTabs);
      expect(result.receipts).toEqual([]);
      expect(result.copiedRequests).toEqual([]);
      expect(result.fixture.writes).toBe(0);
      await context.close();
    });
  }

  test(`disconnect then a new external port waits for verification and cannot inherit the previous binding (${locale})`, async ({browser}) => {
    const context = await browser.newContext({locale});
    const page = await context.newPage();
    await setup(page, {source: 'external'});
    await page.locator('textarea').first().fill('Original draft before disconnect');
    await page.locator('#live').click();
    await page.getByRole('button', {name: locale === 'zh-TW' ? '中斷' : 'Disconnect', exact: true}).click();
    const connect = page.getByRole('button', {name: locale === 'zh-TW' ? '連線' : 'Connect', exact: true});
    await expect(connect).toBeVisible();
    await changeIdentity(page, {endpoint: 'http://127.0.0.1:41002/'});
    const statuses = await page.evaluate(() => (window as any).__storage.statuses);
    await expect.poll(() => page.evaluate(() => (window as any).__storage.statuses)).toBeGreaterThan(statuses);
    expect(await page.evaluate(() => (window as any).__storage.connects)).toBe(1);
    await expect(page.locator('[data-connection-access]')).toHaveCount(0);
    await connect.click();
    await expect(page).toHaveURL(/#\/home$/);
    await page.evaluate(() => {location.hash = '#/session/demo/session-1';});
    await expect(page.locator('textarea').first()).toHaveValue('');
    expect(await page.evaluate(() => (window as any).__storage.writes)).toBe(0);
    await changeIdentity(page, {endpoint: 'http://127.0.0.1:41001/'});
    await page.reload();
    await expect(page.locator('textarea').first()).toHaveValue('Original draft before disconnect');
    await context.close();
  });

  test(`native principal A to B to A keeps drafts and pending operation identity isolated (${locale})`, async ({browser}) => {
    const context = await browser.newContext({locale});
    const page = await context.newPage();
    await setup(page, {source: 'external'});
    await page.locator('textarea').first().fill('Principal A draft');
    await expect.poll(() => page.evaluate(() => Object.keys(localStorage).some(key => key.startsWith('batc.sync.')))).toBe(true);
    const original = await page.evaluate(() => {
      const ns = Object.keys(localStorage).find(key => key.startsWith('batc.sync.'))!.slice('batc.sync.'.length);
      const key = `batc.key.${ns}.send.demo.session-1`, value = JSON.stringify({key: 'principal-a-original', op: 'op_' + 'a'.repeat(32)});
      localStorage.setItem(key, value); return {key, value};
    });
    const replace = async (principal: string, endpoint: string) => {
      await page.locator('#live').click();
      await changeIdentity(page, {principal, endpoint});
      await page.getByRole('button', {name: locale === 'zh-TW' ? '更換憑證' : 'Replace credential', exact: true}).click();
      await expect(page.locator('[data-connection-access]')).toBeVisible();
      await page.evaluate(() => {location.hash = '#/session/demo/session-1';});
      await expect(page.locator('textarea').first()).toBeVisible();
    };
    await replace('fixture-principal-b', 'http://127.0.0.1:41001/');
    await expect(page.locator('textarea').first()).toHaveValue('');
    await page.locator('textarea').first().fill('Principal B draft');
    await replace(bootstrap.sync.principal_id, 'http://127.0.0.1:41001/');
    await expect(page.locator('textarea').first()).toHaveValue('Principal A draft');
    expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(original.value);
    expect(await page.evaluate(() => (window as any).__storage.writes)).toBe(0);
    await context.close();
  });

  test(`external central bootstrap without desktop_identity retains drafts after cursor reset (${locale})`, async ({browser}) => {
    const context = await browser.newContext({locale});
    const page = await context.newPage();
    await setup(page, {source: 'external'});
    await page.locator('textarea').first().fill('External central draft');
    await expect.poll(() => page.evaluate(() => Object.keys(localStorage).some(key => key.startsWith('batc.sync.')))).toBe(true);
    const before = await storageSnapshot(page);
    const checkpointKey = before.localStorage.find(([key]) => key.startsWith('batc.sync.'))![0];
    await page.evaluate(() => {(window as any).__storage.reset = true;});
    await expect.poll(() => page.evaluate(() => (window as any).__storage.bootstraps)).toBeGreaterThan(1);
    await expect(page.locator('textarea').first()).toHaveValue('External central draft');
    expect(await page.evaluate(() => (window as any).__storage.writes)).toBe(0);
    assertStoragePreserved(before, await storageSnapshot(page), checkpointKey);
    await context.close();
  });
}
