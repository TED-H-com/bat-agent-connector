import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';
import {parse} from 'acorn';
import {retiredRead, readThroughResume} from '../src/state/retired-read.ts';
import {clientActionGate} from '../src/central-service-status.js';
import {ApiError, clientActionError} from '../src/error-presentation.js';

// Exercise the real shared owner, including its write and connection guards.
const app = readFileSync(new URL('../src/app.js', import.meta.url), 'utf8');
const declaration: any = parse(app, {ecmaVersion: 'latest', sourceType: 'module'}).body
  .find((node: any) => node.type === 'FunctionDeclaration' && node.id.name === 'apiRequest');
function initialRequest() {
  const calls: any[] = [];
  const context: any = {state: {epoch: 1, token: 'fixture', viewReady: false, online: true, pollHealth: 'connected'},
    foregroundReads: new AbortController(), ApiError, clientActionGate, clientActionError, t: (key: string) => key,
    retiredRead, readThroughResume,
    connectorRequest: (method: string, path: string, _body: unknown, _key: string, _token: string, signal: AbortSignal) =>
      new Promise((resolve, reject) => calls.push({method, path, signal, resolve, reject}))};
  const request = runInNewContext(`(${app.slice(declaration.start, declaration.end)})`, context);
  const resume = () => {const previous = context.foregroundReads; context.foregroundReads = new AbortController(); previous.abort();};
  return {context, calls, request, resume};
}

test('a first source read renews before view readiness without opening the write gate', async () => {
  const f = initialRequest(), pending = f.request('GET', '/managed/setup');
  assert.equal(f.calls.length, 1);
  assert.equal(f.calls[0].signal, f.context.foregroundReads.signal);
  await assert.rejects(f.request('POST', '/operations', {}), {code: 'CLIENT_ACTION_VIEW_NOT_READY'});
  assert.equal(f.calls.length, 1);
  f.resume();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(f.calls.length, 2);
  f.calls[1].resolve({status: 200, data: 'fresh source'});
  assert.equal(await pending, 'fresh source');
  f.calls[0].resolve({status: 200, data: 'old source'});
  assert.equal(f.context.state.viewReady, false);
});

test('a loading read ends at a changed connection instead of renewing under another one', async () => {
  const f = initialRequest(), pending = f.request('GET', '/managed/setup');
  assert.equal(f.calls[0].signal, f.context.foregroundReads.signal);
  f.context.state.epoch++;
  f.resume();
  await assert.rejects(pending, {code: 'CONNECTION_CHANGED'});
  assert.equal(f.calls.length, 1);
  f.calls[0].resolve({status: 200, data: 'late source'});
});

test('a first source transport failure is reported once and never renewed', async () => {
  const f = initialRequest(), pending = f.request('GET', '/managed/setup');
  f.calls[0].reject(new Error('fixture network failure'));
  await assert.rejects(pending, {code: 'NETWORK_ERROR'});
  f.resume();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(f.calls.length, 1);
});

test('retiring a hung native read settles without its response and consumes late failure', async () => {
  const controller = new AbortController();
  let fail!: (error: Error) => void;
  const pending = new Promise<never>((_, reject) => {fail = reject;});
  const retired = retiredRead(pending, controller.signal);
  controller.abort();
  await assert.rejects(retired, {name: 'AbortError', code: 'FOREGROUND_READ_RETIRED'});
  fail(new Error('late native failure'));
});

test('a retired response cannot become current after it arrives', async () => {
  const controller = new AbortController();
  let finish!: (value: string) => void;
  const retired = retiredRead(new Promise<string>(resolve => {finish = resolve;}), controller.signal);
  controller.abort(); finish('old observation');
  await assert.rejects(retired, {name: 'AbortError', code: 'FOREGROUND_READ_RETIRED'});
});

test('a completed read keeps its result; a previously retired read never publishes it', async () => {
  const controller = new AbortController();
  assert.equal(await retiredRead(Promise.resolve('fresh observation'), controller.signal), 'fresh observation');
  controller.abort();
  await assert.rejects(retiredRead(Promise.resolve('old observation'), controller.signal), {name: 'AbortError', code: 'FOREGROUND_READ_RETIRED'});
});

test('a source without an event subscription renews its read and ignores its late original response', async () => {
  let controller = new AbortController(), reads = 0, old!: (value: string) => void;
  const source = readThroughResume(async () => {
    if (++reads === 1) return new Promise<string>(resolve => {old = resolve;});
    return 'post-resume observation';
  }, () => controller.signal, () => {});
  const previous = controller; controller = new AbortController(); previous.abort();
  assert.equal(await source, 'post-resume observation');
  old('pre-resume observation');
  assert.equal(reads, 2);
});

test('renewal neither retries failed transport nor crosses an authority change', async () => {
  let controller = new AbortController(), allowed = true, reads = 0;
  await assert.rejects(readThroughResume(async () => {reads++; throw Error('network failed');},
    () => controller.signal, () => {}), /network failed/);
  assert.equal(reads, 1);
  const source = readThroughResume(() => {reads++; return new Promise(() => {});},
    () => controller.signal, () => {if (!allowed) throw Error('connection changed');});
  allowed = false;
  const previous = controller; controller = new AbortController(); previous.abort();
  await assert.rejects(source, /connection changed/);
  assert.equal(reads, 2);
});
