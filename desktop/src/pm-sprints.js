import {button, disclosure, emptyState, iconButton, sectionCard} from './ui/components.js';
import {timeStamp} from './ui/stamps.js';
// Central Sprint management, with fixed references and the shared durable operation forms.
import {fixedQualityPicker,qualityFact} from './pm-quality-projections.js';
export function workspaceSprints({h, t, api, alive, workspaceId, workspace, permitted, form, input, labelled, errorBox, caps,qualitySelection=null}) {
  const endpoint = `/pm/workspaces/${encodeURIComponent(workspaceId)}`;
  let model = null, signature = '', selectedId = '', serial = 0, loaded = false, dirty = false, stale = false, reviewing = false;
  let forms = [], titles = new Map();
  const status = h('div', {role: 'status'}), view = h('div'), editors = h('div');
  const choose = h('select', {'aria-label': t('pm_sprint_choose'), onchange: () => {selectedId = choose.value; void refresh(true);}});
  const review = button({variant: 'secondary', label: t('pm_sprint_review_fresh'), onClick: () => {if (loaded) renderEditors(true);}});
  review.hidden = true;
  const warning = h('p', {class: 'muted', hidden: true}, t('pm_sprint_stale'));
  const toolbar = h('div', {class: 'pm-tool-head'}, labelled('pm_sprint_choose', choose), review,
    iconButton({icon:'refresh-cw', label:t('pm_sprint_refresh'), ...{type: 'button', onclick: () => void refresh()}}));
  const box = h('section', {class: 'panel pm-evidence', 'data-pm-sprints': ''},
    sectionCard({body: h('div', {}, toolbar, status, warning, view, editors)}));
  const note = key => h('p', {class: 'muted pm-note-text'}, t(key));
  const actor = () => caps()?.actor;
  const can = decide => loaded && permitted(decide);
  const owner = definition => {
    const ws = workspace(), role = Array.isArray(ws?.members) ? ws.members.find(member => member.actor === actor())?.role : ws?.membership?.role;
    return can(false) && actor() === definition.owner_actor && role === 'owner';
  };
  const check = data => {if (data?.workspace_id !== workspaceId) throw Error(t('pm_scope_mismatch')); return data;};
  const title = item => titles.get(item.work_item_id) || `${t('pm_sprint_work_title_unknown')} · ${item.work_item_id}`;
  const source = id => h('code', {class: 'pm-source'}, id || t('pm_sprint_missing'));
  const hidden = value => {const node = h('input', {type: 'hidden', value: JSON.stringify(value)}); node.dataset.pmDefault = '[]'; return node;};
  const opts = (label, entries, value = '') => {
    const select = h('select', {'aria-label': t(label)}, ...entries.map(([key, text]) => h('option', {value: key}, text)));
    select.value = value; select.dataset.pmDefault = value; return select;
  };
  const fold = (label, contents, open = false) => disclosure({element: h('details', {open: open || null}, h('summary', {}, t(label)), contents)});
  const toneFor = {planned: 'info', active: 'info', closed: 'neutral', cancelled: 'warn', not_assessed: 'neutral', unknown: 'neutral'};
  const sprintPill = status => h('span', {class: `ui-pill tone-${toneFor[status] || 'neutral'}`, 'data-status': status,
    'data-pm-sprint-status': status === 'not_assessed' || status === 'unknown' ? null : status}, t(status === 'unknown' ? 'pm_sprint_unknown' : status === 'not_assessed' ? 'pm_sprint_unassessed' : 'pm_sprint_' + status));
  function chosenUtc(field) {
    const shown = h('span', {'data-pm-chosen-time': ''});
    const paint = () => {
      shown.replaceChildren();
      if (!field.value) {shown.append(sprintPill('unknown')); return;}
      const date = new Date(field.value + 'Z');
      if (!Number.isFinite(date.getTime())) {shown.append(sprintPill('unknown')); return;}
      const stamp = timeStamp(date.toISOString());
      shown.append(stamp instanceof Node ? stamp : String(stamp));
    };
    field.addEventListener('input', paint); field.addEventListener('change', paint); paint();
    return shown;
  }
  const mono = value => h('code', {class: 'mono'}, value || t('pm_sprint_missing'));
  const markDirty = () => {dirty = true;};
  function localForm(options) {
    const expected = JSON.stringify({preconditions: options.preconditions(), binding: options.binding || null});
    const baseline = h('input', {type: 'hidden', value: expected});
    baseline.dataset.pmDefault = expected;
    const result = form({...options, fields: {...options.fields, _baseline: baseline},
      extra: h('div', {}, options.extra, baseline),
      params: values => options.params(Object.fromEntries(Object.entries(values).filter(([name]) => name !== '_baseline'))),
      preconditions: () => JSON.parse(baseline.value).preconditions, permitted: () => !stale && options.permitted(),
      recoverPermitted: () => can(false), onSaved: result => {
        if (options.action === 'pm.sprint.create' && result?.sprint_id) selectedId = result.sprint_id;
        return refresh(true);
      }});
    if (reviewing && !result.locked()) result.set('_baseline', expected);
    if (!result.locked() && baseline.value !== expected) stale = true;
    result.box.addEventListener('input', markDirty); result.box.addEventListener('change', markDirty);
    forms.push(result); return result;
  }
  function update() {
    for (const editor of forms) editor.update();
    warning.hidden = !stale; review.hidden = !stale; review.disabled = !loaded; choose.disabled = !loaded;
  }
  function unavailable(error) {
    loaded = false; update(); view.replaceChildren(); choose.replaceChildren(); editors.hidden = true;
    status.replaceChildren(errorBox(error));
  }
  const rationale = () => input('pm_sprint_reason', {multiline: true, max: 4000});
  const row = (label, contents) => h('section', {class: 'pm-brief-row'}, h('h3', {}, t(label)), h('div', {class: 'pm-brief-values'}, contents));
  function workRows(items) {
    return h('div', {class: 'pm-evidence-rows pm-sprint-work-list'}, ...(items.length ? items.map(item => h('article', {class: 'pm-fact', 'data-pm-sprint-work': item.work_item_id},
      h('div', {class: 'pm-sprint-work-heading'}, h('a', {href: `#/item/${encodeURIComponent(item.work_item_id)}`}, title(item)),
        item.assignee ? h('span', {class: 'muted'}, item.assignee) : ''),
      item.assignee ? h('p', {class: 'pm-note-text'}, item.how || t('pm_sprint_how_empty')) : '',
      fold('pm_sprint_versions', h('div', {}, note('pm_sprint_unassessed'), source(item.binding_id), h('p', {}, source(item.plan_revision_id)),
        h('p', {}, (item.requirement_ids || []).join(', ')))))) : [note('pm_sprint_no_work')]));
  }
  function renderView() {
    view.replaceChildren();
    const data = model?.detail;
    if (!data) {view.append(emptyState({title: t('pm_sprint_empty'), heading: 'h3'})); return;}
    const sprint = data.sprint, def = sprint.definition, items = data.selected_items || [];
    const developers = h('span', {});
    def.developers.forEach((id, index) => {if (index) developers.append(', '); developers.append(mono(id));});
    view.append(h('div', {class: 'pm-sprint-answer'},
      h('div', {class: 'pm-tool-head'}, sprintPill(sprint.status), data.quality === 'fixed_original_assessments' ? null : sprintPill('not_assessed')),
      h('p', {class: 'pm-sprint-goal'}, data.goal.text),
      items.length ? h('ul', {class: 'pm-sprint-work'}, ...items.map(item => h('li', {'data-pm-sprint-work': item.work_item_id},
        h('a', {href: `#/item/${encodeURIComponent(item.work_item_id)}`}, title(item))))) : note('pm_sprint_no_work'),
      h('p', {class: 'pm-sprint-period'}, timeStamp(def.starts_at), ' – ', timeStamp(def.ends_at)),
      data.timebox_elapsed ? note('pm_sprint_elapsed') : null,
      fold('pm_sprint_details', h('div', {},
        h('p', {class: 'muted'}, t(def.mode === 'scrum' ? 'pm_sprint_scrum' : 'pm_sprint_personal')),
        h('p', {}, t('pm_sprint_owner'), ': ', t('pm_sprint_role_owner'), ' ', mono(def.owner_actor)),
        h('p', {}, t('pm_sprint_developers'), ': ', developers),
        h('p', {}, t('pm_sprint_capacity'), ': ', def.capacity_note),
        h('ul', {}, ...model.fixed.dod.document.criteria.map(item => h('li', {}, item.text))),
        data.quality === 'fixed_original_assessments' ? null : note('pm_sprint_quality'),
        h('p', {}, t('pm_sprint_product_goal'), ': ', model.fixed.product_goal.document.text),
        h('p', {}, mono(def.product_goal_revision_id)), h('p', {}, mono(def.dod_revision_id)), h('p', {}, mono(def.backlog_revision_id))))));
    const ticket = serial, terminal = ['closed', 'cancelled'].includes(sprint.status);
    let ending = null, eventCursor = data.history_next_cursors?.events ?? null, eventPending = false, loadEventPage = null;
    const closure = h('section', {class: 'pm-plans', 'data-pm-sprint-closure': ''});
    if (terminal) view.append(closure);
    const endingItems = items => h('div', {class: 'pm-evidence-rows'}, ...items.map(item =>
      h('article', {class: 'pm-fact', 'data-pm-sprint-ended-work': item.work_item_id},
        h('a', {href: `#/item/${encodeURIComponent(item.work_item_id)}`}, title(item)),
        item.quality_assessment ? qualityFact({h,t,fact:item.quality_assessment}) : sprintPill('not_assessed'),
        h('p', {class: 'pm-note-text'}, t(item.counts_as_done ? 'pq_increment' : 'pm_sprint_reconsider')),
        h('p', {class: 'pm-note-text'}, item.reason),
        fold('pm_sprint_versions', h('div', {}, source(item.binding_id), h('p', {}, source(item.plan_revision_id)),
          h('p', {}, (item.requirement_ids || []).join(', ')))))));
    function renderClosure() {
      if (!terminal) return;
      closure.replaceChildren(h('h3', {}, t('pm_sprint_closure')));
      if (ending) {
        closure.append(h('p', {class: 'muted'}, t('pm_sprint_recorded_by', {actor: ending.actor})),
          h('p', {class: 'pm-note-text'}, ending.document.rationale), endingItems(ending.document.items || []),
          note('pm_sprint_no_carryover'));
      } else if (eventCursor != null) {
        const loadClosure = button({variant: 'secondary', label: t(eventPending ? 'pm_sprint_history_loading' : 'pm_sprint_load_closure'), onClick: () => void loadEventPage?.()});
        loadClosure.disabled = eventPending;
        closure.append(note('pm_sprint_closure_pending'), loadClosure);
      } else closure.append(note('pm_sprint_closure_unavailable'));
    }
    const history = h('div');
    for (const kind of ['goals', 'selections', 'events']) {
      const holder = h('section', {'data-pm-sprint-history': kind}), navigation = h('div'), seen = new Set();
      const records = kind === 'goals' ? data.goal_revisions : kind === 'selections' ? data.selection_revisions : data.history;
      holder.append(h('h4', {}, t('pm_sprint_history_' + kind))); history.append(holder);
      function append(page) {
        if (!Array.isArray(page.records) || page.next_cursor != null && (!Number.isInteger(page.next_cursor) || page.next_cursor < 0)) throw Error(t('pm_scope_mismatch'));
        navigation.remove();
        for (const entry of page.records) {
          if (entry.sprint_id !== sprint.sprint_id || entry.workspace_id !== undefined && entry.workspace_id !== workspaceId
              || !Number.isInteger(entry.sequence) || entry.sequence < 1) throw Error(t('pm_scope_mismatch'));
          if (seen.has(entry.sequence)) continue;
          seen.add(entry.sequence);
          const isEnding = kind === 'events' && ['pm.sprint.close', 'pm.sprint.cancel'].includes(entry.action);
          if (isEnding && (!ending || entry.sequence > ending.sequence)) ending = entry;
          const body = h('div', {}, h('p', {class: 'muted'}, t('pm_sprint_recorded_by', {actor: entry.actor})),
            h('p', {class: 'pm-note-text'}, entry.rationale || entry.document?.rationale || ''));
          if (entry.text) body.append(h('p', {class: 'pm-note-text'}, entry.text));
          if (entry.items) body.append(workRows(entry.items));
          if (isEnding) body.append(endingItems(entry.document?.items || []), note('pm_sprint_no_carryover'));
          const labels = {'pm.sprint.create': 'pm_sprint_save', 'pm.sprint.goal.revise': 'pm_sprint_save_sprint_goal',
            'pm.sprint.select': 'pm_sprint_save_selection', 'pm.sprint.start': 'pm_sprint_start',
            'pm.sprint.close': 'pm_sprint_close', 'pm.sprint.cancel': 'pm_sprint_cancel'};
          holder.append(disclosure({element: h('details', {}, h('summary', {}, `#${entry.sequence} · ${t(kind === 'events' ? labels[entry.action] || 'pm_sprint_history_events' : 'pm_sprint_history_' + kind)}`), body)}));
        }
        navigation.replaceChildren(); holder.append(navigation);
        if (kind === 'events') {eventCursor = page.next_cursor ?? null; loadEventPage = null;}
        if (page.next_cursor != null) {
          const more = button({variant: 'secondary', label: t('pm_sprint_more_history', {kind: t('pm_sprint_history_' + kind)})});
          const load = async () => {
            if (more.disabled || !loaded || ticket !== serial) return;
            more.disabled = true;
            if (kind === 'events') {eventPending = true; renderClosure();}
            try {
              const next = check(await api('GET', `${endpoint}/sprints/${sprint.sprint_id}/history/${kind}?after=${page.next_cursor}&limit=50`)); alive();
              if (!more.isConnected || ticket !== serial) return;
              if (next.sprint_id !== sprint.sprint_id || next.kind !== kind
                  || next.next_cursor != null && (!Number.isInteger(next.next_cursor) || next.next_cursor <= page.next_cursor)) throw Error(t('pm_scope_mismatch'));
              append(next);
            } catch (error) {if (holder.isConnected && ticket === serial) unavailable(error);}
            finally {
              more.disabled = false;
              if (kind === 'events' && ticket === serial && closure.isConnected) {eventPending = false; renderClosure();}
            }
          };
          more.addEventListener('click', () => void load());
          navigation.append(note('pm_sprint_history_partial'), more);
          if (kind === 'events') loadEventPage = load;
        }
        if (kind === 'events') renderClosure();
      }
      append({records: records || [], next_cursor: data.history_next_cursors?.[kind]});
    }
    view.append(fold('pm_sprint_history', history));
  }
  function checklist(field, choices, editable, editorRef, label, ordered = false) {
    const holder = h('div', {class: 'pm-fields', 'aria-label': t(label)});
    const order = h('ol');
    const controls = choices.map(choice => {
      const checkbox = h('input', {type: 'checkbox'});
      checkbox.addEventListener('change', () => {
        const editor = editorRef(); if (!editor || editor.locked() || !editable() || stale) return;
        const selected = JSON.parse(field.value || '[]');
        field.value = JSON.stringify(checkbox.checked ? [...selected.filter(id => id !== choice.id), choice.id] : selected.filter(id => id !== choice.id));
        editor.changed(); markDirty(); sync();
      }); holder.append(h('label', {class: 'pm-fact-heading'}, checkbox, choice.label)); return [checkbox, choice.id];
    });
    if (ordered) holder.append(h('p', {class: 'muted'}, t('pm_sprint_priority')), order);
    const sync = () => {
      const values = JSON.parse(field.value || '[]'), locked = Boolean(editorRef()?.locked()) || !editable() || stale;
      for (const [checkbox, id] of controls) {checkbox.checked = values.includes(id); checkbox.disabled = locked;}
      if (ordered) order.replaceChildren(...values.map((id, index) => {
        const label = choices.find(choice => choice.id === id)?.label || id;
        const move = shift => {
          if (editorRef().locked() || !editable() || stale) return;
          const next = [...values];
          if (shift === null) next.splice(index, 1);
          else [next[index], next[index + shift]] = [next[index + shift], next[index]];
          field.value = JSON.stringify(next); editorRef().changed(); markDirty(); sync();
        };
        return h('li', {class: 'pm-source'}, label, h('div', {class: 'actions'},
          button({variant: 'ghost', size: 'sm', label: t('pm_sprint_earlier'), disabled: locked || index === 0, onClick: () => move(-1)}),
          button({variant: 'ghost', size: 'sm', label: t('pm_sprint_later'), disabled: locked || index === values.length - 1, onClick: () => move(1)}),
          button({variant: 'ghost', size: 'sm', label: t('pm_sprint_remove'), disabled: locked, onClick: () => move(null)})));
      }));
    };
    return {box: holder, sync};
  }
  function artifactEditors() {
    const artifacts = model.artifacts, adopted = model.accepted.plan, content = h('div', {class: 'pm-fields'});
    content.append(row('pm_sprint_product_goal', h('p', {class: 'pm-note-text'}, artifacts.product_goal?.document.text || t('pm_sprint_missing'))),
      row('pm_sprint_dod', artifacts.dod ? h('ul', {}, ...artifacts.dod.document.criteria.map(item => h('li', {}, item.text))) : note('pm_sprint_missing')),
      row('pm_sprint_backlog', workRows(artifacts.backlog?.document.items || [])));
    content.append(adopted ? h('p', {class: 'muted'}, t('pm_sprint_plan_source'), ' · ', source(adopted.revision_id)) : note('pm_sprint_plan_required'));
    for (const kind of ['product_goal', 'dod']) {
      const reason = rationale(), field = input(kind === 'dod' ? 'pm_sprint_dod_lines' : 'pm_sprint_product_goal',
        {multiline: true, max: kind === 'dod' ? 128000 : 4000, value: kind === 'dod'
          ? (artifacts.dod?.document.criteria || []).map(item => item.text).join('\n') : artifacts.product_goal?.document.text || ''});
      const editor = localForm({action: `pm.${kind}.revise`, target: {workspace_id: workspaceId}, fields: {text: field, rationale: reason},
        params: values => ({plan_revision_id: adopted?.revision_id, rationale: values.rationale,
          ...(kind === 'product_goal' ? {text: values.text} : {criteria: values.text.split('\n').map(text => text.trim()).filter(Boolean).map(text => ({text,
            id: artifacts.dod?.document.criteria.find(item => item.text === text)?.id || `criterion_${crypto.randomUUID().replaceAll('-', '')}`}))})}),
        preconditions: () => ({expected_revision_id: artifacts[kind]?.revision_id || null}),
        binding: {plan_revision_id: adopted?.revision_id || null},
        permitted: () => can(true) && Boolean(adopted), label: kind === 'dod' ? 'pm_sprint_save_dod' : 'pm_sprint_save_goal',
        extra: h('div', {class: 'pm-fields'}, labelled(kind === 'dod' ? 'pm_sprint_dod_lines' : 'pm_sprint_product_goal', field), labelled('pm_sprint_reason', reason))});
      content.append(fold(kind === 'dod' ? 'pm_sprint_edit_dod' : 'pm_sprint_edit_goal', editor.box, editor.locked()));
    }
    const bindingIds = hidden((artifacts.backlog?.document.items || []).map(item => item.binding_id)), reason = rationale(); let backlog;
    const available = model.bindings.filter(item => item.plan_revision_id);
    const picks = checklist(bindingIds, available.map(item => ({id: item.binding_id, label: title(item)})), () => can(true), () => backlog, 'pm_sprint_work_choice', true);
    backlog = localForm({action: 'pm.backlog.revise', target: {workspace_id: workspaceId}, fields: {binding_ids: bindingIds, rationale: reason},
      params: values => ({product_goal_revision_id: artifacts.product_goal?.revision_id, binding_ids: JSON.parse(values.binding_ids || '[]'), rationale: values.rationale}),
      preconditions: () => ({expected_revision_id: artifacts.backlog?.revision_id || null}), permitted: () => can(true) && Boolean(artifacts.product_goal),
      binding: {product_goal_revision_id: artifacts.product_goal?.revision_id || null, available_binding_ids: available.map(item => item.binding_id)},
      label: 'pm_sprint_save_backlog', onStateChange: picks.sync,
      extra: h('div', {class: 'pm-fields'}, bindingIds, available.length ? picks.box : note('pm_sprint_no_bound_work'), labelled('pm_sprint_reason', reason))});
    picks.sync(); content.append(fold('pm_sprint_edit_backlog', backlog.box, backlog.locked()));
    const past = h('div'), history = fold('pm_sprint_artifact_history', past);
    let reading = false, fetched = false;
    history.addEventListener('toggle', async () => {
      if (!history.open || reading || fetched) return;
      reading = true;
      async function show(kind, holder, after = 0) {
        const page = await api('GET', `${endpoint}/sprint-artifacts/${kind}?after=${after}&limit=20`); alive();
        if (!holder.isConnected) return;
        for (const record of page.revisions || []) {
          check(record); if (record.kind !== kind) throw Error(t('pm_scope_mismatch'));
          holder.append(h('details', {}, h('summary', {}, `#${record.sequence} · ${record.actor}`),
            h('p', {class: 'pm-note-text'}, record.document.rationale || ''), source(record.revision_id),
            kind === 'product_goal' ? h('p', {class: 'pm-note-text'}, record.document.text)
              : kind === 'dod' ? h('ul', {}, ...record.document.criteria.map(item => h('li', {}, item.text))) : workRows(record.document.items)));
        }
        if (page.next_cursor != null) {
          if (!Number.isInteger(page.next_cursor) || page.next_cursor <= after) throw Error(t('pm_scope_mismatch'));
          const more = button({variant: 'secondary', label: t('pm_sprint_more_history', {kind: t('pm_sprint_' + kind)}), onClick: async () => {
            more.disabled = true;
            try {await show(kind, holder, page.next_cursor); alive(); more.remove();}
            catch (error) {if (more.isConnected) unavailable(error);} finally {more.disabled = false;}
          }}); holder.append(more);
        }
      }
      try {
        past.replaceChildren();
        for (const kind of ['product_goal', 'dod', 'backlog']) {
          const holder = h('section', {}, h('h4', {}, t('pm_sprint_' + kind))); past.append(holder); await show(kind, holder);
        }
        fetched = true;
      } catch (error) {if (history.isConnected) unavailable(error);} finally {reading = false;}
    });
    content.append(history); return fold('pm_sprint_commitments', content, !artifacts.backlog);
  }
  function creationEditor() {
    const artifacts = model.artifacts, members = workspace()?.members || [], ready = artifacts.product_goal && artifacts.dod && artifacts.backlog;
    const name = input('pm_sprint_name', {max: 200}), goal = input('pm_sprint_goal', {multiline: true, max: 4000}), reason = rationale();
    const capacity = input('pm_sprint_capacity', {multiline: true, max: 4000}), starts = input('pm_sprint_start_at'), ends = input('pm_sprint_end_at'); starts.type = ends.type = 'datetime-local';
    const mode = opts('pm_sprint_mode', [['personal_iteration', t('pm_sprint_personal')], ['scrum', t('pm_sprint_scrum')]], 'personal_iteration');
    const lead = opts('pm_sprint_owner', members.filter(item => item.role === 'owner').map(item => [item.actor, item.actor]), actor());
    const master = opts('pm_sprint_master', [['', t('pm_sprint_none')], ...members.filter(item => ['owner', 'contributor'].includes(item.role)).map(item => [item.actor, item.actor])]);
    const developers = hidden([]); let create;
    const picks = checklist(developers, members.filter(item => ['owner', 'contributor'].includes(item.role)).map(item => ({id: item.actor, label: item.actor})),
      () => can(true), () => create, 'pm_sprint_developers');
    const workspaceVersion = workspace()?.version;
    create = localForm({action: 'pm.sprint.create', target: {workspace_id: workspaceId},
      fields: {title: name, goal, mode, owner_actor: lead, scrum_master: master, developers, starts_at: starts, ends_at: ends, capacity_note: capacity, rationale: reason},
      params: values => {
        const chosen = JSON.parse(values.developers || '[]'); if (!chosen.length || !values.starts_at || !values.ends_at) throw Error(t('pm_sprint_invalid'));
        return {...values, developers: chosen, scrum_master: values.scrum_master || null,
          starts_at: new Date(values.starts_at + 'Z').toISOString(), ends_at: new Date(values.ends_at + 'Z').toISOString(),
          backlog_revision_id: artifacts.backlog.revision_id, dod_revision_id: artifacts.dod.revision_id};
      }, preconditions: () => ({expected_workspace_version: workspaceVersion}),
      binding: {backlog_revision_id: artifacts.backlog?.revision_id || null, dod_revision_id: artifacts.dod?.revision_id || null},
      permitted: () => can(true) && Boolean(ready) && members.length > 0,
      label: 'pm_sprint_save', onStateChange: picks.sync,
      extra: h('div', {class: 'pm-fields'}, !ready ? note('pm_sprint_plan_required') : '', !members.length ? note('pm_sprint_no_members') : '',
        labelled('pm_sprint_name', name), labelled('pm_sprint_goal', goal), labelled('pm_sprint_mode', mode),
        h('div', {class: 'capture-fields'},
          h('div', {class: 'pm-time-field'}, labelled('pm_sprint_start_at', starts), h('span', {class: 'pm-local-time'}, t('pm_sprint_shown'), ' ', chosenUtc(starts))),
          h('div', {class: 'pm-time-field'}, labelled('pm_sprint_end_at', ends), h('span', {class: 'pm-local-time'}, t('pm_sprint_shown'), ' ', chosenUtc(ends)))), note('pm_sprint_period_help'),
        labelled('pm_sprint_owner', lead), h('div', {}, h('p', {}, t('pm_sprint_developers')), developers, picks.box),
        labelled('pm_sprint_master', master), labelled('pm_sprint_capacity', capacity), labelled('pm_sprint_reason', reason), note('pm_sprint_no_execution'))});
    picks.sync(); return fold('pm_sprint_new', create.box, !model.detail || create.locked());
  }
  function sprintEditors() {
    const detail = model.detail, sprint = detail.sprint, def = sprint.definition, target = {workspace_id: workspaceId, sprint_id: sprint.sprint_id};
    const holder = h('div', {class: 'pm-plans'}), preconditions = () => ({expected_version: sprint.version});
    const currentOwner = () => owner(def), canSelect = () => can(false) && (currentOwner() || def.developers.includes(actor()))
      && ['planned', 'active'].includes(sprint.status) && !(sprint.status === 'active' && detail.timebox_elapsed);
    const items = hidden((detail.selected_items || []).map(({binding_id, assignee, how}) => ({binding_id, assignee, how}))), reason = rationale();
    const workChoices = h('div', {class: 'pm-fields'}); let selection;
    const widgets = (model.fixed.backlog.document.items || []).map(binding => {
      const checked = h('input', {type: 'checkbox'}), assignee = opts('pm_sprint_developers', def.developers.map(id => [id, id]));
      assignee.setAttribute('aria-label', t('pm_sprint_assignee', {work: title(binding)}));
      const how = h('textarea', {'aria-label': t('pm_sprint_how', {work: title(binding)}), maxlength: 4000});
      function modify(change) {
        if (selection.locked() || !canSelect() || stale) return;
        items.value = JSON.stringify(change(JSON.parse(items.value || '[]'))); selection.changed(); markDirty(); sync();
      }
      checked.addEventListener('change', () => modify(values => checked.checked
        ? [...values, {binding_id: binding.binding_id, assignee: def.developers.includes(actor()) ? actor() : def.developers[0], how: ''}]
        : values.filter(value => value.binding_id !== binding.binding_id)));
      assignee.addEventListener('change', () => modify(values => values.map(value => value.binding_id === binding.binding_id ? {...value, assignee: assignee.value, how: ''} : value)));
      how.addEventListener('input', () => modify(values => values.map(value => value.binding_id === binding.binding_id ? {...value, how: how.value} : value)));
      workChoices.append(h('div', {class: 'pm-fact'}, h('label', {class: 'pm-fact-heading'}, checked, title(binding)), h('div', {class: 'pm-fields'}, assignee, how)));
      const sync = () => {
        const value = JSON.parse(items.value || '[]').find(item => item.binding_id === binding.binding_id), locked = Boolean(selection?.locked()) || !canSelect() || stale;
        checked.checked = Boolean(value); checked.disabled = locked || Boolean(value && value.assignee !== actor() && !currentOwner());
        assignee.value = value?.assignee || actor(); assignee.disabled = locked || !value || !currentOwner();
        if (how.value !== (value?.how || '')) how.value = value?.how || '';
        how.disabled = locked || !value || value.assignee !== actor();
      }; return sync;
    });
    selection = localForm({action: 'pm.sprint.select', target, fields: {items, rationale: reason},
      params: values => ({items: JSON.parse(values.items || '[]'), rationale: values.rationale}), preconditions,
      permitted: canSelect, label: 'pm_sprint_save_selection', onStateChange: () => widgets.forEach(sync => sync()),
      extra: h('div', {class: 'pm-fields'}, note('pm_sprint_how_help'), items, workChoices, labelled('pm_sprint_reason', reason))});
    widgets.forEach(sync => sync()); holder.append(fold('pm_sprint_select', selection.box, selection.locked()));
    const newGoal = input('pm_sprint_goal', {multiline: true, max: 4000, value: detail.goal.text}), goalReason = rationale();
    const goalEditor = localForm({action: 'pm.sprint.goal.revise', target, fields: {goal: newGoal, rationale: goalReason}, params: values => values,
      preconditions, permitted: () => currentOwner() && sprint.status === 'planned', label: 'pm_sprint_save_sprint_goal',
      extra: h('div', {class: 'pm-fields'}, labelled('pm_sprint_goal', newGoal), labelled('pm_sprint_reason', goalReason))});
    const goalBox = fold('pm_sprint_revise_goal', goalEditor.box, goalEditor.locked()); goalBox.hidden = sprint.status !== 'planned' && !goalEditor.locked(); holder.append(goalBox);
    for (const action of ['start', 'close', 'cancel']) {
      const reasonField = rationale();
      const refs = hidden({}), picks = qualitySelection && action !== 'start' ? detail.selected_items.map(item => ({item,
        picker:fixedQualityPicker({h,t,api,alive,workspaceId,workItemId:item.work_item_id,readSelection:qualitySelection,
          field:refs,multiple:true,constraints:{binding_id:item.binding_id,dod_revision_id:def.dod_revision_id},errorBox})})) : [];
      const eligible = () => action === 'start' ? sprint.status === 'planned' && detail.selected_items.length > 0 && !detail.timebox_elapsed && Date.now() >= Date.parse(def.starts_at)
        : action === 'close' ? sprint.status === 'active' && detail.timebox_elapsed : ['planned', 'active'].includes(sprint.status);
      const editor = localForm({action: `pm.sprint.${action}`, target, fields: {rationale: reasonField,...(picks.length?{quality_refs:refs}:{})},
        params: values => ({rationale:values.rationale,...(values.quality_refs && Object.keys(JSON.parse(values.quality_refs)).length
          ? {quality_refs:JSON.parse(values.quality_refs)} : {})}), preconditions,
        permitted: () => currentOwner() && eligible(), label: 'pm_sprint_' + action,
        onStateChange:()=>picks.forEach(({picker})=>picker.update()),
        extra: h('div', {class: 'pm-fields'}, note(!currentOwner() ? 'pm_sprint_owner_only' : action === 'close' ? 'pm_sprint_close_help'
          : action === 'cancel' ? 'pm_sprint_cancel_help' : 'pm_sprint_future'), labelled('pm_sprint_reason', reasonField),
          ...(picks.length ? [refs,...picks.map(({item,picker})=>h('div',{class:'pm-fact'},h('p',{},title(item)),picker.element))] : []))});
      picks.forEach(({picker})=>picker.attach(editor));
      const section = fold('pm_sprint_' + action, editor.box, editor.locked());
      section.hidden = (action === 'start' ? sprint.status !== 'planned' : action === 'close' ? sprint.status !== 'active'
        : !['planned', 'active'].includes(sprint.status)) && !editor.locked(); holder.append(section);
    } return holder;
  }
  function renderEditors(reviewBaselines = false) {
    stale = false; dirty = false; forms = []; reviewing = reviewBaselines; editors.replaceChildren();
    if (model.detail) editors.append(sprintEditors());
    editors.append(creationEditor(), artifactEditors()); reviewing = false; update();
  }
  async function pages(suffix, property) {
    let cursor = 0, records = [];
    for (let count = 0; count < 20; count++) {
      const data = await api('GET', `${endpoint}/${suffix}?after=${cursor}&limit=100`); alive();
      if (data.workspace_id !== undefined) check(data);
      if (!Array.isArray(data[property])) throw Error(t('pm_scope_mismatch'));
      for (const row of data[property]) check(row);
      records.push(...data[property]); if (data.next_cursor == null) return records;
      if (!Number.isInteger(data.next_cursor) || data.next_cursor <= cursor) throw Error(t('pm_scope_mismatch'));
      cursor = data.next_cursor;
    } throw Error(t('pm_sprint_partial'));
  }
  async function refresh(force = false) {
    const focused = box.contains(document.activeElement) ? document.activeElement : null;
    const selection = focused && typeof focused.selectionStart === 'number' ? [focused.selectionStart, focused.selectionEnd] : null;
    const ticket = ++serial; loaded = false; update(); status.replaceChildren();
    try {
      const [artifacts, accepted, bindings, sprints] = await Promise.all([
        api('GET', `${endpoint}/sprint-artifacts`), api('GET', `${endpoint}/plans/accepted`), pages('work', 'bindings'), pages('sprints', 'sprints')]);
      alive(); if (ticket !== serial) return;
      check(artifacts); check(accepted.head); if (accepted.plan) check(accepted.plan);
      for (const kind of ['product_goal', 'dod', 'backlog']) if (artifacts[kind]) check(artifacts[kind]);
      if (!sprints.some(item => item.sprint_id === selectedId)) selectedId = sprints.find(item => item.status === 'active')?.sprint_id || sprints.at(-1)?.sprint_id || '';
      let detail = null, fixed = {};
      if (selectedId) {
        detail = await api('GET', `${endpoint}/sprints/${selectedId}?history_limit=50`); alive(); if (ticket !== serial) return;
        check(detail.sprint); if (detail.sprint.sprint_id !== selectedId || detail.goal?.sprint_id !== selectedId) throw Error(t('pm_scope_mismatch'));
        fixed = Object.fromEntries(await Promise.all(['product_goal', 'dod', 'backlog'].map(async kind => {
          const id = detail.sprint.definition[kind + '_revision_id'], result = await api('GET', `${endpoint}/sprint-artifacts/${kind}/${id}`); alive();
          check(result.artifact); if (result.artifact.revision_id !== id || result.artifact.kind !== kind) throw Error(t('pm_scope_mismatch'));
          return [kind, result.artifact];
        })));
      }
      const allBindings = [...bindings, ...(fixed.backlog?.document.items || [])], projectIds = [...new Set(allBindings.map(item => item.project_id))];
      const names = new Map();
      await Promise.all(projectIds.map(async id => {
        try {
          const result = await api('GET', `/projects/${encodeURIComponent(id)}`); alive(); if (result.project?.project_id !== id) return;
          const walk = items => {for (const item of items || []) {names.set(item.work_item_id, item.title); walk(item.children);}}; walk(result.work_items);
        } catch { /* Missing current titles never change the fixed binding. */ }
      }));
      alive(); if (ticket !== serial) return;
      const nextSignature = JSON.stringify([artifacts, accepted.head.accepted_revision_id, bindings.map(item => item.binding_id),
        selectedId, detail?.sprint.version, detail?.timebox_elapsed, workspace()?.version]);
      const changed = signature && signature !== nextSignature;
      titles = names; model = {artifacts, accepted, bindings, sprints, detail, fixed}; loaded = true; editors.hidden = false;
      choose.replaceChildren(...(sprints.length ? sprints.map(item => h('option', {value: item.sprint_id}, `${item.definition.title} · ${t('pm_sprint_' + item.status)}`))
        : [h('option', {value: ''}, t('pm_sprint_empty'))])); choose.value = selectedId; renderView();
      if (force || !signature || changed && !dirty && !forms.some(editor => editor.locked())) renderEditors();
      else if (changed) stale = true;
      signature = nextSignature; update();
      if (focused?.isConnected && !focused.disabled && document.activeElement === document.body) {
        focused.focus({preventScroll: true}); if (selection) focused.setSelectionRange(...selection);
      }
    } catch (error) {if (ticket === serial) unavailable(error);}
  }
  return {box, refresh, update};
}
