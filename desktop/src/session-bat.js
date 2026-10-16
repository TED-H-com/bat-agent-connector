import {nativeDesktop, fleetAvailability, fleetControl} from "./transport/index.ts";

// A local, explicit profile launch. Session labels and paths never select a profile.
export function sessionBatPanel({h, t, guard, storageKey, session}) {
  const box = h("section", {class:"session-bat", "aria-label":t("bat_handoff")});
  const content = h("div", {hidden:true});
  const message = h("p", {class:"muted", role:"status"}), copyFallback = h("div");
  let disposed = false, busy = false, expanded = false, readable = false, overview, chosen = "", intent, receipt, restoredPreview = false, damaged = false, pressed = null, displaced = null;
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
  const onFocusOut = event => {
    const from = event.target;
    if (!(from instanceof Element) || !box.contains(from)) return;
    const next = event.relatedTarget;
    if (next instanceof Node && box.contains(next)) return;
    const lost = from.getAttribute("data-bat-action");
    if (!lost) return;
    displaced = lost;
    queueMicrotask(() => { if (displaced === lost) displaced = null; });
  };
  document.addEventListener("focusout", onFocusOut, true);
  const placeFocus = key => {
    if (!alive() || !key) return;
    const nodes = [...content.querySelectorAll("[data-bat-action]")];
    const find = name => nodes.find(node => node.getAttribute("data-bat-action") === name);
    const usable = node => node && !node.disabled && !node.closest("[hidden]");
    const current = find(key);
    if (usable(current)) { current.focus({preventScroll: true}); return; }
    const named = key === "review" || key === "review-again" ? find("launch")
      : key === "launch" || key === "retry" ? (usable(find("new")) ? find("new") : find("read"))
      : key === "cancel" || key === "new" ? find("profile") : null;
    if (usable(named)) { named.focus({preventScroll: true}); return; }
    const group = [...content.querySelectorAll(".actions button, .actions a")].filter(usable);
    if (group.length) { group[0].focus({preventScroll: true}); return; }
    const any = nodes.filter(usable);
    if (any.length) { any[0].focus({preventScroll: true}); return; }
    message.tabIndex = -1; message.focus({preventScroll: true});
  };
  const run = async (name, fn) => {
    if (!alive() || busy) return;
    busy = true; pressed = name; message.textContent = ""; render();
    try {await fn();}
    catch (error) {if (alive()) message.textContent = `${t("bat_problem")} ${String(error)}`;}
    finally {busy = false; pressed = null; if (alive()) render();}
  };
  const readReceipt = async () => {
    if (!intent?.attempted) return;
    const value = await fleetControl({action:"launch_status",launch_id:intent.preview_id});
    if (alive()) {acceptReceipt(value); if (!value) message.textContent = t("bat_no_receipt");}
  };
  const read = async () => {
    let committed = false;
    try {
      const available = await fleetAvailability(); if (!alive()) return;
      if (!available.configured || !available.platform_supported || !available.native_controls) throw new Error(t("bat_missing"));
      const value = await fleetControl({action:"overview"}); if (!alive()) return;
      if (value?.control_version !== 1 || !binding(value.configuration?.binding) || !Array.isArray(value.profiles)
        || value.profiles.length > 1001 || value.profiles.some(p=>!profile(p.id) || typeof p.label !== "string" || !(p.connection === null || typeof p.connection === "string"))
        || new Set(value.profiles.map(p=>p.id)).size !== value.profiles.length || !Array.isArray(value.selection?.connections)) throw new Error(t("bat_missing"));
      overview = value; readable = value.configuration.valid === true && !value.pending_migration; committed = true;
      await readReceipt();
    } catch (error) {
      if (!committed && alive()) readable = false;
      throw error;
    }
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
  const reviewBody = async () => {
    if (!readable || intent && (!restoredPreview || intent.attempted) || damaged || !profile(chosen)) return;
    const previous = intent?.preview_id, selected = intent?.profile_id || chosen;
    const value = await fleetControl({action:"preview_profile", profile_id:selected}); if (!alive()) return;
    const next = {version:1, profile_id:selected, preview_id:value?.preview_id, configuration_binding:value?.configuration_binding,
      summary:value?.summary, attempted:false};
    if (!id(next.preview_id) || !binding(next.configuration_binding) || next.configuration_binding !== overview.configuration.binding
      || !summaryMatches(next.summary, next)) throw new Error(t("bat_wrong_receipt"));
    intent = next; receipt = null; persist(intent); restoredPreview = false;
    if (previous && previous !== next.preview_id && alive()) await fleetControl({action:"discard",preview_id:previous});
  };
  const launchBody = async () => {
    if (!intent || damaged || restoredPreview || intent.summary.already_running || receipt) return;
    // Persist the exact original handle before every explicit attempt. Core reads its durable
    // receipt before considering another effect; unknown/prepared receipts cannot re-spawn.
    const next = {...intent, attempted:true}; persist(next); intent = next;
    guard(); const value = await fleetControl({action:"launch",preview_id:intent.preview_id});
    if (alive()) acceptReceipt(value);
  };
  const resetBody = async () => {
    if (!intent || intent.attempted && !terminal()) return;
    localStorage.removeItem(storageKey);
    const old = intent.preview_id; intent = receipt = null; restoredPreview = false; chosen = "";
    await fleetControl({action:"discard",preview_id:old});
  };
  const render = () => {
    if (!alive()) return;
    const active = document.activeElement;
    let key = active instanceof Element && content.contains(active) ? active.getAttribute("data-bat-action") : null;
    if (!key && (active === document.body || !active) && displaced) key = displaced;
    displaced = null;
    content.hidden = !expanded;
    if (!expanded) { message.removeAttribute("aria-busy"); return; }
    let flying = false;
    const button = (label, action, fn, staticDisabled=false) => {
      const blocked = Boolean(staticDisabled) && pressed !== action;
      const soft = busy && !blocked;
      if (soft) flying = true;
      return h("button", {type:"button", class:"secondary", "data-bat-action":action, disabled:blocked || null, "aria-disabled":soft ? "true" : null, onclick:fn}, t(label));
    };
    const fixed = !!intent || damaged;
    const picker = h("select", {"aria-label":t("bat_profile"), "data-bat-action":"profile", disabled:busy||fixed||!readable, onchange:e=>{chosen=e.target.value;render();}},
      h("option", {value:"",selected:!chosen},t("bat_choose")), ...(overview?.profiles || []).map(p=>h("option",{value:p.id,selected:chosen===p.id},`${p.label} · ${p.id}`)));
    const lines = [h("p",{class:"muted"},t("bat_search_help")),h("label",{},t("bat_profile")," ",picker)];
    if (intent) {
      lines.push(h("p",{class:"note"}, t("bat_chosen",{profile:intent.profile_id})," · ",h("code",{},intent.preview_id)),
        h("p",{class:"muted"},receipt?t("fleet_launch_"+(receipt.state==="prepared"?"uncertain":receipt.state))
          :restoredPreview?t("bat_preview_expired"):intent.summary.already_running?t("fleet_launch_already_running"):t(intent.attempted?"bat_unknown":"bat_reviewed")));
      if (intent.summary.bat_may_open_local_window) lines.push(h("p",{class:"muted"},t("fleet_local_anchor")));
    }
    if (damaged) lines.push(h("p",{class:"error"},t("bat_saved_invalid")));
    const buttons = [button("bat_read","read",()=>run("read", read))];
    if (!fixed) buttons.unshift(button("bat_review","review",()=>run("review", reviewBody),!readable||!chosen));
    if (restoredPreview) buttons.unshift(button("bat_review_again","review-again",()=>run("review-again", reviewBody),!readable));
    if (intent && !restoredPreview && !receipt && !intent.summary.already_running) buttons.unshift(button(intent.attempted?"bat_retry":"bat_launch", intent.attempted?"retry":"launch", ()=>run(intent.attempted?"retry":"launch", launchBody), !readable));
    if (intent && (!intent.attempted || terminal())) buttons.push(button(intent.attempted?"bat_new":"cancel", intent.attempted?"new":"cancel", ()=>run(intent.attempted?"new":"cancel", resetBody)));
    lines.push(h("div",{class:"actions"},...buttons),h("p",{class:"muted"},t("bat_preferences")," ",h("a",{href:"#/settings","data-bat-action":"settings"},t("nav_settings"))));
    content.replaceChildren(...lines);
    // An empty status is display:none, so it leaves the accessibility tree. Keep a
    // clipped mark while a button is aria-disabled so aria-busy stays observable.
    if (flying) {
      if (!message.textContent) message.textContent = "\u2060";
      message.setAttribute("aria-busy","true");
    } else {
      message.removeAttribute("aria-busy");
      if (message.textContent === "\u2060") message.textContent = "";
    }
    const parked = message.textContent === "\u2060";
    message.style.position = parked ? "absolute" : "";
    message.style.width = parked ? "1px" : "";
    message.style.height = parked ? "1px" : "";
    message.style.overflow = parked ? "hidden" : "";
    message.style.clipPath = parked ? "inset(50%)" : "";
    if (key) placeFocus(key);
    displaced = null;
  };
  box.append(content, copyFallback, message);
  render();
  // Reopening only reads the original receipt. It never creates a new preview or launches.
  if (nativeDesktop && expanded) queueMicrotask(()=>run("read", read));
  return {box, update:render, dispose:()=>{disposed=true; document.removeEventListener("focusout", onFocusOut, true);},
    copyTitle: () => copy("title"), copyId: () => copy("id"),
    open: () => {expanded = true; run("read", read);}};
}
