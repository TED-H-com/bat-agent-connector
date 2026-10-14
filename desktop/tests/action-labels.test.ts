import {execFileSync} from 'node:child_process';
import {dirname, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {actionMessageKey} from '../src/global-labels.js';
import {globalStrings} from '../src/global-strings.js';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '../..');

function registeredActions() {
  const output = execFileSync('python3', [resolve(here, 'scan-actions.py'), resolve(root, 'src/bat_agent_connector')], {encoding: 'utf8'});
  return output.split('\n').map(line => line.trim()).filter(Boolean);
}

test('every registered action has an English and Traditional Chinese label', () => {
  const names = registeredActions();
  for (const sentinel of ['artifact.accept', 'artifact.capture.managed', 'pm.sprint.create', 'pm.work_batch.start', 'pm.maintenance.repair', 'pm.conversation.adopt', 'pm.dependencies.capture', 'fanout.plan']) {
    assert.ok(names.includes(sentinel), `scanner missed ${sentinel}`);
  }
  assert.ok(names.length >= 134, `scanner found ${names.length} actions`);
  const missing = [];
  for (const name of names) {
    const key = actionMessageKey(name);
    const en = globalStrings.en[key];
    const zh = globalStrings['zh-TW'][key];
    if (!key || typeof en !== 'string' || !en || en === key || typeof zh !== 'string' || !zh || zh === key) missing.push(`${name} → ${key || '(none)'}`);
  }
  assert.deepEqual(missing, []);
});
