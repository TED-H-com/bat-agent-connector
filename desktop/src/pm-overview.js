import {workspaceCharter, charterSections} from './pm-charter.js';
import {planDocument} from './pm-plans.js';
import {discussHref} from './pm-discuss-link.js';
import {button, iconButton, sectionCard, metric, statusPill, disclosure, emptyState, skeleton, inspector} from './ui/components.js';
import {reasonLabel, reasonLabels} from './ui/ui-strings.js';
import {icon} from './ui/icons.js';
import {viewState} from './ui/view-state.js';

const domains = ['definition', 'scope', 'schedule', 'resources', 'risks', 'decisions'];
const classes = ['accepted', 'in_review', 'in_progress', 'blocked', 'not_started'];
const tones = ['ok', 'review', 'info', 'bad', 'neutral'];
const phaseNames = ['idea', 'planning', 'development', 'maintain', 'paused', 'closed'];

// Live projection and immutable reports deliberately have separate render inputs.
export function workspaceOverview(options) {
  const {h, t, api, alive, namespace, workspaceId, onOpen = () => {}, onHeader = () => {}} = options;
  const endpoint = `/pm/workspaces/${encodeURIComponent(workspaceId)}`;
  const preference = viewState(namespace, workspaceId, 'overview');
  let serial = 0, projection = null, plan = null, saved = null, active = false, reader = null, reportsLoaded = false, reportRead = null;
  const parts = new Map(), attentionNodes = new Map();
  let attentionCard = null, attentionList = null, readerDocument = null, relatedSelection = null;
  let mode = preference.mode === 'saved' ? 'saved' : 'live', signature = '', loading = false;
  const primary = h('a', {class: 'ui-button primary', 'data-overview-discuss': ''}, t('wb_discuss'));
  const meta = h('span', {'data-overview-meta': ''});
  const content = h('div'), status = h('div', {role: 'status'}), savedBar = h('div', {class: 'pm-overview-saved', hidden: true});
  const box = h('section', {class: 'pm-overview', 'data-pm-overview': ''}, savedBar, status, content);
  const reportTools = h('div', {class: 'pm-report-tools', 'data-pm-report-tools': ''});
  const reportControls = flags => {for (const [key, disabled] of Object.entries(flags)) {
    const node = savedBar.querySelector(`[data-overview-report-control="${key}"]`); if (node) node.disabled = disabled;
  }};
  const reports = workspaceCharter({...options, onChange: reportChanged, onControls: reportControls});
  for (const node of reports.box.querySelectorAll(':scope > .pm-workbench-toolbar, :scope > .pm-charter-save, :scope > [data-pm-brief-export]')) reportTools.append(node);
  // Reuse the original comparison, pagination, export and durable form controls.
  reportTools.append(reports.comparison);
  const back = button({variant: 'ghost', label: t('ov_back_live'), 'data-overview-live': '', onClick: () => {
    mode = preference.mode = 'live'; projection = null; plan = null; loading = true; signature = ''; closeReader(); reports.comparison.replaceChildren(); render(); void refreshLive();
  }});
  const reportLabel = h('span');
  reports.previous.setAttribute('aria-label', t('ov_report_previous'));
  reports.next.setAttribute('aria-label', t('ov_report_next'));
  savedBar.append(reportLabel,
    iconButton({icon: 'chevron-left', label: t('ov_report_previous'), 'data-overview-report-control': 'previous', onClick: () => reports.previous.click()}),
    iconButton({icon: 'chevron-right', label: t('ov_report_next'), 'data-overview-report-control': 'next', onClick: () => reports.next.click()}),
    button({variant: 'ghost', label: t('ui_compare'), 'data-overview-report-control': 'compare', onClick: () => options.onCompare?.()}), back);
  reportControls(reports.controls());
  const date = value => {
    const parsed = typeof value === 'number' && Number.isFinite(value) ? new Date(value * 1000)
      : typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) ? new Date(value + 'T12:00:00Z') : null;
    return parsed && Number.isFinite(parsed.getTime()) ? parsed.toLocaleDateString((navigator.language || 'zh-TW').toLowerCase().startsWith('zh') ? 'zh-TW' : 'en', {month: 'short', day: 'numeric', timeZone: 'UTC'}) : '—';
  };
  const refs = () => {
    const revision = mode === 'saved' ? saved?.baseline?.document_revision : projection?.plan?.revision_id;
    return [...(revision ? [{kind: 'plan_revision', id: revision}] : []), ...(mode === 'saved' && saved ? [{kind: 'snapshot', id: saved.snapshot_id}] : [])];
  };
  function header() {
    const current = mode === 'saved' ? saved : projection;
    primary.href = discussHref({workspaceId, refs: refs(), title: mode === 'saved' ? saved?.title : projection?.plan?.title, from: 'overview', report: mode === 'saved' ? saved?.snapshot_id : undefined});
    meta.textContent = mode === 'saved' ? saved ? `${t('ov_report', {n: saved.sequence})} · ${date(saved.as_of)}` : t('ov_not_saved')
      : [projection?.plan?.title, projection?.plan?.adopted_at != null ? t('ov_adopted', {date: date(projection.plan.adopted_at)}) : null, t('ov_live')].filter(Boolean).join(' · ');
    const phase = mode === 'live' && current?.lifecycle?.stage;
    onHeader({meta, phase: phaseNames.includes(phase) ? t('ui_phase_' + phase) : null, active});
  }
  function reportChanged(value, event, error) {
    if (event === 'selected') {mode = preference.mode = 'saved'; saved = null; closeReader(); signature = '';}
    if (event === 'updated') saved = value;
    if (event === 'withdrawn') {saved = null; closeReader(); signature = '';}
    if (event === 'error') status.replaceChildren(options.errorBox(error));
    // Construction does not notify; callbacks run after the controls are mounted.
    if (mode === 'saved') render();
  }
  function discuss(row) {
    return h('a', {class: 'ui-button ghost ui-sm', 'data-overview-row-discuss': row.key || row.milestone_id,
      href: discussHref({workspaceId, refs: row.ref ? [row.ref] : [], title: row.title, from: 'overview'})}, t('ov_discuss'));
  }
  function open(row) {preference.attention = row.key; onOpen(row.ref, row);}
  function itemLink(row) {return row ? h('button', {type: 'button', class: 'pm-overview-link', onclick: () => open(row)}, row.title) : null;}
  function milestoneDate(row) {return h('span', {class: 'ui-meta'}, date(row.baseline_date), row.forecast_date ? ` → ${date(row.forecast_date)}` : '',
    row.delta_days > 0 ? h('span', {class: 'pm-overview-late'}, t('ov_delta', {n: row.delta_days})) : null);}
  function statusStrip(data, fixed = false) {
    const p = data?.progress, complete = p && classes.every(key => Number.isSafeInteger(p[key]));
    const goalReason = (data?.unknown || []).find(row => row.field === 'plan.goal')?.reason;
    const goal = metric({label: t('ov_goal'), value: data?.plan?.goal ?? '—', context: h('div', {},
      !data?.plan?.goal ? h('span', {class: 'ui-meta'}, reasonLabel(t, goalReason || 'unknown')) : null,
      h('div', {}, data?.plan?.title || '—'), data?.plan?.adopted_at != null ? h('div', {}, t('ov_adopted', {date: date(data.plan.adopted_at)})) : null)});
    goal.dataset.overviewMetric = 'goal';
    const next = data?.next_milestone;
    const progress = metric({label: t('ov_progress'), value: !p ? null : complete ? t('ov_accepted', {accepted: p.accepted, total: p.requirements}) : t('ov_requirements', {n: p.requirements}),
      context: h('div', {}, p && !complete ? statusPill(fixed ? 'unknown' : 'partial') : null,
        !p && (data?.unknown || []).some(row => row.reason === 'no_adopted_plan') ? h('a', {href: `#/pm/${encodeURIComponent(workspaceId)}/discussions`}, reasonLabel(t, 'no_adopted_plan')) : null,
        next ? h('div', {class: 'pm-overview-next'}, next.title, h('div', {}, date(next.forecast_date || next.baseline_date), next.delta_days > 0 ? h('span', {class: 'pm-overview-late'}, t('ov_delta', {n: next.delta_days})) : null)) : null),
      bar: complete ? classes.map((key, i) => ({tone: tones[i], value: p[key]})) : null});
    progress.dataset.overviewMetric = 'progress';
    if (complete) progress.append(disclosure({label: t('ov_details'), key: `${namespace}:${workspaceId}:progress`, body: h('ul', {}, ...classes.map(key => h('li', {}, statusPill(key), ` ${p[key]}`)))}));
    const metrics = ['blocker', 'decision'].map(kind => {
      const first = data?.attention?.find(row => row.kind === kind);
      const node = metric({label: t(kind === 'blocker' ? 'ov_blockers' : 'ov_decisions'), value: data?.attention_counts?.[kind] ?? null,
        context: h('div', {}, itemLink(first), kind === 'decision' && first ? discuss(first) : null)});
      node.dataset.overviewMetric = kind; return node;
    });
    return h('div', {class: 'pm-overview-status', 'data-overview-status': ''}, goal, progress, ...metrics);
  }
  function age(since) {
    if (!Number.isFinite(since) || !Number.isFinite(projection?.as_of)) return null;
    const seconds = Math.max(0, projection.as_of - since);
    const [key, unit] = seconds >= 86400 ? ['ov_day', 86400] : seconds >= 3600 ? ['ov_hour', 3600] : ['ov_minute', 60];
    return t(key, {n: Math.floor(seconds / unit)});
  }
  function contextText(row) {
    const c = row.context || {}, ids = c.requirement_ids || (c.requirement_id ? [c.requirement_id] : []);
    const labels = ids.map(id => plan?.document?.requirements?.find(req => req.id === id)?.text).filter(Boolean);
    return labels.join(' · ') || (ids.length ? t('ov_context_requirement', {n: ids.length}) : c.work_item_id ? t('ov_context_work') : c.milestone_id ? t('ov_context_milestone') : '');
  }
  function attention(data) {
    if (!attentionList) {attentionList = h('div', {class: 'pm-overview-attention', 'data-overview-attention': ''}); attentionCard = sectionCard({title: t('ov_attention'), body: attentionList});}
    const rows = attentionList, reading = rows.contains(document.activeElement) || Boolean(rows.querySelector('[data-overview-row]:hover'));
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
      const link = h('button', {type: 'button', class: 'pm-overview-attention-main', 'data-overview-open': row.key, onclick: () => open(row)},
        h('span', {class: 'pm-overview-kind', title: label, 'aria-label': label}, icon(row.kind === 'decision' ? 'message-square' : 'circle-alert')),
        h('span', {}, h('strong', {}, row.title), h('span', {class: 'pm-overview-context'}, [label, contextText(row)].filter(Boolean).join(' · '))));
      const node = h('div', {class: 'pm-overview-attention-row', 'data-overview-row': row.key}, link,
        elapsed === null ? null : h('span', {class: 'ui-meta', 'data-overview-age': ''}, elapsed), discuss(row));
      cached?.node.remove(); attentionNodes.set(row.key, {signature: rowSignature, node});
      rows.insertBefore(node, rows.children[position] || null); position++;
    }
    if (!data.attention.length) rows.append(emptyState({title: t(data.coverage?.complete ? 'ov_empty' : 'ov_incomplete')}));
    if (data.attention_truncated) rows.append(h('div', {class: 'pm-overview-footer'}, t('ov_showing', {n: data.attention.length}), ' · ',
      h('a', {href: `#/pm/${encodeURIComponent(workspaceId)}/issues`}, t('ov_view_all'))));
    return attentionCard;
  }
  function sidebar(data) {
    const milestones = sectionCard({title: t('ov_milestones'), body: h('div', {}, ...data.milestones.map(row => h('div', {class: 'pm-overview-milestone', 'data-overview-milestone': row.milestone_id},
      h('div', {}, itemLink(row), milestoneDate(row)), statusPill(row.status))), !data.milestones.length ? h('span', {class: 'ui-meta'}, t('ov_no_items')) : null,
      data.milestones_truncated ? h('div', {class: 'ui-meta'}, t('ov_showing', {n: data.milestones.length})) : null)});
    const deliveries = sectionCard({title: t(data.recent_deliveries_complete ? 'ov_deliveries' : 'ov_deliveries_loaded'), body: h('div', {},
      ...data.recent_deliveries.map(row => h('div', {class: 'pm-overview-delivery', 'data-overview-delivery': row.work_item_id},
        itemLink({...row, ref: {kind: 'work_item', id: row.work_item_id}}), statusPill(row.state))),
      !data.recent_deliveries.length ? h('span', {class: 'ui-meta'}, t('ov_no_items')) : null)});
    return h('div', {class: 'pm-overview-side'}, milestones, deliveries);
  }
  function closeReader() {reader?.close(); reader = null; readerDocument = null; relatedSelection = null;}
  function openRelated(row, host) {
    closeReader(); relatedSelection = row;
    reader = inspector({title: row.title, onClose: () => {reader = null; relatedSelection = null;}});
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
    const grid = h('div', {class: 'pm-overview-charter-grid'});
    for (const key of domains) {
      const rows = groups[key], expanded = preference.expanded?.includes(key);
      const list = h('ul', {class: 'pm-overview-bullets'}, ...rows.map((row, i) => h('li', {hidden: i >= 3 && !expanded}, row.text,
        ['agent_assumption', 'unresolved', 'conflicting'].includes(row.status) ? statusPill('unconfirmed') : null)));
      const sources = disclosure({label: icon('list-checks'), key: `${namespace}:${workspaceId}:overview-source:${key}`,
        body: h('pre', {class: 'pm-brief-proof'}, JSON.stringify(rows, null, 2))});
      sources.classList.add('pm-overview-sources'); sources.querySelector('summary').setAttribute('aria-label', `${t('ov_sources')} · ${t('wb_' + key)}`);
      sources.querySelector('summary').title = t('ov_sources');
      const card = sectionCard({title: t('wb_' + key), body: h('div', {}, rows.length ? list : h('span', {class: 'ui-meta'}, t('ov_no_items')), sources)});
      card.dataset.charterSection = key;
      if (rows.length > 3) {
        const more = button({variant: 'ghost', size: 'sm', label: t(expanded ? 'ov_less' : 'ov_more', {n: rows.length - 3}), 'aria-expanded': String(Boolean(expanded)), onClick: () => {
          const expand = more.getAttribute('aria-expanded') !== 'true'; more.setAttribute('aria-expanded', String(expand));
          [...list.children].forEach((node, i) => {node.hidden = i >= 3 && !expand;}); more.textContent = t(expand ? 'ov_less' : 'ov_more', {n: rows.length - 3});
          const keys = new Set(preference.expanded || []); expand ? keys.add(key) : keys.delete(key); preference.expanded = [...keys];
        }}); card.append(more);
      }
      grid.append(card);
    }
    return h('section', {'data-pm-charter': '', class: 'pm-overview-charter'}, h('header', {class: 'pm-overview-charter-heading'}, h('h2', {}, t('ov_charter')),
      button({variant: 'ghost', label: t('ov_plan'), disabled: !document, 'data-charter-plan': '', onClick: () => fullPlan(document)})), grid);
  }
  function details(data, fixed) {
    const entries = new Map((data?.unknown || []).map(row => [row.reason, row]));
    for (const source of Object.values(data?.coverage?.sources || {})) if (source.status) entries.set(source.status, {reason: source.status});
    const omitted = data?.coverage?.sources?.lifecycle_inflight?.omitted;
    return disclosure({label: t('ov_details'), key: `${namespace}:${workspaceId}:overview-details`, body: h('div', {},
      h('p', {}, t(fixed ? 'ov_saved_limits' : 'ov_caveat')),
      h('ul', {}, ...[...entries.values()].map(row => h('li', {}, reasonLabel(t, row.reason), Object.hasOwn(reasonLabels, row.reason) ? null : h('code', {}, ` (${row.reason})`))),
        omitted > 0 ? h('li', {}, t('ov_hidden', {n: omitted})) : null),
      h('pre', {class: 'pm-brief-proof'}, JSON.stringify(fixed ? {coverage: saved?.coverage, visibility: saved?.visibility, snapshot_id: saved?.snapshot_id} : {coverage: data?.coverage, as_of: data?.as_of}, null, 2)))});
  }
  function savedData() {
    const document = saved?.baseline?.document;
    const goal = document?.selected_inputs?.find(row => ['value', 'goal', 'problem'].includes(row.slot) && ['user_confirmed', 'repo_evidence'].includes(row.status))?.value;
    return {plan: document ? {title: document.title, goal: goal || null, adopted_at: saved.baseline.adoption?.created_at} : null,
      progress: document ? {requirements: new Set((document.requirements || []).map(row => row.id)).size} : null,
      attention_counts: {blocker: null, decision: null}, attention: [], unknown: [{field: 'progress', reason: 'not_assessed'}]};
  }
  function commitParts(entries) {
    if (!parts.size) content.replaceChildren();
    const keys = new Set(entries.map(row => row.key));
    for (const [key, part] of parts) if (!keys.has(key)) {part.node.remove(); parts.delete(key);}
    entries.forEach((entry, index) => {
      let part = parts.get(entry.key);
      const value = JSON.stringify(entry.value);
      if (!part || part.value !== value) {
        const node = entry.render(); if (part && node !== part.node) part.node.replaceWith(node);
        part = {node, value}; parts.set(entry.key, part);
      }
      if (content.children[index] !== part.node) content.insertBefore(part.node, content.children[index] || null);
    });
  }
  function resetContent(...nodes) {parts.clear(); content.replaceChildren(...nodes);}
  function render() {
    header(); savedBar.hidden = mode !== 'saved'; box.dataset.overviewMode = mode;
    if (mode === 'saved') {
      reportLabel.textContent = saved ? `${t('ov_viewing', {n: saved.sequence})} · ${date(saved.as_of)} · ${saved.title || ''}` : t('ov_not_saved');
      if (!saved) {resetContent(loading ? skeleton(3) : h('p', {}, t('ov_not_saved'))); return;}
      const next = JSON.stringify(saved);
      if (signature === `saved:${next}`) return;
      signature = `saved:${next}`; closeReader();
      commitParts([{key: 'status', value: saved, render: () => statusStrip(savedData(), true)}, {key: 'charter', value: saved, render: () => charter(saved)}, {key: 'details', value: saved, render: () => details(null, true)}]); return;
    }
    if (!projection) {resetContent(loading ? skeleton(4) : statusStrip(null)); return;}
    // as_of changes on every read. It must not replace reading nodes or their focus.
    const next = JSON.stringify([{...projection, as_of: null}, plan]);
    if (signature === `live:${next}`) return;
    signature = `live:${next}`;
    const source = {baseline: {document: plan?.document}, brief: {}};
    // Live decisions come from the projection, never from a previous snapshot.
    source.brief.decisions = {items: projection.attention.filter(row => row.kind === 'decision').map(row => ({text: row.title, source: row.ref}))};
    const focused = content.contains(document.activeElement) ? document.activeElement?.getAttribute('data-overview-open') || document.activeElement?.getAttribute('data-overview-row-discuss') : null;
    const discussFocused = document.activeElement?.hasAttribute('data-overview-row-discuss');
    if (reader && (relatedSelection ? !projection.attention.some(row => row.key === relatedSelection.key && JSON.stringify(row) === JSON.stringify(relatedSelection)) : readerDocument !== JSON.stringify(plan?.document))) closeReader();
    const main = attention(projection);
    commitParts([
      {key: 'status', value: [projection.plan, projection.progress, projection.attention_counts, projection.attention, projection.next_milestone, projection.unknown], render: () => statusStrip(projection)},
      {key: 'columns', value: [projection.milestones, projection.recent_deliveries, projection.milestones_truncated, projection.recent_deliveries_complete], render: () => h('div', {class: 'pm-overview-columns'}, main, sidebar(projection))},
      {key: 'charter', value: source, render: () => charter(source)},
      {key: 'details', value: [projection.coverage, projection.unknown], render: () => details(projection, false)},
    ]);
    if (focused) content.querySelector(`[${discussFocused ? 'data-overview-row-discuss' : 'data-overview-open'}="${CSS.escape(focused)}"]`)?.focus({preventScroll: true});
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
      projection = data; plan = adopted; loading = false; status.replaceChildren(); render();
    } catch (error) {
      try {alive(); if (ticket !== serial) return;} catch {return;}
      projection = null; plan = null; loading = false; signature = ''; closeReader(); render(); status.replaceChildren(options.errorBox(error));
    }
  }
  async function refresh() {if (mode === 'live') await refreshLive(); if (reportsLoaded || mode === 'saved') {await reports.refresh(); if (mode === 'saved') render();}}
  function withdraw() {serial++; projection = null; plan = null; saved = null; signature = ''; loading = false; closeReader(); reports.withdraw(); resetContent(); status.replaceChildren(); header();}
  function prepareReports() {
    reportsLoaded = true;
    if (!reportRead) reportRead = reports.refresh().finally(() => {reportRead = null;});
    return reportRead;
  }
  return {box, primary, meta, reportTools, refresh, withdraw, prepareReports, openRelated, closeRelated: () => {if (relatedSelection) closeReader();},
    reportActions: {compare: () => {if (mode !== 'saved') {mode = preference.mode = 'saved'; saved = reports.current(); signature = ''; render();} return reports.comparePrevious();}, print: () => window.print()},
    setActive(value) {if (active === value) {header(); return;} active = value; if (!value) {preference.scroll = window.scrollY; closeReader();} else if (preference.scroll) window.scrollTo(0, preference.scroll); header();},
    dispose() {serial++; closeReader();},
  };
}
