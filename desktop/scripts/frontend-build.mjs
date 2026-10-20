import {createHash} from 'node:crypto';
import {readFileSync, readdirSync} from 'node:fs';
import {join} from 'node:path';

// Desktop-relative POSIX paths; generated assets and Git metadata are excluded.
// Keep this explicit set and the framing contract documented in diagnostics.md.
const buildFiles = ['index.html', 'package.json', 'package-lock.json', 'tsconfig.json',
  'vite.config.ts', 'scripts/frontend-build.mjs', 'scripts/browser-assets.mjs'];
export function frontendInputs(root) {
  const files = [...buildFiles];
  const visit = directory => {
    for (const entry of readdirSync(join(root, directory), {withFileTypes: true})) {
      const path = `${directory}/${entry.name}`;
      if (entry.isDirectory()) visit(path);
      else if (entry.isFile()) files.push(path);
      else throw new Error(`Frontend input must be a regular file: ${path}`);
    }
  };
  visit('src');
  return files.sort((a, b) => Buffer.compare(Buffer.from(a), Buffer.from(b)));
}
export function frontendDigest(root) {
  const hash = createHash('sha256').update('batc.frontend.v1\0');
  for (const path of frontendInputs(root)) {
    const bytes = readFileSync(join(root, path));
    hash.update(`${Buffer.byteLength(path)}:${path}\0${bytes.length}\0`).update(bytes);
  }
  return hash.digest('hex');
}
