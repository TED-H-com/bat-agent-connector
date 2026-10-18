// One observer for all routes. A zoomed visual viewport is not a keyboard.
export function observeViewport() {
  const viewport = window.visualViewport;
  const root = document.documentElement;
  const clear = () => {
    document.body.classList.remove('mobile-keyboard');
    root.style.removeProperty('--workspace-visible-height');
    root.style.removeProperty('--workspace-visible-top');
  };
  const update = () => {
    if (!viewport || window.innerWidth >= 1000 || !Number.isFinite(viewport.height) || viewport.height <= 0
        || !Number.isFinite(viewport.scale) || Math.abs(viewport.scale - 1) > 0.05
        || viewport.height >= window.innerHeight - 80) { clear(); return; }
    root.style.setProperty('--workspace-visible-height', `${viewport.height}px`);
    root.style.setProperty('--workspace-visible-top', `${Math.max(0, viewport.offsetTop || 0)}px`);
    document.body.classList.add('mobile-keyboard');
  };
  viewport?.addEventListener('resize', update);
  viewport?.addEventListener('scroll', update);
  window.addEventListener('resize', update);
  update();
  return () => {
    viewport?.removeEventListener('resize', update);
    viewport?.removeEventListener('scroll', update);
    window.removeEventListener('resize', update);
    clear();
  };
}
