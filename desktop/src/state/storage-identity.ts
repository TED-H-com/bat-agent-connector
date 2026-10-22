import {storageScope} from './events.ts';

export interface StorageIdentity {server_id: string; principal_id: string; actor: string}

// A managed server_id is the persistent journal identity, including across runtime
// upgrades. The native bridge verifies this tuple; an ephemeral port is transport.
export function managedStorageScope(identity: StorageIdentity): string {
  return ['managed', 'v1', identity.server_id, identity.principal_id, identity.actor].map(encodeURIComponent).join(':');
}

function keys(storage: Storage): string[] {
  return Array.from({length: storage.length}, (_, index) => storage.key(index)!).filter(Boolean);
}

// Namespace-bearing keys share this envelope, including nested PM selector
// prefixes and form suffixes. Globals, credentials and Fleet bindings do not.
export function rebase(key: string, source: string, target: string): string | null {
  const prefix = key.match(/^(batc\.[a-z.-]+\.)(?=https?%3A%2F%2F|managed:v1:)/)?.[1];
  if (!prefix) return null;
  const rest = key.slice(prefix.length);
  return rest === source || rest.startsWith(source + '.') ? prefix + target + rest.slice(source.length) : null;
}

export function legacyIdentity(scope: string): StorageIdentity | null {
  try {
    const parts = scope.split(':');
    if (parts.length !== 4) return null;
    const [endpoint, server_id, principal_id, actor] = parts.map(decodeURIComponent);
    const url = new URL(endpoint);
    if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)
      || !url.port || url.username || url.password || url.pathname !== '/' || url.search || url.hash
      || scope !== storageScope(endpoint, actor, server_id, principal_id)) return null;
    return {server_id, principal_id, actor};
  } catch {return null;}
}

// Call only after native managed bootstrap verification, before any view mounts
// or checkpoint is saved. Copy one complete scope; never merge individual forms.
export function migrateManagedStorage(identity: StorageIdentity, local: Storage, session: Storage): void {
  const target = managedStorageScope(identity), marker = `batc.storage-migration.${target}`;
  const receipt = local.getItem(marker);
  if (receipt !== null) {
    const saved = JSON.parse(receipt);
    if (saved?.version !== 1 || saved.target !== target || saved.status !== 'complete')
      throw Error('Local storage migration is incomplete; original data is retained');
    return; // Never resurrect a draft/key that has since been consumed or removed.
  }
  const areas = [local, session], allKeys = areas.map(keys);
  if (allKeys.some(list => list.some(key => rebase(key, target, target) !== null))) return;
  // The exact checkpoint key delimits all four old identity fields. A draft's
  // action/workspace suffix alone cannot prove where its actor field ends.
  const candidates = allKeys[0].filter(key => key.startsWith('batc.sync.')).map(key => key.slice('batc.sync.'.length));
  const matching = candidates.filter(scope => {
    const old = legacyIdentity(scope);
    return old?.server_id === identity.server_id && old.principal_id === identity.principal_id && old.actor === identity.actor;
  });
  if (matching.length !== 1) return;
  const source = matching[0];
  // An orphan draft or tab preference from another port is still another scope,
  // even if that port never saved a checkpoint. Its actor suffix may be ambiguous;
  // that is evidence to leave everything in place, never to choose the other port.
  for (const key of allKeys.flat()) {
    const endpoint = key.match(/^batc\.[a-z.-]+\.(https?%3A%2F%2F[^:]+):/)?.[1];
    if (!endpoint) continue;
    let possible;
    try {possible = storageScope(decodeURIComponent(endpoint), identity.actor, identity.server_id, identity.principal_id);}
    catch {continue;}
    if (possible !== source && legacyIdentity(possible) && rebase(key, possible, possible) !== null) return;
  }
  // Ambiguous prefix boundaries must not adopt another actor's dotted suffix.
  if (candidates.some(scope => scope !== source && (scope.startsWith(source + '.') || source.startsWith(scope + '.')))) return;
  const copies = areas.flatMap((storage, area) => allKeys[area].flatMap(key => {
    const destination = rebase(key, source, target), value = storage.getItem(key);
    return destination !== null && value !== null ? [{storage, destination, value}] : [];
  }));
  // Unrecognized legacy key envelopes remain untouched, without a false receipt.
  if (!copies.length) return;
  const record = {version: 1, source, target, status: 'copying'};
  // A crash can leave a partial copy. The durable marker makes a later startup
  // refuse that destination rather than read a draft without its operation key.
  local.setItem(marker, JSON.stringify(record));
  const written: typeof copies = [];
  try {
    for (const copy of copies) {
      if (copy.storage.getItem(copy.destination) !== null) throw Error('Local storage migration destination changed');
      copy.storage.setItem(copy.destination, copy.value); written.push(copy);
    }
    local.setItem(marker, JSON.stringify({...record, status: 'complete'}));
  } catch (error) {
    // Only undo our own exact copies. Retain the marker if rollback itself fails.
    for (const copy of written.reverse()) {
      if (copy.storage.getItem(copy.destination) !== copy.value)
        throw Error('Local storage migration destination changed; original data is retained');
      copy.storage.removeItem(copy.destination);
    }
    local.removeItem(marker);
    throw error;
  }
}
