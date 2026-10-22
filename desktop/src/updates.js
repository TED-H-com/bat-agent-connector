import {updateRequest} from "./transport/index.ts";
import {versionProjection, versionRow} from './version-projection.js';
import {versionFacts} from './version-facts.js';

export async function mountUpdates(main, {h, t, versionInput = () => ({desktop:true}), onVersions = () => () => {}, onRead = () => {}}) {
  const panel = h("section", {class: "panel", "aria-label": t("update_title")});
  const content = h("div"), facts = h('div'), message = h("p", {role: "status", class: "muted"});
  panel.append(h("h2", {}, t("update_title")), content, facts, message); main.append(panel);
  let disposed = false, busy = false, readable = false, value, pressed = null, readAt = null;
  const projection = () => versionProjection({...versionInput(),updater:value,updaterReadAt:readAt,updaterReadFailed:!readable});
  const paintFacts = () => {if(active())facts.replaceChildren(versionFacts({h,t,projection:projection()}));};
  const active = () => !disposed && panel.isConnected;
  const phases = new Set(["idle", "checking", "available", "up_to_date", "check_failed", "downloading", "download_failed", "verified", "stopping_fleet", "installation_unknown"]);
  const placeFocus = key => {
    if (!active() || !key) return;
    const nodes = [...content.querySelectorAll("[data-update-action]")];
    const find = name => nodes.find(node => node.getAttribute("data-update-action") === name);
    const usable = node => node && !node.disabled && !node.closest("[hidden]");
    const current = find(key);
    if (usable(current)) { current.focus({preventScroll: true}); return; }
    const named = key === "download" ? (usable(find("install")) ? find("install") : find("read")) : find("read");
    if (usable(named)) { named.focus({preventScroll: true}); return; }
    const group = [...content.querySelectorAll(".actions button")].filter(usable);
    if (group.length) { group[0].focus({preventScroll: true}); return; }
    const any = nodes.filter(usable);
    if (any.length) { any[0].focus({preventScroll: true}); return; }
    message.tabIndex = -1; message.focus({preventScroll: true});
  };
  const render = () => {
    if (!active()) return;
    paintFacts();
    const focused = document.activeElement;
    const key = focused instanceof Element && content.contains(focused) ? focused.getAttribute("data-update-action") : null;
    let flying = false;
    const gate = (name, staticBlocked) => {
      const blocked = Boolean(staticBlocked) && pressed !== name;
      const soft = busy && !blocked;
      if (soft) flying = true;
      return {disabled: blocked || null, "aria-disabled": soft ? "true" : null};
    };
    const action = (name, label, className, staticBlocked, onclick) => h("button", {
      type: "button", class: className, "data-update-action": name, ...gate(name, staticBlocked), onclick,
    }, label);
    if (!value) content.replaceChildren(action("read", t("update_read"), "secondary", false, () => request({action: "status"})));
    else {
      const staticActions = !readable || !value.available || ["checking", "downloading", "stopping_fleet", "installation_unknown"].includes(value.phase);
      const candidate = value.candidate;
      content.replaceChildren(
        h("p", {}, t("update_current", {version: versionRow(projection(),'desktop_update').version || t('diag_unknown')})),
        h("p", {class: "muted"}, !value.available ? t(value.code === "UPDATE_PLATFORM_UNSUPPORTED" ? "update_platform" : "update_unsigned") : t("update_phase_" + value.phase)),
        ...(candidate ? [h("dl", {class: "kv"}, h("dt", {}, t("update_candidate")), h("dd", {}, candidate.version),
          h("dt", {}, t("pub_sha")), h("dd", {class: "mono"}, candidate.source_sha))] : []),
        ...(value.installation ? [h("p", {}, t("update_requested", {version: value.installation.to_version}))] : []),
        ...(value.available ? [h("p", {class: "muted"}, t("update_help")), h("div", {class: "actions"},
          action("check", t("update_check"), candidate ? "secondary" : "primary", staticActions, () => request({action: "check"})),
          ...(candidate ? [
            action("download", t("update_download"), "primary", staticActions || value.phase === "verified", () => request({action: "download", candidate_id: candidate.candidate_id})),
            action("install", t("update_install"), value.phase === "verified" ? "primary" : "secondary", staticActions || value.phase !== "verified", () => request({action: "install", candidate_id: candidate.candidate_id})),
          ] : []),
          action("read", t("update_read"), "secondary", false, () => request({action: "status"})))] : []));
    }
    if (flying) message.setAttribute("aria-busy", "true"); else message.removeAttribute("aria-busy");
    if (key) placeFocus(key);
  };
  const request = async input => {
    if (busy || !active()) return;
    busy = true; pressed = input.action === "status" ? "read" : input.action; message.textContent = ""; render();
    try {
      const next = await updateRequest(input);
      if (!active()) return;
      if (!next || typeof next.current_version !== "string" || typeof next.available !== "boolean" || !phases.has(next.phase)
          || next.candidate && ["candidate_id", "version", "source_sha"].some(k => typeof next.candidate[k] !== "string")) throw new Error("UPDATE_STATUS_INVALID");
      value = next; readable = true; readAt = new Date().toISOString();
      onRead({current_version:next.current_version,candidate:next.candidate ? {
        version:next.candidate.version,source_sha:next.candidate.source_sha,checked_at:next.candidate.checked_at,
      } : null}, readAt);
      if (next.code && next.available && !next.installation) message.textContent = `${t("update_problem")} ${next.code}`;
    } catch {
      if (active()) { readable = false; onRead(null,null); message.textContent = t(input.action === "install" ? "update_unknown" : "update_read_failed"); }
    } finally { busy = false; pressed = null; if (!active()) return; render(); }
  };
  const offVersions = onVersions(paintFacts);
  await request({action: "status"});
  return () => {disposed = true; offVersions(); panel.remove();};
}
