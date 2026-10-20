// Candidate rollout only. Policy may restrict the preview, never grant acceptance.
/** @param {{search?: string, policy?: {allowWorkbench?: boolean}}} [input] */
export function decideLayout({search = '', policy} = {}) {
  const requested = new URLSearchParams(search).get('workbench');
  const blocked = requested === '1' && policy?.allowWorkbench === false;
  return Object.freeze({
    layout: requested === '1' && !blocked ? 'workbench' : 'classic',
    source: blocked ? 'policy' : requested === null ? 'default' : 'url',
    status: 'preview',
    reason: blocked ? 'policy_disabled' : requested !== null && !['0', '1'].includes(requested)
      ? 'unrecognized_workbench_value' : null,
  });
}

// Capture once per page load. Layout never contributes to a storage identity.
export const layoutDecision = decideLayout({search: globalThis.location?.search || ''});
