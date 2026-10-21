// Execute the candidate's transport with the real SDK, without a native bridge.
// Only the display locale and HTTP response are adapters; no IPC status is made up.
import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {createRequire, stripTypeScriptTypes} from 'node:module';

const [snapshot, tools] = process.argv.slice(2);
const require = createRequire(path.join(tools, 'desktop/package.json'));
const {parse} = require('acorn');
const declared = JSON.parse(fs.readFileSync(path.join(snapshot, 'desktop/package.json')));
for (const name of ['acorn', '@tauri-apps/api']) {
  const actual = JSON.parse(fs.readFileSync(path.join(tools, 'desktop/node_modules', name, 'package.json')));
  if (actual.version !== (declared.dependencies?.[name] ?? declared.devDependencies?.[name])) {
    throw new Error('PROBE_DEPENDENCY_DIFFERS');
  }
}
globalThis.window = {}; // An ordinary browser has no __TAURI_INTERNALS__.
const requests = [];
let authenticatedRequest = false;
const source = fs.readFileSync(path.join(snapshot, 'desktop/src/transport/index.ts'), 'utf8');
let code = stripTypeScriptTypes(source);
const imports = parse(code, {ecmaVersion: 'latest', sourceType: 'module'}).body.filter(n => n.type === 'ImportDeclaration');
for (const node of imports.reverse()) {
  const name = node.source.value;
  const target = name === '@tauri-apps/api/core' ? pathToFileURL(require.resolve(name)).href
    : name === '../i18n.js' ? 'data:text/javascript,export const locale="en";' : null;
  if (!target) throw new Error('UNEXPECTED_TRANSPORT_IMPORT');
  code = code.slice(0, node.source.start) + JSON.stringify(target) + code.slice(node.source.end);
}
globalThis.fetch = async (url, options) => {
  requests.push({method: options.method, path: url});
  authenticatedRequest = options.headers.Authorization === 'Bearer fixture-token'
    && options.credentials === 'same-origin' && options.redirect === 'error';
  return {status: 200, json: async () => ({})}; // No server fields invented.
};
const exports = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'));
await exports.connectorRequest('GET', '/capabilities', undefined, undefined, 'fixture-token');
async function unavailable(call) {
  try { await call(); return false; } catch (error) {
    if (error instanceof TypeError && !('__TAURI_INTERNALS__' in window)) return true;
    throw error;
  }
}
console.log(JSON.stringify({native_desktop: exports.nativeDesktop,
  central_request: requests[0],
  authenticated_request: authenticatedRequest,
  native_fleet_unavailable: await unavailable(exports.fleetAvailability),
  credential_vault_unavailable: await unavailable(exports.nativeEnroll)}));
