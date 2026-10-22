// Retirement is not leaving a view. Source owners must not swallow it as such.
export const retiredReadError = () => Object.assign(new Error('Read retired during foreground recovery'),
  {name: 'AbortError', code: 'FOREGROUND_READ_RETIRED'});

// IPC cannot cancel the native read itself. Retire its result, while consuming
// any late rejection; HTTP also receives the signal to abort the actual fetch.
export function retiredRead<T>(pending: Promise<T>, signal: AbortSignal): Promise<T> {
  return new Promise((resolve, reject) => {
    const abort = () => {signal.removeEventListener('abort', abort); reject(retiredReadError());};
    pending.then(value => {signal.removeEventListener('abort', abort); resolve(value);},
      error => {signal.removeEventListener('abort', abort); reject(error);});
    if (signal.aborted) abort();
    else signal.addEventListener('abort', abort, {once: true});
  });
}

// Renew only a retired GET, never a transport failure or a write. This owner may
// have no event subscription; its result must settle without an event replay.
export async function readThroughResume<T>(request: (signal: AbortSignal) => Promise<T>,
  currentSignal: () => AbortSignal, guard: () => void): Promise<T> {
  for (;;) {
    guard();
    const signal = currentSignal();
    try {
      const value = await retiredRead(request(signal), signal);
      if (signal.aborted) throw retiredReadError();
      return value;
    } catch (error) {
      if (!signal.aborted) throw error;
      // currentSignal changes before retirement. A new authority is checked
      // before the next request, and the old native reply is never published.
    }
  }
}
