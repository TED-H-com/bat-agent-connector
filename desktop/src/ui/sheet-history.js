// History belongs to presentation only. Never persist a command, source fact or draft here.
export function sheetHistory({show, hide, canOpen = () => true}) {
  const id = crypto.randomUUID(), url = location.href;
  const key = 'batcSheet';
  let disposed = false, visible = false;
  const owned = () => location.href === url && history.state?.[key] === id;
  const reveal = () => {if (!visible) {visible = true; show();}};
  const conceal = () => {if (visible) {visible = false; hide();}};
  function pop() {
    if (disposed) return;
    if (owned() && canOpen()) reveal();
    else conceal();
  }
  window.addEventListener('popstate', pop);
  return {
    open() {
      if (disposed || visible || !canOpen()) return;
      history.pushState({...history.state, [key]: id}, '', url);
      reveal();
    },
    close() {
      if (disposed || !visible) return;
      conceal();
      if (owned()) history.back();
    },
    dispose() {
      disposed = true;
      window.removeEventListener('popstate', pop);
      // Teardown/withdrawal cannot navigate or resurrect an old source on Forward.
      if (owned()) {const state = {...history.state}; delete state[key]; history.replaceState(state, '', url);}
    },
  };
}
