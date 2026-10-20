// Vite embeds this value in the running code, never in a fetched capabilities read.
// Direct source imports outside Vite have no built identity and report unknown.
export const frontendBuild = Object.freeze({frontend_digest:
  typeof __BATC_FRONTEND_DIGEST__ === 'undefined' ? null : __BATC_FRONTEND_DIGEST__});
