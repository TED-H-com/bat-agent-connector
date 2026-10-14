import {statusPill} from './ui/components.js';

const ACTION = 'pm.governance.revise';
const findingFields = ['title', 'description', 'owner_actor', 'affected_requirement_ids', 'trigger', 'response',
  'review_at', 'dimension', 'alternatives', 'disambiguation'];

// Resolution revises the original sourced finding. It neither rebases its
// snapshot nor establishes requirement acceptance.
export function issueResolution({h, t, api, alive, caps, workspaceId, thread, permitted, form, input, labelled, errorBox, refreshParent}) {
  const base = `/pm/workspaces/${encodeURIComponent(workspaceId)}`;
  const box = h('div', {'data-proposal-resolution': ''}), entries = new Map();
  let serial = 0, current = null, workspace = null, enabled = false;
  const scope = () => caps()?.scopes?.includes('observe') && caps()?.scopes?.includes('manage')
    && caps()?.actions?.some(row => row.action === ACTION && row.allowed === true) && permitted(false);
  const role = () => Array.isArray(workspace?.members) ? workspace.members.find(row => row.actor === caps()?.actor)?.role
    : workspace?.membership?.actor === caps()?.actor ? workspace.membership.role : null;
  const authorized = entry => enabled && Boolean(entry.value) && scope() && ['owner', 'contributor'].includes(role())
    && (role() === 'owner' || entry.value?.revision.document.owner_actor === caps()?.actor);
  const open = entry => ['open', 'monitoring'].includes(entry.value?.revision.document.status);
  const baseline = entry => JSON.parse(entry.editor?.values().finding || 'null');
  const fresh = entry => baseline(entry)?.version === entry.value?.record.version;
  function checked(value, id) {
    if (value?.workspace_id !== workspaceId || value.record?.workspace_id !== workspaceId || value.record.record_id !== id
      || value.revision?.record_id !== id || value.revision.revision_id !== value.record.revision_id
      || value.revision.version !== value.record.version || !Number.isSafeInteger(value.record.version)
      || !Array.isArray(value.revision.document?.sources)) throw Error(t('pm_scope_mismatch'));
    return value;
  }
  function params(document) {
    return {...Object.fromEntries(findingFields.filter(key => Object.hasOwn(document, key)).map(key => [key, document[key]])),
      source_refs: document.sources.map(({kind, id}) => ({kind, id})),
      proposed_revision_id: document.proposed_revision?.revision_id ?? null, status: 'reported_resolved'};
  }
  function makeEntry(value, plan) {
    const id = value.record.record_id, entry = {value, editor: null};
    const finding = h('input', {type: 'hidden', value: JSON.stringify({version: value.record.version, params: params(value.revision.document)})});
    const rationale = input('pcp_resolve_rationale', {multiline: true, max: 4000});
    const extra = h('div', {}, h('strong', {'data-issue-resolve-title': ''}, value.revision.document.title), finding,
      labelled('pcp_resolve_rationale', rationale));
    entry.editor = form({action: ACTION, target: {workspace_id: workspaceId, record_id: id}, fields: {finding, rationale},
      params: values => ({...JSON.parse(values.finding).params, rationale: values.rationale}),
      preconditions: () => ({expected_version: baseline(entry).version}),
      permitted: () => authorized(entry) && open(entry) && fresh(entry), recoverPermitted: () => authorized(entry),
      label: 'pcp_resolve_issue', extra,
      onStateChange: () => {
        if (!entry.node?.isConnected || !authorized(entry)) return;
        if (!entry.editor.locked() && entry.value.revision.document.status === 'reported_resolved') showResolved(entry);
        else controls(entry);
      },
      onSaved: async result => {
        if (result?.workspace_id !== workspaceId || result.record_id !== id) throw Error(t('pm_scope_mismatch'));
        await refresh(current);
        await api('GET', base + '/overview'); alive();
        await refreshParent?.();
      }});
    const defaultRationale = t('pcp_resolve_default', {title: plan.document.title, number: plan.sequence});
    if (!entry.editor.locked() && !rationale.value) entry.editor.set('rationale', defaultRationale);
    // The prefilled draft is not an instruction to send a resolution.
    entry.editor.box.hidden = !entry.editor.locked() && rationale.value === defaultRationale;
    entry.launch = h('button', {type: 'button', class: 'ui-button secondary ui-sm', 'data-issue-resolve-open': '', onclick: () => {
      if (!authorized(entry) || !open(entry)) return;
      entry.editor.box.hidden = false; entry.launch.hidden = true; rationale.focus();
    }}, t('pcp_resolve_issue'));
    entry.review = h('button', {type: 'button', class: 'ui-button secondary ui-sm', hidden: true, onclick: () => {
      if (!authorized(entry) || entry.editor.locked()) return;
      entry.editor.set('finding', JSON.stringify({version: entry.value.record.version, params: params(entry.value.revision.document)}));
      entry.editor.update(); render();
    }}, t('pm_gov_review_fresh'));
    entry.warning = h('p', {class: 'muted', hidden: true}, t('pm_gov_stale'));
    entry.node = h('section', {'data-issue-resolution': id}, entry.launch, entry.warning, entry.review, entry.editor.box);
    return entry;
  }
  function showResolved(entry) {
    box.replaceChildren(h('p', {'data-issue-resolved': entry.value.record.record_id}, t('reg_status_reported_resolved'), ' · ', statusPill('not_assessed')));
  }
  function controls(entry) {
    entry.warning.hidden = fresh(entry) || entry.editor.locked();
    entry.review.hidden = entry.warning.hidden;
    if (entry.editor.locked()) entry.editor.box.hidden = false;
    entry.launch.hidden = !entry.editor.box.hidden;
  }
  function render() {
    for (const entry of entries.values()) entry.editor.update();
    const entry = [...entries.values()].find(row => authorized(row) && row.editor.locked())
      || [...entries.values()].find(row => authorized(row) && open(row))
      || [...entries.values()].find(row => authorized(row) && row.value?.revision.document.status === 'reported_resolved');
    box.replaceChildren();
    if (!entry) return;
    if (!open(entry) && !entry.editor.locked()) {
      showResolved(entry);
      return;
    }
    controls(entry);
    box.append(entry.node);
  }
  async function refresh(revision) {
    current = revision;
    const ticket = ++serial;
    enabled = false; workspace = null; box.replaceChildren();
    for (const entry of entries.values()) {entry.value = null; entry.editor.update();}
    const refs = (thread()?.thread.context || []).filter(ref => ref.kind === 'governance' && ref.available !== false && /^pmgov_[0-9a-f]{32}$/.test(ref.id));
    if (!scope() || !/^pmrev_[0-9a-f]{32}$/.test(revision || '') || !refs.length) return;
    try {
      const [ws, plan, ...records] = await Promise.all([api('GET', base), api('GET', base + '/plans/' + revision),
        ...refs.map(async ref => {
          try {return checked(await api('GET', base + '/governance/' + ref.id), ref.id);}
          catch (error) {if ([403, 404].includes(error.status)) return null; throw error;}
        })]);
      alive(); if (ticket !== serial) return;
      if (ws.workspace?.workspace_id !== workspaceId || plan.plan?.workspace_id !== workspaceId || plan.plan.revision_id !== revision
        || typeof plan.plan.document?.title !== 'string' || !Number.isSafeInteger(plan.plan.sequence)) throw Error(t('pm_scope_mismatch'));
      workspace = ws.workspace; enabled = true;
      for (const value of records) {
        if (!value || value.record.kind !== 'issue') continue;
        let entry = entries.get(value.record.record_id);
        if (!entry) {entry = makeEntry(value, plan.plan); entries.set(value.record.record_id, entry);}
        entry.value = value;
      }
      render();
    } catch (error) {
      try {alive(); if (ticket !== serial) return;} catch {return;}
      enabled = false; workspace = null; box.replaceChildren();
      if (![403, 404].includes(error.status)) box.append(errorBox(error));
    }
  }
  function withdraw() {
    serial++; enabled = false; workspace = null; box.replaceChildren();
    for (const entry of entries.values()) {entry.value = null; entry.editor.update();}
  }
  return {box, refresh, withdraw, dispose() {withdraw(); for (const entry of entries.values()) entry.editor.dispose(); entries.clear();}};
}
