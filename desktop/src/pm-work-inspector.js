import {boardGitHubText, boardGitHubLink} from './board-github.js';
import {timeStamp} from './ui/stamps.js';
import {h} from './ui/dom.js';
import {discussLink, inspector, statusPill, disclosure, agentMark, contextChip, menu, iconButton, button, inlineError} from './ui/components.js';
import {reasonLabel} from './ui/ui-strings.js';
import {firstLabel, joinLabel, labelSegment} from './label-text.js';
import {discussHref} from './pm-discuss-link.js';
import {sessionHref} from './session-navigation.js';
import {qualityFact} from './pm-quality-projections.js';
import {runtimeCompletion, runtimeReceipt} from './pm-runtime-completion.js';

export const workStatus = card => ({backlog: 'todo', in_progress: 'in_progress', waiting: 'waiting', review: 'in_review', accepted: 'done', github_done: 'done', github_closed: 'historical', github_completed: 'historical', archived: 'historical'})[typeof card?.column === 'string' ? card.column : ''] || 'unknown';
// Content approval is a management fact, not proof of delivery or passing DoD.
export const workStatusLabel = (t, card) => ['github_done', 'github_closed', 'github_completed'].includes(card?.column) ? boardGitHubText({github_done: 'bgh_done', github_closed: 'bgh_closed', github_completed: 'bgh_completed'}[card.column]) : card?.column === 'accepted' ? t('wv_content_approved') : t('ui_status_' + workStatus(card));
export function workStatusPill(t, card) {
  const pill = statusPill(workStatus(card));
  pill.textContent = workStatusLabel(t, card);
  return pill;
}
export const blockedWork = card => card?.flow?.blocked === true || card?.execution?.tasks?.some(task => task.state === 'blocked') === true;
export const blockerText = (t, card) => card?.flow?.blocked === true ? reasonLabel(t, card.flow.blocked_reason)
  : blockedWork(card) ? t('wv_task_blocked') : t('wv_blocker_unknown');
export const workAgents = source => [...new Set((source?.sessions || []).map(row => labelSegment(row.agent_kind)).filter(Boolean))];
export function prGroups(deliveries = []) {
  const groups = new Map();
  for (const row of deliveries) {
    const target = row.target;
    if (!target?.provider_origin || !Number.isSafeInteger(target.repository_id) || !Number.isSafeInteger(target.pull_number)) continue;
    const key = JSON.stringify([target.provider_origin, target.repository_id, target.pull_number]);
    if (!groups.has(key)) groups.set(key, {target, rows: []});
    groups.get(key).rows.push(row);
  }
  return [...groups.values()];
}
export const deliveryCoverage = source => !source || source.available !== true ? 'unknown' : source.filtered || source.next != null ? 'partial' : 'complete';
export function aggregateDelivery(sources, incomplete = false) {
  const coverage = sources.map(deliveryCoverage);
  return {deliveries: [...new Map(sources.flatMap(source => source?.deliveries || []).map(row => [row.operation_id, row])).values()],
    available: !coverage.includes('unknown'), filtered: incomplete || coverage.includes('partial'), next: null};
}
export function deployStatus(source) {
  const coverage = deliveryCoverage(source);
  if (coverage !== 'complete') return coverage;
  const groups = prGroups(source.deliveries), deployed = groups.filter(group => group.rows.some(row => row.deployment?.verified === true && row.deployment.is_current === true)).length;
  return deployed && deployed === groups.length ? 'deployed' : deployed ? 'partial' : 'unknown';
}
export function prPills(source) {
  const pills = prGroups(source?.deliveries).map(({target, rows}) => h('span', {class: 'pm-work-pr', title: labelSegment(target.repository)},
    contextChip({text: `PR #${target.pull_number}`}), rows.some(row => row.merge?.merged === true) ? statusPill('merged') : statusPill('unknown')));
  const coverage = deliveryCoverage(source);
  if (coverage !== 'complete') pills.push(statusPill(coverage));
  return pills;
}
export function elapsed(t, value) {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return null;
  if (value < 60) return t('pm_kanban_seconds');
  const [key, divisor] = value < 3600 ? ['minutes', 60] : value < 86400 ? ['hours', 3600] : ['days', 86400];
  return t('pm_kanban_' + key, {count: Math.floor(value / divisor)});
}
// Reuse the project tree's existing work-item route; /work resolves execution sources.
export const workHref = binding => binding?.work_item_id ? `#/item/${encodeURIComponent(binding.work_item_id)}` : null;
export function checkDelivery(data, workspaceId, id, after, t) {
  if (data?.workspace_id !== workspaceId || data.work_item_id !== id || data.contract !== 'pm-engineering-delivery/v1'
    || !Array.isArray(data.deliveries) || data.requirement_acceptance !== 'not_evaluated_by_delivery'
    || data.next_after != null && (!Number.isSafeInteger(data.next_after) || data.next_after <= after)) throw Error(t('pm_scope_mismatch'));
  for (const row of data.deliveries) if (!/^op_[0-9a-f]{32}$/.test(row.operation_id || '') || row.requirement_acceptance !== 'not_evaluated_by_delivery') throw Error(t('pm_scope_mismatch'));
  return data;
}
// Explicit linked sessions only. No ownership, agent or PR inference from names or paths.
// A caller publishes this result only after its generation and parent-source checks.
export async function readWorkSources({api, workspaceId, cards, caps, valid, t, deliveryPages = 1}) {
  const result = new Map(), sessions = new Map();
  async function read(card) {
    const id = card.work_item_id, source = {deliveries: [], available: false, next: null, filtered: false, sessions: [], errors: []};
    if (caps?.features?.pm_workspaces?.engineering_delivery) {
      try {
        let after = 0;
        for (let index = 0; index < deliveryPages; index++) {
          const data = checkDelivery(await api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}/work/${id}/deliveries?after=${after}&limit=20`), workspaceId, id, after, t);
          if (!valid()) return;
          source.deliveries.push(...data.deliveries);
          source.filtered ||= Boolean(data.source_visibility_filtered || data.coverage?.byte_limited); source.next = data.next_after ?? null;
          if (source.next === null) break; after = source.next;
        }
        source.available = true;
      } catch (error) {source.deliveries = []; source.next = null; source.errors.push({kind: 'delivery', code: error.code});}
    }
    const reads = (card.execution?.sessions || []).map(ref => {
      const resource = labelSegment(ref.resource_id), slash = resource.indexOf('/');
      if (slash < 1 || !resource.slice(slash + 1)) return Promise.reject(Error(t('wv_source_mismatch')));
      const host = resource.slice(0, slash), sessionId = resource.slice(slash + 1);
      if (!sessions.has(resource)) sessions.set(resource, api('GET', `/sessions/${encodeURIComponent(host)}/${encodeURIComponent(sessionId)}`).then(data => {
        if (data.session?.host !== host || data.session.session_id !== sessionId) throw Error(t('wv_source_mismatch'));
        return data.session;
      }));
      return sessions.get(resource);
    });
    const settled = await Promise.allSettled(reads);
    if (!valid()) return;
    for (const row of settled) if (row.status === 'fulfilled') source.sessions.push(row.value);
    else source.errors.push({kind: 'session', code: row.reason?.code});
    result.set(id, source);
  }
  // Bounded fan-out on the read-only central endpoints.
  const queue = [...cards];
  await Promise.all(Array.from({length: Math.min(4, queue.length)}, async () => {while (queue.length && valid()) await read(queue.shift());}));
  return result;
}

// Historical text comes only from its explicitly bound, reauthorized revision.
export async function readEarlierPlans({api, workspaceId, cards, currentId, valid}) {
  const ids = [...new Set(cards.map(card => card.binding?.plan_revision_id).filter(id => id && id !== currentId))];
  const rows = await Promise.allSettled(ids.map(async id => {
    const data = await api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}/plans/${encodeURIComponent(id)}`);
    if (!valid() || data.plan?.workspace_id !== workspaceId || data.plan.revision_id !== id) return null;
    return [id, data.plan];
  }));
  return new Map(rows.flatMap(row => row.status === 'fulfilled' && row.value ? [row.value] : []));
}

// One inspector API for Requirements, Board and Schedule. It only renders authorized facts.
export function workInspector({t, workspaceId, from, mount, onClose = () => {}, tools = () => [], returnFocus}) {
  let panel = null, actions = null, signature = '', selection = null, returnTarget = null;
  const unknown = () => statusPill('unknown');
  const paragraph = (...nodes) => h('p', {}, ...nodes);
  const stamp = value => timeStamp(value);
  const captureFacts = capture => (capture.evidence || []).map(row => paragraph(t('pm_kanban_fact'), ' · ',
    ...Object.entries(row.assessment || {}).map(([key, value]) => {
      const label = t('pm_kanban_field_' + key), state = t('pm_kanban_state_' + labelSegment(value));
      return joinLabel([label.startsWith('pm_kanban_') ? t('pm_kanban_fact') : label, state.startsWith('pm_kanban_') ? t('ui_status_unknown') : state]);
    })));
  function close() {panel?.close();}
  function show(data) {
    selection = data;
    if (!panel) {
      panel = inspector({title: data.title || t('ui_work_item'), returnFocus: () => returnTarget, onClose: () => {returnTarget = typeof returnFocus === 'function' ? returnFocus() : returnFocus; actions?.dispose(); actions = null; panel = null; selection = null; signature = ''; onClose();}});
      panel.classList.add('pm-work-inspector'); panel.dataset.workInspector = ''; panel.dataset.rtmDetail = '';
      mount.append(panel);
    }
    const next = JSON.stringify(data, (key, value) => ['age_seconds', 'waiting_seconds', 'observed_at', 'updated_at'].includes(key) ? undefined : value);
    if (signature === next) return;
    signature = next;
    for (const node of panel.body.querySelectorAll(':scope > :not([data-work-section])')) node.remove();
    panel.querySelector('h2').textContent = data.title || t('ui_work_item');
    const {card, plan, requirement, requirements = [], source = {}, issues = []} = data;
    const refs = card ? [{kind: 'work_item', id: card.work_item_id}] : requirement && plan ? [{kind: 'requirement', id: requirement.id, revision_id: plan.revision_id}] : [];
    const href = discussHref({workspaceId, refs, title: data.title, from});
    let actionRow = panel.querySelector('[data-work-actions]');
    if (!actionRow) {
      actionRow = h('div', {class: 'pm-work-inspector-actions', 'data-work-actions': ''}, discussLink({workspaceId, refs, title:data.title, from}, {'data-work-discuss':''}));
      panel.querySelector(':scope > header').append(actionRow);
    }
    if (!actions && tools().length) {
      actions = menu({button: iconButton({icon: 'more-horizontal', label: t('ui_more')}), items: tools()});
      actionRow.append(actions);
    }
    panel.querySelector('[data-work-discuss]').href = href;
    if (data.error) {
      panel.body.replaceChildren(h('section', {'data-work-section': 'error'}, inlineError({error: {message: t('wv_detail_unavailable')}, retry: data.onRetry})),
        disclosure({label: t('wv_details'), body: paragraph(labelSegment(data.error.code))}));
      return;
    }
    const sections = [
      ['requirements', 'wv_served'], ['status', 'wv_status_owner'], ['result', 'wv_result'], ['blocker', 'wv_blocker'], ['delivery', 'wv_delivery'],
    ].map(([key, label]) => h('section', {'data-work-section': key}, h('h3', {}, t(label))));
    const [served, state, result, blocker, delivery] = sections;
    const requirementRows = requirement ? [] : requirements;
    served.append(...(requirementRows.length ? requirementRows.map(row => h('div', {}, paragraph(data.earlierPlan ? firstLabel(row.text, t('wv_earlier_plan')) : h('a', {href: `#/pm/${encodeURIComponent(workspaceId)}/requirements?select=${encodeURIComponent(row.id)}`}, firstLabel(row.text, t('wv_earlier_plan')))),
      row.acceptance?.length ? h('ul', {}, ...row.acceptance.map(item => h('li', {}, labelSegment(item.text)))) : null)) : [paragraph(t(card?.binding?.requirement_ids?.length ? 'wv_earlier_plan' : 'wv_no_requirement'))]));
    if (data.earlierPlan) served.prepend(paragraph(t('wv_from_earlier_plan')));
    if (from === 'requirements' && card) state.append(h('a', {href: `#/pm/${encodeURIComponent(workspaceId)}/board?select=${encodeURIComponent(card.work_item_id)}`}, t('wv_show_board')));
    if (data.milestones) served.append(paragraph(t('wv_milestone'), ' · ', joinLabel(data.milestones.map(row => row.title)) || t('wv_no_milestone')));
    if (card) {
      state.append(h('div', {class: 'pm-work-inline'}, workStatusPill(t, card),
        ...workAgents(source).map(agentMark)), paragraph(t('wv_owner'), ' · ', labelSegment(card.assignment?.actor) ||
          (card.assignment?.reason === 'creator_is_not_assignee' ? t('wv_not_recorded') : reasonLabel(t, card.assignment?.reason))));
      for (const session of source.sessions || []) state.append(paragraph(h('a', {
        href: sessionHref({host: session.host, sessionId: session.session_id, from}),
      }, firstLabel(session.title, t('wv_session'))), ' ', session.api_access === 'managed' && session.provenance === 'connector_managed' ? null : statusPill('read_only')));
      if (!workAgents(source).length) state.append(paragraph(t('wv_agent'), ' · ', unknown()));
      const external = boardGitHubLink(card.work.external);
      if (external) state.append(paragraph(h('a', {href: external.url, target: '_blank', rel: 'noopener noreferrer'}, `${external.repository} · ${external.label}`)));
      if (workHref(card.binding)) state.append(h('a', {href: workHref(card.binding)}, t('wv_work_details')));
      if (source.errors?.some(row => row.kind === 'session')) state.append(paragraph(t('wv_session_unavailable')));
      if (card.quality?.selected_assessment) result.append(qualityFact({h, t, fact: card.quality, current: true}));
      else result.append(paragraph(t('pm_kanban_quality_unknown')));
      if (card.execution?.attempts?.some(attempt => runtimeCompletion(t, attempt))) result.append(paragraph(t('pm_kanban_runtime_recorded')));
      const capture = card.latest_capture;
      if (capture) {
        result.append(paragraph(t('pm_kanban_capture'), ' · ', stamp(capture.captured_at)));
        if (card.capture_matches_binding === false || card.capture_matches_work_version === false) result.append(paragraph(t('pm_kanban_capture_different')));
        result.append(...captureFacts(capture));
      } else result.append(paragraph(t('wv_no_result')));
      blocker.append(paragraph(blockedWork(card) ? statusPill('blocked') : unknown(), blockedWork(card) ? ' ' + blockerText(t, card) : null));
      const groups = prGroups(source.deliveries);
      for (const {target, rows} of groups) delivery.append(h('div', {class: 'pm-work-delivery', 'data-rtm-pr': target.pull_number},
        h('h4', {}, joinLabel([target.repository, `PR #${target.pull_number}`])),
        h('div', {class: 'pm-work-inline'}, h('span', {}, t('wv_merge')), statusPill(rows.some(row => row.merge?.merged === true) ? 'merged' : 'unknown')),
        !rows.some(row => row.deployment) ? paragraph(t('wv_deploy'), ' · ', unknown()) : null,
        ...rows.filter(row => row.deployment).map(row => paragraph(t('wv_deploy'), ' · ', statusPill(row.deployment.verified === true ? 'deployed' : 'unknown'),
          ' ', row.deployment.is_current === false ? t('wb_historical_deployment') : row.deployment.is_current === true ? t('wb_current_environment') : t('ui_status_unknown')))));
      for (const row of source.deliveries || []) if (!row.target?.pull_number) delivery.append(paragraph(t('wv_deploy'), ' · ', statusPill(row.deployment?.verified === true ? 'deployed' : 'unknown')));
      if (deliveryCoverage(source) === 'unknown') delivery.append(paragraph(t('wv_delivery_unknown')));
      else if (!source.deliveries.length && deliveryCoverage(source) === 'complete') delivery.append(paragraph(t('wv_no_delivery')));
      if (source.filtered || source.next != null) delivery.append(statusPill('partial'));
      if (source.next != null && data.onMore) delivery.append(button({label: t('wv_load_more'), 'aria-label': t('wb_receipts_more'), 'data-delivery-more': '', onClick: data.onMore}));
      delivery.append(h('h4', {}, t('wb_attempts')),
        ...(card.execution?.attempts || []).map(row => h('div', {class: 'pm-work-attempt', 'data-work-attempt': row.attempt_id},
          paragraph(row.accepted === true ? t('pm_kanban_accepted') : t('pm_kanban_accept_unknown'), ' · ',
            t('pm_kanban_state_' + labelSegment(row.status)).startsWith('pm_kanban_') ? t('ui_status_unknown') : t('pm_kanban_state_' + labelSegment(row.status))))));
    } else if (requirement) {
      // Requirement detail follows the traceability chain, rather than repeating its title.
      served.dataset.workSection = 'criteria'; served.querySelector('h3').textContent = t('wv_criteria');
      const criterionPill = item => {
        const result = item.source_input_id && data.criteriaResults?.find(row => row.criterion?.kind === 'acceptance'
          && row.criterion.requirement_id === requirement.id && row.criterion.input_id === item.source_input_id);
        const state = result ? ({accepted: 'accepted', rejected: 'failed', unassessed: 'not_assessed'})[result.status] || 'unknown'
          : data.acceptance === 'not_assessed' ? 'not_assessed' : 'unknown';
        const pill = statusPill(state);
        if (result?.status === 'rejected') {pill.textContent = t('pq_rejected'); pill.dataset.qualityResult = 'rejected';}
        return pill;
      };
      served.replaceChildren(h('h3', {}, t('wv_criteria')),
        ...(data.acceptance === 'accepted' && requirement.acceptance?.length ? [] : [statusPill(data.acceptance || 'unknown')]),
        h('ul', {}, ...(requirement.acceptance || []).map(item => h('li', {}, labelSegment(item.text), ' ',
          criterionPill(item)))));
      state.dataset.workSection = 'work'; state.querySelector('h3').textContent = t('wv_bound_work');
      for (const work of data.work || []) state.append(paragraph(button({variant: 'ghost', label: work.work.title, onClick: () => data.onWork?.(work.work_item_id)}), ' ', workStatusPill(t, work)));
      if (!data.work?.length) state.append(paragraph(t(data.workIncomplete ? 'wv_work_unknown' : 'wb_no_work')));
      if (data.workIncomplete) state.append(statusPill('partial'));
      for (const row of data.milestones || []) state.append(paragraph(h('a', {href: `#/pm/${encodeURIComponent(workspaceId)}/schedule?select=milestone:${encodeURIComponent(row.milestone_id)}`}, row.title)));
      const review = data.reviews?.at(-1);
      if (review) {
        const criteria = (review.assessment?.document?.criteria || []).filter(row => row.criterion?.kind === 'acceptance' && row.criterion.requirement_id === requirement.id);
        const breakdown = disclosure({label: t('wv_review_criteria'), key: `${workspaceId}:${requirement.id}:review-criteria`, body: h('ul', {}, ...criteria.map(row => h('li', {},
          labelSegment(requirement.acceptance?.find(item => item.source_input_id === row.criterion.input_id)?.text), ' ',
          statusPill(row.status === 'accepted' ? 'accepted' : 'unknown'))))});
        breakdown.dataset.requirementReviewCriteria = '';
        result.append(paragraph(t('wv_review_number', {n: review.sequence}), ' · ', stamp(review.assessment?.created_at ?? review.created_at)),
          paragraph(t('wv_review_basis')), breakdown);
      }
      else {
        const work = (data.work || []).filter(work => work.latest_capture).sort((a, b) => (b.latest_capture.captured_at || 0) - (a.latest_capture.captured_at || 0))[0];
        if (work) result.append(h('div', {'data-requirement-capture': ''}, paragraph(work.work.title),
          paragraph(t('pm_kanban_capture'), ' · ', stamp(work.latest_capture.captured_at)), ...captureFacts(work.latest_capture)));
        if (data.acceptance === 'unknown') result.append(paragraph(t('wv_acceptance_unknown')));
        else if (!work) result.append(paragraph(t('wv_no_result')));
      }
      blocker.dataset.workSection = 'decisions'; blocker.querySelector('h3').textContent = t('wv_decisions_changes');
      for (const row of data.register?.rows || []) blocker.append(paragraph(h('a', {href: `#/pm/${encodeURIComponent(workspaceId)}/issues?select=${encodeURIComponent(row.row_id)}`}, firstLabel(row.title, t('wv_related_record')))));
      if (!data.register?.rows.length) blocker.append(paragraph(t(data.register?.complete ? 'wv_no_decisions' : 'wv_decisions_unknown')));
      if (data.register && !data.register.complete) blocker.append(statusPill('partial'));
      delivery.append(statusPill(deployStatus(source)));
      for (const {target, rows} of prGroups(source.deliveries)) delivery.append(h('div', {class: 'pm-work-delivery', 'data-rtm-pr': target.pull_number},
        h('h4', {}, joinLabel([target.repository, `PR #${target.pull_number}`])),
        statusPill(rows.some(row => row.merge?.merged === true) ? 'merged' : 'unknown'), ' ',
        statusPill(rows.some(row => row.deployment?.verified === true && row.deployment.is_current === true) ? 'deployed' : 'unknown')));
      if (deliveryCoverage(source) === 'unknown') delivery.append(paragraph(t('wv_delivery_unknown')));
      else if (!source.deliveries.length && deliveryCoverage(source) === 'complete') delivery.append(paragraph(t('wv_no_delivery')));
    }
    // Details is last and retains original identities/timing, not a second interpretation.
    const details = disclosure({label: t('wv_details'), body: h('div', {},
      plan?.adoption ? paragraph(t('wb_approved_by', {actor: labelSegment(plan.adoption.adopted_by)}), ' · ', stamp(plan.adoption.created_at)) : null,
      card ? paragraph(t('pm_kanban_attempts', {count: Number.isSafeInteger(card.execution?.accepted_attempts) ? card.execution.accepted_attempts : t('ui_status_unknown')})) : null,
      card?.flow?.history !== 'available_central_events' ? paragraph(t('pm_kanban_history_unknown')) : null,
      card ? paragraph(t('pm_kanban_age'), ': ', elapsed(t, card.flow?.age_seconds) || t('ui_status_unknown')) : null,
      card?.execution?.coverage?.unavailable_sources || card?.latest_capture?.coverage?.inaccessible_sources ? paragraph(t('pm_kanban_missing')) : null,
      card?.execution?.coverage?.truncated ? paragraph(t('pm_kanban_truncated')) : null,
      paragraph(t('pm_kanban_execution')),
      ...['tasks', 'operations', 'sessions', 'attempts'].flatMap(kind => (card?.execution?.[kind] || []).map(row => paragraph(t('pm_kanban_source_time'), ' · ', stamp(row.observed_at)))),
      paragraph(t('pm_kanban_attempt_basis')), paragraph(t('wb_acceptance_separate')),
      paragraph(t('wv_ci_times_missing')),
      ...(card?.execution?.sessions || []).map(row => {
        const key = 'pm_kanban_state_' + labelSegment(row.runtime_status), label = t(key);
        return paragraph(t('pm_kanban_session'), ' · ', label === key ? t('ui_status_unknown') : label,
          h('span', {class: 'mono'}, ' · ', labelSegment(row.resource_id)));
      }),
      ...(card?.execution?.attempts || []).map(attempt => runtimeReceipt(t, attempt)),
      h('pre', {class: 'mono'}, JSON.stringify({reviews: data.reviews, criteria_results: data.criteriaResults, register: data.register, work_captures: data.work?.map(work => ({work_item_id: work.work_item_id, capture: work.latest_capture})), errors: source.errors, plan_revision: plan?.revision_id || card?.binding?.plan_revision_id, binding: card?.binding, work: card?.work,
        execution: card?.execution, flow: card?.flow, capture: card?.latest_capture, issues, deliveries: source.deliveries, quality: card?.quality}, (key, value) => value === null ? '—' : value, 2))),
      open: panel.body.querySelector('[data-work-section="details"]')?.open || false});
    details.dataset.workSection = 'details';
    const keys = new Set([...sections, details].map(node => node.dataset.workSection));
    if (panel.body.querySelector('[data-work-section="start"]')) keys.add('start');
    if (panel.body.querySelector('[data-work-section="attempt-result"]')) keys.add('attempt-result');
    for (const node of panel.body.querySelectorAll('[data-work-section]')) if (!keys.has(node.dataset.workSection)) node.remove();
    for (const node of [...sections, details]) {
      const previous = panel.body.querySelector(`[data-work-section="${node.dataset.workSection}"]`);
      if (!previous) panel.body.append(node);
      else if (!previous.isEqualNode(node)) {
        if (node.dataset.workSection === 'details') previous.querySelector('div').replaceWith(node.querySelector('div'));
        else previous.replaceChildren(...node.childNodes);
      }
    }
    const attemptResult = panel.body.querySelector('[data-work-section="attempt-result"]');
    const staticResult = panel.body.querySelector('[data-work-section="result"]');
    if (staticResult) {
      staticResult.hidden = Boolean(attemptResult && !attemptResult.hidden);
    }
  }
  return {
    show,
    close,
    dispose: close,
    get element() {return panel;},
    get selection() {return selection;},
    setAttemptResultVisible(visible) {
      // Close and board disposal null this panel before Result reports visibility.
      if (!panel) return;
      const res = panel.body.querySelector('[data-work-section="result"]');
      if (res) res.hidden = Boolean(visible);
    },
  };
}
