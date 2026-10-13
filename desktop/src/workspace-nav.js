// Persistent navigation over central IDs. Workspace labels never assign a project.
import {groupedSessions, sessionActivity} from './state/sessions.js';
import {icon} from './ui/icons.js';
import {iconButton} from './ui/components.js';

export function workspaceNavigation({h, t, api, guard, onEvents, namespace, errorBox}) {
  const key = `batc.tree.${namespace}`;
  let saved = [], hasPreference = false;
  try {const raw = sessionStorage.getItem(key); saved = JSON.parse(raw || '[]'); hasPreference = raw !== null;} catch { /* local preference only */ }
  const expanded = new Set(Array.isArray(saved) ? saved : []), projects = new Map();
  let pmWorkspace = null, bindingSource = false, bindingKnown = false, globalTree = false, contextVersion = 0;
  let roots = [], disposed = false, serial = Promise.resolve(), selected = location.hash, revealProject = null;
  const status = h('div', {class: 'workspace-tree-status', role: 'status'});
  const tree = h('div', {class: 'workspace-tree'});
  let sessionPages = 1, sessionsLoaded = false;
  const sessionRows = h('div', {class: 'workspace-tree'});
  const more = h('button', {class: 'mini', hidden: true, onclick: () => {sessionPages++; refresh().catch(() => {});}}, t('load_more'));
  const sessionTree = h('details', {class: 'workspace-session-tree'},
    h('summary', {}, icon('chevron-right', {size: 20}), h('span', {}, t('nav_sessions'))), sessionRows, more);
  const search = h('input', {type: 'search', 'aria-label': t('workspace_search'), placeholder: t('ui_search_work')});
  const box = h('aside', {class: 'workspace-nav', 'aria-label': t('workspace_navigation')},
    h('div', {class: 'workspace-nav-heading'}, h('strong', {}, t('nav_projects')),
      h('a', {href: '#/projects', title: t('workspace_manage'), 'aria-label': t('workspace_manage')}, '+')),
    h('div', {class: 'workspace-search'}, icon('search', {size: 16}), search), status, h('div', {class: 'workspace-tree-scroll'}, tree, sessionTree),
    h('div', {class: 'workspace-nav-footer'},
      h('a', {href: '#/sessions'}, t('workspace_all_sessions')),
      h('a', {href: '#/projects'}, t('workspace_manage'))));
  const alive = () => {guard(); if (disposed) throw new Error('Navigation disposed');};
  const persist = () => {try {sessionStorage.setItem(key, JSON.stringify([...expanded]));} catch { /* optional */ }};
  const link = (href, label, state = null, reading = null) => h('a', {href, class: 'workspace-tree-link', 'data-tree-key': href,
    'aria-current': selected === href ? 'page' : null},
    h('span', {class: `workspace-dot ${state?.tone || ''}`, 'aria-hidden': 'true'}),
    h('span', {class: 'workspace-tree-label'}, label),
    state ? h('span', {class: 'workspace-tree-state'}, t(state.key)) : null,
    reading?.unread_count > 0 ? h('span', {class: 'workspace-tree-state', title: t('conversation_count_note')},
      t('conversation_badge', {count: reading.unread_count}) + (reading.complete ? '' : '+')) : null);
  const workLink = (pid, item) => link(`#/work/${[pid, item.kind, item.id].map(encodeURIComponent).join('/')}`,
    item.title || item.branch || item.action || item.id, sessionActivity(item.session || {}), item.reading);
  const render = () => {
    if (disposed) return;
    const focus = tree.contains(document.activeElement) ? document.activeElement?.dataset.treeKey : null;
    const query = search.value.trim().toLocaleLowerCase();
    const matches = text => !query || String(text || '').toLocaleLowerCase().includes(query);
    const itemNodes = items => (items || []).flatMap(item => {
      const children = itemNodes(item.children), own = matches(item.title);
      if (!own && !children.length) return [];
      const row = link(`#/item/${encodeURIComponent(item.work_item_id)}`, item.title,
        {key: 'wi_state_' + (item.completion?.display_state || item.state), tone: item.completion?.pending ? 'stale' : ''});
      return [h('li', {}, row, children.length ? h('ul', {}, ...children) : null)];
    });
    const projectNodes = items => items.flatMap(project => {
      const id = project.project_id, data = projects.get(id), children = projectNodes(project.children || []);
      const work = (data?.work || []).filter(item => matches(item.title || item.branch || item.action || item.id));
      const items = itemNodes(data?.work_items), own = matches(project.name);
      if (!own && !children.length && !work.length && !items.length) return [];
      const open = expanded.has(id) || Boolean(query);
      const toggle = h('button', {class: 'workspace-tree-toggle', 'data-tree-key': id,
        'aria-label': t(open ? 'workspace_collapse' : 'workspace_expand', {name: project.name}),
        'aria-expanded': String(open), onclick: () => {
          if (expanded.has(id)) expanded.delete(id); else expanded.add(id);
          persist(); render(); if (expanded.has(id)) refresh().catch(() => {});
        }}, open ? '▾' : '▸');
      return [h('li', {}, h('div', {class: 'workspace-project-row'}, toggle,
        link(`#/project/${encodeURIComponent(id)}`, project.name),
        project.counts?.pending ? h('span', {class: 'workspace-tree-state'}, t('workspace_needs_you')) : null),
        h('ul', {hidden: !open}, ...children, ...items, ...work.map(item => h('li', {}, workLink(id, item))),
          !data ? h('li', {class: 'muted workspace-tree-empty'}, t('workspace_expand_load'))
            : !children.length && !items.length && !work.length ? h('li', {class: 'muted workspace-tree-empty'}, t('workspace_no_work')) : null))];
    });
    const nodes = projectNodes(roots);
    tree.replaceChildren(h('ul', {}, ...nodes));
    const bound = pmWorkspace && !globalTree;
    tree.dataset.bindingState = bound ? bindingKnown ? roots.length ? 'available' : 'empty' : 'unknown' : 'global';
    // No permanent empty/unavailable sentence in the rail. A search still explains
    // whether its missing results are known empty or cannot currently be read.
    if (!nodes.length && (query || !bound)) tree.append(h('p', {class: 'muted workspace-tree-hint'},
      t(bound && !bindingKnown ? 'ui_work_links_unavailable' : query ? 'workspace_no_match' : 'no_projects')));
    if (focus) [...tree.querySelectorAll('[data-tree-key]')].find(node => node.dataset.treeKey === focus)?.focus({preventScroll: true});
    filterSessions();
  };
  const filterSessions = () => {
    const query = search.value.trim().toLocaleLowerCase();
    for (const group of sessionRows.querySelectorAll(':scope > div')) {
      for (const row of group.querySelectorAll('a')) row.hidden = Boolean(query) && !group.querySelector('p').textContent.toLocaleLowerCase().includes(query)
        && !row.textContent.toLocaleLowerCase().includes(query);
      group.hidden = [...group.querySelectorAll('a')].every(row => row.hidden);
    }
  };
  const refresh = () => {
    const version = contextVersion;
    const run = serial.catch(() => {}).then(async () => {
      const current = () => {alive(); if (version !== contextVersion) throw new Error('Navigation context changed');};
      current();
      try {
        const data = await api('GET', '/projects'); current();
        let nextRoots = data.projects || [], nextBindingKnown = false;
        if (pmWorkspace && !globalTree) {
          const ids = new Set();
          if (bindingSource) {
            let after = 0;
            for (let page = 0; page < 20; page++) {
              const data = await api('GET', `/pm/workspaces/${encodeURIComponent(pmWorkspace)}/projects?after=${after}&limit=100`); current();
              if (data.workspace_id !== pmWorkspace || !Array.isArray(data.bindings)) throw Error(t('pm_scope_mismatch'));
              for (const binding of data.bindings) {
                if (binding.workspace_id !== pmWorkspace) throw Error(t('pm_scope_mismatch'));
                ids.add(binding.project_id);
              }
              if (data.next_cursor == null) break;
              if (!Number.isSafeInteger(data.next_cursor) || data.next_cursor <= after || page === 19) throw Error(t('pm_partial'));
              after = data.next_cursor;
            }
          }
          // A parent binding grants no inferred binding to its children.
          const flatten = rows => rows.flatMap(row => [row, ...flatten(row.children || [])]);
          nextRoots = flatten(nextRoots).filter(row => ids.has(row.project_id)).map(row => ({...row, children: []}));
          // Bindings can remain readable after the project catalog is withdrawn.
          // An inaccessible linked project is not an empty binding inventory.
          nextBindingKnown = bindingSource && (ids.size === 0 || nextRoots.length > 0);
        }
        current(); roots = nextRoots; bindingKnown = nextBindingKnown;
        if (!roots.length) projects.clear();
        if (!hasPreference && !expanded.size && roots.length) {expanded.add(roots[0].project_id); hasPreference = true; persist();}
        const ids = new Set();
        const walk = (list, ancestors = []) => {for (const p of list) {
          ids.add(p.project_id);
          // Reveal ancestors only when navigating to a project. Remembered child
          // expansion must not reopen a parent the user deliberately collapsed.
          if (p.project_id === revealProject) [...ancestors, p.project_id].forEach(id => expanded.add(id));
          walk(p.children || [], [...ancestors, p.project_id]);
        }};
        walk(roots);
        if (revealProject !== null) {revealProject = null; persist();}
        // A hidden project can change while collapsed. Invalidate it instead of
        // making its old activity appear current when searching or reopening it.
        for (const id of projects.keys()) if (!ids.has(id) || !expanded.has(id)) projects.delete(id);
        // Lazy project reads bound work to expanded projects, not every session on every host.
        for (const id of expanded) if (ids.has(id)) {
          const detail = await api('GET', `/projects/${encodeURIComponent(id)}`); current(); projects.set(id, detail);
        }
        if (!roots.length && !sessionsLoaded && ['#/home', '#/sessions', '#/', ''].includes(selected)) sessionTree.open = true;
        if (sessionTree.open && (!pmWorkspace || globalTree)) {
          const rows = new Map(), cursors = new Set();
          let cursor = null;
          for (let page = 0; page < sessionPages; page++) {
            const data = await api('GET', '/sessions?order=id&include_gone=true&limit=50' + (cursor ? `&cursor=${encodeURIComponent(cursor)}` : '')); current();
            for (const session of data.sessions || []) rows.set(JSON.stringify([session.host, session.session_id]), session);
            cursor = data.next_cursor;
            if (!cursor) break;
            if (cursors.has(cursor)) throw new Error(t('attention_invalid'));
            cursors.add(cursor);
          }
          const focused = sessionRows.contains(document.activeElement) ? document.activeElement.getAttribute('href') : null;
          sessionRows.replaceChildren(...groupedSessions([...rows.values()]).map(group => h('div', {},
            h('p', {class: 'muted workspace-session-group'}, group.host, ' · ', group.name || group.id || t('obs_unknown')),
            ...group.sessions.map(session => link(`#/session/${[session.host, session.session_id].map(encodeURIComponent).join('/')}`,
              session.title || session.session_id, sessionActivity(session), session.reading)))),
            h('p', {class: 'muted'}, t('attention_loaded', {count: rows.size})));
          if (focused) [...sessionRows.querySelectorAll('a')].find(a => a.getAttribute('href') === focused)?.focus({preventScroll:true});
          more.hidden = !cursor; sessionsLoaded = true; filterSessions();
        }
        status.replaceChildren(); render();
      } catch (error) {
        if (version !== contextVersion || disposed) return;
        alive(); if ([403, 404].includes(error.status)) {roots = []; bindingKnown = false; projects.clear(); sessionRows.replaceChildren(); more.hidden = true; render();}
        // Keep the exact error available on demand, including refresh-cycle reporting.
        const detail = errorBox(error), label = t(pmWorkspace && !globalTree ? 'ui_work_links_unavailable' : 'ui_work_unavailable');
        status.replaceChildren(h('div', {class: 'workspace-read-error', 'data-work-read-error': ''},
          icon('circle-alert', {size: 20}),
          h('details', {}, h('summary', {title: detail.textContent}, label), detail,
            roots.length ? h('p', {class: 'muted'}, t('workspace_stale')) : null),
          iconButton({icon: 'refresh-cw', label: t('ui_retry'), onClick: () => refresh().catch(() => {})})));
        throw error;
      }
    });
    serial = run; return run;
  };
  search.addEventListener('input', render);
  sessionTree.addEventListener('toggle', () => {if (sessionTree.open && !sessionsLoaded) refresh().catch(() => {});});
  box.addEventListener('click', event => {
    if (event.target.closest('a')) {
      const mobileOpen = document.body.classList.contains('workspace-nav-open');
      document.body.classList.remove('workspace-nav-open');
      document.getElementById('workspace-menu')?.setAttribute('aria-expanded', 'false');
      if (mobileOpen) document.getElementById('main')?.focus({preventScroll: true});
    }
  });
  const escape = event => {
    if (event.key === 'Escape' && document.body.classList.contains('workspace-nav-open')) {
      document.body.classList.remove('workspace-nav-open');
      const menu = document.getElementById('workspace-menu');
      menu?.setAttribute('aria-expanded', 'false'); menu?.focus();
    }
  };
  document.addEventListener('keydown', escape);
  const off = onEvents(event => ['project', 'work_item', 'session', 'execution', 'task', 'operation', 'integration', 'pm_workspace', 'pm_evidence'].includes(event.resource_type)
    ? refresh() : undefined);
  refresh().catch(() => {});
  return {box, setWorkspace(id, source, all = false) {
    if (pmWorkspace === id && bindingSource === source && globalTree === all) return;
    pmWorkspace = id; bindingSource = source; globalTree = all; contextVersion++;
    roots = []; bindingKnown = false; projects.clear(); sessionRows.replaceChildren(); sessionsLoaded = false; status.replaceChildren();
    sessionTree.hidden = Boolean(id) && !all; box.querySelector('.workspace-nav-heading').hidden = Boolean(id) && !all;
    render(); refresh().catch(() => {});
  }, select(hash) {
    selected = hash;
    const [type, pid] = hash.replace(/^#\//, '').split('/');
    if (['project', 'dispatch', 'work'].includes(type) && pid) {
      revealProject = decodeURIComponent(pid); refresh().catch(() => {});
    }
    // Selection updates do not remount the tree, search or disclosure controls.
    for (const node of box.querySelectorAll('a')) {
      if (node.getAttribute('href') === hash) node.setAttribute('aria-current', 'page'); else node.removeAttribute('aria-current');
    }
  }, dispose() {disposed = true; off(); document.removeEventListener('keydown', escape); box.remove();}};
}
