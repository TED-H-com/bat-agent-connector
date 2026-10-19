import {h} from './ui/dom.js';
import {icon} from './ui/icons.js';
import {viewState} from './ui/view-state.js';
import {viewIcons} from './pm-workbench.js';

// Presentation only: all destinations retain their central IDs and capability gates.
const dailyViews = ['overview', 'discussions', 'board', 'issues'];
export function projectNavigation({t, namespace, workspaceId, views, current, discussionCount}) {
  const preference = viewState(namespace, workspaceId, 'project-navigation');
  const entry = id => h('button', {type: 'button', class: 'ui-button ghost', 'data-pm-view': id,
    'aria-current': current === id ? 'page' : 'false',
    onclick: () => {location.hash = `#/pm/${encodeURIComponent(workspaceId)}/${id}`;}},
  icon(viewIcons[id], {size: 20}), h('span', {class: 'shell-view-copy'},
    h('span', {}, t('ui_' + id)), h('span', {class: 'shell-view-hint'}, t('ui_nav_' + id))),
  id === 'discussions' ? discussionCount : null);
  const primary = dailyViews.filter(id => views.includes(id));
  const secondary = views.filter(id => !dailyViews.includes(id));
  // Reveal a newly selected destination, but respect a deliberate fold while
  // the same page receives background updates.
  if (preference.current !== current && secondary.includes(current)) preference.expanded = true;
  preference.current = current;
  const nav = h('nav', {class: 'pm-project-views', 'aria-label': t('wb_navigation')}, ...primary.map(entry));
  if (secondary.length) {
    const fold = h('details', {class: 'shell-management', 'data-project-management': '',
      open: Boolean(preference.expanded)},
    h('summary', {}, icon('chevron-right', {size: 20}), t('ui_more_management')),
    h('div', {class: 'shell-management-views'}, ...secondary.map(entry)));
    fold.addEventListener('toggle', () => {
      if (fold.isConnected) preference.expanded = fold.open;
    });
    nav.append(fold);
  }
  return nav;
}
