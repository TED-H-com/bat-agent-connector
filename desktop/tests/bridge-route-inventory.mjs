// Finds central routes the shared frontend can send to connector_request.
// Acorn parses the source. Only path-builder forms are resolved: literals,
// templates, concatenation, constant comparisons, local bindings, and direct
// call sites of a path parameter. An api() call that does not resolve fails
// with its source location. This file does not execute application control flow.
import * as acorn from 'acorn';
import fs from 'node:fs';
import path from 'node:path';

const SRC = path.resolve(path.dirname(new URL(import.meta.url).pathname), '../src');
const BRIDGE = path.resolve(SRC, '../src-tauri/src/bridge.rs');
const hex = (n, ch = 'a') => ch.repeat(n);
const ID = {
  ws: `ws_${hex(32)}`,
  wi: `wi_${hex(20, 'b')}`,
  att: `att_${hex(32, 'c')}`,
  op: `op_${hex(32, 'd')}`,
  pmrev: `pmrev_${hex(32, 'e')}`,
  pmsnap: `pmsnap_${hex(32, 'f')}`,
  pmt: `pmt_${hex(32)}`,
  pmr: `pmr_${hex(32)}`,
  pmmeet: `pmmeet_${hex(32)}`,
  pms: `pms_${hex(32)}`,
  pmwp: `pmwp_${hex(32)}`,
  wpf: `wpf_${hex(32)}`,
  pmqr: `pmqr_${hex(32)}`,
  pmfu: `pmfu_${hex(32)}`,
  pmfa: `pmfa_${hex(32)}`,
  pmoa: `pmoa_${hex(32)}`,
  pmcp: `pmcp_${hex(32)}`,
  pme: `pme_${hex(32)}`,
  pmcap: `pmcap_${hex(32)}`,
  pmrb: `pmrb_${hex(32)}`,
  pmgov: `pmgov_${hex(32)}`,
  pmim: `pmim_${hex(32)}`,
  pmlt: `pmlt_${hex(32)}`,
  pmpb: `pmpb_${hex(32)}`,
  pmdepkey: `pmdepkey_${hex(32)}`,
  dep: `dep_${hex(32)}`,
  prj: `prj_${hex(20)}`,
  wt: `wt_${hex(32)}`,
  art: `art_${hex(32)}`,
  cp: `cp_${hex(32)}`,
};
const SEGMENT = new Map([
  ['workspaces', ID.ws], ['work', ID.wi], ['work-items', ID.wi], ['work-attempts', ID.att],
  ['operations', ID.op], ['projects', ID.prj], ['plans', ID.pmrev], ['snapshots', ID.pmsnap],
  ['threads', ID.pmt], ['runs', ID.pmr], ['meetings', ID.pmmeet], ['sprints', ID.pms],
  ['work-packages', ID.pmwp], ['work-profiles', ID.wpf], ['quality-reviews', ID.pmqr], ['follow-ups', ID.pmfu],
  ['follow-up-assessments', ID.pmfa], ['overview-assessments', ID.pmoa], ['conversation-proposals', ID.pmcp],
  ['evidence', ID.pme], ['captures', ID.pmcap], ['release-baselines', ID.pmrb],
  ['governance', ID.pmgov], ['issue-mappings', ID.pmim], ['worktrees', ID.wt],
  ['artifacts', ID.art], ['checkpoints', ID.cp], ['hosts', 'fixture'], ['sessions', 'fixture'],
  ['repositories', 'octo/repo'], ['pulls', '1'], ['revisions', '1'],
  ['tasks', '0123abcd-ef456789'], ['register', 'governance.item-1'],
  ['maintenance-reviews', `pmrevw_${hex(32)}`], ['maintenance-repairs', `pmrp_${hex(32)}`],
  ['maintenance-policies', `pmmp_${hex(32)}`], ['maintenance-checks', `pmck_${hex(32)}`],
  ['spec-materializations', `pmsp_${hex(32)}`], ['workload-snapshots', `pmwl_${hex(32)}`],
  ['ahp-observations', `pmah_${hex(32)}`], ['github-observations', `pmgh_${hex(32)}`],
  ['work-batches', `pmbt_${hex(32)}`], ['executions', ID.op], ['execution-verifications', ID.op],
  ['pr-publications', ID.op], ['integrations', ID.op], ['product_goal', `pmgoal_${hex(32)}`],
  ['dod', `pmdod_${hex(32)}`], ['backlog', `pmbkl_${hex(32)}`], ['kanban', ID.wi],
  ['cleanup-tombstones', `cr_${hex(32)}`], ['transitions', ID.pmlt],
  ['deployments', ID.dep],
]);
const NAME_SAMPLE = new Map([
  ['endpoint', `/pm/workspaces/${ID.ws}`], ['base', `/pm/workspaces/${ID.ws}`],
  ['workspaceId', ID.ws], ['workspace_id', ID.ws], ['pmWorkspace', ID.ws],
  ['workId', ID.wi], ['work_item_id', ID.wi], ['attemptId', ID.att], ['attempt_id', ID.att],
  ['operationId', ID.op], ['operation_id', ID.op], ['projectId', ID.prj], ['project_id', ID.prj],
  ['host', 'fixture'], ['sid', 'sess.1'], ['sessionId', 'sess.1'], ['session_id', 'sess.1'],
]);

function walkFiles(dir, out = []) {
  for (const entry of fs.readdirSync(dir, {withFileTypes: true})) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) walkFiles(full, out);
    else if (entry.name.endsWith('.js')) out.push(full);
  }
  return out;
}

function children(node) {
  if (!node || typeof node !== 'object') return [];
  const out = [];
  for (const value of Object.values(node)) {
    if (Array.isArray(value)) out.push(...value.filter(item => item && item.type));
    else if (value && value.type) out.push(value);
  }
  return out;
}

class Scope {
  constructor(parent) { this.parent = parent || null; this.bindings = new Map(); }
  get(name) {
    if (this.bindings.has(name)) return this.bindings.get(name);
    return this.parent ? this.parent.get(name) : undefined;
  }
}

function scopeContains(ancestor, descendant) {
  for (let cursor = descendant; cursor; cursor = cursor.parent) if (cursor === ancestor) return true;
  return false;
}

function bindPattern(pattern, init, scope, meta) {
  if (!pattern) return;
  const info = meta || {kind: 'init', constant: true};
  if (pattern.type === 'Identifier') {
    scope.bindings.set(pattern.name, {...info, init, scope});
    return;
  }
  if (pattern.type === 'ObjectPattern') {
    for (const prop of pattern.properties) {
      if (prop.type !== 'Property') continue;
      const key = prop.key?.name || prop.key?.value;
      const found = init?.type === 'ObjectExpression'
        ? init.properties.find(item => item.type === 'Property' && !item.computed && (item.key?.name || item.key?.value) === key)
        : null;
      const child = found ? found.value : (init ? {type: 'MemberExpression', synthetic: true, object: init, property: {type: 'Identifier', name: key}, computed: false} : null);
      bindPattern(prop.value, child, scope, info);
    }
    return;
  }
  if (pattern.type === 'AssignmentPattern') {
    const provided = init && !init.synthetic ? init : pattern.right;
    bindPattern(pattern.left, provided, scope, info);
  }
}

function cellLiteral(node) {
  if (!node) return null;
  return literalString(node) ?? (node.type === 'Literal' ? literalValue(node) : null);
}

function arrayStrings(node) {
  if (!node || node.type !== 'ArrayExpression') return [];
  const values = [];
  for (const el of node.elements) {
    if (!el) continue;
    if (el.type === 'SpreadElement') {
      const choices = el.argument.type === 'ConditionalExpression' ? [el.argument.consequent, el.argument.alternate] : [el.argument];
      for (const choice of choices) if (choice.type === 'ArrayExpression') values.push(...arrayStrings(choice));
      continue;
    }
    const lit = cellLiteral(el);
    if (typeof lit === 'string' && lit !== '') values.push(lit);
  }
  return values;
}

function isForwarder(fn) {
  const rest = (fn.params || []).find(param => param.type === 'RestElement' && param.argument.type === 'Identifier');
  if (!rest) return false;
  let found = false;
  const see = node => {
    if (!node || typeof node !== 'object' || found) return;
    if (node.type === 'CallExpression' && node.callee.type === 'Identifier' && node.callee.name === 'api'
      && node.arguments.length === 1 && node.arguments[0].type === 'SpreadElement'
      && node.arguments[0].argument.type === 'Identifier' && node.arguments[0].argument.name === rest.argument.name) {
      found = true;
      return;
    }
    for (const child of children(node)) see(child);
  };
  see(fn.body);
  return found;
}

function queryValue(pathname, key) {
  if (key === 'after' && pathname.endsWith('/threads')) return ID.pmt;
  if (key === 'after' && pathname.endsWith('/runs')) return ID.pmr;
  if (key === 'after' && pathname.endsWith('/result-sources')) return ID.wi;
  if (key === 'kind' && pathname.endsWith('/governance')) return 'risk';
  if (key === 'kind' && pathname.endsWith('/roadmap/revisions')) return 'baseline';
  if (key === 'kind' && pathname.endsWith('/release-baseline-preview')) return 'github_release';
  if (key === 'kind' && pathname.endsWith('/repair-evidence')) return 'operation';
  if (key === 'type' && pathname.endsWith('/register')) return 'risk';
  if (key === 'state') return 'open';
  if (key === 'agent') return 'claude';
  if (key === 'format') return 'markdown';
  if (key === 'locale') return 'en';
  if (key === 'repository') return 'octo/repo';
  if (key === 'commit_sha') return hex(40);
  if (key === 'dependency') return ID.pmdepkey;
  if (key === 'host') return 'fixture';
  if (key === 'workspace_id' || key === 'workspace') return ID.ws;
  if (key === 'work_item_id') return ID.wi;
  if (key === 'attempt_id') return ID.att;
  if (key === 'project_id') return ID.prj;
  if (key === 'operation_id' || key === 'execution_id' || key === 'source_id') return ID.op;
  if (key === 'source_kind') return 'execution';
  if (key === 'thread_id') return ID.pmt;
  if (key === 'sprint_id') return ID.pms;
  if (key === 'package_id') return ID.pmwp;
  if (key === 'from_revision_id' || key === 'to_revision_id') return ID.pmrev;
  if (key === 'project_binding_id') return ID.pmpb;
  if (key === 'transition_id') return ID.pmlt;
  if (key === 'deployment_id') return ID.dep;
  if (key === 'intake_id') return ID.pmfu;
  if (key === 'paths') return 'openspec/specs/demo/spec.md';
  if (key === 'quality_refs') return '{}';
  if (key === 'buckets') return 'day';
  if (key === 'tz') return 'UTC';
  if (key === 'since' || key === 'until') return '2026-10-16T00:00:00Z';
  if (key === 'maintenance_branch') return 'main';
  if (key === 'refresh') return '1';
  if (key === 'include_tools' || key === 'include_closed' || key === 'include_gone' || key === 'include_archived' || key === 'unread' || key === 'attention') return 'true';
  if (key === 'order') return 'id';
  if (key === 'status') return 'open';
  if (key === 'owner' || key === 'q' || key === 'query') return 'readme';
  if (key === 'profile_id') return 'profile-1';
  if (key === 'limit' || key === 'history_limit' || key === 'last_n') return '20';
  if (key === 'wait_ms' || key === 'offset' || key === 'journal_cursor' || key === 'release_id' || key === 'page' || key === 'pull_number') return '1';
  if (key === 'max_commits' || key === 'max_files') return '10';
  if (key === 'max_chars' || key === 'max_message_chars') return '2000';
  if (key === 'cursor' || key === 'after' || key === 'before') return '1';
  if (key === 'wait') return '3';
  return '1';
}

function sampleHole(prefix) {
  const cut = prefix.split('?')[0];
  const parts = cut.split('/').filter(Boolean);
  const segment = parts.at(-1) || '';
  const parent = parts.at(-2) || '';
  if (parent === 'roadmap' && segment === 'revisions') return ID.pmrb;
  if (parent === 'sessions') return 'sess.1';
  if (parts[0] === 'sessions' && parts.length === 2) return 'sess.1';
  if (SEGMENT.has(segment)) return SEGMENT.get(segment);
  return null;
}

function usablePath(raw) {
  if (typeof raw !== 'string' || !raw.startsWith('/')) return false;
  const pathname = raw.split('?')[0];
  const parts = pathname.split('/');
  if (parts[0] !== '' || parts.slice(1).some(part => part === '' || part === '.' || part === '..')) return false;
  if (!raw.includes('?') && raw.includes('&')) return false;
  // A second ? is the unpinned path.includes('?') branch joined onto a path that already has a query.
  if (raw.indexOf('?') !== raw.lastIndexOf('?')) return false;
  if (pathname.includes('%')) return false;
  return true;
}

function literalValue(node) {
  if (!node || node.type !== 'Literal') return null;
  if (typeof node.value === 'string') return node.value;
  if (typeof node.value === 'number' || typeof node.value === 'boolean') return String(node.value);
  if (node.value === null) return 'null';
  return null;
}

function queryVariants(node, pathname, evalNode) {
  // Each variant is a list of [key, value] pairs. Computed keys and conditional
  // spreads become separate variants so mutually exclusive keys stay apart.
  if (!node || node.type !== 'ObjectExpression') return null;
  let variants = [[]];
  const sampleOf = (key, valueNode) => {
    const lit = literalString(valueNode);
    if (lit != null) return lit;
    const plain = literalValue(valueNode);
    if (plain != null && valueNode.type === 'Literal') return plain;
    return queryValue(pathname, key);
  };
  for (const prop of node.properties) {
    if (prop.type === 'SpreadElement') {
      const arg = prop.argument;
      const choices = arg.type === 'ConditionalExpression'
        ? [arg.consequent, arg.alternate]
        : [arg];
      const extras = [];
      for (const choice of choices) {
        if (choice.type === 'ObjectExpression') {
          const nested = queryVariants(choice, pathname, evalNode);
          if (!nested) return null;
          extras.push(...nested);
        } else if (choice.type === 'Literal' && choice.value == null) extras.push('');
        else return null;
      }
      variants = variants.flatMap(base => extras.map(extra => (extra ? [...base, extra] : [...base]))).slice(0, 8);
      continue;
    }
    let names = null;
    if (prop.computed) names = evalNode ? evalNode(prop.key) : null;
    else if (prop.key) names = [String(prop.key.name || prop.key.value)];
    if (!names || !names.length) return null;
    const value = names.length === 1 ? sampleOf(names[0], prop.value) : null;
    const next = [];
    for (const base of variants) {
      for (const name of names) next.push([...base, `${encodeURIComponent(name)}=${encodeURIComponent(value || queryValue(pathname, name))}`]);
    }
    variants = next.slice(0, 8);
  }
  return variants.map(parts => parts.filter(Boolean).join('&'));
}

function literalString(node) {
  if (!node) return null;
  if (node.type === 'Literal' && typeof node.value === 'string') return node.value;
  if (node.type === 'TemplateLiteral' && node.expressions.length === 0) return node.quasis.map(q => q.value.cooked).join('');
  return null;
}

function testKey(node) {
  if (!node || node.type !== 'BinaryExpression' || (node.operator !== '===' && node.operator !== '==')) return null;
  const side = (nameNode, valueNode) => {
    const name = nameNode.type === 'Identifier' ? nameNode.name : null;
    const lit = literalString(valueNode) ?? (valueNode.type === 'Literal' ? literalValue(valueNode) : null);
    return name && lit != null ? `${name}${node.operator}${lit}` : null;
  };
  return side(node.left, node.right) || side(node.right, node.left);
}

export function discoverCentralRoutes() {
  const calls = [];
  const unresolved = [];
  const callSites = [];
  const functions = [];
  const pendingCalls = [];
  for (const file of walkFiles(SRC)) {
    let ast;
    try { ast = acorn.parse(fs.readFileSync(file, 'utf8'), {ecmaVersion: 'latest', sourceType: 'module', locations: true}); }
    catch (error) {
      unresolved.push({file: path.relative(SRC, file), line: 1, reason: `parse failure: ${error.message}`});
      continue;
    }
    const root = new Scope(null);
    const fnStack = [];
    const assigned = new Map();
    const mapSources = new Map();
    const loopScopes = new Map();
    const pathProps = [];
    const markAssigned = name => {
      for (const owner of fnStack) {
        if (!assigned.has(owner)) assigned.set(owner, new Set());
        assigned.get(owner).add(name);
      }
    };
    const visit = (node, scope) => {
      if (!node || !node.type) return;
      if (node.type === 'FunctionDeclaration' || node.type === 'FunctionExpression' || node.type === 'ArrowFunctionExpression') {
        node.fileName = file;
        if (node.type === 'FunctionDeclaration' && node.id) scope.bindings.set(node.id.name, {kind: 'init', init: node, scope, constant: true});
        const inner = new Scope(scope);
        node.params.forEach((param, index) => {
          if (param.type === 'RestElement') return;
          const meta = {kind: 'param', index, fn: node, file, constant: true};
          bindPattern(param.type === 'AssignmentPattern' ? param.left : param, null, inner, meta);
        });
        functions.push({node, file, scope: inner});
        fnStack.push(node);
        visit(node.body, inner);
        fnStack.pop();
        return;
      }
      if (node.type === 'ForOfStatement') {
        visit(node.right, scope);
        const inner = new Scope(scope);
        loopScopes.set(node, inner);
        const rows = loopRows(node.right, scope, fnStack.at(-1));
        const decl = node.left.type === 'VariableDeclaration' ? node.left.declarations[0] : null;
        if (decl && rows) bindLoop(decl.id, rows, inner);
        visit(node.body, inner);
        return;
      }
      if (node.type === 'VariableDeclaration') {
        for (const decl of node.declarations) {
          if (decl.init && decl.id.type === 'Identifier' && (decl.init.type === 'ArrowFunctionExpression' || decl.init.type === 'FunctionExpression')) decl.init.inferredName = decl.id.name;
          if (decl.id.type === 'Identifier' && decl.init) scope.bindings.set(decl.id.name, {kind: 'init', init: decl.init, scope, constant: node.kind === 'const', fn: fnStack.at(-1)});
          else bindPattern(decl.id, decl.init, scope);
          if (decl.init) visit(decl.init, scope);
        }
        return;
      }
      if (node.type === 'AssignmentExpression' && node.left.type === 'Identifier') markAssigned(node.left.name);
      if (node.type === 'UpdateExpression' && node.argument.type === 'Identifier') markAssigned(node.argument.name);
      if (node.type === 'CallExpression' && node.callee.type === 'Identifier') {
        const bound = scope.get(node.callee.name);
        const target = bound?.init && (bound.init.type === 'ArrowFunctionExpression' || bound.init.type === 'FunctionExpression' || bound.init.type === 'FunctionDeclaration') ? bound.init : null;
        callSites.push({name: node.callee.name, args: node.arguments, scope, file, line: node.loc.start.line, fn: target});
      }
      if (node.type === 'CallExpression' && node.callee.type === 'MemberExpression' && !node.callee.computed && ['map', 'flatMap', 'forEach'].includes(node.callee.property.name)) {
        let arrayNode = null;
        if (node.callee.object.type === 'ArrayExpression') arrayNode = node.callee.object;
        else if (node.callee.object.type === 'Identifier') {
          const init = scope.get(node.callee.object.name)?.init;
          if (init?.type === 'ArrayExpression') arrayNode = init;
        }
        if (arrayNode) for (const arg of node.arguments) if (arg.type === 'ArrowFunctionExpression' || arg.type === 'FunctionExpression') mapSources.set(arg, arrayNode);
      }
      if (node.type === 'ObjectExpression') {
        for (const prop of node.properties) if (prop.type === 'Property' && !prop.computed && (prop.key?.name || prop.key?.value) === 'path') pathProps.push({file, value: prop.value, scope});
      }
      for (const child of children(node)) visit(child, scope);
    };
    const loopRows = (right, scope, fn) => {
      const arrays = [];
      const source = right.type === 'Identifier' ? scope.get(right.name)?.init : right;
      if (source?.type === 'ArrayExpression') arrays.push(source);
      const name = right.type === 'Identifier' ? right.name : '';
      if (name && fn) {
        const walk = current => {
          if (!current?.type) return;
          if ((current.type === 'FunctionDeclaration' || current.type === 'FunctionExpression' || current.type === 'ArrowFunctionExpression') && current !== fn) return;
          if (current.type === 'CallExpression' && current.callee.type === 'MemberExpression' && !current.callee.computed && current.callee.property.name === 'push' && current.callee.object.type === 'Identifier' && current.callee.object.name === name && current.arguments[0]?.type === 'ArrayExpression') {
            arrays.push({type: 'ArrayExpression', elements: [current.arguments[0]]});
          }
          for (const child of children(current)) walk(child);
        };
        walk(fn.body);
      }
      const rows = [];
      for (const array of arrays) {
        for (const el of array.elements) {
          if (!el || el.type === 'SpreadElement') continue;
          rows.push(el.type === 'ArrayExpression' ? el.elements : [el]);
        }
      }
      return rows.length ? rows : null;
    };
    const bindLoop = (pattern, rows, scope) => {
      if (pattern.type === 'Identifier') {
        const values = [];
        for (const row of rows) {
          const lit = cellLiteral(row[0]);
          if (typeof lit !== 'string') return;
          values.push(lit);
        }
        scope.bindings.set(pattern.name, {kind: 'enum', values: [...new Set(values)]});
        return;
      }
      if (pattern.type !== 'ArrayPattern') return;
      pattern.elements.forEach((cell, index) => {
        if (!cell || cell.type !== 'Identifier') return;
        const values = [];
        for (const row of rows) {
          const lit = cellLiteral(row[index]);
          if (typeof lit !== 'string') return;
          values.push(lit);
        }
        if (values.length === rows.length) scope.bindings.set(cell.name, {kind: 'enum', values: [...new Set(values)]});
      });
    };
    visit(ast, root);
    const resolving = new Set();
    const expandStack = new Set();
    let steps = 0;
    let harvesting = false;
    let activePins = new Map();
    const callSitesOf = fn => {
      const linked = callSites.filter(site => site.fn === fn);
      if (linked.length) return linked;
      const name = fn.id?.name || fn.inferredName;
      if (!name) return [];
      const sameName = functions.filter(item => (item.node.id?.name || item.node.inferredName) === name);
      if (sameName.length !== 1) return [];
      return callSites.filter(site => site.name === name && !site.fn);
    };
    const paramFunction = (node, scope) => {
      let best = null;
      let bestDepth = -1;
      const walk = current => {
        if (!current?.type) return;
        if (current !== node && (current.type === 'FunctionDeclaration' || current.type === 'FunctionExpression' || current.type === 'ArrowFunctionExpression')) return;
        if (current.type === 'Identifier') {
          const bound = scope.get(current.name);
          if (bound?.kind === 'param' && bound.fn && !expandStack.has(bound.fn)) {
            const count = callSitesOf(bound.fn).length;
            if (count > 0 && count <= 24) {
              let depthN = 0;
              for (let cursor = bound.scope; cursor; cursor = cursor.parent) depthN++;
              if (depthN > bestDepth) { best = bound.fn; bestDepth = depthN; }
            }
          }
          return;
        }
        for (const child of children(current)) walk(child);
      };
      walk(node);
      return best;
    };
    const mapElements = fn => {
      const values = arrayStrings(mapSources.get(fn));
      return values.length ? values : null;
    };
    const resolve = (node, scope, depth, prefix) => {
      if (!node || depth > 12 || ++steps > 250000) return null;
      if (node.type === 'ChainExpression') return resolve(node.expression, scope, depth, prefix);
      if (node.type === 'Literal') {
        const plain = literalValue(node);
        return plain == null ? null : [plain];
      }
      const lit = literalString(node);
      if (lit != null) return [lit];
      if (node.type === 'ConditionalExpression' && activePins.has(testKey(node.test))) {
        return resolve(activePins.get(testKey(node.test)) ? node.consequent : node.alternate, scope, depth, prefix);
      }
      const owner = depth < 8 ? paramFunction(node, scope) : null;
      if (owner && !expandStack.has(owner)) {
        const sites = callSitesOf(owner).slice(0, 24);
        expandStack.add(owner);
        const found = [];
        try {
          for (const site of sites) {
            const overlay = new Scope(scope);
            overlay.expansion = true;
            owner.params.forEach((param, index) => {
              if (param.type === 'RestElement') return;
              const pattern = param.type === 'AssignmentPattern' ? param.left : param;
              const arg = site.args[index];
              const init = arg && arg.type !== 'SpreadElement' ? arg : (param.type === 'AssignmentPattern' ? param.right : null);
              if (!init) return;
              if (pattern.type === 'Identifier') overlay.bindings.set(pattern.name, {kind: 'init', init, scope: site.scope, constant: true});
              else if (pattern.type === 'ObjectPattern' && init.type === 'ObjectExpression') bindPattern(pattern, init, overlay, {kind: 'init', constant: true});
            });
            const values = resolve(node, overlay, depth + 1, prefix);
            if (values) found.push(...values);
            if (found.length > 64) break;
          }
        } finally { expandStack.delete(owner); }
        const unique = [...new Set(found)];
        if (unique.length) return unique.slice(0, 64);
      }
      if (node.type === 'Identifier') {
        const bound = scope.get(node.name);
        if (!bound) return NAME_SAMPLE.has(node.name) ? [NAME_SAMPLE.get(node.name)] : null;
        if (bound.kind === 'enum') return bound.values;
        if (bound.kind === 'init' && bound.init?.type === 'NewExpression' && bound.init.callee?.name === 'URLSearchParams') {
          const text = searchText(node, bound.scope || scope, prefix);
          if (text) return text;
        }
        if (bound.kind === 'param' && bound.index === 0) {
          const elements = mapElements(bound.fn);
          if (elements) return elements;
        }
        if (bound.kind === 'init' && bound.init && !(bound.constant === false && bound.fn && assigned.get(bound.fn)?.has(node.name))) {
          const mark = `${node.name}:${bound.init.start}`;
          if (resolving.has(mark)) return null;
          resolving.add(mark);
          // Param expansion shadows the function's own parameters. A local such as
          // `query` is stored on that function scope, so read it with the shadow first.
          const readScope = scope.expansion && bound.scope && scopeContains(bound.scope, scope)
            ? Object.assign(new Scope(bound.scope), {bindings: scope.bindings, expansion: true})
            : (bound.scope || scope);
          const value = resolve(bound.init, readScope, depth + 1, prefix);
          resolving.delete(mark);
          if (value) return value;
        }
        if (bound.kind === 'param' && NAME_SAMPLE.has(node.name)) return [NAME_SAMPLE.get(node.name)];
        return NAME_SAMPLE.has(node.name) ? [NAME_SAMPLE.get(node.name)] : null;
      }
      if (node.type === 'TemplateLiteral') {
        const blank = item => item === '' || item === 'null' || item === 'undefined';
        let product = [{text: '', pins: new Map()}];
        for (let i = 0; i < node.quasis.length; i++) {
          const cooked = node.quasis[i].value.cooked ?? '';
          product = product.map(item => ({text: item.text + cooked, pins: item.pins}));
          if (i >= node.expressions.length) break;
          const expr = node.expressions[i];
          const key = expr.type === 'ConditionalExpression' ? testKey(expr.test) : null;
          const next = [];
          let failed = false;
          for (const left of product) {
            const branches = key && !left.pins.has(key) ? [[true, expr.consequent], [false, expr.alternate]] : [[null, expr]];
            for (const [bit, branch] of branches) {
              const pins = bit == null ? left.pins : new Map([...left.pins, [key, bit]]);
              activePins = pins;
              const rawHole = resolve(bit == null ? expr : branch, scope, depth + 1, left.text) || [];
              activePins = new Map();
              const nonempty = rawHole.filter(item => !blank(item));
              const hole = nonempty.length ? nonempty : holeFallback(bit == null ? expr : branch, left.text, scope);
              if (!hole && !(left.text.includes('?') && rawHole.includes(''))) {
                if (bit == null) { failed = true; break; }
                continue;
              }
              for (const right of hole || []) {
                if (blank(right)) continue;
                next.push({text: left.text + right, pins});
                if (next.length > 24) break;
              }
              if (left.text.includes('?') && rawHole.includes('')) next.push({text: left.text, pins});
            }
            if (failed || next.length > 24) break;
          }
          if (failed || !next.length) return null;
          product = next;
        }
        return product.map(item => item.text);
      }
      if (node.type === 'BinaryExpression' && node.operator === '+') {
        const left = resolve(node.left, scope, depth + 1, prefix);
        let right = resolve(node.right, scope, depth + 1, prefix);
        if (right) right = right.filter(item => item !== '' && item !== 'null' && item !== 'undefined');
        if (left && (!right || !right.length)) {
          const filled = [];
          for (const item of left) {
            const hole = holeFallback(node.right, item, scope);
            if (!hole) continue;
            for (const value of hole) filled.push(item + value);
          }
          return filled.length ? [...new Set(filled)].slice(0, 24) : null;
        }
        if (!left || !right) return null;
        return left.flatMap(a => right.map(b => a + b)).slice(0, 24);
      }
      if (node.type === 'ConditionalExpression') {
        const test = resolve(node.test, scope, depth + 1, prefix);
        const yes = resolve(node.consequent, scope, depth + 1, prefix);
        const no = resolve(node.alternate, scope, depth + 1, prefix);
        if (test && test.length === 1 && yes && no) {
          if (test[0] === 'true') return yes;
          if (test[0] === 'false') return no;
        }
        const both = [...(yes || []), ...(no || [])];
        return both.length ? both.slice(0, 24) : null;
      }
      if (node.type === 'LogicalExpression') {
        const left = resolve(node.left, scope, depth + 1, prefix);
        const right = resolve(node.right, scope, depth + 1, prefix);
        if (node.operator === '&&' && left && left.length === 1 && right) return left[0] === 'false' || left[0] === '' ? left : right;
        if ((node.operator === '||' || node.operator === '??') && left && right) {
          if (left.length === 1 && left[0] !== '' && left[0] !== 'false' && left[0] !== 'null' && left[0] !== 'undefined') return left;
          return [...left, ...right].slice(0, 24);
        }
        return right || left;
      }
      if (node.type === 'CallExpression' && node.callee.type === 'MemberExpression' && !node.callee.computed && node.callee.property.name === 'includes' && node.arguments.length === 1) {
        const obj = resolve(node.callee.object, scope, depth + 1, prefix);
        const arg = resolve(node.arguments[0], scope, depth + 1, prefix);
        if (obj && arg && arg.length === 1) return [...new Set(obj.map(item => String(item).includes(arg[0]) ? 'true' : 'false'))];
      }
      if (node.type === 'CallExpression' && node.callee.type === 'MemberExpression' && !node.callee.computed && node.callee.property.name === 'toString' && node.arguments.length === 0) {
        const text = searchText(node.callee.object, scope, prefix);
        if (text) return text;
      }
      if (node.type === 'CallExpression' && node.callee.type === 'Identifier' && node.callee.name === 'encodeURIComponent') {
        const inner = resolve(node.arguments[0], scope, depth + 1, prefix);
        if (!inner) return null;
        const values = inner.filter(value => value !== '' && value !== 'null' && value !== 'undefined').map(value => encodeURIComponent(value));
        return values.length ? values : null;
      }
      if (node.type === 'CallExpression' && node.callee.type === 'Identifier') {
        const bound = scope.get(node.callee.name);
        const fn = bound?.kind === 'init' && bound.init && (bound.init.type === 'ArrowFunctionExpression' || bound.init.type === 'FunctionExpression' || bound.init.type === 'FunctionDeclaration') ? bound.init : null;
        if (fn?.body?.type === 'BlockStatement') {
          const text = blockQuery(fn, (prefix || '').split('?')[0]);
          if (text != null) return [text];
        }
        if (fn && fn.body && fn.body.type !== 'BlockStatement' && fn.params.length <= 2 && node.arguments.length <= fn.params.length) {
          const inner = new Scope(bound.scope?.parent || scope);
          fn.params.forEach((param, index) => {
            if (param.type === 'Identifier' && node.arguments[index]) inner.bindings.set(param.name, {kind: 'init', init: node.arguments[index], scope, constant: true});
          });
          const value = resolve(fn.body, inner, depth + 1, prefix);
          if (value) return value;
        }
        if ((node.callee.name === 'String' || node.callee.name === 'query') && node.arguments[0]?.type === 'ObjectExpression') {
          return queryVariants(node.arguments[0], (prefix || '').split('?')[0], keyNode => resolve(keyNode, scope, depth + 1, prefix));
        }
      }
      if (node.type === 'NewExpression' && node.callee.type === 'Identifier' && node.callee.name === 'URLSearchParams') {
        return searchText(node, scope, prefix);
      }
      if (node.type === 'BinaryExpression' && ['===', '==', '!==', '!='].includes(node.operator)) {
        const left = resolve(node.left, scope, depth + 1, prefix);
        const right = resolve(node.right, scope, depth + 1, prefix);
        if (left && right && left.length === 1 && right.length === 1) {
          const same = left[0] === right[0];
          return [node.operator.startsWith('!') ? (same ? 'false' : 'true') : (same ? 'true' : 'false')];
        }
      }
      if (node.type === 'UnaryExpression' && node.operator === '!') {
        const inner = resolve(node.argument, scope, depth + 1, prefix);
        if (inner && inner.length === 1) return [inner[0] && inner[0] !== 'false' && inner[0] !== '' ? 'false' : 'true'];
      }
      if (node.type === 'MemberExpression' && !node.computed && node.property.type === 'Identifier') {
        const obj = node.object.type === 'Identifier' ? scope.get(node.object.name) : null;
        if (obj?.kind === 'init' && obj.init?.type === 'ObjectExpression') {
          const prop = obj.init.properties.find(item => item.key && (item.key.name || item.key.value) === node.property.name);
          if (prop) return resolve(prop.value, obj.scope, depth + 1, prefix);
        }
        if (node.property.name === 'path' && !harvesting) {
          harvesting = true;
          try {
            const values = [];
            for (const prop of pathProps) {
              if (prop.file !== file) continue;
              const resolved = resolve(prop.value, prop.scope, depth + 1, prefix);
              if (resolved) values.push(...resolved.filter(item => item.startsWith('/')));
            }
            if (values.length) return [...new Set(values)].slice(0, 24);
          } finally { harvesting = false; }
        }
      }
      if (node.type === 'MemberExpression' && node.computed && node.property.type === 'Literal' && node.property.value === 0) {
        const heads = arrayHeads(node.object, scope);
        if (heads) return heads;
      }
      return null;
    };
    const arrayHeads = (objectNode, scope) => {
      let obj = objectNode;
      if (objectNode.type === 'Identifier') obj = scope.get(objectNode.name)?.init;
      if (obj?.type === 'MemberExpression' && obj.computed && obj.object?.type === 'ObjectExpression') obj = obj.object;
      if (obj?.type !== 'ObjectExpression') return null;
      const heads = [];
      for (const prop of obj.properties) {
        const item = prop.value?.type === 'ArrayExpression' ? literalString(prop.value.elements[0]) : null;
        if (item) heads.push(item);
      }
      return heads.length ? heads : null;
    };
    const objectFromSearch = (current, currentScope) => {
      if (!current) return null;
      if (current.type === 'ObjectExpression') return current;
      if (current.type === 'Identifier') {
        const bound = currentScope.get(current.name);
        if (bound?.init) return objectFromSearch(bound.init, bound.scope || currentScope);
      }
      if (current.type === 'NewExpression' && current.callee?.name === 'URLSearchParams' && current.arguments?.[0]) return objectFromSearch(current.arguments[0], currentScope);
      if (current.type === 'CallExpression' && current.callee.type === 'MemberExpression' && current.callee.property.name === 'filter') {
        const inner = current.callee.object;
        if (inner?.type === 'CallExpression' && inner.callee.type === 'MemberExpression' && inner.callee.object?.name === 'Object' && inner.callee.property?.name === 'entries') {
          return objectFromSearch(inner.arguments[0], currentScope);
        }
      }
      return null;
    };
    const setKeys = (name, fn) => {
      const keys = [];
      const walk = current => {
        if (!current?.type) return;
        if ((current.type === 'FunctionDeclaration' || current.type === 'FunctionExpression' || current.type === 'ArrowFunctionExpression') && current !== fn) return;
        if (current.type === 'CallExpression' && current.callee.type === 'MemberExpression' && current.callee.object.type === 'Identifier' && current.callee.object.name === name && ['set', 'append'].includes(current.callee.property.name)) {
          const key = literalString(current.arguments[0]);
          if (key) keys.push(key);
        }
        for (const child of children(current)) walk(child);
      };
      if (fn) walk(fn);
      return [...new Set(keys)];
    };
    const searchText = (current, currentScope, currentPrefix) => {
      const pathname = (currentPrefix || '').split('?')[0];
      if (!current) return null;
      if (current.type === 'NewExpression' && current.callee.type === 'Identifier' && current.callee.name === 'URLSearchParams') {
        const obj = objectFromSearch(current.arguments[0], currentScope);
        if (obj) return queryVariants(obj, pathname, keyNode => resolve(keyNode, currentScope, 0, pathname));
        if (!current.arguments[0]) return [''];
        return null;
      }
      if (current.type === 'Identifier') {
        const bound = currentScope.get(current.name);
        if (bound?.kind === 'init' && bound.init?.type === 'NewExpression' && bound.init.callee?.name === 'URLSearchParams') {
          const built = searchText(bound.init, bound.scope || currentScope, currentPrefix);
          const base = bound.init.arguments?.length ? built : (built || ['']);
          if (!base) return null;
          const extra = setKeys(current.name, bound.fn);
          if (!extra.length) return base;
          const addition = extra.map(key => `${encodeURIComponent(key)}=${encodeURIComponent(queryValue(pathname, key))}`).join('&');
          const bases = base.filter(Boolean);
          if (!bases.length) return [addition];
          return [...new Set([...bases, ...bases.map(item => `${item}&${addition}`)])];
        }
      }
      return null;
    };
    const blockQuery = (fn, pathname) => {
      if (fn.body?.type !== 'BlockStatement' || fn.body.body.length > 16) return null;
      const returnsParams = fn.body.body.some(stmt => stmt.type === 'ReturnStatement' && (
        stmt.argument?.callee?.property?.name === 'toString'
        || (stmt.argument?.type === 'Identifier' && fn.body.body.some(item => item.type === 'VariableDeclaration' && item.declarations.some(decl => decl.id?.name === stmt.argument.name && decl.init?.type === 'NewExpression' && decl.init.callee?.name === 'URLSearchParams')))
      ));
      if (!returnsParams) return null;
      const keys = [];
      const walk = current => {
        if (!current?.type) return;
        if ((current.type === 'FunctionDeclaration' || current.type === 'FunctionExpression' || current.type === 'ArrowFunctionExpression') && current !== fn) return;
        if (current.type === 'NewExpression' && current.callee?.name === 'URLSearchParams' && current.arguments[0]?.type === 'ObjectExpression') {
          for (const prop of current.arguments[0].properties) if (prop.type === 'Property' && !prop.computed) keys.push(String(prop.key.name || prop.key.value));
        }
        if (current.type === 'CallExpression' && current.callee.type === 'MemberExpression' && ['set', 'append'].includes(current.callee.property?.name)) {
          const key = literalString(current.arguments[0]);
          if (key) keys.push(key);
        }
        for (const child of children(current)) walk(child);
      };
      walk(fn.body);
      const unique = [...new Set(keys.filter(Boolean))];
      if (!unique.length) return null;
      return unique.map(key => `${encodeURIComponent(key)}=${encodeURIComponent(queryValue(pathname, key))}`).join('&');
    };
    const holeFallback = (expr, prefix, scope) => {
      const queryHole = prefix.includes('?') && (prefix.endsWith('?') || prefix.endsWith('&') || prefix.endsWith('='));
      if (!queryHole) {
        const sampled = sampleHole(prefix);
        if (sampled != null && (expr.type === 'Identifier' || expr.type === 'MemberExpression' || expr.type === 'CallExpression' || expr.type === 'ChainExpression')) return [sampled];
      }
      if (queryHole && (expr.type === 'NewExpression' || expr.type === 'CallExpression' || expr.type === 'Identifier' || expr.type === 'ConditionalExpression')) {
        const built = resolve(expr, scope, 0, prefix.split('?')[0]);
        const useful = (built || []).filter(item => item !== '' && item !== 'null' && item !== 'undefined');
        if (useful.length) return useful;
      }
      if (prefix.endsWith('=')) {
        const key = prefix.slice(Math.max(prefix.lastIndexOf('?'), prefix.lastIndexOf('&')) + 1, -1);
        if (key) return [queryValue(prefix.split('?')[0], key)];
      }
      if (queryHole) return [''];
      return null;
    };
    const record = (node, scope) => {
      const callee = node.callee;
      let forward = null;
      if (callee.type === 'Identifier' && callee.name !== 'api') {
        const bound = scope.get(callee.name);
        const fn = bound?.kind === 'init' ? bound.init : null;
        if (fn && (fn.type === 'FunctionExpression' || fn.type === 'ArrowFunctionExpression' || fn.type === 'FunctionDeclaration') && isForwarder(fn)) forward = fn;
        else return;
      } else if (!(callee.type === 'Identifier' && callee.name === 'api')) return;
      if (node.arguments.some(arg => arg.type === 'SpreadElement')) return;
      const methodNode = forward ? node.arguments[0] : node.arguments[0];
      const pathNode = node.arguments[1];
      const methods = resolve(methodNode, scope, 0, '');
      const paths = resolve(pathNode, scope, 0, '');
      const rel = path.relative(SRC, file);
      const line = node.loc.start.line;
      const usable = (paths || []).filter(usablePath);
      if (!methods || !methods.length || !usable.length) {
        unresolved.push({file: rel, line, reason: !methods ? 'method did not resolve' : 'path did not resolve'});
        return;
      }
      for (const method of methods) {
        if (method !== 'GET' && method !== 'POST') {
          unresolved.push({file: rel, line, reason: `method is not a fixed GET or POST (${method})`});
          continue;
        }
        for (const raw of usable) calls.push({method, path: raw, body: representativeBody(method, raw), idempotency_key: representativeKey(method, raw), file: rel, line});
      }
    };
    const visitCalls = (node, scope) => {
      if (!node || !node.type) return;
      if (node.type === 'FunctionDeclaration' || node.type === 'FunctionExpression' || node.type === 'ArrowFunctionExpression') {
        const inner = functions.find(item => item.node === node)?.scope || scope;
        visitCalls(node.body, inner);
        return;
      }
      if (node.type === 'ForOfStatement') {
        visitCalls(node.right, scope);
        visitCalls(node.body, loopScopes.get(node) || scope);
        return;
      }
      if (node.type === 'CallExpression') record(node, scope);
      for (const child of children(node)) visitCalls(child, scope);
    };
    // Call sites in later files (an import of this function) are not visible yet.
    pendingCalls.push(() => visitCalls(ast, root));
  }
  for (const run of pendingCalls) run();
  const seen = new Set();
  const unique = [];
  for (const call of calls) {
    const key = `${call.method} ${call.path} ${call.file}:${call.line}`;
    if (seen.has(key)) continue;
    seen.add(key);
    unique.push(call);
  }
  const concrete = new Set(unique.map(call => `${call.file}:${call.line}`));
  return {
    calls: unique,
    unresolved: unresolved.filter(item => !concrete.has(`${item.file}:${item.line}`)),
  };
}

function representativeKey(method, fullPath) {
  return method === 'POST' && fullPath.split('?')[0] === '/operations' ? 'frontend-route-check' : null;
}

function representativeBody(method, fullPath) {
  if (method !== 'POST') return null;
  const pathname = fullPath.split('?')[0];
  if (pathname === '/operations' || pathname.endsWith('/cancel') || pathname.endsWith('/resume')) return {};
  if (pathname === '/repository-previews') return {repository: 'octo/repo', host: 'fixture', workspace_id: 'folder', source_ref: 'refs/heads/main'};
  if (pathname === '/approval-previews') return {host: 'fixture'};
  if (pathname === '/cleanup-previews') return {target: {kind: 'host', host: 'fixture'}, choices: {}};
  if (pathname === '/artifact-capture-previews') return {host: 'fixture', session_id: 'sess.1', relative_path: 'src/main.txt'};
  if (pathname === '/artifact-managed-capture-previews') return {host: 'fixture', session_id: 'sess.1', relative_path: 'src/main.txt', execution_operation_id: ID.op};
  if (pathname === '/managed/setup/secrets') return {kind: 'bat', value: 'test-secret'};
  if (pathname.endsWith('/start-preview')) return {instruction: 'Ship the login fix\nwith tests', delivery: 'pull_request'};
  return {};
}

export function bridgePatterns() {
  const source = fs.readFileSync(BRIDGE, 'utf8');
  const getStart = source.indexOf('Regex::new(concat!(');
  if (getStart < 0) throw new Error('bridge GET allow-list was not found');
  const getBody = source.slice(getStart, source.indexOf(').unwrap()', getStart));
  const parts = [...getBody.matchAll(/r"([^"]*)"/g)].map(match => match[1]);
  if (!parts.length) throw new Error('bridge GET allow-list strings were not found');
  const postAt = source.indexOf('input.method == "POST"', getStart);
  const postNew = source.indexOf('Regex::new(r"', postAt);
  const postRaw = source.slice(postNew + 'Regex::new(r"'.length, source.indexOf('")', postNew + 12));
  return {get: new RegExp(parts.join('')), post: new RegExp(postRaw)};
}

export function allowedByBridge(call, patterns = bridgePatterns()) {
  const pathname = call.path.split('?')[0];
  const query = call.path.slice(pathname.length);
  if (pathname.includes('%') || pathname.split('/').some(part => part === '.' || part === '..')) return false;
  if (/%(?![0-9A-Fa-f]{2})/.test(query)) return false;
  if (call.method === 'GET') return patterns.get.test(pathname);
  if (call.method === 'POST') return patterns.post.test(pathname);
  return false;
}

const isDirect = process.argv[1] && path.resolve(process.argv[1]) === path.resolve(new URL(import.meta.url).pathname);
if (isDirect) {
  const found = discoverCentralRoutes();
  if (process.argv.includes('--json')) {
    process.stdout.write(`${JSON.stringify(found.calls)}\n`);
    if (found.unresolved.length) {
      process.stderr.write(found.unresolved.map(item => `${item.file}:${item.line}: ${item.reason}`).join('\n') + '\n');
      process.exit(2);
    }
  } else {
    const refused = found.calls.filter(call => !allowedByBridge(call));
    console.log(`${found.calls.length} calls, ${found.unresolved.length} unresolved, ${refused.length} refused`);
    for (const item of found.unresolved) console.log(`UNRESOLVED ${item.file}:${item.line}: ${item.reason}`);
    const unique = new Map();
    for (const call of refused) unique.set(`${call.method} ${call.path}`, `${call.file}:${call.line}`);
    for (const [route, loc] of unique) console.log(`REFUSED ${route} (${loc})`);
  }
}
