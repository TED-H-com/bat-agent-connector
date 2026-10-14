import {sessionOrigin} from './session-navigation.js';
export const MAX_DISCUSS_REFS = 8;
export const MAX_DISCUSS_TITLE = 160;

// Mirror pm_thread_contexts.py KINDS/validate. Source access remains central.
const contextFields = {
  snapshot: {id: /^pmsnap_[0-9a-f]{32}$/},
  plan_revision: {id: /^pmrev_[0-9a-f]{32}$/},
  requirement: {id: /^[a-z][a-z0-9_.-]{0,79}$/, revision_id: /^pmrev_[0-9a-f]{32}$/},
  milestone: {id: /^[a-z][a-z0-9_.-]{0,79}$/, revision_id: /^pmrb_[0-9a-f]{32}$/},
  work_item: {id: /^wi_[0-9a-f]{20}$/},
  governance: {id: /^pmgov_[0-9a-f]{32}$/},
  finding: {id: /^pmfn_[0-9a-f]{32}$/, observation_id: /^pmgh_[0-9a-f]{32}$/, document_digest: /^[0-9a-f]{64}$/},
  repair: {id: /^pmrp_[0-9a-f]{32}$/},
  follow_up: {id: /^pmfu_[0-9a-f]{32}$/},
  proposal: {id: /^pmcp_[0-9a-f]{32}$/},
  issue_mapping: {id: /^pmim_[0-9a-f]{32}$/},
  meeting: {id: /^pmmeet_[0-9a-f]{32}$/},
  lifecycle_transition: {id: /^pmlt_[0-9a-f]{32}$/},
};
const projectViews = new Set([
  'overview', 'discussions', 'requirements', 'schedule', 'dependencies', 'issues', 'agents', 'board',
]);

function fullMatch(value, pattern) {
  // JS $ also matches before a final newline; Python re.fullmatch does not.
  return typeof value === 'string' && pattern.exec(value)?.[0] === value;
}

export function isContextRef(ref) {
  if (!ref || typeof ref !== 'object' || Array.isArray(ref)
      || typeof ref.kind !== 'string' || !Object.hasOwn(contextFields, ref.kind)) return false;
  const fields = Object.entries(contextFields[ref.kind]);
  return Object.hasOwn(ref, 'kind') && Object.keys(ref).length === fields.length + 1
    && fields.every(([key, pattern]) => Object.hasOwn(ref, key) && fullMatch(ref[key], pattern));
}

function normalizeRefs(refs) {
  const result = [], seen = new Set();
  if (!Array.isArray(refs)) return result;
  for (const ref of refs) {
    if (!isContextRef(ref)) continue;
    // Fixed field order makes identity independent of the caller's property order.
    const item = {kind: ref.kind};
    for (const key of Object.keys(contextFields[ref.kind])) item[key] = ref[key];
    const identity = JSON.stringify(item);
    if (seen.has(identity)) continue;
    seen.add(identity);
    result.push(item);
    if (result.length === MAX_DISCUSS_REFS) break;
  }
  return result;
}

function normalizeDetails(title, from, report) {
  return {
    // Python len(str) counts code points, not UTF-16 code units.
    title: typeof title === 'string' ? Array.from(title.trim()).slice(0, MAX_DISCUSS_TITLE).join('') : '',
    from: projectViews.has(from) ? from : sessionOrigin(from),
    report: fullMatch(report, contextFields.snapshot.id) ? report : '',
  };
}

export function discussHref({workspaceId, refs = [], title = '', from = '', report = ''}) {
  const query = new URLSearchParams();
  // One JSON object per query value: no field delimiters can collide with IDs.
  for (const ref of normalizeRefs(refs)) query.append('ref', JSON.stringify(ref));
  for (const [key, value] of Object.entries(normalizeDetails(title, from, report))) {
    if (value) query.set(key, value);
  }
  const search = query.toString();
  return `#/pm/${encodeURIComponent(workspaceId)}/discussions/new${search ? '?' + search : ''}`;
}

export function parseDiscussRefs(query) {
  // URLSearchParams tolerates malformed percent escapes; do not decode twice.
  const params = new URLSearchParams(typeof query === 'string' || query instanceof URLSearchParams ? query : '');
  const refs = params.getAll('ref').map(value => {
    try { return JSON.parse(value); } catch { return null; }
  });
  return {
    refs: normalizeRefs(refs),
    ...normalizeDetails(params.get('title'), params.get('from'), params.get('report')),
  };
}
