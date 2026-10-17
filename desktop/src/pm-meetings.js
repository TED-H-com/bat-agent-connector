import {iconButton, button, sectionCard, statusPill, disclosure, contextChip} from './ui/components.js';
import {retiredView, viewLeft} from './pm-retired.js';
import {timeStamp, timeText} from './ui/stamps.js';
import {panelActorLabel} from './pm-governance-strings.js';
// Meeting records share the existing durable command forms. A draft is not a completed discussion.
export function workspaceMeetings({h, t, api, alive, namespace, workspaceId, workspace, permitted, form, input, labelled, errorBox, caps}) {
  const endpoint = `/pm/workspaces/${encodeURIComponent(workspaceId)}`;
  const navigationKey = `batc.pm.meeting-navigation.${namespace}.${workspaceId}.${caps()?.actor}`;
  let navigation = {};
  try {const saved=JSON.parse(localStorage.getItem(navigationKey)||'{}');if(saved&&typeof saved==='object'&&!Array.isArray(saved))navigation=saved;}
  catch { /* Navigation is only a preference, never an operation receipt. */ }
  let model = null, selectedId = typeof navigation.meeting_id === 'string' ? navigation.meeting_id : '', signature = '', serial = 0, loaded = false, dirty = false, stale = false, reviewing = false;
  let forms = [], editorMeetingId = null;
  let createOpen = false, createBox = null, entries = [];
  const createId = 'pm-meeting-create-' + crypto.randomUUID();
  const actorLabel = value => panelActorLabel(t, value);
  const status = h('div', {role: 'status'}), view = h('div'), editors = h('div');
  const remember = (key,value) => {navigation[key]=value;try {localStorage.setItem(navigationKey,JSON.stringify(navigation));} catch { /* Request durability remains the shared form's responsibility. */ }};
  const choose = h('select', {'aria-label': t('pm_meeting_choose'), onchange: () => {selectedId = choose.value; remember('meeting_id',selectedId); void refresh(true);}});
  const selection = labelled('pm_meeting_choose', choose); selection.hidden = true;
  const warning = h('p', {class: 'muted', hidden: true}, t('pm_meeting_stale'));
  const rebase = h('button', {type: 'button', class: 'ui-button secondary ui-sm', hidden: true, onclick: () => {if (loaded) renderEditors(true);}}, t('pm_meeting_rebase'));
  const readOnly = h('p', {class: 'ui-meta', hidden: true, 'data-pm-panel-readonly': ''}, t('pm_panel_read_only'));
  const launch = button({variant: 'primary', label: t('pm_meeting_new'), 'data-pm-meeting-create': '', 'aria-controls': createId, 'aria-expanded': 'false', onClick: () => reveal('pm.meeting.create')});
  const element = sectionCard({body: h('div', {}, h('div', {class: 'pm-heading'}, h('div', {class: 'pm-meeting-heading-text'}, h('h3', {}, t('pm_meeting_title')), readOnly), launch,
    iconButton({icon:'refresh-cw', label:t('pm_meeting_refresh'), onClick: () => void refresh()})),
    selection, status, warning, rebase, view, editors)});
  element.classList.add('pm-evidence', 'pm-meetings'); element.dataset.pmMeetings = '';
  const note = key => h('p', {class: 'muted pm-note-text'}, t(key));
  const fold = (key, body, open = false) => {const node = disclosure({label: t(key), body, open}); node.dataset.pmFold = key; return node;};
  const source = id => h('code', {class: 'mono pm-source'}, id || t('pm_meeting_unknown'));
  const check = data => {if (data?.workspace_id !== workspaceId) throw Error(t('pm_scope_mismatch')); return data;};
  const can = () => loaded && permitted(false);
  const role = actor => workspace()?.members?.find(member => member.actor === actor)?.role
    || (actor === caps()?.actor ? workspace()?.membership?.role : null);
  const owner = () => can() && caps()?.actor === model?.detail?.context.document.sprint_definition.owner_actor && role(caps()?.actor) === 'owner';
  const reason = () => input('pm_meeting_reason', {multiline: true, max: 4000});
  const lines = value => value.split('\n').map(item => item.trim()).filter(Boolean);
  const hidden = value => {const node = h('input', {type: 'hidden', value: JSON.stringify(value)}); node.dataset.pmDefault = JSON.stringify(value); return node;};
  const options = (key, entries, value = '') => {
    const node = h('select', {'aria-label': t(key)}, ...entries.map(([id, text]) => h('option', {value: id}, text)));
    if(value&&!entries.some(([id])=>id===value))node.append(h('option',{value},t('pm_meeting_source_unavailable')));
    node.value = value; node.dataset.pmDefault = value; return node;
  };
  const target = () => ({workspace_id: workspaceId, meeting_id: model.detail.meeting.meeting_id});
  const pre = () => ({expected_version: model.detail.meeting.version});
  function update() {
    entries.forEach(sync => sync());
    forms.forEach(editor => editor.update()); warning.hidden = !stale; rebase.hidden = !stale || !can();
    rebase.disabled = !loaded; choose.disabled = !loaded; selection.hidden = !loaded || !model?.meetings.length;
    launch.hidden = !can() || !model?.sprints.length; readOnly.hidden = !loaded || can();
    launch.setAttribute('aria-expanded', String(loaded && createOpen));
    if (createBox) createBox.hidden = !createOpen;
  }
  function entryPoint(node, permitted) {
    const slot = h('div'); let moving = false;
    const sync = () => {
      if (moving) return;
      moving = true;
      // Detaching a focused field can fire change and reenter update().
      try {if (permitted()) {if (node.parentNode !== slot) slot.append(node);} else node.remove();}
      finally {moving = false;}
    };
    entries.push(sync); sync(); return slot;
  }
  function unavailable(error) {
    loaded = false; update(); view.replaceChildren(); choose.replaceChildren(); editors.hidden = true;
    status.replaceChildren(errorBox(error, {within:status}));
  }
  function localForm(config) {
    const expected = JSON.stringify({preconditions: config.preconditions(), context: config.context || null});
    const baseline = h('input', {type: 'hidden', value: expected}); baseline.dataset.pmDefault = expected;
    const editor = form({...config, fields: {...config.fields, _baseline: baseline},
      params: values => config.params(Object.fromEntries(Object.entries(values).filter(([key]) => key !== '_baseline'))),
      preconditions: () => JSON.parse(baseline.value).preconditions,
      permitted: () => !stale && config.permitted(), recoverPermitted: can,
      extra: h('div', {}, config.extra, baseline), onSaved: result => {
        if (config.action === 'pm.meeting.create' && result?.meeting_id) {selectedId = result.meeting_id;remember('meeting_id',selectedId);}
        return refresh(true);
      }});
    if (reviewing && !editor.locked()) editor.set('_baseline', expected);
    if (!editor.locked() && baseline.value !== expected) stale = true;
    editor.box.addEventListener('input', () => {dirty = true;}); editor.box.addEventListener('change', () => {dirty = true;});
    forms.push(editor); return editor;
  }
  function sourceChoices() {
    const detail = model.detail, context = detail.context.document;
    return [{kind: 'snapshot', id: context.snapshot_ref.snapshot_id, label: t('pm_meeting_snapshot')},
      {kind: 'sprint_goal', id: context.sprint_goal.revision_id, label: t('pm_meeting_goal')},
      ...(context.selection_revision_id ? [{kind: 'selection', id: context.selection_revision_id, label: t('pm_meeting_selection')}] : []),
      ...Object.entries(context.artifacts).map(([kind, row]) => ({kind, id: row.revision_id, label: t('pm_meeting_' + kind)})),
      ...(detail.snapshot.evidence || []).map(row => ({kind: 'evidence', id: row.evidence_id, label: row.summary || row.title || t('pm_meeting_evidence')})),
      ...(detail.notes || []).filter(row => row.context_revision_id === detail.context.context_revision_id)
        .map(row => ({kind: 'note', id: row.note_id, label: row.content}))];
  }
  function refsView(refs = []) {
    return refs.length ? fold('pm_meeting_details', h('ul', {}, ...refs.map(ref => h('li', {}, t('pm_meeting_' + ref.kind), ' · ', source(ref.id))))) : '';
  }
  function refsControl(field, getEditor) {
    const holder = h('div'), choices = sourceChoices();
    const key = ref => `${ref.kind}:${ref.id}`;
    const saved = JSON.parse(field.value || '[]');
    for (const ref of saved) if (!choices.some(choice => key(choice) === key(ref))) choices.push({...ref, label: t('pm_meeting_source_unavailable')});
    const controls = choices.map(choice => {
      const checkbox = h('input', {type: 'checkbox'});
      checkbox.addEventListener('change', () => {
        if (!getEditor() || getEditor().locked() || !can() || stale) return;
        const refs = JSON.parse(field.value || '[]').filter(ref => key(ref) !== key(choice));
        if (checkbox.checked) refs.push({kind: choice.kind, id: choice.id});
        field.value = JSON.stringify(refs); getEditor().changed(); dirty = true;
      });
      holder.append(h('label', {class: 'pm-fact-heading'}, checkbox, h('span', {class: 'pm-source'}, choice.label)));
      return [checkbox, key(choice)];
    });
    const sync = () => {const selected = JSON.parse(field.value || '[]').map(key); for (const [node, id] of controls) {
      node.checked = selected.includes(id); node.disabled = !can() || stale || Boolean(getEditor()?.locked());
    }};
    return {element: fold('pm_meeting_sources', h('div', {}, note('pm_meeting_source_help'), holder)), sync};
  }
  function workTitle(item) {
    const fixed = model.detail.snapshot.work?.find(row => row.work_item_id === item.work_item_id && row.binding?.binding_id === item.binding_id);
    return item.title || fixed?.title || t('pm_meeting_unknown');
  }
  function workView(items) {
    return h('div', {class: 'pm-meeting-work'}, ...items.map(item => h('a', {href: `#/work/${encodeURIComponent(item.work_item_id)}`},
      contextChip({text: workTitle(item)}))));
  }
  function outputView(document) {
    const output = document.output, holder = h('div', {'data-pm-meeting-output': document.kind});
    const prose = (key, text) => holder.append(h('h4', {}, t('pm_meeting_' + key)), h('p', {class: 'pm-note-text'}, text));
    const list = (key, rows) => {if (rows?.length) holder.append(h('h4', {}, t('pm_meeting_' + key)), h('ul', {}, ...rows.map(text => h('li', {class: 'pm-note-text'}, text))));};
    if (document.kind === 'planning') {
      for (const key of ['why', 'delivery_approach', 'capacity_discussion']) prose(key, output[key]);
      list('dependency_notes', output.dependency_notes);
    } else if (document.kind === 'review') {
      prose('goal_review', output.goal_review);
      for (const item of output.outcomes || []) holder.append(h('p', {class: 'pm-note-text'}, item.observation), fold('pm_meeting_details', source(item.binding_id)));
      for (const key of ['feedback', 'environment_changes', 'next_steps']) list(key, output[key]);
      if (output.unreviewed_binding_ids?.length) holder.append(note('pm_meeting_unreviewed'), fold('pm_meeting_details', h('div', {}, ...output.unreviewed_binding_ids.map(source))));
    } else {
      list('observations', output.observations); prose('previous_improvement_review', output.previous_improvement_review);
      if (output.no_improvement_rationale) prose('no_improvement_rationale', output.no_improvement_rationale);
    }
    for (const item of document.decisions || []) holder.append(h('article', {class: 'pm-fact'}, h('h4', {}, item.text || item.title || t('pm_meeting_unknown')),
      note('pm_meeting_proposal'), h('p', {class: 'pm-note-text'}, item.rationale), refsView(item.source_refs)));
    for (const item of document.follow_ups || []) holder.append(h('article', {class: 'pm-fact'}, h('h4', {}, item.text || item.title || t('pm_meeting_unknown')),
      h('p', {}, t('pm_meeting_assigned', {actor: actorLabel(item.owner_actor)})), h('p', {class: 'pm-note-text'}, item.expected_effect),
      h('p', {class: 'pm-note-text'}, t('pm_meeting_verification'), t('pm_panel_separator'), item.verification), refsView(item.source_refs)));
    list('unresolved_questions', document.unresolved_questions); holder.append(refsView(document.source_refs));
    return holder;
  }
  function historyView(detail) {
    const holder = h('div');
    for (const kind of ['notes', 'follow_ups', 'contexts', 'drafts']) {
      const section = h('div'); holder.append(fold('pm_meeting_history_' + kind, section));
      const append = (records, cursor) => {
        for (const record of records) {
          if (record.meeting_id !== detail.meeting.meeting_id) throw Error(t('pm_scope_mismatch'));
          const document = record.document;
          section.append(h('details', {}, h('summary', {}, `#${record.sequence} · ${actorLabel(record.actor)}`),
            kind === 'notes' ? h('div', {}, h('p', {class: 'pm-note-text'}, record.content), refsView(record.source_refs))
              : kind === 'drafts' ? outputView(document) : kind === 'contexts' ? h('div', {}, h('p', {class: 'pm-note-text'}, document.sprint_goal.text),
                source(document.snapshot_ref.snapshot_id), source(record.context_revision_id))
                : h('div', {}, h('p', {}, t('pm_meeting_' + document.status)), h('p', {class: 'pm-note-text'}, document.result),
                  statusPill('not_assessed'), source(document.follow_up_id), source(document.snapshot_ref.snapshot_id), refsView(document.source_refs)),
            source(record.context_revision_id || record.event_id || record.revision_id)));
        }
        if (cursor != null) {
          if (!Number.isInteger(cursor) || cursor < 0) throw Error(t('pm_scope_mismatch'));
          const more = h('button', {type: 'button', class: 'ui-button secondary ui-sm', onclick: async () => {
            more.disabled = true;
            try {
              const page = check(await api('GET', `${endpoint}/meetings/${detail.meeting.meeting_id}/history/${kind}?after=${cursor}&limit=20`)); alive();
              if (!more.isConnected) return;
              if (page.meeting_id !== detail.meeting.meeting_id || page.kind !== kind || !Array.isArray(page.records)
                || page.next_cursor != null && (!Number.isInteger(page.next_cursor) || page.next_cursor <= cursor)) throw Error(t('pm_scope_mismatch'));
              more.remove(); append(page.records, page.next_cursor);
            } catch (error) {if (section.isConnected) unavailable(error);} finally {more.disabled = false;}
          }}, t('pm_meeting_more', {kind: t('pm_meeting_history_' + kind)})); section.append(more);
        }
      };
      append(kind === 'notes' ? detail.notes : kind === 'follow_ups' ? detail.follow_up_events : [],
        ['notes', 'follow_ups'].includes(kind) ? detail.history_next_cursors?.[kind] : 0);
    }
    return fold('pm_meeting_history', holder);
  }
  function renderView() {
    view.replaceChildren(); const detail = model.detail;
    if (!detail) {
      view.append(note(can()&&!model.sprints.length?'pm_meeting_sprint_required':'pm_meeting_empty')); return;
    }
    const context = detail.context.document;
    const facts = h('div', {}, h('p', {}, context.sprint_definition.title),
      h('p', {}, t('pm_meeting_snapshot'), ' · ', timeStamp(context.snapshot_ref.as_of)), source(context.snapshot_ref.snapshot_id),
      source(detail.context.context_revision_id), source(context.sprint_goal.revision_id),
      ...context.selected_items.map(item => source(item.binding_id)), h('h4', {}, t('pm_meeting_dod')),
      h('ul', {}, ...context.artifacts.dod.document.criteria.map(item => h('li', {}, item.text))), h('p',{class:'muted'},t('pm_meeting_quality'),detail.completion ? ' ' + t('pm_meeting_completion_help') : ''), historyView(detail));
    view.append(h('div', {class: 'pm-meeting-summary', 'data-pm-meeting-selected': detail.meeting.meeting_id},
      h('div', {class: 'pm-fact-heading'}, h('span', {class: 'ui-pill tone-neutral'}, t('pm_meeting_' + detail.meeting.kind)),
        statusPill(detail.meeting.status === 'completed' ? 'done' : 'draft'), statusPill('not_assessed')),
      h('p', {class: 'pm-note-text'}, context.sprint_goal.text), workView(context.selected_items)));
    if (detail.draft) {
      view.append(fold('pm_meeting_saved_draft', outputView(detail.draft.document)));
      facts.append(source(detail.draft.revision_id));
    }
    if (detail.completion) {
      const record = detail.completion;
      view.append(fold('pm_meeting_completed', h('div', {'data-pm-meeting-completion': ''},
        h('p', {class: 'pm-note-text'}, record.document.discussion_summary), h('p', {}, t('pm_meeting_recorded_by', {actor: actorLabel(record.actor)})),
        ...(record.document.decisions || []).map(item => h('p', {class: 'pm-note-text'}, item.text || item.title,
          ' · ', t(item.disposition === 'recorded_discussion_decision' ? 'pm_meeting_recorded_decision' : 'pm_meeting_proposal'))))));
      facts.append(source(record.completion_id));
    }
    view.append(fold('pm_meeting_details', facts));
  }
  // Collections use ordinary labelled controls; only the durable storage field is serialized.
  function collection(field, getEditor, definitions, limit, key) {
    const rows = h('div'), add = h('button', {type: 'button', class: 'ui-button secondary ui-sm'}, t('pm_meeting_add_' + key));
    const holder = h('div', {}, rows, add); let controls = [];
    const change = values => {if (!getEditor() || getEditor().locked() || !can() || stale) return;
      field.value = JSON.stringify(values); getEditor().changed(); dirty = true;};
    function render() {
      controls = []; rows.replaceChildren(); const values = JSON.parse(field.value || '[]');
      for (const item of values) {
        const fields = definitions.map(def => {
          const node = def.choices ? options(def.label, def.choices, item[def.name] || '') : input(def.label, {multiline: true, max: 4000, required: def.required !== false, value: item[def.name] || ''});
          node.addEventListener('input', () => {const values = JSON.parse(field.value || '[]'), row = values.find(value => value.id === item.id);
            if (row) {row[def.name] = node.value; change(values);}}); controls.push(node); return labelled(def.label, node);
        });
        const remove = h('button', {type: 'button', class: 'ui-button secondary ui-sm', onclick: () => {change(JSON.parse(field.value || '[]').filter(row => row.id !== item.id)); render(); sync();}}, t('pm_meeting_remove'));
        controls.push(remove); rows.append(h('article', {class: 'pm-fact pm-fields'}, ...fields, refsView(item.source_refs), remove));
      }
    }
    const sync = () => {const disabled = !can() || stale || Boolean(getEditor()?.locked());
      controls.forEach(node => {node.disabled = disabled;}); add.disabled = disabled || JSON.parse(field.value || '[]').length >= limit;};
    add.addEventListener('click', () => {const values = JSON.parse(field.value || '[]'); if (values.length >= limit) return;
      change([...values, {id: `${key}_${crypto.randomUUID().replaceAll('-', '')}`, ...Object.fromEntries(definitions.map(def => [def.name, def.choices?.[0]?.[0] || '']))}]); render(); sync();});
    return {element: holder, mount: () => {render(); sync();}, sync};
  }
  function draftEditor() {
    const detail = model.detail, context = detail.context.document, saved = detail.draft?.document;
    const fields = {}, body = h('div', {class: 'pm-fields'}), widgets = []; let editor;
    const textField = (key, value, required = true) => {const node = input('pm_meeting_' + key, {multiline: true, max: 8000, required, value});
      fields[key] = node; body.append(labelled('pm_meeting_' + key, node));};
    const output = saved?.output || {}, kind = detail.meeting.kind;
    let outcomeHolder;
    if (kind === 'planning') {
      for (const key of ['why', 'delivery_approach', 'capacity_discussion']) textField(key, output[key] || '');
      body.append(note('pm_meeting_what_how')); textField('dependency_notes', (output.dependency_notes || []).join('\n'), false);
    } else if (kind === 'review') {
      textField('goal_review', output.goal_review || '');
      fields.outcomes=hidden((output.outcomes||[]).map(({binding_id,observation})=>({binding_id,observation})));
      outcomeHolder=h('div');body.append(outcomeHolder);
      for (const key of ['feedback', 'environment_changes', 'next_steps']) textField(key, (output[key] || []).join('\n'), false);
      body.append(note('pm_meeting_review_help'));
    } else {
      textField('observations', (output.observations || []).join('\n'));
      textField('previous_improvement_review', output.previous_improvement_review || '');
      textField('no_improvement_rationale', output.no_improvement_rationale || '', false); body.append(note('pm_meeting_retro_help'));
    }
    fields.decisions = hidden((saved?.decisions || []).map(({id,text,title,rationale,alternatives,source_refs}) => ({id,text:text || title,rationale,alternatives,source_refs})));
    fields.follow_ups = hidden((saved?.follow_ups || []).map(({id,text,owner_actor,expected_effect,verification,source_refs}) => ({id,text,owner_actor,expected_effect,verification,source_refs})));
    const writers = (workspace()?.members || []).filter(member => ['owner','contributor'].includes(member.role)).map(member => [member.actor, actorLabel(member.actor)]);
    const decision = collection(fields.decisions, () => editor, [{name:'text',label:'pm_meeting_decision_text'}, {name:'rationale',label:'pm_meeting_decision_reason'}], 10, 'decision');
    const follow = collection(fields.follow_ups, () => editor, [{name:'text',label:'pm_meeting_follow_text'}, {name:'owner_actor',label:'pm_meeting_owner',choices:writers},
      {name:'expected_effect',label:'pm_meeting_expected_effect'}, {name:'verification',label:'pm_meeting_verification'}], kind === 'retro' ? 5 : 10, 'followup');
    widgets.push(decision, follow); body.append(fold('pm_meeting_decisions', h('div', {}, note('pm_meeting_proposal'), decision.element)),
      fold('pm_meeting_follow_ups', h('div', {}, note('pm_meeting_follow_help'), follow.element)));
    textField('unresolved_questions', (saved?.unresolved_questions || []).join('\n'), false);
    fields.source_refs = hidden(saved?.source_refs || []); fields.rationale = reason();
    body.append(labelled('pm_meeting_reason', fields.rationale));
    const contextId = detail.context.context_revision_id;
    editor = localForm({action: 'pm.meeting.draft', target: target(), fields, preconditions: pre, context: contextId,
      permitted: () => can() && model.detail.meeting.status === 'draft', label: 'pm_meeting_save_draft',
      onStateChange: () => widgets.forEach(widget => widget.sync()), extra: body, params: values => {
        const output = kind === 'planning' ? {why:values.why,delivery_approach:values.delivery_approach,capacity_discussion:values.capacity_discussion,dependency_notes:lines(values.dependency_notes)}
          : kind === 'review' ? {goal_review:values.goal_review,outcomes:JSON.parse(values.outcomes).filter(row=>row.observation.trim()),
            feedback:lines(values.feedback),environment_changes:lines(values.environment_changes),next_steps:lines(values.next_steps)}
            : {observations:lines(values.observations),previous_improvement_review:values.previous_improvement_review,
              ...(values.no_improvement_rationale.trim() ? {no_improvement_rationale:values.no_improvement_rationale} : {})};
        return {output,decisions:JSON.parse(values.decisions),follow_ups:JSON.parse(values.follow_ups),source_refs:JSON.parse(values.source_refs),
          unresolved_questions:lines(values.unresolved_questions),rationale:values.rationale};
      }});
    // Construct source choices after durableForm restores the saved fields.
    const refs = refsControl(fields.source_refs, () => editor); widgets.push(refs); body.append(refs.element);
    if(outcomeHolder){
      let nodes=[];
      const sync=()=>nodes.forEach(node=>{node.disabled=!can()||stale||editor.locked();});
      const render=()=>{
        const outcomes=JSON.parse(fields.outcomes.value),items=[...context.selected_items];nodes=[];outcomeHolder.replaceChildren();
        for(const outcome of outcomes)if(!items.some(item=>item.binding_id===outcome.binding_id))items.push({binding_id:outcome.binding_id,unavailable:true});
        for(const item of items){
          const node=input('pm_meeting_outcome',{multiline:true,max:4000,required:false,value:outcomes.find(row=>row.binding_id===item.binding_id)?.observation||''});nodes.push(node);
          const change=values=>{if(!can()||stale||editor.locked())return;fields.outcomes.value=JSON.stringify(values);editor.changed();dirty=true;};
          node.addEventListener('input',()=>change([...JSON.parse(fields.outcomes.value).filter(row=>row.binding_id!==item.binding_id),{binding_id:item.binding_id,observation:node.value}]));
          const label=h('label',{},t('pm_meeting_outcome'),' · ',h('span',{class:'pm-source'},workTitle(item)),node);outcomeHolder.append(label,fold('pm_meeting_details',source(item.binding_id)));
          if(item.unavailable){const remove=h('button',{type:'button',class:'ui-button secondary ui-sm',onclick:()=>{change(JSON.parse(fields.outcomes.value).filter(row=>row.binding_id!==item.binding_id));render();sync();}},t('pm_meeting_remove'));
            nodes.push(remove);outcomeHolder.append(note('pm_meeting_source_unavailable'),remove);}
        }
      };render();widgets.push({sync});sync();
    }
    decision.mount(); follow.mount(); refs.sync();
    return {editor, element: fold('pm_meeting_edit_draft', editor.box, editor.locked())};
  }
  function meetingEditors() {
    const detail = model.detail, context = detail.context.document, holder = h('div', {class: 'pm-fields'});
    const content = input('pm_meeting_note_text', {multiline: true, max: 8000}), rationale = reason(), refs = hidden([]); let noteEditor, sourceWidget;
    const extra = h('div', {class: 'pm-fields'}, labelled('pm_meeting_note_text', content), labelled('pm_meeting_reason', rationale));
    noteEditor = localForm({action:'pm.meeting.note',target:target(),fields:{content,rationale,source_refs:refs},preconditions:pre,context:detail.context.context_revision_id,
      permitted:()=>can() && model.detail.meeting.status === 'draft',label:'pm_meeting_save_note',extra,
      onStateChange:()=>sourceWidget?.sync(),params:values=>({...values,source_refs:JSON.parse(values.source_refs)})});
    sourceWidget = refsControl(refs, () => noteEditor); extra.append(sourceWidget.element); sourceWidget.sync();
    const notes = fold('pm_meeting_discuss', noteEditor.box, noteEditor.locked()); notes.hidden = detail.meeting.status === 'completed' && !noteEditor.locked(); holder.append(notes);
    const draft = draftEditor(); draft.element.hidden = detail.meeting.status === 'completed' && !draft.editor.locked(); holder.append(draft.element);
    const draftId = hidden(detail.draft?.revision_id || null), summary = input('pm_meeting_summary', {multiline:true,max:8000}), completeReason=reason();
    const selected = hidden([]), choices = h('div'); let completion;
    const decisionControls = [];
    const addDecision = item => {
      const node = h('input', {type:'checkbox',onchange:()=>{
        if (!completion || completion.locked() || !owner() || stale) return;
        const values=JSON.parse(selected.value).filter(id=>id!==item.id); if(node.checked) values.push(item.id);
        selected.value=JSON.stringify(values);completion.changed();dirty=true;
      }}); choices.append(h('label',{class:'pm-fact-heading'},node,item.text || item.title)); decisionControls.push([node,item.id]);
    };
    (detail.draft?.document.decisions || []).forEach(addDecision);
    const syncDecisions = () => {for(const [node,id]of decisionControls){node.checked=JSON.parse(selected.value).includes(id);node.disabled=!owner()||stale||Boolean(completion?.locked());}};
    completion=localForm({action:'pm.meeting.complete',target:target(),fields:{draft_revision_id:draftId,discussion_summary:summary,decision_ids:selected,rationale:completeReason},preconditions:pre,
      context:{revision_id:detail.context.context_revision_id,draft_revision_id:detail.draft?.revision_id||null},permitted:()=>owner()&&model.detail.meeting.status==='draft'&&Boolean(detail.draft),
      label:'pm_meeting_complete',onStateChange:syncDecisions,extra:h('div',{class:'pm-fields'},note('pm_meeting_complete_help'),
        !owner()?note('pm_meeting_owner_only'):'',labelled('pm_meeting_summary',summary),choices,note('pm_meeting_decision_help'),labelled('pm_meeting_reason',completeReason)),
      params:values=>({...values,draft_revision_id:JSON.parse(values.draft_revision_id),decision_ids:JSON.parse(values.decision_ids)})});
    for(const id of JSON.parse(selected.value))if(!decisionControls.some(([,key])=>key===id))addDecision({id,text:`${t('pm_meeting_source_unavailable')} · ${id}`});
    if(reviewing&&!completion.locked())completion.set('draft_revision_id',JSON.stringify(detail.draft?.revision_id||null));syncDecisions();
    const completeBox=fold('pm_meeting_complete',completion.box,completion.locked());completeBox.hidden=detail.meeting.status==='completed'&&!completion.locked();holder.append(entryPoint(completeBox,owner));
    const snapshots=options('pm_meeting_snapshot',model.snapshots.map(row=>[row.snapshot_id,`${row.title} · ${timeText(row.as_of)}`]),context.snapshot_ref.snapshot_id),contextReason=reason();
    const sprint=model.sprints.find(row=>row.sprint_id===detail.meeting.sprint_id);
    const revise=localForm({action:'pm.meeting.context.revise',target:target(),fields:{snapshot_id:snapshots,rationale:contextReason},preconditions:()=>({...pre(),expected_sprint_version:sprint?.version}),
      context:detail.context.context_revision_id,permitted:()=>can()&&Boolean(sprint)&&model.detail.meeting.status==='draft',label:'pm_meeting_context_save',params:values=>values,
      extra:h('div',{class:'pm-fields'},note('pm_meeting_context_help'),labelled('pm_meeting_snapshot',snapshots),labelled('pm_meeting_reason',contextReason))});
    const reviseBox=fold('pm_meeting_context_change',revise.box,revise.locked());reviseBox.hidden=detail.meeting.status==='completed'&&!revise.locked();holder.append(reviseBox);
    // Always construct this form so a lost completion/follow-up response can be recovered after reload.
    const items=detail.completion?.document.follow_ups||[],allowedItems=items.filter(item=>owner()||item.owner_actor===caps()?.actor),
      followId=options('pm_meeting_follow_choice',allowedItems.map(item=>[item.id,`${item.text || item.title} · ${actorLabel(item.owner_actor)}`]),allowedItems[0]?.id||'');
    const state=options('pm_meeting_follow_status',['open','blocked','reported_done'].map(key=>[key,t('pm_meeting_'+key)]),'open'),result=input('pm_meeting_follow_result',{multiline:true,max:8000}),followReason=reason();
    const snapshot=options('pm_meeting_result_snapshot',[['',t('pm_meeting_keep_snapshot')],...model.snapshots.map(row=>[row.snapshot_id,`${row.title} · ${timeText(row.as_of)}`])]);
    const canRecord = () => can() && (owner() || items.some(item=>item.id===followId.value&&item.owner_actor===caps()?.actor&&['owner','contributor'].includes(role(caps()?.actor))));
    const follow=localForm({action:'pm.meeting.follow_up.record',target:target(),fields:{follow_up_id:followId,status:state,result,rationale:followReason,snapshot_id:snapshot},preconditions:pre,
      context:detail.meeting.completion_id,permitted:()=>canRecord()&&detail.meeting.status==='completed'&&items.some(item=>item.id===followId.value),
      label:'pm_meeting_save_result',params:values=>{const{snapshot_id,...rest}=values;return {...rest,...(snapshot_id?{snapshot_id}:{}),source_refs:[]};},
      extra:h('div',{class:'pm-fields'},note('pm_meeting_follow_help'),labelled('pm_meeting_follow_choice',followId),labelled('pm_meeting_follow_status',state),
        labelled('pm_meeting_follow_result',result),labelled('pm_meeting_result_snapshot',snapshot),labelled('pm_meeting_reason',followReason))});
    followId.addEventListener('change',()=>update());const followBox=fold('pm_meeting_record_result',follow.box,follow.locked());followBox.hidden=!items.length&&!follow.locked();holder.append(entryPoint(followBox,canRecord));
    return holder;
  }
  function creationEditor() {
    if (!model.sprints.length) {createBox = null; return h('div');}
    const initial=model.sprints.some(row=>row.sprint_id===navigation.sprint_id)?navigation.sprint_id:model.sprints.find(row=>row.status==='active')?.sprint_id||model.sprints[0]?.sprint_id||'';
    const holder=h('div',{class:'pm-fields'}),sprint=options('pm_meeting_sprint',model.sprints.map(row=>[row.sprint_id,row.definition.title]),initial),slot=h('div');
    holder.append(labelled('pm_meeting_sprint',sprint),slot);let current;
    const render=()=>{
      const selected=model.sprints.find(row=>row.sprint_id===sprint.value);if(!selected){slot.replaceChildren(note('pm_meeting_sprint_required'));return;}
      const title=input('pm_meeting_name',{max:200}),kind=options('pm_meeting_kind',['planning','review','retro'].map(key=>[key,t('pm_meeting_'+key)]),'planning'),
        snapshot=options('pm_meeting_snapshot',model.snapshots.map(row=>[row.snapshot_id,`${row.title} · ${timeText(row.as_of)}`]),model.snapshots.at(-1)?.snapshot_id||''),rationale=reason();
      current=localForm({action:'pm.meeting.create',target:{workspace_id:workspaceId,sprint_id:selected.sprint_id},fields:{title,kind,snapshot_id:snapshot,rationale},
        preconditions:()=>({expected_sprint_version:selected.version}),permitted:()=>can()&&model.snapshots.length>0&&(kind.value==='planning'||Boolean(selected.started_at)),label:'pm_meeting_create',params:values=>values,
        extra:h('div',{class:'pm-fields'},labelled('pm_meeting_name',title),labelled('pm_meeting_kind',kind),note('pm_meeting_started_help'),
          labelled('pm_meeting_snapshot',snapshot),!model.snapshots.length?note('pm_meeting_snapshot_required'):'',labelled('pm_meeting_reason',rationale))});
      kind.addEventListener('change',()=>current.update());slot.replaceChildren(current.box);update();
      current.box.querySelector('.actions').append(button({variant:'secondary',label:t('pm_panel_cancel'),onClick:()=>{createOpen=false;update();if(!launch.hidden)launch.focus();}}));
      createOpen=createOpen||current.locked()||current.hasDraft();
    };sprint.addEventListener('change',()=>{remember('sprint_id',sprint.value);render();});render();
    createBox=h('div',{id:createId,hidden:!createOpen},holder);return entryPoint(createBox,can);
  }
  function renderEditors(reviewBaselines=false) {
    // Restore permitted entries hidden during loading before retaining open folds.
    entries.forEach(sync => sync());
    const meetingId=model.detail?.meeting.meeting_id || null;
    const expanded=editorMeetingId===meetingId ? new Set([...editors.querySelectorAll('details[open][data-pm-fold]')].map(node=>node.dataset.pmFold)) : new Set();
    const focused=editors.contains(document.activeElement)?document.activeElement:null;
    const action=focused?.closest('[data-pm-action]')?.dataset.pmAction;
    const field=focused?.getAttribute('aria-label');
    const selection=focused&&typeof focused.selectionStart==='number'?[focused.selectionStart,focused.selectionEnd]:null;
    stale=false;dirty=false;forms=[];entries=[];reviewing=reviewBaselines;editors.replaceChildren();
    if(model.detail) {const actions = meetingEditors(); editors.append(entryPoint(fold('pm_meeting_actions', actions, Boolean(actions.querySelector('details[open]'))),can));} editors.append(creationEditor());reviewing=false;update();
    for(const node of editors.querySelectorAll('details[data-pm-fold]'))if(expanded.has(node.dataset.pmFold))node.open=true;
    if(editorMeetingId===meetingId&&action&&field){
      const replacement=editors.querySelector(`[data-pm-action="${CSS.escape(action)}"] [aria-label="${CSS.escape(field)}"]`);
      if(replacement&&!replacement.disabled){replacement.focus({preventScroll:true});if(selection)replacement.setSelectionRange(...selection);}
    }
    editorMeetingId=meetingId;
  }
  async function pages(suffix,property) {
    let after=0,records=[];
    for(let count=0;count<20;count++){
      const page=await api('GET',`${endpoint}/${suffix}?after=${after}&limit=50`);alive();if(page.workspace_id!==undefined)check(page);
      if(!Array.isArray(page[property]))throw Error(t('pm_scope_mismatch'));
      for(const row of page[property])check(row);records.push(...page[property]);
      if(page.next_cursor==null)return records;
      if(!Number.isInteger(page.next_cursor)||page.next_cursor<=after)throw Error(t('pm_scope_mismatch'));after=page.next_cursor;
    }throw Error(t('pm_meeting_partial'));
  }
  async function refresh(force=false) {
    const focused=element.contains(document.activeElement)?document.activeElement:null;
    const selection=focused&&typeof focused.selectionStart==='number'?[focused.selectionStart,focused.selectionEnd]:null;
    const ticket=++serial;loaded=false;update();status.replaceChildren();
    try{
      const [meetings,sprints,snapshots]=await Promise.all([pages('meetings','meetings'),pages('sprints','sprints'),pages('snapshots','snapshots')]);alive();if(ticket!==serial)return;
      if(!meetings.some(row=>row.meeting_id===selectedId))selectedId=meetings.at(-1)?.meeting_id||'';
      const detail=selectedId?check(await api('GET',`${endpoint}/meetings/${selectedId}?history_limit=20`)):null;alive();if(ticket!==serial)return;
      if(detail&&(check(detail.meeting).meeting_id!==selectedId||detail.context?.meeting_id!==selectedId||detail.context.context_revision_id!==detail.meeting.context_revision_id
        ||detail.context.document.sprint_id!==detail.meeting.sprint_id||check(detail.snapshot).snapshot_id!==detail.context.document.snapshot_ref.snapshot_id
        ||detail.draft&&(detail.draft.meeting_id!==selectedId||detail.draft.revision_id!==detail.meeting.draft_revision_id)
        ||detail.completion&&(detail.completion.meeting_id!==selectedId||detail.completion.completion_id!==detail.meeting.completion_id)))throw Error(t('pm_scope_mismatch'));
      const next=JSON.stringify([selectedId,detail?.meeting.version,sprints.map(row=>[row.sprint_id,row.version]),snapshots.map(row=>row.snapshot_id),workspace()?.version]);
      const changed=signature&&signature!==next;model={meetings,sprints,snapshots,detail};loaded=true;editors.hidden=false;
      choose.replaceChildren(...meetings.map(row=>h('option',{value:row.meeting_id},row.title)));choose.value=selectedId;
      renderView();
      if(force||!signature||changed&&!dirty&&!forms.some(editor=>editor.locked()))renderEditors();else if(changed)stale=true;
      signature=next;update();
      if(focused?.isConnected&&!focused.disabled&&document.activeElement===document.body){focused.focus({preventScroll:true});if(selection)focused.setSelectionRange(...selection);}
    }catch(error){if(retiredView(error)||viewLeft(alive))return;if(ticket===serial)unavailable(error);}
  }
  function reveal(action, followUpId) {
    const editor = forms.find(row => row.box.dataset.pmAction === action);
    if (!loaded || !editor) return;
    if (action === 'pm.meeting.create') {createOpen = true; update();}
    if (followUpId && action === 'pm.meeting.follow_up.record') {
      const field = editor.fields.follow_up_id;
      if (![...field.options].some(option => option.value === followUpId)) return;
      if (editor.locked() && field.value !== followUpId) return;
      if (!editor.locked()) editor.set('follow_up_id', followUpId);
      update();
    }
    if (!editor.box.isConnected) return;
    for (let node = editor.box.parentElement; node && node !== element; node = node.parentElement) if (node instanceof HTMLDetailsElement) {node.hidden = false; node.open = true;}
    editor.box.querySelector('input:not([type="hidden"]), select, textarea')?.focus(); editor.box.scrollIntoView({block: 'nearest'});
  }
  async function openRef(target, {form: requested = 'follow_up.record'} = {}) {
    if (!target?.meeting_id) return;
    selectedId = target.meeting_id; remember('meeting_id', selectedId); await refresh(true);
    if (model?.detail?.meeting.meeting_id !== target.meeting_id) return;
    reveal('pm.meeting.' + requested, target.follow_up_id);
  }
  return {element,refresh,openRef};
}
