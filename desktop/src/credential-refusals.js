import {credentialStorageDigest} from './credential-storage-key.js';
// Refusals belong to one credential at one endpoint. Store only its digest and
// refusal status; keep the original credential and all work storage untouched.
export const BROWSER_TOKEN_KEY = 'batc.dashboard.token';
export function isCredentialRefusal(status, method, path) {
  return status === 401 || status === 403 && method === 'GET'
    && ['/capabilities', '/bootstrap', '/events'].includes(path.split('?')[0].replace(/\/+$/, ''));
}

export function credentialRefusals({stores = () => [sessionStorage, localStorage], endpoint = () => location.origin} = {}) {
  const memory = new Map();
  const identity = token => endpoint() + '\0' + token;
  const storage = () => {try {return stores();} catch {return [];}};
  const prepare = token => {
    if (!token) return;
    const id = identity(token);
    if (!memory.has(id)) memory.set(id, {key: `batc.credential-refused.${encodeURIComponent(endpoint())}.${credentialStorageDigest(token)}`,
      value: null, persisted: false});
    return memory.get(id);
  };
  const forget = token => {
    const entry = prepare(token);
    if (!entry) return;
    entry.value = null; entry.persisted = false;
    for (const store of storage()) {try {store.removeItem(entry.key);} catch { /* unavailable */ }}
  };
  // Preparing a loaded credential is separate from the synchronous request gate.
  for (const store of storage()) {try {prepare(store.getItem(BROWSER_TOKEN_KEY));} catch { /* unavailable */ }}
  return {
    prepare,
    read(token) {
      const entry = memory.get(identity(token));
      if (!entry) return null;
      let ownerReadable = false;
      for (const store of storage()) {
        try {
          const value = JSON.parse(store.getItem(entry.key));
          if (store === entry.owner) ownerReadable = true;
          if (value?.status === 401 || value?.status === 403) {
            entry.persisted = true;
            entry.owner = store;
            return entry.value = {...value, credentialRefused: true};
          }
        } catch { /* The in-memory refusal still pauses this page. */ }
      }
      // Another tab's explicit retry removes the shared record. Do not let a
      // cached copy keep this tab paused after that removal.
      if (ownerReadable && entry.persisted) {entry.value = null; entry.persisted = false;}
      return entry.value;
    },
    refuse(token, status, method, path) {
      if (!token || !isCredentialRefusal(status, method, path)) return;
      const entry = prepare(token), value = {status, code: status === 401 ? 'UNAUTHORIZED' : 'FORBIDDEN', credentialRefused: true};
      entry.value = value;
      const stores = storage();
      const owner = [...stores].reverse().find(store => {
        try {return store.getItem(BROWSER_TOKEN_KEY) === token;} catch {return false;}
      });
      for (const store of stores) {
        try {
          if (store === owner) {store.setItem(entry.key, JSON.stringify(value)); entry.persisted = true; entry.owner = store;}
          else store.removeItem(entry.key);
        } catch { /* The current page retains the refusal in memory. */ }
      }
    },
    storageChanged(event) {
      for (const entry of memory.values()) if (entry.persisted && (!event.key || event.key === entry.key)) {
        entry.value = null; entry.persisted = false;
      }
    },
    retry: forget,
    forget,
  };
}

export const browserCredentialRefusals = credentialRefusals();
