import {icon} from './ui/icons.js';
// Preferences only, never source records; isolated by central connection/principal.
const selections = new Map(), workspaces = new Map();
const aliases = {charter: 'overview', rtm: 'requirements', workspace: 'discussions', planning: 'schedule', governance: 'issues', maintenance: 'dependencies'};
export const projectViewIds = ['overview', 'discussions', 'requirements', 'schedule', 'dependencies', 'issues', 'agents', 'board'];
export const viewIcons = {overview: 'layout-dashboard', discussions: 'message-square', requirements: 'list-checks', schedule: 'calendar-range', dependencies: 'network', issues: 'circle-alert', agents: 'cpu', board: 'kanban'};
export function availableProjectViews(caps) {
  const pm = caps?.features?.pm_workspaces || {};
  return projectViewIds.filter(id => ({overview: pm.brief_snapshots || pm.lifecycle?.version === 1, discussions: pm.version === 1,
    requirements: pm.work_evidence || pm.work_package_prepare || pm.spec_sources || pm.quality_reviews?.version === 1
      || pm.spec_materialization?.version === 1 || pm.package_handoff?.available || pm.issue_mapping
      || pm.engineering_delivery?.contract === 'pm-engineering-delivery/v1' || pm.plan_drafting && pm.kanban && pm.brief_snapshots,
    schedule: pm.roadmap || pm.sprints, dependencies: pm.maintenance_policy?.version === 1 || pm.release_baselines,
    issues: pm.governance || pm.maintenance_repairs?.version === 1 || pm.maintenance_review?.version === 1 || pm.follow_up_prepare,
    agents: caps?.features?.pm_resources || pm.ahp_observation?.contract === 'ahp_observer_v1', board: pm.kanban || pm.work_batches?.version === 1})[id]);
}
const bounded = (map, key, value) => {map.delete(key); map.set(key, value); if (map.size > 40) map.delete(map.keys().next().value);};
export const normalizeProjectView = id => aliases[id] || id;
export function rememberProjectView(namespace, workspaceId, id) {bounded(selections, JSON.stringify([namespace, workspaceId]), normalizeProjectView(id));}
export const lastProjectView = (namespace, workspaceId) => selections.get(JSON.stringify([namespace, workspaceId])) || 'overview';
export function rememberWorkspace(namespace, workspaceId) {bounded(workspaces, namespace, workspaceId);}
export const lastWorkspace = namespace => workspaces.get(namespace);
export function projectViews({h, t, namespace, workspaceId, groups, view, onSelect = () => {}}) {
  const views = groups.map(group => ({...group, id: normalizeProjectView(group.id)})).filter(group => group.sections.length);
  views.sort((a, b) => projectViewIds.indexOf(a.id) - projectViewIds.indexOf(b.id));
  let selected = normalizeProjectView(view || lastProjectView(namespace, workspaceId));
  if (!views.some(group => group.id === selected)) selected = views[0]?.id;
  const buttons = new Map(), element = h('nav', {class: 'pm-project-views', 'aria-label': t('wb_navigation')});
  for (const group of views) {
    const button = h('button', {type: 'button', class: 'ui-button ghost', 'data-pm-local-view': group.id,
      onclick: () => {location.hash = `#/pm/${encodeURIComponent(workspaceId)}/${group.id}`;}}, icon(viewIcons[group.id], {size: 20}), h('span', {}, t('ui_' + group.id)));
    buttons.set(group.id, button); element.append(button);
  }
  function apply() {
    for (const group of views) {
      const current = group.id === selected;
      buttons.get(group.id).setAttribute('aria-current', current ? 'page' : 'false');
      for (const section of group.sections) section.hidden = !current;
    }
  }
  function select(id) {
    id = normalizeProjectView(id); if (!buttons.has(id)) return;
    selected = id; rememberProjectView(namespace, workspaceId, id); apply(); onSelect(id);
  }
  return {element, apply, select, selected: () => selected, views: views.map(group => group.id)};
}
