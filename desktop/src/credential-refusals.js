// Refusals belong to one credential at one endpoint. Store only its digest and
// refusal status; keep the original credential and all work storage untouched.
export const BROWSER_TOKEN_KEY = 'batc.dashboard.token';
export function isCredentialRefusal(status, method, path) {
  return status === 401 || status === 403 && method === 'GET'
    && ['/capabilities', '/bootstrap', '/events'].includes(path.split('?')[0].replace(/\/+$/, ''));
}

export function credentialRefusals({stores = () => [sessionStorage, localStorage], endpoint = () => location.origin} = {}) {
  const memory = new Map(), digests = new Map();
  const keyFor = async token => {
    if (!digests.has(token)) digests.set(token, crypto.subtle.digest('SHA-256', new TextEncoder().encode(token))
      .then(bytes => [...new Uint8Array(bytes)].map(byte => byte.toString(16).padStart(2, '0')).join('')));
    return `batc.credential-refused.${encodeURIComponent(endpoint())}.${await digests.get(token)}`;
  };
  const storage = () => {try {return stores();} catch {return [];}};
  const forget = async token => {
    const key = await keyFor(token);
    memory.delete(key);
    for (const store of storage()) {try {store.removeItem(key);} catch { /* unavailable */ }}
    digests.delete(token);
  };
  return {
    async read(token) {
      const key = await keyFor(token);
      for (const store of storage()) {
        try {
          const value = JSON.parse(store.getItem(key));
          if (value?.status === 401 || value?.status === 403) return {...value, credentialRefused: true};
        } catch { /* The in-memory refusal still pauses this page. */ }
      }
      return memory.get(key) || null;
    },
    async refuse(token, status, method, path) {
      if (!isCredentialRefusal(status, method, path)) return;
      const key = await keyFor(token), value = {status, code: status === 401 ? 'UNAUTHORIZED' : 'FORBIDDEN', credentialRefused: true};
      memory.set(key, value);
      // Prefer the durable token's store when it is also mirrored in this tab.
      // A session-only token must never leave a durable refusal record behind.
      const stores = storage();
      const owner = [...stores].reverse().find(store => {
        try {return store.getItem(BROWSER_TOKEN_KEY) === token;} catch {return false;}
      });
      for (const store of stores) {
        try {
          if (store === owner) store.setItem(key, JSON.stringify(value));
          else store.removeItem(key);
        } catch { /* The current page retains the refusal in memory. */ }
      }
    },
    retry: forget,
    forget,
  };
}

export const browserCredentialRefusals = credentialRefusals();
