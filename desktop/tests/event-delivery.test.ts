import {test} from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {eventDelivery} from './event-delivery.mjs';

function fixture() {
  const page = new EventEmitter() as any;
  const frame = {};
  page.mainFrame = () => frame;
  return {page, frame, gate:eventDelivery(page)};
}

test('a quiet point holds the next poll without fabricating its response', async () => {
  const {gate} = fixture(), response = {}, calls: string[] = [];
  const quiet = gate.pause().then(() => {calls.push('quiet');});
  const poll = gate.through(() => {calls.push('forward'); return response;});
  await quiet;
  assert.deepEqual(calls, ['quiet']);
  gate.resume();
  assert.equal(await poll, response);
  assert.deepEqual(calls, ['quiet','forward']);
});

test('navigation drains an old held poll and waits for the current view', async () => {
  const {page, frame, gate} = fixture();
  const firstQuiet = gate.pause();
  const oldPoll = gate.through(() => 'old');
  await firstQuiet;
  page.emit('framenavigated', frame);
  let currentReady = false;
  const nextQuiet = gate.pause().then(() => {currentReady=true;});
  assert.equal(await oldPoll, 'old');
  assert.equal(currentReady, false);
  const currentPoll = gate.through(() => 'current');
  await nextQuiet;
  gate.resume();
  assert.equal(await currentPoll, 'current');
});

test('closing the page releases a held poll', async () => {
  const {page, gate} = fixture();
  const quiet = gate.pause(), poll = gate.through(() => 'closed');
  await quiet; page.emit('close');
  assert.equal(await poll, 'closed');
});


test('a write runs once at the quiet point and releases only after its request', async () => {
  const {gate} = fixture(), calls: string[] = [], input = {method:'POST',path:'/operations'};
  const writing = gate.post(async () => {
    calls.push('click');
    await gate.forward(input, received => {assert.equal(received,input);calls.push('POST');});
  });
  assert.deepEqual(calls, []);
  const poll = gate.forward({method:'GET',path:'/events?after=0'}, () => {calls.push('poll');});
  await writing; await poll;
  assert.deepEqual(calls, ['click','POST','poll']);
});


test('resuming releases an obsolete poll as well as its renewed read', async () => {
  const {gate} = fixture(), calls: number[] = [];
  const quiet = gate.pause();
  const old = gate.through(() => {calls.push(1);});
  await quiet;
  const renewed = gate.through(() => {calls.push(2);});
  gate.resume(); await Promise.all([old,renewed]);
  assert.deepEqual(calls,[1,2]);
});

test('a failed refresh can replay before the settled write boundary', async () => {
  const {gate} = fixture();
  let ready = false;
  const quiet = gate.pause(() => ready);
  assert.equal(await gate.through(() => 'replay'), 'replay');
  ready = true;
  const next = gate.through(() => 'held');
  await quiet;
  gate.resume();
  assert.equal(await next, 'held');
});
