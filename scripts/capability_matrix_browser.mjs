// Execute shipped transport and SDK bytes; Python CI does not install npm tools.
// Locale and HTTP are adapters. No native status or IPC reply is fabricated.
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';

const [snapshot, capabilitiesFile] = process.argv.slice(2);
const capabilities = JSON.parse(fs.readFileSync(capabilitiesFile, 'utf8'));
const expectedCapabilities = JSON.stringify(capabilities);
const bundle = fs.readFileSync(path.join(snapshot, 'src/bat_agent_connector/dashboard/app.js'), 'utf8');
function uniqueSlice(start, end) {
  if (bundle.split(start).length !== 2) throw new Error('BROWSER_PRODUCER_REGION_UNAVAILABLE');
  const offset = bundle.indexOf(start), stop = bundle.indexOf(end, offset + start.length);
  if (stop < 0) throw new Error('BROWSER_PRODUCER_REGION_UNAVAILABLE');
  return bundle.slice(offset, stop);
}
// These are the producer's unminified source regions, not another copy of its
// functions. Layout changes fail closed; regenerate the golden with this probe.
// Existing check:browser also guards source -> shipped bundle freshness.
const runtime = uniqueSlice('var __defProp =', '//#endregion');
const sdk = uniqueSlice('var init_tslib_es6 =', '//#endregion');
const producers = new Map();
function includeRegion(marker) {
  if (producers.has(marker)) return;
  const source = uniqueSlice(marker, '//#endregion');
  producers.set(marker, source);
  for (const [, initializer] of source.matchAll(/\b(init_\w+)\(\);/g)) {
    if (initializer === 'init_i18n') continue; // Locale is this probe's adapter.
    const declaration = bundle.indexOf(`var ${initializer} =`);
    if (declaration < 0) throw new Error('BROWSER_DEPENDENCY_REGION_UNAVAILABLE');
    const start = bundle.lastIndexOf('//#region ', declaration);
    includeRegion(bundle.slice(start, bundle.indexOf('\n', start)));
  }
}
includeRegion('//#region src/transport/index.ts');
// The bundler may hoist this shared module's initialization out of transport.
// Keep it as an explicit probe root (and initialize it below); renamed regions
// fail closed, while its dependencies still come from the shipped init chain.
includeRegion('//#region src/credential-refusals.js');
const producerBytes = [...producers.values()].join('\n');
if (!sdk.includes('async function invoke(') || !sdk.includes('function isTauri(')) {
  throw new Error('BROWSER_SDK_REGION_UNAVAILABLE');
}
const requests = [];
let authenticatedRequest = false;
const browserWindow = {}; // An ordinary browser has no __TAURI_INTERNALS__.
const storage = {getItem: () => null};
const context = {window: browserWindow, locale: 'en', init_i18n() {},
  sessionStorage: storage, localStorage: storage, location: {origin: 'http://localhost'}, TextEncoder,
  fetch: async (url, options) => {
    requests.push({method: options.method, path: url});
    authenticatedRequest = options.headers.Authorization === 'Bearer fixture-token'
      && options.credentials === 'same-origin' && options.redirect === 'error';
    return {status: 200, json: async () => capabilities}; // Complete real producer response.
  }};
// Preserve module scope: a script-global function named isTauri would itself
// populate globalThis.isTauri, unlike the shipped ES module, and fake native mode.
const exports = vm.runInNewContext(`(() => {${runtime}\n${sdk}\n${producerBytes}\ninit_credential_refusals(); init_transport();
  return {nativeDesktop, connectorRequest, fleetAvailability, nativeEnroll};})()`, context, {timeout: 5000});
const reply = await exports.connectorRequest('GET', '/capabilities', undefined, undefined, 'fixture-token');
async function unavailable(call) {
  try { await call(); return false; } catch (error) {
    if (error.name === 'TypeError' && !('__TAURI_INTERNALS__' in browserWindow)) return true;
    throw error;
  }
}
console.log(JSON.stringify({producer: 'shipped_browser_bundle', native_desktop: exports.nativeDesktop,
  central_request: requests[0], authenticated_request: authenticatedRequest,
  producer_response_unchanged: reply.status === 200 && JSON.stringify(reply.data) === expectedCapabilities,
  native_fleet_unavailable: await unavailable(exports.fleetAvailability),
  credential_vault_unavailable: await unavailable(exports.nativeEnroll)}));
