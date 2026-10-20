export const frontendDigest = value => typeof value === 'string' && value.length === 64 && /^[a-f0-9]{64}$/.test(value) ? value : null;
// Connector substitutes this fixed 64-byte slot once when serving app.js. Native
// and dev leave it unknown. Capture at module init; later capabilities cannot
// replace the identity of the code already running in this window.
export const frontendBuild = Object.freeze({frontend_digest:
  frontendDigest('__BATC_FRONTEND_DIGEST_PLACEHOLDER______________________________')});
