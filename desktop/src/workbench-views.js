import {h, place} from './ui/dom.js';
import {tabs, button, inspector} from './ui/components.js';
import {timeStamp} from './ui/stamps.js';
import {viewState} from './ui/view-state.js';
import {workbenchProjection} from './workbench-projection.js';

// All three views retain the original conversation/composer DOM and event reader.
export function workbenchViews({main, head, work, results, namespace, workspace, host, sessionId, workId, t}) {
  const memory = viewState(namespace, workspace, 'workbench:' + (workId || JSON.stringify([host, sessionId])));
  let projection = null, panel = null, signature = '', inspected = null, updateInspector = null;
  const summary = h('p', {class: 'wc-summary', 'data-workbench-summary': ''});
  const goal = h('p', {class: 'wc-goal', hidden: true});
  const responsibility = h('p', {class: 'ui-meta wc-responsibility'});
  const lineage = h('section', {class: 'wc-perspective', hidden: true, 'data-workbench-lineage': ''});
  const live = h('section', {class: 'wc-perspective', hidden: true, 'data-workbench-live': ''});
  // Results belongs to the selected work, not the visibility of its conversation.
  const evidence = h('div', {class: 'session-workspace wc-inspector-host'}, results);
  const regions = {work, lineage, live};
  let current = Object.hasOwn(regions, memory.view) ? memory.view : 'work';
  const controls = tabs({label: t('wc_views'), value: current,
    options: Object.entries(regions).map(([value, region]) => ({value, label: t('wc_' + value), panel: region})),
    onChange: select});
  controls.dataset.workbenchViews = '';
  const bar = h('div', {class: 'wc-view-bar'}, controls);
  head.append(goal, responsibility, summary); head.before(bar); work.after(lineage, live, evidence);
  function select(value) {
    if (!Object.hasOwn(regions, value)) return;
    memory.scroll ||= {};
    memory.scroll[current] = regions[current].scrollTop;
    panel?.close(); results.close();
    current = memory.view = value;
    for (const [key, region] of Object.entries(regions)) region.hidden = key !== value;
    regions[value].scrollTop = memory.scroll[value] || 0;
  }
  select(current);
  const edgeControl = key => [...lineage.querySelectorAll('[data-edge]')].find(node => node.dataset.edge === key);
  const activeTab = () => controls.querySelector('[aria-selected="true"]');
  function showEdge(edge, opener) {
    panel?.close(); inspected = edge.key;
    const title = h('span'), kind = h('p'), name = h('p'), link = h('a', {class: 'ui-button secondary'}, t('wc_open'));
    const source = h('code'), id = h('p', {class: 'mono'});
    panel = inspector({title, overlay: true,
      returnFocus: () => edgeControl(edge.key) || (opener.isConnected ? opener : activeTab()),
      onClose: () => {panel = null; inspected = null; updateInspector = null;}});
    panel.dataset.workbenchInspect = edge.key;
    updateInspector = next => {
      title.textContent = t('wc_inspecting') + ' · ' + (next.title || t('wc_' + next.type));
      kind.textContent = t('wc_' + next.type); name.textContent = next.title || next.id;
      link.href = next.href; source.textContent = next.source; id.textContent = next.id;
    };
    updateInspector(edge);
    panel.body.append(kind, name, link, h('details', {}, h('summary', {}, t('wc_source')), source, id));
    main.append(panel);
  }
  function card() {
    return h('article', {class: 'wc-live-card'}, h('h3', {}, projection.title || t('session_untitled')),
      h('p', {class: 'ui-meta'}, [projection.host, projection.runtime, projection.model].filter(Boolean).join(' · ')),
      h('p', {}, t('wc_' + projection.state)),
      h('p', {class: 'ui-meta'}, t('wc_latest'), ' · ', timeStamp(projection.observedAt, {empty: t('obs_unknown')})),
      button({label: t('wc_work'), size: 'sm', 'data-live-work': '', onClick: () => {select('work'); controls.setValue('work');}}));
  }
  function paint() {
    if (!projection) return;
    goal.textContent = projection.goal; goal.hidden = !projection.goal;
    responsibility.textContent = [t(projection.managed ? 'managed' : 'read_only'), projection.runtime, projection.host].filter(Boolean).join(' · ');
    summary.textContent = t('wc_' + projection.state);
    const focused = lineage.contains(document.activeElement) ? document.activeElement?.dataset.edge : null;
    const liveFocused = live.contains(document.activeElement) && document.activeElement?.hasAttribute('data-live-work');
    const edges = projection.edges.slice(0, 30);
    place(lineage, 'replaceChildren', h('h2', {}, t('wc_related')), h('p', {class: 'ui-meta'}, t('wc_lineage_help')),
      h('div', {class: 'wc-relations'}, ...edges.map(edge => {
        const control = button({variant: 'ghost', class: 'wc-relation', 'data-edge': edge.key,
          label: h('span', {}, h('span', {class: 'ui-meta'}, t('wc_' + edge.type)), h('strong', {}, edge.title || t('wc_' + edge.type))),
          onClick: () => showEdge(edge, control)});
        return control;
      })),
      h('p', {class: 'ui-meta'}, t(edges.length ? 'wc_coverage' : 'wc_no_relations')),
      projection.edges.length > 30 ? h('p', {class: 'ui-meta'}, t('attention_loaded', {count: 30})) : null);
    if (focused) (edgeControl(focused) || activeTab())?.focus({preventScroll: true});
    place(live, 'replaceChildren', h('h2', {}, projection.host), h('p', {class: 'ui-meta'}, t('wc_coverage')),
      h('div', {class: 'wc-live-lanes'}, ...['now', 'next', 'waiting'].map(lane => h('section', {class: 'wc-live-lane'},
        h('h3', {}, t('wc_' + lane)),
        lane === 'now' && projection.state === 'running' || lane === 'waiting' && projection.state === 'wait'
          ? card() : h('p', {class: 'ui-meta'}, t(lane === 'next' ? 'wc_queue_unknown' : 'wc_no_live'))))),
      !['running', 'wait'].includes(projection.state) ? card() : null);
    if (liveFocused) live.querySelector('[data-live-work]')?.focus({preventScroll: true});
  }
  return {
    update(data, record) {
      const next = workbenchProjection(data, record), key = JSON.stringify(next);
      if (key === signature) return;
      signature = key; projection = next;
      if (panel) {
        const edge = projection?.edges.find(edge => edge.key === inspected);
        if (!edge) panel.close(); else updateInspector(edge);
      }
      paint();
    },
    fail() {
      projection = null; signature = ''; panel?.close(); goal.textContent = ''; goal.hidden = true;
      responsibility.textContent = '';
      summary.textContent = t('wc_unavailable');
      for (const region of [lineage, live]) region.replaceChildren(h('p', {role: 'status'}, t('wc_unavailable')));
    },
    withdraw() {projection = null; signature = ''; panel?.close(); goal.textContent = ''; goal.hidden = true; responsibility.textContent = ''; summary.textContent = ''; lineage.replaceChildren(); live.replaceChildren(); bar.hidden = true;},
    restore() {bar.hidden = false; select(current);},
    dispose() {panel?.dispose();},
  };
}
