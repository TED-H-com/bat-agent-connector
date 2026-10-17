import {disclosure} from './ui/components.js';
export const actionAllowed = (caps, name) => caps?.actions?.some(a => a.action === name && a.allowed === true) === true;
export const mayStartSession = caps => caps?.scopes?.includes('start') && actionAllowed(caps, 'session.start');
export function checkpointReason(caps, host) {
  if (!caps?.features?.checkpoints?.includes(host)) return 'paths_host_unavailable';
  if (!caps?.scopes?.includes('start') || !actionAllowed(caps, 'checkpoint.continue')) return 'paths_start_required';
  if (!actionAllowed(caps, 'checkpoint.create')) return 'paths_checkpoint_required';
  return null;
}
export function dispatchExplanation({h, t}) {
  return disclosure({label: t('paths_how'), body: h('ol', {class: 'work-paths-steps'},
    ...['paths_isolation', 'paths_bat', 'paths_tracking'].map(key => h('li', {}, t(key))))});
}
// Reject protocol-relative URLs, malformed encoding, hidden separators and unknown routes.
export function internalFrom(value) {
  if (typeof value !== 'string' || value.length > 2048 || !value.startsWith('#/')) return null;
  let decoded;
  try {decoded = decodeURIComponent(value);} catch {return null;}
  if (/[\\\s\u0000-\u001f\u007f]/u.test(decoded) || decoded.includes('://') || decoded.includes('//') || decoded.slice(1).includes('#')) return null;
  const path = decoded.split('?')[0];
  if (path.split('/').some(part => part === '.' || part === '..')) return null;
  if(/^#\/pm\/ws_[a-f0-9]{32}\/board$/.test(path))return /^#\/pm\/ws_[a-f0-9]{32}\/board(?:\?select=wi_[a-f0-9]{20})?$/.test(decoded)?value:null;
  if(/^#\/(?:published|dispatch\/[^/?]+(?:\/[^/?]+)?)$/.test(path))return decoded===path?value:null;
  if (!/^#\/(?:pm\/ws_[a-f0-9]{32}\/discussions(?:\/(?:new|[a-zA-Z0-9_-]+))?|sessions|session\/[^/?]+\/[^/?]+)$/.test(path)) return null;
  return value;
}

export function connectionReturn(value) {
  const href=internalFrom(value);if(!href)return null;
  const path=decodeURIComponent(href).split('?')[0];
  const label=path.endsWith('/board')?'wpf_return_work':path==='#/published'?'wpf_return_published'
    :path.startsWith('#/dispatch/')?'wpf_return_dispatch':/^#\/sessions?(?:\/|$)/.test(path)?'wpf_return_session':'agents_back';
  return {href,label};
}
