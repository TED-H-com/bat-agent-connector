import {test, expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {permissionFixture, openPermissions, operationId} from './permissions-fixture';

for (const native of [false, true]) {
  const transport = native ? 'native' : 'browser';
  test(`${transport}: permissions wait for the entire initial view read`, async ({page}) => {
    const state = await permissionFixture(page, native, {delayLateSession: true});
    const form = await openPermissions(page);
    try {
      await expect.poll(() => Boolean(state.releaseLateSession)).toBe(true);
      await expect(form.getByRole('button', {name: 'Apply permissions', exact: true})).toBeDisabled();
      expect(state.posts).toHaveLength(0);
    } finally {state.releaseLateSession?.();}
    await form.getByRole('button', {name: 'Apply permissions', exact: true}).click();
    await expect(form).toContainText('BAT accepted the requested configuration');
    expect(state.posts).toHaveLength(1);
    expect(state.errors).toEqual([]);
  });
  test(`${transport}: definitive admission refusal permits an explicit changed draft`, async ({page}) => {
    const state = await permissionFixture(page, native, {refuse: 'TASK_PAUSED'});
    let form = await openPermissions(page);
    const savedIntent = () => page.evaluate(() => JSON.parse(Object.entries(localStorage)
      .find(([key]) => key.startsWith('batc.permissions.'))![1]).intent);
    for (const code of ['TASK_PAUSED', 'CONTROL_VERSION_CONFLICT', 'PERMISSIONS_HOST_POLICY', 'CONFINEMENT_RAISE_REFUSED']) {
      state.refuse = code;
      await form.getByRole('combobox').selectOption('allow_all');
      await form.getByRole('button', {name: 'Apply permissions', exact: true}).click();
      await expect(form).toContainText(code);
      await expect(form.getByRole('button', {name: 'Start another change'})).toBeEnabled();
      const before = state.posts.length;
      const refusedIntent = await savedIntent();
      expect(refusedIntent.key).toBe(state.posts[before - 1].idempotency_key);
      expect(refusedIntent.request).toEqual(state.posts[before - 1].body);
      await page.reload(); form = page.locator('[data-permissions]'); await form.locator('summary').click();
      expect(await savedIntent()).toEqual(refusedIntent);
      await form.getByRole('button', {name: 'Start another change'}).click();
      await expect(form.getByRole('combobox')).toHaveValue('default');
      expect(await savedIntent()).toBeUndefined();
      expect(state.posts).toHaveLength(before);
    }
    state.refuse = null;
    await form.getByRole('button', {name: 'Apply permissions', exact: true}).click();
    await expect(form).toContainText('BAT accepted the requested configuration');
    expect(new Set(state.posts.map(post => post.idempotency_key)).size).toBe(5);
    expect(state.posts[4].body.params.mode).toBe('default');
  });
  test(`${transport}: auth or ambiguous failure after lost acceptance never permits a new key`, async ({page}) => {
    const state = await permissionFixture(page, native, {lost: true});
    const form = await openPermissions(page);
    await form.getByRole('button', {name: 'Apply permissions', exact: true}).click();
    await expect(form).toContainText('Lost permission reply');
    for (const code of ['FORBIDDEN', 'UNKNOWN_ACTION', 'IDEMPOTENCY_CONFLICT']) {
      state.refuse = code;
      await form.getByRole('button', {name: 'Retry original request'}).click();
      await expect(form).toContainText(code);
      await expect(form.getByRole('button', {name: 'Start another change'})).toBeHidden();
      await expect(form.getByRole('combobox')).toBeDisabled();
    }
    expect(new Set(state.posts.map(post => post.idempotency_key)).size).toBe(1);
  });
  test(`${transport}: same body from a different operation key cannot be accepted`, async ({page}) => {
    const state = await permissionFixture(page, native, {badKey: true});
    const form = await openPermissions(page);
    await form.getByRole('button', {name: 'Apply permissions', exact: true}).click();
    await expect(form).toContainText('does not match');
    await expect(form.getByRole('link', {name: 'View operation and step receipts'})).toHaveCount(0);
    await expect(form.getByRole('button', {name: 'Start another change'})).toBeHidden();
    state.badKey = false;
    await form.getByRole('button', {name: 'Retry original request'}).click();
    await expect(form).toContainText('BAT accepted the requested configuration');
    expect(state.posts[1]).toEqual(state.posts[0]);
  });
  test(`${transport}: event preceding first reply waits for identity then fresh operation read`, async ({page}) => {
    const state = await permissionFixture(page, native, {status: 'running', delayPost: true});
    const form = await openPermissions(page);
    await form.getByRole('button', {name: 'Apply permissions', exact: true}).click();
    await expect.poll(() => Boolean(state.holdPost)).toBe(true);
    state.status = 'succeeded'; state.failRead = true;
    state.events.push({seq: 1, kind: 'operation.succeeded', resource_type: 'operation', resource_id: operationId});
    await expect.poll(() => state.delivered).toBe(true);
    expect(state.after).toBe(0);
    state.holdPost();
    await expect(form).toContainText('Permission read failed');
    expect(state.after).toBe(0);
    state.failRead = false;
    await expect(form).toContainText('BAT accepted the requested configuration');
    await expect.poll(() => state.after).toBe(1);
    expect(state.posts).toHaveLength(1);
  });
  test(`${transport}: new changes start with normal permissions and require a fresh explicit apply`, async ({page}) => {
    const state = await permissionFixture(page, native);
    const form = await openPermissions(page);
    await expect(form.getByRole('combobox')).toHaveValue('default');
    await form.getByRole('combobox').selectOption('allow_all');
    await form.getByRole('button', {name: 'Apply permissions', exact: true}).click();
    await expect(form).toContainText('BAT accepted the requested configuration');
    await form.getByRole('button', {name: 'Start another change'}).click();
    await expect(form.getByRole('combobox')).toHaveValue('default');
    expect(state.posts).toHaveLength(1);
    await form.getByRole('button', {name: 'Apply permissions', exact: true}).click();
    await expect.poll(() => state.posts.length).toBe(2);
    expect(state.posts[1].body.params).toEqual({mode: 'default'});
    expect(state.posts[1].idempotency_key).not.toBe(state.posts[0].idempotency_key);
  });
  test(`${transport}: lost reply retains fixed mode, session and key across reload`, async ({page}) => {
    const state = await permissionFixture(page, native, {lost: true, delayPost: true});
    let form = await openPermissions(page);
    await form.getByRole('combobox').selectOption('allow_all');
    // Keep the reply pending through both clicks. An immediate native error can
    // otherwise relabel this button as Retry before the second click lands.
    try {
      await form.getByRole('button', {name: 'Apply permissions', exact: true}).dblclick();
      await expect.poll(() => state.posts.length).toBe(1);
      await expect.poll(() => Boolean(state.holdPost)).toBe(true);
    } finally {state.delayPost = false; state.holdPost?.();}
    await expect(form).toContainText('Lost permission reply');
    await expect(form.getByRole('combobox')).toBeDisabled();
    expect(state.posts[0].body).toEqual({action: 'session.permissions', target: {host: 'demo', session_id: 'managed-session-1'},
      params: {mode: 'allow_all'}, preconditions: {}}); // unrelated synthetic row version is not task authority
    await page.reload(); form = page.locator('[data-permissions]'); await form.locator('summary').click();
    await expect(form.getByRole('combobox')).toHaveValue('allow_all');
    await form.getByRole('button', {name: 'Retry original request'}).click();
    await expect(form.getByRole('link', {name: 'View operation and step receipts'})).toBeVisible();
    expect(state.posts).toHaveLength(2);
    expect(state.posts[1].idempotency_key).toBe(state.posts[0].idempotency_key);
    expect(state.posts[1].body).toEqual(state.posts[0].body);
    await page.reload();
    await expect.poll(() => state.reads.filter(path => path === '/operations/'+operationId).length).toBeGreaterThan(0);
    expect(state.posts).toHaveLength(2); expect(state.errors).toEqual([]);
  });
  test(`${transport}: unknown operation remains readback-only and exposes step receipts`, async ({page}) => {
    const state = await permissionFixture(page, native, {status: 'uncertain'});
    const form = await openPermissions(page);
    await form.getByRole('button', {name: 'Apply permissions', exact: true}).click();
    await expect(form.getByRole('button', {name: 'Start another change'})).toBeHidden();
    await form.getByRole('button', {name: 'Check original operation'}).click();
    expect(state.posts).toHaveLength(1);
    await form.getByRole('link', {name: 'View operation and step receipts'}).click();
    await expect(page.getByRole('heading', {name: 'Set session permissions'})).toBeVisible();
    await expect(page.locator('code', {hasText: 'session.permissions'})).toBeVisible();
    await expect(page.getByText('permission_frame', {exact: true})).toBeVisible();
    expect(state.errors).toEqual([]);
  });
  test(`${transport}: streaming refusal requires an explicit new request after idle`, async ({page}) => {
    const state = await permissionFixture(page, native, {status: 'failed', streaming: true});
    const form = await openPermissions(page);
    await form.getByRole('button', {name: 'Apply permissions', exact: true}).click();
    await expect(form).toContainText('Wait for idle and submit a new operation');
    const key = state.posts[0].idempotency_key;
    await form.getByRole('button', {name: 'Check original operation'}).click();
    expect(state.posts).toHaveLength(1);
    state.status = 'succeeded'; state.streaming = false;
    await form.getByRole('button', {name: 'Start another change'}).click();
    await form.getByRole('combobox').selectOption('allow_all');
    await form.getByRole('button', {name: 'Apply permissions', exact: true}).click();
    await expect.poll(() => state.posts.length).toBe(2);
    expect(state.posts[1].idempotency_key).not.toBe(key);
    expect(state.posts[1].body.params).toEqual({mode: 'allow_all'});
  });
  test(`${transport}: saved unsent selection survives polling and reload`, async ({page}) => {
    const state = await permissionFixture(page, native);
    const form = await openPermissions(page);
    await form.getByRole('combobox').selectOption('allow_all');
    state.events.push({seq: 1, kind: 'session.updated', resource_type: 'session', resource_id: 'demo/managed-session-1'});
    await expect.poll(() => state.after).toBe(1);
    await expect(form.getByRole('combobox')).toHaveValue('allow_all');
    await page.reload(); await page.locator('[data-permissions] > summary').click();
    await expect(page.locator('[data-permissions] select')).toHaveValue('allow_all');
    expect(state.posts).toHaveLength(0);
  });
  for (const field of ['server', 'principal']) test(`${transport}: ${field} isolates stored permission intent`, async ({page}) => {
    const state = await permissionFixture(page, native, {lost: true});
    let form = await openPermissions(page);
    await form.getByRole('combobox').selectOption('allow_all');
    await form.getByRole('button', {name: 'Apply permissions', exact: true}).click();
    await expect(form).toContainText('Lost permission reply');
    const original = state[field]; state[field] = 'different-identity';
    await page.reload(); form = page.locator('[data-permissions]'); await form.locator('summary').click();
    await expect(form.getByRole('combobox')).toHaveValue('default');
    await expect(form.getByRole('button', {name: 'Apply permissions', exact: true})).toBeEnabled();
    expect(state.posts).toHaveLength(1);
    state[field] = original;
    await page.reload(); await form.locator('summary').click();
    await expect(form.getByRole('combobox')).toHaveValue('allow_all');
    await expect(form.getByRole('button', {name: 'Retry original request'})).toBeVisible();
  });
  test(`${transport}: missing capability, host write grant or operate scope cannot enable`, async ({page}) => {
    const state = await permissionFixture(page, native);
    for (const options of [{action: null}, {action: false}, {writes: false}, {writes: undefined}, {scopes: ['observe']}]) {
      Object.assign(state, {action: true, writes: true, scopes: ['observe', 'operate']}, options);
      await page.goto('/dashboard/#/session/demo/managed-session-1'); await page.reload();
      const form = page.locator('[data-permissions]'); await form.locator('summary').click();
      await expect(form.getByRole('button', {name: 'Apply permissions', exact: true})).toBeDisabled();
    }
    expect(state.posts).toHaveLength(0);
  });
  test(`${transport}: manual and unknown sessions never mount permission controls`, async ({page}) => {
    const state = await permissionFixture(page, native);
    for (const provenance of ['manual', 'unknown']) {
      Object.assign(state, {provenance, apiAccess: 'read_only'});
      await page.goto('/dashboard/#/session/demo/managed-session-1'); await page.reload();
      await expect(page.getByRole('heading', {name: 'Managed session'})).toBeVisible();
      await expect(page.locator('[data-permissions]')).toHaveCount(0);
    }
    expect(state.posts).toHaveLength(0);
  });
  test(`${transport}: delayed response cannot write accepted intent into another identity`, async ({page}) => {
    const state = await permissionFixture(page, native, {delayPost: true});
    const form = await openPermissions(page);
    await form.getByRole('button', {name: 'Apply permissions', exact: true}).click();
    await expect.poll(() => Boolean(state.holdPost)).toBe(true);
    state.principal = 'replacement-principal';
    await page.goto('/dashboard/#/settings');
    const disconnect = page.getByRole('button', {name: 'Disconnect', exact: true});
    await disconnect.click();
    if (!native) await page.locator('input[type=password]').fill('replacement-token');
    await page.getByRole('button', {name: 'Connect', exact: true}).click();
    await expect(page).toHaveURL(/#\/home$/);
    state.holdPost();
    const fresh = await openPermissions(page);
    await expect(fresh.getByRole('button', {name: 'Apply permissions', exact: true})).toBeEnabled();
    expect(state.posts).toHaveLength(1);
    expect(await page.evaluate(() => Object.entries(localStorage).filter(([key]) => key.startsWith('batc.permissions.'))
      .map(([,value]) => JSON.parse(value).intent?.operation_id))).toEqual([null]);
  });
  test(`${transport}: operation refresh failure retains cursor and original intent`, async ({page}) => {
    const state = await permissionFixture(page, native, {status: 'uncertain'});
    const form = await openPermissions(page);
    await form.getByRole('combobox').selectOption('allow_all');
    await form.getByRole('button', {name: 'Apply permissions', exact: true}).click();
    await expect(form.getByRole('button', {name: 'Check original operation'})).toBeVisible();
    state.failRead = true;
    state.events.push({seq: 1, kind: 'operation.updated', resource_type: 'operation', resource_id: operationId});
    await expect(form).toContainText('Permission read failed');
    expect(state.after).toBe(0);
    state.failRead = false;
    await expect.poll(() => state.after).toBe(1);
    await expect(form.getByRole('combobox')).toHaveValue('allow_all');
    expect(state.posts).toHaveLength(1); expect(state.errors).toEqual([]);
  });
  test(`${transport}: misrouted accepted response cannot unlock or re-target`, async ({page}) => {
    const state = await permissionFixture(page, native, {badTarget: true});
    const form = await openPermissions(page);
    await form.getByRole('combobox').selectOption('allow_all');
    await form.getByRole('button', {name: 'Apply permissions', exact: true}).click();
    await expect(form).toContainText('does not match');
    await expect(form.getByRole('combobox')).toBeDisabled();
    await expect(form.getByRole('button', {name: 'Start another change'})).toBeHidden();
    expect(await page.evaluate(() => Object.entries(localStorage).find(([key]) => key.startsWith('batc.permissions.'))
      ?.map(value => value).at(1))).not.toContain(operationId);
    state.badTarget = false;
    await form.getByRole('button', {name: 'Retry original request'}).click();
    await expect(form.getByRole('link', {name: 'View operation and step receipts'})).toBeVisible();
    expect(state.posts[1].idempotency_key).toBe(state.posts[0].idempotency_key);
  });
  test(`${transport}: malformed saved request preserves recoverable operation ID`, async ({page}) => {
    const state = await permissionFixture(page, native, {status: 'needs_attention'});
    const form = await openPermissions(page);
    await form.getByRole('button', {name: 'Apply permissions', exact: true}).click();
    await expect(form.getByRole('button', {name: 'Check original operation'})).toBeVisible();
    await page.evaluate(() => {
      const key = Object.keys(localStorage).find(key => key.startsWith('batc.permissions.'))!;
      const saved = JSON.parse(localStorage.getItem(key)!); saved.intent.request = null;
      localStorage.setItem(key, JSON.stringify(saved));
    });
    await page.reload(); await form.locator('summary').click();
    await expect(form.getByRole('link', {name: 'View operation and step receipts'})).toBeVisible();
    await expect(form.getByRole('button', {name: 'Start another change'})).toBeHidden();
    await expect(form.getByRole('combobox')).toBeDisabled();
    expect(state.posts).toHaveLength(1); expect(state.errors).toEqual([]);
  });
}
for (const locale of ['en-US', 'zh-TW']) for (const width of [390, 768, 1440]) test(`permissions layout ${locale} ${width}`, async ({browser}) => {
  const context = await browser.newContext({locale, viewport: {width, height: width === 390 ? 844 : 900}});
  const page = await context.newPage(); const state = await permissionFixture(page, false);
  const form = await openPermissions(page); await form.getByRole('combobox').selectOption('allow_all');
  await expect(form).toContainText(locale === 'en-US' ? 'Bypass the agent' : '略過代理');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await mkdir('/tmp/bac-permissions-after', {recursive: true});
  await page.screenshot({path: `/tmp/bac-permissions-after/session-${locale}-${width}.png`, fullPage: true});
  expect(state.errors).toEqual([]); await context.close();
});
