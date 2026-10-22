// Refusals belong to one credential at one endpoint. Store only its digest and
// refusal status; keep the original credential and all work storage untouched.
export function credentialRefusals({stores = () => [sessionStorage, localStorage], endpoint = () => location.origin} = {}) {
  const memory = new Map(), digests = new Map();
  const keyFor = async token => {
    if (!digests.has(token)) digests.set(token, crypto.subtle.digest('SHA-256', new TextEncoder().encode(token))
      .then(bytes => [...new Uint8Array(bytes)].map(byte => byte.toString(16).padStart(2, '0')).join('')));
    return `batc.credential-refused.${encodeURIComponent(endpoint())}.${await digests.get(token)}`;
  };
  const storage = () => {try {return stores();} catch {return [];}};
  return {
    async read(token) {
      const key = await keyFor(token);
      for (const store of storage()) {
        try {
          const value = JSON.parse(store.getItem(key));
          if (value?.status === 401 || value?.status === 403) return value;
        } catch { /* The in-memory refusal still pauses this page. */ }
      }
      return memory.get(key) || null;
    },
    async refuse(token, status) {
      if (status !== 401 && status !== 403) return;
      const key = await keyFor(token), value = {status, code: status === 401 ? 'UNAUTHORIZED' : 'FORBIDDEN'};
      memory.set(key, value);
      for (const store of storage()) {try {store.setItem(key, JSON.stringify(value));} catch { /* unavailable */ }}
    },
    async retry(token) {
      const key = await keyFor(token);
      memory.delete(key);
      for (const store of storage()) {try {store.removeItem(key);} catch { /* unavailable */ }}
    },
  };
}

export const browserCredentialRefusals = credentialRefusals();
