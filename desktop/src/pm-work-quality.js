import {button} from './ui/components.js';
import {workspaceQualityReviews} from './pm-quality-reviews.js';

// Open the existing review editor against one centrally identified fixed Result.
// This entry never starts an analysis, verifier or review when merely rendered.
export function workQualityContext(envelope, workspaceId, attempt) {
  const id = envelope?.identity, commit = envelope?.changes?.fixed_result_commit?.commit_sha;
  if (envelope?.contract !== 'pm-work-attempt-result/v1' || envelope.workspace_id !== workspaceId
      || envelope.attempt_id !== attempt?.attempt_id || id?.workspace_id !== workspaceId
      || id.attempt_id !== attempt.attempt_id || id.work_item_id !== attempt.work_item_id
      || !/^wi_[0-9a-f]{20}$/.test(id.work_item_id) || !/^pmwp_[0-9a-f]{32}$/.test(id.package_id)
      || !/^op_[0-9a-f]{32}$/.test(id.execution_operation_id) || !/^[0-9a-f]{40}$/.test(commit)) return null;
  return {attempt_id: envelope.attempt_id, work_item_id: id.work_item_id, package_id: id.package_id,
    execution_operation_id: id.execution_operation_id, candidate_commit: commit};
}

export function createWorkQuality({h, t, api, alive, workspaceId, caps, form, errorBox, namespace, refreshResult}) {
  let context = null, envelope = null, editor = null, workspace = null, serial = 0, sourceSignature = null, saving = 0;
  const body = h('div'), status = h('div', {role: 'status'});
  const open = button({variant: 'secondary', size: 'sm', label: t('pmq_result_open'), onClick: () => void load()});
  const element = h('section', {'data-work-quality': ''}, open, status, body);
  const getCaps = () => typeof caps === 'function' ? caps() : caps;
  const offered = (action, target) => Boolean(envelope?.next_allowed_actions?.some(row => row.kind === 'action'
    && row.action === action && row.target?.workspace_id === workspaceId
    && (action === 'pm.quality.review.create' ? row.target.work_item_id === target.work_item_id
      : action === 'pm.execution.verify' ? row.target.execution_operation_id === target.execution_operation_id
      : action === 'pm.quality.review.record' && row.target.review_id === target.review_id)));
  function dispose() {
    serial++; editor?.withdraw(); editor = null; workspace = null; context = envelope = null;
    sourceSignature = null;
    body.replaceChildren(); status.replaceChildren(); open.disabled = false; open.hidden = false;
  }
  async function load() {
    if (!context || editor || open.disabled) return;
    const ticket = ++serial, selected = context;
    const valid = () => {try {alive?.(); return ticket === serial && context === selected;} catch {return false;}};
    open.disabled = true; status.textContent = t('pmq_loading');
    try {
      const data = await api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}`);
      if (!valid()) return;
      if (data.workspace?.workspace_id !== workspaceId) throw Error(t('pmq_invalid'));
      workspace = data.workspace;
      const input = (key, {multiline = false, max = 4000, required = true, value = ''} = {}) =>
        h(multiline ? 'textarea' : 'input', {'aria-label': t(key), maxlength: max, required, value});
      editor = workspaceQualityReviews({h, t, api, alive: () => {if (!valid()) throw Error('VIEW_CHANGED');},
        workspaceId, workspace: () => workspace, caps: getCaps, permitted: () => Boolean(envelope),
        form, input, labelled: (key, field) => h('label', {}, t(key), field), errorBox,
        namespace: `${namespace}.result.${envelope.attempt_id}.${selected.candidate_commit}`,
        fixedResult: selected, allowedAction: offered, onSaved: async () => {
          saving++;
          try {await refreshResult();} finally {saving--;}
          if (!valid()) throw Error('VIEW_CHANGED');
        }});
      body.replaceChildren(editor.element); status.replaceChildren(); open.hidden = true;
      const heading = editor.element.querySelector('h2'); heading.tabIndex = -1; heading.focus({preventScroll: true});
      editor.setWork(selected.work_item_id);
    } catch (err) {
      if (!valid()) return;
      open.disabled = false; status.replaceChildren(errorBox(err, {within: status}));
    }
  }
  function render(value, attempt) {
    const next = workQualityContext(value, workspaceId, attempt);
    if (!form || getCaps()?.features?.pm_workspaces?.quality_reviews?.version !== 1 || !next) {dispose(); return null;}
    if (JSON.stringify(next) !== JSON.stringify(context)) {dispose(); context = next;}
    const signature = JSON.stringify([value.verification, value.next_allowed_actions?.filter(row => row.action?.startsWith('pm.quality.') || row.action === 'pm.execution.verify'),
      getCaps()?.actor, getCaps()?.scopes]);
    const sourcesChanged = signature !== sourceSignature; sourceSignature = signature;
    envelope = value;
    // Existing readers may inspect the fixed review without acquiring write rights.
    if (editor && !saving) {
      if (sourcesChanged) void editor.refresh();
      else editor.refreshDiscussionSources();
    }
    return element;
  }
  return {render, dispose, recheck: () => {if (editor && !saving) editor.refreshDiscussionSources();}};
}
