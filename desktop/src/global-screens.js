// Shared chrome for Pending, Operations, Settings and the signed-out card.
import {iconButton} from './ui/components.js';

export function setupGuide() {
  const zh = (typeof navigator !== 'undefined' ? navigator.language || '' : '').toLowerCase().startsWith('zh');
  return zh
    ? 'https://github.com/teddashh/bat-agent-connector/blob/main/docs/getting-started.zh-TW.md'
    : 'https://github.com/teddashh/bat-agent-connector/blob/main/docs/getting-started.md';
}

export function inboxRow({h, key, title, href, meta, pill, action, detailsLabel, onOpen}) {
  const article = h('article', {class: 'global-inbox-row', 'data-inbox-key': key});
  article.addEventListener('click', event => {
    if (event.target.closest('a, button, input, select, textarea')) return;
    onOpen?.();
  });
  const text = h('div', {class: 'global-inbox-text'},
    h('a', {class: 'title', href}, title),
    meta ? h('p', {class: 'global-inbox-meta'}, meta) : null);
  const details = iconButton({icon: 'chevron-right', label: detailsLabel, onClick: () => onOpen?.()});
  article.append(text, pill || null, h('div', {class: 'global-inbox-actions'}, action || null, details));
  return article;
}

export function firstRunCard({h, t, body}) {
  return h('div', {class: 'global-first-run'},
    h('section', {class: 'global-first-card', 'aria-labelledby': 'global-first-title'},
      h('span', {class: 'brand-mark', 'aria-hidden': 'true'}),
      h('h1', {id: 'global-first-title'}, t('global_product')),
      h('p', {}, t('global_first_sentence')),
      body,
      h('a', {href: setupGuide()}, t('global_setup_guide'))));
}

// Sections stay mounted. The nav moves selection and scrolls; it does not hide siblings,
// except a section explicitly marked hidden until its content exists.
export function settingsFrame({h, t, sections}) {
  const nav = h('nav', {class: 'global-settings-nav', role: 'tablist', 'aria-orientation': 'vertical', 'aria-label': t('global_settings_nav')});
  const panels = h('div', {class: 'global-settings-panels'});
  const buttons = [];
  const roots = [];
  for (const section of sections) {
    const panel = h('section', {
      class: 'global-settings-section', role: 'tabpanel', id: `settings-panel-${section.id}`,
      'data-settings-panel': section.id, 'aria-labelledby': `settings-tab-${section.id}`,
    });
    if (section.id === 'models') panel.setAttribute('data-settings-models', '');
    const button = h('button', {
      type: 'button', role: 'tab', id: `settings-tab-${section.id}`, 'data-settings-nav': section.id,
      'aria-controls': panel.id,
    }, section.label);
    if (section.hidden) {button.hidden = true; panel.hidden = true;}
    button.addEventListener('click', () => select(section.id, true));
    buttons.push(button);
    roots.push(panel);
    nav.append(button);
    panels.append(panel);
  }
  const visible = () => buttons.filter(button => !button.hidden);
  function select(id, scroll) {
    for (const button of buttons) {
      const on = button.dataset.settingsNav === id && !button.hidden;
      button.setAttribute('aria-selected', on ? 'true' : 'false');
      button.tabIndex = on ? 0 : -1;
    }
    if (scroll) document.getElementById('settings-panel-' + id)?.scrollIntoView({block: 'nearest'});
  }
  nav.addEventListener('keydown', event => {
    const keys = ['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Home', 'End'];
    if (!keys.includes(event.key)) return;
    const enabled = visible();
    const current = enabled.indexOf(document.activeElement);
    if (current < 0) return;
    event.preventDefault();
    const index = event.key === 'Home' ? 0 : event.key === 'End' ? enabled.length - 1
      : (current + (event.key === 'ArrowUp' || event.key === 'ArrowLeft' ? -1 : 1) + enabled.length) % enabled.length;
    const next = enabled[index];
    if (!next) return;
    next.focus();
    select(next.dataset.settingsNav, true);
  });
  const first = sections.find(section => !section.hidden);
  if (first) select(first.id, false);
  function panel(id) {return roots.find(node => node.dataset.settingsPanel === id);}
  function show(id) {
    const button = buttons.find(node => node.dataset.settingsNav === id);
    const node = panel(id);
    if (!button || !node) return;
    button.hidden = false;
    node.hidden = false;
    if (!visible().some(item => item.getAttribute('aria-selected') === 'true')) select(id, false);
  }
  function hide(id) {
    const button = buttons.find(node => node.dataset.settingsNav === id);
    const node = panel(id);
    if (!button || !node) return;
    const was = button.getAttribute('aria-selected') === 'true';
    button.hidden = true;
    node.hidden = true;
    if (was) {
      const next = visible()[0];
      if (next) select(next.dataset.settingsNav, false);
    }
  }
  return {root: h('div', {class: 'global-settings'}, nav, panels), panel, show, hide};
}
