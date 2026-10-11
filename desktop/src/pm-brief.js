// All five Brief sections render one immutable snapshot, filtered for current source access.
export function projectBrief({h, t, api, alive, workspaceId, workspace, permitted, form, input, labelled, errorBox}) {
  const endpoint = `/pm/workspaces/${encodeURIComponent(workspaceId)}`;
  let head = null, snapshot = null, signature = '', serial = 0;
  const title = input('pm_brief_snapshot_title', {max: 160, required: false});
  const view = h('div', {class: 'pm-brief-view'}), history = h('div'), status = h('div', {role: 'status'});
  const create = form({action: 'pm.snapshot.create', target: {workspace_id: workspaceId}, fields: {title},
    params: values => values.title.trim() ? {title: values.title.trim()} : {},
    preconditions: () => ({expected_plan_version: head.version}),
    permitted: () => Boolean(head) && permitted(false), recoverPermitted: () => permitted(false),
    label: 'pm_brief_create', onSaved: async result => {await refresh(result.snapshot_id);},
    extra: h('details', {}, h('summary', {}, t('pm_brief_name_optional')), labelled('pm_brief_snapshot_title', title))});
  const box = h('section', {class: 'pm-brief', 'data-pm-brief': ''}, h('header', {class: 'pm-heading'},
    h('div', {}, h('h2', {}, t('pm_brief_title')), h('p', {class: 'muted'}, t('pm_brief_intro'))), create.box),
    status, view, h('details', {}, h('summary', {}, t('pm_brief_history')), history));
  const stamp = value => typeof value === 'number' ? new Date(value * 1000).toLocaleString() : t('pm_evidence_time_unknown');
  const check = data => {if (data.workspace_id !== workspaceId) throw Error(t('pm_scope_mismatch')); return data;};
  const readable = (prefix, value) => {const key = prefix + value, text = t(key); return text === key ? String(value) : text;};
  function render(data) {
    snapshot = check(data).snapshot;
    view.replaceChildren();
    if (!snapshot) {view.append(h('p', {class: 'muted'}, t('pm_brief_empty'))); return;}
    if (snapshot.workspace_id !== workspaceId) throw Error(t('pm_scope_mismatch'));
    view.append(h('p', {class: 'pm-brief-date'}, h('strong', {}, snapshot.title || workspace().name), ' · ',
      t('pm_brief_as_of', {time: stamp(snapshot.as_of)})), h('p', {class: 'muted'}, t('pm_brief_fixed')));
    const sections = h('div', {class: 'pm-brief-sections'});
    for (const name of ['goals','delivery','blockers','deviations','decisions']) {
      const section = snapshot.brief?.[name] || {status:'unavailable', items:[]};
      const values = h('div', {class: 'pm-brief-values'});
      values.append(h('p', {class: 'muted'}, readable('pm_brief_status_', section.status)));
      for (const item of section.items || []) {
        const source = item.source || {}, details = h('div'), row = h('details', {'data-pm-brief-fact': item.kind || name},
          h('summary', {}, item.text), details);
        if (item.status) details.append(h('p', {class: 'muted'}, readable('pm_status_', item.status)));
        if (source.kind === 'plan_input') details.append(h('p', {class: 'muted'}, t('pm_brief_plan_source')),
          h('code', {}, source.revision_id), h('p', {}, source.source_ref?.ref || source.source_ref?.message_id || ''));
        else if (source.kind === 'plan_adoption') details.append(h('p', {class: 'muted'}, t('pm_brief_decision_source')),
          h('code', {}, source.revision_id));
        else if (source.kind === 'work_binding') details.append(h('a', {href: `#/work/${source.work_item_id}`}, t('pm_evidence_open_work')));
        else if (source.kind === 'evidence') {
          const evidence = (snapshot.evidence || []).find(row => row.evidence_id === source.evidence_id);
          if (evidence) details.append(h('p', {class: 'muted'}, readable('pm_evidence_kind_', evidence.source_kind), ' · ', stamp(evidence.source_observed_at)));
          details.append(h('code', {}, source.evidence_id));
        }
        const proof = source.kind === 'evidence' ? (snapshot.evidence || []).find(entry => entry.evidence_id === source.evidence_id)
          : source.kind === 'plan_input' ? snapshot.baseline?.document?.selected_inputs?.find(entry => entry.input_id === source.input_id)
          : source.kind === 'plan_adoption' ? snapshot.baseline?.adoption : null;
        if (proof) details.append(h('details', {}, h('summary', {}, t('pm_evidence_original')),
          h('pre', {class: 'pm-brief-proof'}, JSON.stringify(proof, null, 2))));
        if (item.details) details.append(h('pre', {class: 'pm-brief-proof'}, JSON.stringify(item.details, null, 2)));
        values.append(row);
      }
      if (!section.items?.length) values.append(h('p', {}, t('pm_brief_empty_' + name)));
      sections.append(h('section', {class: 'pm-brief-row', 'data-pm-brief-section': name}, h('h3', {}, t('pm_brief_' + name)), values));
    }
    view.append(sections);
    const trace = h('details', {class: 'pm-brief-trace'}, h('summary', {}, t('pm_brief_coverage')),
      h('p', {class: 'muted'}, t('pm_brief_filtered')),
      h('pre', {class: 'pm-brief-proof'}, JSON.stringify({coverage:snapshot.coverage,visibility:snapshot.visibility,
        journal_cursor:snapshot.journal_cursor,snapshot_digest:snapshot.snapshot_digest,view_digest:snapshot.view_digest},null,2)));
    view.append(trace);
  }
  function renderHistory(page, append = false) {
    if (!append) history.replaceChildren();
    for (const entry of page.snapshots || []) history.append(h('button', {type:'button',class:'secondary',onclick:async()=>{
      const ticket=++serial;
      try {const data=await api('GET',`${endpoint}/snapshots/${entry.snapshot_id}`);alive();if(ticket===serial)render(data);}
      catch(error){status.replaceChildren(errorBox(error));}
    }}, `${entry.title || t('pm_brief_title')} · ${stamp(entry.as_of)}`));
    if (page.next_cursor !== null && page.next_cursor !== undefined) {
      const more=h('button',{type:'button',class:'secondary',onclick:async()=>{
        more.disabled=true;
        try {const next=check(await api('GET',`${endpoint}/snapshots?after=${page.next_cursor}&limit=20`));alive();
          if(!more.isConnected)return;more.remove();renderHistory(next,true);
        }catch(error){if(more.isConnected){more.after(errorBox(error));more.disabled=false;}}
      }},t('pm_brief_more'));history.append(more);
    }
  }
  async function refresh(selectedId = null) {
    const ticket=++serial;head=null;
    try {
      const [latest, listing, current]=await Promise.all([api('GET',`${endpoint}/snapshots/${selectedId || snapshot?.snapshot_id || 'latest'}`),
        api('GET',`${endpoint}/snapshots?limit=20`),api('GET',`${endpoint}/plans/head`)]);alive();if(ticket!==serial)return;
      check(latest);check(listing);if(current.head.workspace_id!==workspaceId)throw Error(t('pm_scope_mismatch'));
      head=current.head;status.replaceChildren();
      const next=JSON.stringify([latest,listing]);if(signature!==next){signature=next;render(latest);renderHistory(listing);}
      create.update();
    }catch(error){if(ticket===serial){head=null;create.update();status.replaceChildren(errorBox(error));}}
  }
  return {box,refresh};
}
