import {button, disclosure} from './ui/components.js';

const criterionKey = value => value?.kind === 'acceptance' && [value.requirement_id,value.input_id].every(id => typeof id === 'string' && id.length)
  ? JSON.stringify([value.kind, value.requirement_id, value.input_id])
  : value?.kind === 'dod' && typeof value.criterion_id === 'string' && value.criterion_id.length ? JSON.stringify([value.kind, value.criterion_id]) : null;
const outcomes = ['accepted', 'rejected', 'unassessed', 'not_defined'];

// A lazy view of the same original document included in the PM context. It does
// not evaluate current acceptance or substitute the review's latest conclusion.
export function discussionAssessments({h, t, api, alive, workspaceId, threadId, namespace}) {
  let refs = [], serial = 0, ready = false, pending = null, disposed = false;
  const body = h('div'), status = h('div', {role:'status'});
  const element = h('section', {'data-discussion-assessment':'', hidden:true}, status, body);
  const text = (key, attrs = {}) => h('p', {class:'muted', ...attrs}, t(key));
  const valid = ticket => {try {alive();return !disposed && ticket === serial;} catch {return false;}};
  const unavailable = () => text('pd_assessment_unavailable');
  function withdraw() {serial++;ready=false;pending=null;body.replaceChildren();status.replaceChildren();}
  function original(ref, item) {
    const value = item?.summary, assessment = value?.assessment, context = value?.context;
    if (item?.available !== true || item.id !== ref.id || item.review_id !== ref.review_id || item.document_digest !== ref.document_digest
      || value?.workspace_id !== workspaceId || value.review_id !== ref.review_id
      || value.semantics !== 'immutable_original_assessment' || value.current_applicability !== 'not_evaluated'
      || assessment?.operation_id !== ref.id || assessment.document_digest !== ref.document_digest
      || !Number.isSafeInteger(assessment.version) || !/^[0-9a-f]{40}$/.test(context?.native_result?.candidate_commit)
      || !Array.isArray(context.criteria) || !context.criteria.length || !Array.isArray(assessment.document?.criteria)
      || typeof assessment.actor !== 'string' || typeof assessment.document.rationale !== 'string'
      || !outcomes.includes(assessment.document.acceptance) || !outcomes.includes(assessment.document.dod)) return null;
    const keys = context.criteria.map(row => criterionKey(row.criterion));
    const conclusions = assessment.document.criteria;
    if (keys.some(key => !key) || new Set(keys).size !== keys.length || conclusions.length !== keys.length
      || new Set(conclusions.map(row => criterionKey(row.criterion))).size !== keys.length
      || context.criteria.some(row => typeof row.text !== 'string')
      || conclusions.some(row => !keys.includes(criterionKey(row.criterion)) || !['accepted','rejected','unassessed'].includes(row.status) || typeof row.rationale !== 'string')) return null;
    return value;
  }
  function render(ref, item, coverage) {
    if (ref.available === false) return unavailable();
    if (item?.available === true && !item.summary && coverage?.omitted_context_positions?.includes(item.position)) return text('pd_assessment_omitted');
    const value = original(ref, item);
    if (!value) return unavailable();
    const {context, assessment} = value, document = assessment.document;
    const line = (key, content) => h('p', {class:'pm-source'}, h('strong', {}, t(key)+': '), content);
    return h('section', {}, h('h3', {}, t('pd_kind_quality_assessment')),
      text('pd_assessment_historical'),
      h('p', {}, `${t('pmq_acceptance')}: ${t('pmq_'+document.acceptance)} · ${t('pmq_dod')}: ${t('pmq_'+document.dod)}`),
      line('pmq_candidate', h('span', {title:context.native_result.candidate_commit}, context.native_result.candidate_commit.slice(0,12))),
      document.rationale ? h('p', {class:'pm-note-text'}, document.rationale) : null,
      ...context.criteria.map(row => {
        const saved = document.criteria.find(item => criterionKey(item.criterion) === criterionKey(row.criterion));
        return disclosure({key:JSON.stringify([namespace,workspaceId,threadId,ref.id,criterionKey(row.criterion)]),
          label:`${row.text} · ${t('pmq_'+saved.status)}`,
          body:h('div', {}, h('p', {class:'pm-note-text'}, saved.rationale), saved.verification
            ? line('pmq_verification', ['passed','failed','unassessed','unavailable'].includes(saved.verification.status)
              ? t('pmq_'+saved.verification.status) : t('ui_status_unknown')) : text('pmq_no_proof'))});
      }),
      disclosure({label:t('pd_details'), body:h('div', {}, line('pmq_reviewer', assessment.actor),
        line('pmq_candidate', context.native_result.candidate_commit), line('pmq_digest', assessment.document_digest),
        h('a', {href:`#/op/${ref.id}`}, t('pmq_history_receipts')))}));
  }
  function refresh() {
    if (disposed || !refs.length || ready) return Promise.resolve();
    if (pending) return pending;
    if (!refs.some(ref => ref.available !== false)) {body.replaceChildren(unavailable());ready=true;return Promise.resolve();}
    const ticket = ++serial, selected = structuredClone(refs);
    status.replaceChildren(text('pd_assessment_loading'));
    const read = (async () => {
      try {
        const value = await api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}/threads/${encodeURIComponent(threadId)}/context?max_chars=64000`);
        if (!valid(ticket)) return;
        if (value.workspace_id !== workspaceId || value.thread_id !== threadId || !Array.isArray(value.bound_context)) throw Error('scope');
        body.replaceChildren(...selected.map(ref => {
          const matches = value.bound_context.filter(item => item.kind === 'quality_assessment' && item.position === ref.position);
          return matches.length === 1 ? render(ref, matches[0], value.coverage) : unavailable();
        }));
        status.replaceChildren();ready=true;
      } catch {
        if (!valid(ticket)) return;
        body.replaceChildren();status.replaceChildren(unavailable(), button({variant:'secondary',size:'sm',label:t('pd_assessment_retry'),onClick:()=>void refresh()}));
      } finally {if (valid(ticket)) pending=null;}
    })();
    pending=read;return read;
  }
  function update(context = []) {
    const next = context.filter(ref => ref.kind === 'quality_assessment');
    if (JSON.stringify(next) !== JSON.stringify(refs)) {withdraw();refs=structuredClone(next);}
    element.hidden=!refs.length;
    if (element.isConnected && !element.closest('[hidden]')) void refresh();
  }
  return {element, update, refresh, withdraw, dispose(){withdraw();disposed=true;}};
}
