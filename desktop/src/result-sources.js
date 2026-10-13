import {iconButton, emptyState} from './ui/components.js';
import {timeStamp, timeLabel} from './ui/stamps.js';
const itemHref = item => `#/item/${encodeURIComponent(item.work_item_id)}`;
const artifactHref = artifact => `#/artifact-review/artifact/${encodeURIComponent(artifact.artifact_id)}/${artifact.revision}`;
const date = value => timeStamp(value);
export function resultSourcesPanel({h, t, api, guard, workItemId, linkTarget, onEvents, errorBox}) {
  let doc = null, serial = 0, children = [], refreshing = null;
  const warning = h('div', {'data-child-warning': ''}), status = h('p', {class: 'muted', role: 'status'}), body = h('div');
  const refresh = iconButton({icon:'refresh-cw', label:t('results_refresh'), ...{class: 'mini', type: 'button', onclick: () => load()}});
  const more = h('button', {class: 'secondary', type: 'button', hidden: true, onclick: () => load(doc?.children_next_cursor)}, t('results_more'));
  const details = h('details', {}, h('summary', {}, t('results_title')), status, h('div', {class: 'actions'}, refresh), body, more);
  const box = h('section', {class: 'result-sources panel', 'data-result-sources': ''}, warning, details);
  function renderSource(item, own = false) {
    const content = h('div', {}, h('p', {class: 'muted'}, t('results_completion', {state: t(`wi_state_${item.completion.display_state}`)})));
    if (!item.links.length && !item.result_artifacts.length) content.append(emptyState({title:t('results_empty')}));
    for (const link of item.links) {
      const row = h('div', {class: 'panel'}, linkTarget(link));
      if (link.note) row.append(h('p', {class: 'muted'}, link.note));
      const observation = link.observation;
      if (observation) row.append(h('p', {class: 'muted'}, t('results_activity', {
        state: observation.status !== 'available' || observation.stale || observation.fields_stale || observation.gone_at ? t('obs_unknown')
          : observation.streaming === true ? t('results_streaming') : observation.pending && typeof observation.pending === 'object' ? t('results_waiting')
            : typeof observation.pending === 'number' && observation.pending > 0 ? t('results_pending', {count: observation.pending})
            : observation.streaming === false && (observation.pending === null || observation.pending === 0) ? t('results_no_activity') : t('obs_unknown'),
        time: date(observation.observed_at)})));
      for (const receipt of link.delivered_to || []) row.append(h('p', {class: 'note ok'}, t('results_delivered'), ' ',
        h('a', {href: `https://github.com/${receipt.repository}/pull/${receipt.pull_number}`, target: '_blank', rel: 'noopener'}, `${receipt.repository}#${receipt.pull_number}`),
        ' · ', h('a', {href: `#/op/${encodeURIComponent(receipt.operation_id)}`}, t('results_receipt')), ' · ', h('code', {}, receipt.delivered_sha?.slice(0, 12) || '—')));
      if (link.delivery_truncated) row.append(h('p', {class: 'muted'}, t('results_partial')));
      content.append(row);
    }
    for (const artifact of item.result_artifacts) {
      const row = h('div', {class: 'panel'}, h('a', {href: artifactHref(artifact)}, artifact.display_name || artifact.artifact_id),
        h('p', {class: 'muted'}, `r${artifact.revision} · ${artifact.state || 'unknown'} · ${artifact.media_type || ''}`));
      if (!artifact.available) row.append(h('p', {class: 'note warn'}, t('results_unavailable', {reason: artifact.reason || 'unknown'})));
      row.append(h('details', {}, h('summary', {}, t('results_provenance')), h('code', {}, artifact.digest),
        artifact.source_operation_id ? h('p', {}, h('a', {href: `#/op/${encodeURIComponent(artifact.source_operation_id)}`}, t('results_source_operation'))) : null,
        artifact.source?.kind === 'session' && artifact.source.host && artifact.source.session_id
          ? h('p', {}, h('a', {href: `#/session/${encodeURIComponent(artifact.source.host)}/${encodeURIComponent(artifact.source.session_id)}`}, t('nav_sessions'))) : null));
      const consumers = artifact.recorded_consumers || [];
      row.append(h('p', {class: 'muted'}, t('results_consumers', {count: consumers.length})),
        ...consumers.map(consumer => {
          const href = consumer.owner_kind === 'work_item' ? `#/item/${encodeURIComponent(consumer.owner_id)}`
            : consumer.owner_kind === 'operation' ? `#/op/${encodeURIComponent(consumer.owner_id)}` : null;
          return h('p', {class: 'muted'}, `${consumer.role} · ${consumer.owner_kind} · `, href ? h('a', {href}, consumer.owner_id) : consumer.owner_id);
        }));
      if (artifact.consumers_truncated) row.append(h('p', {class: 'muted'}, t('results_partial')));
      content.append(row);
    }
    if (item.links_truncated || item.artifacts_truncated) content.append(h('p', {class: 'note'}, t('results_partial')));
    return own ? content : h('details', {'data-result-child': item.work_item_id}, h('summary', {}, item.title),
      h('p', {}, h('a', {href: itemHref(item)}, t('results_open_child'))), content);
  }
  function render() {
    const focused = document.activeElement?.getAttribute('href'), opens = new Set([...body.querySelectorAll('details[open][data-result-child]')].map(node => node.dataset.resultChild));
    const unfinished = children.filter(child => !child.archived && !child.completion.approved);
    warning.replaceChildren(...(unfinished.length ? [h('p', {class: 'note warn'}, t('results_unfinished', {count: unfinished.length}), ' ',
      ...unfinished.flatMap((child, index) => [index ? ' · ' : '', h('a', {href: itemHref(child)}, child.title)]))] : []));
    if (doc.children_next_cursor) warning.append(h('p', {class: 'muted'}, t('results_children_partial')));
    body.replaceChildren(h('p', {class: 'muted'}, t('results_independent')),
      ...[doc.parent && ['results_parent', doc.parent], doc.derived_from && ['derived_from', doc.derived_from]].filter(Boolean).map(([label, item]) =>
        h('p', {}, t(label), ': ', h('a', {href: itemHref(item)}, item.title))),
      renderSource(doc.item, true), ...children.map(child => renderSource(child)));
    for (const node of body.querySelectorAll('details[data-result-child]')) node.open = opens.has(node.dataset.resultChild);
    if (focused) [...body.querySelectorAll('a')].find(node => node.getAttribute('href') === focused)?.focus({preventScroll: true});
    status.replaceChildren(timeLabel(t, 'models_source', {source: doc.source, time: date(doc.read_at)}));
    more.hidden = !doc.children_next_cursor;
  }
  function load(after = '') {
    if (refreshing) return refreshing.then(() => load(after));
    refreshing = loadNow(after).finally(() => {refreshing = null;});
    return refreshing;
  }
  async function loadNow(after) {
    const request = ++serial; refresh.disabled = more.disabled = true;
    try {
      const next = await api('GET', `/work-items/${encodeURIComponent(workItemId)}/result-sources?limit=50${after ? `&after=${encodeURIComponent(after)}` : ''}`);
      guard(); if (request !== serial) return;
      if (next.version !== 1 || next.item?.work_item_id !== workItemId || !Array.isArray(next.children)) throw Error(t('results_invalid'));
      doc = next; children = after ? [...new Map([...children, ...next.children].map(child => [child.work_item_id, child])).values()] : next.children;
      render();
    } catch (error) {try {guard(); if (request === serial) {errorBox?.(error); status.textContent = `${t('models_not_refreshed')} ${error.message || error}`;}} catch { /* retired */ }}
    finally {refresh.disabled = more.disabled = false;}
  }
  const off = onEvents(event => ['work_item', 'integration', 'artifact', 'operation', 'session'].includes(event.resource_type) ? load() : undefined);
  load(); return {box, dispose() {serial++; off();}};
}
