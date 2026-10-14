import {t} from './i18n.js';
import {actionLabel} from './global-labels.js';
import {cliName} from './pm-agents-trend.js';
import {button, disclosure, emptyState, iconButton, menu, pageHeader, sectionCard, statusPill} from './ui/components.js';
import {timeStamp} from './ui/stamps.js';
import {observedText as textOf} from './observed-resource-strings.js';

const say = (key, values) => textOf(t, key, values);
const text = value => typeof value === 'string' ? value.trim() : '';
const list = value => Array.isArray(value) ? value.filter(Boolean) : [];
const TASK_PILLS = {
  queued: 'not_started', dispatching: 'in_progress', accepted: 'in_progress', running: 'running',
  waiting_permission: 'asking_you', quota_limited: 'blocked', human_owned: 'needs_you', needs_ted: 'needs_you',
  verifying: 'in_review', done: 'done', failed: 'failed', uncertain: 'unknown', waiting: 'waiting',
};
// Only recorded creation kinds label an owner; unfamiliar kinds stay read-only.
function ownerKind(resource) {
  if (!text(resource?.intent_id)) return 'manual';
  const kind = text(resource?.intent_type);
  if (kind === 'task' || kind === 'registry') return kind;
  if (['operation', 'checkpoint.continue', 'integration.handoff', 'repository.continue', 'session.start'].includes(kind)) return 'operation';
  return 'manual';
}
function actionLink(h, href, label, variant) {
  return h('a', {class: `ui-button ${variant} ui-sm`, href}, label);
}
export function unavailableProject({h, error, collection = false, transportFailed = false, retry, connection}) {
  const denied = [401, 403].includes(error?.status), missing = error?.status === 404;
  const recoverable = error?.status === 503 || error?.status === 0 || transportFailed;
  const reason = denied ? 'or_project_denied' : missing ? (collection ? 'or_projects_missing' : 'or_project_missing') : 'or_connector_unavailable';
  const actions = [];
  if (!collection) actions.push(actionLink(h, '#/projects', say('or_back_projects'), 'secondary'));
  if (collection && denied && connection) actions.push(button({variant: 'secondary', label: say('or_connection_settings'), onClick: connection}));
  if (recoverable && retry) actions.push(button({variant: 'secondary', label: t('retry'), onClick: retry}));
  const state = emptyState({icon: 'circle-alert', title: say(collection ? 'or_projects_unavailable' : 'or_project_unavailable'),
    text: say(reason)});
  if (text(error?.code)) state.append(disclosure({label: say('or_details'), body: h('p', {class: 'mono'}, text(error.code))}));
  if (actions.length) state.append(h('div', {class: 'actions'}, ...actions));
  state.dataset.projectUnavailable = collection ? 'list' : 'project';
  return state;
}
function pairs(h, rows) {
  const kept = rows.filter(([, value]) => value !== null && value !== undefined && value !== '');
  return kept.length ? h('dl', {class: 'or-dl'}, ...kept.flatMap(([label, value]) => [
    h('dt', {}, label), h('dd', {class: typeof value === 'string' ? 'mono' : ''}, value)])) : null;
}
function fact(h, label, value) {
  return h('div', {class: 'or-fact'}, h('span', {class: 'or-label'}, label), h('div', {class: 'or-value'}, value));
}
function stamp(value) {
  return value === null || value === undefined || value === '' ? null : timeStamp(value);
}
function at(value) {
  if (typeof value === 'number') return Number.isFinite(value) ? value : 0;
  return text(value) ? Date.parse(value) / 1000 || 0 : 0;
}
function sessionHref(session) {
  return `#/session/${encodeURIComponent(session.host)}/${encodeURIComponent(session.session_id)}`;
}
function sessionRow(h, session) {
  const observation = session?.observation || {};
  const title = text(observation.title) || text(session?.title) || t('session_untitled');
  const fresh = !observation.stale && !observation.fields_stale && !observation.state?.evidence?.activity?.stale
    && !observation.gone_at && !['gone', 'missing'].includes(observation.state?.enumeration) && observation.state?.lifecycle !== 'ended';
  const activity = fresh && observation.pending ? statusPill('asking_you')
    : fresh && observation.streaming === true ? statusPill('running')
    : fresh && observation.streaming === false ? statusPill('waiting') : null;
  return h('div', {class: 'or-session', 'data-observed-session': ''},
    h('div', {class: 'or-session-name'},
      session?.host && session?.session_id ? h('a', {href: sessionHref(session)}, title) : h('span', {}, title),
      text(session?.host) ? h('span', {class: 'or-label', 'data-observed-session-host': ''}, session.host) : null), activity);
}
function pauseReason(task) {
  const event = list(task?.events).filter(row => row.kind === 'paused').sort((a, b) => at(b.created_at) - at(a.created_at))[0];
  try {
    const body = typeof event?.body === 'string' ? JSON.parse(event.body) : event?.body;
    return text(body?.reason);
  } catch {return '';}
}
function receipts(item) {
  return list(item?.delivered_to).filter(row => text(row.repository) && Number.isInteger(row.pull_number) && row.pull_number > 0)
    .sort((a, b) => at(b.delivered_at) - at(a.delivered_at));
}
function prAnchor(h, receipt) {
  return receipt ? h('a', {
    class: 'or-pr', href: `https://github.com/${receipt.repository}/pull/${receipt.pull_number}`,
    target: '_blank', rel: 'noopener', 'data-observed-pr': String(receipt.pull_number),
  }, say('or_pr', {n: receipt.pull_number})) : null;
}
function deliveryPill(item) {
  if (item?.unavailable || !item?.result?.status) return statusPill('unknown');
  if (item.result.status === 'failed') return statusPill('failed');
  if (item.result.status !== 'unverified') return statusPill('unknown');
  // The shared neutral pill supplies the styling; dispatch acceptance never proves a verified result.
  const pill = statusPill('not_assessed');
  pill.textContent = say('or_unverified');
  return pill;
}
function workName(item) {
  return text(item?.title) || text(item?.branch) || (item?.action ? actionLabel(item.action) : '') || say('or_work');
}
function workLinks(item, repositories, caps) {
  const links = [];
  if (item.operation_id) links.push(['operation', say('or_link_operation'), `#/op/${encodeURIComponent(item.operation_id)}`]);
  if (item.task_id) links.push(['execution', say('or_link_execution'), `#/task/${encodeURIComponent(item.task_id)}`]);
  if (item.host && item.session_id) links.push(['session', say('or_open_session'), sessionHref(item)]);
  if (item.worktree_id) links.push(['worktree', say('or_link_worktree'), `#/worktree/${encodeURIComponent(item.worktree_id)}`]);
  if (item.eligible && item.host && item.kind && item.id && caps?.features?.execution_delivery?.version === 1) {
    const repo = item.repository || (repositories.length === 1 ? repositories[0] : '');
    links.push(['review', say('or_review'), `#/delivery/${[item.host, item.kind, item.id, repo].map(encodeURIComponent).join('/')}`]);
  }
  return links;
}
function rawRecord(h, value) {
  const nested = disclosure({label: say('or_raw'), body: h('pre', {class: 'pre'}, JSON.stringify(value ?? {}, null, 2))});
  nested.dataset.observedRaw = '';
  return nested;
}
function detailsBlock(body, key) {
  const root = disclosure({label: say('or_details'), body, key});
  root.dataset.observedDetails = '';
  return root;
}
function resultReason(h, item) {
  return h('p', {class: 'or-note'}, item?.unavailable ? say('or_unavailable')
    : item?.result?.status === 'unverified' ? say('or_verification_reason') : say('or_result_unknown'));
}
function technicalWork(h, item, links) {
  return h('div', {class: 'or-tech-work'}, pairs(h, [
    [say('or_actor'), text(item.actor)], [say('or_host'), text(item.host)], [say('or_branch'), text(item.branch)],
    [say('or_work_id'), text(item.id)], [say('or_operation_id'), text(item.operation_id)],
    [say('or_task_id'), text(item.task_id)], [say('or_session_id'), text(item.session_id)],
    [say('or_worktree_id'), text(item.worktree_id)], [say('or_path'), text(item.worktree_path)],
    [say('or_dispatch'), text(item.status)], [say('or_reason'), text(item.unavailable?.code)],
  ]), resultReason(h, item), stamp(item.created_at),
  ...receipts(item).map(receipt => h('div', {class: 'or-receipt'}, prAnchor(h, receipt), stamp(receipt.delivered_at), pairs(h, [
    [say('or_operation_id'), text(receipt.operation_id)], [say('or_pinned'), text(receipt.pinned_sha)],
    [say('or_delivered'), text(receipt.delivered_sha)],
  ]))), links.length ? h('div', {class: 'or-links'}, ...links.map(([, label, href]) => h('a', {href}, label))) : null);
}
function renderWorktree({h, id, resource, caps, namespace}) {
  const kind = ownerKind(resource), work = list(resource?.work), sessions = list(resource?.known_sessions);
  const latest = [...work].sort((a, b) => Math.max(at(b.created_at), at(receipts(b)[0]?.delivered_at))
    - Math.max(at(a.created_at), at(receipts(a)[0]?.delivered_at)))[0];
  const receipt = receipts(latest)[0], session = sessions.find(item => item.host && item.session_id);
  const ownerWork = work.find(item => kind === 'task' ? item.task_id === resource.intent_id : item.operation_id === resource.intent_id);
  const ownerHref = kind === 'task' ? `#/task/${encodeURIComponent(resource.intent_id)}`
    : kind === 'operation' ? `#/op/${encodeURIComponent(resource.intent_id)}` : null;
  const owner = h('span', {'data-observed-owner': kind}, say(`or_owner_${kind}`), ownerHref
    ? [' · ', h('a', {href: ownerHref}, text(ownerWork?.title) || say(kind === 'task' ? 'or_link_execution' : 'or_link_operation'))] : null);
  const actions = [];
  if (session) actions.push(actionLink(h, sessionHref(session), say('or_open_session'), 'primary'));
  if (kind !== 'manual' && (kind === 'task' || resource.host))
    actions.push(actionLink(h, `#/cleanup/${kind === 'task' ? 'task' : 'host'}/${encodeURIComponent(kind === 'task' ? resource.intent_id : resource.host)}`, say('or_cleanup_preview'), 'secondary'));
  const details = detailsBlock(h('div', {}, pairs(h, [
    [say('or_worktree_id'), text(resource?.resource_id || id)], [say('or_owner_id'), text(resource?.intent_id)],
    [say('or_owner_kind'), text(resource?.intent_type)], [say('or_path'), text(resource?.worktree_path)],
    [say('or_branch'), text(resource?.branch)],
  ]), h('p', {class: 'or-note'}, say('or_identity')), h('p', {class: 'or-note'}, say('or_cleanup')),
  ...work.map(item => technicalWork(h, item, workLinks(item, [], caps))),
  !latest ? h('p', {class: 'or-note'}, say('or_result_unknown')) : null, rawRecord(h, resource)), `observed.${namespace}.worktree.${id}`);
  return [
    pageHeader({title: text(work[0]?.title) || text(resource?.branch) || say('or_worktree'),
      meta: [resource?.host, resource?.branch].map(text).filter(Boolean).join(' · '), actions}),
    sectionCard({body: h('div', {class: 'or-body'}, fact(h, say('or_ownership'), owner),
      fact(h, say('or_latest'), h('span', {class: 'or-result', 'data-observed-result': ''},
        latest ? deliveryPill(latest) : statusPill('unknown'), prAnchor(h, receipt), stamp(receipt?.delivered_at ?? latest?.created_at))),
      sessions.length ? h('div', {class: 'or-sessions'}, h('span', {class: 'or-label'}, say('or_sessions')), ...sessions.map(item => sessionRow(h, item))) : null,
      details)}),
  ];
}
function renderExecution({h, id, task, caps, namespace}) {
  const choices = [];
  if (task?.host && task?.session_id) choices.push([say('or_open_session'), sessionHref(task)]);
  if (caps?.artifacts?.capture?.managed_single_file === true) choices.push([say('or_review'), `#/artifact-review/task/${encodeURIComponent(id)}`]);
  if (caps?.features?.cleanup_task === true) choices.push([say('or_cleanup_preview'), `#/cleanup/task/${encodeURIComponent(id)}`]);
  const pause = task?.paused === true || task?.paused === 1 ? statusPill('stopped') : null;
  if (pause) {pause.dataset.taskDispatch = ''; pause.textContent = say('or_paused');}
  const reason = pause ? pauseReason(task) : '';
  const delivery = task?.delivery, stage = delivery?.stage;
  const resultPill = ['merged', 'deployed', 'adopted'].includes(stage) ? statusPill(stage)
    : statusPill(stage === 'verified' ? 'accepted' : 'unknown');
  if (stage === 'verified') {resultPill.dataset.status = 'verified'; resultPill.textContent = say('or_verified');}
  const title = text(task?.title) || text(task?.original_words).split('\n').find(Boolean) || text(task?.external_branch) || say('or_execution');
  return [pageHeader({title, meta: [task?.host, task?.external_branch].map(text).filter(Boolean).join(' · '),
    actions: choices.slice(0, 2).map(([label, href], i) => actionLink(h, href, label, i ? 'secondary' : 'primary'))}),
  sectionCard({body: h('div', {class: 'or-body'},
    fact(h, say('or_ownership'), say('or_owner_task')),
    text(task?.lead_agent) ? fact(h, say('or_owner'), cliName(text(task.lead_agent))) : null,
    fact(h, say('or_state'), statusPill(TASK_PILLS[task?.state] || 'unknown')),
    pause ? fact(h, say('or_dispatch'), h('span', {class: 'or-result'}, pause,
      reason ? h('span', {'data-observed-pause-reason': ''}, reason) : null)) : null,
    fact(h, say('or_latest'), h('span', {class: 'or-result'}, resultPill, stamp(delivery?.[stage]?.at))),
    detailsBlock(h('div', {}, pairs(h, [
      [say('or_task_id'), text(task?.task_id || id)], [say('or_session_id'), text(task?.session_id)],
      [say('or_project'), text(task?.project)], [say('or_branch'), text(task?.external_branch)],
      [say('or_path'), text(task?.external_worktree_path)], [say('or_state'), text(task?.state)],
    ]), choices.length > 2 ? h('div', {class: 'or-links'}, ...choices.slice(2).map(([label, href]) => h('a', {href}, label))) : null,
    !stage ? h('p', {class: 'or-note'}, say('or_result_unknown')) : null,
    h('p', {class: 'or-note'}, say('or_identity')), h('p', {class: 'or-note'}, say('or_cleanup')), rawRecord(h, task)), `observed.${namespace}.execution.${id}`))})];
}
export function renderObservedResource({h, type, id, resource, caps, namespace = ''}) {
  return type === 'execution' ? renderExecution({h, id, task: resource, caps, namespace}) : renderWorktree({h, id, resource, caps, namespace});
}
export function renderDeliveryWork(item, repositories = [], compact = false, {h, caps, namespace = ''} = {}) {
  const links = workLinks(item, list(repositories), caps), review = links.find(([key]) => key === 'review');
  const other = links.filter(([key]) => key !== 'review');
  const overflow = !compact && other.length ? menu({button: iconButton({icon: 'more-horizontal', label: t('ui_more')}),
    items: other.map(([key, label, href]) => ({key, label, href}))}) : null;
  const details = detailsBlock(technicalWork(h, item, compact ? other : []), `observed.${namespace}.work.${item.kind}.${item.id}`);
  return h('div', {class: 'or-work', 'data-project-work': item.id},
    h('div', {class: 'or-work-main'}, h('span', {class: 'or-work-title'}, workName(item)), deliveryPill(item), prAnchor(h, receipts(item)[0])),
    review || overflow ? h('div', {class: 'or-work-actions'}, review ? actionLink(h, review[2], review[1], 'secondary') : null, overflow) : null, details);
}
