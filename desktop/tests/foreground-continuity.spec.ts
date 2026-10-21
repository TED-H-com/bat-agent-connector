import {test, expect, type Page, type Request} from '@playwright/test';
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

const retiredReplyMarker = 'This event must only appear from a live page.';
const sessionUpdated = (seq: number) => ({seq, resource_type: 'session', resource_id: 'demo/search-results', kind: 'session.updated'});
const eventPage = (cursor: number) => ({status: 200, data: {events: [sessionUpdated(cursor)], next_cursor: cursor,
  head_cursor: cursor, has_more: false, sync: {checkpoint: {cursor, token: 'w-' + cursor}}}});
const savedCheckpoints = (page: Page) => page.evaluate(() => Object.entries(localStorage).filter(([key]) => key.startsWith('batc.sync.')));

for (const native of [false, true]) for (const locale of ['en', 'zh-TW']) {
  test(`a poll started before resume cannot reopen writes or create a second reader ${native ? 'IPC' : 'HTTP'} ${locale}`, async ({page}) => {
    const f = workspaceFixture(locale), dispatch = f.dispatch;
    const releases: (() => void)[] = [];
    const gates = Array.from({length: 5}, () => new Promise<void>(resolve => releases.push(resolve)));
    let releaseSources!: () => void;
    const sourcesGate = new Promise<void>(resolve => {releaseSources = resolve;});
    const polls: {path: string, pending: boolean}[] = [], sources: string[] = [];
    const requests: Request[] = [], live = new Set<Request>(), failures = new Map<Request, string | undefined>();
    const httpTimeline: {event: string, request: number, live: number}[] = [];
    let armed = false, sourcePhase = 'late', livePeak = 0;
    const outstanding = () => polls.filter(poll => poll.pending).length;
    page.on('request', request => {
      if (!request.url().includes('/api/v1/events?')) return;
      requests.push(request); live.add(request); livePeak = Math.max(livePeak, live.size);
      httpTimeline.push({event: 'issued', request: requests.length - 1, live: live.size});
    });
    page.on('requestfinished', request => {
      if (!live.delete(request)) return;
      httpTimeline.push({event: 'finished', request: requests.indexOf(request), live: live.size});
    });
    page.on('requestfailed', request => {
      if (!live.delete(request)) return;
      failures.set(request, request.failure()?.errorText);
      httpTimeline.push({event: 'failed', request: requests.indexOf(request), live: live.size});
    });
    f.dispatch = async input => {
      if (armed && input.path.startsWith('/events?')) {
        const poll = {path: input.path, pending: true}, index = polls.push(poll) - 1;
        try {
          if (gates[index]) await gates[index];
          return index < 3 ? eventPage(91 + index) : index === 3 ? eventPage(1) : dispatch(input);
        } finally {poll.pending = false;}
      }
      const path = input.path.split('?')[0];
      if (armed && ['/sessions/demo/search-results', '/sessions/demo/search-results/messages'].includes(path)) {
        sources.push(path);
        if (sourcePhase === 'fresh') await sourcesGate;
        else {
          const result = structuredClone(await dispatch(input));
          if (path.endsWith('/messages')) result.data.messages = [{id: 'late-reply', role: 'assistant',
            ts: '2026-10-10T12:03:00Z', text: retiredReplyMarker}];
          else result.data.session.title = retiredReplyMarker;
          return result;
        }
      }
      return dispatch(input);
    };
    await mountWorkspace(page, native, f);
    await page.addInitScript(locale => Object.defineProperty(navigator, 'language', {value: locale}), locale);
    if (native) await page.addInitScript(() => {
      // Observe replies at the existing IPC fixture boundary, not app state.
      const boundary = (window as any).__TAURI_INTERNALS__, invoke = boundary.invoke;
      (window as any).__fixtureEventReplies = [];
      boundary.invoke = async (command: string, args: any) => {
        const result = await invoke(command, args);
        if (command === 'connector_request' && args.input.path.startsWith('/events?'))
          (window as any).__fixtureEventReplies.push(result.data.next_cursor);
        return result;
      };
    });
    const lateReplies = () => page.evaluate(() => (window as any).__fixtureEventReplies.filter((cursor: number) => cursor >= 91));
    try {
      await page.goto('/dashboard/?workbench=1' + workspaceRoute);
      const send = page.getByRole('button', {name: locale === 'en' ? 'Send' : '送出', exact: true});
      const draft = page.locator('#workspace-message-input textarea');
      await expect(send).toBeEnabled(); await draft.fill('尚未送出，keep this draft.');
      const before = await savedCheckpoints(page);
      armed = true; await expect.poll(() => polls.length).toBe(1);
      for (let count = 2; count <= 4; count++) {
        const previousOutstanding = outstanding(), previousRequest = requests.at(-1)!;
        await visibility(page, 'hidden'); await visibility(page, 'visible');
        // The replacement arrives while every older fixture handler stays held.
        await expect.poll(() => polls.length).toBe(count);
        if (native) {
          // IPC retires results. A stalled native call ends at the timeout owned
          // by the Bridge client builder in src-tauri/src/bridge.rs.
          expect(outstanding()).toBeLessThanOrEqual(previousOutstanding + 1);
        } else {
          await expect.poll(() => failures.get(previousRequest)).toBe('net::ERR_ABORTED');
          expect(livePeak, JSON.stringify(httpTimeline)).toBe(1); expect(live.size).toBe(1);
        }
        await expect(send).toBeDisabled();
      }
      const beforeLate = await page.locator('#main').innerText();
      releases[0](); releases[1]();
      await expect.poll(() => polls.slice(0, 2).every(poll => !poll.pending)).toBe(true);
      if (native) await expect.poll(lateReplies).toEqual([91, 92]);
      await expect(send).toBeDisabled();
      expect(await savedCheckpoints(page)).toEqual(before);
      expect(await page.locator('#main').innerText()).toBe(beforeLate);
      expect(sources).toEqual([]); expect(polls).toHaveLength(4);
      sourcePhase = 'fresh'; f.data.events.push(sessionUpdated(1)); releases[3]();
      await expect.poll(() => [...new Set(sources)].sort()).toEqual(['/sessions/demo/search-results', '/sessions/demo/search-results/messages']);
      await expect(send).toBeDisabled(); expect(await savedCheckpoints(page)).toEqual(before);
      expect(polls).toHaveLength(4); expect(f.data.posts).toEqual([]);
      releaseSources(); await expect(send).toBeEnabled();
      expect(await savedCheckpoints(page)).toEqual(before.map(([key]) => [key, JSON.stringify({cursor: 1, token: 'w-1'})]));
      // Hold the reader's one normal next page while draining the last old reply.
      await expect.poll(() => polls.length).toBe(5);
      expect(polls.map(poll => new URL(poll.path, 'http://fixture').searchParams.get('after'))).toEqual(['0', '0', '0', '0', '1']);
      const settled = {checkpoint: await savedCheckpoints(page), text: await page.locator('#main').innerText(), sources: sources.length};
      sourcePhase = 'late'; releases[2]();
      await expect.poll(() => polls.slice(0, 4).every(poll => !poll.pending)).toBe(true);
      if (native) await expect.poll(lateReplies).toEqual([91, 92, 93]);
      await expect(send).toBeEnabled();
      expect(await savedCheckpoints(page)).toEqual(settled.checkpoint);
      expect(await page.locator('#main').innerText()).toBe(settled.text);
      await expect(page.locator('#main')).not.toContainText(retiredReplyMarker);
      await expect(draft).toHaveValue('尚未送出，keep this draft.');
      expect(sources).toHaveLength(settled.sources); expect(f.data.posts).toEqual([]);
      expect(polls).toHaveLength(5); expect(outstanding()).toBe(1);
      if (!native) {expect(failures.size).toBe(3); expect(livePeak, JSON.stringify(httpTimeline)).toBe(1); expect(live.size).toBe(1);}
    } finally {releaseSources(); releases.forEach(release => release());}
  });

  test(`a live poll applies the event used to detect a retired reply ${native ? 'IPC' : 'HTTP'} ${locale}`, async ({page}) => {
    const f = workspaceFixture(locale);
    await mountWorkspace(page, native, f);
    await page.addInitScript(locale => Object.defineProperty(navigator, 'language', {value: locale}), locale);
    await page.goto('/dashboard/?workbench=1' + workspaceRoute);
    await expect(page.getByRole('button', {name: locale === 'en' ? 'Send' : '送出', exact: true})).toBeEnabled();
    const before = await savedCheckpoints(page);
    f.data.messages = [{id: 'late-reply', role: 'assistant', ts: '2026-10-10T12:03:00Z', text: retiredReplyMarker}];
    f.data.events.push(sessionUpdated(91));
    await expect(page.locator('#main')).toContainText(retiredReplyMarker);
    await expect.poll(() => savedCheckpoints(page)).toEqual(before.map(([key]) => [key, JSON.stringify({cursor: 91, token: 'w-91'})]));
    expect(f.data.posts).toEqual([]);
  });
}

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
        // Source owners may renew before the resumed event page returns. Hold
        // every resumed source, keeping the same exact two-read barrier.
        if (stage === 'resumed' || stage === 'fresh') {freshReads++; await gates[2];}
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

for (const native of [false, true]) for (const locale of ['en', 'zh-TW']) {
  test(`resume replaces a hung session retry even when the event page is empty ${native ? 'IPC' : 'HTTP'} ${locale}`, async ({page}) => {
    const f = workspaceFixture(locale), dispatch = f.dispatch;
    const releases: (() => void)[] = [];
    const gates = Array.from({length: 3}, () => new Promise<void>(resolve => releases.push(resolve)));
    const sourceReads = new Map<string, number>();
    let stage = 'old', oldReads = 0, resumedPolls = 0, freshReads = 0;
    f.dispatch = async input => {
      const path = input.path.split('?')[0];
      if (stage === 'resumed' && path === '/events') {resumedPolls++; await gates[1];}
      if (['/sessions/demo/search-results', '/sessions/demo/search-results/messages'].includes(path)) {
        const count = (sourceReads.get(path) || 0) + 1; sourceReads.set(path, count);
        if (count === 1) {
          if (path.endsWith('/messages')) return {status: 503, data: {error: {code: 'UNAVAILABLE', message: 'Message read unavailable'}}};
        } else if (stage === 'old') {oldReads++; await gates[0];}
        else if (stage === 'resumed' || stage === 'fresh') {freshReads++; await gates[2];}
      }
      return dispatch(input);
    };
    await mountWorkspace(page, native, f);
    await page.addInitScript(locale => Object.defineProperty(navigator, 'language', {value: locale}), locale);
    try {
      await page.goto('/dashboard/?workbench=1#/session/demo/search-results');
      const send = page.getByRole('button', {name: locale === 'en' ? 'Send' : '送出', exact: true});
      await expect.poll(() => oldReads).toBe(2);
      await visibility(page, 'hidden'); stage = 'resumed'; await visibility(page, 'visible');
      await expect.poll(() => resumedPolls).toBe(1);
      await expect(send).toBeDisabled();
      stage = 'fresh'; releases[1]();
      // The failed read's retry has no unacknowledged event to replay. It must
      // still read fresh sources; its old, unanswered requests stay held.
      await expect.poll(() => freshReads).toBe(2);
      await expect(send).toBeDisabled();
      expect(f.data.posts).toEqual([]);
      releases[2](); await expect(send).toBeEnabled();
      expect(f.data.events).toEqual([]);
      expect(f.data.posts).toEqual([]);
    } finally {releases.forEach(release => release());}
  });
}

for (const native of [false, true]) for (const locale of ['en', 'zh-TW']) {
  test(`resume rereads a failed session before its old backoff expires ${native ? 'IPC' : 'HTTP'} ${locale}`, async ({page}, info) => {
    const f = workspaceFixture(locale), dispatch = f.dispatch;
    const releases: (() => void)[] = [];
    const gates = [0, 1].map(() => new Promise<void>(resolve => releases.push(resolve)));
    let failed = 0, fresh = 0, resumedPolls = 0, eventSources = 0, stage = 'failed';
    f.dispatch = async input => {
      const path = input.path.split('?')[0];
      if (path === '/events' && stage === 'resumed') {resumedPolls++; await gates[0];}
      // Navigation's event read proves the page has reached its listeners before
      // advancing their debounce. It is not an invented event or response shape.
      if (path.startsWith('/projects/') && stage === 'sources') eventSources++;
      if (path === '/sessions/demo/search-results/messages' && stage === 'failed')
        return {status: 503, data: {error: {code: 'UNAVAILABLE', message: `Message read failed ${++failed}`}}};
      if (stage === 'sources' && ['/sessions/demo/search-results', '/sessions/demo/search-results/messages'].includes(path)) {
        fresh++; await gates[1];
      }
      return dispatch(input);
    };
    await page.clock.install();
    await mountWorkspace(page, native, f);
    await page.addInitScript(locale => Object.defineProperty(navigator, 'language', {value: locale}), locale);
    try {
      await page.goto('/dashboard/?workbench=1#/session/demo/search-results');
      const error = page.locator('[data-session-error=UNAVAILABLE] details');
      const send = page.getByRole('button', {name: locale === 'en' ? 'Send' : '送出', exact: true});
      await expect(error).toContainText('Message read failed 1');
      await page.clock.pauseAt(await page.evaluate(() => Date.now()));
      // Exercise the real 1, 2, 4 second retries. The next retry is eight seconds
      // away; the resumed event must refresh now, while that timer is still paused.
      for (const [index, delay] of [1000, 2000, 4000].entries()) {
        await page.clock.runFor(delay);
        await expect(error).toContainText(`Message read failed ${index + 2}`);
      }
      expect(failed).toBe(4);
      await expect(send).toBeDisabled();
      await visibility(page, 'hidden');
      f.data.events.push({seq: 1, resource_type: 'session', resource_id: 'demo/search-results', kind: 'session.updated'});
      stage = 'resumed'; await visibility(page, 'visible');
      await expect.poll(() => resumedPolls).toBe(1);
      stage = 'sources'; releases[0]();
      await expect.poll(() => eventSources).toBeGreaterThan(0);
      await page.clock.runFor(500);
      await expect.poll(() => fresh).toBe(2);
      await expect(send).toBeDisabled();
      expect(f.data.posts).toEqual([]);
      expect(await page.evaluate(() => Object.entries(localStorage).filter(([key]) => key.startsWith('batc.sync.')).map(([, value]) => JSON.parse(value).cursor))).not.toContain(1);
      releases[1]();
      await expect(send).toBeEnabled(); await expect(error).toHaveCount(0);
      await expect.poll(() => page.evaluate(() => Object.entries(localStorage).filter(([key]) => key.startsWith('batc.sync.')).map(([, value]) => JSON.parse(value).cursor))).toContain(1);
      expect(f.data.posts).toEqual([]);
    } finally {
      releases.forEach(release => release());
      await info.attach('retry-reads', {body: JSON.stringify({failed, fresh, resumedPolls, eventSources}), contentType: 'application/json'});
    }
  });
}
