import {iconButton} from './ui/components.js';
import {fleetAvailability, fleetRequest} from "./transport/index.ts";

// Local Fleet state never shares the central event cursor, principal or operation journal.
export async function mountFleet(main, {h, t}) {
  if (!main.isConnected) return () => {};
  const panel = h("section", {class: "panel", "aria-label": t("fleet_title")});
  const content = h("div"), message = h("p", {class: "muted", role: "status"});
  panel.append(h("h2", {}, t("fleet_title")), h("p", {class: "muted"}, t("fleet_help")), content, message);
  main.append(panel);
  let nativeCleanup, bootstrapCleanup;
  let disposed = false, busy = false, readable = false, timer, snapshot, draft, binding;
  const alive = () => !disposed && panel.isConnected;
  const key = () => `batc.desktop.fleet.selection.${binding}`;
  const save = () => { try { if (draft) sessionStorage.setItem(key(), JSON.stringify(draft)); else sessionStorage.removeItem(key()); } catch { /* Keep the mounted draft when storage is unavailable. */ } };
  const same = (a, b) => a.length === b.length && [...a].sort().every((x, i) => x === [...b].sort()[i]);
  const changed = () => draft && (draft.revision !== snapshot.selection.revision || draft.epoch !== snapshot.monitor.epoch);
  const editable = () => readable && !busy && snapshot?.configuration.valid && (snapshot.monitor.state === "stopped" || snapshot.monitor.controllable);
  const render = () => {
    if (!alive() || !snapshot) return;
    const names = draft?.connections || snapshot.selection.connections;
    const ready = new Map([...(snapshot.readiness.hosts || []), snapshot.readiness.connector].filter(Boolean).map(row => [row.name, row]));
    const focus = document.activeElement?.dataset?.fleetName;
    const rows = snapshot.configuration.connections.map(entry => {
      const input = h("input", {type: "checkbox", checked: names.includes(entry.name), disabled: !editable() || !!changed(),
        "data-fleet-name": entry.name, onchange: () => {
          if (!draft) draft = {revision: snapshot.selection.revision, epoch: snapshot.monitor.epoch, connections: [...snapshot.selection.connections]};
          draft.connections = input.checked ? [...new Set([...draft.connections, entry.name])] : draft.connections.filter(x => x !== entry.name);
          if (same(draft.connections, snapshot.selection.connections)) draft = null;
          save(); render();
        }});
      const observed = ready.get(entry.name);
      return h("div", {class: "row"}, h("label", {class: "grow fleet-choice"}, input, " ", entry.label),
        h("span", {class: "chip"}, t("fleet_" + (observed?.level || "unavailable"))),
        observed?.blocking ? h("span", {class: "muted"}, observed.blocking) : null);
    });
    content.replaceChildren(...[h("p", {}, t("fleet_monitor_" + snapshot.monitor.state), " · ",
      t("fleet_" + snapshot.readiness.state)),
      !snapshot.configuration.valid ? h("p", {class: "error"}, t("fleet_invalid", {n: snapshot.configuration.issue_count})) : null,
      snapshot.monitor.state === "running" && !snapshot.monitor.controllable ? h("p", {class: "note"}, t("fleet_other_owner")) : null,
      ...rows,
      h("p", {class: "muted"}, t(snapshot.selection.applied_revision === snapshot.selection.revision ? "fleet_applied" : "fleet_waiting")),
      changed() ? h("p", {class: "error"}, t("fleet_changed")) : h("span"),
      h("div", {class: "actions"},
        h("button", {class: "primary", disabled: !editable() || !draft || !!changed(), onclick: () => mutate({action: "set_connections",
          connections: [...draft.connections], expected_selection_revision: draft.revision, expected_monitor_epoch: draft.epoch,
          expected_configuration_binding: binding})}, t("fleet_apply")),
        iconButton({icon:'refresh-cw', label:t('fleet_refresh'), ...{class: "secondary", disabled: busy, onclick: () => read(true)}}),
        h("button", {class: "secondary", disabled: !readable || busy || !draft, onclick: () => {draft = null; save(); message.textContent = ""; render();}}, t("fleet_use_current"))),
      h("div", {class: "actions"},
        h("button", {class: "secondary", disabled: !editable() || !!draft || snapshot.monitor.state !== "stopped",
          onclick: () => mutate({action: "ensure_monitor", expected_configuration_binding: binding})}, t("fleet_start")),
        h("button", {class: "danger", disabled: !editable() || !!draft || !snapshot.monitor.controllable,
          onclick: () => mutate({action: "quit_owned", expected_configuration_binding: binding, expected_monitor_epoch: snapshot.monitor.epoch})}, t("fleet_quit")))].filter(Boolean));
    if (focus) [...content.querySelectorAll("input")].find(input => input.dataset.fleetName === focus)?.focus();
  };
  const accept = value => {
    if (!value?.configuration?.connections || !value?.monitor || !value?.selection || !value?.readiness) throw new Error(t("fleet_unavailable"));
    if (binding !== value.configuration.binding) {
      binding = value.configuration.binding; draft = null;
      try {
        const old = JSON.parse(sessionStorage.getItem(key()) || "null");
        if (old && Array.isArray(old.connections) && old.connections.every(x => typeof x === "string") && typeof old.revision === "string") draft = old;
      } catch { /* Invalid local draft cannot authorize any native action. */ }
    }
    snapshot = value; readable = true;
    // A lost reply may already have saved the exact requested set. Read it back; never resend automatically.
    if (draft && same(draft.connections, value.selection.connections)) {draft = null; save();}
  };
  const read = async (explicit = false) => {
    if (!alive() || busy) return;
    busy = true; render();
    try {
      const value = await fleetRequest({action: "status"});
      if (!alive()) return;
      accept(value); if (explicit) message.textContent = "";
    } catch (error) {
      if (alive()) { readable = false; message.textContent = `${t("fleet_read_failed")} ${String(error)}`; }
    } finally {busy = false; render();}
  };
  const mutate = async input => {
    if (!editable() || !alive()) return;
    busy = true; readable = false; message.textContent = t("fleet_working"); render();
    try {
      const result = await fleetRequest(input);
      if (!alive()) return;
      if (input.action !== "quit_owned") accept(result);
      message.textContent = t(input.action === "quit_owned" ? "fleet_quit_requested" : "fleet_saved");
    } catch (error) {
      if (alive()) message.textContent = `${t("fleet_unknown")} ${String(error)}`;
    } finally {busy = false; render();}
  };
  try {
    const availability = await fleetAvailability();
    if (!alive()) return () => {disposed = true;};
    if (!availability.configured || !availability.platform_supported) {
      message.textContent = t(!availability.platform_supported ? "fleet_windows" : "fleet_setup");
    } else if (availability.native_controls === true) {
      const {mountFleetDesktop} = await import("./fleet-desktop.js");
      if (alive()) {panel.remove(); nativeCleanup = await mountFleetDesktop(main, {h, t});
        if (availability.bootstrap_controls === true && !disposed && main.isConnected) {
          const {mountFleetBootstrap} = await import("./fleet-bootstrap.js");
          bootstrapCleanup = await mountFleetBootstrap(main, {h, t});
        }
      }
    } else {
      await read();
      const poll = async () => {if (!alive()) return; await read(); if (alive()) timer = setTimeout(poll, 5000);};
      timer = setTimeout(poll, 5000);
    }
  } catch { message.textContent = t("fleet_unavailable"); }
  return () => {disposed = true; clearTimeout(timer); nativeCleanup?.(); bootstrapCleanup?.(); panel.remove();};
}
