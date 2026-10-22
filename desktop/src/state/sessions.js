// Presentation identities only: workspace labels never imply a repository or project.
const text = value => typeof value === "string" ? value : "";
export function workspaceGroup(session) {
  const host = text(session.host), id = text(session.workspace_id), name = text(session.workspace);
  return {key: JSON.stringify([host, id ? "id" : name ? "label" : "unknown", id || name]), host, id, name};
}
export function groupedSessions(sessions) {
  const groups = new Map();
  for (const session of sessions) {
    const group = workspaceGroup(session);
    if (!groups.has(group.key)) groups.set(group.key, {...group, sessions: []});
    groups.get(group.key).sessions.push(session);
  }
  return [...groups.values()].sort((a, b) => a.host.localeCompare(b.host) ||
    (a.name || a.id).localeCompare(b.name || b.id) || a.key.localeCompare(b.key));
}
export function matchesSession(session, query) {
  const haystack = [session.title, session.session_id, session.host, session.workspace,
    session.workspace_id, session.agent_kind, session.model, session.worktree_branch,
    ...(Array.isArray(session.connector_metadata?.labels) ? session.connector_metadata.labels : [])]
    .map(text).join("\n").toLocaleLowerCase();
  return query.trim().toLocaleLowerCase().split(/\s+/).every(word => haystack.includes(word));
}
export function runtimeStale(session) {
  return Boolean(session.stale || session.fields_stale || session.state?.evidence?.activity?.stale);
}
// Independent facts from the same envelope. A pending prompt remains a fact of
// its observation even when output evidence is stale; it grants no action rights.
export function sessionFacets(session) {
  const source = session || {}, pending = source.pending;
  const managedStatus = source.api_access === "managed" ? source.status || source.state?.status : null;
  const attention = pending?.kind === "permission" || pending?.kind === "ask_user" ? "asking_you"
    : pending ? "needs_you" : ["failed", "error"].includes(managedStatus) ? "failed"
      : ["needs_ted", "needs_you", "needs_attention", "waiting_permission", "awaiting_approval", "uncertain", "blocked"].includes(managedStatus) ? "needs_you" : "none";
  const staleness = {runtime: runtimeStale(source),
    gone: Boolean(source.gone_at) || source.state?.enumeration === "gone",
    missing: source.state?.enumeration === "missing"};
  const lifecycle = source.state?.lifecycle || "unknown";
  // Stale observations must not claim that output is still arriving.
  const activity = Object.values(staleness).some(Boolean) ? "unknown"
    : lifecycle === "ended" ? "not_streaming"
      : source.streaming === true ? "streaming" : source.streaming === false ? "not_streaming" : "unknown";
  return {activity, attention, staleness, lifecycle, pendingKind: pending?.kind,
    hasPending: Boolean(pending), notLoaded: source.loaded === false || source.state?.loading === "not_loaded"};
}
export function sessionActivity(session) {
  const facets = sessionFacets(session);
  if (facets.hasPending) return {key: "pending_" + facets.pendingKind, tone: "stale"};
  if (facets.attention === "failed") return {key: "op_failed", tone: "bad"};
  if (facets.attention === "needs_you") return {key: "workspace_needs_you", tone: "stale"};
  if (facets.lifecycle === "ended") return {key: "obs_value_ended", tone: ""};
  if (facets.staleness.gone || facets.staleness.missing) return {key: "sessions_not_seen", tone: "stale"};
  if (facets.staleness.runtime) return {key: "sessions_stale", tone: "stale"};
  if (facets.activity === "streaming") return {key: "obs_value_streaming", tone: "info"};
  if (facets.activity === "not_streaming") return {key: "obs_value_not_streaming", tone: ""};
  if (facets.notLoaded) return {key: "sessions_not_loaded", tone: ""};
  return {key: "sessions_activity_unknown", tone: ""};
}

// Rows and the session header show a pill only when the session needs the user, is running,
// is out of sync, or (for managed sessions) has a failed or attention state.
export function sessionStatus(session) {
  const facets = sessionFacets(session);
  // Preserve the existing single-pill order: pending, managed attention/failure,
  // stale/gone/missing, then current streaming. This is not a new lane priority.
  if (facets.attention !== "none") return facets.attention;
  if (Object.values(facets.staleness).some(Boolean)) return "stale";
  if (facets.activity === "streaming") return "running";
  return null;
}

export function sessionPill(session) {
  const status = sessionStatus(session);
  if (status === "asking_you" || status === "needs_you") return {key: "sessions_pill_waiting", tone: "stale", status};
  if (status === "failed") return {key: "op_failed", tone: "bad", status};
  if (status === "stale") return {key: "sessions_pill_stale", tone: "stale", status};
  if (status === "running") return {key: "sessions_pill_running", tone: "info", status};
  return null;
}
