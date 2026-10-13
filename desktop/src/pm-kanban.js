import {h} from './ui/dom.js';
import {pageHeader, iconButton, statusPill, agentMark, contextChip, disclosure, emptyState} from './ui/components.js';
import {icon} from './ui/icons.js';
import {viewState} from './ui/view-state.js';
import {firstLabel, joinLabel, labelSegment} from './label-text.js';
import {workInspector, workStatus, blockedWork, blockerText, workAgents, prGroups, readWorkSources} from './pm-work-inspector.js';

// Central management columns stay separate from execution, quality and delivery.
export function workspaceKanban({t, api, alive, namespace = '', workspaceId, errorBox, projectId = null, pageSize = 20, caps = () => null, embedded = false}) {
  const endpoint = `/pm/workspaces/${encodeURIComponent(workspaceId)}`, preference = viewState(namespace, workspaceId, 'board');
  const integer = value => Number.isSafeInteger(value) && value >= 0;
  if (!integer(pageSize) || pageSize < 1 || pageSize > 100) throw Error('Invalid Kanban page size');
  let serial = 0, detailSerial = 0, next = null, reading = false, rows = new Map(), page = null, sources = new Map(), plan = null, milestones = [], pages = 1;
  let selected = preference.selected || null, active = !embedded, preserveSelection = false, toolItems = () => [], deliveryPages = 1;
  const meta = h('span'), status = h('div', {role: 'status'}), list = h('div', {class: 'pm-kanban-list', 'aria-label': t('ui_board')}), boardInfo = h('div');
  const refreshButton = iconButton({icon: 'refresh-cw', label: t('wv_refresh'), onClick: () => void refresh()});
  const more = h('button', {type: 'button', class: 'ui-button secondary', hidden: true, onclick: () => void loadMore()}, t('pm_kanban_more'));
  const columns = ['backlog', 'in_progress', 'waiting', 'review', 'accepted'];
  const search = h('input', {type: 'search', placeholder: t('ui_search_work'), 'aria-label': t('ui_search_work'), oninput: () => {preference.search = search.value; renderColumns(true);}}); search.value = preference.search || '';
  const requirementFilter = h('select', {'aria-label': t('wv_requirement'), onchange: () => {preference.requirement = requirementFilter.value; renderColumns(true);}});
  const agentFilter = h('select', {'aria-label': t('wv_agent'), onchange: () => {preference.agent = agentFilter.value; renderColumns(true);}});
  const group = h('select', {'aria-label': t('wv_group'), onchange: () => {preference.group = group.value; renderColumns(true);}},
    ...['status', 'milestone'].map(value => h('option', {value}, t('wv_' + value)))); group.value = preference.group || 'status';
  const content = h('div', {class: 'pm-board-content'}, list), layout = h('div', {class: 'pm-work-layout'}, content);
  const element = h('section', {class: 'pm-kanban', 'data-pm-kanban': ''},
    !embedded ? pageHeader({title: t('ui_board'), meta, actions: [refreshButton]}) : null,
    h('div', {class: 'pm-board-toolbar'}, h('label', {class: 'pm-work-search'}, icon('search'), search), requirementFilter, agentFilter, h('label', {}, t('wv_group'), group)),
    status, layout, h('footer', {class: 'pm-kanban-footer'}, more,
      disclosure({label: t('pm_kanban_board_details'), body: h('div', {}, boardInfo, h('p', {}, t('pm_kanban_wip_basis')), h('p', {}, t('pm_kanban_quality')), h('p', {}, t('pm_kanban_limits')))})));
  const detail = workInspector({t, workspaceId, from: 'board', mount: layout, tools: () => toolItems(), onClose: () => {
    detailSerial++; if (!preserveSelection) {selected = null; preference.selected = null; markSelection();}
    requestAnimationFrame(updateEdges);
  }});
  const note = key => h('p', {class: 'ui-meta'}, t(key));
  const elapsed = value => {
    if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return null;
    if (value < 60) return t('pm_kanban_seconds');
    const [key, divisor] = value < 3600 ? ['minutes', 60] : value < 86400 ? ['hours', 3600] : ['days', 86400];
    return t('pm_kanban_' + key, {count: Math.floor(value / divisor)});
  };
  function checkCard(card, expectedId = null) {
    if (!card || !/^wi_[0-9a-f]{20}$/.test(card.work_item_id || '') || expectedId && card.work_item_id !== expectedId
      || card.binding?.workspace_id !== workspaceId || card.binding?.work_item_id !== card.work_item_id || card.work?.work_item_id !== card.work_item_id
      || !integer(card.binding.sequence) || !integer(card.work.version)) throw Error(t('pm_kanban_scope_error'));
    if (projectId && card.binding.project_id !== projectId || card.latest_capture && (card.latest_capture.workspace_id !== workspaceId
      || card.latest_capture.work_item_id !== card.work_item_id)) throw Error(t('pm_kanban_scope_error'));
    for (const item of card.latest_capture?.evidence || []) if (item.workspace_id && item.workspace_id !== workspaceId) throw Error(t('pm_kanban_scope_error'));
    return card;
  }
  function checkPage(data, after) {
    if (data?.workspace_id !== workspaceId || (data.project_id ?? null) !== projectId || !Array.isArray(data.cards)
      || data.next_cursor != null && (!integer(data.next_cursor) || data.next_cursor <= after)
      || !data.wip || !['count', 'unknown', 'cards'].every(key => integer(data.wip[key])) || data.wip.scope !== 'returned_page') throw Error(t('pm_kanban_scope_error'));
    const seen = new Set();
    for (const card of data.cards) {checkCard(card); if (card.binding.sequence <= after || seen.has(card.work_item_id)) throw Error(t('pm_kanban_scope_error')); seen.add(card.work_item_id);}
    if (data.next_cursor != null && (!data.cards.length || data.next_cursor < data.cards.at(-1).binding.sequence)) throw Error(t('pm_kanban_scope_error'));
    return data;
  }
  function closeRetaining() {preserveSelection = true; detail.close(); preserveSelection = false;}
  function withdraw() {
    ++serial; ++detailSerial; reading = false; rows.clear(); sources.clear(); page = null; next = null; plan = null; milestones = [];
    closeRetaining(); list.replaceChildren(); meta.replaceChildren(); boardInfo.replaceChildren(); more.hidden = true; more.disabled = false; element.removeAttribute('aria-busy');
  }
  function unavailable(error) {withdraw(); status.replaceChildren(errorBox ? errorBox(error) : h('p', {class: 'error'}, error.message),
    h('button', {type: 'button', class: 'ui-button secondary', onclick: () => {pages = 1; void refresh();}}, t('pm_kanban_retry')));}
  function requirements(card) {return (plan?.document?.requirements || []).filter(row => card.binding.plan_revision_id === plan.revision_id && card.binding.requirement_ids?.includes(row.id));}
  function fillFilters() {
    const fill = (select, all, options, saved) => {
      const current = select.value || saved || '', values = new Map(options);
      if (current && !values.has(current)) values.set(current, t('ui_status_unknown'));
      const signature = JSON.stringify([...values]);
      if (select.dataset.options !== signature) {select.dataset.options = signature; select.replaceChildren(h('option', {value: ''}, t(all)), ...[...values].map(([value, label]) => h('option', {value}, label)));}
      select.value = current;
    };
    fill(requirementFilter, 'wv_all_requirements', [...rows.values()].flatMap(({card}) => (card.binding.requirement_ids || []).map(id => [id, firstLabel(requirements(card).find(req => req.id === id)?.text, id)])), preference.requirement);
    fill(agentFilter, 'wv_all_agents', [...sources.values()].flatMap(source => workAgents(source).map(agent => [agent, agent])), preference.agent);
  }
  function renderMetrics() {
    if (!page) return;
    const partial = next != null || page.coverage?.byte_limited || [...sources.values()].some(source => source.filtered || source.next != null);
    const blocked = [...rows.values()].filter(({card}) => blockedWork(card)).length;
    meta.textContent = joinLabel([t(partial ? 'wv_loaded' : 'wv_work_count', {n: rows.size}), blocked ? t(blocked === 1 ? 'wv_blocked_one' : 'wv_blocked_count', {n: blocked}) : null]);
    boardInfo.replaceChildren(...[h('p', {}, t('pm_kanban_wip', page.wip)),
      partial ? note('pm_kanban_partial') : null, page.coverage?.byte_limited ? note('pm_kanban_bounded') : null].filter(Boolean));
    more.hidden = next == null; more.disabled = reading;
  }
  function preview(card) {
    const source = sources.get(card.work_item_id), reqs = requirements(card), coverage = card.execution?.coverage || {};
    const chip = text => {const node = contextChip({text}); node.title = text; return node;};
    const age = elapsed(card.completion?.approved ? card.flow?.cycle_seconds : card.flow?.age_seconds);
    return [h('h3', {title: firstLabel(card.work.title, t('ui_work_item'))}, firstLabel(card.work.title, t('ui_work_item'))),
      h('div', {class: 'pm-board-requirements'}, ...(reqs.length ? reqs.map(req => chip(firstLabel(req.text, req.id)))
        : [chip(card.binding.requirement_ids?.length ? card.binding.requirement_ids.map(labelSegment).join(' · ') : t('wv_no_requirement'))])),
      h('div', {class: 'pm-kanban-card-meta'}, ...workAgents(source).map(agentMark).filter(mark => mark.textContent !== '?'),
        ...prGroups(source?.deliveries).map(({target, rows}) => {const node = contextChip({text: `PR #${target.pull_number}`});
          node.title = joinLabel([target.repository, t(rows.some(row => row.merge?.merged === true) ? 'ui_status_merged' : 'ui_status_unknown')]); return node;}), blockedWork(card) ? h('span', {class: 'pm-board-blocker', tabindex: 0, title: blockerText(t, card), 'aria-label': blockerText(t, card)}, icon('circle-alert'), t('ui_status_blocked')) : null,
        group.value !== 'status' ? h('span', {class: 'pm-board-state'}, statusPill(workStatus(card))) : null,
        age ? h('span', {class: 'pm-board-age', title: t(card.completion?.approved ? 'pm_kanban_cycle' : 'pm_kanban_age')}, age) : null),
      coverage.unavailable_sources || coverage.inaccessible_sources || coverage.truncated ? h('span', {class: 'ui-meta'}, t('pm_kanban_partial_sources')) : null].filter(Boolean);
  }
  function makeCard(card) {
    const node = h('article', {class: 'pm-kanban-card', 'data-pm-kanban-work': card.work_item_id, role: 'button', tabindex: 0,
      'aria-label': firstLabel(card.work.title, t('ui_work_item')), onclick: () => {node.focus(); void select(card.work_item_id);}}, ...preview(card));
    node.addEventListener('keydown', event => {
      if (event.target !== node) return;
      if (['Enter', ' '].includes(event.key)) {event.preventDefault(); void select(card.work_item_id);}
      if (['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
        event.preventDefault(); const lane = node.closest('[data-pm-kanban-column]'), cards = [...lane.querySelectorAll('[data-pm-kanban-work]:not([hidden])')], index = cards.indexOf(node);
        let target;
        if (['ArrowLeft', 'ArrowRight'].includes(event.key)) {
          const lanes = [...list.children], next = lanes[lanes.indexOf(lane) + (event.key === 'ArrowLeft' ? -1 : 1)];
          const available = next?.querySelector('.pm-kanban-cards')?.hidden ? [] : [...(next?.querySelectorAll('[data-pm-kanban-work]:not([hidden])') || [])];
          target = available[index] || available[0] || next?.querySelector('button');
        } else target = cards[event.key === 'Home' ? 0 : event.key === 'End' ? cards.length - 1 : index + (event.key === 'ArrowUp' ? -1 : 1)];
        target?.focus({preventScroll: true}); if (target) revealCard(target);
      }
    }); return node;
  }
  const locked = lane => lane && (lane.matches(':hover') || lane.contains(document.activeElement));
  function laneKey(card) {
    if (group.value === 'milestone') return milestones.find(row => row.plan_revision_id === card.binding.plan_revision_id && row.requirement_ids?.some(id => card.binding.requirement_ids?.includes(id)))?.milestone_id || 'unassigned';
    return [...columns, 'archived'].includes(card.column) ? card.column : 'unknown';
  }
  function renderColumns(force = false) {
    const query = search.value.trim().toLocaleLowerCase();
    const groupKeys = group.value === 'status' ? [...columns] : milestones.map(row => row.milestone_id);
    const currentLanes = new Map([...list.children].map(node => [node.dataset.pmKanbanColumn, node]));
    for (const {card} of rows.values()) if (!groupKeys.includes(laneKey(card))) groupKeys.push(laneKey(card));
    for (const key of groupKeys) if (!currentLanes.has(key)) {
      const name = group.value === 'milestone' ? firstLabel(milestones.find(row => row.milestone_id === key)?.title, t('wb_unassigned')) : key === 'accepted' ? t('wv_content_approved') : t('ui_status_' + workStatus({column: key}));
      const holder = h('div', {class: 'pm-kanban-cards'}), collapsed = preference.collapsed?.includes(key) === true;
      const toggle = h('button', {type: 'button', class: 'pm-board-lane-toggle', 'aria-expanded': String(!collapsed), onclick: () => {
        holder.hidden = !holder.hidden; toggle.setAttribute('aria-expanded', String(!holder.hidden));
        preference.collapsed = [...list.children].filter(lane => lane.querySelector('.pm-kanban-cards').hidden).map(lane => lane.dataset.pmKanbanColumn);
      }}, icon('chevron-right'), h('span', {}, name), h('span', {class: 'pm-kanban-count'})); holder.hidden = collapsed;
      const lane = h('section', {class: 'pm-kanban-column', 'data-pm-kanban-column': key, 'aria-label': name}, h('header', {}, toggle), holder);
      lane.addEventListener('pointerleave', () => requestAnimationFrame(() => renderColumns()));
      lane.addEventListener('focusout', () => requestAnimationFrame(() => renderColumns()));
      currentLanes.set(key, lane); list.append(lane);
    }
    for (const entry of rows.values()) {
      const {card, node} = entry;
      if ((entry.pending || force) && (!node.contains(document.activeElement) || node === document.activeElement)) {node.replaceChildren(...preview(card)); entry.pending = false;}
      const matches = (!query || joinLabel([card.work.title, ...requirements(card).map(req => req.text)], ' ').toLocaleLowerCase().includes(query))
        && (!requirementFilter.value || card.binding.requirement_ids?.includes(requirementFilter.value))
        && (!agentFilter.value || workAgents(sources.get(card.work_item_id)).includes(agentFilter.value));
      node.hidden = !matches;
      const lane = currentLanes.get(laneKey(card)), previous = node.closest('[data-pm-kanban-column]');
      if (previous !== lane && (force || !locked(previous) && !locked(lane))) lane.querySelector('.pm-kanban-cards').append(node);
      else if (!previous) lane.querySelector('.pm-kanban-cards').append(node);
    }
    for (const [key, lane] of currentLanes) {
      if (!groupKeys.includes(key) && !lane.querySelector('[data-pm-kanban-work]')) lane.remove();
      else lane.querySelector('.pm-kanban-count').textContent = lane.querySelectorAll('[data-pm-kanban-work]:not([hidden])').length;
    }
    markSelection();
    requestAnimationFrame(updateEdges);
  }
  function markSelection() {for (const {node, card} of rows.values()) node.setAttribute('aria-pressed', String(selected === card.work_item_id));}
  function present(data, append = false) {
    const ids = new Set(data.cards.map(card => card.work_item_id));
    if (!append) for (const [id, {node}] of rows) if (!ids.has(id)) {node.remove(); rows.delete(id);}
    for (const card of data.cards) {
      const previous = rows.get(card.work_item_id);
      if (previous) {
        // Preserve the card node and focus, even when its facts change.
        const contentSignature = JSON.stringify([card.work.title, card.column, card.flow?.age_seconds, card.flow?.cycle_seconds, blockedWork(card), sources.get(card.work_item_id)]);
        if (previous.signature !== contentSignature) previous.pending = true;
        previous.signature = contentSignature; previous.card = card; previous.node.setAttribute('aria-label', firstLabel(card.work.title, t('ui_work_item')));
      } else rows.set(card.work_item_id, {node: makeCard(card), card});
    }
    page = data; next = data.next_cursor ?? null; fillFilters(); renderColumns(); renderMetrics();
    status.replaceChildren(...(!rows.size ? [emptyState({title: t('pm_kanban_empty')})] : []));
    if (selected && !rows.has(selected)) closeRetaining();
    else if (selected && active && detail.element) showDetail(rows.get(selected).card);
  }
  function showDetail(card) {
    const opening = !detail.element;
    detail.show({title: card.work.title, card, plan: card.binding.plan_revision_id === plan?.revision_id ? plan : null, requirements: requirements(card),
    milestones: milestones.filter(row => row.plan_revision_id === card.binding.plan_revision_id && row.requirement_ids?.some(id => card.binding.requirement_ids?.includes(id))), source: sources.get(card.work_item_id),
    onMore: () => {deliveryPages++; void select(card.work_item_id, true);}});
    if (opening) requestAnimationFrame(() => {const node = rows.get(card.work_item_id)?.node;
      if (selected === card.work_item_id && node?.isConnected && detail.element) revealCard(node);});
  }
  async function select(id, retain = false) {
    const current = rows.get(id); if (!current) return;
    if (selected !== id) deliveryPages = 1;
    selected = id; preference.selected = id; markSelection();
    const generation = serial, ticket = ++detailSerial;
    if (!retain) {detail.show({title: current.card.work.title}); detail.element.body.replaceChildren(note('pm_kanban_detail_loading'));
      requestAnimationFrame(() => {if (selected === id && current.node.isConnected && detail.element) revealCard(current.node);});}
    try {
      const data = await api('GET', `${endpoint}/kanban/${id}`); alive(); if (generation !== serial || ticket !== detailSerial) return;
      if (data.workspace_id !== workspaceId) throw Error(t('pm_kanban_scope_error')); checkCard(data.card, id);
      const source = await readWorkSources({api, workspaceId, cards: [data.card], caps: caps(), t, deliveryPages, valid: () => {alive(); return generation === serial && ticket === detailSerial;}});
      alive(); if (generation !== serial || ticket !== detailSerial) return;
      sources.set(id, source.get(id)); current.card = data.card;
      current.node.replaceChildren(...preview(data.card)); current.node.setAttribute('aria-label', firstLabel(data.card.work.title, t('ui_work_item')));
      renderColumns(); renderMetrics(); showDetail(data.card);
    } catch (error) {if (generation === serial && ticket === detailSerial) unavailable(error);}
  }
  const path = after => `${endpoint}/kanban?${new URLSearchParams({...(projectId ? {project_id: projectId} : {}), after: String(after), limit: String(pageSize)})}`;
  async function refresh() {
    const ticket = ++serial; detailSerial++; reading = true; refreshButton.disabled = true; more.disabled = true;
    if (!rows.size) status.replaceChildren(note('pm_kanban_loading')); element.setAttribute('aria-busy', 'true');
    try {
      const combined = new Map(); let after = 0, data;
      for (let index = 0; index < pages; index++) {
        data = checkPage(await api('GET', path(after)), after); alive(); if (ticket !== serial) return;
        for (const card of data.cards) combined.set(card.work_item_id, card);
        if (data.next_cursor == null) break; after = data.next_cursor;
      }
      const features = caps()?.features?.pm_workspaces;
      const [accepted, roadmap, read] = await Promise.all([features?.plan_drafting ? api('GET', `${endpoint}/plans/accepted`) : null,
        features?.roadmap ? api('GET', `${endpoint}/roadmap`) : null,
        readWorkSources({api, workspaceId, cards: [...combined.values()], caps: caps(), t, valid: () => {alive(); return ticket === serial;}})]);
      alive(); if (ticket !== serial) return;
      if (accepted && (accepted.head?.workspace_id !== workspaceId || accepted.plan && accepted.plan.workspace_id !== workspaceId)
        || roadmap && roadmap.workspace_id !== workspaceId) throw Error(t('pm_kanban_scope_error'));
      plan = accepted?.plan || null; milestones = roadmap?.baseline?.document?.milestones || []; sources = read;
      present({...data, cards: [...combined.values()]});
    } catch (error) {if (ticket === serial) unavailable(error);}
    finally {if (ticket === serial) {reading = false; element.removeAttribute('aria-busy'); more.disabled = false; refreshButton.disabled = false;}}
  }
  async function loadMore() {
    if (reading || next == null) return;
    // Reread all previously loaded pages to remove withdrawn records as well.
    pages++; await refresh();
  }
  function updateEdges() {
    content.classList.toggle('pm-board-clipped-start', list.scrollLeft > 1);
    content.classList.toggle('pm-board-clipped-end', list.scrollLeft + list.clientWidth < list.scrollWidth - 1);
  }
  function revealCard(node) {
    node.scrollIntoView({block: 'nearest', inline: 'nearest', behavior: 'instant'}); updateEdges();
  }
  const resize = new ResizeObserver(updateEdges); resize.observe(list);
  list.addEventListener('scroll', () => {preference.laneScroll = list.scrollLeft; updateEdges();});
  function setActive(value) {
    if (active === value) return; active = value;
    if (!value) {preference.scroll = window.scrollY; closeRetaining();}
    else {if (selected && rows.has(selected)) void select(selected, true); list.scrollLeft = preference.laneScroll || 0; requestAnimationFrame(() => window.scrollTo(0, preference.scroll || 0));}
  }
  return {element, box: element, meta, refresh, withdraw, setActive, hideInspector: closeRetaining, setTools: value => {toolItems = value;}, dispose: () => {resize.disconnect(); withdraw();}};
}
