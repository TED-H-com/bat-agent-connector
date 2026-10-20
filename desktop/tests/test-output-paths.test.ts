import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readdir, readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join, relative} from 'node:path';
import {tokenizer} from 'acorn';

test('desktop tests keep temporary output out of tmpfs', async () => {
  const root = fileURLToPath(new URL('.', import.meta.url));
  const violations: string[] = [];
  const generated = new Set(['node_modules', 'test-results', 'playwright-report', 'dist', 'coverage', '.test-tmp']);
  async function scan(directory: string) {
    for (const entry of await readdir(directory, {withFileTypes: true})) {
      const file = join(directory, entry.name);
      if (entry.isDirectory() && !generated.has(entry.name)) await scan(file);
      else if (entry.isFile() && /\.(?:ts|mjs|js)$/.test(entry.name)) {
        const source = await readFile(file, 'utf8');
        const allowed = new Set<number>();
        const tokens = [...tokenizer(source, {ecmaVersion: 'latest', locations: true,
          onComment: (_block, comment, _start, _end, location) => {
            if (/\btmpfs-path-allow:\s*\S/.test(comment)) allowed.add(location.line);
          }})];
        tokens.forEach((token, index) => {
          const line = token.loc!.start.line;
          if (allowed.has(line)) return;
          const previous = tokens[index - 1], property = tokens[index - 2], before = tokens[index - 3];
          // Object root/repository_root values describe mock responses, not output
          // destinations. Other absolute temp literals (including template pieces)
          // require an inline justification; calls to the OS temp resolver are
          // forbidden even inside join/resolve. Tokenization ignores quoted code
          // examples and comments, and also works with TypeScript annotations.
          const payload = previous?.type.label === ':' && ['{', ','].includes(before?.type.label)
            && ['root', 'repository_root'].includes(String(property?.value));
          const literal = ['string', 'template'].includes(token.type.label)
            && /^\/tmp(?:\/|$)/.test(String(token.value)) && !payload;
          const systemTemporary = token.type.label === 'name' && token.value === 'tmpdir'
            && tokens[index + 1]?.type.label === '(';
          if (literal || systemTemporary) {
            violations.push(`${relative(root, file)}:${line}`);
          }
        });
      }
    }
  }
  await scan(root);
  assert.deepEqual(violations, [], `Use per-test output or the shared test scratch root:\n${violations.join('\n')}`);
});
