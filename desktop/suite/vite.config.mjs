import {fileURLToPath} from 'node:url';
import {resolve} from 'node:path';
import {defineConfig} from 'vite';

export const desktopRoot = fileURLToPath(new URL('..', import.meta.url));
const transport = resolve(desktopRoot, 'src/transport/index.ts');
const adapter = resolve(desktopRoot, 'suite/core-transport.ts');

// Only the shared transport entrypoint is substituted; the adapter imports real core.
export function suiteTransportAlias({requireRedirect = true} = {}) {
  let redirects = 0;
  return {
    name: 'suite-transport-alias',
    enforce: 'pre',
    resolveId(source, importer) {
      if (source === '@tauri-apps/api/core' && importer === transport) {
        redirects++;
        return adapter;
      }
    },
    generateBundle() {
      if (requireRedirect && redirects !== 1) throw new Error(`Expected one suite transport redirect; got ${redirects}`);
    },
    getRedirectCount() { return redirects; },
  };
}

export function suiteConfig({harness = false} = {}) {
  return defineConfig({
    configFile: false,
    root: desktopRoot,
    base: './',
    logLevel: 'silent',
    plugins: [suiteTransportAlias()],
    build: {
      outDir: resolve(desktopRoot, harness ? 'dist-suite-harness' : 'dist-suite/assets'),
      emptyOutDir: true,
      sourcemap: false,
      rollupOptions: {input: resolve(desktopRoot, harness ? 'suite/tests/harness/tailscale.html' : 'index.html')},
    },
  });
}

export default suiteConfig();
