import {boardGitHubPanel, boardGitHubLink} from './board-github.js';
import {h} from './ui/dom.js';
import {button, searchField, pageHeader, iconButton, statusPill, agentMark, contextChip, disclosure, emptyState, inlineError} from './ui/components.js';
import {icon} from './ui/icons.js';
import {viewState} from './ui/view-state.js';
import {cliName} from './pm-agents-trend.js';
import {kanbanObservation} from './pm-kanban-observation.js';
import {runtimeCompletion} from './pm-runtime-completion.js';
import {firstLabel, joinLabel, labelSegment} from './label-text.js';
import {workInspector, workStatusLabel, workStatusPill, blockedWork, blockerText, workAgents, prGroups, readWorkSources, deliveryCoverage, elapsed, readEarlierPlans} from './pm-work-inspector.js';
import {workStartPanel} from './pm-work-start.js';
import {adoptStartLanding} from './pm-adopt-start-landing.js';
import {workResultPanel} from './pm-work-result.js';
import {retiredView, viewLeft} from './pm-retired.js';

// Central management columns stay separate from execution, quality and delivery.
export function workspaceKanban({t, api, alive, namespace = '', workspaceId, errorBox, permitted, form, projectId = null, pageSize = 20, caps = () => null, embedded = false, workspaceSettled = () => Promise.resolve()}) {
  const endpoint = `/pm/workspaces/${encodeURIComponent(workspaceId)}`, preference = viewState(namespace, workspaceId, 'board');
  const integer = value => Number.isSafeInteger(value) && value >= 0;
  if (!integer(pageSize) || pageSize < 1 || pageSize > 100) throw Error('Invalid Kanban page size');
  let serial = 0, detailSerial = 0, next = null, reading = false, rows = new Map(), page = null, sources = new Map(), plan = null, earlierPlans = new Map(), milestones = [], pages = 1;
  let selected = preference.selected || null, active = !embedded, preserveSelection = false, toolItems = () => [], deliveryPages = 1, stale = true, scrollGeneration = 0;
  let failureSignature = '';
  const observers = new Map();
  const withdrawObservers = () => {for (const observer of observers.values()) observer.withdraw();};
  let startPanel = null;
  let resultPanel = null;
  const updateHeaderButton = startable => {
    if (!detail.element) return;
    const actionRow = detail.element.querySelector('[data-work-actions]');
    if (!actionRow) return;
    let btn = actionRow.querySelector('[data-work-start-open]');
    if (startable && startPanel) {
      if (!btn) {
        btn = button({
          variant: 'primary',
          size: 'sm',
          label: t('wst_start_open'),
          'data-work-start-open': '',
          onClick: () => {
            startPanel.focus();
          },
        });
        const discuss = actionRow.querySelector('[data-work-discuss]');
        if (discuss) {
          actionRow.insertBefore(btn, discuss);
        } else {
          actionRow.prepend(btn);
        }
      }
    } else if (btn) {
      btn.remove();
    }
  };
  const ensureStartPanel = () => {
    if (!startPanel && form) {
      const cap = caps()?.features?.pm_workspaces?.work_start;
      if (cap?.version === 1 && cap.available === true && cap.action === 'work.start') {
        const resultCap = caps()?.features?.pm_workspaces?.work_attempt_results;
        if (resultCap?.version === 1) {
          resultPanel = workResultPanel({
            h, t, api, alive, workspaceId, caps, errorBox, form, namespace,
            onVisibility: visible => {
              const attemptSection = detail.element?.body?.querySelector('[data-work-section="attempt-result"]');
              if (attemptSection) {
                attemptSection.hidden = !visible;
              }
              detail.setAttemptResultVisible?.(visible);
            },
          });
        }
        startPanel = workStartPanel({
          h, t, api, alive, namespace, workspaceId, caps, permitted, form, errorBox, plan: () => plan,
          onStartableChange: updateHeaderButton,
          onLatestAttempt: attempt => {
            resultPanel?.setAttempt(attempt);
          },
        });
      }
    }
    return startPanel;
  };
  ensureStartPanel();
  const landing=adoptStartLanding({t,api,alive,namespace,workspaceId,caps,errorBox});
  const github = form && caps()?.features?.board_github_sync?.version === 1 ? boardGitHubPanel({t, api, alive, caps, form, permitted: () => permitted(true), workspaceId, onSaved: () => refresh()}) : null;
  const meta = h('span'), status = h('div', {role: 'status'}), list = h('div', {class: 'pm-kanban-list'}), boardInfo = h('div');
  const refreshButton = iconButton({icon: 'refresh-cw', label: t('wv_refresh'), onClick: () => void refresh()});
  const more = h('button', {type: 'button', class: 'ui-button secondary', hidden: true, onclick: () => void loadMore()}, t('pm_kanban_more'));
  const columns = ['backlog', 'in_progress', 'waiting', 'review', 'accepted'];
  const search = h('input', {type: 'search', placeholder: t('ui_search_work'), 'aria-label': t('ui_search_work'), oninput: () => {preference.search = search.value; renderColumns(true);}}); search.value = preference.search || '';
  const requirementFilter = h('select', {'aria-label': t('wv_requirement'), onchange: () => {preference.requirement = requirementFilter.value; renderColumns(true);}});
  const agentFilter = h('select', {'aria-label': t('wv_agent'), onchange: () => {preference.agent = agentFilter.value; renderColumns(true);}});
  const group = h('select', {'aria-label': t('wv_group'), onchange: () => {preference.group = group.value; renderColumns(true);}},
    ...['status', 'milestone'].map(value => h('option', {value}, t('wv_' + value)))); group.value = preference.group || 'status';
  const empty = h('div', {'data-board-empty': '', hidden: true});
  const content = h('div', {class: 'pm-board-content'}, list, empty), layout = h('div', {class: 'pm-work-layout'}, content);
  const element = h('section', {class: 'pm-kanban', 'data-pm-kanban': ''},
    !embedded ? pageHeader({title: t('ui_board'), meta, actions: [refreshButton]}) : null,
    h('div', {class: 'pm-board-toolbar'}, searchField(search, {class:'pm-work-search'}), requirementFilter, agentFilter, h('label', {}, t('wv_group'), group)),
    status, ...(github ? [github.element] : []), landing.element, layout, h('footer', {class: 'pm-kanban-footer'}, more,
      disclosure({label: t('pm_kanban_board_details'), body: h('div', {}, boardInfo, h('p', {}, t('pm_kanban_wip_basis')), h('p', {}, t('pm_kanban_quality')), h('p', {}, t('pm_kanban_limits')))})));
  const detail = workInspector({t, workspaceId, from: 'board', mount: layout, tools: () => toolItems(), returnFocus: () => rows.get(selected)?.node.querySelector('[data-board-open]') || viewHeading(), onClose: () => {
    withdrawObservers();
    startPanel?.withdraw();
    resultPanel?.withdraw();
    updateHeaderButton(false);
    detailSerial++; if (!preserveSelection) {selected = null; preference.selected = null; writeSelection(null); markSelection();}
    requestAnimationFrame(updateEdges);
  }});
  const viewHeading = () => {const heading = element.closest('main')?.querySelector('h1'); if (heading) heading.tabIndex = -1; return heading;};
  const note = key => h('p', {class: 'ui-meta'}, t(key));
  function checkCard(card, expectedId = null) {
    if (!card || !/^wi_[0-9a-f]{20}$/.test(card.work_item_id || '') || expectedId && card.work_item_id !== expectedId
      || card.binding?.workspace_id !== workspaceId || card.binding?.work_item_id !== card.work_item_id || card.work?.work_item_id !== card.work_item_id
      || !integer(card.binding.sequence) || !integer(card.work.version)) throw Error(t('pm_kanban_scope_error'));
    if (projectId && card.binding.project_id !== projectId || card.latest_capture && (card.latest_capture.workspace_id !== workspaceId
      || card.latest_capture.work_item_id !== card.work_item_id)) throw Error(t('pm_kanban_scope_error'));
    for (const item of card.latest_capture?.evidence || []) if (item.workspace_id && item.workspace_id !== workspaceId) throw Error(t('pm_kanban_scope_error'));
    for (const attempt of card.execution?.attempts || []) runtimeCompletion(t, attempt);
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
  function writeSelection(id) {
    const [path, query] = location.hash.split('?');
    if (path !== `#/pm/${encodeURIComponent(workspaceId)}/board`) return;
    const params = new URLSearchParams(query); if (id) params.set('select', id); else params.delete('select');
    history.replaceState(null, '', path + (params.size ? '?' + params : ''));
  }
  function closeRetaining() {preserveSelection = true; detail.close(); preserveSelection = false;}
  function withdraw({forgetRoute = true, keepStatus = false} = {}) {
    landing.withdraw();withdrawObservers();
    ++serial; ++detailSerial; ++scrollGeneration; reading = false; rows.clear(); sources.clear(); page = null; next = null; plan = null; earlierPlans.clear(); milestones = [];
    if (!keepStatus) {failureSignature = ''; status.replaceChildren();}
    if (forgetRoute) writeSelection(null);
    closeRetaining(); list.replaceChildren(); meta.replaceChildren(); boardInfo.replaceChildren(); more.hidden = true; more.disabled = false; refreshButton.disabled = false; element.removeAttribute('aria-busy');
    list.hidden = true; empty.hidden = true; empty.replaceChildren(); element.querySelector('[data-board-partial]')?.remove();
    requirementFilter.replaceChildren(h('option', {value: ''}, t('wv_all_requirements'))); delete requirementFilter.dataset.options;
    agentFilter.replaceChildren(h('option', {value: ''}, t('wv_all_agents'))); delete agentFilter.dataset.options;
  }
  function unavailable(error) {
    errorBox?.(error); withdraw({keepStatus: true});
    const message = error.message === t('pm_kanban_scope_error') ? error.message : t(error.code === 'SNAPSHOT_CHANGED' ? 'wb_plan_changed' : error.status === 403 ? 'forbidden_scope' : 'wv_board_unavailable');
    const signature = JSON.stringify([message, labelSegment(error.code)]);
    if (signature === failureSignature) return;
    failureSignature = signature;
    const notice = inlineError({error: {message}});
    notice.classList.add('pm-board-error');
    notice.append(button({size: 'sm', label: t('ui_retry'), onClick: () => {if (reading) return; pages = 1; void refresh();}}));
    const code = labelSegment(error.code);
    if (code && !message.includes(code)) notice.append(disclosure({label: t('wv_details'), body: h('p', {class: 'mono'}, code)}));
    status.replaceChildren(notice);}
  function requirements(card) {const bound = card.binding.plan_revision_id === plan?.revision_id ? plan : earlierPlans.get(card.binding.plan_revision_id);
    return (bound?.document?.requirements || []).filter(row => card.binding.requirement_ids?.includes(row.id));}
  function fillFilters() {
    const fill = (select, all, options, saved) => {
      const current = select.value || saved || '', values = new Map(options);
      if (current && !values.has(current)) values.set(current, t('ui_status_unknown'));
      const signature = JSON.stringify([...values]);
      if (select.dataset.options !== signature) {select.dataset.options = signature; select.replaceChildren(h('option', {value: ''}, t(all)), ...[...values].map(([value, label]) => h('option', {value}, label)));}
      select.value = current;
    };
    fill(requirementFilter, 'wv_all_requirements', [...rows.values()].flatMap(({card}) => (card.binding.requirement_ids || []).map(id => [id, firstLabel(requirements(card).find(req => req.id === id)?.text, t('wv_earlier_plan'))])), preference.requirement);
    // Keep option identity and order independent of concurrent source-read completion.
    fill(agentFilter, 'wv_all_agents', [...sources].sort(([left], [right]) => left.localeCompare(right)).flatMap(([, source]) => workAgents(source).map(agent => [agent, cliName(agent)])), preference.agent);
  }
  const partialWorkPage = () => next != null || page?.visibility_filtered || page?.coverage?.byte_limited || page?.coverage?.visibility_filtered || page?.coverage?.withdrawn;
  const partialRead = () => partialWorkPage() || [...sources.values()].some(source => deliveryCoverage(source) !== 'complete');
  const filtersActive = () => Boolean(search.value.trim() || requirementFilter.value || agentFilter.value);
  function clearFilters() {
    search.value = ''; requirementFilter.value = ''; agentFilter.value = '';
    preference.search = ''; preference.requirement = ''; preference.agent = '';
    search.focus(); renderColumns(true);
  }
  function renderEmpty(count) {
    list.hidden = !count;
    empty.hidden = Boolean(count);
    if (count) {empty.replaceChildren(); return;}
    const filtered = filtersActive(), partial = partialRead();
    empty.replaceChildren(emptyState({
      title: t(partial ? 'pm_kanban_incomplete' : filtered ? 'pm_kanban_no_matches' : 'pm_kanban_empty'),
      text: partial && filtered ? t('pm_kanban_no_loaded_matches') : null,
      action: filtered ? button({size: 'sm', label: t('pm_kanban_clear_filters'), onClick: clearFilters}) : null,
    }));
  }
  function renderMetrics() {
    if (!page) return;
    const partial = partialRead();
    const blocked = [...rows.values()].filter(({card}) => blockedWork(card)).length;
    const shown = [...rows.values()].filter(({node}) => !node.hidden).length;
    meta.textContent = joinLabel([t(partial ? 'wv_loaded' : 'wv_work_count', {n: rows.size}), blocked ? t(blocked === 1 ? 'wv_blocked_one' : 'wv_blocked_count', {n: blocked}) : null,
      filtersActive() ? t(shown ? 'reg_shown' : 'pm_kanban_none_shown', {n: shown}) : null]);
    boardInfo.replaceChildren(...[page.wip ? h('p', {}, t('pm_kanban_wip', page.wip)) : note('wv_source_unavailable'),
      partial ? note('pm_kanban_partial') : null, page.coverage?.byte_limited ? note('pm_kanban_bounded') : null].filter(Boolean));
    element.querySelector('[data-board-partial]')?.remove();
    if (partialWorkPage()) more.before(h('span', {class: 'ui-meta', 'data-board-partial': ''}, t('pm_kanban_partial')));
    more.hidden = next == null; more.disabled = reading;
  }
  function preview(card) {
    const external = boardGitHubLink(card.work.external);
    const source = sources.get(card.work_item_id), reqs = requirements(card), coverage = card.execution?.coverage || {};
    const earlier = card.binding.plan_revision_id !== plan?.revision_id;
    const chip = text => {const node = contextChip({text}); node.title = text; return node;};
    const coverageState = deliveryCoverage(source);
    const age = elapsed(t, card.completion?.approved ? card.flow?.cycle_seconds : card.flow?.age_seconds);
    const title = firstLabel(card.work.title, t('ui_work_item')), metaId = `board-facts-${card.work_item_id}`;
    return [h('h3', {title}, h('button', {type: 'button', class: 'pm-board-open', 'data-board-open': card.work_item_id, 'aria-describedby': metaId}, title)),
      h('div', {id: metaId, class: 'pm-board-facts'}, h('span', {class: 'sr-only'}, workStatusLabel(t, card)),
      h('div', {class: 'pm-board-requirements'}, ...(reqs.length ? reqs.map(req => {const text = firstLabel(req.text, t('wv_earlier_plan'));
          return earlier ? chip(joinLabel([t('wv_earlier_plan'), text])) : h('a', {class: 'ui-context-chip', title: text, href: `#/pm/${encodeURIComponent(workspaceId)}/requirements?select=${encodeURIComponent(req.id)}`}, text);})
        : [chip(card.binding.requirement_ids?.length ? t('wv_earlier_plan') : t('wv_no_requirement'))])),
      h('div', {class: 'pm-kanban-card-meta'}, external ? h('a', {class: 'ui-context-chip', href: external.url, target: '_blank', rel: 'noopener noreferrer', title: `${external.repository} · ${external.label}`, 'data-github-card-link': ''}, external.label) : null, ...workAgents(source).map(agentMark).filter(mark => mark.textContent !== '?'),
        ...prGroups(source?.deliveries).map(({target, rows}) => {const node = contextChip({text: `PR #${target.pull_number}`});
          node.title = joinLabel([node.textContent, target.repository, t(rows.some(row => row.merge?.merged === true) ? 'ui_status_merged' : 'ui_status_unknown')]);
          node.setAttribute('aria-label', node.title); return node;}), coverageState !== 'complete' ? statusPill(coverageState) : null, blockedWork(card) ? h('span', {class: 'pm-board-blocker', title: blockerText(t, card), 'aria-label': blockerText(t, card)}, icon('circle-alert'), h('span', {class: 'pm-board-blocker-label'}, t('ui_status_blocked'))) : null,
        group.value !== 'status' ? h('span', {class: 'pm-board-state', title: workStatusLabel(t, card)}, workStatusPill(t, card)) : null,
        age ? h('span', {class: 'pm-board-age', title: t(card.completion?.approved ? 'pm_kanban_cycle' : 'pm_kanban_age')}, age) : null),
      card.execution?.attempts?.some(attempt => runtimeCompletion(t, attempt)) ? h('span', {class: 'ui-meta', 'data-runtime-summary': ''}, t('pm_kanban_runtime_recorded')) : null,
      coverage.unavailable_sources || coverage.inaccessible_sources || coverage.truncated ? h('span', {class: 'ui-meta'}, t('pm_kanban_partial_sources')) : null)];
  }
  function makeCard(card) {
    const node = h('article', {class: 'pm-kanban-card', 'data-pm-kanban-work': card.work_item_id, role: 'listitem'}, ...preview(card));
    node.addEventListener('click', event => {if (event.target.closest('[data-board-open]')) void select(card.work_item_id);});
    node.addEventListener('keydown', event => {
      if (!event.target.matches('[data-board-open]')) return;
      if (['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
        event.preventDefault(); const lane = node.closest('[data-pm-kanban-column]'), cards = [...lane.querySelectorAll('[data-pm-kanban-work]:not([hidden])')], index = cards.indexOf(node);
        let target;
        if (['ArrowLeft', 'ArrowRight'].includes(event.key)) {
          const lanes = [...list.children], next = lanes[lanes.indexOf(lane) + (event.key === 'ArrowLeft' ? -1 : 1)];
          const available = next?.querySelector('.pm-kanban-cards')?.hidden ? [] : [...(next?.querySelectorAll('[data-pm-kanban-work]:not([hidden])') || [])];
          target = available[index] || available[0] || next?.querySelector('button');
        } else target = cards[event.key === 'Home' ? 0 : event.key === 'End' ? cards.length - 1 : index + (event.key === 'ArrowUp' ? -1 : 1)];
        (target?.querySelector('[data-board-open]') || target)?.focus({preventScroll: true}); if (target) revealCard(target);
      }
    }); return node;
  }
  function patchCard(node, card) {
    const [title, facts] = preview(card);
    node.querySelector('h3').title = title.title;
    node.querySelector('[data-board-open]').textContent = title.textContent;
    node.querySelector('.pm-board-facts').replaceWith(facts);
  }
  const locked = lane => lane && (lane.matches(':hover') || lane.contains(document.activeElement));
  function laneKey(card) {
    if (group.value === 'milestone') return milestones.find(row => row.plan_revision_id === card.binding.plan_revision_id && row.requirement_ids?.some(id => card.binding.requirement_ids?.includes(id)))?.milestone_id || 'unassigned';
    return [...columns, 'github_done', 'github_closed', 'github_completed', 'archived'].includes(card.column) ? card.column : 'unknown';
  }
  function renderColumns(force = false) {
    if (!page) return;
    if (!rows.size) {list.replaceChildren(); renderEmpty(0); renderMetrics(); return;}
    const query = search.value.trim().toLocaleLowerCase();
    const groupKeys = group.value === 'status' ? [...columns] : milestones.map(row => row.milestone_id);
    const currentLanes = new Map([...list.children].map(node => [node.dataset.pmKanbanColumn, node]));
    for (const {card} of rows.values()) if (!groupKeys.includes(laneKey(card))) groupKeys.push(laneKey(card));
    for (const key of groupKeys) if (!currentLanes.has(key)) {
      const name = group.value === 'milestone' ? firstLabel(milestones.find(row => row.milestone_id === key)?.title, t('wb_unassigned')) : workStatusLabel(t, {column: key});
      const holder = h('div', {class: 'pm-kanban-cards', role: 'list', 'aria-label': name}), collapsed = preference.collapsed?.includes(key) === true;
      const toggle = h('button', {type: 'button', class: 'pm-board-lane-toggle', 'aria-expanded': String(!collapsed), onclick: () => {
        holder.hidden = !holder.hidden; toggle.setAttribute('aria-expanded', String(!holder.hidden));
        preference.collapsed = [...list.children].filter(lane => lane.querySelector('.pm-kanban-cards').hidden).map(lane => lane.dataset.pmKanbanColumn);
      }}, icon('chevron-right'), h('span', {class: 'pm-board-lane-name', title: name}, name), h('span', {class: 'pm-kanban-count'})); holder.hidden = collapsed;
      const lane = h('section', {class: 'pm-kanban-column', 'data-pm-kanban-column': key, 'aria-label': name}, h('header', {}, h('h2', {}, toggle)), holder);
      lane.addEventListener('pointerleave', () => requestAnimationFrame(() => renderColumns()));
      lane.addEventListener('focusout', () => requestAnimationFrame(() => renderColumns()));
      currentLanes.set(key, lane); list.append(lane);
    }
    for (const entry of rows.values()) {
      const {card, node} = entry;
      if (entry.pending || force) {patchCard(node, card); entry.pending = false;}
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
      else {const count = lane.querySelectorAll('[data-pm-kanban-work]:not([hidden])').length, partial = partialWorkPage();
        const label = lane.querySelector('.pm-kanban-count'); label.textContent = partial ? t('wv_loaded', {n: count}) : count;
        if (partial) label.setAttribute('aria-label', t('wv_lane_loaded', {n: count})); else label.removeAttribute('aria-label');
      }
    }
    markSelection();
    renderEmpty([...rows.values()].filter(({node}) => !node.hidden).length);
    renderMetrics();
    requestAnimationFrame(updateEdges);
  }
  function markSelection() {for (const {node, card} of rows.values()) node.querySelector('[data-board-open]').setAttribute('aria-pressed', String(selected === card.work_item_id));}
  function present(data, append = false) {
    const ids = new Set(data.cards.map(card => card.work_item_id));
    if (!append) for (const [id, {node}] of rows) if (!ids.has(id)) {node.remove(); rows.delete(id);}
    for (const card of data.cards) {
      const previous = rows.get(card.work_item_id);
      if (previous) {
        // Preserve the card node and focus, even when its facts change.
        const contentSignature = JSON.stringify([card.work.title, card.column, card.flow?.age_seconds, card.flow?.cycle_seconds, card.execution?.attempts, blockedWork(card), sources.get(card.work_item_id)]);
        if (previous.signature !== contentSignature) previous.pending = true;
        previous.signature = contentSignature; previous.card = card; previous.node.querySelector('[data-board-open]').textContent = firstLabel(card.work.title, t('ui_work_item'));
      } else rows.set(card.work_item_id, {node: makeCard(card), card});
    }
    page = data; const routeId = new URLSearchParams(location.hash.split('?')[1]).get('select');
    if (routeId && rows.has(routeId)) {selected = routeId; preference.selected = routeId;}
    else if(routeId&&!landing.element.hidden){selected=null;preference.selected=null;closeRetaining();}
    next = data.next_cursor ?? null; fillFilters(); renderColumns();
    failureSignature = ''; status.replaceChildren();
    if (selected && !rows.has(selected)) {selected = null; preference.selected = null; writeSelection(null); closeRetaining(); viewHeading()?.focus();}
    else if (selected && active) void select(selected, true);
  }
  function showDetail(card) {
    const opening = !detail.element;
    detail.show({title: card.work.title, card, plan: card.binding.plan_revision_id === plan?.revision_id ? plan : earlierPlans.get(card.binding.plan_revision_id), earlierPlan: card.binding.plan_revision_id !== plan?.revision_id, requirements: requirements(card),
    milestones: milestones.filter(row => row.plan_revision_id === card.binding.plan_revision_id && row.requirement_ids?.some(id => card.binding.requirement_ids?.includes(id))), source: sources.get(card.work_item_id),
    onMore: () => {deliveryPages++; void select(card.work_item_id, true);}});
    const panel = ensureStartPanel();
    if (panel) {
      const statusSection = detail.element.body.querySelector('[data-work-section="status"]');
      let startSection = detail.element.body.querySelector('[data-work-section="start"]');
      if (!startSection) {
        startSection = h('section', {'data-work-section': 'start'});
        if (statusSection && statusSection.nextSibling) {
          detail.element.body.insertBefore(startSection, statusSection.nextSibling);
        } else {
          detail.element.body.append(startSection);
        }
      }
      if (!startSection.contains(panel.element)) {
        startSection.replaceChildren(panel.element);
      }
      if (resultPanel) {
        let attemptResultSection = detail.element.body.querySelector('[data-work-section="attempt-result"]');
        if (!attemptResultSection) {
          attemptResultSection = h('section', {'data-work-section': 'attempt-result'});
          attemptResultSection.hidden = true;
          if (startSection && startSection.nextSibling) {
            detail.element.body.insertBefore(attemptResultSection, startSection.nextSibling);
          } else {
            detail.element.body.append(attemptResultSection);
          }
        }
        if (!attemptResultSection.contains(resultPanel.element)) {
          attemptResultSection.replaceChildren(resultPanel.element);
        }
        attemptResultSection.hidden = resultPanel.element.hidden;
        detail.setAttemptResultVisible?.(!attemptResultSection.hidden);
      }
      panel.setWork(card);
      updateHeaderButton(panel.startable());
    }
    if (form && caps()?.actions?.some(row => row.action === 'pm.session.quiescence.observe')) {
      let observer = observers.get(card.work_item_id);
      if (!observer) {
        observer = kanbanObservation({h, t, api, alive, workspaceId, workId: card.work_item_id, caps, permitted, form, errorBox, onSourceFailure: unavailable});
        observers.set(card.work_item_id, observer);
      }
      const body = detail.element.body.querySelector('[data-work-section="details"] > div');
      if (observer.box.parentNode !== body) body.insertBefore(observer.box, body.querySelector(':scope > pre'));
      observer.update(card.execution?.manual_observation_sources ?? []);
    }
    if (opening) requestAnimationFrame(() => {const node = rows.get(card.work_item_id)?.node;
      if (selected === card.work_item_id && node?.isConnected && detail.element) revealCard(node);});
  }
  async function select(id, retain = false) {
    const current = rows.get(id); if (!current) return;
    if (selected !== id || !retain) { withdrawObservers(); updateHeaderButton(false); }
    if (selected !== id) deliveryPages = 1;
    selected = id; preference.selected = id; writeSelection(id); markSelection();
    const generation = serial, ticket = ++detailSerial;
    if (!retain) {detail.show({title: current.card.work.title}); detail.element.body.replaceChildren(note('pm_kanban_detail_loading'));
      requestAnimationFrame(() => {if (selected === id && current.node.isConnected && detail.element) revealCard(current.node);});}
    try {
      const data = await api('GET', `${endpoint}/kanban/${id}`); alive(); if (generation !== serial || ticket !== detailSerial) return;
      if (data.workspace_id !== workspaceId) throw Error(t('pm_kanban_scope_error')); checkCard(data.card, id);
      const source = await readWorkSources({api, workspaceId, cards: [data.card], caps: caps(), t, deliveryPages, valid: () => {alive(); return generation === serial && ticket === detailSerial;}});
      alive(); if (generation !== serial || ticket !== detailSerial) return;
      sources.set(id, source.get(id)); current.card = data.card;
      patchCard(current.node, data.card);
      renderColumns(); renderMetrics(); showDetail(data.card);
    } catch (error) {
      if (retiredView(error) || viewLeft(alive)) return;
      if (generation === serial && ticket === detailSerial) {
        observers.get(id)?.withdraw();
        if (error.message === t('pm_kanban_scope_error')) {unavailable(error); return;}
        if ([403, 404].includes(error.status)) {
          try {checkPage(await api('GET', path(0)), 0); alive(); if (generation !== serial || ticket !== detailSerial) return;}
          catch (parentError) {
            if (retiredView(parentError) || viewLeft(alive)) return;
            if (generation === serial && ticket === detailSerial) unavailable(parentError);
            return;
          }
        }
        if ([403, 404].includes(error.status)) {current.node.remove(); rows.delete(id); sources.delete(id); page = {...page, wip: null, coverage: {...page.coverage, withdrawn: true}}; renderColumns(); renderMetrics();}
        detail.show({title: current.card.work.title, error, onRetry: () => void refresh()});
      }
    }
  }
  const path = after => `${endpoint}/kanban?${new URLSearchParams({...(projectId ? {project_id: projectId} : {}), after: String(after), limit: String(pageSize)})}`;
  async function refresh() {
    if (!active) {stale = true; return;}
    stale = false;
    withdrawObservers();
    const ticket = ++serial; detailSerial++; reading = true; refreshButton.disabled = true; more.disabled = true;
    // Keep the same recovery controls while checking an existing failure. A
    // repeated error must not remove the button under the pointer or keyboard.
    if (!rows.size && !failureSignature) status.replaceChildren(note('pm_kanban_loading')); element.setAttribute('aria-busy', 'true');
    try {
      const combined = new Map(); let after = 0, data;
      for (let index = 0; index < pages; index++) {
        data = checkPage(await api('GET', path(after)), after); alive(); if (ticket !== serial) return;
        for (const card of data.cards) combined.set(card.work_item_id, card);
        if (data.next_cursor == null) break; after = data.next_cursor;
      }
      await github?.refresh(); alive(); if (ticket !== serial) return;
      const features = caps()?.features?.pm_workspaces;
      const [accepted, roadmap, read] = await Promise.all([features?.plan_drafting ? api('GET', `${endpoint}/plans/accepted`) : null,
        features?.roadmap ? api('GET', `${endpoint}/roadmap`).then(data => {if (data.workspace_id !== workspaceId) throw Error(t('wv_source_mismatch')); return data;}).catch(() => null) : null,
        readWorkSources({api, workspaceId, cards: [...combined.values()], caps: caps(), t, deliveryPages, valid: () => {alive(); return ticket === serial;}})]);
      alive(); if (ticket !== serial) return;
      if (accepted && (accepted.head?.workspace_id !== workspaceId || accepted.plan && accepted.plan.workspace_id !== workspaceId)
        || roadmap && roadmap.workspace_id !== workspaceId) throw Error(t('pm_kanban_scope_error'));
      earlierPlans = await readEarlierPlans({api, workspaceId, cards: [...combined.values()], currentId: accepted?.plan?.revision_id, valid: () => {alive(); return ticket === serial;}});
      alive(); if (ticket !== serial) return;
      plan = accepted?.plan || null; milestones = roadmap?.baseline?.document?.milestones || []; sources = read;
      await landing.refresh();alive();if(ticket!==serial)return;
      present({...data, cards: [...combined.values()]});
    } catch (error) {if (retiredView(error) || viewLeft(alive)) return; if (ticket === serial) unavailable(error);}
    finally {if (ticket === serial) {reading = false; element.removeAttribute('aria-busy'); more.disabled = false; refreshButton.disabled = false;}}
  }
  async function loadMore() {
    if (reading || next == null) return;
    // Reread all previously loaded pages to remove withdrawn records as well.
    pages++; await refresh();
  }
  function titleFor(id) {const title = rows.get(id)?.card?.work?.title; return typeof title === 'string' ? title : '';}
  function fitLanes() {
    if (matchMedia('(max-width: 599px)').matches) {
      if (list.style.getPropertyValue('--board-lane')) list.style.removeProperty('--board-lane');
      return;
    }
    const width = list.clientWidth;
    const lanes = list.querySelectorAll(':scope > .pm-kanban-column').length;
    if (width < 32 || !lanes) return;
    const gap = Number.parseFloat(getComputedStyle(list).columnGap) || 0;
    const min = 168;
    const fit = Math.max(1, Math.floor((width + gap) / (min + gap)));
    const count = Math.max(1, Math.min(lanes, fit));
    const lane = (width - gap * (count - 1)) / count;
    const next = `${lane.toFixed(2)}px`;
    if (list.style.getPropertyValue('--board-lane') !== next) list.style.setProperty('--board-lane', next);
  }
  function updateEdges() {
    fitLanes();
    content.classList.toggle('pm-board-clipped-start', list.scrollLeft > 1);
    content.classList.toggle('pm-board-clipped-end', list.scrollLeft + list.clientWidth < list.scrollWidth - 1);
  }
  function revealCard(node) {
    node.scrollIntoView({block: 'nearest', inline: 'nearest', behavior: 'instant'}); updateEdges();
  }
  const resize = new ResizeObserver(() => {updateEdges(); if (detail.element && selected) {const node = rows.get(selected)?.node; if (node) requestAnimationFrame(() => revealCard(node));}}); resize.observe(list);
  list.addEventListener('scroll', () => {preference.laneScroll = list.scrollLeft; updateEdges();});
  function rememberScroll() {
    if (element.isConnected && element.getClientRects().length > 0) preference.scroll = window.scrollY;
  }
  const onScroll = () => {
    if (active && !reading && element.isConnected && element.getClientRects().length > 0) preference.scroll = window.scrollY;
  };
  window.addEventListener('scroll', onScroll, {passive: true});
  function setActive(value) {
    const routeId = new URLSearchParams(location.hash.split('?')[1]).get('select');
    if (active === value) {if (value && routeId && routeId !== selected) void select(routeId); return;} active = value;
    if (!value) {landing.withdraw();stale = true; serial++; detailSerial++; ++scrollGeneration; rememberScroll(); closeRetaining();}
    else {
      const generation = ++scrollGeneration;
      // The loading layout can clamp the first scroll and emit a scroll event.
      // Keep the requested position until workspace layout settles; an actual
      // user movement still wins through the restoreY check below.
      const targetY = preference.scroll || 0;
      const read = stale || !page ? refresh() : null;
      if (!read && selected && rows.has(selected)) void select(selected, true);
      list.scrollLeft = preference.laneScroll || 0;
      let restoreY = 0;
      requestAnimationFrame(() => {
        if (generation === scrollGeneration) {
          window.scrollTo(0, targetY);
          restoreY = window.scrollY;
        }
      });
      void Promise.all([read, workspaceSettled()]).then(() => {
        if (generation === scrollGeneration && window.scrollY === restoreY) {
          window.scrollTo(0, targetY);
        }
      });
      return read;
    }
  }
  return {element, box: element, meta, refresh, titleFor, withdraw, setActive, rememberScroll, hideInspector: closeRetaining, setTools: value => {toolItems = value;}, dispose: () => {rememberScroll(); window.removeEventListener('scroll', onScroll); github?.dispose();landing.dispose();startPanel?.dispose(); startPanel = null; resultPanel?.dispose(); resultPanel = null; resize.disconnect(); withdraw({forgetRoute: false});}};
}
