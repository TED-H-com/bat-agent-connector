import { STRINGS as productStrings } from "../../src/i18n.js?product";
import { prototypeStrings } from "./prototype-strings.js";
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
  // Reuse only labels whose meaning agrees in both locales. Intent and judgment
  // labels stay in the prototype dictionary when a product key has another context.
  const existing = {
    assessmentSim: 'pm_fa_title',
    idea: 'pm_lifecycle_idea',
    planned: 'pm_sprint_planned',
    running: 'ui_status_running',
    maintenance: 'wb_maintenance',
    discuss: 'res_discuss',
    messageLabel: 'pd_source_message',
    messagePlaceholder: 'pd_message',
    goal: 'pm_sprint_history_goals',
    nextStep: 'reg_col_next',
    merged: 'pm_kanban_state_merged',
    deployed: 'ui_status_deployed',
    notRecorded: 'ui_reason_not_recorded',
    passed: 'pm_kanban_state_passed',
    results: 'session_results',
    details: 'global_details',
    context: 'pd_tab_context',
    inputs: 'pd_tab_inputs',
    planTab: 'pd_tab_plan',
    discussionInfo: 'pd_panel',
    delivery: 'session_delivery',
    requirement: 'wb_requirements',
    cancel: 'pm_panel_cancel',
    setupPm: 'pd_setup',
    returnThread: 'agents_back',
    milestones: 'ov_milestones',
    deliveries: 'ov_deliveries',
    charter: 'ov_charter',
    purpose: 'wb_definition',
    progress: 'ov_progress',
    blockers: 'pm_brief_blockers',
    decisions: 'wb_decisions',
    resources: 'wb_resources',
    risks: 'wb_risks'
  };
  key = existing[key] || key;
  const table = STRINGS[locale] || STRINGS.en;
  let s = table[key] ?? STRINGS.en[key] ?? key;
  for (const [k, v] of Object.entries(vars)) {
    s = s.replaceAll(`{${k}}`, String(v ?? ""));
  }
  return s;
}
