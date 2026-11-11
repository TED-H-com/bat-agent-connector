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
