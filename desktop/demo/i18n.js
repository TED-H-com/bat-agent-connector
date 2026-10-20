import {STRINGS, detectLocale} from '../src/i18n.js';
export {STRINGS};
const requested = new URLSearchParams(location.search).get('lang');
export const locale = requested === 'zh-TW' || !requested && detectLocale(navigator.language) === 'zh-TW' ? 'zh-TW' : 'en';
export function t(key, vars = {}) {
  return (STRINGS[locale][key] ?? STRINGS.en[key] ?? key).replace(/\{(\w+)\}/g, (_, k) => String(vars[k] ?? ''));
}

// Demo data is built per locale. Match its existing language link by navigating
// within the public tour; never change the installed product's saved preference.
export function setLocale(value) {
  if (!['en', 'zh-TW'].includes(value) || value === locale) return;
  const url = new URL(location.href);
  url.searchParams.set('lang', value);
  location.assign(url.href);
}
