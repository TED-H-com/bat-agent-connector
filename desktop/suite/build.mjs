import {readFileSync, writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {build} from 'vite';
import {execFileSync} from '../../scripts/hidden-child-process.mjs';
import {buildManifest, verifyManifest} from './manifest.mjs';
import {desktopRoot, suiteConfig} from './vite.config.mjs';

async function main() {
  if (process.argv.slice(2).some(arg => arg !== '--harness') || process.argv.slice(2).length > 1)
    throw new Error('Only --harness is supported');
  const root = resolve(desktopRoot, '..');
  const project = readFileSync(resolve(root, 'pyproject.toml'), 'utf8').match(/^\[project\]\s*\n([\s\S]*?)(?=^\[|$(?![\s\S]))/m);
  const version = project?.[1].match(/^version\s*=\s*["']([^"']+)["']\s*$/m)?.[1];
  if (!version) throw new Error('Cannot read project version');
  const git = args => execFileSync('git', args, {cwd: root, encoding: 'utf8'}).trim();
  const gitCommit = git(['status', '--porcelain']) ? null : git(['rev-parse', 'HEAD']);
  await build(suiteConfig());
  const assetRoot = resolve(desktopRoot, 'dist-suite/assets');
  const manifest = buildManifest(assetRoot, {connectorVersion: version, gitCommit});
  const manifestPath = resolve(desktopRoot, 'dist-suite/manifest.json');
  writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);
  verifyManifest(assetRoot, JSON.parse(readFileSync(manifestPath, 'utf8')));
  if (process.argv.includes('--harness')) await build(suiteConfig({harness: true}));
  console.log(`Suite assets verified: ${manifest.files.length} files, ${manifest.treeSha256}`);
}

main().catch(error => { console.error(`Suite build refused: ${String(error.message || error).split('\n')[0]}`); process.exitCode = 1; });
