/* Local project-site behavior. No API calls, analytics or credentials. */
(function () {
  'use strict';
  const root = document.documentElement;
  function applyLanguage(language, updateUrl) {
    const next = language === 'zh' ? 'zh' : 'en';
    root.dataset.lang = next;
    root.lang = next === 'zh' ? 'zh-Hant' : 'en';
    document.title = root.dataset[next === 'zh' ? 'titleZh' : 'titleEn'] || document.title;
    const description = root.dataset[next === 'zh' ? 'descZh' : 'descEn'];
    if (description) document.querySelector('meta[name="description"]')?.setAttribute('content', description);
    document.querySelectorAll('[data-language]').forEach(button => {
      button.setAttribute('aria-pressed', String(button.dataset.language === next));
    });
    try { localStorage.setItem('tedh-project-lang', next); } catch { /* Optional preference. */ }
    if (updateUrl) {
      const url = new URL(location.href);
      url.searchParams.set('lang', next === 'zh' ? 'zh-TW' : 'en');
      history.replaceState(null, '', url);
    }
  }
  document.querySelectorAll('[data-language]').forEach(button => {
    button.addEventListener('click', () => {
      applyLanguage(button.dataset.language, true);
      const wasOpen = nav?.classList.contains('is-open');
      closeMenu();
      if (wasOpen) menu?.focus({ preventScroll: true });
      // Translation changes section heights. Restore the named section after
      // closing the mobile menu so its heading stays below the sticky header.
      document.getElementById(location.hash.slice(1))?.scrollIntoView({ block: 'start', behavior: 'instant' });
    });
  });
  applyLanguage(root.dataset.lang, false);
  const menu = document.querySelector('.menu-button');
  const nav = document.querySelector('.site-nav');
  function closeMenu() { menu?.setAttribute('aria-expanded', 'false'); nav?.classList.remove('is-open'); }
  menu?.addEventListener('click', () => {
    const open = menu.getAttribute('aria-expanded') !== 'true';
    menu.setAttribute('aria-expanded', String(open));
    nav?.classList.toggle('is-open', open);
  });
  nav?.querySelectorAll('a').forEach(link => link.addEventListener('click', closeMenu));
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && nav?.classList.contains('is-open')) { closeMenu(); menu?.focus(); }
  });
  function showView(view) {
    document.querySelectorAll('[data-view]').forEach(button => {
      const selected = button.dataset.view === view;
      button.setAttribute('aria-pressed', String(selected));
      const target = document.getElementById(button.getAttribute('aria-controls'));
      if (target) target.hidden = !selected;
    });
  }
  document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => showView(button.dataset.view)));
  showView(document.querySelector('[data-view][aria-pressed="true"]')?.dataset.view || 'overview');
})();
