// Cosmetic return locations only. Session identities remain exact central IDs.
const projectViews = new Set(['overview', 'discussions', 'requirements', 'schedule', 'dependencies', 'issues', 'agents', 'board']);
export function sessionHref({host, sessionId, from = ''}) {
  const query = projectViews.has(from) ? '?' + new URLSearchParams({from}) : '';
  return `#/session/${encodeURIComponent(host)}/${encodeURIComponent(sessionId)}${query}`;
}
export function sessionOrigin(value) {
  if (typeof value !== 'string' || value.length > 4096) return '';
  const match = /^#\/session\/([^/?#]+)\/([^/?#]+)(?:\?([^#]*))?$/.exec(value);
  if (!match) return '';
  try {
    const host = decodeURIComponent(match[1]), sessionId = decodeURIComponent(match[2]);
    if (!host || !sessionId || /[\u0000-\u001f\u007f]/.test(host + sessionId)) return '';
    const query = new URLSearchParams(match[3] || '');
    if ([...query.keys()].some(key => key !== 'from') || query.getAll('from').length > 1) return '';
    const from = query.get('from') || '';
    if (from && !projectViews.has(from)) return '';
    return sessionHref({host, sessionId, from});
  } catch {return '';}
}
