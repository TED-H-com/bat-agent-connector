// Candidate rollout only. No business state, IDs or drafts are migrated.
export const workbenchEnabled = () => new URLSearchParams(location.search).get('workbench') === '1';
