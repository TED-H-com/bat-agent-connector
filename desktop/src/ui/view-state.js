// Bounded UI preferences only. Never store fetched records, counts, or permissions here.
const states = new Map();
export function viewState(namespace, workspaceId, view) {
  const key = JSON.stringify([namespace, workspaceId, view]);
  const value = states.get(key) || {};
  states.delete(key); states.set(key, value);
  if (states.size > 40) states.delete(states.keys().next().value);
  return value;
}
