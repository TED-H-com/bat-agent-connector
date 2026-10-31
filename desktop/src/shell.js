import {layoutDecision} from './workbench-mode.js';
import {h} from './ui/dom.js';
import {t} from './i18n.js';
import {button, iconButton, menu, trapFocus} from './ui/components.js';
import {icon} from './ui/icons.js';
import {lastWorkspace, lastProjectView, rememberWorkspace, forgetWorkspace, availableProjectViews} from './pm-workbench.js';
import {projectNavigation} from './project-navigation.js';
import {setupSidebarResizer, calcSidebarBounds} from './workspace-layout.js';

export function dashboardShell({navigation, namespace, caps, api, guard, onEvents, errorBox}) {
  const candidate = layoutDecision.layout === 'workbench';
  Object.assign(document.body.dataset, {layout: layoutDecision.layout, layoutSource: layoutDecision.source,
    layoutStatus: layoutDecision.status, layoutReason: layoutDecision.reason || ''});
  const aside = navigation.box, main = document.getElementById('main'), top = document.querySelector('.top');
  const toggle = document.getElementById('workspace-menu'), crumb = document.getElementById('nav');
  const workspaceBlock = h('div', {class: 'shell-workspace-block'}), projectNav = h('div', {class: 'shell-project-nav'});
  const workTitle = h('div', {class: 'shell-work-header'}, h('h2', {class: 'shell-nav-label'}, t('ui_work')),
    h('a', {class: 'shell-all-projects', href: '#/projects'}, t('ui_all_projects')));
  const daily = h('nav', {class: 'wc-daily', 'aria-label': t('wc_candidate'), hidden: !candidate});
  const contextList = h('section', {class: 'wc-context-list', 'aria-label': t('wc_list'), hidden: true});
  const listLink = h('a', {class: 'wc-list-back ui-button ghost', href: '#/work-list', title: t('wc_list'), 'aria-label': t('wc_list')}, icon('chevron-left'));
  const listTitle = h('h1', {class: 'wc-list-title', tabindex: -1}, t('wc_list'));
  const boardLink = h('a', {class: 'ui-button ghost', hidden: true}, t('wc_board'));
  const listHeader = h('header', {class: 'wc-list-header'}, listTitle, boardLink);
  const listScroll = {page: 0, rail: 0};
  let listPlacement = null, listResizer = null;
  contextList.addEventListener('scroll', () => {
    if (listPlacement && contextList.getClientRects().length) listScroll[listPlacement] = contextList.scrollTop;
  });
  const contextNodes = candidate ? [workTitle, ...aside.querySelectorAll(':scope > .workspace-nav-heading, :scope > .workspace-search, :scope > .workspace-tree-status, :scope > .workspace-binding-note, :scope > .workspace-tree-scroll')] : [];
  aside.prepend(workspaceBlock, daily, projectNav, workTitle);
  if (candidate) {aside.after(contextList); contextList.append(listHeader); toggle.after(listLink); document.body.classList.add('workbench-preview');}
  function locateContext() {
    if (!candidate) return;
    const listing = currentRoute === 'work-list';
    const show = listing || ['session', 'item', 'work', 'sessions'].includes(currentRoute);
    const placement = listing ? 'page' : show && media.matches ? 'rail' : null;
    if (listPlacement && contextList.getClientRects().length) listScroll[listPlacement] = contextList.scrollTop;
    contextList.hidden = !listing && (!show || !media.matches);
    contextList.setAttribute('role', listing ? 'main' : 'region');
    listHeader.hidden = !listing;
    document.body.classList.toggle('wc-list-page', listing);
    listLink.hidden = !['session', 'item', 'work'].includes(currentRoute);
    const heading = contextNodes.find(node => node.matches('.workspace-nav-heading'));
    if (heading) heading.querySelector('strong').textContent = t('wc_work');
    document.body.classList.toggle('wc-has-list', show);
    // Move the existing source controls once; reflow never copies a search or event reader.
    for (const node of contextNodes) if (node.parentElement !== contextList) contextList.append(node);
    if (placement !== listPlacement && placement) contextList.scrollTop = listScroll[placement];
    listPlacement = placement;
    if (listResizer) {listResizer.resizer.hidden = placement !== 'rail'; listResizer.refresh();}
    const workEntry = daily.querySelector('[data-daily=work]');
    if (workEntry) workEntry.href = !media.matches ? '#/work-list' : workEntry.dataset.desktopHref;
  }
  let workspace = null, workspaces = [], views = [], currentView = '', currentRoute = '', disposed = false, serial = 0;
  let renderSignature = null;
  let menus = [], creator = null, phase = null;
  let drawerOpener = toggle;
  const media = matchMedia('(min-width: 1000px)');
  if (candidate) {
    const workspaceElement = document.getElementById('workspace');
    const defaultWidth = () => window.innerWidth < 1280 ? 220 : 256;
    listResizer = setupSidebarResizer({aside: contextList, workspace: workspaceElement, t, layout: {
      storageKey: 'batc.workbench.list.width', property: '--wc-list-width', defaultWidth, resetWidth: defaultWidth,
      label: 'wc_list_resizer', help: 'wc_list_resizer_help', enabled: () => listPlacement === 'rail',
      bounds: () => calcSidebarBounds(workspaceElement.clientWidth - aside.getBoundingClientRect().width - 8),
    }});
    listResizer.resizer.classList.add('wc-list-resizer');
    listResizer.resizer.setAttribute('aria-controls', 'workbench-context-list');
    contextList.id = 'workbench-context-list';
    listResizer.resizer.hidden = true;
    contextList.after(listResizer.resizer);
  }
  const scrim = h('div', {class: 'shell-scrim', hidden: true, onclick: () => closeDrawer()});
  aside.before(scrim);
  toggle.replaceChildren(icon('menu', {size: 20})); toggle.setAttribute('aria-label', t('ui_menu')); toggle.title = t('ui_menu');
  toggle.setAttribute('aria-controls', 'workspace-sidebar'); aside.id = 'workspace-sidebar';
  function closeDrawer(restore = true) {
    document.body.classList.remove('workspace-nav-open'); scrim.hidden = true; toggle.setAttribute('aria-expanded', 'false');
    crumb.querySelector('.shell-mobile-name')?.setAttribute('aria-expanded', 'false');
    main.inert = false; contextList.inert = false; document.body.classList.remove('shell-drawer-open');
    aside.removeAttribute('aria-modal'); aside.removeAttribute('role');
    if (restore) drawerOpener?.focus();
  }
  function openDrawer() {
    if (media.matches) return;
    drawerOpener = document.activeElement?.closest('#workspace-menu, .shell-mobile-name') || toggle;
    document.body.classList.add('workspace-nav-open', 'shell-drawer-open'); scrim.hidden = false; toggle.setAttribute('aria-expanded', 'true');
    crumb.querySelector('.shell-mobile-name')?.setAttribute('aria-expanded', 'true');
    aside.setAttribute('role', 'dialog'); aside.setAttribute('aria-modal', 'true'); main.inert = true; contextList.inert = true;
    aside.querySelector('button, a, input')?.focus();
  }
  toggle.onclick = () => scrim.hidden ? openDrawer() : closeDrawer();
  function keydown(event) {if (scrim.hidden) return;
    // Capture composing Escape before the legacy navigation's bubbling listener.
    if (event.key === 'Escape' && (event.isComposing || event.keyCode === 229)) {event.stopPropagation(); return;}
    if (event.key === 'Escape' && !event.isComposing && event.keyCode !== 229) {event.preventDefault(); closeDrawer();} else trapFocus(aside, event);}
  function resize() {if (media.matches) closeDrawer(false); locateContext();}
  document.addEventListener('keydown', keydown, true); media.addEventListener('change', resize);
  aside.addEventListener('click', event => {if (event.target.closest('a, [data-pm-view]') && !scrim.hidden) {closeDrawer(false); main.focus({preventScroll: true});}});
  const countBadge = () => h('span', {class: 'shell-count', hidden: true, 'data-pending-count': ''});
  const pendingCounts = [countBadge(), countBadge()];
  const pending = h('a', {href: '#/home', class: 'ui-button ghost shell-pending', 'aria-label': t('ui_pending'), title: t('ui_pending')}, icon('inbox'), pendingCounts[0]);
  const right = h('div', {class: 'shell-top-actions'}, pending, document.getElementById('central-service-status')); top.append(right);
  const layoutUrl = new URL(location.href); layoutUrl.searchParams.set('workbench', candidate ? '0' : '1');
  const tools = menu({button: button({variant: 'ghost', icon: 'wrench', label: t('ui_tools'), 'data-shell-tools':''}), items: [
    ['operations', 'nav_operations'], ['delivery', 'nav_delivery'], ['artifact-review', 'ar_nav'], ['cleanup', 'nav_cleanup'], ['sessions', 'nav_sessions'],
  ].map(([id, key]) => ({label: t(key), href: '#/' + id})).concat([{key: 'workbench', label: t(candidate ? 'wc_classic' : 'wc_candidate'), href: layoutUrl.href}]).concat(caps()?.features?.pm_workspaces ? [{key: 'create-workspace', label: t('ui_new_workspace'), href: '#/pm/new'}] : [])});
  tools.querySelector(':scope > button').append(icon('chevron-down'));
  aside.querySelector('.workspace-nav-footer').replaceChildren(h('a', {href: '#/home', 'data-shell-pending': '', 'aria-label': t('ui_pending')}, icon('inbox'), h('span', {}, t('ui_pending')), pendingCounts[1]), tools,
    h('a', {href: '#/settings', 'data-shell-settings':''}, icon('settings'), h('span', {}, t('ui_settings'))));
  function switcher(label, className) {
    const trigger = button({variant: 'ghost', label: h('span', {class: 'shell-workspace-name'}, label), icon: 'chevron-down', iconPosition:'end',
      class: className, title: label, 'aria-label': `${t('ui_switch_workspace')}: ${label}`});
    const control = menu({button: trigger, items: [...workspaces.map(row => ({label: row.name, href: `#/pm/${row.workspace_id}`})),
      ...(creator && currentRoute === 'pm' ? [{label: t('ui_new_workspace'), icon: 'plus', onSelect: () => {
        closeDrawer(false); if (!media.matches) toggle.focus(); creator?.();
      }}] : [{label: t('ui_new_workspace'), href: '#/pm/new'}]),
      ...(candidate ? [{label: t('ui_all_projects'), href: '#/projects'}] : [])]});
    menus.push(control); return control;
  }
  function render() {
    layoutUrl.hash = location.hash; tools.querySelector('[data-menu-key=workbench]').href = layoutUrl.href;
    const signature = JSON.stringify([workspace, workspaces, views, currentView, currentRoute, phase, threadCount, threadMore]);
    if (signature === renderSignature) return; renderSignature = signature;
    for (const control of menus) control.dispose(); menus = [];
    const focusedView = projectNav.contains(document.activeElement) ? document.activeElement.dataset.pmView : null;
    const focusedManagement = projectNav.contains(document.activeElement) && document.activeElement.matches('summary');
    workspaceBlock.replaceChildren(); projectNav.replaceChildren(); crumb.replaceChildren();
    if (candidate) {
      const project = workspace ? `#/pm/${encodeURIComponent(workspace.workspace_id)}/` : '';
      const current = currentRoute === 'home' ? 'today' : ['session','item','work','sessions','work-list'].includes(currentRoute) || currentRoute === 'pm' && currentView === 'board' ? 'work' : currentRoute === 'pm' && currentView === 'agents' ? 'resources' : ['pm','project','projects'].includes(currentRoute) ? 'projects' : '';
      daily.replaceChildren(...[
        ['today', 'layout-dashboard', '#/home'],
        ['projects', 'folder', project ? project + 'overview' : '#/projects'],
        ['work', 'list-checks', project && views.includes('board') ? project + 'board' : '#/sessions'],
        ['resources', 'cpu', project && views.includes('agents') ? project + 'agents' : '#/settings?section=connection'],
      ].map(([id, glyph, href]) => h('a', {class: 'ui-button ghost', href, 'data-desktop-href': href, 'data-daily': id, 'aria-current': id === current ? 'page' : 'false'}, icon(glyph), t('wc_' + id))));
      boardLink.hidden = !project || !views.includes('board');
      boardLink.href = project + 'board';
      locateContext();
    }
    aside.classList.toggle('has-pm-context', Boolean(workspace)); workTitle.hidden = !workspace;
    const createEntry = tools.querySelector('[data-menu-key=create-workspace]'); if (createEntry) {createEntry.hidden = Boolean(workspace); createEntry.setAttribute('aria-disabled', String(Boolean(workspace)));}
    if (workspace) {
      workspaceBlock.dataset.workspaceId = workspace.workspace_id;
      workspaceBlock.append(switcher(workspace.name, 'shell-workspace-switch'));
      if (phase) workspaceBlock.append(h('span', {class: 'ui-pill tone-neutral', 'data-shell-phase': ''}, phase));
      projectNav.append(h('h2', {class: 'shell-nav-label'}, t('ui_daily_navigation')),
        projectNavigation({t, namespace, workspaceId: workspace.workspace_id, views,
          current: currentRoute === 'pm' ? currentView : '',
          discussionCount: threadCount !== null
            ? h('span', {class: 'shell-count', title: t(threadMore ? 'ui_threads_loaded' : threadCount === 1 ? 'ui_thread_count' : 'ui_threads_count', {n: threadCount, more: threadMore ? '+' : ''}), 'aria-label': t(threadMore ? 'ui_threads_loaded' : threadCount === 1 ? 'ui_thread_count' : 'ui_threads_count', {n: threadCount, more: threadMore ? '+' : ''})}, String(threadCount) + (threadMore ? '+' : '')) : null}));
      crumb.append(h('a', {class: 'shell-crumb-workspace', href: `#/pm/${encodeURIComponent(workspace.workspace_id)}/overview`, title: workspace.name},
        h('span', {class: 'shell-workspace-name'}, workspace.name)));
    } else {
      delete workspaceBlock.dataset.workspaceId;

    }
    for (const link of aside.querySelectorAll('.workspace-nav-footer > a')) link.setAttribute('aria-current', link.getAttribute('href') === '#/' + currentRoute ? 'page' : 'false');
    tools.querySelector(':scope > button').setAttribute('aria-current', ['operations','op','delivery','artifact-review','cleanup','sessions'].includes(currentRoute) ? 'page' : 'false');
    pending.setAttribute('aria-current', currentRoute === 'home' ? 'page' : 'false');
    const routeKey = {pm: 'ui_' + (currentView || 'discussions'), home: 'ui_pending', settings: 'ui_settings',
      operations: 'nav_operations', delivery: 'nav_delivery', cleanup: 'nav_cleanup', 'artifact-review': 'ar_nav',
      projects: 'ui_all_projects', project: 'ui_project', sessions: 'nav_sessions', session: 'nav_sessions',
      item: 'ui_work_item', work: 'workspace_work_details', approvals: 'bulk_title', start: 'start_title_page',
      published: 'pub_title', orchestrate: 'orch_title', op: 'link_operation', host: 'host', task: 'task_title',
      worktree: 'obs_worktree', dispatch: 'dispatch_title'}[currentRoute];
    const routeTitle = candidate && currentRoute === 'work-list' ? t('wc_list') : candidate && currentRoute === 'home' ? t('wc_today') : routeKey ? t(routeKey) : '';
    const workSession = workspace && ['session', 'item', 'work'].includes(currentRoute);
    if (workSession) crumb.append(h('span', {class: 'shell-breadcrumb-divider', 'aria-hidden': 'true'}, '/'), h('span', {class: 'shell-crumb-view'}, t('ui_work')),
      h('span', {class: 'shell-breadcrumb-divider', 'aria-hidden': 'true'}, '/'), h('span', {class: 'shell-crumb-view'}, t('ui_session')));
    else if (routeTitle) {
      if (workspace) crumb.append(h('span', {class: 'shell-breadcrumb-divider', 'aria-hidden': 'true'}, '/'));
      crumb.append(h('span', {class: 'shell-crumb-view'}, routeTitle));
    }
    crumb.append(h('button', {type: 'button', class: 'shell-mobile-name', title: workspace?.name || routeTitle,
      'aria-label': workspace?.name || routeTitle || t('ui_menu'), 'aria-controls': 'workspace-sidebar', 'aria-expanded': 'false', onclick: openDrawer}, workspace?.name || routeTitle));
    workspaceBlock.hidden = !workspaceBlock.children.length; projectNav.hidden = candidate || !projectNav.children.length;
    if (!projectNav.hidden && (media.matches || !scrim.hidden)) {
      const focus = focusedView ? [...projectNav.querySelectorAll('[data-pm-view]')].find(node => node.dataset.pmView === focusedView)
        : focusedManagement ? projectNav.querySelector('summary') : null;
      if (focus?.getClientRects().length) focus.focus({preventScroll: true});
    }

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
  return {update, refresh, setRoute(name) {currentRoute = name;
      if (name === 'pm') {const routeView = location.hash.split('?')[0].split('/')[3]; currentView = routeView === 'discussions' ? 'discussions' : availableProjectViews(caps()).includes(routeView) ? routeView : lastProjectView(namespace, location.hash.split('/')[2]);}
      serial++; closeDrawer(false); navigation.setWorkspace(workspace?.workspace_id || null, Boolean(caps()?.features?.pm_workspaces?.work_evidence), name === 'projects'); render();
      // The first route mounts before has-workspace enables the final grid.
      listResizer?.refresh();
      if (candidate && name === 'work-list') listTitle.focus({preventScroll: true});},
    pending({count, partial} = {}) {for (const badge of pendingCounts) {
      const known = Number.isSafeInteger(count) && count > 0;
      badge.hidden = !known; badge.textContent = known ? String(count) + (partial ? '+' : '') : '';
      badge.title = known ? t(partial ? 'ui_pending_loaded' : 'ui_pending_count', {n: count, more: partial ? '+' : ''}) : '';
    }},
    withdraw() {serial++; update({workspace: null, workspaces: [], threadCount: null, phase: null});},
    dispose() {disposed = true; serial++; off(); menus.forEach(control => control.dispose()); tools.dispose(); closeDrawer(false);
      listResizer?.dispose();
      document.removeEventListener('keydown', keydown, true); media.removeEventListener('change', resize); scrim.remove();
      // The static connection control belongs to the application, not this namespace.
      top.append(document.getElementById('central-service-status')); right.remove(); listLink.remove(); creator = null; contextList.remove(); document.body.classList.remove('workbench-preview', 'wc-has-list', 'wc-list-page');},
  };
}
