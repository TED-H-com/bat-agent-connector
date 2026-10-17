// Return locations are cosmetic. Work context is only a requested selection;
// callers must validate its public Work/Attempt relationship before using it.
const projectViews = new Set(['overview', 'discussions', 'requirements', 'schedule', 'dependencies', 'issues', 'agents', 'board']);
export function workSessionContext(params) {
  const fields = {workspaceId: 'workspace', workItemId: 'work', attemptId: 'attempt'};
  const patterns = {workspaceId: /^ws_[0-9a-f]{32}$/, workItemId: /^wi_[0-9a-f]{20}$/, attemptId: /^att_[0-9a-f]{32}$/};
  const context = {};
  for (const [key, name] of Object.entries(fields)) {
    if (params.getAll(name).length !== 1 || !patterns[key].test(params.get(name))) return null;
    context[key] = params.get(name);
  }
  return context;
}
export function sessionHref({host, sessionId, from = '', workspaceId, workItemId, attemptId}) {
  const query = new URLSearchParams(projectViews.has(from) ? {from} : {});
  const context = new URLSearchParams({workspace: workspaceId || '', work: workItemId || '', attempt: attemptId || ''});
  if (workSessionContext(context)) for (const [key, value] of context) query.set(key, value);
  return `#/session/${encodeURIComponent(host)}/${encodeURIComponent(sessionId)}${query.size ? '?' + query : ''}`;
}
export function sessionOrigin(value) {
  if (typeof value !== 'string' || value.length > 4096) return '';
  const match = /^#\/session\/([^/?#]+)\/([^/?#]+)(?:\?([^#]*))?$/.exec(value);
  if (!match) return '';
  try {
    const host = decodeURIComponent(match[1]), sessionId = decodeURIComponent(match[2]);
    if (!host || !sessionId || /[\u0000-\u001f\u007f]/.test(host + sessionId)) return '';
    const query = new URLSearchParams(match[3] || '');
    if ([...query.keys()].some(key => !['from', 'workspace', 'work', 'attempt'].includes(key)) || query.getAll('from').length > 1) return '';
    const from = query.get('from') || '';
    if (from && !projectViews.has(from)) return '';
    const context = workSessionContext(query);
    if (['workspace', 'work', 'attempt'].some(key => query.has(key)) && !context) return '';
    return sessionHref({host, sessionId, from, ...context});
  } catch {return '';}
}
