import {fleetStatus, rememberFleetDetails} from "./fleet-status.js";
import {button} from './ui/components.js';
import {fleetAvailability, fleetRequest} from "./transport/index.ts";

// Local Fleet state never shares the central event cursor, principal or operation journal.
export async function mountFleet(main, {h, t, onStatus = () => {}}) {
  if (!main.isConnected) return () => {};
  const panel = h("section", {class: "panel", "aria-label": t("fleet_title")});
  const content = h("div"), message = h("p", {class: "muted", role: "status"});
  panel.append(h("h2", {}, t("fleet_title")), h("p", {class: "muted"}, t("fleet_help")), content, message);
  main.append(panel);
  let nativeCleanup, bootstrapCleanup;
  let disposed = false, busy = false, readable = false, timer, snapshot, draft, binding, pressed = null;
  const detailsState = new Map();
  const alive = () => !disposed && panel.isConnected;
  const key = () => `batc.desktop.fleet.selection.${binding}`;
  const save = () => { try { if (draft) sessionStorage.setItem(key(), JSON.stringify(draft)); else sessionStorage.removeItem(key()); } catch { /* Keep the mounted draft when storage is unavailable. */ } };
  const same = (a, b) => a.length === b.length && [...a].sort().every((x, i) => x === [...b].sort()[i]);
  const changed = () => draft && (draft.revision !== snapshot.selection.revision || draft.epoch !== snapshot.monitor.epoch);
  // Static reasons stay disabled. Only an in-flight read blocks a control with aria-disabled, so it stays focusable.
  const canEdit = () => readable && snapshot?.configuration.valid && (snapshot.monitor.state === "stopped" || snapshot.monitor.controllable);
  const gate = (blocked, action) => busy && action && pressed === action ? {"aria-disabled": "true"} : blocked ? {disabled: true} : busy ? {"aria-disabled": "true"} : {};
  const focusKeys = ["data-fleet-action", "data-fleet-name", "data-fleet-details"];
  const focusKey = node => {
    for (let current = node; current && current !== panel; current = current.parentElement) {
      for (const attr of focusKeys) if (current.hasAttribute?.(attr)) return [attr, current.getAttribute(attr)];
    }
    return null;
  };
  const controlByKey = key => {
    if (!key) return null;
    for (const node of content.querySelectorAll("button, input, select, summary")) if (node.getAttribute(key[0]) === key[1]) return node;
    return null;
  };
  // Forward within the same action row. A removed or disabled control continues at the status text.
  const groups = [["apply", "refresh", "use-current"], ["start", "quit"]];
  const successor = key => {
    if (key?.[0] === "data-fleet-action") {
      const group = groups.find(items => items.includes(key[1]));
      if (group) for (const candidate of group.slice(group.indexOf(key[1]) + 1)) {
        const node = controlByKey(["data-fleet-action", candidate]);
        if (node && !node.disabled && node.getAttribute("aria-disabled") !== "true") return node;
      }
    }
    message.tabIndex = -1;
    return message;
  };
  const render = () => {
    if (!alive()) return;
    rememberFleetDetails(content, detailsState);
    if (!snapshot) {
      content.replaceChildren(h('button', {class: 'secondary', disabled: busy, 'data-fleet-action': 'refresh',
        onclick: () => read(true)}, t('fleet_refresh')));
      return;
    }
    const names = draft?.connections || snapshot.selection.connections;
    const ready = new Map([...(snapshot.readiness.hosts || []), snapshot.readiness.connector].filter(Boolean).map(row => [row.name, row]));
    const previous = panel.contains(document.activeElement) ? focusKey(document.activeElement) : null;
    const rows = snapshot.configuration.connections.map(entry => {
      const checked = names.includes(entry.name);
      const blocked = !canEdit() || !!changed();
      const input = h("input", {type: "checkbox", checked, ...gate(blocked), "data-fleet-name": entry.name,
        onkeydown: event => { if (busy && event.key === " ") event.preventDefault(); },
        onclick: event => { if (busy) event.preventDefault(); },
        onchange: () => {
          if (busy || blocked) { input.checked = checked; return; }
          if (!draft) draft = {revision: snapshot.selection.revision, epoch: snapshot.monitor.epoch, connections: [...snapshot.selection.connections]};
          draft.connections = input.checked ? [...new Set([...draft.connections, entry.name])] : draft.connections.filter(x => x !== entry.name);
          if (same(draft.connections, snapshot.selection.connections)) draft = null;
          save(); render();
        }});
      const observed = ready.get(entry.name);
      return h("div", {class: "fleet-machine"}, h("label", {class: "fleet-choice"}, input, " ", entry.label),
        fleetStatus(observed, {h, t, snapshot, readable, entry, detailsState}));
    });
    content.replaceChildren(...[h("p", {}, readable ? t("fleet_monitor_" + snapshot.monitor.state) : t('fleet_check_title_unavailable')),
      !snapshot.configuration.valid ? h("p", {class: "error"}, t("fleet_invalid", {n: snapshot.configuration.issue_count})) : null,
      snapshot.monitor.state === "running" && !snapshot.monitor.controllable ? h("p", {class: "note"}, t("fleet_other_owner")) : null,
      h('div', {class: 'actions'}, button({icon: "refresh-cw", label: t("fleet_refresh"), ...gate(false, "refresh"), "data-fleet-action": "refresh", onclick: () => { if (busy) return; read(true); }})),
      ...rows,
      h("p", {class: "muted"}, t(snapshot.selection.applied_revision === snapshot.selection.revision ? "fleet_applied" : "fleet_waiting")),
      changed() ? h("p", {class: "error"}, t("fleet_changed")) : h("span"),
      h("div", {class: "actions"},
        h("button", {class: "primary", ...gate(!canEdit() || !draft || !!changed(), "apply"), "data-fleet-action": "apply", onclick: () => {
          if (busy || !canEdit() || !draft || changed()) return;
          mutate({action: "set_connections", connections: [...draft.connections], expected_selection_revision: draft.revision,
            expected_monitor_epoch: draft.epoch, expected_configuration_binding: binding}, "apply");
        }}, t("fleet_apply")),
        h("button", {class: "secondary", ...gate(!readable || !draft, "use-current"), "data-fleet-action": "use-current", onclick: () => {
          if (busy || !readable || !draft) return;
          draft = null; save(); message.textContent = ""; render();
        }}, t("fleet_use_current"))),
      h("div", {class: "actions"},
        h("button", {class: "secondary", ...gate(!canEdit() || !!draft || snapshot.monitor.state !== "stopped", "start"), "data-fleet-action": "start", onclick: () => {
          if (busy || !canEdit() || draft || snapshot.monitor.state !== "stopped") return;
          mutate({action: "ensure_monitor", expected_configuration_binding: binding}, "start");
        }}, t("fleet_start")),
        h("button", {class: "danger", ...gate(!canEdit() || !!draft || !snapshot.monitor.controllable, "quit"), "data-fleet-action": "quit", onclick: () => {
          if (busy || !canEdit() || draft || !snapshot.monitor.controllable) return;
          mutate({action: "quit_owned", expected_configuration_binding: binding, expected_monitor_epoch: snapshot.monitor.epoch}, "quit");
        }}, t("fleet_quit")))].filter(Boolean));
    if ([...content.querySelectorAll("button")].some(node => node.getAttribute("aria-disabled") === "true")) message.setAttribute("aria-busy", "true");
    else message.removeAttribute("aria-busy");
    const again = controlByKey(previous);
    if (again && !again.disabled) again.focus();
    else if (previous) successor(previous).focus();
  };
  const accept = value => {
    if (!value?.configuration?.connections || !value?.monitor || !value?.selection || !value?.readiness) throw new Error(t("fleet_unavailable"));
    if (binding !== value.configuration.binding) {
      binding = value.configuration.binding; draft = null; detailsState.clear();
      try {
        const old = JSON.parse(sessionStorage.getItem(key()) || "null");
        if (old && Array.isArray(old.connections) && old.connections.every(x => typeof x === "string") && typeof old.revision === "string") draft = old;
      } catch { /* Invalid local draft cannot authorize any native action. */ }
    }
    snapshot = value; readable = true;
    onStatus(value);
    // A lost reply may already have saved the exact requested set. Read it back; never resend automatically.
    if (draft && same(draft.connections, value.selection.connections)) {draft = null; save();}
  };
  const read = async (explicit = false) => {
    if (!alive() || busy) return;
    if (explicit) pressed = "refresh";
    busy = true; render();
    try {
      const value = await fleetRequest({action: "status"});
      if (!alive()) return;
      accept(value); if (explicit) message.textContent = "";
    } catch (error) {
      if (alive()) { readable = false; message.textContent = `${t("fleet_read_failed")} ${String(error)}`; }
    } finally {busy = false; pressed = null; render();}
  };
  const mutate = async (input, action) => {
    if (busy || !alive() || !canEdit()) return;
    pressed = action;
    busy = true; readable = false; message.textContent = t("fleet_working"); render();
    try {
      const result = await fleetRequest(input);
      if (!alive()) return;
      if (input.action !== "quit_owned") accept(result);
      message.textContent = t(input.action === "quit_owned" ? "fleet_quit_requested" : "fleet_saved");
    } catch (error) {
      if (alive()) message.textContent = `${t("fleet_unknown")} ${String(error)}`;
    } finally {busy = false; pressed = null; render();}
  };
  try {
    const availability = await fleetAvailability();
    if (!alive()) return () => {disposed = true;};
    if (!availability.configured || !availability.platform_supported) {
      message.textContent = t(!availability.platform_supported ? "fleet_windows" : "fleet_setup");
    } else if (availability.native_controls === true) {
      const {mountFleetDesktop} = await import("./fleet-desktop.js");
      if (alive()) {panel.remove(); nativeCleanup = await mountFleetDesktop(main, {h, t, onStatus});
        if (availability.bootstrap_controls === true && !disposed && main.isConnected) {
          const {mountFleetBootstrap} = await import("./fleet-bootstrap.js");
          bootstrapCleanup = await mountFleetBootstrap(main, {h, t});
        }
      }
    } else {
      document.addEventListener('health-display-tick', render);
      await read();
      const poll = async () => {if (!alive()) return; await read(); if (alive()) timer = setTimeout(poll, 5000);};
      timer = setTimeout(poll, 5000);
    }
  } catch { message.textContent = t("fleet_unavailable"); }
  return () => {disposed = true; clearTimeout(timer); document.removeEventListener('health-display-tick', render); nativeCleanup?.(); bootstrapCleanup?.(); panel.remove();};
}
