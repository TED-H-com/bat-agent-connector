import {pmDurableForm} from './pm-workspace.js';

const ACTION = 'pm.work_input.defer';
const OP = /^op_[0-9a-f]{32}$/;
const THREAD = /^pmt_[0-9a-f]{32}$/;

// One stable form outside the refreshing receipt list keeps its draft and focus.
export function mountWorkInputIdea({h, t, api, caps, guard, namespace, workspaceId, workItemId, receipts, permitted, refresh, errorBox}) {
  const box = h('div', {'data-work-input-idea': ''});
  let form = null, selected = '', completed = false, disposed = false;
  const current = () => receipts().find(row => row.operation_id === selected);
  const allowed = () => !disposed && permitted() && caps()?.scopes?.includes('manage')
    && caps()?.actions?.some(row => row.action === ACTION && row.allowed === true);
  function offered(row) {
    return row?.state === 'queued' && row.cancellable === true && !row.deferred_idea
      && row.actor === caps()?.actor && allowed()
      && row.next_allowed_actions?.some(action => action.kind === 'action' && action.action === ACTION
        && Object.keys(action.target || {}).length === 3
        && action.target.workspace_id === workspaceId && action.target.work_item_id === workItemId
        && action.target.source_operation_id === row.operation_id);
  }
  function clear() {
    form?.dispose(); form = null; selected = ''; completed = false;
    box.replaceChildren();
  }
  function open(row) {
    if (!offered(row) || !OP.test(row.operation_id)) return;
    if (selected === row.operation_id && form) {form.fields.reason.focus(); return;}
    clear(); selected = row.operation_id;
    const reason = h('textarea', {id: `idea-reason-${selected}`, rows: 2, maxlength: 4000});
    const result = h('div', {'data-work-idea-result': ''});
    const exactSource = selected;
    form = pmDurableForm({h, t, api, caps, guard, namespace, action: ACTION,
      target: {workspace_id: workspaceId, work_item_id: workItemId, source_operation_id: selected},
      fields: {reason}, params: values => ({reason: values.reason}),
      preconditions: () => ({expected_state: 'queued'}),
      permitted: () => !completed && offered(current()), recoverPermitted: allowed,
      canSend: () => allowed() && Boolean(reason.value.trim()) && reason.value.length <= 4000,
      keepEditable: () => !disposed && !completed,
      label: 'loop_next_idea_save', errorBox,
      extra: h('div', {class: 'pm-fields'},
        h('p', {class: 'ui-meta'}, t('loop_next_idea_hint')),
        h('blockquote', {class: 'pm-note-text'}, row.text_excerpt || ''),
        h('label', {for: reason.id}, t('loop_next_idea_reason')), reason),
      onSaved: async value => {
        guard();
        if (disposed || selected !== exactSource || value?.workspace_id !== workspaceId
            || value?.work_item_id !== workItemId || value?.source_operation_id !== selected
            || value?.execution_authorized !== false || !THREAD.test(value?.thread_id || '')) return;
        completed = true;
        result.replaceChildren(h('a', {href: `#/pm/${encodeURIComponent(workspaceId)}/discussions/${value.thread_id}`}, t('loop_next_idea_open')));
        void refresh();
      },
    });
    reason.addEventListener('input', () => form?.update());
    box.replaceChildren(h('details', {open: true}, h('summary', {}, t('loop_next_idea_title')), form.box, result));
    reason.focus();
  }
  function links(row) {
    const saved = row?.deferred_idea;
    if (!saved || !OP.test(saved.operation_id || '')) return [];
    return [h('a', {href: `#/op/${saved.operation_id}`}, t('loop_next_idea_request')),
      ...(saved.status === 'succeeded' && THREAD.test(saved.thread_id || '') ? [
        h('a', {href: `#/pm/${encodeURIComponent(workspaceId)}/discussions/${saved.thread_id}`}, t('loop_next_idea_open'))] : [])];
  }
  return {box, offered, open, links, clear,
    update: () => {if (selected && !current()) clear(); else form?.update();},
    dispose: () => {disposed = true; clear();}};
}
