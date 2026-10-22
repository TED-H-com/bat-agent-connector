import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {setLocale, t} from '../src/i18n.js';
import {browserCredentialRefusals} from '../src/credential-refusals.js';
import {connectorRequest, connectorUploadArtifact} from '../src/transport/index.ts';
import {readArtifactContent} from '../src/transport/artifact-content.ts';

const golden = JSON.parse(readFileSync(new URL('../../tests/fixtures/browser-credential-responses.json', import.meta.url), 'utf8'));
const reference = {artifact_id: 'art_' + 'a'.repeat(32), revision: 1, digest: 'b'.repeat(64)};
Object.assign(globalThis, {location: {origin: 'http://localhost'}});
for (const locale of ['en', 'zh-TW']) for (const path of ['request', 'upload', 'content']) {
  test(`${path} preserves the final request frame and reports credential refusal in ${locale}`, async () => {
    setLocale(locale);
    const token = `fixture-${locale}-${path}`;
    browserCredentialRefusals.prepare(token);
    let requests = 0;
    const original = globalThis.fetch;
    globalThis.fetch = async () => {
      requests++;
      return new Response(JSON.stringify(golden['401'].data), {status: 401});
    };
    const call = () => path === 'request' ? connectorRequest('POST', '/operations', {}, 'key', token)
      : path === 'upload' ? connectorUploadArtifact('op_' + 'a'.repeat(32), new ArrayBuffer(0), token)
      : readArtifactContent(reference, 0, token, new AbortController().signal);
    try {
      const pending = call();
      assert.equal(requests, 1, 'fetch must run in the caller final-guard frame');
      if (path === 'content') await assert.rejects(pending, {message: t('global_token_rejected'), status: 401});
      else assert.equal((await pending as {status: number}).status, 401);
      await assert.rejects(call(), {message: t('global_token_rejected'), credentialRefused: true});
      assert.equal(requests, 1);
    } finally {globalThis.fetch = original; browserCredentialRefusals.forget(token);}
  });
}

for (const path of ['request', 'upload', 'content']) test(`ordinary ${path} permission denial leaves the bearer usable`, async () => {
  const token = 'permission-' + path; browserCredentialRefusals.prepare(token);
  const original = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify(golden.action_forbidden.data), {status: 403});
  try {
    if (path === 'request') assert.equal((await connectorRequest('GET', '/projects', undefined, undefined, token)).status, 403);
    else if (path === 'upload') assert.equal((await connectorUploadArtifact('op_' + 'a'.repeat(32), new ArrayBuffer(0), token)).status, 403);
    else await assert.rejects(readArtifactContent(reference, 0, token, new AbortController().signal), {status: 403});
    assert.equal(browserCredentialRefusals.read(token), null);
  } finally {globalThis.fetch = original; browserCredentialRefusals.forget(token);}
});
