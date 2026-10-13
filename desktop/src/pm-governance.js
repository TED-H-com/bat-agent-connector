// Sourced management findings and explicit direction decisions share one journal.
export function workspaceGovernance({h, t, api, alive, namespace, workspaceId, workspace, permitted, form, input, labelled, errorBox, caps}) {
  const endpoint = `/pm/workspaces/${encodeURIComponent(workspaceId)}`, kinds = ['risk', 'assumption', 'issue', 'dependency', 'drift', 'change'];
  let serial = 0, loaded = false, dirty = false, stale = false, signature = '', selectedId = '', model = null, forms = [], widgets = [];
  let pageCount = 1, sourceTicket = 0, createSnapshot = null, createEditor = null, refreshCreateSource = null;
  const navigationKey = `batc.pm.governance.selection.${namespace}.${workspaceId}.${caps()?.actor}`;
  try {const saved = sessionStorage.getItem(navigationKey); if (/^pmgov_[0-9a-f]{32}$/.test(saved || '')) selectedId = saved;} catch { /* Navigation is optional. */ }
  const selectRecord = value => {selectedId = value; try {alive(); sessionStorage.setItem(navigationKey, value);} catch { /* Form persistence remains authoritative. */ }};
  const status = h('div', {role: 'status'}), list = h('div', {class: 'pm-evidence-rows'}), detail = h('div'), editors = h('div');
  const warning = h('p', {class: 'muted', hidden: true}, t('pm_gov_stale'));
  const review = h('button', {class: 'secondary', type: 'button', hidden: true, onclick: () => {if (loaded) void renderEditors(true);}}, t('pm_gov_review_fresh'));
  const element = h('section', {class: 'panel pm-evidence', 'data-pm-governance': ''},
    h('header', {class: 'pm-heading'}, h('div', {}, h('h2', {}, t('pm_gov_title')), h('p', {class: 'muted'}, t('pm_gov_intro'))),
      h('button', {class: 'secondary', type: 'button', onclick: () => void refresh()}, t('pm_gov_refresh'))),
    status, list, detail, warning, review, editors);
  const actor = () => caps()?.actor;
  const can = () => loaded && permitted(false);
  const owner = () => {
    const ws = workspace(), role = Array.isArray(ws?.members) ? ws.members.find(member => member.actor === actor())?.role : ws?.membership?.role;
    return can() && role === 'owner';
  };
  const check = value => {if (value?.workspace_id !== workspaceId) throw Error(t('pm_scope_mismatch')); return value;};
  const id = value => h('code', {class: 'pm-source'}, value || t('pm_gov_unknown'));
  const note = key => h('p', {class: 'muted pm-note-text'}, t(key));
  const fold = (key, content, open = false) => h('details', {open}, h('summary', {}, t(key)), content);
  const line = (key, value) => h('section', {class: 'pm-brief-row'}, h('h3', {}, t(key)), h('div', {class: 'pm-brief-values'}, value));
  const text = value => h('p', {class: 'pm-note-text'}, value || t('pm_gov_unknown'));
  const hidden = value => {const node = h('input', {type: 'hidden', value: JSON.stringify(value)}); node.dataset.pmDefault = JSON.stringify(value); return node;};
  const select = (key, choices, value = '') => {
    const node = h('select', {'aria-label': t(key)}, ...choices.map(([value, label]) => h('option', {value}, label)));
    node.value = value; node.dataset.pmDefault = value; return node;
  };
  const reason = value => input('pm_gov_reason', {multiline: true, max: 4000, value: value || ''});
  const target = () => ({workspace_id: workspaceId, record_id: selectedId});
  const invalid = () => {throw Error(t('pm_scope_mismatch'));};
  function checkedEntry(entry, expectedId) {
    check(entry.record);
    if (expectedId && entry.record.record_id !== expectedId || entry.revision?.record_id !== entry.record.record_id
      || entry.revision?.revision_id !== entry.record.revision_id || entry.revision?.version !== entry.record.version) invalid();
    return entry;
  }
  function update() {
    forms.forEach(editor => editor.update()); widgets.forEach(widget => widget());
    warning.hidden = !stale; review.hidden = !stale; review.disabled = !loaded;
  }
  function unavailable(error) {
    loaded = false; ++sourceTicket; list.replaceChildren(); detail.replaceChildren(); editors.hidden = true;
    update(); status.replaceChildren(errorBox(error));
  }
  async function currentWorkspace() {
    const value = await api('GET', endpoint); alive(); check(value.workspace);
  }
  function clearEditors() {
    ++sourceTicket; forms = []; widgets = []; createSnapshot = null; createEditor = null; refreshCreateSource = null;
    editors.replaceChildren();
  }
  function localForm(options, rebase = false) {
    const expected = JSON.stringify({preconditions: options.preconditions(), context: options.context});
    const baseline = h('input', {type: 'hidden', value: expected}); baseline.dataset.pmDefault = expected;
    let editor;
    editor = form({...options, fields: {...options.fields, _baseline: baseline}, extra: h('div', {}, options.extra, baseline),
      params: values => {
        if (options.valid && !options.valid()) throw Error(t('pm_gov_check_sources'));
        return options.params(Object.fromEntries(Object.entries(values).filter(([key]) => key !== '_baseline')));
      },
      onStateChange: () => {
        options.onStateChange?.();
        if (editor && !editor.locked() && options.valid && !options.valid()) editor.box.querySelector('button[type=submit]').disabled = true;
      },
      preconditions: () => JSON.parse(baseline.value).preconditions,
      permitted: () => !stale && options.permitted(), recoverPermitted: can,
      onSaved: async result => {
        if (result?.workspace_id !== workspaceId || !/^pmgov_[0-9a-f]{32}$/.test(result.record_id)) invalid();
        selectRecord(result.record_id);
        await refresh(true);
        if (result.deduplicated) status.replaceChildren(note('pm_gov_deduplicated'));
      }});
    if (rebase && !editor.locked()) editor.set('_baseline', expected);
    if (!editor.locked() && baseline.value !== expected) stale = true;
    editor.box.addEventListener('input', () => {dirty = true;}); editor.box.addEventListener('change', () => {dirty = true; update();});
    forms.push(editor); return editor;
  }
  function sourceLabel(kind, source) {
    const words = source.value || source.text || source.title || source.description || source.summary || source.source_kind || source.work_item_id;
    const state = source.status || source.verification_status;
    const stateKey = 'pm_gov_info_' + state, label = state ? t(stateKey) : '';
    return [t('pm_gov_source_' + kind), typeof words === 'string' ? words : '', label === stateKey ? state : label].filter(Boolean).join(' · ');
  }
  function sourceChoices(snapshot) {
    const baseline = snapshot?.baseline?.document;
    if (!baseline) return [];
    return [...baseline.selected_inputs.map(row => ({kind: 'plan_input', id: row.input_id, source: row})),
      ...baseline.requirements.map(row => ({kind: 'requirement', id: row.id, source: row})),
      ...snapshot.evidence.map(row => ({kind: 'evidence', id: row.evidence_id, source: row})),
      ...snapshot.work.map(row => ({kind: 'work_binding', id: row.binding.binding_id, source: row.binding}))];
  }
  const key = ref => `${ref.kind}:${ref.id}`;
  function refsWidget(field, holder, snapshot, getEditor, original = []) {
    const choices = sourceChoices(snapshot), saved = JSON.parse(field.value || '[]'), required = new Set(original.map(key));
    for (const ref of saved) if (!choices.some(choice => key(choice) === key(ref))) choices.push({...ref, missing: true});
    const controls = choices.map(choice => {
      const checkbox = h('input', {type: 'checkbox'});
      const label = choice.missing ? t('pm_gov_source_missing') : sourceLabel(choice.kind, choice.source);
      checkbox.addEventListener('change', () => {
        if (!can() || stale || getEditor().locked() || required.has(key(choice))) return;
        const refs = JSON.parse(field.value || '[]').filter(ref => key(ref) !== key(choice));
        if (checkbox.checked) refs.push({kind: choice.kind, id: choice.id});
        getEditor().set('source_refs', JSON.stringify(refs)); dirty = true; update();
      });
      return {choice, checkbox, element: h('label', {class: 'pm-fact-heading'}, checkbox, h('span', {class: 'pm-source'}, label, ' · ', id(choice.id)))};
    });
    const sync = () => {
      const selected = new Set(JSON.parse(field.value || '[]').map(key));
      for (const {choice, checkbox} of controls) {checkbox.checked = selected.has(key(choice)); checkbox.disabled = !can() || stale || getEditor().locked() || getEditor().fields.title.disabled || required.has(key(choice));}
    };
    holder.replaceChildren(note('pm_gov_source_help'), ...controls.map(row => row.element)); sync(); return sync;
  }
  function documentView(entry) {
    const d = entry.document;
    return h('div', {}, text(d.description), line('pm_gov_owner', text(d.owner_actor)),
      line('pm_gov_response', h('div', {}, text(d.response), d.trigger ? text(t('pm_gov_trigger') + ': ' + d.trigger) : note('pm_gov_trigger_unknown'),
        text(t('pm_gov_review_at') + ': ' + (d.review_at || t('pm_gov_unscheduled'))))),
      d.dimension ? line('pm_gov_dimension', text(t('pm_gov_' + d.dimension))) : '',
      line('pm_gov_assessment', h('div', {}, note('pm_gov_unassessed'), note(d.assessment_basis === 'attributed_assumption' ? 'pm_gov_assumption_basis' : 'pm_gov_finding_basis'))),
      d.affected_requirement_ids.length ? line('pm_gov_affected', text(d.affected_requirement_ids.join(', '))) : '',
      d.alternatives.length ? line('pm_gov_alternatives', h('ul', {}, ...d.alternatives.map(value => h('li', {class: 'pm-note-text'}, value)))) : '',
      d.decision ? line('pm_gov_decision', h('div', {}, text(t('pm_gov_' + d.decision.disposition)), text(d.decision.rationale),
        text(d.decision.recorded_by), note('pm_gov_direction_only'))) : '',
      d.adoption ? line('pm_gov_adoption', h('div', {}, note('pm_gov_adoption_historical'), id(d.adoption.revision_id),
        h('p', {}, h('a', {href: `#/op/${d.adoption.operation_id}`}, t('pm_operation'))), text(d.adoption.adopted_by))) : '',
      d.proposed_revision ? fold('pm_gov_proposed', h('div', {}, id(d.proposed_revision.revision_id), text(d.proposed_revision.document_digest), note('pm_gov_direction_only'))) : '',
      fold('pm_gov_sources', h('div', {}, ...d.sources.map(ref => h('article', {class: 'pm-fact'}, text(sourceLabel(ref.kind, ref.source)), id(ref.id),
        fold('pm_gov_exact_source', h('pre', {class: 'pm-brief-proof'}, JSON.stringify(ref.source, null, 2))))))),
      d.disambiguation ? fold('pm_gov_distinction', h('div', {}, text(d.disambiguation.rationale), ...d.disambiguation.record_ids.map(id))) : '',
      line('pm_gov_reason', text(d.rationale)));
  }
  function historyView(record) {
    const holder = h('div'), container = fold('pm_gov_history', holder); let cursor = 0, started = false, busy = false;
    const load = async () => {
      if (busy) return; busy = true;
      try {
        const page = check(await api('GET', `${endpoint}/governance/${record.record_id}/history?after=${cursor}&limit=20`)); alive();
        if (!container.isConnected) return;
        if (page.record_id !== record.record_id || JSON.stringify(page.context) !== JSON.stringify(record.context)
          || !Array.isArray(page.revisions) || page.next_cursor != null && (!Number.isSafeInteger(page.next_cursor) || page.next_cursor <= cursor)) invalid();
        let last = cursor;
        for (const row of page.revisions) {
          if (row.record_id !== record.record_id || !Number.isSafeInteger(row.sequence) || row.sequence <= last) invalid(); last = row.sequence;
          holder.append(h('details', {}, h('summary', {}, `#${row.version} · ${row.actor} · ${t('pm_gov_action_' + row.action.split('.').slice(2).join('_'))}`), documentView(row),
            h('a', {href: `#/op/${row.operation_id}`}, t('pm_operation'))));
        }
        more.remove(); cursor = page.next_cursor;
        if (cursor != null) holder.append(more);
      } catch (error) {if (container.isConnected) unavailable(error);} finally {busy = false; more.disabled = false;}
    };
    const more = h('button', {type: 'button', class: 'secondary', onclick: () => {more.disabled = true; void load();}}, t('pm_gov_more_history'));
    container.addEventListener('toggle', () => {if (container.open && !started) {started = true; void load();}}); return container;
  }
  function renderRead() {
    const ordered = [...model.records].sort((a, b) => {
      const score = row => (row.revision.document.status === 'reported_resolved' ? 2 : row.revision.document.status === 'monitoring' ? 1 : 0);
      return score(a) - score(b) || (a.record.kind === 'change' ? -1 : 0) - (b.record.kind === 'change' ? -1 : 0) || a.record.sequence - b.record.sequence;
    });
    list.replaceChildren(...(ordered.length ? ordered.map(({record, revision}) => h('article', {class: 'pm-fact', 'data-pm-governance-row': record.record_id},
      h('div', {class: 'pm-fact-heading'}, h('strong', {class: 'pm-source'}, revision.document.title), h('span', {class: 'muted'}, t('pm_gov_' + record.kind))),
      h('p', {class: 'muted'}, t('pm_gov_' + revision.document.status), ' · ', revision.document.owner_actor,
        revision.document.decision ? ' · ' + t('pm_gov_' + revision.document.decision.disposition) : ''),
      h('button', {type: 'button', class: 'secondary', 'aria-pressed': String(selectedId === record.record_id), onclick: () => {selectRecord(record.record_id); void refresh(true);}},
        t(selectedId === record.record_id ? 'pm_gov_selected' : 'pm_gov_inspect')))) : [note('pm_gov_empty')]));
    if (model.nextCursor != null) list.append(note('pm_gov_partial'), h('button', {type: 'button', class: 'secondary', onclick: () => {pageCount++; void refresh();}}, t('pm_gov_more')));
    detail.replaceChildren();
    if (!model.detail) return;
    const {record, revision} = model.detail;
    detail.append(h('section', {'data-pm-governance-detail': record.record_id}, h('h3', {class: 'pm-source'}, revision.document.title), documentView(revision),
      fold('pm_gov_baseline', h('div', {}, id(record.context.plan_revision_id), text(record.context.plan_document_digest), id(record.context.snapshot_ref.snapshot_id),
        text(record.context.snapshot_ref.as_of), text(t('pm_gov_root') + ': ' + record.root_cause_key))), historyView(record)));
  }
  function findingEditor(existing, rebase) {
    const doc = existing?.revision.document, creating = !existing, snapshot = creating ? null : model.fixed;
    const fields = {
      title: input('pm_gov_name', {max: 200, value: doc?.title || ''}), description: input('pm_gov_description', {multiline: true, max: 8000, value: doc?.description || ''}),
      owner_actor: input('pm_gov_owner', {max: 200, value: doc?.owner_actor || actor()}), rationale: reason(),
      source_refs: hidden(doc?.sources.map(({kind, id}) => ({kind, id})) || []),
      status: select('pm_gov_status', ['open', 'monitoring', 'reported_resolved'].map(value => [value, t('pm_gov_' + value)]), doc?.status || 'open'),
      affected_requirement_ids: input('pm_gov_affected_ids', {required: false, value: doc?.affected_requirement_ids.join(', ') || ''}),
      trigger: input('pm_gov_trigger', {multiline: true, max: 4000, required: false, value: doc?.trigger || ''}),
      response: input('pm_gov_response', {multiline: true, max: 4000, required: false, value: doc?.response || ''}),
      review_at: input('pm_gov_review_time', {required: false, value: doc?.review_at || ''}),
      dimension: select('pm_gov_dimension', [['', t('pm_gov_unknown')], ...['scope', 'schedule', 'quality', 'resources'].map(value => [value, t('pm_gov_' + value)])], doc?.dimension || ''),
      alternatives: input('pm_gov_alternatives_lines', {multiline: true, max: 20009, required: false, value: doc?.alternatives.join('\n') || ''}),
      proposed_revision_id: input('pm_gov_proposed_id', {required: false, value: doc?.proposed_revision?.revision_id || ''}),
      related_ids: input('pm_gov_related_ids', {required: false, value: doc?.disambiguation?.record_ids.join(', ') || ''}),
      distinction: input('pm_gov_distinction_reason', {multiline: true, required: false, value: doc?.disambiguation?.rationale || ''}),
    };
    if (creating) {
      fields.kind = select('pm_gov_kind', kinds.map(value => [value, t('pm_gov_' + value)]), 'risk');
      fields.root_cause_key = input('pm_gov_root', {max: 120});
      fields.snapshot_id = select('pm_gov_snapshot', model.snapshots.map(row => [row.snapshot_id, row.title || row.snapshot_id]), model.snapshots.at(-1)?.snapshot_id || '');
    }
    const sourceHolder = h('div'), sourceStatus = h('div', {role: 'status'}), proofStatus = h('div', {role: 'status'}), driftHint = note('pm_gov_drift_help');
    let editor, sourceSync = () => {}, proof = doc?.proposed_revision?.revision_id || '', proofSerial = 0;
    const allowed = () => can() && (creating || owner() || doc.owner_actor === actor());
    const inspect = h('button', {type: 'button', class: 'secondary', onclick: async () => {
      const candidate = fields.proposed_revision_id.value.trim(), ticket = ++proofSerial; proof = ''; inspect.disabled = true;
      try {
        if (!/^pmrev_[0-9a-f]{32}$/.test(candidate)) throw Error(t('pm_gov_invalid_id'));
        const value = await api('GET', `${endpoint}/plans/${candidate}`); alive();
        if (!editor.box.isConnected || ticket !== proofSerial || fields.proposed_revision_id.value.trim() !== candidate) return;
        check(value.plan); if (value.plan.revision_id !== candidate) invalid();
        proof = candidate; proofStatus.replaceChildren(text(value.plan.document?.title || candidate), text(value.plan.document_digest), note('pm_gov_direction_only'));
      } catch (error) {if (editor.box.isConnected) proofStatus.replaceChildren(errorBox(error));} finally {update();}
    }}, t('pm_gov_inspect_plan'));
    fields.proposed_revision_id.addEventListener('input', () => {proof = ''; ++proofSerial; proofStatus.replaceChildren(); update();});
    const extra = h('div', {class: 'pm-fields'}, creating ? h('div', {class: 'capture-fields'}, labelled('pm_gov_kind', fields.kind), labelled('pm_gov_snapshot', fields.snapshot_id)) : '',
      creating ? labelled('pm_gov_root', fields.root_cause_key) : note('pm_gov_revision_fixed'), labelled('pm_gov_name', fields.title), labelled('pm_gov_description', fields.description),
      h('div', {class: 'capture-fields'}, labelled('pm_gov_owner', fields.owner_actor), labelled('pm_gov_status', fields.status)),
      driftHint, fold('pm_gov_sources', h('div', {}, sourceStatus, sourceHolder), true),
      fold('pm_gov_response_fields', h('div', {class: 'pm-fields'}, labelled('pm_gov_trigger', fields.trigger), labelled('pm_gov_response', fields.response),
        labelled('pm_gov_review_time', fields.review_at), note('pm_gov_no_schedule'), labelled('pm_gov_affected_ids', fields.affected_requirement_ids), labelled('pm_gov_dimension', fields.dimension))),
      fold('pm_gov_options', h('div', {class: 'pm-fields'}, labelled('pm_gov_alternatives_lines', fields.alternatives), labelled('pm_gov_proposed_id', fields.proposed_revision_id), inspect, proofStatus,
        note('pm_gov_proposed_help'), labelled('pm_gov_related_ids', fields.related_ids), labelled('pm_gov_distinction_reason', fields.distinction))), labelled('pm_gov_reason', fields.rationale));
    editor = localForm({action: 'pm.governance.' + (creating ? 'record' : 'revise'), target: creating ? {workspace_id: workspaceId} : target(), fields,
      context: creating ? 'create' : existing.record.context_digest, extra, label: creating ? 'pm_gov_save' : 'pm_gov_save_revision',
      preconditions: () => creating ? {expected_plan_version: model.head.version} : {expected_version: existing.record.version},
      permitted: allowed,
      valid: () => {
        const chosen = JSON.parse(fields.source_refs.value), fixed = creating ? createSnapshot : snapshot, kind = creating ? fields.kind.value : existing.record.kind;
        return Boolean(fixed && (!creating || fixed.snapshot_id === fields.snapshot_id.value))
          && (!fields.proposed_revision_id.value.trim() || kind === 'change' && proof === fields.proposed_revision_id.value.trim())
          && (kind !== 'drift' || fields.dimension.value && chosen.some(ref => ref.kind === 'evidence') && chosen.some(ref => ['plan_input', 'requirement'].includes(ref.kind)))
          && chosen.length > 0 && chosen.length <= 50 && chosen.every(ref => sourceChoices(fixed).some(choice => key(choice) === key(ref)));
      },
      onStateChange: () => {sourceSync(); inspect.disabled = !allowed() || stale || Boolean(editor?.locked()); driftHint.hidden = (creating ? fields.kind.value : existing.record.kind) !== 'drift';},
      params: values => {
        const split = value => value.split(',').map(part => part.trim()).filter(Boolean);
        const result = {...values, source_refs: JSON.parse(values.source_refs), affected_requirement_ids: split(values.affected_requirement_ids),
          alternatives: values.alternatives.split('\n').map(value => value.trim()).filter(Boolean),
          trigger: values.trigger.trim() || null, response: values.response.trim() || null, review_at: values.review_at.trim() || null,
          dimension: values.dimension || null, proposed_revision_id: values.proposed_revision_id.trim() || null};
        delete result.related_ids; delete result.distinction;
        if (values.related_ids.trim() || values.distinction.trim()) result.disambiguation = {record_ids: split(values.related_ids), rationale: values.distinction};
        return result;
      }}, rebase);
    if (creating) {
      createEditor = editor;
      const readSource = async () => {
        const snapshotId = fields.snapshot_id.value, ticket = ++sourceTicket; createSnapshot = null; sourceHolder.replaceChildren(); sourceStatus.replaceChildren(); update();
        if (!snapshotId) {sourceStatus.append(note('pm_gov_no_snapshot')); return;}
        try {
          const result = await api('GET', `${endpoint}/snapshots/${encodeURIComponent(snapshotId)}`); alive();
          if (ticket !== sourceTicket || !editor.box.isConnected) return;
          check(result.snapshot);
          if (result.snapshot.snapshot_id !== snapshotId) invalid();
          if (result.snapshot.visibility?.filtered) throw Object.assign(Error(t('pm_gov_source_missing')), {status: 404});
          if (!result.snapshot.baseline?.document) {sourceStatus.append(note('pm_gov_no_snapshot')); update(); return;}
          createSnapshot = result.snapshot; sourceSync = refsWidget(fields.source_refs, sourceHolder, createSnapshot, () => editor); update();
        } catch (error) {
          if (ticket !== sourceTicket || !editor.box.isConnected) return;
          if (error.status !== 404) {unavailable(error); return;}
          // One unavailable source must not trap the source selector. Recheck
          // the parent boundary before retaining any other workspace facts.
          try {
            await currentWorkspace();
            if (ticket !== sourceTicket || !editor.box.isConnected) return;
            sourceStatus.replaceChildren(errorBox(error)); update();
          } catch (failure) {if (ticket === sourceTicket && editor.box.isConnected) unavailable(failure);}
        }
      };
      refreshCreateSource = readSource;
      fields.snapshot_id.addEventListener('change', () => {void readSource();});
      widgets.push(() => {sourceSync();});
      return {element: fold('pm_gov_new', editor.box, editor.locked()), mount: readSource};
    }
    sourceSync = refsWidget(fields.source_refs, sourceHolder, snapshot, () => editor, doc.sources); widgets.push(sourceSync);
    return {element: fold('pm_gov_edit', editor.box, editor.locked()), mount: async () => {}};
  }
  function decisionEditors(rebase) {
    const current = model.detail, record = current.record, d = current.revision.document, holder = h('div');
    if (record.kind !== 'change') return holder;
    const rationale = reason(), disposition = select('pm_gov_disposition', ['adopted_direction', 'rejected'].map(value => [value, t('pm_gov_' + value)]), 'adopted_direction');
    const decision = localForm({action: 'pm.governance.decide', target: target(), fields: {disposition, rationale}, context: record.revision_id,
      preconditions: () => ({expected_version: record.version}), permitted: owner, label: 'pm_gov_save_decision', params: values => values,
      extra: h('div', {class: 'pm-fields'}, note('pm_gov_direction_only'), labelled('pm_gov_disposition', disposition), labelled('pm_gov_reason', rationale))}, rebase);
    holder.append(fold('pm_gov_decide', decision.box, decision.locked()));
    const adoption_operation_id = input('pm_gov_operation_id', {max: 35}), adoptionReason = reason(), receipt = h('div', {role: 'status'}); let proof = '', editor, ticket = 0;
    const inspect = h('button', {type: 'button', class: 'secondary', onclick: async () => {
      const value = adoption_operation_id.value.trim(), request = ++ticket; proof = ''; inspect.disabled = true;
      try {
        if (!/^op_[0-9a-f]{32}$/.test(value)) throw Error(t('pm_gov_invalid_id'));
        const {operation} = await api('GET', `/operations/${value}`); alive();
        if (!editor.box.isConnected || request !== ticket || adoption_operation_id.value.trim() !== value) return;
        if (operation?.operation_id !== value || operation.action !== 'pm.plan.adopt' || operation.status !== 'succeeded'
          || operation.target?.workspace_id !== workspaceId || operation.target?.revision_id !== d.proposed_revision?.revision_id
          || operation.result?.previous_revision_id !== record.context.plan_revision_id) throw Error(t('pm_gov_adoption_mismatch'));
        proof = value; receipt.replaceChildren(note('pm_gov_adoption_historical'), id(operation.target.revision_id), h('a', {href: `#/op/${value}`}, t('pm_operation')));
      } catch (error) {if (editor.box.isConnected) receipt.replaceChildren(errorBox(error));} finally {update();}
    }}, t('pm_gov_inspect_adoption'));
    adoption_operation_id.addEventListener('input', () => {proof = ''; ++ticket; receipt.replaceChildren(); update();});
    editor = localForm({action: 'pm.governance.adoption.link', target: target(), fields: {adoption_operation_id, rationale: adoptionReason}, context: record.revision_id,
      preconditions: () => ({expected_version: record.version}), label: 'pm_gov_link_adoption', params: values => values,
      permitted: owner,
      valid: () => d.decision?.disposition === 'adopted_direction' && Boolean(d.proposed_revision) && Boolean(proof) && proof === adoption_operation_id.value.trim(),
      onStateChange: () => {inspect.disabled = !owner() || stale || Boolean(editor?.locked());},
      extra: h('div', {class: 'pm-fields'}, note('pm_gov_link_help'), labelled('pm_gov_operation_id', adoption_operation_id), inspect, receipt, labelled('pm_gov_reason', adoptionReason))}, rebase);
    holder.append(fold('pm_gov_adoption', editor.box, editor.locked())); return holder;
  }
  async function renderEditors(rebase = false) {
    clearEditors(); dirty = false; stale = false;
    if (model.detail) editors.append(findingEditor(model.detail, rebase).element, decisionEditors(rebase));
    const create = findingEditor(null, rebase); editors.append(create.element); update(); await create.mount();
  }
  async function refresh(force = false) {
    const ticket = ++serial, focused = element.contains(document.activeElement) ? document.activeElement : null;
    const selection = focused && typeof focused.selectionStart === 'number' ? [focused.selectionStart, focused.selectionEnd] : null;
    loaded = false; editors.hidden = true; update(); status.replaceChildren();
    try {
      const records = []; let cursor = 0, page;
      for (let count = 0; count < pageCount; count++) {
        page = check(await api('GET', `${endpoint}/governance?after=${cursor}&limit=20`)); alive(); if (ticket !== serial) return;
        if (!Array.isArray(page.records) || page.next_cursor != null && (!Number.isSafeInteger(page.next_cursor) || page.next_cursor <= cursor)) invalid();
        for (const row of page.records) {checkedEntry(row); if (records.some(value => value.record.record_id === row.record.record_id)) invalid(); records.push(row);}
        if (page.next_cursor == null) break; cursor = page.next_cursor;
      }
      const readSnapshots = async () => {
        const snapshots = []; let after = 0;
        for (let count = 0; count < 20; count++) {
          const page = check(await api('GET', `${endpoint}/snapshots?after=${after}&limit=50`)); alive();
          if (!Array.isArray(page.snapshots)) invalid(); page.snapshots.forEach(check); snapshots.push(...page.snapshots);
          if (page.next_cursor == null) return snapshots;
          if (!Number.isSafeInteger(page.next_cursor) || page.next_cursor <= after) invalid(); after = page.next_cursor;
        }
        throw Error(t('pm_gov_snapshot_partial'));
      };
      const [accepted, snapshots] = await Promise.all([api('GET', `${endpoint}/plans/accepted`), readSnapshots()]);
      alive(); if (ticket !== serial) return; check(accepted.head);
      if (!selectedId) selectedId = records.find(row => row.revision.document.status === 'open')?.record.record_id || records[0]?.record.record_id || '';
      let current = null, fixed = null;
      if (selectedId) {
        try {
          current = check(await api('GET', `${endpoint}/governance/${selectedId}`)); alive(); if (ticket !== serial) return;
          checkedEntry(current, selectedId);
          const result = await api('GET', `${endpoint}/snapshots/${current.record.context.snapshot_ref.snapshot_id}`); alive(); if (ticket !== serial) return;
          fixed = check(result.snapshot);
          if (fixed.snapshot_id !== current.record.context.snapshot_ref.snapshot_id || fixed.snapshot_digest !== current.record.context.snapshot_ref.snapshot_digest
            || !fixed.baseline?.document) invalid();
          if (fixed.visibility?.filtered) throw Object.assign(Error(t('pm_gov_source_missing')), {status: 404});
        } catch (error) {
          if (ticket !== serial) return;
          if (error.status !== 404) throw error;
          const withdrawn = selectedId;
          detail.replaceChildren(); list.replaceChildren(); clearEditors();
          await currentWorkspace(); if (ticket !== serial) return;
          // Absence from a bounded list is not withdrawal: only an explicit
          // failed detail/source read clears selection. Durable drafts stay put.
          for (let i = records.length - 1; i >= 0; i--) if (records[i].record.record_id === withdrawn) records.splice(i, 1);
          selectRecord(''); current = null; fixed = null; force = true;
          status.replaceChildren(errorBox(error));
        }
      }
      const next = JSON.stringify([selectedId, current?.record.version, accepted.head.version, workspace()?.version]);
      const changed = signature && signature !== next;
      model = {records, detail: current, fixed, head: accepted.head, snapshots, nextCursor: page.next_cursor};
      loaded = true; renderRead();
      if (force || !signature || changed && !dirty && !forms.some(editor => editor.locked())) await renderEditors();
      else {
        if (changed) stale = true;
        // Reauthorize an unchanged creation source without replacing typed form fields.
        if (createEditor?.fields.snapshot_id.value) {
          await refreshCreateSource(); alive(); if (ticket !== serial) return;
        }
      }
      if (ticket !== serial || !loaded) return; signature = next; editors.hidden = false; update();
      if (focused?.isConnected && !focused.disabled && document.activeElement === document.body) {focused.focus({preventScroll: true}); if (selection) focused.setSelectionRange(...selection);}
    } catch (error) {if (ticket === serial) unavailable(error);}
  }
  return {element, refresh, openRef: async ref => {
    if (ref.kind !== 'governance') return;
    selectRecord(ref.id); await refresh(true);
    const row = list.querySelector(`[data-pm-governance-row="${CSS.escape(ref.id)}"]`);
    row?.querySelector('button')?.focus(); row?.scrollIntoView({block: 'nearest'});
  }};
}
