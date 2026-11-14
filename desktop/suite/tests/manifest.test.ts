// @ts-nocheck
import assert from 'node:assert/strict';
import {mkdtempSync, mkdirSync, writeFileSync, symlinkSync, rmSync, truncateSync, readFileSync, lstatSync} from 'node:fs';
import {join} from 'node:path';
import {test} from 'node:test';
import {testScratchRoot} from '../../tests/test-scratch.mjs';
import {assetEntryKind, buildManifest, verifyManifest, treeSha256, MAX_FILE_BYTES, MAX_FILES, MAX_TOTAL_BYTES, MIME_TYPES} from '../manifest.mjs';

const provenance = {connectorVersion: '1.2.3', gitCommit: 'a'.repeat(40)};
function withTree(run) {
  const root = mkdtempSync(join(testScratchRoot(), 'suite-assets-'));
  try { writeFileSync(join(root, 'index.html'), 'ok'); return run(root); }
  finally { rmSync(root, {recursive: true, force: true}); }
}
function add(root, path, content = '') {
  const target = join(root, path);
  mkdirSync(join(target, '..'), {recursive: true});
  writeFileSync(target, content);
}

test('tree hash matches the independent cross-language vectors', () => {
  const vectors = JSON.parse(readFileSync(new URL('./fixtures/tree-hash-vectors.json', import.meta.url), 'utf8'));
  for (const vector of vectors) {
    assert.equal(treeSha256(vector.entries.map(({path, content}) => ({path, content: Buffer.from(content)}))), vector.sha256, vector.name);
  }
});

test('manifest records bytes, MIME, sorted paths and exact provenance', () => withTree(root => {
  add(root, 'z.js', 'z'); add(root, 'a.css', 'a'); add(root, '資料/頁.html', 'text');
  const manifest = buildManifest(root, provenance);
  assert.deepEqual(Object.keys(manifest), ['schema', 'algorithm', 'treeSha256', 'connectorVersion', 'gitCommit', 'files']);
  assert.deepEqual(manifest.files.map(file => file.path), ['a.css', 'index.html', 'z.js', '資料/頁.html']);
  assert.equal(manifest.files[1].length, 2);
  assert.equal(manifest.files[1].mime, MIME_TYPES['.html']);
  assert.equal(verifyManifest(root, manifest), true);
  const reordered = {...manifest, files: manifest.files.map(({path, length, sha256, mime}) => ({mime, sha256, length, path}))};
  assert.equal(verifyManifest(root, reordered), true);
}));

test('every permitted extension has its specified MIME', () => withTree(root => {
  for (const extension of Object.keys(MIME_TYPES)) add(root, `asset${extension}`);
  const manifest = buildManifest(root, provenance);
  for (const [extension, expected] of Object.entries({
    '.html': 'text/html;charset=utf-8', '.js': 'text/javascript;charset=utf-8',
    '.mjs': 'text/javascript;charset=utf-8', '.css': 'text/css;charset=utf-8',
    '.json': 'application/json;charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png',
    '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp',
    '.ico': 'image/x-icon', '.woff2': 'font/woff2',
  })) assert.equal(manifest.files.find(file => file.path === `asset${extension}`).mime, expected);
}));

test('verification rejects malformed, missing, added and changed assets', () => withTree(root => {
  const manifest = buildManifest(root, provenance);
  for (const modified of [
    {...manifest, extra: true}, {...manifest, schema: 'other'}, {...manifest, gitCommit: 'bad'},
    {...manifest, files: []}, {...manifest, files: [{...manifest.files[0], length: 1}]},
    {...manifest, files: [{...manifest.files[0], sha256: '0'.repeat(64)}]},
    {...manifest, files: [{...manifest.files[0], mime: 'text/plain'}]},
    {...manifest, files: [{...manifest.files[0], path: '../index.html'}]},
    {...manifest, treeSha256: '0'.repeat(64)},
  ]) assert.throws(() => verifyManifest(root, modified));
  add(root, 'extra.js'); assert.throws(() => verifyManifest(root, manifest));
  rmSync(join(root, 'extra.js')); writeFileSync(join(root, 'index.html'), 'NO');
  assert.throws(() => verifyManifest(root, manifest));
  rmSync(join(root, 'index.html')); assert.throws(() => verifyManifest(root, manifest));
  add(root, 'index.html', 'ok'); add(root, 'app.js', 'console.log(1);');
  const multi = buildManifest(root, provenance);
  assert.throws(() => verifyManifest(root, {...multi, files: [...multi.files].reverse()}), /does not match the tree/);
  rmSync(join(root, 'app.js'));
  assert.throws(() => verifyManifest(root, multi), /does not match the tree/);
}));

test('links, invalid roots, bad names and unsupported extensions are refused', () => {
  for (const prepare of [
    root => symlinkSync('index.html', join(root, 'link.html')),
    root => symlinkSync('.', join(root, 'directory'), 'dir'),
    root => add(root, 'bad.map'), root => add(root, 'bad.txt'), root => add(root, 'back\\slash.js'),
    root => {add(root, 'Case.js'); add(root, 'case.js');},
    root => {add(root, 'Ä.js'); add(root, 'ä.js');},
    root => {add(root, 'Directory/one.js'); add(root, 'directory/two.js');},
  ]) withTree(root => {
    const manifest = buildManifest(root, provenance);
    prepare(root);
    assert.throws(() => buildManifest(root, provenance));
    assert.throws(() => verifyManifest(root, manifest));
  });
  withTree(root => {
    const manifest = buildManifest(root, provenance), link = `${root}-link`;
    try {
      symlinkSync(root, link, 'dir');
      assert.throws(() => buildManifest(link, provenance), /Asset root/);
      assert.throws(() => verifyManifest(link, manifest), /Asset root/);
      assert.throws(() => buildManifest(join(root, 'index.html'), provenance), /Asset root/);
    } finally { rmSync(link, {force: true}); }
  });
  for (const path of ['/index.html', '../index.html', 'a/../index.html', 'a\\b.js', 'a\0b.js', '\ud800.js']) {
    assert.throws(() => treeSha256([{path, content: Buffer.alloc(0)}]));
  }
  if (process.platform !== 'win32') withTree(root => {
    const manifest = buildManifest(root, provenance);
    writeFileSync(Buffer.concat([Buffer.from(root + '/'), Buffer.from([0xff, 0x2e, 0x6a, 0x73])]), '');
    assert.throws(() => buildManifest(root, provenance), /UTF-8/);
    assert.throws(() => verifyManifest(root, manifest), /UTF-8/);
  });
});

test('asset entry classifier rejects character devices', {skip: process.platform === 'win32'}, () => {
  const device = lstatSync('/dev/null');
  assert.equal(device.isCharacterDevice(), true);
  assert.throws(() => assetEntryKind(device), /not a regular file/);
});

test('file limit accepts the boundary and refuses the next byte', () => withTree(root => {
  const file = join(root, 'large.js'); writeFileSync(file, ''); truncateSync(file, MAX_FILE_BYTES - 1);
  assert.doesNotThrow(() => buildManifest(root, provenance));
  truncateSync(file, MAX_FILE_BYTES);
  const manifest = buildManifest(root, provenance);
  assert.equal(verifyManifest(root, manifest), true);
  truncateSync(file, MAX_FILE_BYTES + 1); assert.throws(() => buildManifest(root, provenance), /size limit/);
  assert.throws(() => verifyManifest(root, manifest), /size limit/);
}));

test('file count limit accepts the boundary and refuses the next file', () => withTree(root => {
  for (let i = 1; i < MAX_FILES - 1; i++) add(root, `${i}.js`);
  assert.equal(buildManifest(root, provenance).files.length, MAX_FILES - 1);
  add(root, 'boundary.js');
  const manifest = buildManifest(root, provenance);
  assert.equal(manifest.files.length, MAX_FILES);
  assert.equal(verifyManifest(root, manifest), true);
  add(root, 'one-more.js'); assert.throws(() => buildManifest(root, provenance), /Too many/);
  assert.throws(() => verifyManifest(root, manifest), /Too many/);
}));

test('total byte limit accepts the boundary and refuses the next byte', () => withTree(root => {
  rmSync(join(root, 'index.html')); add(root, 'index.html');
  for (let i = 0; i < MAX_TOTAL_BYTES / MAX_FILE_BYTES; i++) {
    const file = join(root, `${i}.js`); writeFileSync(file, ''); truncateSync(file, MAX_FILE_BYTES);
  }
  const last = join(root, `${MAX_TOTAL_BYTES / MAX_FILE_BYTES - 1}.js`);
  truncateSync(last, MAX_FILE_BYTES - 1);
  assert.doesNotThrow(() => buildManifest(root, provenance));
  truncateSync(last, MAX_FILE_BYTES);
  const manifest = buildManifest(root, provenance);
  assert.equal(verifyManifest(root, manifest), true);
  writeFileSync(join(root, 'index.html'), 'x'); assert.throws(() => buildManifest(root, provenance), /tree exceeds/);
  assert.throws(() => verifyManifest(root, manifest), /tree exceeds/);
}));
