import {h} from './ui/dom.js';
import {inspector, statusPill, disclosure, agentMark, contextChip, menu, iconButton, button} from './ui/components.js';
import {reasonLabel} from './ui/ui-strings.js';
import {firstLabel, joinLabel, labelSegment} from './label-text.js';
import {discussHref} from './pm-discuss-link.js';
import {qualityFact} from './pm-quality-projections.js';

export const workStatus = card => ({backlog: 'not_started', in_progress: 'in_progress', waiting: 'waiting', review: 'in_review', accepted: 'done', archived: 'historical'})[typeof card?.column === 'string' ? card.column : ''] || 'unknown';
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
export function prPills(source) {
  return prGroups(source?.deliveries).map(({target, rows}) => h('span', {class: 'pm-work-pr', title: labelSegment(target.repository)},
    contextChip({text: `PR #${target.pull_number}`}), rows.some(row => row.merge?.merged === true) ? statusPill('merged') : statusPill('unknown')));
}
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
    const id = card.work_item_id, source = {deliveries: [], next: null, filtered: false, sessions: []};
    if (caps?.features?.pm_workspaces?.engineering_delivery) {
      let after = 0;
      for (let index = 0; index < deliveryPages; index++) {
        const data = checkDelivery(await api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}/work/${id}/deliveries?after=${after}&limit=20`), workspaceId, id, after, t);
        if (!valid()) return;
        source.deliveries.push(...data.deliveries); source.filtered ||= Boolean(data.source_visibility_filtered); source.next = data.next_after ?? null;
        if (source.next === null) break; after = source.next;
      }
    }
    if (caps) for (const ref of card.execution?.sessions || []) {
      const resource = labelSegment(ref.resource_id), slash = resource.indexOf('/');
      if (slash < 1 || !resource.slice(slash + 1)) continue;
      const host = resource.slice(0, slash), id = resource.slice(slash + 1);
      if (!sessions.has(resource)) sessions.set(resource, api('GET', `/sessions/${encodeURIComponent(host)}/${encodeURIComponent(id)}`).then(data => {
        if (data.session?.host !== host || data.session.session_id !== id) throw Error(t('pm_scope_mismatch'));
        return data.session;
      }));
      const session = await sessions.get(resource); if (!valid()) return; source.sessions.push(session);
    }
    result.set(id, source);
  }
  // Bounded fan-out on the read-only central endpoints.
  const queue = [...cards];
  await Promise.all(Array.from({length: Math.min(4, queue.length)}, async () => {while (queue.length && valid()) await read(queue.shift());}));
  return result;
}

// One inspector API for Requirements, Board and Schedule. It only renders authorized facts.
export function workInspector({t, workspaceId, from, mount, onClose = () => {}, tools = () => []}) {
  let panel = null, actions = null, signature = '', selection = null;
  const unknown = () => statusPill('unknown');
  const paragraph = (...nodes) => h('p', {}, ...nodes);
  const stamp = value => {
    const date = typeof value === 'number' && Number.isFinite(value) ? new Date(value * 1000) : null;
    return date && Number.isFinite(date.valueOf()) ? h('time', {datetime: date.toISOString()}, date.toISOString().replace('T', ' ').replace('.000Z', ' UTC')) : '—';
  };
  function close() {panel?.close();}
  function show(data) {
    selection = data;
    if (!panel) {
      panel = inspector({title: data.title || t('ui_work_item'), onClose: () => {actions?.dispose(); actions = null; panel = null; selection = null; signature = ''; onClose();}});
      panel.classList.add('pm-work-inspector'); panel.dataset.workInspector = ''; panel.dataset.rtmDetail = '';
      mount.append(panel);
    }
    const next = JSON.stringify(data);
    if (signature === next) return;
    signature = next;
    for (const node of panel.body.querySelectorAll(':scope > :not([data-work-section])')) node.remove();
    panel.querySelector('h2').textContent = data.title || t('ui_work_item');
    const {card, plan, requirement, requirements = [], source = {}, issues = []} = data;
    const refs = card ? [{kind: 'work_item', id: card.work_item_id}] : requirement && plan ? [{kind: 'requirement', id: requirement.id, revision_id: plan.revision_id}] : [];
    const href = discussHref({workspaceId, refs, title: data.title, from});
    actions?.dispose(); panel.querySelector('[data-work-actions]')?.remove();
    actions = menu({button: iconButton({icon: 'more-horizontal', label: t('ui_more')}), items: tools()});
    const actionRow = h('div', {class: 'pm-work-inspector-actions', 'data-work-actions': ''}, h('a', {href, class: 'ui-button primary', 'data-work-discuss': ''}, t('wv_discuss')),
      tools().length ? actions : null);
    panel.querySelector(':scope > header').append(actionRow);
    const sections = [
      ['requirements', 'wv_served'], ['status', 'wv_status_owner'], ['result', 'wv_result'], ['blocker', 'wv_blocker'], ['delivery', 'wv_delivery'],
    ].map(([key, label]) => h('section', {'data-work-section': key}, h('h3', {}, t(label))));
    const [served, state, result, blocker, delivery] = sections;
    const requirementRows = requirement ? [requirement] : requirements;
    served.append(...(requirementRows.length ? requirementRows.map(row => h('div', {}, paragraph(firstLabel(row.text, row.id)),
      row.acceptance?.length ? h('ul', {}, ...row.acceptance.map(item => h('li', {}, labelSegment(item.text)))) : null)) : [paragraph(t(card?.binding?.requirement_ids?.length ? 'ui_reason_unavailable' : 'wv_no_requirement'))]));
    if (card) {
      state.append(h('div', {class: 'pm-work-inline'}, statusPill(workStatus(card)), card.column === 'accepted' ? h('span', {}, t('wv_content_approved')) : null,
        ...workAgents(source).map(agentMark)), paragraph(t('wv_owner'), ' · ', labelSegment(card.assignment?.actor) || reasonLabel(t, card.assignment?.reason)));
      for (const session of source.sessions || []) state.append(paragraph(h('a', {
        href: `#/session/${encodeURIComponent(session.host)}/${encodeURIComponent(session.session_id)}`,
      }, firstLabel(session.title, t('wv_session'))), ' ', session.api_access === 'managed' && session.provenance === 'connector_managed' ? null : statusPill('read_only')));
      if (!workAgents(source).length) state.append(paragraph(t('wv_agent'), ' · ', unknown()));
      state.append(h('a', {href: `#/work/${encodeURIComponent(card.work_item_id)}`}, t('wv_work_details')));
      if (card.quality?.selected_assessment) result.append(qualityFact({h, t, fact: card.quality, current: true}));
      else result.append(paragraph(t('pm_kanban_quality_unknown')));
      const capture = card.latest_capture;
      if (capture) {
        result.append(paragraph(t('pm_kanban_capture'), ' · ', stamp(capture.captured_at)));
        if (card.capture_matches_binding === false || card.capture_matches_work_version === false) result.append(paragraph(t('pm_kanban_capture_different')));
        result.append(...(capture.evidence || []).map(row => paragraph(t('pm_kanban_fact'), ' · ',
          ...Object.entries(row.assessment || {}).map(([key, value]) => {
            const label = t('pm_kanban_field_' + key), state = t('pm_kanban_state_' + labelSegment(value));
            return joinLabel([label.startsWith('pm_kanban_') ? t('pm_kanban_fact') : label, state.startsWith('pm_kanban_') ? t('ui_status_unknown') : state]);
          }))));
      } else result.append(paragraph(t('wv_no_result')));
      blocker.append(paragraph(blockedWork(card) ? statusPill('blocked') : unknown(), ' ', blockerText(t, card)),
        paragraph(t('wv_next'), ' · ', h('a', {href}, t('wv_discuss'))));
      const groups = prGroups(source.deliveries);
      for (const {target, rows} of groups) delivery.append(h('div', {class: 'pm-work-delivery', 'data-rtm-pr': target.pull_number},
        h('h4', {}, joinLabel([target.repository, `PR #${target.pull_number}`])),
        h('div', {class: 'pm-work-inline'}, h('span', {}, t('wv_merge')), statusPill(rows.some(row => row.merge?.merged === true) ? 'merged' : 'unknown'),
          h('span', {}, t('wv_ci')), unknown()),
        !rows.some(row => row.deployment) ? paragraph(t('wv_deploy'), ' · ', unknown()) : null,
        ...rows.filter(row => row.deployment).map(row => paragraph(t('wv_deploy'), ' · ', statusPill(row.deployment.verified === true ? 'deployed' : 'unknown'),
          ' ', row.deployment.is_current === false ? t('wb_historical_deployment') : row.deployment.is_current === true ? t('wb_current_environment') : t('ui_status_unknown')))));
      for (const row of source.deliveries || []) if (!row.target?.pull_number) delivery.append(paragraph(t('wv_deploy'), ' · ', statusPill(row.deployment?.verified === true ? 'deployed' : 'unknown')));
      if (!(source.deliveries || []).length) delivery.append(paragraph(t('wv_no_delivery')));
      if (source.filtered || source.next != null) delivery.append(statusPill('partial'));
      if (source.next != null && data.onMore) delivery.append(button({label: t('wb_receipts_more'), onClick: data.onMore}));
      delivery.append(h('h4', {}, t('wb_attempts')), paragraph(t('wv_start_end')),
        ...(card.execution?.attempts || []).map(row => h('div', {class: 'pm-work-attempt', 'data-work-attempt': row.attempt_id},
          paragraph(row.accepted === true ? t('pm_kanban_accepted') : t('pm_kanban_accept_unknown'), ' · ',
            t('pm_kanban_state_' + labelSegment(row.status)).startsWith('pm_kanban_') ? t('ui_status_unknown') : t('pm_kanban_state_' + labelSegment(row.status))),
          // The existing projection has accepted_at/observed_at, neither proves execution start/end.
          paragraph(h('span', {title: t('wv_unknown_times')}, '— – —')))));
    } else {state.append(statusPill(data.accepted ? 'accepted' : 'not_assessed')); result.append(paragraph(t('wv_no_result'))); blocker.append(unknown()); delivery.append(paragraph(t('wb_acceptance_separate')));}
    // Details is last and retains original identities/timing, not a second interpretation.
    const details = disclosure({label: t('wv_details'), body: h('div', {},
      plan?.adoption ? paragraph(t('wb_approved_by', {actor: labelSegment(plan.adoption.adopted_by)}), ' · ', stamp(plan.adoption.created_at)) : null,
      card ? paragraph(t('pm_kanban_attempts', {count: Number.isSafeInteger(card.execution?.accepted_attempts) ? card.execution.accepted_attempts : t('ui_status_unknown')})) : null,
      card?.flow?.history !== 'available_central_events' ? paragraph(t('pm_kanban_history_unknown')) : null,
      card ? paragraph(t('pm_kanban_age'), ': ', typeof card.flow?.age_seconds === 'number' ? String(card.flow.age_seconds) : t('ui_status_unknown')) : null,
      card?.execution?.coverage?.unavailable_sources || card?.latest_capture?.coverage?.inaccessible_sources ? paragraph(t('pm_kanban_missing')) : null,
      card?.execution?.coverage?.truncated ? paragraph(t('pm_kanban_truncated')) : null,
      paragraph(t('pm_kanban_execution')),
      ...['tasks', 'operations', 'sessions', 'attempts'].flatMap(kind => (card?.execution?.[kind] || []).map(row => paragraph(t('pm_kanban_source_time'), ' · ', stamp(row.observed_at)))),
      paragraph(t('pm_kanban_attempt_basis')), paragraph(t('wb_acceptance_separate')),
      h('pre', {class: 'mono'}, JSON.stringify({plan_revision: plan?.revision_id || card?.binding?.plan_revision_id, binding: card?.binding, work: card?.work,
        execution: card?.execution, flow: card?.flow, capture: card?.latest_capture, issues, deliveries: source.deliveries, quality: card?.quality}, (key, value) => value === null ? '—' : value, 2))),
      open: panel.body.querySelector('[data-work-section="details"]')?.open || false});
    details.dataset.workSection = 'details';
    for (const node of [...sections, details]) {
      const previous = panel.body.querySelector(`[data-work-section="${node.dataset.workSection}"]`);
      if (!previous) panel.body.append(node);
      else if (!previous.isEqualNode(node)) {
        if (node.dataset.workSection === 'details') previous.querySelector('div').replaceWith(node.querySelector('div'));
        else previous.replaceChildren(...node.childNodes);
      }
    }
  }
  return {show, close, dispose: close, get element() {return panel;}, get selection() {return selection;}};
}
