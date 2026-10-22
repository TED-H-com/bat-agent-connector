import {test, expect} from '@playwright/test';
import {readFileSync} from 'node:fs';
import {storageScope} from '../src/state/events.ts';
import {workspaceFixture, workspaceProject, workspaceRoute} from './workspace-fixture.ts';
import {mountConversation} from './conversation-fixture.ts';
import {storageSnapshot} from './storage-snapshot.ts';

// Producer-pinned by tests/test_api_v1_bootstrap_golden.py; identity and
// capabilities come unchanged from the managed producer, also served to Web.
const bootstrap = JSON.parse(readFileSync(new URL('../../tests/fixtures/managed-bootstrap.json', import.meta.url), 'utf8'));

for (const locale of ['en-US', 'zh-TW']) for (const initial of ['classic', 'workbench']) {
  test(`layout round trip preserves every draft, key and selected target ${locale} ${initial}`, async ({page}) => {
    await page.addInitScript(locale => Object.defineProperty(navigator, 'language', {value: locale}), locale);
    await page.setViewportSize({width: 1440, height: 900});
    await page.addInitScript(() => {
      (window as any).__csp = [];
      document.addEventListener('securitypolicyviolation', event => (window as any).__csp.push(event.violatedDirective));
    });
    const fixture = workspaceFixture(locale);
    const selected = fixture.data.detail.work[1];
    const selectedRoute = `#/work/${workspaceProject}/execution/${selected.operation_id}`;
    const posts: any[] = [], polls: string[] = [];
    const operation = {operation_id: 'op_' + 'e'.repeat(32), action: 'session.send', status: 'queued',
      actor: bootstrap.capabilities.actor, target: {host: selected.host, session_id: selected.session_id}};
    await mountConversation(page, false, async input => {
      const url = new URL(input.path, 'https://fixture.example'), path = url.pathname;
      if (input.method !== 'GET') {
        expect(input.body.action).toBe('session.send');
        posts.push(input);
        return {status: 200, data: {operation}};
      }
      if (path === '/bootstrap') return {status: 200, data: bootstrap};
      if (path === '/capabilities') return {status: 200, data: bootstrap.capabilities};
      if (path === '/events') {
        polls.push(input.path);
        return {status: 200, data: {events: [], next_cursor: bootstrap.sync.checkpoint.cursor,
          head_cursor: bootstrap.sync.head_cursor, has_more: false, sync: bootstrap.sync}};
      }
      if (path === `/operations/${operation.operation_id}`) return {status: 200, data: {operation}};
      if (path === `/sessions/${selected.host}/${selected.session_id}`)
        return {status: 200, data: {session: {...selected.session, pending: null}}};
      if (path === `/sessions/${selected.host}/${selected.session_id}/messages`)
        return {status: 200, data: {messages: fixture.data.messages}};
      if (path === `/sessions/${selected.host}/${selected.session_id}/checkpoints`)
        return {status: 200, data: {checkpoints: []}};
      return fixture.dispatch(input);
    });
    await page.goto(`/dashboard/?workbench=${initial === 'workbench' ? '1' : '0'}` + workspaceRoute);
    const composer = page.locator('#workspace-message-input textarea');
    const firstDraft = 'Keep the first target draft 第一份草稿';
    const selectedDraft = 'Keep the selected target draft 第二份草稿';
    await composer.fill(firstDraft);
    await expect(page.locator('body')).toHaveAttribute('data-layout', initial);
    await expect(page.locator('body')).toHaveAttribute('data-layout-source', 'url');
    await expect(page.locator('body')).toHaveAttribute('data-layout-status', 'preview');
    const selectedLink = page.locator(`.workspace-tree-link[href="${selectedRoute}"]`);
    await selectedLink.click();
    await expect(composer).toHaveValue('');
    await composer.fill('One explicit queued operation');
    await page.getByRole('button', {name: locale === 'zh-TW' ? '送出' : 'Send', exact: true}).click();
    await expect.poll(() => posts.length).toBe(1);
    await expect(page.locator('.session-send-line')).toBeVisible();
    await composer.fill(selectedDraft);
    await expect.poll(() => polls.length).toBeGreaterThan(0);
    const before = await storageSnapshot(page);
    const namespace = storageScope(new URL(page.url()).origin, bootstrap.capabilities.actor,
      bootstrap.sync.server_id, bootstrap.sync.principal_id);
    // Discover the keys the application produced; never seed or construct keys.
    const drafts = before.localStorage.filter(([, value]) => [firstDraft, selectedDraft].includes(value));
    expect(drafts).toHaveLength(2);
    const receipt = before.localStorage.find(([, value]) => {
      try {return JSON.parse(value).key === posts[0].idempotency_key;} catch {return false;}
    });
    expect(receipt).toBeTruthy();
    expect(JSON.parse(receipt![1])).toEqual({key: posts[0].idempotency_key, op: operation.operation_id});
    const checkpoint = before.localStorage.find(([, value]) => value === JSON.stringify(bootstrap.sync.checkpoint));
    expect(checkpoint).toBeTruthy();
    for (const [key] of [...drafts, receipt!, checkpoint!]) expect(key).toContain(namespace);
    const history = await page.locator('.conversation .msg').allTextContents();
    expect(history).toHaveLength(2);
    const originalKey = posts[0].idempotency_key;
    expect(posts[0].body.target).toEqual(operation.target);

    for (const layout of [initial === 'workbench' ? 'classic' : 'workbench', initial]) {
      const previousPolls = polls.length;
      await page.locator('[data-shell-tools]').click();
      // Exercise the product link, including its current target hash, both ways.
      await page.locator('[data-menu-key=workbench]').click();
      await expect(page).toHaveURL(url => url.searchParams.get('workbench') === (layout === 'workbench' ? '1' : '0')
        && url.hash === selectedRoute);
      await expect(page.locator('[data-workbench-views]')).toHaveCount(layout === 'workbench' ? 1 : 0);
      await expect(composer).toHaveValue(selectedDraft);
      await expect(page.locator('body')).toHaveAttribute('data-layout', layout);
      await expect(page.locator('body')).toHaveAttribute('data-layout-source', 'url');
      await expect(page.locator('body')).toHaveAttribute('data-layout-status', 'preview');
      await expect(page.locator('body')).toHaveAttribute('data-layout-reason', '');
      await expect(selectedLink).toHaveAttribute('aria-current', 'page');
      await expect(page.locator('.conversation .msg')).toHaveCount(history.length);
      expect(await page.locator('.conversation .msg').allTextContents()).toEqual(history);
      expect(await storageSnapshot(page)).toEqual(before);
      await expect.poll(() => polls.length).toBeGreaterThan(previousPolls);
      const poll = new URL(polls.at(-1)!, 'https://fixture.example');
      expect(poll.searchParams.get('after')).toBe(String(bootstrap.sync.checkpoint.cursor));
      expect(poll.searchParams.get('checkpoint')).toBe(bootstrap.sync.checkpoint.token);
      expect(posts).toHaveLength(1);
      expect(posts[0].idempotency_key).toBe(originalKey);
      expect(await page.evaluate(() => (window as any).__csp)).toEqual([]);
    }
    await page.locator(`.workspace-tree-link[href="${workspaceRoute}"]`).click();
    await expect(composer).toHaveValue(firstDraft);
    expect(posts).toHaveLength(1);
  });
}
