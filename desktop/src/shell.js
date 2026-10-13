import {h} from './ui/dom.js';
import {t} from './i18n.js';
import {button, iconButton, menu, trapFocus} from './ui/components.js';
import {icon} from './ui/icons.js';
import {lastWorkspace, lastProjectView, rememberWorkspace, forgetWorkspace, viewIcons, availableProjectViews} from './pm-workbench.js';

export function dashboardShell({navigation, namespace, caps, api, guard, onEvents, errorBox}) {
  const aside = navigation.box, main = document.getElementById('main'), top = document.querySelector('.top');
  const toggle = document.getElementById('workspace-menu'), crumb = document.getElementById('nav');
  const workspaceBlock = h('div', {class: 'shell-workspace-block'}), projectNav = h('div', {class: 'shell-project-nav'});
  const workTitle = h('div', {class: 'shell-work-header'}, h('h2', {class: 'shell-nav-label'}, t('ui_work')),
    h('a', {class: 'shell-all-projects', href: '#/projects'}, t('ui_all_projects')));
  aside.prepend(workspaceBlock, projectNav, workTitle);
  let workspace = null, workspaces = [], views = [], currentView = '', currentRoute = '', disposed = false, serial = 0;
  let renderSignature = null;
  let menus = [], creator = null, phase = null;
  let drawerOpener = toggle;
  const media = matchMedia('(min-width: 1000px)');
  const scrim = h('div', {class: 'shell-scrim', hidden: true, onclick: () => closeDrawer()});
  aside.before(scrim);
  toggle.replaceChildren(icon('menu', {size: 20})); toggle.setAttribute('aria-label', t('ui_menu')); toggle.title = t('ui_menu');
  toggle.setAttribute('aria-controls', 'workspace-sidebar'); aside.id = 'workspace-sidebar';
  function closeDrawer(restore = true) {
    document.body.classList.remove('workspace-nav-open'); scrim.hidden = true; toggle.setAttribute('aria-expanded', 'false');
    crumb.querySelector('.shell-mobile-name')?.setAttribute('aria-expanded', 'false');
    main.inert = false; document.body.classList.remove('shell-drawer-open');
    aside.removeAttribute('aria-modal'); aside.removeAttribute('role');
    if (restore) drawerOpener?.focus();
  }
  function openDrawer() {
    if (media.matches) return;
    drawerOpener = document.activeElement?.closest('#workspace-menu, .shell-mobile-name') || toggle;
    document.body.classList.add('workspace-nav-open', 'shell-drawer-open'); scrim.hidden = false; toggle.setAttribute('aria-expanded', 'true');
    crumb.querySelector('.shell-mobile-name')?.setAttribute('aria-expanded', 'true');
    aside.setAttribute('role', 'dialog'); aside.setAttribute('aria-modal', 'true'); main.inert = true;
    aside.querySelector('button, a, input')?.focus();
  }
  toggle.onclick = () => scrim.hidden ? openDrawer() : closeDrawer();
  function keydown(event) {if (scrim.hidden) return;
    if (event.key === 'Escape') {event.preventDefault(); closeDrawer();} else trapFocus(aside, event);}
  function resize() {if (media.matches) closeDrawer(false);}
  document.addEventListener('keydown', keydown); media.addEventListener('change', resize);
  aside.addEventListener('click', event => {if (event.target.closest('a, [data-pm-view]') && !scrim.hidden) {closeDrawer(false); main.focus({preventScroll: true});}});
  const countBadge = () => h('span', {class: 'shell-count', hidden: true, 'data-pending-count': ''});
  const pendingCounts = [countBadge(), countBadge()];
  const pending = h('a', {href: '#/home', class: 'ui-button ghost shell-pending', 'aria-label': t('ui_pending'), title: t('ui_pending')}, icon('inbox'), pendingCounts[0]);
  const right = h('div', {class: 'shell-top-actions'}, pending, document.getElementById('live')); top.append(right);
  const tools = menu({button: button({variant: 'ghost', icon: 'wrench', label: t('ui_tools')}), items: [
    ['operations', 'nav_operations'], ['delivery', 'nav_delivery'], ['artifact-review', 'ar_nav'], ['cleanup', 'nav_cleanup'], ['sessions', 'nav_sessions'],
  ].map(([id, key]) => ({label: t(key), href: '#/' + id})).concat(caps()?.features?.pm_workspaces ? [{key: 'create-workspace', label: t('ui_new_workspace'), href: '#/pm/new'}] : [])});
  aside.querySelector('.workspace-nav-footer').replaceChildren(h('a', {href: '#/home', 'data-shell-pending': '', 'aria-label': t('ui_pending')}, icon('inbox'), h('span', {}, t('ui_pending')), pendingCounts[1]), tools,
    h('a', {href: '#/settings'}, icon('settings'), h('span', {}, t('ui_settings'))));
  function switcher(label, className) {
    const trigger = button({variant: 'ghost', label: h('span', {class: 'shell-workspace-name'}, label), icon: 'chevron-down',
      class: className, title: label, 'aria-label': `${t('ui_switch_workspace')}: ${label}`});
    const control = menu({button: trigger, items: [...workspaces.map(row => ({label: row.name, href: `#/pm/${row.workspace_id}`})),
      ...(creator && currentRoute === 'pm' ? [{label: t('ui_new_workspace'), icon: 'plus', onSelect: () => {
        closeDrawer(false); if (!media.matches) toggle.focus(); creator?.();
      }}] : [{label: t('ui_new_workspace'), href: '#/pm/new'}])]});
    menus.push(control); return control;
  }
  function render() {
    const signature = JSON.stringify([workspace, workspaces, views, currentView, currentRoute, phase, threadCount, threadMore]);
    if (signature === renderSignature) return; renderSignature = signature;
    for (const control of menus) control.dispose(); menus = [];
    workspaceBlock.replaceChildren(); projectNav.replaceChildren(); crumb.replaceChildren();
    aside.classList.toggle('has-pm-context', Boolean(workspace)); workTitle.hidden = !workspace;
    const createEntry = tools.querySelector('[data-menu-key=create-workspace]'); if (createEntry) {createEntry.hidden = Boolean(workspace); createEntry.setAttribute('aria-disabled', String(Boolean(workspace)));}
    if (workspace) {
      workspaceBlock.dataset.workspaceId = workspace.workspace_id;
      workspaceBlock.append(switcher(workspace.name, 'shell-workspace-switch'));
      if (phase) workspaceBlock.append(h('span', {class: 'ui-pill tone-neutral', 'data-shell-phase': ''}, phase));
      projectNav.append(h('h2', {class: 'shell-nav-label'}, t('ui_project')),
        h('nav', {class: 'pm-project-views', 'aria-label': t('wb_navigation')}, ...views.map(id => h('button', {type: 'button', class: 'ui-button ghost', 'data-pm-view': id,
          'aria-current': currentRoute === 'pm' && currentView === id ? 'page' : 'false', onclick: () => {location.hash = `#/pm/${workspace.workspace_id}/${id}`;}},
          icon(viewIcons[id], {size: 20}), h('span', {}, t('ui_' + id)), id === 'discussions' && threadCount !== null
            ? h('span', {class: 'shell-count', title: t('ui_threads_loaded', {n: threadCount, more: threadMore ? '+' : ''}), 'aria-label': t('ui_threads_loaded', {n: threadCount, more: threadMore ? '+' : ''})}, String(threadCount) + (threadMore ? '+' : '')) : null))));
      crumb.append(h('a', {class: 'shell-crumb-workspace', href: `#/pm/${encodeURIComponent(workspace.workspace_id)}/overview`, title: workspace.name},
        h('span', {class: 'shell-workspace-name'}, workspace.name)));
    } else {
      delete workspaceBlock.dataset.workspaceId;

    }
    const routeKey = {pm: 'ui_' + (currentView || 'discussions'), home: 'ui_pending', settings: 'ui_settings',
      operations: 'nav_operations', delivery: 'nav_delivery', cleanup: 'nav_cleanup', 'artifact-review': 'ar_nav',
      projects: 'ui_all_projects', project: 'ui_project', sessions: 'nav_sessions', session: 'nav_sessions',
      item: 'ui_work_item', work: 'workspace_work_details', approvals: 'bulk_title', start: 'start_title_page',
      published: 'pub_title', orchestrate: 'orch_title', op: 'link_operation', host: 'host', task: 'task_title',
      worktree: 'obs_worktree', dispatch: 'dispatch_title'}[currentRoute];
    const routeTitle = routeKey ? t(routeKey) : '';
    if (routeTitle) crumb.append(h('span', {class: 'shell-breadcrumb-divider', 'aria-hidden': 'true'}, '/'), h('span', {class: 'shell-crumb-view'}, routeTitle));
    crumb.append(h('button', {type: 'button', class: 'shell-mobile-name', title: workspace?.name || routeTitle,
      'aria-label': workspace?.name || routeTitle || t('ui_menu'), 'aria-controls': 'workspace-sidebar', 'aria-expanded': 'false', onclick: openDrawer}, workspace?.name || routeTitle));
    workspaceBlock.hidden = !workspaceBlock.children.length; projectNav.hidden = !projectNav.children.length;

  }
  let threadCount = null, threadMore = false;
  function update(context) {
    if (disposed) return;
    workspace = context.workspace; workspaces = context.workspaces || workspaces; views = context.views || (views.length ? views : availableProjectViews(caps()));
    currentView = context.view || currentView || 'overview'; creator = context.create || creator;
    if (Object.hasOwn(context, 'phase')) phase = context.phase;
    if (Object.hasOwn(context, 'threadCount')) {threadCount = context.threadCount; threadMore = context.threadMore;}
    if (workspace) rememberWorkspace(namespace, workspace.workspace_id);
    navigation.setWorkspace(workspace?.workspace_id || null, Boolean(caps()?.features?.pm_workspaces?.work_evidence), currentRoute === 'projects'); render();
  }
  async function refresh() {
    if (!caps()?.features?.pm_workspaces || currentRoute === 'pm') return;
    const ticket = ++serial;
    try {
      const list = await api('GET', '/pm/workspaces'); guard(); if (disposed || serial !== ticket) return;
      const id = workspace?.workspace_id || lastWorkspace(namespace);
      const selected = list.workspaces?.find(row => row.workspace_id === id);
      if (!selected) {forgetWorkspace(namespace, id); update({workspace: null, workspaces: list.workspaces || [], threadCount: null, phase: null}); return;}
      // A fresh shell only stores the selected ID. Access and labels are reread centrally.
      const data = await api('GET', `/pm/workspaces/${id}`); guard(); if (disposed || serial !== ticket) return;
      if (data.workspace?.workspace_id !== id) throw new Error(t('pm_scope_mismatch'));
      update({workspace: data.workspace, workspaces: list.workspaces, view: lastProjectView(namespace, id)});
    } catch (error) {if (disposed || serial !== ticket) return; if ([403,404].includes(error.status)) forgetWorkspace(namespace, workspace?.workspace_id || lastWorkspace(namespace)); update({workspace: null, workspaces: [], threadCount: null, phase: null});}
  }
  const off = onEvents(event => {
    if (['session','work_item','operation','host'].includes(event.resource_type)) {for (const badge of pendingCounts) {badge.hidden = true; badge.textContent = '';}}
    return ['pm_workspace', 'pm_lifecycle'].includes(event.resource_type) ? refresh() : undefined;
  });
  render();
  return {update, refresh, setRoute(name) {currentRoute = name; serial++; closeDrawer(false); navigation.setWorkspace(workspace?.workspace_id || null, Boolean(caps()?.features?.pm_workspaces?.work_evidence), name === 'projects'); render();},
    pending({count, partial} = {}) {for (const badge of pendingCounts) {
      const known = Number.isSafeInteger(count) && count > 0;
      badge.hidden = !known; badge.textContent = known ? String(count) + (partial ? '+' : '') : '';
      badge.title = known ? t('ui_pending_loaded', {n: count, more: partial ? '+' : ''}) : '';
    }},
    withdraw() {serial++; update({workspace: null, workspaces: [], threadCount: null, phase: null});},
    dispose() {disposed = true; serial++; off(); menus.forEach(control => control.dispose()); tools.dispose(); closeDrawer(false);
      document.removeEventListener('keydown', keydown); media.removeEventListener('change', resize); scrim.remove();
      // The static connection control belongs to the application, not this namespace.
      top.append(document.getElementById('live')); right.remove(); creator = null;},
  };
}
