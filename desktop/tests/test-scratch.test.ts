import assert from 'node:assert/strict';
import {mkdtemp, rm, stat} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {test} from 'node:test';
import {testScratchRoot} from './test-scratch.mjs';

test('desktop scratch defaults to the checkout even from a different working directory', async () => {
  const expected = fileURLToPath(new URL('../../.test-tmp', import.meta.url));
  const directory = await mkdtemp(join(testScratchRoot({}), 'scratch-rule-'));
  const previous = process.cwd();
  try {
    process.chdir(directory);
    assert.equal(testScratchRoot({TMPDIR: join(directory, 'ignored-os-temp')}), expected);
  } finally {
    process.chdir(previous);
    await rm(directory, {recursive: true, force: true});
  }
});

test('desktop scratch honors explicit overrides before the CI runner directory', async () => {
  const directory = await mkdtemp(join(testScratchRoot({}), 'scratch-rule-'));
  try {
    const override = join(directory, 'explicit'), runner = join(directory, 'runner');
    assert.equal(testScratchRoot({BATC_TEST_TMP: override, RUNNER_TEMP: runner}), override);
    assert((await stat(override)).isDirectory());
    await assert.rejects(stat(runner), {code: 'ENOENT'});
    assert.equal(testScratchRoot({RUNNER_TEMP: runner}), runner);
    assert((await stat(runner)).isDirectory());
  } finally {
    await rm(directory, {recursive: true, force: true});
  }
});
