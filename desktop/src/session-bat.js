import {nativeDesktop, fleetAvailability, fleetControl} from "./transport/index.ts";

// A local, explicit profile launch. Session labels and paths never select a profile.
export function sessionBatPanel({h, t, guard, storageKey, session}) {
  const box = h("section", {class:"session-bat", "aria-label":t("bat_handoff")});
  const content = h("div", {hidden:true});
  const message = h("p", {class:"muted", role:"status"}), copyFallback = h("div");
  let disposed = false, busy = false, expanded = false, readable = false, overview, chosen = "", intent, receipt, restoredPreview = false, damaged = false;
  const alive = () => {if (disposed) return false; try {guard(); return true;} catch {return false;}};
  const id = v => typeof v === "string" && /^[0-9a-f]{32}$/.test(v);
  const profile = v => typeof v === "string" && /^[A-Za-z0-9_.-]{1,64}$/.test(v);
  const binding = v => typeof v === "string" && /^[0-9a-f]{64}$/.test(v);
  const summaryMatches = (s, value) => s?.launch_id === value.preview_id && s.dashboard === false
    && s.opens_bat === true && typeof s.already_running === "boolean" && typeof s.bat_may_open_local_window === "boolean"
    && Array.isArray(s.profiles) && s.profiles.length === 1 && s.profiles[0] === value.profile_id;
  const persist = value => localStorage.setItem(storageKey, JSON.stringify(value));
  try {
    const raw = localStorage.getItem(storageKey);
    if (raw) {
      const saved = JSON.parse(raw);
      if (saved?.version !== 1 || !id(saved.preview_id) || !profile(saved.profile_id) || !binding(saved.configuration_binding)
        || typeof saved.attempted !== "boolean" || !summaryMatches(saved.summary, saved)) throw new Error("invalid saved launch");
      intent = saved; chosen = saved.profile_id; expanded = true; restoredPreview = !saved.attempted;
    }
  } catch {damaged = true; expanded = true;}
  const terminal = () => ["started", "not_started", "already_running"].includes(receipt?.state);
  const acceptReceipt = value => {
    if (!value || !intent) return;
    const r = value.summary || value;
    if (r.launch_id !== intent.preview_id || r.dashboard !== false || !Array.isArray(r.profiles)
      || r.profiles.length !== 1 || r.profiles[0] !== intent.profile_id
      || !["prepared", "uncertain", "started", "not_started", "already_running"].includes(value.state)
      || value.summary && !summaryMatches(value.summary, intent)) throw new Error(t("bat_wrong_receipt"));
    receipt = value;
  };
  const run = async fn => {
    if (!alive() || busy) return;
    busy = true; message.textContent = ""; render();
    try {await fn();}
    catch (error) {if (alive()) message.textContent = `${t("bat_problem")} ${String(error)}`;}
    finally {busy = false; if (alive()) render();}
  };
  const readReceipt = async () => {
    if (!intent?.attempted) return;
    const value = await fleetControl({action:"launch_status",launch_id:intent.preview_id});
    if (alive()) {acceptReceipt(value); if (!value) message.textContent = t("bat_no_receipt");}
  };
  const read = async () => {
    readable = false;
    const available = await fleetAvailability(); if (!alive()) return;
    if (!available.configured || !available.platform_supported || !available.native_controls) throw new Error(t("bat_missing"));
    const value = await fleetControl({action:"overview"}); if (!alive()) return;
    if (value?.control_version !== 1 || !binding(value.configuration?.binding) || !Array.isArray(value.profiles)
      || value.profiles.length > 1001 || value.profiles.some(p=>!profile(p.id) || typeof p.label !== "string" || !(p.connection === null || typeof p.connection === "string"))
      || new Set(value.profiles.map(p=>p.id)).size !== value.profiles.length || !Array.isArray(value.selection?.connections)) throw new Error(t("bat_missing"));
    overview = value; readable = value.configuration.valid === true && !value.pending_migration;
    await readReceipt();
  };
  const copy = async field => {
    if (!alive()) return;
    const value = field === "title" ? session()?.title || "" : session()?.session_id || "";
    try {await navigator.clipboard.writeText(value); if (alive()) message.textContent = t("bat_copied");}
    catch {
      if (!alive()) return;
      const input = h("textarea", {readonly:true, "aria-label":t(field === "title" ? "bat_menu_title" : "bat_menu_id")}, value);
      copyFallback.replaceChildren(h("p", {class:"muted"}, t("bat_copy_manually")), input); input.focus(); input.select();
    }
  };
  const review = () => run(async () => {
    if (!readable || intent && (!restoredPreview || intent.attempted) || damaged || !profile(chosen)) return;
    const previous = intent?.preview_id, selected = intent?.profile_id || chosen;
    const value = await fleetControl({action:"preview_profile", profile_id:selected}); if (!alive()) return;
    const next = {version:1, profile_id:selected, preview_id:value?.preview_id, configuration_binding:value?.configuration_binding,
      summary:value?.summary, attempted:false};
    if (!id(next.preview_id) || !binding(next.configuration_binding) || next.configuration_binding !== overview.configuration.binding
      || !summaryMatches(next.summary, next)) throw new Error(t("bat_wrong_receipt"));
    intent = next; receipt = null; persist(intent); restoredPreview = false;
    if (previous && previous !== next.preview_id && alive()) await fleetControl({action:"discard",preview_id:previous});
  });
  const launch = () => run(async () => {
    if (!intent || damaged || restoredPreview || intent.summary.already_running || receipt) return;
    // Persist the exact original handle before every explicit attempt. Core reads its durable
    // receipt before considering another effect; unknown/prepared receipts cannot re-spawn.
    const next = {...intent, attempted:true}; persist(next); intent = next;
    guard(); const value = await fleetControl({action:"launch",preview_id:intent.preview_id});
    if (alive()) acceptReceipt(value);
  });
  const reset = () => run(async () => {
    if (!intent || intent.attempted && !terminal()) return;
    localStorage.removeItem(storageKey);
    const old = intent.preview_id; intent = receipt = null; restoredPreview = false; chosen = "";
    await fleetControl({action:"discard",preview_id:old});
  });
  const render = () => {
    if (!alive()) return;
    const button = (key, fn, disabled=false) => h("button", {class:"secondary",disabled:busy||disabled,onclick:fn},t(key));
    content.hidden = !expanded;
    if (!expanded) return;
    const fixed = !!intent || damaged;
    const picker = h("select", {"aria-label":t("bat_profile"),disabled:busy||fixed||!readable,onchange:e=>{chosen=e.target.value;render();}},
      h("option", {value:"",selected:!chosen},t("bat_choose")), ...(overview?.profiles || []).map(p=>h("option",{value:p.id,selected:chosen===p.id},`${p.label} · ${p.id}`)));
    const lines = [h("p",{class:"muted"},t("bat_search_help")),h("label",{},t("bat_profile")," ",picker)];
    if (intent) {
      lines.push(h("p",{class:"note"}, t("bat_chosen",{profile:intent.profile_id})," · ",h("code",{},intent.preview_id)),
        h("p",{class:"muted"},receipt?t("fleet_launch_"+(receipt.state==="prepared"?"uncertain":receipt.state))
          :restoredPreview?t("bat_preview_expired"):intent.summary.already_running?t("fleet_launch_already_running"):t(intent.attempted?"bat_unknown":"bat_reviewed")));
      if (intent.summary.bat_may_open_local_window) lines.push(h("p",{class:"muted"},t("fleet_local_anchor")));
    }
    if (damaged) lines.push(h("p",{class:"error"},t("bat_saved_invalid")));
    const buttons = [button("bat_read",()=>run(read))];
    if (!fixed) buttons.unshift(button("bat_review",review,!readable||!chosen));
    if (restoredPreview) buttons.unshift(button("bat_review_again",review,!readable));
    if (intent && !restoredPreview && !receipt && !intent.summary.already_running) buttons.unshift(button(intent.attempted?"bat_retry":"bat_launch",launch,!readable));
    if (intent && (!intent.attempted || terminal())) buttons.push(button(intent.attempted?"bat_new":"cancel",reset));
    lines.push(h("div",{class:"actions"},...buttons),h("p",{class:"muted"},t("bat_preferences")," ",h("a",{href:"#/settings"},t("nav_settings"))));
    content.replaceChildren(...lines);
  };
  box.append(content, copyFallback, message);
  render();
  // Reopening only reads the original receipt. It never creates a new preview or launches.
  if (nativeDesktop && expanded) queueMicrotask(()=>run(read));
  return {box, update:render, dispose:()=>{disposed=true;},
    copyTitle: () => copy("title"), copyId: () => copy("id"),
    open: () => {expanded = true; run(read);}};
}
