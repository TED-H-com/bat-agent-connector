// Native settings intent only. Connector setup/enrollment keeps its existing authority.
export function startupRoleChoice({h, t, errorBox, snapshot, select}) {
  let busy = false;
  const info = h('div', {'aria-live': 'polite'});
  const buttons = [];
  const options = ['client', 'provider', 'both'].map(role => {
    const button = h('button', {type: 'button', class: 'primary', 'data-role': role, onclick: async () => {
      if (busy) return;
      busy = true;
      for (const other of buttons) other.disabled = true;
      info.replaceChildren(h('p', {}, t('desktop_role_saving')));
      try {await select({role, expected_generation: snapshot.generation});}
      catch (error) {info.replaceChildren(errorBox(error, {sourceRefresh: false, within: info}));}
      finally {busy = false; for (const other of buttons) other.disabled = false;}
    }}, t('desktop_role_' + role));
    buttons.push(button);
    return h('section', {class: 'panel'}, button, h('p', {}, t('desktop_role_' + role + '_help')));
  });
  return h('section', {'data-startup-role': '', 'aria-labelledby': 'startup-role-title'},
    h('h2', {id: 'startup-role-title'}, t('desktop_role_title')), h('p', {}, t('desktop_role_help')), ...options, info);
}
