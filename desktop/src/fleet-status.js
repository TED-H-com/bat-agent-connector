// Shared local Fleet presentation; native status contains codes, never prose.
export function fleetStatusKey(code) {
  return {TAILSCALE_WAKING: "fleet_tailscale_waking", ROUTE_UNAVAILABLE: "fleet_route_unavailable", PROBE_PENDING: "fleet_probe_pending"}[code];
}

export function fleetStatus(row, {h, t}) {
  // Native rows separate the diagnostic code from its blocking layer. Legacy
  // rows may have only blocking; retain both fields when they are supplied.
  const reason = [row?.code, row?.blocking].find(value => typeof value === "string" && value.length);
  if (!reason) return null;
  const key = fleetStatusKey(reason);
  return h("div", {class: "muted", "data-fleet-route-status": true},
    key ? h("span", {role: "status"}, t(key)) : null,
    h("details", {}, h("summary", {}, key ? t("fleet_status_details") : `${t("fleet_status_details")}: ${reason}`),
      h("code", {}, reason),
      row?.blocking && row.blocking !== reason ? h("code", {}, row.blocking) : null));
}
