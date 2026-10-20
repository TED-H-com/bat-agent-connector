import {parse} from 'acorn';

// Test-only observer: no timers, network calls, DOM contents, request keys,
// resource IDs, credentials or error messages enter this bounded ring.
export function gateTimeline(now = () => performance.now(), capacity = 1024) {
  const entries = [], previous = new Map();
  let sequence = 0, dropped = 0, forms = 0;
  const actions = ['pm.governance.revise', 'pm.conversation.adopt'];
  const source = () => (new Error().stack || '').split('\n').slice(2, 12)
    .map(line => line.match(/\bat (?:async )?([A-Za-z_$][\w.$]*)[ (]/)?.[1] || 'callback')
    .filter(name => !/^(source|capture|value|event|snapshot|Object\.)/.test(name)).slice(0, 6);
  const capture = (channel, id, inputs, force = false) => {
    if (id < 0) return;
    // Call sites supply fixed labels and scalar facts. Reject arbitrary strings
    // even if a future observer accidentally passes a document field.
    const facts = Object.fromEntries(Object.entries(inputs).filter(([, value]) =>
      typeof value === 'boolean' || typeof value === 'number' && Number.isFinite(value)));
    const key = channel + ':' + id, signature = JSON.stringify(facts);
    if (!force && previous.get(key) === signature) return;
    previous.set(key, signature);
    if (previous.size > capacity) previous.delete(previous.keys().next().value);
    entries.push({sequence: ++sequence, at: now(), channel, id, inputs: facts, source: source()});
    if (entries.length > capacity) {entries.shift(); dropped++;}
  };
  const value = (channel, id, result, inputs = {}) => {
    capture(channel, id, {...inputs, value: Boolean(result)});
    return result; // Keep the predicate's exact value, including undefined.
  };
  const event = (channel, inputs = {}) => capture(channel, 0, inputs, true);
  const error = (channel, error, inputs = {}) => event(channel, {...inputs,
    status: Number.isFinite(error?.status) ? error.status : -1,
    networkError: error?.code === 'NETWORK_ERROR', viewChanged: error?.code === 'VIEW_CHANGED',
    connectionChanged: error?.code === 'CONNECTION_CHANGED'});
  function observeState(state) {
    const snapshot = () => capture('app.inputs', 0, {online: state.online, viewReady: state.viewReady,
      nativeBusy: state.nativeBusy, epoch: state.epoch, hasToken: Boolean(state.token)});
    for (const key of ['online', 'viewReady', 'nativeBusy', 'epoch', 'token', 'refreshCycle']) {
      let current = state[key];
      Object.defineProperty(state, key, {enumerable: true, configurable: true, get: () => current, set(next) {
        current = next;
        if (key === 'refreshCycle' && next) {
          let failure = next.error;
          Object.defineProperty(next, 'error', {enumerable: true, configurable: true, get: () => failure, set(value) {
            failure = value; error('refreshCycle.error', value, {failed: Boolean(value)});
          }});
        }
        snapshot();
      }});
    }
    snapshot();
  }
  return {capture, value, event, error, observeState,
    form: action => actions.includes(action) ? ++forms : -1,
    action: action => actions.indexOf(action),
    read: () => ({capacity, dropped, at: now(), entries: entries.slice()})};
}

function nodes(root) {
  const result = [];
  function visit(node, parents = []) {
    if (!node || typeof node !== 'object' || typeof node.type !== 'string') return;
    result.push({node, parents});
    for (const [key, value] of Object.entries(node)) {
      if (key === 'start' || key === 'end') continue;
      for (const child of Array.isArray(value) ? value : [value]) visit(child, [...parents, node]);
    }
  }
  visit(root); return result;
}

// Insert observations in the served response, never source/assets on disk.
// AST ranges preserve predicate expressions and their short circuiting.
export function instrumentPmGates(source) {
  const all = nodes(parse(source, {ecmaVersion: 'latest', sourceType: 'module'})), edits = [];
  const insert = (at, text) => edits.push({start: at, end: at, text: '\n' + text + '\n'});
  const unique = (predicate, label) => {
    const found = all.filter(predicate);
    if (found.length !== 1) throw Error(`Gate trace expected one ${label}, got ${found.length}`);
    return found[0];
  };
  const named = name => unique(({node}) => node.type === 'FunctionDeclaration' && node.id?.name === name, name).node;
  const within = (fn, predicate) => all.filter(({node}) => node.start > fn.start && node.end < fn.end && predicate(node));
  const wrap = (node, channel, id, facts = '{}') => edits.push({start: node.start, end: node.end,
    text: `window.__pmGateTrace.value(${JSON.stringify(channel)},${id},(${source.slice(node.start,node.end)}),${facts})`});

  const durable = named('durableForm');
  insert(durable.body.start + 1, `const __gateForm = window.__pmGateTrace.form(action);
    if (__gateForm >= 0) {
      const originalPermitted = permitted, originalRecover = recoverPermitted, originalCanSend = canSend;
      permitted = () => window.__pmGateTrace.value('form.permitted', __gateForm, originalPermitted());
      recoverPermitted = () => window.__pmGateTrace.value('form.recoverPermitted', __gateForm, originalRecover());
      canSend = () => window.__pmGateTrace.value('form.canSend', __gateForm, originalCanSend());
    }`);
  for (const name of ['allowed', 'sendReady']) {
    const declarations = within(durable, node => node.type === 'VariableDeclarator' && node.id.name === name);
    if (declarations.length !== 1) throw Error('Gate trace predicate changed: ' + name);
    wrap(declarations[0].node.init.body, 'form.' + name, '__gateForm', name === 'allowed'
      ? `{action:window.__pmGateTrace.action(action),hasIntent:Boolean(saved.intent),hasOperationId:Boolean(saved.intent?.operation_id),scope:Boolean(caps()?.scopes?.includes(requiredScope)),actionAllowed:Boolean(caps()?.actions?.some(row=>row.action===action&&row.allowed===true))}`
      : `{hasOperationId:Boolean(saved.intent?.operation_id)}`);
  }
  const update = within(durable, node => node.type === 'FunctionDeclaration' && node.id.name === 'update')[0].node;
  insert(update.body.end - 1, `window.__pmGateTrace.capture('form.control',__gateForm,{action:window.__pmGateTrace.action(action),disabled:button.disabled,ariaDisabled:button.getAttribute('aria-disabled')==='true',busy,damaged,initialChecking,receiptTimer:receiptTimer!==null,hasIntent:Boolean(saved.intent),hasOperationId:Boolean(saved.intent?.operation_id)});`);
  const run = within(durable, node => node.type === 'FunctionDeclaration' && node.id.name === 'run')[0].node;
  const catches = within(run, node => node.type === 'CatchClause' && node.param?.name === 'error');
  if (catches.length !== 1) throw Error('Gate trace durable catch changed');
  insert(catches[0].node.body.start + 1, `if(__gateForm>=0)window.__pmGateTrace.error('form.catch',error,{form:__gateForm,action:window.__pmGateTrace.action(action),automatic,requestStarted});`);

  const workspace = named('mountPmWorkspace'), resolution = named('issueResolution');
  for (const [fn, declarationName, functionName, facts] of [
    [workspace, 'disposed', '__traceWorkspace', '{loaded,initialReadComplete,refreshing,workspacePresent:Boolean(workspace),owner:workspace?.membership?.role===\'owner\',contributor:workspace?.membership?.role===\'contributor\',admin:Boolean(caps()?.scopes?.includes(\'admin\')),threadWrite:threadData?.capabilities?.write===true,threadDecide:threadData?.capabilities?.decide===true}'],
    [resolution, 'serial', '__traceResolution', '{reading,enabled,workspacePresent:Boolean(workspace),owner:role()===\'owner\',contributor:role()===\'contributor\',manage:Boolean(caps()?.scopes?.includes(\'manage\')),observe:Boolean(caps()?.scopes?.includes(\'observe\')),actionAllowed:Boolean(caps()?.actions?.some(row=>row.action===\'pm.governance.revise\'&&row.allowed===true)),entryPresent:[...entries.values()].some(entry=>Boolean(entry.value))}']
  ]) {
    const declaration = within(fn, node => node.type === 'VariableDeclaration' && node.declarations.some(d => d.id.name === declarationName))[0].node;
    insert(declaration.end, `function ${functionName}(value){window.__pmGateTrace.capture('${fn===workspace?'workspace.inputs':'resolution.inputs'}',0,${facts});return value;}`);
    const inputs = fn === workspace ? ['loaded','initialReadComplete','refreshing','workspace','threadData'] : ['reading','enabled','workspace'];
    for (const {node} of within(fn, node => node.type === 'AssignmentExpression' && node.left.type === 'Identifier' && inputs.includes(node.left.name)))
      edits.push({start:node.start,end:node.end,text:`${functionName}((${source.slice(node.start,node.end)}))`});
  }
  const load = within(workspace, node => node.type === 'FunctionDeclaration' && node.id.name === 'load')[0].node;
  insert(load.body.start + 1, `window.__pmGateTrace.event('workspace.load',{event});`);
  const refresh = within(resolution, node => node.type === 'FunctionDeclaration' && node.id.name === 'refresh')[0].node;
  insert(refresh.body.start + 1, `window.__pmGateTrace.event('resolution.refresh');`);

  const state = unique(({node}) => node.type === 'VariableDeclarator' && node.id.name === 'state' && node.init?.type === 'ObjectExpression'
    && node.init.properties.some(property => property.key?.name === 'refreshCycle'), 'app state');
  insert(state.parents.at(-1).end, 'window.__pmGateTrace.observeState(state);');
  const errorBox = named('errorBox');
  const refreshContext = source.slice(errorBox.start,errorBox.body.start).includes('sourceRefresh') ? 'sourceRefresh' : 'true';
  insert(errorBox.body.start + 1, `window.__pmGateTrace.error('errorBox',e,{cycleActive:Boolean(state.refreshCycle),sourceRefresh:${refreshContext}});`);
  const stream = named('streamEvents');
  const pollCatch = within(stream, node => node.type === 'CatchClause' && node.param?.name === 'error')[0].node;
  insert(pollCatch.body.start + 1, `window.__pmGateTrace.error('events.catch',error,{pollSucceeded,cycleError:Boolean(cycle?.error)});`);

  for (const edit of edits.sort((a,b) => b.start-a.start || b.end-a.end)) source = source.slice(0,edit.start) + edit.text + source.slice(edit.end);
  return source;
}

export async function installPmGateTrace(page) {
  await page.addInitScript({content: `window.__pmGateTrace=(${gateTimeline.toString()})();
    for(const type of ['pointerdown','pointerup','click','submit'])document.addEventListener(type,event=>{
      const form=event.target.closest?.('[data-pm-action]'),action=window.__pmGateTrace.action(form?.dataset.pmAction);
      if(action<0)return;
      const button=form.querySelector('button[type=submit]');
      window.__pmGateTrace.event('gesture.'+type,{action,disabled:Boolean(button?.disabled),ariaDisabled:button?.getAttribute('aria-disabled')==='true'});
    },true);`});
  await page.route('**/dashboard/app.js',async route=>{
    const response=await route.fetch();
    await route.fulfill({response,body:instrumentPmGates(await response.text())});
  });
}

export const readPmGateTrace = page => page.evaluate(() => window.__pmGateTrace?.read() ?? null).catch(() => null);
