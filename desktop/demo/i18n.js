import {STRINGS} from '../src/i18n.js';
export {STRINGS};
const requested = new URLSearchParams(location.search).get('lang');
export const locale = requested === 'zh-TW' || !requested && navigator.language.toLowerCase().startsWith('zh') ? 'zh-TW' : 'en';
export function t(key, vars = {}) {
  return (STRINGS[locale][key] ?? STRINGS.en[key] ?? key).replace(/\{(\w+)\}/g, (_, k) => String(vars[k] ?? ''));
}
