// A shared, read-only board. Columns follow central management state, never execution success.
import {qualityFact} from './pm-quality-projections.js';
import {kanbanObservation} from './pm-kanban-observation.js';
export function workspaceKanban({h, t, api, alive, workspaceId, errorBox, caps, permitted, form, projectId = null, pageSize = 20}) {
  const endpoint = `/pm/workspaces/${encodeURIComponent(workspaceId)}/kanban`;
  const integer = value => Number.isSafeInteger(value) && value >= 0;
  if (!integer(pageSize) || pageSize < 1 || pageSize > 100) throw Error('Invalid Kanban page size');
  let serial = 0, next = null, reading = false, rows = new Map(), page = null;
  const observers=new Map(),operationId=value=>/^op_[0-9a-f]{32}$/.test(value||'');
  function completion(attempt){
    const value=attempt.runtime_completion;
    if(value==null)return null;
    if(value.runtime_status!=='original_turn_completed'||value.process_death_proven!==false
      ||!operationId(value.observation_operation_id)||!operationId(value.execution_operation_id)
      ||value.execution_operation_id!==attempt.operation_id||!/^[0-9a-f]{64}$/.test(value.document_digest||'')
      ||!Number.isFinite(value.recorded_at))throw Error(t('pm_kanban_scope_error'));
    return value;
  }
  const status = h('div', {role: 'status'}), metrics = h('div', {class: 'pm-kanban-summary'}), list = h('div', {class: 'pm-kanban-list'});
  const more = h('button', {type: 'button', class: 'secondary', hidden: true, onclick: () => void loadMore()}, t('pm_kanban_more'));
  const refreshButton = h('button', {type: 'button', class: 'secondary', onclick: () => void refresh()}, t('pm_kanban_refresh'));
  const columns = ['backlog', 'in_progress', 'waiting', 'review', 'accepted'];
  const boardInfo = h('div');
  const info = h('details', {class: 'pm-kanban-info'}, h('summary', {}, t('pm_kanban_board_details')), boardInfo,
    h('p', {class: 'pm-kanban-note'}, t('pm_kanban_wip_basis')),
    h('p', {class: 'pm-kanban-note'}, t('pm_kanban_quality')),
    h('p', {class: 'pm-kanban-note'}, t('pm_kanban_limits')));
  const element = h('section', {class: 'panel pm-kanban', 'data-pm-kanban': ''},
    h('header', {class: 'pm-heading'}, h('h2', {}, t('pm_kanban_title')), refreshButton),
    status, metrics, list, h('footer', {class: 'pm-kanban-footer'}, more, info));
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
    for(const attempt of card.execution?.attempts||[])completion(attempt);
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
    for(const observer of observers.values())observer.withdraw();
    list.replaceChildren(); metrics.replaceChildren(); boardInfo.replaceChildren(); more.hidden = true; more.disabled = false;
    element.removeAttribute('aria-busy');
    status.replaceChildren(errorBox ? errorBox(error) : h('p', {class: 'error'}, error.message),
      h('button', {type: 'button', class: 'secondary', onclick: () => void refresh()}, t('pm_kanban_retry')));
  }
  function renderMetrics() {
    metrics.replaceChildren();
    if (!page) return;
    metrics.append(note('pm_kanban_loaded', {count: rows.size}));
    boardInfo.replaceChildren(note('pm_kanban_wip', page.wip), h('p', {}, t('pm_kanban_page_at'), ': ', stamp(page.observed_at)));
    if (next != null) metrics.append(note('pm_kanban_partial'));
    if (page.coverage?.byte_limited) metrics.append(note('pm_kanban_bounded'));
    more.hidden = next == null; more.disabled = reading;
  }
  function detailsFor(card, observedAt) {
    const execution = card.execution || {}, capture = card.latest_capture;
    const lines = (kind, records, content) => records?.length ? h('ul', {}, ...records.map(value => h('li', {},
      h('strong', {}, t(kind)), ' · ', content(value)))) : '';
    const flow = card.flow || {};
    const detail = h('div', {class: 'pm-kanban-detail'},
      card.quality?.selected_assessment ? qualityFact({h,t,fact:card.quality,current:true}) : note('pm_kanban_quality_unknown'),
      h('p', {}, t(card.completion?.approved ? 'pm_kanban_cycle' : 'pm_kanban_age'), ': ', elapsed(card.completion?.approved ? flow.cycle_seconds : flow.age_seconds)),
      flow.history !== 'available_central_events' ? note('pm_kanban_history_unknown') : '',
      h('h4', {}, t('pm_kanban_execution')),
      note('pm_kanban_attempts', {count: integer(execution.accepted_attempts) ? execution.accepted_attempts : t('pm_kanban_unknown')}),
      h('p', {class: 'pm-kanban-note'}, t('pm_kanban_read_at'), ': ', stamp(observedAt)), note('pm_kanban_saved_only'),
      ...notices(execution.coverage), note('pm_kanban_attempt_basis'),
      lines('pm_kanban_task', execution.tasks, value => h('span', {}, state(value.state), code(value.task_id),
        h('span', {class: 'pm-kanban-note'}, t('pm_kanban_source_time'), ': ', stamp(value.observed_at)))),
      lines('pm_kanban_operation', execution.operations, value => h('span', {}, state(value.status), code(value.operation_id),
        h('span', {class: 'pm-kanban-note'}, t('pm_kanban_source_time'), ': ', stamp(value.observed_at)))),
      lines('pm_kanban_session', execution.sessions, value => h('span', {}, value.runtime_status ? state(value.runtime_status) : t('pm_kanban_stream_unknown'), code(value.resource_id),
        h('span', {class: 'pm-kanban-note'}, t('pm_kanban_source_time'), ': ', stamp(value.observed_at)))),
      lines('pm_kanban_attempt', execution.attempts, value => h('span', {}, t(value.accepted === true ? 'pm_kanban_accepted' : 'pm_kanban_accept_unknown'), ' · ', state(value.status),
        code(value.attempt_id), h('span', {class: 'pm-kanban-note'}, t('pm_kanban_source_time'), ': ', stamp(value.observed_at)),runtimeReceipt(value))),
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
    if(form&&caps?.()?.actions?.some(row=>row.action==='pm.session.quiescence.observe')){
      let observer=observers.get(card.work_item_id);
      if(!observer){observer=kanbanObservation({h,t,api,alive,workspaceId,workId:card.work_item_id,caps,permitted,form,errorBox,onSourceFailure:unavailable});observers.set(card.work_item_id,observer);}
      observer.update(execution.manual_observation_sources??[]);detail.append(observer.box);
    }
    return detail;
  }
  function runtimeReceipt(attempt){
    const value=completion(attempt);if(!value)return '';
    return h('div',{'data-runtime-completion':value.execution_operation_id},h('p',{},t('pm_kanban_runtime_completed')),
      note('pm_kanban_runtime_limits'),h('p',{},t('pm_kanban_runtime_recorded_at'),': ',stamp(value.recorded_at)),
      h('a',{href:`#/op/${encodeURIComponent(value.observation_operation_id)}`},t('pm_kanban_runtime_receipt')),
      code(value.observation_operation_id),code(value.document_digest));
  }
  function previewFor(card, observedAt) {
    const flow = card.flow || {}, coverage = card.execution?.coverage || {};
    const age = card.completion?.approved ? flow.cycle_seconds : flow.age_seconds;
    return h('div', {class: 'pm-kanban-overview'},
      h('h3', {}, h('a', {href: `#/work/${encodeURIComponent(card.work_item_id)}`}, card.work.title)),
      h('p', {class: 'pm-kanban-stage'}, card.column === 'accepted' ? t('pm_kanban_content_approved') : state(card.column)),
      h('div', {class: 'pm-kanban-card-meta'},
        h('span', {}, card.quality?.selected_assessment ? t(card.quality.qualified ? 'pq_qualified' : 'pq_not_qualified') : t('pm_kanban_quality_unknown')),
        typeof age === 'number' && age >= 0 ? h('span', {title: t(card.completion?.approved ? 'pm_kanban_cycle' : 'pm_kanban_age')}, elapsed(age)) : ''),
      card.execution?.attempts?.some(attempt=>completion(attempt))?note('pm_kanban_runtime_recorded'):'',
      coverage.unavailable_sources || coverage.inaccessible_sources || coverage.truncated ? note('pm_kanban_partial_sources') : '');
  }
  function renderCard(card, observedAt) {
    const ticket = serial, preview = h('div', {}, previewFor(card, observedAt));
    const node = h('article', {class: 'pm-kanban-card', 'data-pm-kanban-work': card.work_item_id}, preview);
    const body = h('div'), fold = h('details', {}, h('summary', {}, t('pm_kanban_detail')), body);
    let detailRequest = 0;
    fold.addEventListener('toggle', async () => {
      const request = ++detailRequest;
      if (!fold.open) {body.replaceChildren();observers.get(card.work_item_id)?.withdraw();return;}
      body.replaceChildren(note('pm_kanban_detail_loading'));
      try {
        const data = await api('GET', `${endpoint}/${encodeURIComponent(card.work_item_id)}`); alive();
        if (ticket !== serial || request !== detailRequest || !fold.open || !node.isConnected) return;
        if (data.workspace_id !== workspaceId) throw Error(t('pm_kanban_scope_error'));
        checkCard(data.card, card.work_item_id);
        const contents = detailsFor(data.card, data.observed_at);
        preview.replaceChildren(previewFor(data.card, data.observed_at)); body.replaceChildren(contents);
        rows.set(card.work_item_id, {node, card: data.card}); renderColumns();
      } catch (error) {if (ticket === serial && request === detailRequest && node.isConnected) unavailable(error);}
    });
    node.append(fold); return node;
  }
  function renderColumns() {
    const groups = new Map(columns.map(column => [column, []]));
    for (const row of rows.values()) {
      const column = [...columns, 'archived'].includes(row.card.column) ? row.card.column : 'unknown';
      if (!groups.has(column)) groups.set(column, []);
      groups.get(column).push(row.node);
    }
    // Reuse column containers: an expanded card keeps its focused disclosure and original read.
    for (const [column, cards] of groups) {
      let lane = [...list.children].find(node => node.dataset.pmKanbanColumn === column);
      if (!lane) {
        lane = h('section', {class: 'pm-kanban-column', 'data-pm-kanban-column': column,
          'aria-label': column === 'accepted' ? t('pm_kanban_content_approved') : state(column)},
          h('header', {}, h('h3', {}, column === 'accepted' ? t('pm_kanban_content_approved') : state(column)), h('span', {class: 'pm-kanban-count'})),
          h('div', {class: 'pm-kanban-cards'}));
        list.append(lane);
      }
      lane.querySelector('.pm-kanban-count').textContent = cards.length;
      const holder = lane.querySelector('.pm-kanban-cards');
      for (const card of cards) if (card.parentNode !== holder) holder.append(card);
    }
    for (const lane of [...list.children]) if (!groups.has(lane.dataset.pmKanbanColumn)) lane.remove();
  }
  function accept(data, append) {
    if (!append) {rows.clear(); list.replaceChildren();}
    for (const card of data.cards) {
      const previous = rows.get(card.work_item_id), node = renderCard(card, data.observed_at);
      if (previous) previous.node.replaceWith(node);
      rows.set(card.work_item_id, {node, card});
    }
    page = data; next = data.next_cursor ?? null;
    renderColumns();
    if (!rows.size) status.replaceChildren(note('pm_kanban_empty'));
    else status.replaceChildren();
    renderMetrics();
  }
  const path = after => `${endpoint}?${new URLSearchParams({...(projectId ? {project_id: projectId} : {}), after: String(after), limit: String(pageSize)})}`;
  async function refresh() {
    const ticket = ++serial;
    for(const observer of observers.values())observer.withdraw();
    reading = true; page = null; next = null; rows.clear(); list.replaceChildren(); metrics.replaceChildren(); boardInfo.replaceChildren(); more.hidden = true;
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
