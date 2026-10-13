// Refresh the loaded window through central cursors. A shown count is only the loaded page.
import {dataTable, disclosure, emptyState, inlineError, inspector, skeleton, statusPill} from './ui/components.js';
import {joinLabel} from './label-text.js';
import {actionLabel, formatTime, operationDuration, operationMeta, operationStatusKey, operationTargetText, reasonText} from './global-labels.js';
import {viewState} from './ui/view-state.js';

export function operationList({h, t, api, guard, statuses, namespace}) {
  const memory = viewState(namespace || '', '', 'operations:' + (statuses || 'all'));
  let rows = [], before = null, pages = 1, queue = Promise.resolve(), pending = 0, failed = false, query = memory.query || '';
  let sheet = null, request = 0;
  const list = h('div', {'data-operation-list': ''});
  const count = h('p', {class: 'muted', 'data-operation-count': ''});
  const status = h('div', {role: 'status'});
  const inspectorHost = h('div', {class: 'global-ops-inspector'});
  const layout = h('div', {class: 'global-ops'}, list, inspectorHost);
  const current = () => {try {guard(); return true;} catch {return false;}};
  const more = h('button', {class: 'secondary', onclick: () => load(true).catch(() => {})}, t('operations_more'));
  const box = h('div', {}, count, layout, status, h('div', {class: 'actions'}, more));
  const update = () => {more.hidden = before === null; more.disabled = pending > 0 || failed;};
  const visible = () => {
    const needle = query.trim().toLocaleLowerCase();
    if (!needle) return rows;
    return rows.filter(op => [actionLabel(op.action), op.action, op.actor, operationTargetText(op), op.operation_id, op.status, op.status_reason]
      .join('\n').toLocaleLowerCase().includes(needle));
  };
  const columns = [
    {key: 'action', label: t('global_col_action'), render: op => actionLabel(op.action)},
    {key: 'target', label: t('global_col_target'), render: op => operationTargetText(op) || '—'},
    {key: 'status', label: t('global_col_status'), render: op => statusPill(operationStatusKey(op.status))},
    {key: 'actor', label: t('actor'), render: op => op.actor || '—'},
    {key: 'started', label: t('created'), render: op => formatTime(op.created_at) || '—'},
    {key: 'duration', label: t('global_col_duration'), render: op => operationDuration(op) || '—'},
  ];
  function body(op) {
    const steps = Array.isArray(op.steps) ? op.steps : [];
    const precise = t('op_' + op.status);
    return [
      statusPill(operationStatusKey(op.status)),
      h('p', {class: 'muted'}, operationMeta(op, formatTime(op.created_at)) || '—'),
      op.status_reason ? h('p', {}, op.status_reason) : null,
      precise !== 'op_' + op.status ? h('p', {class: 'muted'}, precise) : null,
      disclosure({label: t('global_details'), key: 'operations.' + op.operation_id, body: h('div', {},
        h('dl', {class: 'kv'},
          h('dt', {}, t('global_action_id')), h('dd', {}, h('code', {}, op.action || '—')),
          h('dt', {}, t('global_operation_id')), h('dd', {}, h('code', {}, op.operation_id || '—')),
          h('dt', {}, t('global_col_target')), h('dd', {}, h('code', {}, JSON.stringify(op.target ?? null))),
          op.error_code ? [h('dt', {}, t('global_code')), h('dd', {}, reasonText(op.error_code), ' ', h('code', {}, String(op.error_code)))] : null),
        steps.length ? h('h3', {}, t('steps')) : null,
        ...steps.map(step => h('p', {}, joinLabel([step.name, step.status, step.error?.code]))))}),
    ];
  }
  async function fill(op) {
    const id = op.operation_id;
    const token = ++request;
    if (!sheet?.isConnected || sheet.dataset.operationId !== id) {
      const previous = sheet;
      sheet = null;
      previous?.dispose();
      const node = inspector({title: actionLabel(op.action), onClose: () => {
        if (sheet !== node) return;
        memory.selected = ''; sheet = null; layout.classList.remove('has-inspector');
      }});
      node.dataset.operationId = id;
      sheet = node;
      inspectorHost.append(node);
      layout.classList.add('has-inspector');
    }
    sheet.body.replaceChildren(skeleton(3));
    let record = op;
    try {
      const data = await api('GET', '/operations/' + encodeURIComponent(id));
      if (!current() || token !== request || sheet?.dataset.operationId !== id) return;
      if (data?.operation?.operation_id === id) record = {...op, ...data.operation};
    } catch (error) {
      if (!current() || token !== request || sheet?.dataset.operationId !== id) return;
      if ([403, 404].includes(error.status)) {
        rows = rows.filter(item => item.operation_id !== id);
        sheet.dispose();
        paint();
        return;
      }
      sheet.body.replaceChildren(inlineError({error}), ...body(op).filter(Boolean));
      return;
    }
    if (!current() || token !== request || sheet?.dataset.operationId !== id) return;
    sheet.body.replaceChildren(...body(record).filter(Boolean));
  }
  function paint() {
    const focus = list.contains(document.activeElement) ? document.activeElement.closest('[data-row-key]')?.dataset.rowKey : null;
    const shown = visible();
    if (!rows.length) list.replaceChildren(emptyState({title: t('operations_empty')}));
    else if (!shown.length) list.replaceChildren(emptyState({title: t('global_no_match')}));
    else list.replaceChildren(dataTable({
      columns, rows: shown, rowKey: 'operation_id', selected: memory.selected,
      onSelect: (key, row) => {memory.selected = key; fill(row).catch(() => {});},
    }));
    if (focus) list.querySelector(`[data-row-key="${CSS.escape(focus)}"]`)?.focus({preventScroll: true});
    count.hidden = before === null;
    count.textContent = before === null ? '' : t('global_shown', {count: rows.length});
    if (sheet?.isConnected && memory.selected) {
      const row = rows.find(op => op.operation_id === memory.selected);
      if (!row) sheet.dispose();
    }
  }
  function load(append = false) {
    pending++; update();
    const job = queue.catch(() => {}).then(async () => {
      guard(); if (append && before === null) return;
      const anchor = [...list.querySelectorAll('[data-row-key]')].find(el => el.getBoundingClientRect().bottom >= 0);
      const anchorId = anchor?.dataset.rowKey;
      const top = anchor?.getBoundingClientRect().top;
      let next = append ? before : null, candidate = append ? [...rows] : [], read = 0;
      const targetPages = append ? 1 : pages;
      for (let i = 0; i < targetPages; i++) {
        const params = new URLSearchParams({limit: '100'});
        if (statuses) params.set('status', statuses);
        if (next !== null) params.set('before', String(next));
        const result = await api('GET', '/operations?' + params); guard();
        if (!Array.isArray(result.operations) || result.operations.some(op => !/^op_[0-9a-f]{32}$/.test(op?.operation_id)) ||
            result.next_before !== null && result.next_before !== undefined &&
            (typeof result.next_before !== 'number' || !Number.isFinite(result.next_before) || result.next_before <= 0 ||
              next !== null && result.next_before >= next || result.operations.length === 0)) throw new Error(t('operations_invalid_page'));
        candidate.push(...result.operations); read++; next = result.next_before ?? null;
        if (next === null) break;
      }
      guard(); rows = [...new Map(candidate.map(op => [op.operation_id, op])).values()];
      pages = append ? pages + read : read; before = next; failed = false;
      paint(); status.replaceChildren();
      const restored = anchorId ? list.querySelector(`[data-row-key="${CSS.escape(anchorId)}"]`) : null;
      if (restored && top !== undefined) window.scrollBy(0, restored.getBoundingClientRect().top - top);
      if (memory.selected) {
        const selected = rows.find(op => op.operation_id === memory.selected);
        if (selected && sheet?.isConnected) fill(selected).catch(() => {});
      }
    }).catch(error => {
      if (current()) {failed = true; status.replaceChildren(h('p', {class: 'error'}, error.message));}
      throw error;
    }).finally(() => {pending--; if (current()) update();});
    queue = job; return job;
  }
  update();
  return {box, load, filter(text) {query = memory.query = text || ''; paint();}};
}
