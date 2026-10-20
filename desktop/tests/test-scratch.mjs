import {mkdirSync} from 'node:fs';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

// Keep the existing managed-browser checkout default, independent of cwd. An
// explicit test override wins; CI otherwise uses its runner-provided disk root.
const checkoutScratch = fileURLToPath(new URL('../../.test-tmp', import.meta.url));

export function testScratchRoot(environment = process.env) {
  const root = resolve(environment.BATC_TEST_TMP || environment.RUNNER_TEMP || checkoutScratch);
  mkdirSync(root, {recursive: true, mode: 0o700});
  return root;
}
