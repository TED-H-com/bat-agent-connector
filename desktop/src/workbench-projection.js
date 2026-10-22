import {sessionFacets, sessionStatus, sessionPill} from './state/sessions.js';

// A presentation over an authorized session envelope, not a new Work authority.
// No path/title joins and no inferred requirement, dependency or queue edges.
export function workbenchProjection(data, record = null) {
  const session = data.session;
  if (!session?.host || !session?.session_id) return null;
  const source = `/sessions/${encodeURIComponent(session.host)}/${encodeURIComponent(session.session_id)}`;
  const href = '#/session/' + [session.host, session.session_id].map(encodeURIComponent).join('/');
  const facets = sessionFacets(session), status = sessionStatus(session), pill = sessionPill(session);
  const state = ['asking_you', 'needs_you'].includes(status) ? 'wait' : status
    || (facets.activity === 'not_streaming' && facets.lifecycle !== 'ended' ? 'idle' : 'unknown');
  const edges = [];
  for (const item of data.work_items || []) {
    if (!item.work_item_id) continue;
    edges.push({key: `item:${item.work_item_id}`, type: 'linked', title: item.title,
      id: item.work_item_id, href: '#/item/' + encodeURIComponent(item.work_item_id), source});
  }
  if (data.started_from?.commit_sha && data.started_from?.source_host && data.started_from?.source_session_id) {
    const from = data.started_from;
    edges.push({key: 'derived:' + from.commit_sha, type: 'derived', title: from.commit_sha.slice(0, 12), id: from.commit_sha,
      href: '#/session/' + [from.source_host, from.source_session_id].map(encodeURIComponent).join('/'), source});
  }
  for (const relation of data.relations_summary || []) {
    if (!relation.execution_id) continue;
    edges.push({key: 'execution:' + relation.execution_id, type: 'execution', title: relation.title,
      id: relation.execution_id, href: '#/task/' + encodeURIComponent(relation.execution_id), source});
  }
  const item = record?.work_item;
  if (record?.derived_from?.work_item_id) {
    const from = record.derived_from;
    edges.push({key: 'item-derived:' + from.work_item_id, type: 'branched', title: from.title, id: from.work_item_id,
      href: '#/item/' + encodeURIComponent(from.work_item_id), source: '/work-items/' + encodeURIComponent(item.work_item_id)});
  }
  return {id: item?.work_item_id || JSON.stringify([session.host, session.session_id]), title: item?.title || session.title,
    goal: item?.goal || item?.request || '', recordState: item?.display_state || item?.completion?.display_state || item?.state || null, external: item?.external || null,
    href: item ? '#/item/' + encodeURIComponent(item.work_item_id) : href, source,
    host: session.host, runtime: session.agent_kind, model: session.model, observedAt: session.observed_at,
    state, pill, facets, managed: session.api_access === 'managed' && session.provenance === 'connector_managed',
    edges: [...new Map(edges.map(edge => [edge.key, edge])).values()]};
}
