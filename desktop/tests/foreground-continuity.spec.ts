import {test, expect, type Page} from '@playwright/test';
import {workspaceFixture, mountWorkspace, workspaceRoute} from './workspace-fixture.ts';

async function visibility(page: Page, value: 'hidden' | 'visible') {
  await page.evaluate(value => {
    Object.defineProperty(document, 'visibilityState', {configurable: true, value});
    document.dispatchEvent(new Event('visibilitychange'));
  }, value);
}

for (const native of [false, true]) for (const locale of ['en', 'zh-TW']) {
  test(`foreground waits for central continuity and keeps the unsent composer ${native ? 'IPC' : 'HTTP'} ${locale}`, async ({page}) => {
    const f = workspaceFixture(locale), dispatch = f.dispatch;
    let held = false, entered = false, polls = 0, release!: () => void;
    const resumed = new Promise<void>(resolve => {release = resolve;});
    f.dispatch = async input => {
      if (input.path.startsWith('/events?')) {polls++; if (held) {entered = true; await resumed;}}
      return dispatch(input);
    };
    await mountWorkspace(page, native, f);
    await page.addInitScript(locale => Object.defineProperty(navigator, 'language', {value: locale}), locale);
    await page.setViewportSize({width: 390, height: 844});
    try {
      await page.goto('/dashboard/?workbench=1' + workspaceRoute);
      const draft = page.locator('#workspace-message-input textarea');
      const send = page.getByRole('button', {name: locale === 'en' ? 'Send' : '送出', exact: true});
      await expect(send).toBeEnabled(); await expect.poll(() => polls).toBeGreaterThan(0);
      await draft.fill('尚未送出，保留這一段。'); const original = await draft.elementHandle();
      await draft.evaluate((node: HTMLTextAreaElement) => node.setSelectionRange(3, 7));
      held = true;
      await visibility(page, 'hidden');
      await expect(send).toBeDisabled();
      await visibility(page, 'visible');
      await expect.poll(() => entered).toBe(true);
      await expect(send).toBeDisabled();
      expect(f.data.posts).toEqual([]);
      release();
      await expect(send).toBeEnabled();
      expect(await draft.evaluate((node, original) => node === original, original)).toBe(true);
      await expect(draft).toHaveValue('尚未送出，保留這一段。');
      expect(await draft.evaluate((node: HTMLTextAreaElement) => [node.selectionStart, node.selectionEnd])).toEqual([3, 7]);
      expect(f.data.posts).toEqual([]);
      await send.click(); await expect.poll(() => f.data.posts.length).toBe(1);
    } finally {release();}
  });
}

test('a poll started before resume cannot reopen writes or create a second reader', async ({page}) => {
  const f = workspaceFixture(), dispatch = f.dispatch;
  let stage = 0, entered = 0, active = 0, maximum = 0;
  const releases: (() => void)[] = [];
  const holds = [0, 1].map(() => new Promise<void>(resolve => releases.push(resolve)));
  f.dispatch = async input => {
    if (!input.path.startsWith('/events?')) return dispatch(input);
    active++; maximum = Math.max(maximum, active);
    try {if (stage && entered < 2) await holds[entered++]; return await dispatch(input);}
    finally {active--;}
  };
  await mountWorkspace(page, false, f);
  try {
    await page.goto('/dashboard/?workbench=1' + workspaceRoute);
    const send = page.getByRole('button', {name: 'Send', exact: true});
    await expect(send).toBeEnabled();
    stage = 1; await expect.poll(() => entered).toBe(1);
    await visibility(page, 'hidden'); await visibility(page, 'visible');
    releases[0]();
    await expect.poll(() => entered).toBe(2);
    await expect(send).toBeDisabled();
    expect(maximum).toBe(1);
    releases[1](); await expect(send).toBeEnabled();
    expect(f.data.posts).toEqual([]);
  } finally {releases.forEach(release => release());}
});

test('a page restored from the browser cache waits for a fresh central poll', async ({page}) => {
  const f = workspaceFixture(), dispatch = f.dispatch;
  let held = false, entered = false, release!: () => void;
  const gate = new Promise<void>(resolve => {release = resolve;});
  f.dispatch = async input => {
    if (input.path.startsWith('/events?') && held) {entered = true; await gate;}
    return dispatch(input);
  };
  await mountWorkspace(page, false, f);
  try {
    await page.goto('/dashboard/?workbench=1' + workspaceRoute);
    const send = page.getByRole('button', {name: 'Send', exact: true});
    await expect(send).toBeEnabled(); held = true;
    await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true})));
    await expect(send).toBeDisabled(); await expect.poll(() => entered).toBe(true);
    release(); await expect(send).toBeEnabled();
    expect(f.data.posts).toEqual([]);
  } finally {release();}
});

for (const native of [false, true]) test(`foreground access reset masks old facts before a new principal loads ${native ? 'IPC' : 'HTTP'}`, async ({page}) => {
  const f = workspaceFixture(), dispatch = f.dispatch;
  let reset = false, reloading = false, bootstrapping = false, release!: () => void;
  const bootstrap = new Promise<void>(resolve => {release = resolve;});
  f.dispatch = async input => {
    if (input.path.startsWith('/events?') && reset) {
      reset = false; reloading = true;
      return {status: 409, data: {error: {code: 'EVENT_CURSOR_RESET', reason: 'access_changed'}}};
    }
    if (input.path === '/bootstrap' && reloading) {bootstrapping = true; await bootstrap;}
    return dispatch(input);
  };
  await mountWorkspace(page, native, f);
  try {
    await page.goto('/dashboard/?workbench=1' + workspaceRoute);
    const draft = page.locator('#workspace-message-input textarea');
    await expect(page.getByRole('button', {name: 'Send', exact: true})).toBeEnabled();
    await draft.fill('Only the original principal may see this draft.');
    await visibility(page, 'hidden');
    f.fixture.data.principal = 'another-reader'; reset = true;
    await visibility(page, 'visible');
    await expect.poll(() => bootstrapping).toBe(true);
    await expect(draft).toHaveCount(0);
    await expect(page.locator('#main')).not.toContainText(f.data.session.title);
    release();
    await expect(draft).toHaveValue('');
    expect(await page.evaluate(() => Object.values(localStorage).includes('Only the original principal may see this draft.'))).toBe(true);
    expect(f.data.posts).toEqual([]);
  } finally {release();}
});

test('foreground keeps writes paused until the event source read has settled', async ({page}) => {
  const f = workspaceFixture(), dispatch = f.dispatch;
  let held = false, entered = false, release!: () => void;
  const source = new Promise<void>(resolve => {release = resolve;});
  f.dispatch = async input => {
    if (input.path === '/sessions/demo/search-results' && held) {entered = true; await source;}
    return dispatch(input);
  };
  await mountWorkspace(page, false, f);
  try {
    await page.goto('/dashboard/?workbench=1' + workspaceRoute);
    const send = page.getByRole('button', {name: 'Send', exact: true});
    await expect(send).toBeEnabled();
    await visibility(page, 'hidden'); held = true;
    f.data.events.push({seq: 1, resource_type: 'session', resource_id: 'demo/search-results', kind: 'session.updated'});
    await visibility(page, 'visible');
    await expect.poll(() => entered).toBe(true); await expect(send).toBeDisabled();
    expect(await page.evaluate(() => Object.entries(localStorage).filter(([key]) => key.startsWith('batc.sync.')).map(([, value]) => JSON.parse(value).cursor))).not.toContain(1);
    release(); await expect(send).toBeEnabled();
    await expect.poll(() => page.evaluate(() => Object.entries(localStorage).filter(([key]) => key.startsWith('batc.sync.')).map(([, value]) => JSON.parse(value).cursor))).toContain(1);
    expect(f.data.posts).toEqual([]);
  } finally {release();}
});

for (const native of [false, true]) for (const locale of ['en', 'zh-TW']) {
  test(`resume retires unanswered source reads before replaying the event page ${native ? 'IPC' : 'HTTP'} ${locale}`, async ({page}) => {
    const f = workspaceFixture(locale), dispatch = f.dispatch;
    const releases: (() => void)[] = [];
    const gates = Array.from({length: 3}, () => new Promise<void>(resolve => releases.push(resolve)));
    let stage = 'initial', oldReads = 0, resumedPolls = 0, freshReads = 0;
    f.dispatch = async input => {
      const path = input.path.split('?')[0];
      if (stage === 'resumed' && path === '/events') {resumedPolls++; await gates[1];}
      if (['/sessions/demo/search-results', '/sessions/demo/search-results/messages'].includes(path)) {
        if (stage === 'old') {
          const response = structuredClone(await dispatch(input));
          oldReads++; await gates[0]; return response;
        }
        if (stage === 'fresh') {freshReads++; await gates[2];}
      }
      return dispatch(input);
    };
    await mountWorkspace(page, native, f);
    await page.addInitScript(locale => Object.defineProperty(navigator, 'language', {value: locale}), locale);
    try {
      await page.goto('/dashboard/?workbench=1' + workspaceRoute);
      const draft = page.locator('#workspace-message-input textarea');
      const send = page.getByRole('button', {name: locale === 'en' ? 'Send' : '送出', exact: true});
      await expect(send).toBeEnabled(); await draft.fill('未送出，keep this draft 🐈');
      const original = await draft.elementHandle();
      await draft.focus(); await draft.evaluate((node: HTMLTextAreaElement) => node.setSelectionRange(2, 6, 'backward'));
      stage = 'old';
      f.data.events.push({seq: 1, resource_type: 'session', resource_id: 'demo/search-results', kind: 'session.updated'});
      await expect.poll(() => oldReads).toBe(2);
      await visibility(page, 'hidden');
      stage = 'resumed';
      await visibility(page, 'visible');
      // Neither old response is released. The single reader must retire their
      // listener before it can read and settle the post-resume page.
      await expect.poll(() => resumedPolls).toBe(1);
      await expect(send).toBeDisabled();
      expect(f.data.posts).toEqual([]);
      stage = 'fresh'; releases[1]();
      await expect.poll(() => freshReads).toBe(2);
      await expect(send).toBeDisabled();
      expect(await page.evaluate(() => Object.entries(localStorage).filter(([key]) => key.startsWith('batc.sync.')).map(([, value]) => JSON.parse(value).cursor))).not.toContain(1);
      releases[2](); await expect(send).toBeEnabled();
      await expect.poll(() => page.evaluate(() => Object.entries(localStorage).filter(([key]) => key.startsWith('batc.sync.')).map(([, value]) => JSON.parse(value).cursor))).toContain(1);
      releases[0]();
      expect(await draft.evaluate((node, old) => node === old && document.activeElement === node, original)).toBe(true);
      await expect(draft).toHaveValue('未送出，keep this draft 🐈');
      expect(await draft.evaluate((node: HTMLTextAreaElement) => [node.selectionStart, node.selectionEnd, node.selectionDirection])).toEqual([2, 6, 'backward']);
      expect(f.data.posts).toEqual([]);
      await send.click(); await expect.poll(() => f.data.posts.length).toBe(1);
    } finally {releases.forEach(release => release());}
  });
}
