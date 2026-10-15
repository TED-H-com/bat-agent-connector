import { uiStrings } from "../../src/ui/ui-strings.js";
import { prototypeStrings } from "./prototype-strings.js";

export const STRINGS = {
  en: {
    ...uiStrings.en,
    ...prototypeStrings.en,
  },
  "zh-TW": {
    ...uiStrings["zh-TW"],
    ...prototypeStrings["zh-TW"],
  },
};

function detectInitialLocale() {
  if (typeof window !== "undefined" && window.location) {
    const params = new URLSearchParams(window.location.search);
    const qLang = params.get("lang");
    if (qLang === "zh-TW" || qLang === "zh") return "zh-TW";
    if (qLang === "en") return "en";
  }
  const nav = typeof navigator !== "undefined" ? (navigator.language || "en") : "en";
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
  const table = STRINGS[locale] || STRINGS.en;
  let s = table[key] ?? STRINGS.en[key] ?? key;
  for (const [k, v] of Object.entries(vars)) {
    s = s.replaceAll(`{${k}}`, String(v ?? ""));
  }
  return s;
}
