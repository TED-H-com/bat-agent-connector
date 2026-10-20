import {test, expect, type Page, type Locator} from '@playwright/test';
import {readFileSync, writeFileSync} from 'node:fs';
import {followUpFixture, openWork, WS, WI, ATTEMPT} from './work-next-turn-fixture.ts';

// The existing Work journey uses workspaceFixture/mountWorkspace. Its Attempt
// result here comes from the real producer, pinned by test_pm_work_follow_up_golden.py.
const golden = JSON.parse(readFileSync(new URL('../../tests/fixtures/work-follow-up-decisions.json', import.meta.url), 'utf8'));
test.describe.configure({mode: 'parallel'});

const newerAttempt = 'att_' + 'c'.repeat(32);
const draft = '  尚未送出的第三句 — unsent draft\n' + Array.from({length: 36}, (_, i) => `第 ${i + 1} 行：keep this text unchanged.`).join('\n') + '\n  ';
const scenarios = ['bfcache', 'offline', 'ime', 'newer-attempt', 'sequence'] as const;

function deferred() {
  let release!: () => void;
  const promise = new Promise<void>(resolve => {release = resolve;});
  return {promise, release};
}
async function visibility(page: Page, value: 'hidden' | 'visible') {
  await page.evaluate(value => {
    Object.defineProperty(document, 'visibilityState', {configurable: true, value});
    document.dispatchEvent(new Event('visibilitychange'));
  }, value);
}
async function transition(page: Page, type: 'pagehide' | 'pageshow') {
  // Exercise the production bfcache lifecycle handler. This is not proof that a
  // physical Safari page was admitted to its browser's actual back/forward cache.
  await page.evaluate(type => window.dispatchEvent(new PageTransitionEvent(type, {persisted: true})), type);
}
async function frame(page: Page) {
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}
async function snapshot(field: Locator) {
  return field.evaluate((node: HTMLTextAreaElement) => ({
    focused: document.activeElement === node,
    value: node.value,
    selection: [node.selectionStart, node.selectionEnd, node.selectionDirection],
    scrollY: window.scrollY,
    scrollTop: node.scrollTop,
    readerTop: document.querySelector('main.session-view')!.scrollTop,
    stored: Object.entries(localStorage).filter(([key]) => key.startsWith('batc.pm.') && key.includes('.session.send.')),
  }));
}

// Full matrix: no thinning. Both engine transports and both locales see each
// lifecycle condition in portrait and touch-sized landscape, including reflow.
for (const native of [false, true]) for (const lang of ['en', 'zh-TW']) for (const viewport of [{width: 390, height: 844}, {width: 844, height: 390}]) {
  test.describe(`${native ? 'IPC' : 'HTTP'} ${lang} ${viewport.width}x${viewport.height}`, () => {
    test.use({locale: lang === 'en' ? 'en-US' : lang, viewport});
    for (const scenario of scenarios) test(`${scenario}: preserves the mobile draft, focus and original Attempt`, async ({page, context}, testInfo) => {
      const f = followUpFixture(lang, {native});
      // Repeat existing message shapes to make reading position and textarea
      // scrolling meaningful without inline styles (the production CSP applies).
      f.data.messages = Array.from({length: 60}, (_, i) => ({...f.data.messages[i % 2], id: `message-${i}`}));
      const requests: any[] = [], attempts: any[] = [], failures: any[] = [];
      const events = deferred(), sources = deferred(), post = deferred();
      let holding = false, holdingEvents = false, eventReads = 0;
      const sourceReads: string[] = [];
      let offline = false;
      const dispatch = f.dispatch;
      f.dispatch = async input => {
        requests.push(structuredClone(input));
        const path = new URL(input.path, 'http://fixture').pathname;
        if (holdingEvents && path === '/events') {eventReads++; await events.promise;}
        if (holding && input.method === 'GET' && (path === '/sessions/demo/search-results' || path.endsWith('/inputs') || path.endsWith('/result'))) {
          sourceReads.push(input.path); await sources.promise;
        }
        if (path === `/pm/workspaces/${WS}/work-attempts/${ATTEMPT}/result`)
          return {status: 200, data: structuredClone(golden.cases.allowed)};
        return dispatch(input);
      };
      page.on('request', request => {
        if (native || !new URL(request.url()).pathname.startsWith('/api/v1/')) return;
        const url = new URL(request.url());
        attempts.push({method: request.method(), path: url.pathname.slice('/api/v1'.length) + url.search, body: request.postDataJSON(), idempotency_key: request.headers()['idempotency-key']});
      });
      page.on('requestfailed', request => failures.push({url: request.url(), error: request.failure()?.errorText}));
      const operationPosts = () => attempts.filter(row => row.method === 'POST' && row.path === '/operations');
      try {
        await openWork(page, f, lang);
        if (native) {
          attempts.push(...requests.map(row => structuredClone(row)));
          await page.exposeFunction('recordContinuityAttempt', input => {attempts.push(structuredClone(input));});
          // The simulated IPC binding does not use Chromium's network. Put a real
          // same-origin fetch before it so setOffline produces a transport error
          // through apiRequest, rather than synthesizing an offline event/error.
          await page.evaluate(() => {
            const bridge = (window as any).__TAURI_INTERNALS__, invoke = bridge.invoke;
            bridge.invoke = async (command: string, args: any) => {
              if (command === 'connector_request') {
                await (window as any).recordContinuityAttempt(args.input);
                await fetch('/dashboard/index.html?continuity-network=' + (args.input.path.startsWith('/events?') ? 'events' : 'source'), {cache: 'no-store'});
              }
              return invoke(command, args);
            };
          });
        } else {
          // route.fulfill bypasses Chromium's offline switch. While offline,
          // continue to the real network; while online reuse mountWorkspace.
          await page.route('**/api/v1/**', route => offline ? route.continue() : route.fallback());
        }
        const field = page.locator('[data-follow-up-input]');
        const send = page.getByRole('button', {name: lang === 'en' ? 'Send next' : '送出下一步', exact: true});
        await field.fill(draft);
        await expect(send).toBeEnabled();
        await page.getByRole('button', {name: lang === 'en' ? 'Results' : '成果', exact: true}).click();
        const result = page.locator('[data-work-result]');
        await expect(result).toBeVisible();
        const selectedResult = result.locator('[data-work-result-focus="live-output"]');
        const originalHref = await selectedResult.getAttribute('href');
        expect(originalHref).toContain(`attempt=${ATTEMPT}`);
        const originalResult = await result.elementHandle();
        await page.keyboard.press('Escape');
        await field.fill(draft);
        const original = await field.elementHandle();
        await field.evaluate((node: HTMLTextAreaElement) => node.setSelectionRange(3, 19, 'backward'));
        await page.setViewportSize({width: viewport.height, height: viewport.width});
        await frame(page);
        expect(await field.evaluate((node, old) => node === old, original)).toBe(true);
        await expect(field).toBeFocused();
        await expect(field).toHaveValue(draft);
        expect(await field.evaluate((node: HTMLTextAreaElement) => [node.selectionStart, node.selectionEnd, node.selectionDirection])).toEqual([3, 19, 'backward']);
        await page.setViewportSize(viewport);
        await frame(page);
        await field.evaluate((node: HTMLTextAreaElement) => {
          node.scrollTop = 45;
          document.querySelector('main.session-view')!.scrollTop = 75;
        });
        await frame(page);
        const before = await snapshot(field);
        expect(before.focused).toBe(true);
        expect(before.scrollTop).toBeGreaterThan(0);
        expect(before.readerTop).toBeGreaterThan(0);
        expect(before.stored).toHaveLength(1);
        expect(before.stored[0][0]).toContain(`.session.send.${JSON.stringify({workspace_id: WS, work_item_id: WI})}`);
        expect(JSON.parse(before.stored[0][1]).values.text).toBe(draft);
        expect(JSON.parse(before.stored[0][1]).intent).toBeUndefined();
        const unchanged = async () => {
          expect(await field.evaluate((node, old) => node === old, original)).toBe(true);
          expect(await snapshot(field)).toEqual(before);
          expect(await result.evaluate((node, old) => node === old, originalResult)).toBe(true);
          await expect(selectedResult).toHaveAttribute('href', originalHref!);
          expect(operationPosts()).toEqual([]);
          expect(f.follow.items).toEqual([]); // Local text is not a server receipt.
        };
        const composingEnter = async () => {
          await field.evaluate(node => {
            node.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', ctrlKey: true, bubbles: true, cancelable: true, isComposing: true, keyCode: 229}));
            node.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', ctrlKey: true, bubbles: true, cancelable: true, keyCode: 229}));
          });
          expect(operationPosts()).toEqual([]);
        };
        holding = true;
        if (scenario === 'ime') await field.dispatchEvent('compositionstart', {data: '第三句'});
        if (scenario === 'bfcache') await transition(page, 'pagehide');
        else await visibility(page, 'hidden');
        if (scenario !== 'bfcache') await expect(send).toBeDisabled();
        if (scenario === 'offline' || scenario === 'sequence') {
          offline = true; await context.setOffline(true);
          expect(await page.evaluate(() => navigator.onLine)).toBe(false);
          if (scenario === 'offline') {
            await visibility(page, 'visible');
            await expect.poll(() => failures.filter(row => row.url.includes(native ? 'continuity-network=events' : '/api/v1/events?')).length).toBeGreaterThan(0);
            await expect(page.locator('#live')).toHaveAttribute('data-connection', 'reconnecting');
            await expect(page.locator('#live')).toContainText(lang === 'en' ? 'Reconnecting' : '重新連線');
            await expect(send).toBeDisabled();
            await unchanged();
          } else {
            // Hidden polling is intentionally paused. Prove real offline here
            // with the same engine network path, without inventing a hidden poll.
            expect(await page.evaluate(async () => {try {await fetch('/dashboard/index.html?offline-hidden', {cache: 'no-store'}); return false;} catch {return true;}})).toBe(true);
          }
        }
        if (scenario === 'newer-attempt' || scenario === 'sequence') f.follow.latest = newerAttempt;
        f.pushEvent('operation');
        // A real session event forces source/permission refresh before Send.
        f.data.events.push({seq: 2, resource_type: 'session', resource_id: 'demo/search-results', kind: 'session.updated'});
        holdingEvents = true;
        if (scenario === 'offline' || scenario === 'sequence') {offline = false; await context.setOffline(false);}
        if (scenario === 'bfcache') await transition(page, 'pageshow');
        else await visibility(page, 'visible');
        if (scenario === 'sequence') await transition(page, 'pageshow');
        await expect(send).toBeDisabled();
        if (scenario === 'ime') await composingEnter();
        await expect.poll(() => eventReads).toBeGreaterThan(0);
        await unchanged();
        events.release();
        await expect.poll(() => sourceReads.length).toBeGreaterThan(0);
        await expect(send).toBeDisabled();
        await unchanged();
        sources.release();
        await expect(send).toBeEnabled();
        await expect(page.locator('#live')).toHaveAttribute('data-connection', 'connected');
        await frame(page);
        await unchanged();
        if (scenario === 'ime') {
          await composingEnter();
          await field.dispatchEvent('compositionend', {data: '第三句'});
          await expect(field).toHaveValue(draft);
          await unchanged();
        }
        if (scenario === 'newer-attempt' || scenario === 'sequence') {
          expect(f.follow.latest).toBe(newerAttempt);
          const inputReads = requests.filter(row => row.path.includes(`/work/${WI}/inputs`));
          expect(inputReads.some(row => row.path.includes(`attempt_id=${ATTEMPT}`))).toBe(true);
          expect(requests.filter(row => row.path.includes('/work-attempts/')).every(row => row.path.includes(ATTEMPT))).toBe(true);
        }
        // pmDurableForm creates the operation key only on explicit submission.
        // Hold admission and compare the very first persisted intent to the sole
        // request, including its original target and selected-Attempt precondition.
        f.follow.holdPost = post.promise;
        await send.click();
        await expect.poll(() => operationPosts().length).toBe(1);
        const saved = await page.evaluate(key => JSON.parse(localStorage.getItem(key)!), before.stored[0][0]);
        const sent = operationPosts()[0];
        expect(sent.idempotency_key).toBe(saved.intent.key);
        expect(sent.body).toEqual(saved.intent.request);
        expect(sent.body.target).toEqual({workspace_id: WS, work_item_id: WI});
        expect(sent.body.preconditions).toEqual({expected_attempt_id: ATTEMPT});
        expect(sent.body.params).toEqual({text: draft.trim(), schedule: 'after_current'});
        post.release();
        await expect(page.locator('[data-follow-up-receipt]')).toHaveCount(1);
        expect(operationPosts()).toHaveLength(1);
        expect(f.follow.posts).toHaveLength(1);
        expect(f.follow.errors).toEqual([]);
      } finally {
        events.release(); sources.release(); post.release();
        await context.setOffline(false);
        // All per-case artifacts follow Playwright's outputPath ownership.
        writeFileSync(testInfo.outputPath('continuity-requests.json'), JSON.stringify({scenario, native, lang, viewport, requests, attempts, failures, eventReads, sourceReads, errors: f.follow.errors}, null, 2));
      }
    });
  });
}
