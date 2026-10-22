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
export function eventDelivery(page) {
  if (gates.has(page)) return gates.get(page);
  let paused = false, held = null, arrival = deferred(), navigation = 0, posts = 0;
  page.on('request', request => {
    if (request.method() === 'POST' && new URL(request.url()).pathname.startsWith('/api/v1/')) posts++;
  });
  page.on('framenavigated', frame => {if (frame === page.mainFrame()) navigation++;});
  const through = async proceed => {
    if (!paused) return proceed();
    const entry = {release: deferred(), navigation};
    held = entry; arrival.resolve(entry);
    await entry.release.promise;
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
    async pause() {
      paused = true;
      // A held poll from before navigation cannot prove this view is ready.
      // Let it finish once; the next request belongs to the settled new view.
      if (held && held.navigation !== navigation) {
        const old = held; held = null; arrival = deferred(); old.release.resolve();
      }
      if (!held) await arrival.promise;
    },
    resume() {
      paused = false;
      if (held) {const old = held; held = null; arrival = deferred(); old.release.resolve();}
    },
    async post(action) {
      await gate.pause();
      const before = posts;
      try {await action(); await expect.poll(() => posts).toBeGreaterThan(before);}
      finally {gate.resume();}
    },
  };
  page.on('close', () => gate.resume());
  gates.set(page, gate);
  return gate;
}

export async function httpEventDelivery(page) {
  const gate = eventDelivery(page);
  await page.route('**/api/v1/events?*', route => gate.through(() => route.fallback()));
  return gate;
}

