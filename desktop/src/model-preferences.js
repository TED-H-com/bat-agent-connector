import {timeStamp, timeLabel} from './ui/stamps.js';
// Catalogs and subscription observations come only from the selected BAT host.
const fields = ['initial_agent', 'initial_model', 'last_agent', 'last_model', 'hidden', 'order'];
const modelKey = model => `${model.agent}:${model.id}`;
const copyPreferences = prefs => Object.fromEntries(fields.map(key => [key, Array.isArray(prefs[key]) ? [...prefs[key]] : prefs[key] ?? null]));
const date = value => timeStamp(value);
const equal = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const validPreferences = value => value && typeof value === 'object' && fields.every(key => key in value)
  && ['initial_agent', 'last_agent'].every(key => value[key] === null || ['claude', 'codex'].includes(value[key]))
  && ['initial_model', 'last_model'].every(key => value[key] === null || typeof value[key] === 'string' && value[key].length <= 256)
  && ['hidden', 'order'].every(key => Array.isArray(value[key]) && value[key].length <= 1000 && value[key].every(id => typeof id === 'string' && id.length <= 300));
const validDraft = value => value && Number.isInteger(value.revision) && value.revision >= 0 && validPreferences(value.params);
export function orderedModels(doc, current = '') {
  const prefs = doc.model_preferences, catalog = doc.model_catalog;
  const order = new Map((prefs.order || []).map((id, i) => [id, i]));
  return (catalog.models || []).filter(model => model.available !== false &&
    (!prefs.hidden?.includes(modelKey(model)) || model.id === current))
    .sort((a, b) => (order.get(modelKey(a)) ?? 9999) - (order.get(modelKey(b)) ?? 9999));
}

export function modelChoice({h, t, api, caps, guard, host, agent, model, submit = null, storageKey = ''}) {
  let doc = null, serial = 0, currentHost = '', busy = false;
  const list = h('datalist', {id: `models-${crypto.randomUUID()}`}), note = h('p', {class: 'muted', role: 'status'});
  const current = h('p', {class: 'model-current muted', hidden: true});
  if (caps()?.features?.host_preferences?.version === 1) model.setAttribute('list', list.id);
  model.setAttribute('title', model.value);
  const choose = role => {
    if (!doc || model.disabled || agent.disabled) return;
    const prefs = doc.model_preferences, selectedAgent = prefs[`${role}_agent`], selectedModel = prefs[`${role}_model`];
    if (selectedAgent) {agent.value = selectedAgent; agent.dispatchEvent(new Event('change', {bubbles: true}));}
    model.value = selectedModel || ''; model.dispatchEvent(new Event('input', {bubbles: true}));
    update();
  };
  const initial = h('button', {class: 'mini', type: 'button', onclick: () => choose('initial')}, t('models_use_initial'));
  const last = h('button', {class: 'mini', type: 'button', onclick: () => choose('last')}, t('models_use_last'));
  const remember = h('button', {class: 'mini', type: 'button', hidden: !submit, onclick: async () => {
    if (!doc || busy || model.disabled) return;
    busy = true; update();
    try {
      guard(); const op = await submit('preferences.models.update', {host: currentHost},
        {last_agent: agent.value, last_model: model.value || null}, {expected_revision: doc.model_preferences.revision}, `${storageKey}.last-model`);
      guard(); if (op.status !== 'succeeded') throw Error(op.status_reason || t('models_pending'));
      await refresh(true);
    } catch (error) {note.textContent = error.message || String(error);} finally {busy = false; update();}
  }}, t('models_remember_last'));
  const box = h('div', {class: 'model-choice', hidden: true}, list, current, note, h('div', {class: 'actions'}, initial, last, remember));
  function update() {
    box.hidden = caps()?.features?.host_preferences?.version !== 1 || !host();
    initial.disabled = busy || model.disabled || !doc?.model_preferences.initial_agent;
    last.disabled = busy || model.disabled || !doc?.model_preferences.last_agent;
    remember.disabled = busy || model.disabled || !doc || !caps()?.actions?.some(action => action.action === 'preferences.models.update' && action.allowed);
    model.title = model.value;
    current.hidden = !model.value; current.textContent = model.value;
    current.title = model.value; current.setAttribute('aria-label', t('models_current', {model: model.value}));
  }
  async function refresh(force = false) {
    update(); if (box.hidden) return;
    const selectedHost = host(), selectedAgent = agent.value, request = ++serial;
    if (selectedHost !== currentHost) {doc = null; list.replaceChildren(); update();}
    try {
      const next = await api('GET', `/hosts/${encodeURIComponent(selectedHost)}/preferences?agent=${encodeURIComponent(selectedAgent)}${force ? '&refresh=1' : ''}`);
      guard(); if (request !== serial || selectedHost !== host() || selectedAgent !== agent.value) return;
      if (next.version !== 1 || next.host !== selectedHost || next.model_catalog?.agent !== selectedAgent || !next.model_preferences) throw Error(t('models_invalid'));
      doc = next; currentHost = selectedHost;
      list.replaceChildren(...orderedModels(doc, model.value).map(value => h('option', {value: value.id, label: value.label || value.id})));
      const known = doc.model_catalog.models?.some(value => value.id === model.value);
      note.textContent = model.value && !known ? t('models_unknown_current', {model: model.value})
        : doc.model_catalog.status !== 'available' ? t('models_catalog_unavailable', {reason: doc.model_catalog.reason || doc.model_catalog.status})
        : t(doc.model_catalog.stale ? 'models_catalog_stale' : 'models_catalog_ready', {host: selectedHost, count: list.childElementCount});
      update();
    } catch (error) {try {guard(); if (request === serial) {doc = null; list.replaceChildren(); note.textContent = error.message || String(error); update();}} catch { /* retired */ }}
  }
  agent.addEventListener('change', () => refresh());
  model.addEventListener('input', () => {update(); if (doc && model.value && !doc.model_catalog.models?.some(value => value.id === model.value)) note.textContent = t('models_unknown_current', {model: model.value});});
  return {box, refresh, update};
}

export function modelPreferencesPanel({h, t, api, caps, guard, submit, storageKey, onEvents, errorBox}) {
  let doc = null, draft = null, intent = null, busy = false, serial = 0, loadedHost = '';
  let drafts = {};
  try {drafts = JSON.parse(localStorage.getItem(storageKey) || '{}'); if (!drafts || Array.isArray(drafts) || typeof drafts !== 'object') drafts = {};} catch { /* no draft */ }
  const host = h('select', {'aria-label': t('models_host')}, h('option', {value: ''}, t('start_choose_host')),
    ...(caps()?.hosts || []).map(value => h('option', {value: value.host}, value.host)));
  if (caps()?.hosts?.length === 1) host.value = caps().hosts[0].host;
  const catalogAgent = h('select', {'aria-label': t('models_catalog_agent')}, h('option', {value: 'claude'}, 'Claude'), h('option', {value: 'codex'}, 'Codex'));
  const status = h('p', {class: 'muted', role: 'status'}), evidence = h('div'), usage = h('div'), rows = h('div'), form = h('div', {class: 'capture-fields'});
  const inputs = {};
  const persist = () => {
    guard(); if (loadedHost) drafts[loadedHost] = {draft, intent};
    localStorage.setItem(storageKey, JSON.stringify(drafts));
  };
  const modified = () => {
    if (!draft || intent || busy || doc?.host !== host.value) return;
    for (const key of ['initial_agent', 'initial_model', 'last_agent', 'last_model']) draft.params[key] = inputs[key].value || null;
    try {persist(); status.textContent = t('models_unsaved');} catch (error) {status.textContent = error.message || String(error);}
    update();
  };
  for (const role of ['initial', 'last']) {
    const agent = h('select', {'aria-label': t(`models_${role}_agent`)}, h('option', {value: ''}, t('models_no_preference')),
      h('option', {value: 'claude'}, 'Claude'), h('option', {value: 'codex'}, 'Codex'));
    const model = h('input', {'aria-label': t(`models_${role}_model`), maxlength: 256});
    inputs[`${role}_agent`] = agent; inputs[`${role}_model`] = model;
    agent.addEventListener('change', modified); model.addEventListener('input', modified);
    form.append(h('label', {}, t(`models_${role}_agent`), agent), h('label', {}, t(`models_${role}_model`), model));
  }
  const refresh = h('button', {class: 'secondary', type: 'button', onclick: () => load(true)}, t('models_refresh'));
  const reset = h('button', {class: 'secondary', type: 'button', onclick: () => {
    if (!draft || busy || intent || doc?.host !== host.value) return;
    draft.params.hidden = []; draft.params.order = [];
    try {persist(); status.textContent = t('models_unsaved'); renderRows(); update();} catch (error) {status.textContent = error.message || String(error);}
  }}, t('models_reset_display'));
  const discard = h('button', {class: 'secondary', type: 'button', onclick: () => {
    if (busy || intent || doc?.host !== host.value) return;
    try {
      guard(); const next = {...drafts}; delete next[loadedHost]; localStorage.setItem(storageKey, JSON.stringify(next));
      drafts = next; draft = null; load(true);
    } catch (error) {status.textContent = error.message || String(error);}
  }}, t('models_reload_saved'));
  const save = h('button', {class: 'primary', type: 'button', onclick: async () => {
    if (busy || !draft || !doc || doc.host !== host.value) return;
    busy = true;
    try {
      guard(); intent ||= {params: copyPreferences(draft.params), revision: draft.revision}; persist(); update();
      const op = await submit('preferences.models.update', {host: loadedHost}, intent.params,
        {expected_revision: intent.revision}, `${storageKey}.${loadedHost}`);
      guard();
      if (op.status === 'succeeded') {
        intent = null; draft = null; delete drafts[loadedHost]; localStorage.setItem(storageKey, JSON.stringify(drafts));
        status.textContent = t('models_saved'); await load(false);
      } else {
        if (['failed', 'cancelled'].includes(op.status)) {intent = null; persist();}
        status.textContent = op.status_reason || t('models_pending');
      }
    } catch (error) {
      try {guard(); if (error.code === 'VERSION_CONFLICT' && error.status === 409) {intent = null; persist();}
        status.textContent = error.message || String(error);} catch { /* retired */ }
    } finally {busy = false; update();}
  }}, t('save'));
  const box = h('details', {class: 'panel model-preferences', 'data-model-preferences': ''}, h('summary', {}, t('models_title')),
    h('p', {class: 'muted'}, t('models_personal', {actor: caps()?.actor || ''})),
    h('div', {class: 'capture-fields'}, h('label', {}, t('models_host'), host), h('label', {}, t('models_catalog_agent'), catalogAgent)),
    h('div', {class: 'actions'}, refresh), status, evidence, form, rows,
    h('div', {class: 'actions'}, save, reset, discard), h('h2', {}, t('models_usage')), usage);
  function update() {
    const locked = busy || Boolean(intent), coherent = doc?.host === host.value;
    for (const input of Object.values(inputs)) input.disabled = locked || !draft || !coherent;
    host.disabled = locked; catalogAgent.disabled = busy;
    save.disabled = busy || !draft || !coherent || !caps()?.actions?.some(action => action.action === 'preferences.models.update' && action.allowed);
    save.textContent = t(intent ? 'permissions_retry' : 'save');
    reset.disabled = discard.disabled = locked || !draft || !coherent;
    refresh.disabled = busy || !host.value;
    for (const input of rows.querySelectorAll('input,button')) input.disabled = locked || !coherent || input.dataset.boundary === 'true';
  }
  function renderRows() {
    const focused = document.activeElement?.dataset.modelControl;
    const prefs = draft?.params;
    if (!doc || !prefs) {rows.replaceChildren(); return;}
    const catalog = doc.model_catalog, order = new Map(prefs.order.map((id, i) => [id, i]));
    const models = [...catalog.models].sort((a, b) => (order.get(modelKey(a)) ?? 9999) - (order.get(modelKey(b)) ?? 9999));
    const change = action => {
      if (busy || intent || doc?.host !== host.value) return;
      action(); try {persist(); status.textContent = t('models_unsaved'); renderRows(); update();} catch (error) {status.textContent = error.message || String(error);}
    };
    rows.replaceChildren(h('h2', {}, t('models_display')),
      h('p', {class: 'muted'}, t('models_display_help')),
      ...models.map((model, i) => {
        const id = modelKey(model);
        const visible = h('input', {type: 'checkbox', checked: !prefs.hidden.includes(id), 'data-model-control': `${id}.visible`,
          onchange: () => change(() => {prefs.hidden = visible.checked ? prefs.hidden.filter(value => value !== id) : [...new Set([...prefs.hidden, id])];})});
        const move = delta => change(() => {
          if (i + delta < 0 || i + delta >= models.length) return;
          const next = models.map(modelKey); [next[i], next[i + delta]] = [next[i + delta], next[i]];
          prefs.order = [...next, ...prefs.order.filter(value => !next.includes(value))];
        });
        return h('div', {class: 'row'}, h('label', {class: 'grow', title: model.id}, visible, ' ', model.label || model.id, h('code', {}, ` · ${model.id}`)),
          h('button', {class: 'mini', type: 'button', disabled: i === 0, 'data-boundary': String(i === 0), 'aria-label': t('models_move_up', {model: model.id}), 'data-model-control': `${id}.up`, onclick: () => move(-1)}, '↑'),
          h('button', {class: 'mini', type: 'button', disabled: i === models.length - 1, 'data-boundary': String(i === models.length - 1), 'aria-label': t('models_move_down', {model: model.id}), 'data-model-control': `${id}.down`, onclick: () => move(1)}, '↓'));
      }));
    if (!models.length) rows.append(h('p', {class: 'muted'}, t('models_catalog_unavailable', {reason: catalog.reason || catalog.status})));
    [...rows.querySelectorAll('[data-model-control]')].find(node => node.dataset.modelControl === focused)?.focus({preventScroll: true});
  }
  function showUsage(value) {
    usage.replaceChildren(h('p', {class: 'muted'}, timeLabel(t, 'models_source', {source: value.source || 'bat_host', time: date(value.observed_at)})));
    if (value.status !== 'available') usage.append(h('p', {class: 'note'}, t('models_usage_unavailable', {reason: value.reason || value.status})));
    for (const provider of value.providers || []) {
      const row = h('section', {class: 'panel'}, h('strong', {}, provider.provider),
        h('p', {class: 'muted'}, t('models_host_account', {account: provider.account_email || t('obs_unknown')})),
        h('p', {class: 'muted'}, provider.plan_type, provider.plan_type ? ' · ' : '', date(provider.fetched_at)));
      if (provider.stale) row.append(h('p', {class: 'note warn'}, t('models_usage_stale', {reason: provider.reason || 'unknown'})));
      for (const [key, label] of [['five_hour', 'models_five_hour'], ['seven_day', 'models_seven_day']]) {
        const window = provider[key], known = typeof window?.utilization === 'number' && window.utilization >= 0 && window.utilization <= 1;
        row.append(h('p', {}, t(label), ': ', known ? `${Math.round(window.utilization * 100)}%` : t('obs_unknown'),
          ' · ', timeLabel(t, 'models_resets', {time: date(window?.resets_at)})));
      }
      usage.append(row);
    }
  }
  async function load(force = false) {
    if (!host.value) return;
    const selected = host.value, agent = catalogAgent.value, request = ++serial;
    update();
    try {
      const next = await api('GET', `/hosts/${encodeURIComponent(selected)}/preferences?agent=${agent}${force ? '&refresh=1' : ''}`);
      guard(); if (request !== serial || selected !== host.value || agent !== catalogAgent.value) return;
      if (next.version !== 1 || next.host !== selected || !Number.isInteger(next.model_preferences?.revision) || next.model_catalog?.agent !== agent || !validPreferences(copyPreferences(next.model_preferences)) || !Array.isArray(next.model_catalog?.models)) throw Error(t('models_invalid'));
      doc = next;
      if (loadedHost !== selected || !draft) {
        const saved = drafts[selected], valid = validDraft(saved?.draft);
        draft = valid ? saved.draft : {params: copyPreferences(next.model_preferences), revision: next.model_preferences.revision};
        intent = valid && validDraft(saved.intent) ? saved.intent : null;
        loadedHost = selected;
        for (const [key, input] of Object.entries(inputs)) input.value = draft.params[key] || '';
      }
      evidence.replaceChildren(...[h('p', {class: 'muted'}, timeLabel(t, 'models_source', {source: next.model_catalog.source || 'bat_host', time: date(next.model_catalog.observed_at)})),
        next.model_catalog.stale ? h('p', {class: 'note warn'}, t('models_catalog_stale', {host: selected})) : null,
        ...(next.unknown_models || []).map(model => h('p', {class: 'note'}, t('models_unknown_current', {model: `${model.agent}:${model.id}`})))].filter(Boolean));
      if (draft.revision !== next.model_preferences.revision) status.textContent = t('models_conflict');
      else if (!equal(draft.params, copyPreferences(next.model_preferences))) status.textContent = t('models_unsaved');
      renderRows(); showUsage(next.usage); update();
    } catch (error) {try {guard(); if (request === serial) {errorBox?.(error); status.textContent = `${t('models_not_refreshed')} ${error.message || error}`;}} catch { /* retired */ }}
  }
  host.addEventListener('change', () => load()); catalogAgent.addEventListener('change', () => load());
  box.addEventListener('toggle', () => {if (box.open && !doc) load();});
  const off = onEvents(event => box.open && event.resource_type === 'preferences' && event.resource_id === host.value ? load() : undefined);
  update(); return {box, dispose() {serial++; off();}};
}
