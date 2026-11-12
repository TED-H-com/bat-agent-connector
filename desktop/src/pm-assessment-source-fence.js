// Ephemeral invalidation only: central reads remain the sole source authority.
// Release the entry when its last mounted reader leaves; never persist a grant.
const sources = new Map();
export function assessmentSourceFence(selection, onWithdraw) {
  const key = JSON.stringify([selection.partition, selection.workspace_id, selection.review_id]);
  const entry = sources.get(key) || {epoch: 0, readers: new Set()};
  sources.set(key, entry); entry.readers.add(onWithdraw);
  let mounted = true;
  return {
    token: () => entry.epoch,
    current: token => mounted && token === entry.epoch,
    withdraw: error => {if (mounted) {entry.epoch++; for (const notify of [...entry.readers]) notify(error);}},
    release: () => {mounted = false; entry.readers.delete(onWithdraw); if (!entry.readers.size) sources.delete(key);},
  };
}

export const assessmentSourceRefused = error => [401,403,404].includes(error?.status)
  || /(?:^|_)SOURCES?_UNAVAILABLE$/.test(error?.code || '');

// Projection presentations participate even when their exact reader is closed.
// Replacing a projection requires a fresh authorized read; cached renders never
// renew these subscriptions. Losing a reference invalidates all its readers.
export function assessmentPresentations(scope, onWithdraw) {
  const mounted = new Map();
  return {
    replace(references) {
      const ids = new Set(references.map(ref => ref?.review_id).filter(Boolean));
      for (const [id, fence] of mounted) if (!ids.has(id)) {
        fence.withdraw({code:'SOURCE_UNAVAILABLE'}); fence.release(); mounted.delete(id);
      }
      for (const review_id of ids) if (!mounted.has(review_id))
        mounted.set(review_id, assessmentSourceFence({...scope, review_id}, onWithdraw));
    },
    withdraw(error) {for (const fence of mounted.values()) fence.withdraw(error);},
    dispose() {for (const fence of mounted.values()) fence.release(); mounted.clear();},
  };
}
