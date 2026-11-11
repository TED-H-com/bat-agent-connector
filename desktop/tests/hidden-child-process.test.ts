import {test} from 'node:test';
import assert from 'node:assert/strict';
import {promisify} from 'node:util';
import {spawn, spawnSync, execFile, execFileSync} from '../../scripts/hidden-child-process.mjs';

test('hidden process wrappers preserve argv, IO, options and Node overloads', async () => {
  const args = ['-e', 'process.stdout.write(process.argv[1]);process.stderr.write("err")', '繁體 a " b'];
  const options = {encoding: 'utf8', windowsHide: false};
  assert.equal(execFileSync(process.execPath, args, {...options, stdio: ['ignore', 'pipe', 'ignore']}), args[2]);
  const result = spawnSync(process.execPath, args, options);
  assert.equal(result.stdout, args[2]);
  assert.equal(result.stderr, 'err');
  assert.equal(result.status, 0);
  assert.equal(options.windowsHide, false);
  const pending = promisify(execFile)(process.execPath, args, options);
  assert.ok(pending.child.pid);
  assert.deepEqual(await pending, {stdout: args[2], stderr: 'err'});
  await new Promise((resolve, reject) => execFile(process.execPath, args, (error, stdout, stderr) => {
    if (error) return reject(error);
    assert.equal(stdout, args[2]); assert.equal(stderr, 'err'); resolve();
  }));
  const child = spawn(process.execPath, ['-e', 'process.exit(0)']);
  assert.equal(await new Promise(resolve => child.once('close', resolve)), 0);
});
