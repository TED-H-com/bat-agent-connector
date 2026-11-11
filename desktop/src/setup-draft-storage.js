// Shared privacy projection for form persistence and managed-origin migration.
const explicitPort = value => value.replace(/^wss?:\/\//i, '').split(/[/?#]/, 1)[0].match(/:(\d+)$/)?.[1];
export function validSetupEndpoint(value) {
  try {
    const url = new URL(value);
    return value.length <= 512 && !/\s/.test(value) && /^wss:\/\//i.test(value) &&
      url.protocol === 'wss:' && !!url.hostname && Number(explicitPort(value)) > 0 && !url.username && !url.password &&
      !value.includes('?') && !value.includes('#') && /^wss:\/\/[^/]+\/?$/i.test(value);
  } catch {return false;}
}
export const validSetupHostId = value => /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/.test(value);
// Guided repository setup accepts the credential-free GitHub forms in config._integrate_url.
export const validSetupRemoteUrl = value => typeof value === 'string' && !/\s/.test(value) &&
  /^(?:git@github\.com:|ssh:\/\/git@github\.com\/|https:\/\/github\.com\/)[A-Za-z0-9_.-]{1,100}\/[A-Za-z0-9_.-]{1,100}(?:\.git)?$/i.test(value);
const setupDraftFields = ['host', 'url', 'fingerprint', 'profile_id', 'import_profile_id', 'managed_roots', 'ssh_alias',
  'repository', 'repository_host', 'workspace_id', 'remote_url', 'verification_project', 'verification_executable',
  'verification_arguments', 'verification_timeout', 'writes', 'orchestrate', 'shared_clone_worktrees',
  'allow_integrate', 'allow_merge', 'allow_pr_update'];
const setupFlags = new Set(['writes', 'orchestrate', 'shared_clone_worktrees', 'allow_integrate', 'allow_merge', 'allow_pr_update']);
const object = value => !!value && typeof value === 'object' && !Array.isArray(value);
const text = value => typeof value === 'string';
const digest = value => text(value) && /^[0-9a-f]{64}$/.test(value);
const strings = value => Array.isArray(value) && value.every(text);
const commands = value => object(value) && Object.values(value).every(strings);
const verificationFields = {commands, timeout_s: Number.isInteger};
const requestFields = {
  'setup.host': {url: validSetupEndpoint, fingerprint: text, profile_id: text, import_profile_id: text,
    writes: value => typeof value === 'boolean', orchestrate: value => typeof value === 'boolean',
    shared_clone_worktrees: value => typeof value === 'boolean', managed_roots: strings, ssh_alias: text,
    git: value => ['ssh', 'local'].includes(value), local_confirmation: value => typeof value === 'boolean', source_digest: digest},
  'setup.repository': {host: text, workspace_id: text, remote_url: validSetupRemoteUrl,
    allow_integrate: value => typeof value === 'boolean', allow_merge: value => typeof value === 'boolean', allow_pr_update: value => typeof value === 'boolean'},
  'setup.verification': verificationFields,
};
const matchesFields = (value, schema) => object(value) && Object.entries(value).every(([key, entry]) =>
  Object.hasOwn(schema, key) && schema[key](entry));
export function setupRequestForStorage(request) {
  const schema = Object.hasOwn(requestFields, request?.action) ? requestFields[request.action] : null;
  const target = request?.action === 'setup.host' ? {host: text} : request?.action === 'setup.repository' ? {repository: text} : {};
  if (!schema || !matchesFields(request, {action: text, target: value => matchesFields(value, target),
    params: value => matchesFields(value, {...schema, secret_ref: value => text(value) && /^setup_[0-9a-f]{32}$/.test(value)}),
    preconditions: value => matchesFields(value, {config_revision: digest, review_digest: digest})})) return null;
  if (!object(request.params)) return null;
  return {action: request.action, target: structuredClone(request.target),
    params: Object.fromEntries(Object.keys(schema).filter(name => Object.hasOwn(request.params, name))
      .map(name => [name, structuredClone(request.params[name])])), preconditions: structuredClone(request.preconditions)};
}
export function setupDraftForStorage(draft) {
  const saved = Object.fromEntries(setupDraftFields.filter(name => Object.hasOwn(draft, name) &&
    (setupFlags.has(name) ? typeof draft[name] === 'boolean' : text(draft[name]))).map(name => [name, draft[name]]));
  if (saved.url && !validSetupEndpoint(saved.url)) saved.url = '';
  if (typeof saved.remote_url === 'string' && !validSetupRemoteUrl(saved.remote_url)) saved.remote_url = '';
  if (matchesFields(draft.verification_base, {...verificationFields, revision: digest}))
    saved.verification_base = structuredClone(draft.verification_base);
  if (draft.intent) {
    const intent = draft.intent;
    const original = intent.lookup_only === true && intent.lookup_request ? intent.lookup_request : intent.request;
    const request = setupRequestForStorage(original);
    saved.intent = Object.fromEntries(['key', 'operation_id', 'problemCode'].filter(name => text(intent[name]))
      .map(name => [name, intent[name]]));
    if (request) {
      // A staged reference can reuse a credential for another target until expiry;
      // it is not single-use. Never persist it. Central's unique actor/key record
      // is the authority for recovery via GET, including after expiry or use.
      // lookup_only is permanent: this projection must NEVER be POSTed, even if
      // a list is empty, truncated, inaccessible or the original reply was lost.
      if (intent.lookup_only === true || original.params.secret_ref) {
        saved.intent.lookup_only = true;
        saved.intent.lookup_request = request;
        // Older clients do not understand lookup_only. Leave their request
        // envelope deliberately incomplete so their validIntent check refuses
        // replay, rather than POSTing this redacted projection under our key.
        saved.intent.request = {action: request.action};
      } else saved.intent.request = request;
    } else {
      // Unknown/nested fields or legacy credential URLs cannot become an altered
      // replay. Keep the original identity for manual operation inspection.
      saved.intent.problemCode = 'INVALID_CONFIGURATION';
      saved.intent.request = {action: Object.hasOwn(requestFields, intent.request?.action) ? intent.request.action : ''};
    }
  }
  return saved;
}
