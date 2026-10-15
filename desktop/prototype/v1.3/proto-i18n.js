import { STRINGS as productStrings } from "../../src/i18n.js?product";
import { prototypeStrings } from "./prototype-strings.js";
const reusedLabels = Object.fromEntries(Object.entries(prototypeStrings.en).flatMap(([key, english]) => {
  const found = Object.entries(productStrings.en).find(([, text]) => text === english);
  return found ? [[key, found[0]]] : [];
}));
export const STRINGS = {
  en: {
    ...productStrings.en,
    ...prototypeStrings.en
  },
  "zh-TW": {
    ...productStrings["zh-TW"],
    ...prototypeStrings["zh-TW"]
  }
};
function detectInitialLocale() {
  if (typeof window !== "undefined" && window.location) {
    const params = new URLSearchParams(window.location.search);
    const qLang = params.get("lang");
    if (qLang === "zh-TW" || qLang === "zh") return "zh-TW";
    if (qLang === "en") return "en";
  }
  const nav = typeof navigator !== "undefined" ? navigator.language || "en" : "en";
  return nav.toLowerCase().startsWith("zh") ? "zh-TW" : "en";
}
export let locale = detectInitialLocale();
if (typeof document !== "undefined" && document.documentElement) {
  document.documentElement.lang = locale;
}
export function setLocale(next) {
  locale = next === "zh-TW" ? "zh-TW" : "en";
  if (typeof document !== "undefined" && document.documentElement) {
    document.documentElement.lang = locale;
  }
  return locale;
}
export function t(key, vars = {}) {
  const existing = {
    results: 'session_results',
    details: 'global_details',
    context: 'pd_tab_context',
    inputs: 'pd_tab_inputs',
    planTab: 'pd_tab_plan',
    discussionInfo: 'pd_panel',
    delivery: 'session_delivery',
    requirement: 'wb_requirements',
    sources: 'ov_sources'
  };
  key = existing[key] || reusedLabels[key] || key;
  const table = STRINGS[locale] || STRINGS.en;
  let s = table[key] ?? STRINGS.en[key] ?? key;
  for (const [k, v] of Object.entries(vars)) {
    s = s.replaceAll(`{${k}}`, String(v ?? ""));
  }
  return s;
}
