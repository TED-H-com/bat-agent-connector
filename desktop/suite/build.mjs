import {readFileSync, writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {build} from 'vite';
import {buildManifest, verifyManifest} from './manifest.mjs';
import {suiteProvenance} from './provenance.mjs';
import {desktopRoot, suiteConfig} from './vite.config.mjs';

async function main() {
  const {connectorVersion, gitCommit, harness} = suiteProvenance(resolve(desktopRoot, '..'));
  await build(suiteConfig());
  const assetRoot = resolve(desktopRoot, 'dist-suite/assets');
  const manifest = buildManifest(assetRoot, {connectorVersion, gitCommit});
  const manifestPath = resolve(desktopRoot, 'dist-suite/manifest.json');
  writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);
  verifyManifest(assetRoot, JSON.parse(readFileSync(manifestPath, 'utf8')));
  if (harness) await build(suiteConfig({harness: true}));
  console.log(`Suite assets verified: ${manifest.files.length} files, ${manifest.treeSha256}`);
}

main().catch(error => { console.error(`Suite build refused: ${String(error.message || error).split('\n')[0]}`); process.exitCode = 1; });
