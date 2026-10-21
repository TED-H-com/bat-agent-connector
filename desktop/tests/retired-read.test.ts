import {test} from 'node:test';
import assert from 'node:assert/strict';
import {retiredRead, readThroughResume} from '../src/state/retired-read.ts';

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
