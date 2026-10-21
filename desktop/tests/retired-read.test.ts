import {test} from 'node:test';
import assert from 'node:assert/strict';
import {retiredRead} from '../src/state/retired-read.ts';

test('retiring a hung native read settles without its response and consumes late failure', async () => {
  const controller = new AbortController();
  let fail!: (error: Error) => void;
  const pending = new Promise<never>((_, reject) => {fail = reject;});
  const retired = retiredRead(pending, controller.signal);
  controller.abort();
  await assert.rejects(retired, {name: 'AbortError'});
  fail(new Error('late native failure'));
});

test('a retired response cannot become current after it arrives', async () => {
  const controller = new AbortController();
  let finish!: (value: string) => void;
  const retired = retiredRead(new Promise<string>(resolve => {finish = resolve;}), controller.signal);
  controller.abort(); finish('old observation');
  await assert.rejects(retired, {name: 'AbortError'});
});

test('a completed read keeps its result; a previously retired read never publishes it', async () => {
  const controller = new AbortController();
  assert.equal(await retiredRead(Promise.resolve('fresh observation'), controller.signal), 'fresh observation');
  controller.abort();
  await assert.rejects(retiredRead(Promise.resolve('old observation'), controller.signal), {name: 'AbortError'});
});
