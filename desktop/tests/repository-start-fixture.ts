import {expect, type Page} from '@playwright/test';
export const publishedId = 'op_'+'a'.repeat(32), publishedSha = 'b'.repeat(40), bindingDigest = 'c'.repeat(64);
export const uploadId = 'op_'+'d'.repeat(32), aid = 'art_'+'e'.repeat(32);
export const selected = {repository: 'example/project', host: 'demo', workspace_id: 'demo-ws'};
export const dispatchProject = 'prj_'+'1'.repeat(20), dispatchArtifact = {artifact_id: 'art_'+'2'.repeat(32), revision: 1, digest: '3'.repeat(64)};
export async function publishedFixture(page: Page, native: boolean, options: any = {}) {
  const state = {actor: 'published-person', server: 'published-server', principal: 'published-principal', scopes: ['observe', 'start', 'manage'],
    allowed: true, writes: true, orchestrate: true, status: 'succeeded', posts: [] as any[], previews: [] as any[], reads: [] as string[],
    errors: [] as string[], events: [] as any[], after: 0, operation: null as any, bytes: [] as number[], uploaded: false,
    project: {project_id: dispatchProject, name: 'Dashboard project', description: 'Shared browser and desktop work',
      repositories: ['example/project'], children: [], version: 1, archived: false, counts: {total: 0, approved: 0}}, ...options};
  const baseCaps = () => ({actor: state.actor, scopes: state.scopes, api_version: 1, contract_version: '2026-10-08',
    hosts: ['demo', 'other'].map(host => ({host, writes: state.writes, orchestrate: state.orchestrate})),
    actions: state.absent ? [] : [{action: 'repository.continue', allowed: state.allowed}, {action: 'session.start', allowed: state.allowed}, {action: 'artifact.upload', allowed: true}],
    ...(state.dispatch ? {artifacts: {limits: {max_file_bytes: 1048576}}} : {}),
    features: {...(state.dispatch && !state.oldCentral ? {project_dispatch: {version: 1, artifacts: true, model: true}} : {}),
      repository_sync: state.unbound ? [] : (state.bindings || [selected, {...selected, host: 'other', workspace_id: 'other-ws'}]).map(b => ({...b, exact_ref_head_only: true}))}});
  const caps = () => state.extendCaps ? state.extendCaps(baseCaps()) : baseCaps();
  const mismatch = (operation: any) => {
    const op = structuredClone(operation);
    if (state.bad === 'key') op.idempotency_key = 'wrong';
    if (state.bad === 'actor') op.actor = 'wrong';
    if (state.bad === 'workspace') op.target.workspace_id = 'wrong';
    if (state.bad === 'prompt') op.params.prompt = 'changed';
    if (state.bad === 'preconditions') op.preconditions.binding_digest = '0'.repeat(64);
    if (state.bad === 'result') op.result.source_sha = '0'.repeat(40);
    if (state.bad === 'refs') op.external_refs.repository_binding = '0'.repeat(64);
    if (state.bad === 'message') op.result.message_id = 'unrelated';
    if (state.bad === 'artifacts') op.params.artifacts[0].digest = '0'.repeat(64);
    if (state.bad === 'project') op.external_refs.project_id = 'prj_'+'0'.repeat(20);
    return op;
  };
  const dispatch = async (input: any) => {
    const url = new URL(input.path, 'http://fixture'), path = url.pathname;
    const extra = await state.extraDispatch?.(input, state);
    if (extra) return extra;
    if (path === '/repository-previews') {
      state.previews.push(structuredClone(input)); const target = {...input.body}; delete target.source_ref;
      const preview = {target, workspace: {workspace_id: target.workspace_id, name: 'Dashboard', folder: '/srv/dashboard'}, source_ref: input.body.source_ref,
        source_sha: state.sha || publishedSha, repository_id: 123, binding_digest: bindingDigest, exact_ref_head_only: true,
        preconditions: {repository_id: 123, binding_digest: bindingDigest}};
      if (state.badPreview === 'target') preview.target.host = 'wrong';
      if (state.badPreview === 'ref') preview.source_ref = 'refs/heads/wrong';
      if (state.badPreview === 'sha') preview.source_sha = 'short';
      if (state.badPreview === 'preconditions') preview.preconditions.binding_digest = '0'.repeat(64);
      if (state.delayPreview) await new Promise(resolve => {state.holdPreview = resolve;});
      return {status: 200, data: {preview}};
    }
    if (input.method === 'BINARY') {
      state.bytes = input.bytes; state.uploaded = true; return {status: 200, data: {}};
    }
    if (input.method === 'POST') {
      state.posts.push(structuredClone(input));
      if (state.refuse) return {status: 403, data: {error: {code: state.refuse, message: 'Fixture refusal'}}};
      if (input.body?.action === 'artifact.upload') {
        state.uploadCount = (state.uploadCount || 0) + 1;
        state.uploadDigest = input.body.params.expected_digest;
        if (state.failUpload) return {status: 500, data: {error: {code: 'UPLOAD_FAILED', message: 'Upload failed'}}};
        return {status: 200, data: {operation: {operation_id: uploadId,
          status: state.instantUpload ? 'succeeded' : 'waiting_external',
          result: {artifact_id: aid, revision: 1, digest: input.body.params.expected_digest},
          external_refs: {content_url: 'https://untrusted.invalid'}}}};
      }
      if (input.body.action === 'project.create') {
        state.project = {...state.project, ...input.body.params, repositories: input.body.params.repositories || []};
        return {status: 200, data: {operation: {operation_id: publishedId, actor: state.actor, idempotency_key: input.idempotency_key, ...input.body, status: 'succeeded',
          result: {project_id: dispatchProject, version: 1}}}};
      }
      if (!state.operation || state.operation.idempotency_key !== input.idempotency_key) state.operation = {
        operation_id: publishedId, actor: state.actor, idempotency_key: input.idempotency_key, ...structuredClone(input.body), status: state.status,
        steps: [{name: 'session.start', status: 'succeeded'}, {name: 'send', status: state.status === 'uncertain' ? 'uncertain' : 'succeeded'}],
        external_refs: {host: input.body.target.host, session_id: 'managed-created', repository: input.body.target.repository,
          source_ref: input.body.params.source_ref, source_sha: input.body.params.source_sha, repository_id: 123, repository_binding: bindingDigest},
        result: state.status === 'succeeded' ? {...input.body.target, source_ref: input.body.params.source_ref, source_sha: input.body.params.source_sha,
          ...input.body.preconditions, session_id: 'managed-created', message_id: 'batc-'+publishedId} : null};
      if (state.delayPost) await new Promise(resolve => {state.holdPost = resolve;});
      if (state.lost) {state.lost = false; return {status: 503, data: {error: {code: 'LOST', message: 'Lost published start reply'}}};}
      return {status: 200, data: {operation: mismatch(state.operation)}};
    }
    state.reads.push(input.path);
    if (path === '/projects') return {status: 200, data: {projects: state.dispatch && !state.project.archived ? [state.project] : [], archived: []}};
    if (path.startsWith('/projects/')) {
      if (state.failProject) return {status: 503, data: {error: {code: 'READ_FAILED', message: 'Project read failed'}}};
      const project = structuredClone({...state.project, project_id: path.split('/').at(-1)});
      if (state.delayProject) await new Promise(resolve => {state.holdProject = resolve;});
      return {status: 200, data: {project, path: [], sub_projects: [], work_items: [], archived: []}};
    }
    if (path === '/artifacts') return {status: 200, data: {artifacts: [{revision: {...dispatchArtifact, state: 'ready', display_name: 'notes.txt'}}], next_cursor: null}};
    if (path.startsWith('/artifacts/')) {
      if (state.delayArtifact) await new Promise(resolve => {state.holdArtifact = resolve;});
      return {status: 200, data: {artifact: {...dispatchArtifact, state: state.unreadyArtifact ? 'reserved' : 'ready', display_name: 'notes.txt'}}};
    }
    if (path === '/operations/'+uploadId) {
      if (state.failUpload) return {status: 500, data: {error: {code: 'UPLOAD_FAILED', message: 'Upload failed'}}};
      return {status: 200, data: {operation: {operation_id: uploadId,
        status: state.uploaded || state.instantUpload ? 'succeeded' : 'waiting_external',
        result: {artifact_id: aid, revision: 1, digest: state.uploadDigest || 'd'.repeat(64)},
        external_refs: {content_url: 'https://untrusted.invalid'}}}};
    }
    if (path === '/operations/'+publishedId) {
      if (state.failRead) return {status: 503, data: {error: {code: 'READ_FAILED', message: 'Published read failed'}}};
      return {status: 200, data: {operation: mismatch({...state.operation, status: state.status})}};
    }
    if (path === '/events') state.after = Number(url.searchParams.get('after'));
    const events = state.events.filter((e: any) => e.seq > state.after), cursor = events.at(-1)?.seq || state.after;
    if (path === '/events' && events.length) state.delivered = true;
    return {status: 200, data: path === '/capabilities' ? caps() : path === '/bootstrap' ? {capabilities: caps(),
      sync: {version: 1, server_id: state.server, principal_id: state.principal, checkpoint: {cursor: 0, token: 'proof-0'}}}
      : path === '/events' ? {events, head_cursor: cursor, next_cursor: cursor, sync: {checkpoint: {cursor, token: 'proof-'+cursor}}}
      : {hosts: caps().hosts, sessions: [], operations: [], work_items: [], checkpoints: []}};
  };
  page.on('pageerror', e => state.errors.push(e.message));
  if (native) {
    await page.exposeFunction('publishedFixture', dispatch);
    await page.addInitScript(() => Object.assign(window, {isTauri: true, __TAURI_INTERNALS__: {invoke: async (command: string, args: any, options: any) => {
      if (command === 'native_status') return {endpoint: 'https://fixture.example', credential_available: true};
      if (command === 'connector_connect') return (await (window as any).publishedFixture({method: 'GET', path: '/capabilities'})).data;
      if (command === 'connector_disconnect') return;
      if (command === 'connector_request') return (window as any).publishedFixture(args.input);
      if (command === 'connector_upload_artifact') return (window as any).publishedFixture({method: 'BINARY',
        path: '/artifacts/uploads/' + (options?.headers?.['x-batc-upload-operation'] || uploadId) + '/content', bytes: [...new Uint8Array(args)]});
      if (command === 'fleet_availability') return {configured: false, platform_supported: false};
      throw new Error(command);
    }}}));
  } else await page.addInitScript(() => sessionStorage.setItem('batc.dashboard.token', 'fixture-token'));
  await page.route('**/api/v1/**', async route => {
    if (native) throw new Error('Native published start must use IPC');
    const request = route.request(), url = new URL(request.url()), binary = url.pathname.endsWith('/content');
    const response = await dispatch({method: binary ? 'BINARY' : request.method(), path: url.pathname.slice('/api/v1'.length)+url.search,
      body: binary ? null : request.postDataJSON(), bytes: binary ? [...request.postDataBuffer()!] : undefined,
      idempotency_key: request.headers()['idempotency-key']});
    await route.fulfill({status: response.status, json: response.data});
  });
  return state;
}
export async function openPublished(page: Page) {
  await page.goto('/dashboard/#/published'); const form = page.locator('[data-published-start]'); await expect(form).toBeVisible(); return form;
}
export async function previewPublished(page: Page) {
  const form = page.locator('[data-published-start]');
  await form.getByRole('combobox', {name: 'Repository · host · workspace ID'}).selectOption(JSON.stringify(selected));
  await form.getByRole('textbox', {name: 'Published branch ref'}).fill('refs/heads/main');
  await form.getByRole('button', {name: 'Preview published version'}).click(); await expect(form.locator('[data-published-preview]')).toContainText(publishedSha);
  await form.getByRole('textbox', {name: 'Original instructions', exact: true}).fill('  Work from this version.\nPreserve these instructions.  '); return form;
}
