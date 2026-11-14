// @ts-nocheck
import assert from 'node:assert/strict';
import {test} from 'node:test';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {build} from 'vite';
import {desktopRoot, suiteTransportAlias} from '../vite.config.mjs';
import {invoke, isTauri} from '../core-transport.ts';

globalThis.window = globalThis;
globalThis.isTauri = true;
const calls = [];
globalThis.__TAURI_INTERNALS__ = {invoke: async (...args) => { calls.push(args); return {fixture: true}; }};

test('only native_status invokes the real core', async () => {
  calls.length = 0;
  assert.equal(isTauri(), true);
  assert.deepEqual(await invoke('native_status'), {fixture: true});
  assert.deepEqual(calls.map(call => call[0]), ['plugin:bac|native_status']);
  calls.length = 0;
  assert.deepEqual(await invoke('managed_control', {input: {action: 'status'}}), {background: false, lifecycle: null});
  assert.deepEqual(await invoke('tailscale_control', {input: {action: 'status'}}),
    {version: 1, supported: false, can_open: false, installation: 'unknown', login: 'unknown', latest: null});
  assert.deepEqual(calls, []);
});

test('all other native commands and non-status actions reject locally', async () => {
  calls.length = 0;
  const commands = [
    'fleet_availability', 'native_diagnostics_save', 'native_files_status', 'native_files_pick',
    'native_files_paste_arm', 'native_files_paste', 'native_files_upload', 'native_files_drop_target',
    'native_files_control', 'native_files_save', 'native_files_preview', 'native_brief_save',
    'managed_select_role', 'connector_connect', 'connector_disconnect', 'connector_enroll',
    'connector_reload_configuration', 'connector_setup_configuration', 'connector_forget_credential',
    'open_external', 'fleet_bootstrap', 'fleet_control', 'fleet_request', 'desktop_update',
    'connector_request', 'unknown_command',
  ];
  for (const command of commands) {
    await assert.rejects(invoke(command), error => error instanceof Error && error.code === 'BAC_PREVIEW_UNSUPPORTED');
    assert.deepEqual(calls, [], command);
  }
  for (const [command, actions] of [
    ['managed_control', ['open_browser', 'stop', 'start', 'set_login', 'unknown', null]],
    ['tailscale_control', ['open', 'receipt', 'unknown', null]],
  ]) {
    for (const action of actions) {
      await assert.rejects(invoke(command, {input: {action}}), {code: 'BAC_PREVIEW_UNSUPPORTED'});
      assert.deepEqual(calls, [], `${command}:${action}`);
    }
    await assert.rejects(invoke(command), {code: 'BAC_PREVIEW_UNSUPPORTED'});
    assert.deepEqual(calls, []);
  }
});

test('Vite redirects only the exact transport importer and enforces the count', async () => {
  const alias = suiteTransportAlias({requireRedirect: false});
  const fixture = fileURLToPath(new URL('./fixtures/unrelated-import.ts', import.meta.url));
  await build({configFile: false, root: desktopRoot, logLevel: 'silent', plugins: [alias],
    build: {write: false, rollupOptions: {input: fixture}}});
  assert.equal(alias.getRedirectCount(), 0);
  await assert.rejects(build({configFile: false, root: desktopRoot, logLevel: 'silent',
    plugins: [suiteTransportAlias()], build: {write: false, rollupOptions: {input: fixture}}}),
    /Expected one suite transport redirect; got 0/);
  const exact = suiteTransportAlias();
  const importer = resolve(desktopRoot, 'src/transport/index.ts');
  assert.equal(exact.resolveId('@tauri-apps/api/core', `${importer}?other`), undefined);
  assert.equal(exact.resolveId('@tauri-apps/api/window', importer), undefined);
  assert.equal(exact.resolveId('@tauri-apps/api/core', importer), resolve(desktopRoot, 'suite/core-transport.ts'));
  assert.equal(exact.resolveId('@tauri-apps/api/core', importer), resolve(desktopRoot, 'suite/core-transport.ts'));
  assert.throws(() => exact.generateBundle(), /got 2/);
});
