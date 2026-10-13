import {pageHeader} from './ui/components.js';
// Each list has its own cursor and loaded-page count. No client-invented total or
// completion state, and no read receipts are written by visiting this view.
export async function attentionView({main, h, t, api, guard, caps, storageKey, route, onEvents, debounceRefresh,
  errorBox, sessionRow, workItemRow, opRow, onPending = () => {}}) {
  const unread = caps?.features?.work_item_reads?.version === 1;
  const saved = sessionStorage.getItem(storageKey) || sessionStorage.getItem("batc.tab");
  const tab = saved === "confirm" || saved === "active" ? "active" : saved === "unread" && unread ? "unread" : "needs";
  const tabs = h("div", {class: "tabs attention-tabs", role: "group", "aria-label": t("nav_home")},
    ...["needs", "active", ...(unread ? ["unread"] : [])].map(value => h("button", {
      class: value === tab ? "on" : "", "aria-pressed": String(value === tab), onclick: () => {
        guard(); sessionStorage.setItem(storageKey, value); route();
      }}, t("attention_tab_" + value))));
  const empty = h("section", {class: "panel", hidden: true}, h("p", {}, t("empty_needs_you")),
    h("p", {class: "muted"}, t("empty_next_work")), h("div", {class: "actions"},
      h("a", {href: "#/projects"}, t("sessions_projects")),
      caps?.scopes?.includes('start') && caps?.actions?.some(a => a.action === 'session.start' && a.allowed === true)
        ? h("a", {href: "#/start"}, t("start_title_page")) : null));
  const sections = [];
  const add = (key, path, field, row, options = {}) => {
    const list = h("div", {}), status = h("div", {role: "status"});
    const count = h("span", {class: "muted"}), more = h("button", {class: "secondary", hidden: true}, t("load_more"));
    const section = h("section", {class: "panel attention-section", "aria-label": t("attention_" + key)},
      h("h2", {}, t("attention_" + key)), h("p", {class: "muted"}, t("attention_" + key + "_note")), status, list,
      h("div", {class: "actions attention-footer"}, count, more,
        key === "problems" || key === "active" ? h("a", {href: "#/operations/" + (key === "problems" ? "attention" : "active")},
          t(key === "problems" ? "operations_view_attention" : "operations_view_active")) : null));
    const state = {key, path, field, row, ...options, section, list, status, count, more, pages: 1, size: null, busy: false};
    more.onclick = () => refresh(state, state.pages + 1).catch(() => {});
    sections.push(state); return section;
  };
  const hostRow = x => h("div", {class: "row"}, h("div", {class: "grow"}, h("div", {class: "title"}, x.host),
    h("div", {class: "muted"}, x.error || t("stale_reason_" + x.stale_reason))));
  main.append(pageHeader({title: t("nav_home")}), tabs, empty, ...(tab === "needs" ? [
    add("replies", "/sessions?attention=true&limit=50", "sessions", sessionRow),
    add("completion", "/work-items?pending=true&limit=50", "work_items", workItemRow),
    add("problems", "/operations?status=needs_attention,uncertain&limit=50", "operations", opRow, {cursor: "next_before", param: "before"}),
    add("hosts", "/hosts", "hosts", hostRow, {filter: x => x.stale})
  ] : tab === "active" ? [
    add("active", "/operations?status=accepted,running,waiting_checks,waiting_external&limit=50", "operations", opRow,
      {cursor: "next_before", param: "before"})
  ] : [add("unread", "/work-items?unread=true&limit=50", "work_items", workItemRow)]));
  let serial = Promise.resolve();
  const load = async (section, wanted) => {
    guard(); section.busy = true; section.more.disabled = true;
    if (section.size === null) section.status.replaceChildren(h("p", {class: "muted"}, t("loading")));
    try {
      let cursor = null, pages = 0;
      const items = new Map(), seen = new Set();
      do {
        const path = section.path + (cursor == null ? "" : `&${section.param || "cursor"}=${encodeURIComponent(cursor)}`);
        const data = await api("GET", path); guard();
        if (!Array.isArray(data[section.field])) throw new Error(t("attention_invalid"));
        for (const row of data[section.field]) {
          if (!section.filter || section.filter(row)) items.set(row.work_item_id || row.operation_id ||
            (row.session_id ? JSON.stringify([row.host, row.session_id]) : row.host), row);
        }
        cursor = data[section.cursor || "next_cursor"] ?? null; pages++;
        if (cursor !== null && seen.has(cursor)) throw new Error(t("attention_invalid"));
        seen.add(cursor);
      } while (cursor !== null && pages < wanted);
      section.pages = pages; section.size = items.size; section.partial = cursor !== null;
      section.list.replaceChildren(...(items.size ? [...items.values()].map(section.row)
        : [h("p", {class: "muted"}, t("attention_empty"))]));
      section.count.textContent = t("attention_loaded", {count: items.size});
      section.status.replaceChildren(); section.more.hidden = cursor === null;
      section.section.dataset.fresh = "true";
      if (tab === 'needs') onPending(sections.every(item => item.section.dataset.fresh === 'true')
        ? {count: sections.reduce((sum, item) => sum + item.size, 0), partial: sections.some(item => item.partial)} : {});
    } catch (error) {
      guard(); section.section.dataset.fresh = "false"; onPending({});
      if ([403, 404].includes(error.status)) {section.list.replaceChildren(); section.count.replaceChildren(); section.size = null; section.more.hidden = true;}
      section.status.replaceChildren(errorBox(error), h("p", {class: "muted"}, t("attention_not_updated")));
      throw error;
    } finally {
      section.busy = false; section.more.disabled = false;
    }
  };
  const refresh = (only, wanted) => {
    const work = serial.catch(() => {}).then(async () => {
      guard();
      const results = await Promise.allSettled((only ? [only] : sections).map(s => load(s, only ? wanted : s.pages)));
      guard();
      empty.hidden = tab !== "needs" || !sections.every(s => s.size === 0 && s.section.dataset.fresh === "true");
      const failed = results.find(r => r.status === "rejected");
      if (failed) throw failed.reason;
    });
    serial = work; return work;
  };
  await refresh().catch(() => {});
  // Initial route loading may finish after the user has navigated away. Let the
  // router retire that view; do not turn a superseded load into a login failure.
  try {guard();} catch {return;}
  return onEvents(debounceRefresh(() => refresh(), 500));
}
