import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readdir, readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join, relative} from 'node:path';

test('TypeScript tests keep temporary output out of tmpfs', async () => {
  const root = fileURLToPath(new URL('.', import.meta.url));
  const violations: string[] = [];
  async function scan(directory: string) {
    for (const entry of await readdir(directory, {withFileTypes: true})) {
      const file = join(directory, entry.name);
      if (entry.isDirectory()) await scan(file);
      else if (entry.name.endsWith('.ts')) {
        const lines = (await readFile(file, 'utf8')).split('\n');
        lines.forEach((line, index) => {
          // Include bare temp-root literals used to construct paths in templates.
          if (/\/tmp(?:\/|['"]\s*})/.test(line) && !/\/\/\s*tmpfs-path-allow:\s*\S.*$/.test(line)) {
            violations.push(`${relative(root, file)}:${index + 1}`);
          }
        });
      }
    }
  }
  await scan(root);
  assert.deepEqual(violations, [], `Use testInfo.outputPath() for test output:\n${violations.join('\n')}`);
});
