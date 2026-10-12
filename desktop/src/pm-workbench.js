// Only navigation preferences survive a remount. Source facts never live here.
const selections = new Map();
export function rememberProjectView(namespace, workspaceId, id) {
  const key = JSON.stringify([namespace, workspaceId]);
  selections.delete(key); selections.set(key, id);
  if (selections.size > 40) selections.delete(selections.keys().next().value);
}
export function projectViews({h, t, namespace, workspaceId, groups, onSelect = () => {}}) {
  const key = JSON.stringify([namespace, workspaceId]);
  const views = groups.filter(group => group.sections.length);
  let selected = views.some(group => group.id === selections.get(key)) ? selections.get(key) : 'charter';
  const buttons = new Map();
  const element = h('nav', {class: 'pm-project-views', 'aria-label': t('wb_navigation')});
  for (const group of views) {
    const button = h('button', {type: 'button', class: 'secondary', 'data-pm-view': group.id,
      onclick: () => select(group.id)}, t('wb_' + group.id));
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
    if (!buttons.has(id)) return;
    selected = id; rememberProjectView(namespace, workspaceId, id);
    apply(); onSelect(id);
  }
  return {element, apply, select, selected: () => selected};
}
