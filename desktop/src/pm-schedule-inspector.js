// Narrow adapter for the shared work inspector: render(row, context) returns content only.
import {h} from './ui/dom.js';
import {t} from './i18n.js';
import {agentMark, disclosure, statusPill} from './ui/components.js';
import {reasonLabel} from './ui/ui-strings.js';
import {discussHref} from './pm-discuss-link.js';
import {dayNumber, milestoneDelta, scheduleRef, sessionAttempt, notObserved} from './pm-schedule-model.js';

export function rowStatus(row, data) {
  return notObserved(row, data) ? h('span', {class: 'ui-pill tone-neutral'}, t('sched_not_observed')) : statusPill(row.status);
}
export function dateRange(span) {
  if (!span) return t('sched_no_dates');
  const format = value => value?.includes('T') ? new Intl.DateTimeFormat(navigator.language, {dateStyle: 'medium', timeStyle: 'short', timeZone: 'UTC'}).format(new Date(value)) + ' UTC' : value;
  const validEnd = span.end && (!span.start || dayNumber(span.end) >= dayNumber(span.start)) ? span.end : null;
  if (validEnd && validEnd === span.start) return format(span.start) || '—';
  return [format(span.start) || '—', validEnd ? format(validEnd) : span.open === true ? t('sched_open') : reasonLabel(t, 'terminal_time_unrecorded')].join(' → ');
}
const unknown = () => h('span', {class: 'ui-meta'}, t('sched_unavailable'));
const section = (key, ...body) => h('section', {'data-schedule-section': key}, h('h3', {}, t('sched_' + key)), ...body);
export function render(row, {workspaceId, data}) {
  const refs = row.refs || {}, work = row.kind === 'attempt' || row.kind === 'issue' ? data.rows.find(value => value.id === row.parent_id) : row;
  const served = work?.refs || refs, reqIds = served.requirement_ids || (served.requirement_id ? [served.requirement_id] : []);
  const requirements = data.rows.filter(value => value.kind === 'requirement'
    && (!served.plan_revision_id || value.refs?.plan_revision_id === served.plan_revision_id)
    && reqIds.includes(value.refs?.requirement_id));
  const attempts = data.rows.filter(value => value.kind === 'attempt' && value.parent_id === (work?.id || row.id));
  const sessions = row.kind === 'attempt' ? [row] : attempts;
  const status = section('status', rowStatus(row, data));
  for (const key of ['baseline', 'forecast', 'actual'].filter(key => row[key])) {
    status.append(h('p', {}, t('sched_' + key), ': ', dateRange(row[key])));
  }
  const delta = milestoneDelta(row);
  if (row.kind === 'milestone' && delta !== null && delta !== 0) {
    status.append(h('p', {}, t('sched_delta', {n: delta > 0 ? '+' + delta : delta})));
  }
  if (sessionAttempt(row)) status.append(h('p', {class: 'ui-meta'},
    row.status === 'in_progress' ? t('sched_open') + ' · ' + t('sched_no_dates') : t('sched_no_dates')));
  for (const attempt of sessions) {
    const agent = attempt.refs?.agent, session = attempt.refs?.session;
    if (agent) status.append(h('div', {class: 'schedule-agent'}, agent.cli ? agentMark(agent.cli) : null, agent.cli || agent.model));
    if (session) status.append(h('div', {class: 'schedule-session'},
      h('a', {href: `#/session/${encodeURIComponent(session.host)}/${encodeURIComponent(session.session_id)}`}, t('sched_session')),
      session.read_only ? statusPill('read_only') : null));
  }
  const attemptList = h('ul', {class: 'schedule-attempts'}, ...attempts.map(attempt => h('li', {'data-schedule-attempt': attempt.id},
    h('strong', {}, attempt.title), h('div', {}, attempt.refs?.agent?.cli || attempt.refs?.agent?.model || t('sched_agent')),
    h('div', {}, rowStatus(attempt, data)), h('div', {class: 'ui-meta'}, sessionAttempt(attempt) ? t('sched_no_dates') : dateRange(attempt.actual)),
    sessionAttempt(attempt) && attempt.status === 'in_progress' ? h('span', {class: 'ui-meta'}, t('sched_open')) : null)));
  if (attempts.length) status.append(disclosure({label: t('sched_attempts'), body: attemptList, open: true}));
  const select = refs.work_item_id || work?.refs?.work_item_id || refs.requirement_id || refs.requirement_ids?.[0] || served.requirement_ids?.[0] || null;
  const links = h('div', {class: 'schedule-inspector-actions'},
    select ? h('a', {href: `#/pm/${encodeURIComponent(workspaceId)}/requirements?${new URLSearchParams({select})}`}, t('sched_requirements')) : null,
    h('a', {class: 'ui-button secondary', href: discussHref({workspaceId, refs: [scheduleRef(row, data)], title: row.title, from: 'schedule'}), 'data-schedule-discuss': ''}, t('sched_propose')));
  const notes = [...data.unknown || [], ...row.notes || []];
  const details = disclosure({label: t('sched_details'), body: h('div', {},
    h('div', {class: 'schedule-full-title'}, row.title),
    ...['baseline', 'forecast', 'actual'].filter(key => row[key]).map(key => h('p', {}, t('sched_' + key), ': ', dateRange(row[key]))),
    ...notes.map(note => h('p', {}, reasonLabel(t, note.reason), h('code', {}, note.reason))),
    h('code', {}, row.id), refs.issue ? h('code', {}, `${refs.issue.repository}#${refs.issue.number}`) : null,
    ...Object.values(refs).flatMap(value => typeof value === 'string' ? [h('code', {}, value)] : []),
    ...[data.baseline?.revision_id, data.forecast?.revision_id, data.as_of].filter(Boolean).map(value => h('code', {}, value))) });
  details.dataset.scheduleSection = 'details';
  return h('div', {class: 'schedule-inspector-content'},
    section('requirement', ...(requirements.length ? requirements.map(value => h('p', {class: 'schedule-full-title'}, value.title)) : [unknown()])),
    status, section('result', unknown()),
    section('blocker', ['blocked', 'waiting'].includes(row.status) ? statusPill(row.status) : unknown(), links),
    section('delivery', ['accepted', 'merged', 'deployed'].includes(row.status) ? statusPill(row.status) : unknown()), details);
}
