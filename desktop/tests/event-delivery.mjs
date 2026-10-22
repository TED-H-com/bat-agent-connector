import {expect} from '@playwright/test';

const gates = new WeakMap();
const deferred = () => {
  let resolve;
  const promise = new Promise(done => {resolve = done;});
  return {promise, resolve};
};

// The shared reader requests its next page only after the previous page's
// listeners have settled. Holding that request establishes a quiet point and
// prevents another refresh until the test explicitly releases delivery.
export function eventDelivery(page, {initiallyPaused = false} = {}) {
  if (gates.has(page)) return gates.get(page);
  let paused = initiallyPaused, held = null, arrival = deferred(), navigation = 0, posts = 0;
  const outstanding = new Set();
  let readyWhen = null;
  page.on('request', request => {
    if (request.method() === 'POST' && new URL(request.url()).pathname.startsWith('/api/v1/')) posts++;
  });
  page.on('framenavigated', frame => {if (frame === page.mainFrame()) navigation++;});
  const through = async proceed => {
    if (!paused || readyWhen && !await readyWhen()) return proceed();
    const entry = {release: deferred(), navigation};
    outstanding.add(entry); held = entry; arrival.resolve(entry);
    await entry.release.promise;
    outstanding.delete(entry);
    if (held === entry) {held = null; arrival = deferred();}
    return proceed();
  };
  const gate = {
    through,
    forward(input, dispatch) {
      if (input.method === 'POST') posts++;
      return new URL(input.path, 'http://fixture').pathname === '/events'
        ? through(() => dispatch(input)) : dispatch(input);
    },
    async pause(ready = null) {
      paused = true; readyWhen = ready;
      // A held poll from before navigation cannot prove this view is ready.
      // Let it finish once; the next request belongs to the settled new view.
      if (held && held.navigation !== navigation) {
        const old = held; held = null; arrival = deferred(); old.release.resolve();
      }
      if (!held) await arrival.promise;
    },
    resume() {
      paused = false; readyWhen = null;
      held = null; arrival = deferred();
      for (const entry of outstanding) entry.release.resolve();
    },
    async post(action, {resume = true} = {}) {
      // Control readiness includes the initial view reads. The poll fence then
      // prevents a new background refresh between readiness and the one click.
      if (held && held.navigation !== navigation) gate.resume();
      let ready = null;
      if (typeof action !== 'function') {
        const control = action;
        await expect(control).toBeEnabled();
        // A failed page can close the gate before its next retry. Let the
        // existing reader finish recovery before holding a writable boundary.
        ready = async () => await control.count() === 1 && await control.isEnabled();
        action = () => control.click();
      }
      await gate.pause(ready);
      const before = posts;
      try {await action(); await expect.poll(() => posts).toBeGreaterThan(before);}
      finally {if (resume) gate.resume();}
    },
  };
  page.on('close', () => gate.resume());
  gates.set(page, gate);
  return gate;
}

export async function httpEventDelivery(page, options) {
  const gate = eventDelivery(page, options);
  await page.route('**/api/v1/events?*', route => gate.through(() => route.fallback()));
  return gate;
}

