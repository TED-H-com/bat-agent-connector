import {overviewAssessment} from './pm-overview-assessment.js';
import {relativeAge} from './relative-age.js';
import {proof} from './pm-brief.js';
import {timeStamp, timeLabel} from './ui/stamps.js';
import {workspaceCharter, charterSections} from './pm-charter.js';
import {planDocument} from './pm-plans.js';
import {discussHref, isContextRef} from './pm-discuss-link.js';
import {discussLink, button, sectionCard, metric, statusPill, disclosure, emptyState, skeleton, inspector} from './ui/components.js';
import {reasonLabel} from './ui/ui-strings.js';
import {icon} from './ui/icons.js';
import {viewState} from './ui/view-state.js';
import {locale} from './i18n.js';
import {acceptedFinding, findingName, findingSeverity} from './ui/finding-name.js';

const domains = ['definition', 'scope', 'schedule', 'resources', 'risks', 'decisions'];
const classes = ['accepted', 'in_review', 'in_progress', 'blocked', 'not_started'];
const phaseNames = ['idea', 'planning', 'development', 'maintain', 'paused', 'closed'];

// Live projection and immutable reports deliberately have separate render inputs.
export function workspaceOverview(options) {
  const {h, t, api, alive, namespace, workspaceId, onOpen = () => {}, onHeader = () => {}} = options;
  const endpoint = `/pm/workspaces/${encodeURIComponent(workspaceId)}`;
  const preference = viewState(namespace, workspaceId, 'overview');
  let serial = 0, projection = null, plan = null, saved = null, active = false, reader = null, reportsLoaded = false, reportRead = null;
  const parts = new Map(), attentionNodes = new Map();
  let attentionCard = null, attentionList = null, readerDocument = null, relatedSelection = null;
  let mode = preference.mode === 'saved' ? 'saved' : 'live', signature = '', loading = false, reportLoading = false;
  const primary = discussLink({workspaceId}, {'data-overview-discuss':''});
  const meta = h('span', {'data-overview-meta': ''});
  const content = h('div', {class: 'pm-overview-content', 'data-overview-content': ''}), status = h('div', {role: 'status'}), savedBar = h('div', {class: 'pm-overview-saved', hidden: true});
  const box = h('section', {class: 'pm-overview', 'data-pm-overview': ''}, savedBar, status, content);
  const reportTools = h('div', {class: 'pm-report-tools', 'data-pm-report-tools': ''});
  const historyHint = h('span', {class: 'ui-meta', hidden: true, 'data-overview-history-partial': ''}, t('ov_partial_history'));
  const reportPartial = statusPill('partial'); reportPartial.hidden = true;
  const reportControls = flags => {historyHint.hidden = !flags.historyPartial;for (const [key, disabled] of Object.entries(flags)) {
    const node = savedBar.querySelector(`[data-overview-report-control="${key}"]`); if (node) node.disabled = disabled;
  }};
  const reports = workspaceCharter({...options, onChange: reportChanged, onControls: reportControls});
  const reportStatus = reports.status, reportStatusHome = reportStatus.parentNode;
  for (const node of reports.box.querySelectorAll(':scope > .pm-workbench-toolbar, :scope > .pm-charter-save, :scope > [data-pm-brief-export]')) reportTools.append(node);
  // Reuse the original comparison, pagination, export and durable form controls.
  reportTools.append(reports.comparison);
  const back = button({variant: 'ghost', label: t('ov_back_live'), 'data-overview-live': '', onClick: () => {
    mode = preference.mode = 'live'; projection = null; plan = null; loading = true; signature = ''; closeReader(); reports.comparison.replaceChildren(); render(); void refreshLive();
  }});
  const reportLabel = h('span');
  reports.previous.setAttribute('aria-label', t('ov_report_previous'));
  reports.next.setAttribute('aria-label', t('ov_report_next'));
  reports.previous.dataset.overviewReportControl = 'previous'; reports.next.dataset.overviewReportControl = 'next';
  savedBar.append(reportLabel, reportPartial, historyHint, h('span', {class: 'pm-overview-report-arrows'}, reports.previous, reports.next),
    button({variant: 'ghost', label: t('ui_compare'), 'data-overview-report-control': 'compare', onClick: () => options.onCompare?.()}), back);
  reportControls(reports.controls());
  const date = value => timeStamp(value, {dateOnly: typeof value === 'string' && !value.includes('T')});
  const refs = () => {
    const revision = mode === 'saved' ? saved?.baseline?.document_revision : projection?.plan?.revision_id;
    return [...(revision ? [{kind: 'plan_revision', id: revision}] : []), ...(mode === 'saved' && saved ? [{kind: 'snapshot', id: saved.snapshot_id}] : [])];
  };
  const assessment = options.caps()?.features?.pm_workspaces?.overview_assessment === true
    ? overviewAssessment({...options, refs}) : null;
  function header() {
    const current = mode === 'saved' ? saved : projection;
    primary.href = discussHref({workspaceId, refs: refs(), title: mode === 'saved' ? saved?.title : projection?.plan?.title, from: 'overview', report: mode === 'saved' ? saved?.snapshot_id : undefined});
    meta.replaceChildren(...(mode === 'saved' ? saved ? [t('ov_report', {n: saved.sequence}), ' · ', date(saved.as_of)] : [t(reportLoading ? 'ov_loading_report' : 'ov_not_saved')]
      : [projection?.plan?.title, projection?.plan?.adopted_at != null ? timeLabel(t, 'ov_adopted', {date: date(projection.plan.adopted_at)}) : null, t('ov_live')].filter(Boolean).flatMap((part, i) => i ? [' · ', part] : [part])));
    const phase = mode === 'live' && current?.lifecycle?.stage;
    onHeader({meta, phase: phaseNames.includes(phase) ? t('ui_phase_' + phase) : null, active});
  }
  function reportChanged(value, event) {
    if (event === 'loading' || event === 'selected') reportLoading = true;
    if (['updated', 'withdrawn', 'error'].includes(event)) reportLoading = false;
    if (event === 'selected') {mode = preference.mode = 'saved'; saved = null; closeReader(); signature = '';}
    if (event === 'updated') saved = value;
    if (event === 'withdrawn') {saved = null; closeReader(); signature = '';}
    // Keep Charter's status element intact: withdrawal and retry still clear the
    // same node, even when saved mode displays it in the report view.
    if (event === 'error') {
      if (mode === 'saved') status.replaceChildren(reportStatus);
      else reportStatusHome.append(reportStatus);
    }
    // Construction does not notify; callbacks run after the controls are mounted.
    if (mode === 'saved') render();
  }
  function attentionTitle(row) {
    const fallback = typeof row?.title === 'string' ? row.title : '';
    return findingName(row?.finding, locale, fallback);
  }
  function discuss(row, part = 'attention') {
    return discussLink({workspaceId, refs: row.ref ? [row.ref] : [], title: attentionTitle(row), from:'overview'}, {'data-overview-row-discuss': row.key || row.milestone_id, 'data-overview-link': `${part}:${row.key || row.milestone_id}:discuss`});
  }
  function open(row) {onOpen(row.ref, row);}
  function itemLink(row, part) {return row ? h('button', {type: 'button', class: 'pm-overview-link', 'data-overview-link': `${part}:${row.key || row.milestone_id || row.work_item_id}`, onclick: () => open(row)}, row.title) : null;}
  function milestoneDate(row) {
    const changed = row.forecast_date && row.forecast_date !== row.baseline_date;
    const late = row.delta_days > 0 ? h('span', {class: 'pm-overview-late'}, t('ov_delta', {n: row.delta_days})) : null;
    return h('span', {class: 'ui-meta pm-overview-date-range', 'data-overview-date-range': ''},
      h('span', {}, date(row.baseline_date), !changed ? late : null),
      changed ? h('span', {}, ' → ', date(row.forecast_date), late) : null);
  }
  function statusStrip(data, fixed = false) {
    const p = data?.progress, complete = p && p.complete === true && Number.isSafeInteger(p.requirements) && classes.every(key => Number.isSafeInteger(p[key]));
    const acceptanceGap = !fixed && complete && p.complete === true && Number.isSafeInteger(p.requirements)
      && p.accepted < p.requirements && data.recent_deliveries?.length > 0;
    const progressTitle = acceptanceGap ? `${t('ov_progress')} · ${t('ov_accepted', {accepted: p.accepted, total: p.requirements})}` : '';
    const askProgress = acceptanceGap ? discussLink({workspaceId, refs: refs(), title: progressTitle, from: 'overview'},
      {'data-overview-ask-progress': '', 'data-overview-link': 'status:progress:discuss', class: 'ui-button secondary ui-sm'}) : null;
    if (askProgress) askProgress.textContent = t('ov_ask_pm');
    const goalReason = (data?.unknown || []).find(row => row.field === 'plan.goal')?.reason;
    const goal = metric({label: t('ov_goal'), value: data?.plan?.goal ?? '—', context: h('div', {},
      !data?.plan?.goal ? goalReason === 'goal_unconfirmed' ? statusPill('unconfirmed') : !goalReason || goalReason === 'unknown' ? statusPill('unknown') : h('span', {class: 'ui-meta'}, reasonLabel(t, goalReason)) : null,
      fixed ? h('div', {}, data?.plan?.title || '—') : null,
      fixed && data?.plan?.adopted_at != null ? h('div', {}, timeLabel(t, 'ov_adopted', {date: date(data.plan.adopted_at)})) : null)});
    goal.dataset.overviewMetric = 'goal';
    if (fixed && savedPartial()) goal.append(statusPill('partial'));
    const next = data?.next_milestone;
    const progress = metric({label: t('ov_progress'), value: !p || fixed ? null : complete ? t('ov_accepted', {accepted: p.accepted, total: p.requirements}) : t('ov_requirements', {n: p.requirements}),
      context: h('div', {}, fixed && p ? h('div', {}, t('ov_requirements', {n: p.requirements})) : p && !complete ? statusPill('partial') : null,
        !p && (data?.unknown || []).some(row => row.reason === 'no_adopted_plan') ? h('a', {href: `#/pm/${encodeURIComponent(workspaceId)}/discussions`}, reasonLabel(t, 'no_adopted_plan')) : null,
        acceptanceGap ? h('div', {'data-overview-acceptance-gap': ''}, h('p', {class: 'pm-overview-acceptance-gap'}, t('ov_acceptance_gap')),
          h('div', {class: 'pm-overview-progress-actions'}, h('a', {class: 'ui-button secondary ui-sm', href: `#/pm/${encodeURIComponent(workspaceId)}/requirements`,
            'data-overview-unaccepted': '', 'data-overview-link': 'status:progress:requirements'}, t('ov_see_unaccepted')), askProgress)) : null,
        next ? h('div', {class: 'pm-overview-next'}, next.title, h('div', {}, date(next.forecast_date || next.baseline_date), next.delta_days > 0 ? h('span', {class: 'pm-overview-late'}, t('ov_delta', {n: next.delta_days})) : null)) : null),
      // The headline and track both measure acceptance; other states stay in Details.
      bar: complete ? [{tone: 'ok', value: p.accepted}, {tone: 'neutral', value: p.requirements - p.accepted}] : null});
    progress.dataset.overviewMetric = 'progress';
    if (complete) {
      const counts = disclosure({label: t('ov_details'), key: `${namespace}:${workspaceId}:progress`, body: h('ul', {}, ...classes.map(key => h('li', {}, statusPill(key), ` ${p[key]}`)))});
      counts.querySelector('summary').dataset.overviewLink = 'status:progress:details';
      progress.append(counts);
    }
    const metrics = ['blocker', 'decision'].map(kind => {
      const first = data?.attention?.find(row => row.kind === kind);
      const node = metric({label: t(kind === 'blocker' ? 'ov_blockers' : 'ov_decisions'), value: data?.attention_counts?.[kind] ?? null,
        context: h('div', {}, kind === 'decision' && first ? h('div', {class: 'pm-overview-decision-row', 'data-overview-open-decision': first.key}, itemLink(first, 'status'), discuss(first, 'status')) : itemLink(first, 'status'),
          kind === 'decision' && !fixed ? recentDecisions(data) : null,
          kind === 'decision' && data?.attention_truncated ? statusPill('partial') : null,
          kind === 'decision' && data?.attention_counts?.decision > (data?.attention || []).filter(row => row.kind === 'decision').length
            ? h('a', {class: 'ui-button secondary ui-sm', href: `#/pm/${encodeURIComponent(workspaceId)}/issues`}, t('ov_more', {n: data.attention_counts.decision - (data?.attention || []).filter(row => row.kind === 'decision').length})) : null)});
      node.dataset.overviewMetric = kind; return node;
    });
    return h('div', {class: 'pm-overview-status', 'data-overview-status': ''}, goal, progress, ...metrics);
  }
  function age(since, past = false) {
    if (!Number.isFinite(since) || !Number.isFinite(projection?.as_of)) return null;
    const seconds = Math.max(0, projection.as_of - since);
    return relativeAge(seconds, t, {direction: past ? 'past' : 'duration'});
  }
  const decisionMeta = row => [age(row.decided_at, true), row.actor].filter(Boolean).join(' · ');
  function recentDecisions(data) {
    const rows = Array.isArray(data?.recent_decisions) ? data.recent_decisions : null;
    const partial = rows && (data.recent_decisions_complete !== true || data.recent_decisions_truncated === true);
    return h('section', {class: 'pm-overview-decisions' + (rows?.length === 0 && !partial ? ' pm-overview-decisions-empty' : ''), 'data-overview-recent-decisions': ''},
      h('h3', {}, t('ov_recent_decisions')),
      ...(rows || []).map(row => h('div', {'data-overview-decided': row.key}, itemLink(row, 'decided'),
        h('div', {class: 'ui-meta', 'data-overview-decided-meta': ''}, decisionMeta(row)))),
      !rows ? statusPill('unknown') : partial ? h('div', {class: 'ui-meta'}, statusPill('partial'), ' ', t('ov_showing', {n: rows.length}))
        : !rows.length && data.recent_decisions_count === 0 ? h('span', {class: 'ui-meta'}, t('ov_no_items')) : null);
  }
  function contextText(row) {
    const c = row.context || {}, ids = c.requirement_ids || (c.requirement_id ? [c.requirement_id] : []);
    const labels = ids.map(id => plan?.document?.requirements?.find(req => req.id === id)?.text).filter(Boolean);
    return labels.join(' · ') || (ids.length ? t('ov_context_requirement', {n: ids.length}) : c.work_item_id ? t('ov_context_work') : c.milestone_id ? t('ov_context_milestone') : '');
  }
  function attention(data, resetOrder) {
    if (!attentionList) {attentionList = h('div', {class: 'pm-overview-attention', 'data-overview-attention': ''}); attentionCard = sectionCard({title: t('ov_attention'), body: attentionList});}
    const rows = attentionList;
    const reading = !resetOrder && (rows.contains(document.activeElement) || Boolean(rows.querySelector('[data-overview-row]:hover'))
      || attentionSelection());
    const keys = new Set(data.attention.map(row => row.key));
    for (const [key, entry] of attentionNodes) if (!keys.has(key)) {entry.node.remove(); attentionNodes.delete(key);}
    rows.querySelector('.pm-overview-footer')?.remove(); rows.querySelector('.ui-empty')?.remove();
    const existingOrder = [...rows.children].map(node => node.dataset.overviewRow);
    const ordered = reading ? [...data.attention].sort((a, b) => {
      const ai = existingOrder.indexOf(a.key), bi = existingOrder.indexOf(b.key);
      return (ai < 0 ? Infinity : ai) - (bi < 0 ? Infinity : bi);
    }) : data.attention;
    let position = 0;
    for (const row of ordered) {
      const rowSignature = JSON.stringify([row, contextText(row)]), cached = attentionNodes.get(row.key);
      if (cached?.signature === rowSignature) {
        if (rows.children[position] !== cached.node) rows.insertBefore(cached.node, rows.children[position] || null);
        position++; continue;
      }
      const label = ['blocker', 'decision', 'deviation', 'risk'].includes(row.kind) ? t('ov_' + row.kind) : reasonLabel(t, 'unknown');
      const elapsed = age(row.since);
      const severity = findingSeverity(acceptedFinding(row.finding)?.severity, locale);
      const link = h('button', {type: 'button', class: 'pm-overview-attention-main', 'data-overview-open': row.key, 'data-overview-link': `attention:${row.key}:open`, onclick: () => open(row)},
        h('span', {class: 'pm-overview-kind', title: label, 'aria-label': label}, icon(row.kind === 'decision' ? 'message-square' : 'circle-alert')),
        h('span', {}, h('strong', {}, attentionTitle(row))));
      const node = h('div', {class: 'pm-overview-attention-row', 'data-overview-row': row.key}, link,
        h('div', {class: 'pm-overview-attention-meta'}, h('span', {class: 'pm-overview-context'}, [label, contextText(row)].filter(Boolean).join(' · '),
          elapsed === null ? null : h('span', {}, ' · ', h('span', {'data-overview-age': ''}, elapsed))),
          h('span', {class: 'pm-overview-attention-actions'},
            severity ? h('span', {class: `ui-pill tone-${severity.tone}`, 'data-finding-severity': severity.severity}, severity.label) : null,
            discuss(row))));
      cached?.node.remove(); attentionNodes.set(row.key, {signature: rowSignature, node});
      rows.insertBefore(node, rows.children[position] || null); position++;
    }
    if (!data.attention.length) rows.append(emptyState({title: t(data.coverage?.complete ? 'ov_empty' : 'ov_incomplete')}));
    if (data.attention_truncated) rows.append(h('div', {class: 'pm-overview-footer'}, t('ov_showing', {n: data.attention.length}), ' · ',
      h('a', {href: `#/pm/${encodeURIComponent(workspaceId)}/issues`}, t('ov_view_all'))));
    return attentionCard;
  }
  function emptyField(data, field, complete, sources = []) {
    if (complete) return h('span', {class: 'ui-meta'}, t('ov_no_items'));
    const unknown = data?.unknown || [];
    const reason = unknown.find(row => row.field === field)?.reason
      || unknown.find(row => sources.some(key => row.field === key || row.field.startsWith(key + '.')))?.reason
      || (data?.visibility?.restricted ? 'restricted' : data?.visibility?.filtered ? 'filtered' : null)
      || sources.map(key => data?.coverage?.sources?.[key]?.status).find(Boolean) || 'incomplete_sources';
    return h('span', {class: 'ui-meta'}, statusPill('unknown'), ' ', reasonLabel(t, reason));
  }
  function sidebar(data) {
    const milestones = sectionCard({title: t('ov_milestones'), body: h('div', {}, ...data.milestones.map(row => h('div', {class: 'pm-overview-milestone', 'data-overview-milestone': row.milestone_id},
      h('div', {}, itemLink(row, 'milestones'), milestoneDate(row)), statusPill(row.status))), !data.milestones.length ? emptyField(data, 'milestones', data.coverage?.sources?.roadmap?.complete === true, ['roadmap']) : null,
      data.milestones_truncated ? h('div', {class: 'ui-meta'}, t('ov_showing', {n: data.milestones.length})) : null)});
    const deliveriesComplete = data.recent_deliveries_complete === true && data.coverage?.sources?.attention_references?.complete !== false;
    const deliveries = sectionCard({title: t(deliveriesComplete ? 'ov_deliveries' : 'ov_deliveries_loaded'), body: h('div', {},
      ...data.recent_deliveries.map(row => h('div', {class: 'pm-overview-delivery', 'data-overview-delivery': row.work_item_id},
        h('div', {class: 'pm-overview-delivery-body'},
          itemLink({...row, ref: {kind: 'work_item', id: row.work_item_id}}, 'deliveries'), deliveryAssessment(row)), statusPill(row.state))),
      !data.recent_deliveries.length ? emptyField(data, 'recent_deliveries', deliveriesComplete, ['work', 'quality', 'deliveries', 'attention_references']) : null)});
    return h('div', {class: 'pm-overview-side'}, milestones, deliveries);
  }
  function deliveryAssessment(row) {
    const reference = row.assessment_ref;
    if (row.state !== 'accepted' || !reference || typeof reference !== 'object' || Array.isArray(reference)) return null;
    const ref = {kind: 'quality_assessment', id: reference.operation_id, review_id: reference.review_id, document_digest: reference.document_digest};
    const key = JSON.stringify(['delivery-assessment', row.work_item_id, reference.review_id,
      reference.operation_id, reference.version, reference.document_digest, reference.candidate_commit]);
    const controls = isContextRef(ref) ? h('div', {class: 'pm-overview-progress-actions'},
      h('a', {class: 'ui-button secondary ui-sm', href: `#/op/${reference.operation_id}`, 'data-overview-link': `${key}:operation`}, t('pm_operation')),
      discussLink({workspaceId, refs: [ref], title: row.title, from: 'overview'}, {'data-overview-link': `${key}:discuss`})) : null;
    const node = disclosure({label: t('ov_original_assessment'), key: `${namespace}:${workspaceId}:${key}`,
      body: h('div', {}, h('p', {class: 'ui-meta'}, t('ov_assessment_source')), controls,
        h('pre', {class: 'pm-brief-proof'}, JSON.stringify(reference, null, 2)))});
    node.dataset.overviewAssessment = reference.operation_id || '';
    node.querySelector('summary').dataset.overviewLink = `${key}:source`;
    return node;
  }
  function closeReader() {reader?.close(); reader = null; readerDocument = null; relatedSelection = null;}
  function openRelated(row, host, returnFocus) {
    closeReader(); relatedSelection = row;
    reader = inspector({title: attentionTitle(row), returnFocus, onClose: () => {reader = null; relatedSelection = null;}});
    reader.classList.add('pm-overview-related'); reader.dataset.overviewRelated = row.key;
    reader.body.append(h('section', {'data-overview-related-row': row.key, 'aria-current': 'true'},
      h('p', {}, t('ov_' + row.kind)), h('p', {}, contextText(row)),
      row.delta_days > 0 ? h('p', {class: 'pm-overview-late'}, t('ov_delta', {n: row.delta_days})) : null,
      discuss(row), disclosure({label: t('ov_details'), body: h('div', {}, row.reason ? h('p', {}, reasonLabel(t, row.reason)) : null,
        h('pre', {class: 'pm-brief-proof'}, JSON.stringify({ref: row.ref, since: row.since, context: row.context}, null, 2)))})));
    host.append(reader);
  }
  function fullPlan(document) {
    closeReader(); reader = inspector({title: t('ov_plan'), onClose: () => {reader = null;}});
    reader.dataset.overviewPlan = ''; readerDocument = JSON.stringify(document);
    reader.body.append(planDocument(h, document.markdown), h('ul', {}, ...(document.requirements || []).map(row => h('li', {}, row.text,
      h('ul', {}, ...(row.acceptance || []).map(item => h('li', {}, item.text)))))));
    box.append(reader);
  }
  function charter(source) {
    const groups = charterSections(source), document = source?.baseline?.document;
    const grid = h('div', {class: 'pm-overview-charter-grid'}), empty = [];
    // Live milestones and pending decisions already have their own cards.
    const shownDomains = mode === 'live' ? domains.filter(key => !['schedule', 'decisions'].includes(key)) : domains;
    for (const key of shownDomains) {
      const rows = groups[key], expanded = preference.expanded?.includes(key);
      if (!rows.length) {empty.push(key); continue;}
      const list = h('ul', {class: 'pm-overview-bullets'}, ...rows.map((row, i) => h('li', {hidden: i >= 3 && !expanded}, row.text,
        ['agent_assumption', 'unresolved', 'conflicting'].includes(row.status) ? statusPill('unconfirmed') : null)));
      const sources = disclosure({label: t('ov_sources'), key: `${namespace}:${workspaceId}:overview-source:${key}`,
        body: proof({h, t, value: rows.map(row => ({...row, ...(document?.selected_inputs?.find(input => input.input_id === row.input_id) || {})}))})});
      sources.classList.add('pm-overview-sources'); sources.querySelector('summary').setAttribute('aria-label', `${t('ov_sources')} · ${t('wb_' + key)}`);
      sources.querySelector('summary').title = t('ov_sources'); sources.querySelector('summary').dataset.overviewLink = `charter:${key}:sources`;
      const card = sectionCard({title: t('wb_' + key), body: h('div', {}, list, sources)});
      card.dataset.charterSection = key;
      if (rows.length > 3) {
        const more = button({variant: 'ghost', size: 'sm', label: t(expanded ? 'ov_less' : 'ov_more', {n: rows.length - 3}), 'aria-expanded': String(Boolean(expanded)), 'data-overview-link': `charter:${key}:expand`, onClick: () => {
          const expand = more.getAttribute('aria-expanded') !== 'true'; more.setAttribute('aria-expanded', String(expand));
          [...list.children].forEach((node, i) => {node.hidden = i >= 3 && !expand;}); more.textContent = t(expand ? 'ov_less' : 'ov_more', {n: rows.length - 3});
          const keys = new Set(preference.expanded || []); expand ? keys.add(key) : keys.delete(key); preference.expanded = [...keys];
        }}); card.append(more);
      }
      grid.append(card);
    }
    return h('section', {'data-pm-charter': '', class: 'pm-overview-charter'}, h('header', {class: 'pm-overview-charter-heading'}, h('h2', {}, t('ov_charter')),
      button({variant: 'secondary', label: t('ov_plan'), disabled: !document, 'data-charter-plan': '', 'data-overview-link': 'charter:plan', onClick: () => fullPlan(document)})), grid,
      empty.length ? h('p', {class: 'ui-meta', 'data-charter-empty': ''},
        emptyField(source, 'plan', Boolean(document) && (Object.hasOwn(source, 'plan_complete') ? source.plan_complete : !savedPartial()), ['plan']),
        ' · ', empty.map(key => t('wb_' + key)).join(' · ')) : null);
  }
  function details(data, fixed) {
    const knownSources = ['plan', 'plan_candidate', 'work', 'governance', 'quality', 'roadmap', 'forecast', 'proposals', 'findings', 'lifecycle_inflight', 'deliveries', 'projects', 'evidence'];
    const sourceLabel = key => t('ov_source_' + (knownSources.includes(key.split('.')[0]) ? key.split('.')[0] : 'other'));
    const reasons = data?.unknown || [], shown = new Set(), lines = [];
    const reasonText = reason => {if (!reason || shown.has(reason)) return ''; shown.add(reason); return ` · ${reasonLabel(t, reason)}`;};
    const sources = fixed ? Object.fromEntries(Object.entries(saved?.coverage || {}).filter(([key]) => key.endsWith('_truncated')).map(([key, value]) => [key.slice(0, -10), {complete: !value, status: value ? 'source_limit' : null}])) : data?.coverage?.sources || {};
    for (const [key, source] of Object.entries(sources)) {
      const sourceReasons = reasons.filter(row => row.field === key).map(row => row.reason);
      if (source.status) sourceReasons.push(source.status);
      lines.push(h('li', {}, sourceLabel(key), ' · ', t(source.complete === true ? 'ov_scan_complete' : 'ui_status_partial'), ...sourceReasons.map(reasonText)));
    }
    for (const row of reasons) if (!shown.has(row.reason)) {shown.add(row.reason); lines.push(h('li', {}, reasonLabel(t, row.reason)));}
    const omitted = data?.coverage?.sources?.lifecycle_inflight?.omitted;
    if (omitted > 0) lines.push(h('li', {}, t('ov_hidden', {n: omitted})));
    return disclosure({label: t('ov_details'), key: `${namespace}:${workspaceId}:overview-details`, body: h('div', {},
      h('p', {}, t(fixed ? 'ov_saved_limits' : 'ov_caveat')), h('ul', {'data-overview-coverage': ''}, ...lines),
      disclosure({label: t('ov_raw_coverage'), body: h('pre', {class: 'pm-brief-proof'}, JSON.stringify(fixed ? {coverage: saved?.coverage, visibility: saved?.visibility, snapshot_id: saved?.snapshot_id, unknown: data?.unknown} : {coverage: data?.coverage, unknown: data?.unknown, as_of: data?.as_of}, null, 2))}))});
  }
  function savedPartial() {return Boolean(saved?.visibility?.restricted || saved?.visibility?.filtered || Object.entries(saved?.coverage || {}).some(([key, value]) => key.endsWith('_truncated') && value));}
  function savedData() {
    const document = saved?.baseline?.document;
    const goal = document?.selected_inputs?.find(row => ['value', 'goal', 'problem'].includes(row.slot) && ['user_confirmed', 'repo_evidence'].includes(row.status))?.value;
    const unknown = [{field: 'progress', reason: 'not_assessed'}];
    if (!goal) unknown.push({field: 'plan.goal', reason: document?.selected_inputs?.some(row => ['value', 'goal', 'problem'].includes(row.slot)) ? 'goal_unconfirmed' : 'no_explicit_goal'});
    if (saved?.visibility?.restricted) unknown.push({field: 'visibility', reason: 'restricted'});
    if (saved?.visibility?.filtered) unknown.push({field: 'visibility', reason: 'filtered'});
    if (Object.entries(saved?.coverage || {}).some(([key, value]) => key.endsWith('_truncated') && value)) unknown.push({field: 'coverage', reason: 'source_limit'});
    return {plan: document ? {title: document.title, goal: goal || null, adopted_at: saved.baseline.adoption?.created_at} : null,
      progress: document ? {requirements: new Set((document.requirements || []).map(row => row.id)).size} : null,
      attention_counts: {blocker: null, decision: null}, attention: [], unknown};
  }
  function commitParts(entries) {
    if (!parts.size) content.replaceChildren();
    const keys = new Set(entries.map(row => row.key));
    for (const [key, part] of parts) if (!keys.has(key)) {part.node.remove(); parts.delete(key);}
    entries.forEach((entry, index) => {
      let part = parts.get(entry.key);
      const value = JSON.stringify(entry.value);
      if (!part || part.value !== value) {
        const node = entry.render(); node.dataset.overviewPart = entry.key; if (part && node !== part.node) part.node.replaceWith(node);
        part = {node, value}; parts.set(entry.key, part);
      }
      if (content.children[index] !== part.node) content.insertBefore(part.node, content.children[index] || null);
    });
  }
  function resetContent(...nodes) {parts.clear(); content.replaceChildren(...nodes);}
  function attentionSelection() {
    const selection = document.getSelection();
    return Boolean(selection && !selection.isCollapsed && attentionList?.contains(selection.anchorNode) && attentionList.contains(selection.focusNode));
  }
  function readingAnchor() {
    if (!active || !attentionList?.isConnected) return null;
    const rows = [...attentionList.querySelectorAll('[data-overview-row]')];
    let scroller = attentionList.parentElement;
    while (scroller && !(scroller.scrollHeight > scroller.clientHeight && /auto|scroll|overlay/.test(getComputedStyle(scroller).overflowY))) scroller = scroller.parentElement;
    scroller ||= document.scrollingElement;
    if (!scroller) return null;
    const bounds = scroller === document.scrollingElement ? {top: 0, bottom: innerHeight} : scroller.getBoundingClientRect();
    const hovered = rows.find(node => node.matches(':hover'));
    const selection = attentionSelection() ? document.getSelection() : null;
    const selected = selection && (rows.find(node => node.contains(selection.focusNode)) || rows.find(node => node.contains(selection.anchorNode)));
    // Reading from the list's top lets new rows push the rest down in place.
    if (!attentionList.contains(document.activeElement) && !hovered && !selection
      && attentionList.getBoundingClientRect().top >= Math.max(0, bounds.top)) return null;
    const row = rows.find(node => node.contains(document.activeElement)) || hovered || selected
      || rows.find(node => {const rect = node.getBoundingClientRect(); return rect.bottom > Math.max(0, bounds.top) && rect.top < Math.min(innerHeight, bounds.bottom);});
    return row ? {key: row.dataset.overviewRow, top: row.getBoundingClientRect().top, scroller} : null;
  }
  function restoreAnchor(anchor) {
    const row = anchor && attentionNodes.get(anchor.key)?.node;
    // Withdrawal takes precedence over holding an obsolete reading position.
    if (!row?.isConnected || !box.contains(row) || !anchor.scroller.isConnected) return;
    anchor.scroller.scrollTop += row.getBoundingClientRect().top - anchor.top;
  }
  function render(anchor = mode === 'live' ? readingAnchor() : null, resetOrder = false) {
    try {renderContent(resetOrder);} finally {restoreAnchor(anchor);}
  }
  function renderContent(resetOrder) {
    if (mode === 'live' && reportStatus.parentNode !== reportStatusHome) reportStatusHome.append(reportStatus);
    assessment?.setActive(active && mode === 'live');
    header(); savedBar.hidden = mode !== 'saved'; box.dataset.overviewMode = mode;
    if (mode === 'saved') {
      reportPartial.hidden = !savedPartial();
      reportLabel.replaceChildren(...(saved ? [t('ov_viewing', {n: saved.sequence}), ' · ', date(saved.as_of), saved.title ? ' · ' + saved.title : ''] : [t(reportLoading ? 'ov_loading_report' : 'ov_not_saved')]));
      if (!saved) {resetContent(reportLoading ? skeleton(3, {label:t('ov_loading_report')}) : emptyState({title:t('ov_not_saved')})); return;}
      const next = JSON.stringify(saved);
      if (signature === `saved:${next}`) return;
      signature = `saved:${next}`; closeReader();
      commitParts([{key: 'status', value: saved, render: () => statusStrip(savedData(), true)}, {key: 'charter', value: saved, render: () => charter(saved)}, {key: 'details', value: saved, render: () => details(savedData(), true)}]); return;
    }
    if (!projection) {resetContent(loading ? skeleton(4) : statusStrip(null)); return;}
    // Advance ages without replacing cached rows, changing order or moving focus.
    for (const row of projection.attention) {
      const node = attentionNodes.get(row.key)?.node.querySelector('[data-overview-age]'), value = age(row.since);
      if (node && value !== null && node.textContent !== value) node.textContent = value;
    }
    for (const row of Array.isArray(projection.recent_decisions) ? projection.recent_decisions : []) {
      const node = content.querySelector(`[data-overview-decided="${CSS.escape(row.key)}"] [data-overview-decided-meta]`), value = decisionMeta(row);
      if (node && node.textContent !== value) node.textContent = value;
    }
    // as_of changes on every read. It must not replace reading nodes or their focus.
    const next = JSON.stringify([{...projection, as_of: null}, plan]);
    if (signature === `live:${next}` && !resetOrder) return;
    signature = `live:${next}`;
    const source = {baseline: {document: plan?.document}, brief: {}, plan_complete: projection.coverage?.sources?.plan?.complete === true,
      coverage: projection.coverage, unknown: projection.unknown, decision_count: projection.attention_counts.decision, attention_truncated: projection.attention_truncated};
    // Live decisions come from the projection, never from a previous snapshot.
    source.brief.decisions = {items: projection.attention.filter(row => row.kind === 'decision').map(row => ({text: row.title, source: row.ref}))};
    const focused = content.contains(document.activeElement) ? document.activeElement : null;
    const focusId = focused?.getAttribute('data-overview-link'), focusPart = focused?.closest('[data-overview-part]')?.dataset.overviewPart;
    if (reader && (relatedSelection ? !projection.attention.some(row => row.key === relatedSelection.key && JSON.stringify(row) === JSON.stringify(relatedSelection)) : readerDocument !== JSON.stringify(plan?.document))) closeReader();
    const main = attention(projection, resetOrder);
    commitParts([
      {key: 'status', value: [projection.plan, projection.progress, projection.attention_counts, projection.attention, projection.next_milestone, projection.unknown,
        projection.recent_decisions, projection.recent_decisions_count, projection.recent_decisions_complete, projection.recent_decisions_truncated,
        projection.attention_truncated, projection.recent_deliveries.length > 0], render: () => statusStrip(projection)},
      ...(assessment ? [{key: 'assessment', value: true, render: () => assessment.box}] : []),
      {key: 'columns', value: [projection.milestones, projection.recent_deliveries, projection.milestones_truncated, projection.recent_deliveries_complete, projection.coverage, projection.unknown], render: () => {
        const columns = parts.get('columns')?.node;
        if (columns) {columns.querySelector('.pm-overview-side').replaceWith(sidebar(projection)); return columns;}
        return h('div', {class: 'pm-overview-columns'}, main, sidebar(projection));
      }},
      {key: 'charter', value: source, render: () => charter(source)},
      {key: 'details', value: [projection.coverage, projection.unknown], render: () => details(projection, false)},
    ]);
    if (focused && !focused.isConnected && focusId && focusPart) parts.get(focusPart)?.node.querySelector(`[data-overview-link="${CSS.escape(focusId)}"]`)?.focus({preventScroll: true});
  }
  async function refreshLive() {
    const ticket = ++serial; loading = true;
    if (!projection && mode === 'live') render();
    try {
      const data = await api('GET', `${endpoint}/overview`); alive(); if (ticket !== serial) return;
      if (data?.workspace_id !== workspaceId || data.version !== 1 || !Array.isArray(data.attention) || !Array.isArray(data.milestones) || !Array.isArray(data.recent_deliveries)) throw Error(t('pm_scope_mismatch'));
      let adopted = null;
      if (data.plan) {
        const value = await api('GET', `${endpoint}/plans/${encodeURIComponent(data.plan.revision_id)}`); alive(); if (ticket !== serial) return;
        if (value.plan?.workspace_id !== workspaceId || value.plan.revision_id !== data.plan.revision_id) throw Error(t('pm_scope_mismatch'));
        adopted = value.plan;
      }
      const anchor = mode === 'live' ? readingAnchor() : null;
      projection = data; plan = adopted; loading = false; status.replaceChildren(); render(anchor);
      if (mode === 'live') await assessment?.refresh();
    } catch (error) {
      try {alive(); if (ticket !== serial) return;} catch {return;}
      projection = null; plan = null; loading = false; signature = ''; assessment?.withdraw(); closeReader(); render(); status.replaceChildren(options.errorBox(error, {within:status}));
    }
  }
  async function refresh() {if (mode === 'live') await refreshLive(); if (reportsLoaded || mode === 'saved') {await reports.refresh(); if (mode === 'saved') render();}}
  function withdraw() {serial++; projection = null; plan = null; saved = null; signature = ''; loading = false; assessment?.withdraw(); closeReader(); reports.withdraw(); resetContent(); status.replaceChildren(); header();}
  function prepareReports() {
    reportsLoaded = true;
    if (!reportRead) reportRead = reports.refresh().finally(() => {reportRead = null;});
    return reportRead;
  }
  function labelFor(kind, id) {
    if (kind !== 'requirement') return '';
    const text = plan?.document?.requirements?.find(row => row.id === id)?.text;
    return typeof text === 'string' ? text : '';
  }
  return {box, primary, meta, reportTools, refresh, refreshProjection: () => mode === 'live' ? refreshLive() : undefined, phase: () => mode === 'live' ? projection?.lifecycle?.stage : null, withdraw, prepareReports, openRelated, labelFor, closeRelated: () => {if (relatedSelection) closeReader();},
    reportActions: {compare: () => {if (mode !== 'saved') {mode = preference.mode = 'saved'; saved = reports.current(); signature = ''; render();} return reports.comparePrevious();}, print: () => window.print()},
    setActive(value) {if (active === value) {header(); return;} active = value; assessment?.setActive(value && mode === 'live');
      if (!value) {preference.scroll = window.scrollY; closeReader();}
      else {
        // Returning to Overview starts with Central's order, including unchanged data.
        render(null, true);
        if (preference.scroll) requestAnimationFrame(() => {if (active) window.scrollTo(0, preference.scroll);});
      }
      header();},
    dispose() {active = false; serial++; assessment?.dispose(); closeReader();},
  };
}
