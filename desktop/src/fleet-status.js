// Shared local Fleet presentation; native status contains codes, never prose.
export function fleetStatusKey(code) {
  return {TAILSCALE_WAKING: "fleet_tailscale_waking", ROUTE_UNAVAILABLE: "fleet_route_unavailable", PROBE_PENDING: "fleet_probe_pending"}[code];
}

export function fleetStatus(row, {h, t}) {
  const key = fleetStatusKey(row?.code);
  if (!key) return null;
  return h("div", {class: "muted", "data-fleet-route-status": true},
    h("span", {role: "status"}, t(key)),
    h("details", {}, h("summary", {}, t("fleet_status_details")), h("code", {}, row.code)));
}
