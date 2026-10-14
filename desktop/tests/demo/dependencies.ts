// Member sign-in dependency capture. Shapes follow the current dependency read.
// Counts, omissions and impact rows describe this returned capture only.
import {digest, hexId, sha40, unix, type DemoProject} from './project.ts';

export const DEPENDENCY_COMMIT = 'c0ffee1' + 'a'.repeat(33);

export const dependencyFixtureIds = {
  api: hexId('pmcomp_', 1, 32),
  web: hexId('pmcomp_', 2, 32),
  billing: hexId('pmcomp_', 3, 32),
  declaration: hexId('pmdecl_', 1, 32),
  revision: hexId('pmdep_', 1, 32),
  commit: DEPENDENCY_COMMIT,
  keys: {
    pytest: hexId('pmdepkey_', 1, 32),
    httpx: hexId('pmdepkey_', 2, 32),
    contract: hexId('pmdepkey_', 3, 32),
    smtp: hexId('pmdepkey_', 4, 32),
    legacy: hexId('pmdepkey_', 5, 32),
    vitest: hexId('pmdepkey_', 6, 32),
    vite: hexId('pmdepkey_', 7, 32),
    react: hexId('pmdepkey_', 8, 32),
  },
};

const FRESHNESS = {stale: null, stale_reason: 'repository_head_not_observed'};

function dependency(key: string, name: string, ecosystem: string, kind: string) {
  return {key, name, ecosystem, kinds: [kind]};
}

function edge(row: {
  n: number, componentId: string, key: string, relation: string, kind: string, version: string | null,
  resolved: string | null, level: string, basis: string, reason?: string | null, versionReason?: string | null,
  refs: Record<string, unknown>[],
}) {
  return {
    edge_id: hexId('pmde_', row.n, 32), component_id: row.componentId, dependency_key: row.key,
    direction: 'component_to_dependency', relation: row.relation, kind: row.kind, version: row.version,
    resolved: row.resolved, version_reason: row.versionReason ?? (row.version ? null : 'not_declared'),
    resolution_reason: row.reason ?? null, evidence_level: row.level, basis: row.basis, evidence_refs: row.refs,
  };
}

function ref(project: DemoProject, path: string, extra: Record<string, unknown> = {}) {
  return {repository: project.ids.repository, path, commit_sha: DEPENDENCY_COMMIT, ...extra};
}

function file(path: string, status: string, reason: string | null, n: number) {
  const read = status === 'read';
  return {path, blob_sha: read ? sha40(n) : null, size: read ? 1200 + n : null, sha256: read ? digest(n) : null, status, reason};
}

export function dependencyDocument(project: DemoProject) {
  const ids = dependencyFixtureIds;
  const repository = project.ids.repository;
  const capturedAt = unix(project.now);
  const pytestHint = {
    hint_id: hexId('pmhint_', 1, 32), component_id: ids.api, relation: 'test', job_id: 'python', command: 'pytest',
    path: '.github/workflows/api.yml', line: 18, commit_sha: DEPENDENCY_COMMIT, repository,
  };
  const vitestHint = {
    hint_id: hexId('pmhint_', 2, 32), component_id: ids.web, relation: 'test', job_id: 'web', command: 'npm test',
    path: '.github/workflows/web.yml', line: 24, commit_sha: DEPENDENCY_COMMIT, repository,
  };
  const api = {
    component_id: ids.api, name: 'membership-api', ecosystem: 'python', manifest_path: 'api/pyproject.toml',
    repository, commit_sha: DEPENDENCY_COMMIT, version: '0.9.0', maintenance_owner: null,
    maintenance_owner_reason: 'not_named_in_visible_policy', verification_hints: [pytestHint],
  };
  const web = {
    component_id: ids.web, name: 'membership-web', ecosystem: 'npm', manifest_path: 'web/package.json',
    repository, commit_sha: DEPENDENCY_COMMIT, version: '0.9.0', maintenance_owner: null,
    maintenance_owner_reason: 'not_named_in_visible_policy', verification_hints: [vitestHint],
  };
  const deps = {
    pytest: dependency(ids.keys.pytest, 'pytest', 'python', 'build_test_toolchain'),
    httpx: dependency(ids.keys.httpx, 'httpx', 'python', 'runtime_library'),
    contract: dependency(ids.keys.contract, 'billing-contract', 'openapi', 'api_schema'),
    smtp: dependency(ids.keys.smtp, 'smtp-gateway', 'service', 'external_service'),
    legacy: dependency(ids.keys.legacy, 'legacy-mailer', 'python', 'runtime_library'),
    vitest: dependency(ids.keys.vitest, 'vitest', 'npm', 'build_test_toolchain'),
    vite: dependency(ids.keys.vite, 'vite', 'npm', 'build_test_toolchain'),
    react: dependency(ids.keys.react, 'react', 'npm', 'runtime_library'),
  };
  const edges = [
    edge({n: 1, componentId: ids.api, key: ids.keys.pytest, relation: 'test', kind: 'build_test_toolchain', version: '>=8', resolved: '8.3.2', level: 'verified', basis: 'python_group', refs: [ref(project, 'api/pyproject.toml', {line: 18}), ref(project, 'api/uv.lock', {package: 'pytest'})]}),
    edge({n: 2, componentId: ids.api, key: ids.keys.httpx, relation: 'runtime', kind: 'runtime_library', version: '>=0.27', resolved: null, level: 'declared', basis: 'python_group', reason: 'lockfile_does_not_cover_component', refs: [ref(project, 'api/pyproject.toml', {line: 22})]}),
    edge({n: 3, componentId: ids.api, key: ids.keys.contract, relation: 'contract', kind: 'api_schema', version: '1', resolved: null, level: 'declared', basis: 'exact_published_package', reason: 'not_lockfile_dependency', refs: [ref(project, 'api/pyproject.toml', {line: 30})]}),
    edge({n: 4, componentId: ids.api, key: ids.keys.smtp, relation: 'runtime', kind: 'external_service', version: null, resolved: null, level: 'inferred', basis: 'same_ci_job', reason: 'not_lockfile_dependency', refs: [ref(project, '.github/workflows/api.yml', {line: 40, job_id: 'python'})]}),
    edge({n: 6, componentId: ids.web, key: ids.keys.vitest, relation: 'test', kind: 'build_test_toolchain', version: '^2.1.0', resolved: '2.1.4', level: 'verified', basis: 'workflow_step', refs: [ref(project, 'web/package.json', {line: 12}), ref(project, 'web/package-lock.json', {package: 'vitest'})]}),
    edge({n: 7, componentId: ids.web, key: ids.keys.vite, relation: 'build', kind: 'build_test_toolchain', version: '^5.4.0', resolved: '5.4.8', level: 'verified', basis: 'workflow_step', refs: [ref(project, 'web/package-lock.json', {package: 'vite'})]}),
    edge({n: 8, componentId: ids.web, key: ids.keys.react, relation: 'runtime', kind: 'runtime_library', version: '^18.3.0', resolved: '18.3.1', level: 'verified', basis: 'workflow_step', refs: [ref(project, 'web/package-lock.json', {package: 'react'})]}),
  ];
  const declared = [{
    declaration_id: ids.declaration, component_id: ids.api, dependency: {...deps.legacy, kind: 'runtime_library', version: null},
    dependency_key: ids.keys.legacy, relation: 'reference', direction: 'component_to_dependency', version: null, resolved: null,
    evidence_level: 'declared', version_reason: 'not_declared', resolution_reason: 'owner_declaration',
    source_ref: {path: 'api/pyproject.toml', commit_sha: DEPENDENCY_COMMIT},
    evidence_refs: [ref(project, 'api/pyproject.toml', {line: 4})], note: null, actor: project.ids.actor,
    created_at: capturedAt, capture_revision_id: ids.revision,
  }];
  return {
    workspace_id: project.ids.workspaceId, version: 2, workspace_version: 4,
    source: {repository, commit_sha: DEPENDENCY_COMMIT, binding_digest: digest(21)},
    capture: {revision_id: ids.revision, commit_sha: DEPENDENCY_COMMIT, captured_at: capturedAt, status: 'partial'},
    ...FRESHNESS, partial: true, reason: null, components: [api, web],
    dependencies: {
      runtime_library: [deps.httpx, deps.react],
      api_schema: [deps.contract],
      build_test_toolchain: [deps.pytest, deps.vitest, deps.vite],
      external_service: [deps.smtp],
    },
    edges, declared,
    files: [
      file('api/pyproject.toml', 'read', null, 1),
      file('api/uv.lock', 'read', null, 2),
      file('api/constraints.txt', 'missing', 'missing_at_commit', 3),
      file('web/package.json', 'read', null, 4),
      file('web/package-lock.json', 'read', null, 5),
      file('web/vendor.lock', 'too_large', 'document_too_large', 6),
      file('tools/broken.toml', 'unparsable', 'unsupported_or_invalid_format', 7),
    ],
    unreadable_paths: [{kind: 'tree', path: 'vendor/closed', object_sha: sha40(9)}],
    verification_hints: [pytestHint, vitestHint],
    parser_reasons: ['capture_budget', 'future_parser_code'], parser_notes: [],
    omitted_edges: 4, omitted_hints: 2, omitted_components: 1,
    counts: {components: 2, dependencies: 7, edges: 7, declared: 1, basis: 'captured_revision', partial: true},
    maintenance_owner: null, maintenance_owner_reason: 'not_named_in_visible_policy',
    cross_repository: {status: 'bounded', complete: false},
    cross_repository_edges: [{
      link_id: hexId('pmdeplink_', 1, 32), component_id: ids.api, target_component_id: ids.billing,
      dependency_key: ids.keys.contract, relation: 'contract', version: '1', resolved: null,
      evidence_level: 'declared', basis: 'exact_published_package', direction: 'component_to_component',
    }],
    external_components: [{
      component_id: ids.billing, name: 'billing-api', ecosystem: 'python', repository: 'example/billing',
      published_name: 'billing-contract', maintenance_owner: null, maintenance_owner_reason: 'not_named_in_visible_policy',
    }],
  };
}

export function dependencyRevisions(project: DemoProject, url: URL) {
  const after = Number(url.searchParams.get('after') || 0);
  const document = dependencyDocument(project);
  return {
    workspace_id: project.ids.workspaceId,
    revisions: after > 0 ? [] : [{
      revision_id: dependencyFixtureIds.revision, captured_at: document.capture.captured_at, commit_sha: DEPENDENCY_COMMIT,
      status: 'partial', partial: true, reason: null, repository: project.ids.repository,
      binding_digest: document.source.binding_digest, ...FRESHNESS,
    }],
    next_cursor: null, reason: null,
  };
}

export function dependencyImpact(project: DemoProject, key: string) {
  const document = dependencyDocument(project);
  const base = {
    workspace_id: project.ids.workspaceId, dependency: key, capture: document.capture, ...FRESHNESS,
    partial: true, coverage: 'captured_edges_only', declared: document.declared.filter(row => row.dependency_key === key),
    cross_repository: key === dependencyFixtureIds.keys.contract
      ? {status: 'partial', complete: false, reason: 'selection_budget', selection_reasons: ['selection_budget', 'unreadable_manifest']}
      : {status: 'bounded', complete: false},
  };
  const owner = document.edges.find(row => row.dependency_key === key);
  if (!owner) {
    return {...base, reason: base.declared.length ? 'bounded_captured_graph' : 'dependency_not_observed', components: [], counts: {components: 0, basis: 'returned_components', partial: true}};
  }
  const component = document.components.find(row => row.component_id === owner.component_id);
  return {
    ...base, reason: 'bounded_captured_graph',
    components: [{...component, ...FRESHNESS, distance: 1, direct: true, relations: [owner.relation], edges: [owner]}],
    counts: {components: 1, basis: 'returned_components', partial: true},
  };
}
