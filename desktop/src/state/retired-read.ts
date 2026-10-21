// IPC cannot cancel the native read itself. Retire its result, while consuming
// any late rejection; HTTP also receives the signal to abort the actual fetch.
export function retiredRead<T>(pending: Promise<T>, signal: AbortSignal): Promise<T> {
  return new Promise((resolve, reject) => {
    const abort = () => {signal.removeEventListener('abort', abort); reject(new DOMException('Read retired', 'AbortError'));};
    pending.then(value => {signal.removeEventListener('abort', abort); resolve(value);},
      error => {signal.removeEventListener('abort', abort); reject(error);});
    if (signal.aborted) abort();
    else signal.addEventListener('abort', abort, {once: true});
  });
}
