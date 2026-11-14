import {createHash} from 'node:crypto';
import {lstatSync, readFileSync, readdirSync} from 'node:fs';
import {createServer} from 'node:http';
import {extname, join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {MIME_TYPES, verifyManifest} from '../manifest.mjs';

export const CSP = "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; font-src 'self'; connect-src ipc: http://ipc.localhost; frame-src 'none'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'; form-action 'none'";
const desktop = fileURLToPath(new URL('../..', import.meta.url));
const assetRoot = resolve(desktop, 'dist-suite/assets');
const manifest = JSON.parse(readFileSync(resolve(desktop, 'dist-suite/manifest.json'), 'utf8'));
verifyManifest(assetRoot, manifest);
const files = new Map();
const urlPath = path => '/' + path.split('/').map(encodeURIComponent).join('/');
for (const entry of manifest.files) {
  const content = readFileSync(join(assetRoot, ...entry.path.split('/')));
  if (content.length !== entry.length || createHash('sha256').update(content).digest('hex') !== entry.sha256)
    throw new Error('Asset changed while starting fixture server');
  files.set(urlPath(entry.path), {content, mime: entry.mime});
}
files.set('/', files.get('/index.html'));
if (process.argv.includes('--harness')) {
  const root = resolve(desktop, 'dist-suite-harness');
  const visit = (directory, prefix = '') => {
    for (const name of readdirSync(directory)) {
      const path = prefix ? `${prefix}/${name}` : name;
      const absolute = join(directory, name), stat = lstatSync(absolute);
      if (stat.isSymbolicLink()) throw new Error('Harness links are not allowed');
      if (stat.isDirectory()) visit(absolute, path);
      else if (stat.isFile() && MIME_TYPES[extname(name).toLowerCase()]) {
        files.set('/__harness' + urlPath(path), {content: readFileSync(absolute), mime: MIME_TYPES[extname(name).toLowerCase()]});
      } else throw new Error('Invalid harness file');
    }
  };
  visit(root);
}
const port = Number(process.env.BATC_UI_TEST_PORT || process.env.BATC_TEST_PORT || 1421);
if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error('Invalid fixture port');
createServer((request, response) => {
  response.setHeader('X-Content-Type-Options', 'nosniff');
  response.setHeader('Cache-Control', 'no-store');
  response.setHeader('X-Frame-Options', 'DENY');
  response.setHeader('Content-Security-Policy', CSP);
  const asset = ['GET', 'HEAD'].includes(request.method) ? files.get(request.url) : undefined;
  if (!asset) { response.writeHead(404); response.end(); return; }
  response.setHeader('Content-Type', asset.mime);
  response.setHeader('Content-Length', asset.content.length);
  response.writeHead(200);
  response.end(request.method === 'HEAD' ? undefined : asset.content);
}).listen(port, '127.0.0.1');
