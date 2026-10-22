import {test} from 'node:test';
import assert from 'node:assert/strict';

test('native raw-byte upload refuses before invoke or fetch', async () => {
  const globals = globalThis as any;
  const original = {isTauri: globals.isTauri, window: globals.window, fetch: globals.fetch};
  const calls: string[] = [];
  globals.isTauri = true;
  globals.window = {__TAURI_INTERNALS__: {invoke: async (command: string) => {
    calls.push(command);
    throw new Error('Unexpected native command: ' + command);
  }}};
  globals.fetch = async () => {calls.push('fetch'); throw new Error('Unexpected HTTP request');};
  try {
    const {connectorUploadArtifact} = await import('../src/transport/index.ts');
    for (const operationId of ['op_' + 'a'.repeat(32), 'invalid']) {
      await assert.rejects(connectorUploadArtifact(operationId, new ArrayBuffer(1), 'unused'),
        {message: 'Native uploads require the native file manager.'});
    }
    assert.deepEqual(calls, []);
  } finally {Object.assign(globals, original);}
});
