// A shared, read-only work list. Status, send acceptance and saved quality evidence remain distinct.
import {qualityFact} from './pm-quality-projections.js';
export function workspaceKanban({h, t, api, alive, workspaceId, errorBox, projectId = null, pageSize = 20}) {
  const endpoint = `/pm/workspaces/${encodeURIComponent(workspaceId)}/kanban`;
  const integer = value => Number.isSafeInteger(value) && value >= 0;
  if (!integer(pageSize) || pageSize < 1 || pageSize > 100) throw Error('Invalid Kanban page size');
  let serial = 0, next = null, reading = false, rows = new Map(), page = null;
  const status = h('div', {role: 'status'}), metrics = h('div', {class: 'pm-kanban-summary'}), list = h('div', {class: 'pm-kanban-list'});
  const more = h('button', {type: 'button', class: 'secondary', hidden: true, onclick: () => void loadMore()}, t('pm_kanban_more'));
  const refreshButton = h('button', {type: 'button', class: 'secondary', onclick: () => void refresh()}, t('pm_kanban_refresh'));
  const element = h('section', {class: 'panel pm-kanban', 'data-pm-kanban': ''},
    h('header', {class: 'pm-heading'}, h('div', {}, h('h2', {}, t('pm_kanban_title')), h('p', {class: 'pm-kanban-note'}, t('pm_kanban_intro'))), refreshButton),
    status, metrics, h('p', {class: 'pm-kanban-note'}, t('pm_kanban_quality')), list,
    h('footer', {class: 'pm-kanban-footer'}, more, h('p', {class: 'pm-kanban-note'}, t('pm_kanban_limits'))));
  const note = (key, args, warning = false) => h('p', {class: warning ? 'pm-kanban-notice' : 'pm-kanban-note'}, t(key, args));
  const code = value => h('code', {class: 'pm-source'}, value || t('pm_kanban_unassigned'));
  const state = value => {
    if (typeof value === 'boolean') return t(value ? 'pm_kanban_yes' : 'pm_kanban_no');
    if (!value) return t('pm_kanban_unknown');
    const key = 'pm_kanban_state_' + value, translated = t(key);
    return translated === key ? String(value).replaceAll('_', ' ') : translated;
  };
  const stamp = value => {
    const date = typeof value === 'number' && Number.isFinite(value) ? new Date(value * 1000) : null;
    if (!date || !Number.isFinite(date.getTime())) return t('pm_kanban_unknown');
    const iso = date.toISOString();
    return h('time', {datetime: iso, title: iso}, iso.replace('T', ' ').replace(/\.\d+Z$/, ' UTC'));
  };
  const elapsed = value => {
    if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return t('pm_kanban_unknown');
    if (value < 60) return t('pm_kanban_seconds');
    const [key, divisor] = value < 3600 ? ['minutes', 60] : value < 86400 ? ['hours', 3600] : ['days', 86400];
    return t('pm_kanban_' + key, {count: Math.floor(value / divisor)});
  };
  function checkCard(card, expectedId = null) {
    if (!card || !/^wi_[0-9a-f]{20}$/.test(card.work_item_id || '') || expectedId && card.work_item_id !== expectedId
      || card.binding?.workspace_id !== workspaceId || card.binding?.work_item_id !== card.work_item_id
      || card.work?.work_item_id !== card.work_item_id || !integer(card.binding.sequence) || !integer(card.work.version)) throw Error(t('pm_kanban_scope_error'));
    if (projectId && card.binding.project_id !== projectId || card.latest_capture && (card.latest_capture.workspace_id !== workspaceId
      || card.latest_capture.work_item_id !== card.work_item_id)) throw Error(t('pm_kanban_scope_error'));
    return card;
  }
  function checkPage(data, after) {
    if (data?.workspace_id !== workspaceId || (data.project_id ?? null) !== projectId || !Array.isArray(data.cards)
      || data.next_cursor != null && (!integer(data.next_cursor) || data.next_cursor <= after)
      || !data.wip || !['count', 'unknown', 'cards'].every(key => integer(data.wip[key])) || data.wip.scope !== 'returned_page') throw Error(t('pm_kanban_scope_error'));
    const seen = new Set();
    for (const card of data.cards) {
      checkCard(card);
      if (card.binding.sequence <= after || seen.has(card.work_item_id)) throw Error(t('pm_kanban_scope_error'));
      seen.add(card.work_item_id);
    }
    if (data.next_cursor != null && (!data.cards.length || data.next_cursor < data.cards.at(-1).binding.sequence)) throw Error(t('pm_kanban_scope_error'));
    return data;
  }
  function notices(coverage) {
    const out = [];
    if (coverage?.unavailable_sources || coverage?.inaccessible_sources) out.push(note('pm_kanban_missing', {}, true));
    if (coverage?.truncated) out.push(note('pm_kanban_truncated', {}, true));
    if (coverage?.unsupported_sources) out.push(note('pm_kanban_unsupported'));
    return out;
  }
  function unavailable(error) {
    ++serial; reading = false; rows.clear(); page = null; next = null;
    list.replaceChildren(); metrics.replaceChildren(); more.hidden = true; more.disabled = false;
    element.removeAttribute('aria-busy');
    status.replaceChildren(errorBox ? errorBox(error) : h('p', {class: 'error'}, error.message),
      h('button', {type: 'button', class: 'secondary', onclick: () => void refresh()}, t('pm_kanban_retry')));
  }
  function renderMetrics() {
    metrics.replaceChildren();
    if (!page) return;
    metrics.append(h('div', {}, h('strong', {}, t('pm_kanban_wip', page.wip)), note('pm_kanban_wip_basis'),
      note('pm_kanban_loaded', {count: rows.size})), h('div', {class: 'pm-kanban-note'}, t('pm_kanban_page_at'), ': ', stamp(page.observed_at)));
    if (next != null) metrics.append(note('pm_kanban_partial'));
    if (page.coverage?.byte_limited) metrics.append(note('pm_kanban_bounded'));
    more.hidden = next == null; more.disabled = reading;
  }
  function detailsFor(card, observedAt) {
    const execution = card.execution || {}, capture = card.latest_capture;
    const lines = (kind, records, content) => records?.length ? h('ul', {}, ...records.map(value => h('li', {},
      h('strong', {}, t(kind)), ' · ', content(value)))) : '';
    const detail = h('div', {class: 'pm-kanban-detail'}, h('h4', {}, t('pm_kanban_execution')),
      h('p', {class: 'pm-kanban-note'}, t('pm_kanban_read_at'), ': ', stamp(observedAt)), note('pm_kanban_saved_only'),
      ...notices(execution.coverage), note('pm_kanban_attempt_basis'),
      lines('pm_kanban_task', execution.tasks, value => h('span', {}, state(value.state), code(value.task_id),
        h('span', {class: 'pm-kanban-note'}, t('pm_kanban_source_time'), ': ', stamp(value.observed_at)))),
      lines('pm_kanban_operation', execution.operations, value => h('span', {}, state(value.status), code(value.operation_id),
        h('span', {class: 'pm-kanban-note'}, t('pm_kanban_source_time'), ': ', stamp(value.observed_at)))),
      lines('pm_kanban_session', execution.sessions, value => h('span', {}, value.runtime_status ? state(value.runtime_status) : t('pm_kanban_stream_unknown'), code(value.resource_id),
        h('span', {class: 'pm-kanban-note'}, t('pm_kanban_source_time'), ': ', stamp(value.observed_at)))),
      lines('pm_kanban_attempt', execution.attempts, value => h('span', {}, t(value.accepted === true ? 'pm_kanban_accepted' : 'pm_kanban_accept_unknown'), ' · ', state(value.status),
        code(value.attempt_id), h('span', {class: 'pm-kanban-note'}, t('pm_kanban_source_time'), ': ', stamp(value.observed_at)))),
      h('h4', {}, t('pm_kanban_capture')));
    if (!capture) detail.append(note('pm_kanban_no_capture'));
    else {
      if (capture.workspace_id !== workspaceId || capture.work_item_id !== card.work_item_id) throw Error(t('pm_kanban_scope_error'));
      detail.append(h('p', {class: 'pm-kanban-note'}, t('pm_kanban_capture_time'), ': ', stamp(capture.captured_at)), code(capture.capture_id),
        code(capture.binding_id), ...notices(capture.coverage));
      if (card.capture_matches_binding === false || card.capture_matches_work_version === false) detail.append(note('pm_kanban_capture_different', {}, true));
      detail.append(h('ul', {}, ...(capture.evidence || []).map(value => {
        if (value.workspace_id && value.workspace_id !== workspaceId) throw Error(t('pm_kanban_scope_error'));
        const sourceKey = 'pm_kanban_source_' + value.source_kind, source = t(sourceKey);
        return h('li', {}, source === sourceKey ? t('pm_kanban_fact') : source, ' · ',
          Object.entries(value.assessment || {}).map(([key, value]) => {
            const fieldKey = 'pm_kanban_field_' + key, field = t(fieldKey);
            return `${field === fieldKey ? key.replaceAll('_', ' ') : field}: ${state(value)}`;
          }).join(' · '), code(value.evidence_id), h('span', {class: 'pm-kanban-note'}, t('pm_kanban_source_time'), ': ', stamp(value.source_observed_at)));
      })));
    }
    detail.append(h('h4', {}, t('pm_kanban_versions')), h('dl', {},
      ...[['pm_kanban_binding', card.binding.binding_id], ['pm_kanban_plan', card.binding.plan_revision_id],
        ['pm_kanban_requirements', (card.binding.requirement_ids || []).join(', ')], ['pm_kanban_work_version', card.work.version],
        ['pm_kanban_bound_version', card.binding.work_version]].flatMap(([key, value]) => [h('dt', {}, t(key)), h('dd', {}, code(value))])),
      note('pm_kanban_assignment'), note('pm_kanban_dependencies'));
    return detail;
  }
  function previewFor(card, observedAt) {
    const execution = card.execution || {}, flow = card.flow || {};
    const states = execution.tasks?.length ? execution.tasks.map(task => task.state)
      : execution.attempts?.length ? execution.attempts.map(attempt => attempt.status)
      : execution.operations?.length ? execution.operations.map(operation => operation.status)
      : execution.sessions?.length ? execution.sessions.map(session => session.runtime_status) : [];
    const summary = [...new Set(states.map(value => state(value)))].join(' · ');
    const node = h('div', {},
      h('div', {class: 'pm-kanban-overview'}, h('div', {class: 'pm-kanban-work'},
        h('h3', {}, h('a', {href: `#/work/${encodeURIComponent(card.work_item_id)}`}, card.work.title)),
        card.quality?.selected_assessment ? qualityFact({h,t,fact:card.quality,current:true}) : note('pm_kanban_quality_unknown')),
      h('div', {class: 'pm-kanban-metric'}, h('strong', {}, card.column === 'accepted' ? t('pm_kanban_content_approved') : state(card.column)),
        h('p', {}, t(card.completion?.approved ? 'pm_kanban_cycle' : 'pm_kanban_age'), ': ', elapsed(card.completion?.approved ? flow.cycle_seconds : flow.age_seconds)),
        card.work.state === 'waiting' ? h('p', {}, t('pm_kanban_wait'), ': ', elapsed(flow.waiting_seconds)) : ''),
      h('div', {class: 'pm-kanban-metric'}, h('strong', {}, t('pm_kanban_attempts', {count: integer(execution.accepted_attempts) ? execution.accepted_attempts : t('pm_kanban_unknown')})),
        h('p', {}, summary || t('pm_kanban_no_execution')))), ...notices(execution.coverage));
    if (flow.history !== 'available_central_events') node.append(note('pm_kanban_history_unknown'));
    node.append(h('p', {class: 'pm-kanban-note'}, t('pm_kanban_read_at'), ': ', stamp(observedAt), ' · ',
      t(card.latest_capture ? 'pm_kanban_capture_time' : 'pm_kanban_no_capture'), card.latest_capture ? ': ' : '', card.latest_capture ? stamp(card.latest_capture.captured_at) : ''));
    return node;
  }
  function renderCard(card, observedAt) {
    const ticket = serial, preview = h('div', {}, previewFor(card, observedAt));
    const node = h('article', {class: 'pm-kanban-card', 'data-pm-kanban-work': card.work_item_id}, preview);
    const body = h('div'), fold = h('details', {}, h('summary', {}, t('pm_kanban_detail')), body);
    let detailRequest = 0;
    fold.addEventListener('toggle', async () => {
      const request = ++detailRequest;
      if (!fold.open) {body.replaceChildren(); return;}
      body.replaceChildren(note('pm_kanban_detail_loading'));
      try {
        const data = await api('GET', `${endpoint}/${encodeURIComponent(card.work_item_id)}`); alive();
        if (ticket !== serial || request !== detailRequest || !fold.open || !node.isConnected) return;
        if (data.workspace_id !== workspaceId) throw Error(t('pm_kanban_scope_error'));
        checkCard(data.card, card.work_item_id);
        const contents = detailsFor(data.card, data.observed_at);
        preview.replaceChildren(previewFor(data.card, data.observed_at)); body.replaceChildren(contents);
      } catch (error) {if (ticket === serial && request === detailRequest && node.isConnected) unavailable(error);}
    });
    node.append(fold); return node;
  }
  function accept(data, append) {
    if (!append) {rows.clear(); list.replaceChildren();}
    for (const card of data.cards) {
      const previous = rows.get(card.work_item_id), node = renderCard(card, data.observed_at);
      if (previous) previous.node.replaceWith(node); else list.append(node);
      rows.set(card.work_item_id, {node, card});
    }
    page = data; next = data.next_cursor ?? null;
    if (!rows.size) list.replaceChildren(note('pm_kanban_empty'));
    status.replaceChildren(); renderMetrics();
  }
  const path = after => `${endpoint}?${new URLSearchParams({...(projectId ? {project_id: projectId} : {}), after: String(after), limit: String(pageSize)})}`;
  async function refresh() {
    const ticket = ++serial;
    reading = true; page = null; next = null; rows.clear(); list.replaceChildren(); metrics.replaceChildren(); more.hidden = true;
    status.replaceChildren(note('pm_kanban_loading')); element.setAttribute('aria-busy', 'true');
    try {const data = checkPage(await api('GET', path(0)), 0); alive(); if (ticket === serial) accept(data, false);}
    catch (error) {if (ticket === serial) unavailable(error);}
    finally {if (ticket === serial) {reading = false; element.removeAttribute('aria-busy'); more.disabled = false;}}
  }
  async function loadMore() {
    if (reading || next == null) return;
    const ticket = serial, after = next;
    reading = true; more.disabled = true;
    try {const data = checkPage(await api('GET', path(after)), after); alive(); if (ticket === serial) accept(data, true);}
    catch (error) {if (ticket === serial) unavailable(error);}
    finally {if (ticket === serial) {reading = false; more.disabled = false;}}
  }
  return {element, box: element, refresh};
}
