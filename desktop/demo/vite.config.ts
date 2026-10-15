import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {defineConfig} from 'vite';

const root = path.dirname(fileURLToPath(import.meta.url));
// The public tour builds the real Dashboard. Only its transport and locale are
// substituted at build time; installed/browser product builds never import demo data.
const replacements = new Map([
  [path.resolve(root, '../src/transport/index.ts'), path.join(root, 'transport.ts')],
  [path.resolve(root, '../src/i18n.js'), path.join(root, 'i18n.js')],
]);
export default defineConfig({
  root, base: './',
  plugins: [{name: 'public-demo-boundary', enforce: 'pre', async resolveId(source, importer) {
    if (!importer || [...replacements.values()].includes(importer)) return null;
    const resolved = await this.resolve(source, importer, {skipSelf: true});
    return resolved && replacements.get(resolved.id) || null;
  }}],
  build: {outDir: '../dist-demo', emptyOutDir: true, modulePreload: false,
    rollupOptions: {output: {codeSplitting: false, entryFileNames: 'app.js', assetFileNames: 'app[extname]'}}},
});
