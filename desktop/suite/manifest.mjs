import {createHash} from 'node:crypto';
import {constants, closeSync, fstatSync, lstatSync, openSync, readFileSync, readdirSync} from 'node:fs';
import {extname, join} from 'node:path';
import {TextDecoder} from 'node:util';

export const MAX_FILE_BYTES = 16 * 1024 * 1024;
export const MAX_FILES = 4096;
export const MAX_TOTAL_BYTES = 64 * 1024 * 1024;
export const MIME_TYPES = Object.freeze({
  '.html': 'text/html;charset=utf-8', '.js': 'text/javascript;charset=utf-8',
  '.mjs': 'text/javascript;charset=utf-8', '.css': 'text/css;charset=utf-8',
  '.json': 'application/json;charset=utf-8', '.svg': 'image/svg+xml',
  '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
  '.webp': 'image/webp', '.ico': 'image/x-icon', '.woff2': 'font/woff2',
});
const DOMAIN = Buffer.from('bac-dashboard-assets-v1\0', 'utf8');
const UTF8 = new TextDecoder('utf-8', {fatal: true, ignoreBOM: true});
const comparePaths = (a, b) => Buffer.compare(Buffer.from(a.path, 'utf8'), Buffer.from(b.path, 'utf8'));
const digest = bytes => createHash('sha256').update(bytes).digest('hex');
const refuse = reason => { throw new Error(reason); };

function safePath(path) {
  if (typeof path !== 'string' || !path || path.startsWith('/') || path.includes('\\') || path.includes('\0')
    || Buffer.from(path, 'utf8').toString('utf8') !== path
    || path.split('/').some(part => !part || part === '.' || part === '..')) refuse('Unsafe asset path');
  return path;
}

function mime(path) {
  const extension = extname(path).toLowerCase();
  if (!Object.hasOwn(MIME_TYPES, extension)) refuse('Unsupported asset extension');
  return MIME_TYPES[extension];
}

function lengthBytes(length) {
  const result = Buffer.alloc(8);
  result.writeBigUInt64BE(BigInt(length));
  return result;
}

export function treeSha256(entries) {
  const hash = createHash('sha256').update(DOMAIN);
  for (const entry of [...entries].sort(comparePaths)) {
    const path = Buffer.from(safePath(entry.path), 'utf8');
    const content = Buffer.from(entry.content);
    hash.update(lengthBytes(path.length)).update(path).update(lengthBytes(content.length)).update(content);
  }
  return hash.digest('hex');
}

function readTree(assetRoot) {
  const root = lstatSync(assetRoot);
  if (!root.isDirectory() || root.isSymbolicLink()) refuse('Asset root must be a directory, not a link');
  const entries = [], names = new Set();
  let total = 0;
  function walk(directory, prefix = '') {
    for (const entry of readdirSync(directory, {encoding: 'buffer', withFileTypes: true})) {
      let name;
      try { name = UTF8.decode(entry.name); } catch { refuse('Asset path is not UTF-8'); }
      const path = safePath(prefix ? `${prefix}/${name}` : name);
      const full = join(directory, name);
      const stat = lstatSync(full);
      if (stat.isSymbolicLink()) refuse('Asset links are not allowed');
      if (stat.isDirectory()) { walk(full, path); continue; }
      if (!stat.isFile()) refuse('Asset entry is not a regular file');
      mime(path);
      const folded = path.toLowerCase();
      if (names.has(folded)) refuse('Asset paths collide after lower-casing');
      names.add(folded);
      if (entries.length >= MAX_FILES) refuse('Too many asset files');
      if (stat.size > MAX_FILE_BYTES) refuse('Asset file exceeds size limit');
      if (total + stat.size > MAX_TOTAL_BYTES) refuse('Asset tree exceeds size limit');
      const fd = openSync(full, constants.O_RDONLY | (constants.O_NOFOLLOW || 0));
      let content;
      try {
        const opened = fstatSync(fd);
        if (!opened.isFile() || opened.size !== stat.size || opened.ino !== stat.ino || opened.dev !== stat.dev)
          refuse('Asset changed while reading');
        content = readFileSync(fd);
      } finally { closeSync(fd); }
      if (content.length !== stat.size) refuse('Asset changed while reading');
      total += content.length;
      entries.push({path, content});
    }
  }
  walk(assetRoot);
  return entries.sort(comparePaths);
}

export function buildManifest(assetRoot, {connectorVersion, gitCommit}) {
  if (typeof connectorVersion !== 'string' || !connectorVersion ||
      !(gitCommit === null || typeof gitCommit === 'string' && /^[0-9a-f]{40}$/.test(gitCommit))) {
    refuse('Invalid asset provenance');
  }
  const entries = readTree(assetRoot);
  if (!entries.some(entry => entry.path === 'index.html')) refuse('Missing index.html');
  return {
    schema: 'bac-suite-assets/1', algorithm: 'sha256-tree-v1', treeSha256: treeSha256(entries),
    connectorVersion, gitCommit,
    files: entries.map(({path, content}) => ({path, length: content.length, sha256: digest(content), mime: mime(path)})),
  };
}

export function verifyManifest(assetRoot, manifest) {
  if (!manifest || typeof manifest !== 'object' || Array.isArray(manifest)
      || Object.keys(manifest).sort().join(',') !== 'algorithm,connectorVersion,files,gitCommit,schema,treeSha256'
      || manifest.schema !== 'bac-suite-assets/1' || manifest.algorithm !== 'sha256-tree-v1'
      || !/^[0-9a-f]{64}$/.test(manifest.treeSha256) || !Array.isArray(manifest.files)) {
    refuse('Invalid asset manifest');
  }
  const actual = buildManifest(assetRoot, manifest);
  const matchingFiles = actual.files.length === manifest.files.length && actual.files.every((file, index) => {
    const listed = manifest.files[index];
    return listed && typeof listed === 'object' && !Array.isArray(listed)
      && Object.keys(listed).sort().join(',') === 'length,mime,path,sha256'
      && file.path === listed.path && file.length === listed.length && file.sha256 === listed.sha256 && file.mime === listed.mime;
  });
  if (!matchingFiles || actual.treeSha256 !== manifest.treeSha256)
    refuse('Asset manifest does not match the tree');
  return true;
}
