import {repairIntent, repairRequest, validateRepairRecord} from './repair-intent.js';

// Read fixed central evidence, then create/reuse an ordinary work item. Launch is separate.
export function repairPanel({h, t, api, caps, guard, namespace, source, errorBox, opStatus}) {
  let controller, document, busy = false, reading = false, disposed = false, serial = 0, initialized = false;
  const alive = () => {try {guard(); return !disposed;} catch {return false;}};
  const key = `batc.repair.${namespace}.${JSON.stringify(source)}`;
  const status = h('div', {role:'status'}), evidence = h('div'), outcome = h('div');
  const project = h('select', {'aria-label':t('repair_project')}, h('option',{value:''},t('repair_choose_project')));
  const allowed = () => ['observe', 'manage'].every(scope => caps()?.scopes?.includes(scope)) && caps()?.actions?.some(action => action.action === 'repair.create' && action.allowed === true);
  function ownFocus(button) {
    // `document` in this panel is the repair evidence, not the page.
    const page = globalThis.document;
    let owned = page.activeElement === button;
    const onFocus = event => {
      const target = event.target;
      if (target === button) {owned = true; return;}
      if ((target === page.body || target === page.documentElement) && (button.hidden || button.disabled || !button.isConnected)) return;
      owned = false;
    };
    page.addEventListener('focusin', onFocus, true);
    return () => {
      page.removeEventListener('focusin', onFocus, true);
      const active = page.activeElement;
      return owned && (active === button || active === page.body || active == null);
    };
  }
  function shown(node) {
    return node && node.isConnected && !node.hidden && !node.disabled && !node.parentElement?.closest('[hidden]');
  }
  function moveFrom(button, candidate) {
    if (shown(candidate)) {candidate.focus(); return;}
    const group = button.closest('.actions') || button.parentElement;
    const controls = [...group.querySelectorAll('button, a[href], input, select, textarea')];
    const index = Math.max(0, controls.indexOf(button));
    const nextControl = [...controls.slice(index + 1), ...controls.slice(0, index)].find(node => shown(node) && node.getAttribute('aria-disabled') !== 'true');
    if (nextControl) {nextControl.focus(); return;}
    status.tabIndex = -1; status.focus();
  }
  function block(button, staticReason, flight, pressed) {
    if (pressed || (!staticReason && flight)) {button.disabled = false; button.setAttribute('aria-disabled', 'true');}
    else if (staticReason) {button.disabled = true; button.removeAttribute('aria-disabled');}
    else {button.disabled = false; button.removeAttribute('aria-disabled');}
  }
  const reload = h('button',{class:'secondary',type:'button',onclick:async()=>{
    if (!alive() || reading || busy) return;
    reading = true;
    const release = ownFocus(reload);
    update();
    try {await read();}
    finally {
      reading = false;
      if (alive()) update();
      if (alive() && release() && (reload.hidden || reload.disabled)) moveFrom(reload);
    }
  }},t('repair_read'));
  const create = h('button',{class:'secondary',type:'button',disabled:true,onclick:async()=>{
    if (!alive() || busy || !allowed()) return;
    const release = ownFocus(create);
    busy = true; update();
    try {show(await (controller.snapshot().intent ? controller.check() : controller.create(document)));}
    catch(error) {if(alive()) status.replaceChildren(errorBox(error, {within:status}));}
    finally {
      busy=false;
      if(alive()) update();
      if (alive() && release() && (create.hidden || create.disabled)) moveFrom(create);
    }
  }},t('repair_create'));
  const next = h('button',{class:'secondary',type:'button',hidden:true,onclick:()=>{
    if (!alive() || busy || !['succeeded','failed','cancelled'].includes(controller.snapshot().operation?.status)) return;
    const release = ownFocus(next);
    try {localStorage.removeItem(key); initializeController(); outcome.replaceChildren(); read();}
    catch(error) {release(); if(alive()) status.replaceChildren(errorBox(error, {within:status})); return;}
    if (release() && (next.hidden || next.disabled)) moveFrom(next, reload);
  }},t('repair_review_new'));
  const box = h('details',{class:'panel', 'data-repair-panel':''}, h('summary',{},t('repair_title')),
    h('p',{class:'muted'},t('repair_help')),h('label',{},t('repair_project'),project),
    h('div',{class:'actions'},reload,create,next),status,evidence,outcome);
  function initializeController() {
    controller=repairIntent({api,guard:()=>{guard();if(disposed)throw Error('Retired view');},
      actor:()=>caps()?.actor,allowed,storage:localStorage,storageKey:key});
  }
  function update() {
    const snapshot=controller?.snapshot(), fixed=!!snapshot?.intent;
    project.disabled=busy||fixed;
    block(reload, !project.value, busy, reading);
    block(create, !allowed()||(!document&&!fixed), false, busy);
    create.textContent=t(fixed?'repair_recover':'repair_create');
    next.hidden=!['succeeded','failed','cancelled'].includes(snapshot?.operation?.status);
    if ([create, reload].some(button => button.getAttribute('aria-disabled') === 'true')) status.setAttribute('aria-busy', 'true');
    else status.removeAttribute('aria-busy');
  }
  function links(record) {
    if (!record) return;
    if (!/^wi_[0-9a-f]{20}$/.test(record.work_item_id) || record.project_id !== project.value ||
        record.dispatch_operation_id != null && !/^op_[0-9a-f]{32}$/.test(record.dispatch_operation_id)) throw new Error(t('repair_unavailable'));
    outcome.append(h('p',{},h('a',{href:`#/item/${record.work_item_id}`},t('repair_open_work')),' · ',
      record.dispatch_operation_id ? h('a',{href:`#/op/${record.dispatch_operation_id}`},t('repair_open_dispatch')) :
        h('a',{href:`#/dispatch/${record.project_id || project.value}/${record.work_item_id}`},t('repair_review_dispatch'))));
  }
  function show(operation) {
    if (!alive()) return;
    outcome.replaceChildren(opStatus(operation),' ',h('a',{href:`#/op/${operation.operation_id}`},operation.operation_id));
    if(operation.status==='succeeded') {
      if(operation.result?.evidence_digest !== controller.snapshot().intent.request.preconditions.expected_evidence_digest) throw new Error(t('repair_unavailable'));
      links(operation.result);
    }
  }
  async function read() {
    if(!alive()||busy||!project.value) return;
    const mine=++serial, pid=project.value;
    document=null;status.replaceChildren();update();
    try {
      const params=new URLSearchParams(source);
      const doc=await api('GET',`/projects/${pid}/repair-evidence?${params}`);
      if(!alive()||mine!==serial||project.value!==pid)return;
      const request = repairRequest(doc);
      if (doc.project_id !== pid || JSON.stringify(Object.entries(request.params.source).sort()) !== JSON.stringify(Object.entries(source).sort())) {
        throw new Error(t('repair_unavailable'));
      }
      document=doc;
      evidence.replaceChildren(h('p',{},t('repair_fixed_evidence'),' ',h('code',{},doc.evidence_digest)),
        h('details',{},h('summary',{},t('repair_evidence_details')),h('pre',{class:'pre'},JSON.stringify(doc.evidence,null,2))));
      if(!controller.snapshot().intent) {outcome.replaceChildren();links(doc.existing);}
    } catch(error) {if(alive()&&mine===serial)status.replaceChildren(errorBox(error, {within:status}));}
    finally{if(alive()&&mine===serial)update();}
  }
  async function init() {
    if(initialized||!alive())return;initialized=true;
    try {
      initializeController();
      const selected=controller.snapshot().intent?.request.target.project_id;
      const doc=await api('GET','/projects');if(!alive())return;
      const rows=new Map();
      const visit=row=>{if(row?.project_id&&!row.archived)rows.set(row.project_id,row);for(const child of row.children||[])visit(child);};
      for(const row of doc.projects||[])visit(row);
      project.replaceChildren(h('option',{value:''},t('repair_choose_project')),
        ...[...rows.values()].map(row=>h('option',{value:row.project_id},row.name)));
      if(selected&&!rows.has(selected))project.append(h('option',{value:selected},selected));
      if(selected)project.value=selected;
      update();
      if(controller.snapshot().intent?.operation_id)show(await controller.check());
    }catch(error){if(alive())status.replaceChildren(errorBox(error, {within:status}));}
    finally{if(alive())update();}
  }
  project.addEventListener('change',()=>read());
  box.addEventListener('toggle',()=>{if(box.open)init();});
  return {box,dispose(){disposed=true;serial++;}};
}

// Kept outside the work editor so refreshing dispatch receipts never replaces drafts.
export function repairWorkItemPanel({h, t, api, guard, projectId, workItemId, originOperationId, errorBox}) {
  const box = h('section', {class: 'panel', 'data-repair-work-item': '', hidden: true});
  let disposed = false, repairOrigin = null, refreshQueue = Promise.resolve();
  const alive = () => {try {guard(); return !disposed;} catch {return false;}};
  function refresh() {
    const work = refreshQueue.catch(() => {}).then(async () => {
      if (!alive()) return;
      try {
        if (repairOrigin === null) {
          const doc = await api('GET', `/operations/${originOperationId}`);
          if (!alive()) return;
          if (doc.operation?.operation_id !== originOperationId) throw new Error(t('repair_unavailable'));
          repairOrigin = doc.operation.action === 'repair.create' && doc.operation.target?.project_id === projectId;
        }
        if (!repairOrigin) return;
        const record = await api('GET', `/work-items/${workItemId}/repair`);
        if (!alive()) return;
        validateRepairRecord(record, projectId, workItemId);
        box.replaceChildren(h('h2', {}, t('repair_title')),
          h('p', {}, t('repair_fixed_evidence'), ' ', h('code', {}, record.evidence_digest)),
          h('details', {}, h('summary', {}, t('repair_evidence_details')), h('pre', {class: 'pre'}, JSON.stringify(record.evidence, null, 2))),
          record.dispatch_operation_id ? h('p', {}, t('repair_dispatched'), ' ',
            h('a', {href: `#/op/${record.dispatch_operation_id}`}, t('repair_open_dispatch'))) :
            record.dispatchable ? h('a', {href: `#/dispatch/${projectId}/${workItemId}`}, t('repair_review_dispatch')) :
              h('p', {class: 'muted'}, t('repair_unavailable')));
        box.hidden = false;
      } catch (error) {
        if (!alive()) return;
        if (error.status === 404 && error.code === 'REPAIR_NOT_FOUND') {repairOrigin = false; box.replaceChildren(); box.hidden = true;}
        else {box.replaceChildren(errorBox(error, {within:box})); box.hidden = false;}
      }
    });
    refreshQueue = work;
    return work;
  }
  return {box, refresh, dispose() {disposed = true;}};
}
