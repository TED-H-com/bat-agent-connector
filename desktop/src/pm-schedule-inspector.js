// Narrow adapter for the shared work inspector: render(row, context) returns content only.
import {h} from './ui/dom.js';
import {t} from './i18n.js';
import {agentMark, disclosure, statusPill} from './ui/components.js';
import {reasonLabel} from './ui/ui-strings.js';
import {discussHref} from './pm-discuss-link.js';
import {scheduleRef, sessionAttempt} from './pm-schedule-model.js';

export function notObserved(row, data) {
  return sessionAttempt(row) && row.status === 'unknown' && !row.refs?.recorded_state && !row.refs?.agent
    && (data.unknown || []).some(note => note.reason === 'session_not_observed');
}
export function rowStatus(row, data) {
  return notObserved(row, data) ? h('span', {class: 'ui-pill tone-neutral'}, t('sched_not_observed')) : statusPill(row.status);
}
export function dateRange(span) {
  if (!span) return t('sched_no_dates');
  const start = span.start?.slice(0, 10), end = span.end?.slice(0, 10);
  return [start || '—', end && (!start || end >= start) ? end : t('sched_open')].join(' → ');
}
const unknown = () => h('span', {class: 'ui-meta'}, t('sched_unavailable'));
const section = (key, ...body) => h('section', {'data-schedule-section': key}, h('h3', {}, t('sched_' + key)), ...body);
export function render(row, {workspaceId, data}) {
  const refs = row.refs || {}, work = row.kind === 'attempt' ? data.rows.find(value => value.id === row.parent_id) : row;
  const served = work?.refs || refs;
  const requirements = data.rows.filter(value => value.kind === 'requirement'
    && value.refs?.plan_revision_id === served.plan_revision_id
    && (served.requirement_ids || [served.requirement_id]).includes(value.refs?.requirement_id));
  const attempts = data.rows.filter(value => value.kind === 'attempt' && value.parent_id === (work?.id || row.id));
  const sessions = row.kind === 'attempt' ? [row] : attempts;
  const status = section('status', rowStatus(row, data));
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
  const select = refs.work_item_id || work?.refs?.work_item_id || refs.requirement_id || row.id;
  const links = h('div', {class: 'schedule-inspector-actions'},
    h('a', {href: `#/pm/${encodeURIComponent(workspaceId)}/requirements?${new URLSearchParams({select})}`}, t('sched_requirements')),
    h('a', {class: 'ui-button secondary', href: discussHref({workspaceId, refs: [scheduleRef(row, data)], title: row.title, from: 'schedule'}), 'data-schedule-discuss': ''}, t('sched_propose')));
  const notes = [...data.unknown || [], ...row.notes || []];
  const details = disclosure({label: t('sched_details'), body: h('div', {},
    h('div', {class: 'schedule-full-title'}, row.title),
    ...['baseline', 'forecast', 'actual'].filter(key => row[key]).map(key => h('p', {}, t('sched_' + key), ': ', dateRange(row[key]))),
    ...notes.map(note => h('p', {}, reasonLabel(note.reason), h('code', {}, note.reason))),
    h('pre', {class: 'pm-brief-proof'}, JSON.stringify({id: row.id, refs, baseline: data.baseline, forecast: data.forecast, as_of: data.as_of}, null, 2))) });
  details.dataset.scheduleSection = 'details';
  return h('div', {class: 'schedule-inspector-content'},
    section('requirement', ...(requirements.length ? requirements.map(value => h('p', {class: 'schedule-full-title'}, value.title)) : [unknown()])),
    status, section('result', unknown()),
    section('blocker', ['blocked', 'waiting'].includes(row.status) ? statusPill(row.status) : unknown(), links),
    section('delivery', ['accepted', 'merged', 'deployed'].includes(row.status) ? statusPill(row.status) : unknown()), details);
}
