import {emptyState, disclosure, statusPill} from './ui/components.js';
import {timeStamp, timeLabel} from './ui/stamps.js';
import {retiredView, viewLeft} from './pm-retired.js';
// Source records share a readable surface; the full identity remains copyable in Details.
export function proof({h, t, value, name, kind}) {
  const records = Array.isArray(value) ? value : [value];
  const label = sourceKind => {
    const key = 'pd_source_' + sourceKind, text = t(key);
    return text === key ? t('pd_source_record') : text;
  };
  const technical = data => {
    const rows = h('dl', {class: 'pd-source-facts'});
    function visit(value, path = '') {
      if (value && typeof value === 'object') {
        for (const [key, child] of Object.entries(value)) visit(child, path ? path + ' · ' + key : key);
      } else {
        const stamp = /(?:_at|as_of)$/.test(path) && value != null ? timeStamp(value) : null;
        rows.append(h('dt', {class: 'mono'}, path), h('dd', {class: 'mono'}, stamp || (value == null ? t('ui_status_unknown') : typeof value === 'boolean' ? t(value ? 'pd_yes' : 'pd_no') : String(value))));
      }
    }
    visit(data); return rows;
  };
  return h('div', {class: 'pd-sources', 'data-readable-sources': ''}, ...records.filter(Boolean).map(row => {
    const source = row.source || row.source_ref || row;
    const title = row.title || row.name || name || row.text || row.value || source.label || source.ref;
    const human = typeof title === 'string' && !/^(?:pm[a-z]*_|op_|wi_)[a-f0-9]{32}$|^[a-f0-9]{40,64}$|^[a-z][a-z\d+.-]*:\/\//i.test(title) ? title : t('ui_status_unknown');
    const time = row.source_observed_at ?? row.observed_at ?? row.created_at ?? row.as_of;
    return h('article', {class: 'pd-source-row'}, h('span', {class: 'ui-meta'}, label(kind || source.kind || row.source_kind)),
      h('p', {}, human), h('div', {class: 'ui-meta'}, t('pd_source_time'), ' · ', time != null ? timeStamp(time) : statusPill('unknown')),
      disclosure({label: t('pd_details'), body: technical(row)}));
  }));
}
// All five Brief sections render one immutable snapshot, filtered for current source access.
import {briefExport} from './pm-brief-export.js';
import {labelSegment} from './label-text.js';
import {qualityFact} from './pm-quality-projections.js';
export function projectBrief({h, t, api, alive, workspaceId, workspace, permitted, form, input, labelled, errorBox, caps, canRead=()=>true}) {
  const endpoint = `/pm/workspaces/${encodeURIComponent(workspaceId)}`;
  let head = null, snapshot = null, signature = '', serial = 0, selectedSnapshotId = null;
  const title = input('pm_brief_snapshot_title', {max: 160, required: false});
  const view = h('div', {class: 'pm-brief-view'}), history = h('div'), status = h('div', {role: 'status'});
  const pending = h('p', {id:'pm-brief-pending-'+workspaceId,class:'muted',hidden:true,'data-pm-brief-pending':''});
  let create = null;
  const canCreate = () => permitted(false) && caps()?.scopes?.includes('manage')
    && caps()?.actions?.some(row => row.action === 'pm.snapshot.create' && row.allowed === true);
  const updateCreate = () => {
    if (!create) return;
    const submit = create.box.querySelector('button[type=submit]'), recovering = create.locked();
    // Hide unavailable write entry points. A retained original request keeps
    // its recovery controls and draft, including while a fresh head is read.
    create.box.hidden = !recovering && (!head || !canCreate());
    submit.hidden = !recovering && !create.canSubmit();
    const unavailable = submit.disabled || submit.getAttribute('aria-disabled') === 'true';
    pending.hidden = create.box.hidden || submit.hidden || !unavailable;
    pending.textContent = t(create.checking() ? 'pd_report_pending' : 'pd_report_unavailable');
    if (!pending.hidden) submit.setAttribute('aria-describedby',pending.id);
    else submit.removeAttribute('aria-describedby');
  };
  create = form({action: 'pm.snapshot.create', target: {workspace_id: workspaceId}, fields: {title},
    params: values => values.title.trim() ? {title: values.title.trim()} : {},
    preconditions: () => ({expected_plan_version: head.version}),
    permitted: () => Boolean(head) && permitted(false), recoverPermitted: () => permitted(false),
    label: 'pm_brief_create', onSaved: async result => {await refresh(result.snapshot_id);},
    onStateChange: updateCreate,
    extra: h('details', {}, h('summary', {}, t('pm_brief_name_optional')), labelled('pm_brief_snapshot_title', title))});
  const box = h('section', {class: 'pm-brief', 'data-pm-brief': ''}, h('header', {class: 'pm-heading'},
    h('div', {}, h('h2', {}, t('pm_brief_title')), h('p', {class: 'muted'}, t('pm_brief_intro'))), h('div',{},create.box,pending)),
    status, view, h('details', {}, h('summary', {}, t('pm_brief_history')), history));
  const exports=caps?.()?.features?.pm_workspaces?.brief_export
    ?briefExport({h,t,api,alive,workspaceId,caps,snapshot:()=>snapshot,readable:canRead,onUnavailable:error=>unavailable(error),errorBox}):null;
  if(exports)box.insertBefore(exports.element,box.lastChild);
  function withdraw(){++serial;head=null;snapshot=null;signature='';view.replaceChildren();history.replaceChildren();create.update();exports?.update();}
  function unavailable(error){withdraw();status.replaceChildren(errorBox(error, {within:status}));}
  const stamp = value => timeStamp(value, {empty: t('pm_evidence_time_unknown')});
  const check = data => {if (data.workspace_id !== workspaceId) throw Error(t('pm_scope_mismatch')); return data;};
  const readable = (prefix, value) => {
    const present = labelSegment(value);
    if (!present) return t('pm_label_unknown');
    const key = prefix + present, text = t(key);
    return text === key ? present : text;
  };
  function render(data) {
    snapshot = check(data).snapshot;
    view.replaceChildren();status.replaceChildren();
    if (!snapshot) {view.append(emptyState({title:t('pd_report_empty'),text:t('pd_report_empty_help')})); exports?.update();return;}
    if (snapshot.workspace_id !== workspaceId) throw Error(t('pm_scope_mismatch'));
    selectedSnapshotId=snapshot.snapshot_id;
    view.append(h('p', {class: 'pm-brief-date'}, h('strong', {}, snapshot.title || workspace().name), ' · ',
      timeLabel(t, 'pm_brief_as_of', {time: stamp(snapshot.as_of)})), h('p', {class: 'muted'}, t('pm_brief_fixed')));
    const sections = h('div', {class: 'pm-brief-sections'});
    for (const name of ['goals','delivery','blockers','deviations','decisions']) {
      const section = snapshot.brief?.[name] || {status:'unavailable', items:[]};
      const values = h('div', {class: 'pm-brief-values'});
      values.append(h('p', {class: 'muted'}, readable('pm_brief_status_', section.status)));
      if (section.quality) values.append(h('p',{},h('strong',{},t('pq_count',{
        qualified:section.quality.qualified_requirements,total:section.quality.total_requirements}))),h('p',{class:'muted'},t('pq_coverage')));
      if (name==='deviations'&&section.version===1) values.append(h('p',{class:'muted'},t('pq_deviations_boundary')),
        h('details',{'data-pm-deviation-coverage':''},h('summary',{},t('pq_deviations_coverage')),
          h('p',{},t(section.coverage?.selected_source_coverage_complete?'pq_deviations_selected':'pq_deviations_partial')),
          ...(section.coverage?.selections||[]).map(selection=>h('div',{},h('a',{href:`#/item/${selection.work_item_id}`},t('pm_evidence_open_work')),
            h('p',{},selection.reason ? t('pq_deviation_reason_'+selection.reason) :
              `${t('pq_acceptance')}: ${t('pq_'+selection.acceptance)} · ${t('pq_dod')}: ${t('pq_'+selection.dod)}`))),
          proof({h,t,value:section.coverage,name:t('pq_deviations_coverage')})));
      for (const item of section.items || []) {
        const source = item.source || {}, details = h('div'), row = h('details', {'data-pm-brief-fact': item.kind || name},
          h('summary', {}, item.kind==='quality_assessment' ? t(item.details?.qualified?'pq_qualified':'pq_not_qualified') :
            item.kind==='quality_rejection' ? `${t('pq_rejected_'+item.details.criterion.criterion.kind)}: ${item.text}` : item.text), details);
        if(item.kind==='quality_assessment')details.append(qualityFact({h,t,fact:item.details}));
        if(item.kind==='quality_rejection')details.append(h('p',{},t('pq_recorded_by',{actor:item.details.assessed_by})),
          h('p',{},item.details.assessment.rationale),h('p',{class:'muted'},t('pq_fixed')));
        if (item.status) details.append(h('p', {class: 'muted'}, item.kind==='quality_rejection'?t('pq_rejected'):readable('pm_status_', item.status)));
        const record = source.kind === 'evidence' ? (snapshot.evidence || []).find(entry => entry.evidence_id === source.evidence_id)
          : source.kind === 'plan_input' ? snapshot.baseline?.document?.selected_inputs?.find(entry => entry.input_id === source.input_id)
          : source.kind === 'plan_adoption' ? snapshot.baseline?.adoption : null;
        details.append(proof({h, t, value: {...source, ...record, ...(item.details && item.kind !== 'quality_assessment' ? {assessment: item.details} : {})}, kind: source.kind,
          name: source.kind === 'plan_adoption' ? snapshot.baseline?.document?.title : record?.value || source.source_ref?.ref || item.text}));
        if (source.kind === 'work_binding') details.append(h('a', {href: `#/item/${source.work_item_id}`}, t('pm_evidence_open_work')));
        values.append(row);
      }
      if (!section.items?.length) values.append(h('p', {}, t(name==='deviations'&&section.version===1?'pq_deviations_empty':'pm_brief_empty_' + name)));
      sections.append(h('section', {class: 'pm-brief-row', 'data-pm-brief-section': name}, h('h3', {}, t('pm_brief_' + name)), values));
    }
    view.append(sections);
    const trace = h('details', {class: 'pm-brief-trace'}, h('summary', {}, t('pm_brief_coverage')),
      h('p', {class: 'muted'}, t('pm_brief_filtered')),
      proof({h, t, value: {coverage:snapshot.coverage,visibility:snapshot.visibility,
        journal_cursor:snapshot.journal_cursor,snapshot_digest:snapshot.snapshot_digest,view_digest:snapshot.view_digest}, name: snapshot.title}));
    view.append(trace);
    exports?.update();
  }
  function renderHistory(page, append = false) {
    if (!append) history.replaceChildren();
    for (const entry of page.snapshots || []) history.append(h('button', {type:'button',class:'secondary',onclick:async()=>{
      selectedSnapshotId=entry.snapshot_id;const ticket=++serial;snapshot=null;signature='';view.replaceChildren();exports?.update();
      try {const data=await api('GET',`${endpoint}/snapshots/${entry.snapshot_id}`);alive();if(ticket===serial)render(data);}
      catch(error){if(retiredView(error)||viewLeft(alive))return;if(ticket===serial)unavailable(error);}
    }}, entry.title || t('pm_brief_title'), ' · ', stamp(entry.as_of)));
    if (page.next_cursor !== null && page.next_cursor !== undefined) {
      const more=h('button',{type:'button',class:'secondary',onclick:async()=>{
        more.disabled=true;
        try {const next=check(await api('GET',`${endpoint}/snapshots?after=${page.next_cursor}&limit=20`));alive();
          if(!more.isConnected)return;more.remove();renderHistory(next,true);
        }catch(error){if(more.isConnected)unavailable(error);}
      }},t('pm_brief_more'));history.append(more);
    }
  }
  async function refresh(selectedId = null) {
    const selected=selectedId||selectedSnapshotId||'latest';if(selectedId)selectedSnapshotId=selectedId;
    const ticket=++serial;head=null;snapshot=null;signature='';view.replaceChildren();history.replaceChildren();create.update();exports?.update();
    try {
      const [latest, listing, current]=await Promise.all([api('GET',`${endpoint}/snapshots/${selected}`),
        api('GET',`${endpoint}/snapshots?limit=20`),api('GET',`${endpoint}/plans/head`)]);alive();if(ticket!==serial)return;
      check(latest);check(listing);if(current.head.workspace_id!==workspaceId)throw Error(t('pm_scope_mismatch'));
      head=current.head;status.replaceChildren();
      const next=JSON.stringify([latest,listing]);if(signature!==next){signature=next;render(latest);renderHistory(listing);}
      create.update();
    }catch(error){if(retiredView(error)||viewLeft(alive))return;if(ticket===serial)unavailable(error);}
  }
  return {box,refresh,withdraw};
}
