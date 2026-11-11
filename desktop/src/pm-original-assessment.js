import {assessmentSourceFence, assessmentSourceRefused} from './pm-assessment-source-fence.js';
import {originalAssessment} from './pm-work-result-requirements.js';
import {createWorkQuality} from './pm-work-quality.js';
import {assessmentSelection, assessmentQuery, parseAssessmentSelection, matchesAssessment,
  matchesAttempt, matchesResult, selectionFromResult, contextualAssessment, assessmentQueryKeys} from './pm-assessment-selection.js';

// One mounted reader owns its epoch. Withdrawal invalidates all pending reads,
// including replies issued before an event removed the originating source.
export function originalAssessmentReader({h, t, api, alive, workspaceId, serverId, caps, namespace, form, errorBox,
  refreshOrigin = async () => {}, onSelectionChange = () => {}, returnFocus = () => null}) {
  let reading = null, fence = null, sourceToken = null;
  let pin = null, epoch = 0, quality = null, currentResult = null, currentAttempt = null, recorded = null;
  const body = h('div'), editor = h('div'), status = h('div', {role: 'status'});
  const heading = h('h4', {tabindex: -1}, t('pmq_original_assessment'));
  const element = h('section', {'data-original-assessment': '', hidden: true}, heading, status, body, editor);
  const getCaps = () => typeof caps === 'function' ? caps() : caps;
  const supported = () => getCaps()?.features?.pm_workspaces?.quality_reviews?.exact_assessment_read === true;
  const guard = ticket => {alive?.(); if (ticket !== epoch || fence && !fence.current(sourceToken)) throw Error('VIEW_CHANGED');};
  function captureReading() {
    const active = element.ownerDocument.activeElement;
    if (body.contains(active) || editor.contains(active)) reading = {
      node: editor.contains(active) ? active : null, key: active.dataset.workResultFocus,
      start: active.selectionStart, end: active.selectionEnd,
      open: new Set([...body.querySelectorAll('details[open]')].map(node => node.dataset.resultRequirementDetails)),
    };
  }
  function restoreReading() {
    if (!reading || element.ownerDocument.activeElement !== element.ownerDocument.body) return;
    const target = reading.node?.isConnected ? reading.node : [...body.querySelectorAll('[data-work-result-focus]')].find(node => node.dataset.workResultFocus === reading.key);
    target?.focus({preventScroll: true});
    if (target?.setSelectionRange && reading.start != null) try {target.setSelectionRange(reading.start, reading.end);} catch { /* non-text control */ }
  }
  function remember(value) {
    // Replace only our typed fields on the existing route; never write a storage record.
    const [route, raw] = location.hash.split('?'), query = new URLSearchParams(raw || '');
    for (const key of assessmentQueryKeys) query.delete(key);
    if (value) for (const [key, item] of assessmentQuery(value, namespace)) query.set(key, item);
    history.replaceState(history.state, '', route + (query.size ? '?' + query : ''));
  }
  function close({focus = false, forget = false} = {}) {
    const focusTarget = focus ? returnFocus() : null;
    epoch++; fence?.release(); fence = null; sourceToken = null; reading = null; pin = null; recorded = null; quality?.dispose(); quality = null;
    body.replaceChildren(); editor.replaceChildren(); status.replaceChildren(); element.hidden = true; onSelectionChange(null);
    if (forget) remember(null);
    if (focus) {
      const target = focusTarget;
      if (target && !target.hasAttribute('data-assessment-open')) {
        target.parentElement.querySelector('[data-assessment-return-note]')?.remove();
        target.after(h('p', {role: 'status', 'data-assessment-return-note': ''}, t('pmq_return_changed')));
      }
      if (target) {
        for (let ancestor = target.parentElement; ancestor; ancestor = ancestor.parentElement) if (ancestor.tagName === 'DETAILS') ancestor.open = true;
        if (!target.matches('button,a,input,select,textarea')) target.tabIndex = -1; target.focus({preventScroll: true});}
    }
  }
  const back = h('button', {type: 'button', class: 'ui-button secondary', onclick: () => close({focus: true, forget: true})}, t('pmq_return_origin'));
  element.append(back);
  async function readContext(selected, original, ticket) {
    let requested = {...selected};
    if (!requested.attempt_id) {
      const context = original.context;
      requested = {...requested, package_id: context.package_ref.package_id, package_digest: context.package_ref.document_digest,
        execution_operation_id: context.execution_operation_id, candidate_commit: context.native_result.candidate_commit};
      const data = await api('GET', `/pm/workspaces/${workspaceId}/work/${requested.work_item_id}/attempts?after=0&limit=50`); guard(ticket);
      if (data.workspace_id !== workspaceId || data.work_item_id !== requested.work_item_id || !Array.isArray(data.attempts)) throw Error(t('pmq_invalid'));
      const matches = data.attempts.filter(row => row.workspace_id === workspaceId && row.work_item_id === requested.work_item_id
        && row.package_id === requested.package_id && row.execution_operation_id === requested.execution_operation_id);
      if (data.next_cursor !== null || data.visibility_filtered || matches.length !== 1) return null;
      requested.attempt_id = matches[0].attempt_id;
    }
    const [work, attempt, result] = await Promise.all([
      api('GET', `/work-items/${requested.work_item_id}`),
      api('GET', `/pm/workspaces/${workspaceId}/work-attempts/${requested.attempt_id}`),
      api('GET', `/pm/workspaces/${workspaceId}/work-attempts/${requested.attempt_id}/result`),
    ]); guard(ticket);
    if (!serverId || result?.identity?.server_id !== serverId) throw Error(t('pmq_invalid'));
    if (work?.work_item?.work_item_id !== requested.work_item_id || !matchesAttempt(requested, attempt)) throw Error(t('pmq_invalid'));
    if (!requested.result_id) requested = selectionFromResult({partition: namespace, envelope: result, reference: requested, origin: requested.origin});
    if (!requested || !matchesAssessment(requested, original)) return null;
    return {selection: requested, attempt: attempt.attempt, envelope: result, matches: matchesResult(requested, result, serverId)};
  }
  async function refresh({focus = false} = {}) {
    if (!pin || !supported()) {close(); return;}
    const selected = pin, ticket = ++epoch; sourceToken = fence?.token();
    captureReading();
    status.textContent = t('pmq_loading'); body.replaceChildren(); editor.hidden = true;
    const valid = () => {try {guard(ticket); return pin === selected;} catch {return false;}};
    try {
      const original = await api('GET', `/pm/workspaces/${selected.workspace_id}/quality-reviews/${selected.review_id}/assessments/${selected.operation_id}?version=${selected.version}&document_digest=${selected.document_digest}`); guard(ticket);
      if (!matchesAssessment(selected, original)) throw Error(t('pmq_invalid'));
      const context = await readContext(selected, original, ticket); guard(ticket);
      if (context && !selected.result_id) {pin = context.selection; remember(pin);}
      currentResult = context?.envelope; currentAttempt = context?.attempt;
      const contextual = context?.matches ? contextualAssessment(pin, currentResult, serverId) : null;
      const document = original.assessment.document;
      const assessment = {reference: {review_id: pin.review_id, operation_id: pin.operation_id, version: pin.version, document_digest: pin.document_digest},
        original: document, assessed_by: original.assessment.actor,
        conclusion: pin.requirement_id ? document.requirements?.find(row => row.requirement_id === pin.requirement_id)?.status || 'unassessed' : document.acceptance,
        ...(contextual ? {applicable: contextual.applicable, qualified: contextual.qualified,
          applicability_reasons: contextual.applicability_reasons, revocation: contextual.revocation} : {})};
      const content = originalAssessment({h, t, assessment, requirementId: pin.requirement_id, open: reading?.open}); content.open = true;
      body.replaceChildren(content); status.replaceChildren(); element.hidden = false;
      if (!context?.matches) {
        quality?.dispose(); quality = null; editor.replaceChildren();
        status.append(h('p', {}, t(context ? 'pmq_result_changed' : 'pmq_result_unresolved')),
          h('a', {class: 'ui-button secondary', href: `#/pm/${workspaceId}/board?select=${pin.work_item_id}`,
            onclick: () => {close({forget: true});}}, t('pmq_open_current_result')));
      } else {
        if (!quality) quality = createWorkQuality({h, t, api, alive, workspaceId, serverId, caps, namespace, form, errorBox,
          assessmentSelection: pin, originalAssessment: original, onSourceUnavailable: error => fence?.withdraw(error),
          refreshResult: async () => {await refreshOrigin(); await refresh();},
          onRecorded: reference => {
            recorded = assessmentSelection({...pin, ...reference}, namespace, {complete: true});
            if (recorded) status.append(h('button', {type: 'button', class: 'ui-button secondary',
              onclick: () => {if (recorded) void open(recorded);}}, t('pmq_view_recorded')));
          }});
        const node = quality.render(currentResult, currentAttempt);
        if (node && !editor.contains(node)) editor.replaceChildren(node);
        editor.hidden = false;
      }
      if (focus) heading.focus({preventScroll: true}); else restoreReading();
    } catch (error) {
      // A successful resolution may have enriched the pin without changing its epoch.
      if (!valid() && ticket !== epoch) return;
      try {guard(ticket);} catch {return;}
      if (assessmentSourceRefused(error)) fence?.withdraw(error);
      else fail(error);
    }
  }
  async function open(value) {
    const selected = assessmentSelection({return_operation_id: value.operation_id, return_review_id: value.review_id,
      return_document_digest: value.document_digest, ...value}, namespace);
    if (!supported() || !selected || selected.workspace_id !== workspaceId) return;
    close(); pin = selected; onSelectionChange(pin); fence = assessmentSourceFence(pin, fail); sourceToken = fence.token(); element.hidden = false; remember(pin); await refresh({focus: true});
  }
  function restore(origin, accepts = () => true) {
    const value = parseAssessmentSelection(new URLSearchParams(location.hash.split('?')[1] || ''), namespace);
    if (!pin && value?.origin === origin && value.workspace_id === workspaceId && accepts(value)) void open(value);
  }
  function fail(error) {
    epoch++; quality?.dispose(); quality = null; body.replaceChildren(); editor.replaceChildren();
    status.replaceChildren(errorBox(error, {within: status})); element.hidden = false;
  }
  return {element, open, refresh, close, restore, supported, selected: () => pin,
    sourceWithdrawn: error => fence?.withdraw(error),
    suspend: () => {captureReading(); epoch++; body.replaceChildren(); editor.hidden = true;},
    fail};
}
