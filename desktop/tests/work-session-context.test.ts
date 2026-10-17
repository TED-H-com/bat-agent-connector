import assert from 'node:assert/strict';
import {test} from 'node:test';
import {sessionHref, sessionOrigin, workSessionContext} from '../src/session-navigation.js';

const selection = {workspaceId: 'ws_' + 'a'.repeat(32), workItemId: 'wi_' + 'b'.repeat(20), attemptId: 'att_' + 'c'.repeat(32)};
test('a return link preserves a complete explicit Work selection', () => {
  const link = sessionHref({host: 'a host', sessionId: 'session/one', from: 'board', ...selection});
  assert.equal(sessionOrigin(link), link);
  assert.deepEqual(workSessionContext(new URLSearchParams(link.split('?')[1])), selection);
  assert.equal(sessionOrigin('#/session/demo/one?from=board'), '#/session/demo/one?from=board');
});
test('incomplete, duplicate and malformed context never becomes Work authority', () => {
  const query = new URLSearchParams({workspace: selection.workspaceId, work: selection.workItemId, attempt: selection.attemptId});
  for (const key of ['workspace', 'work', 'attempt']) {
    for (const kind of ['missing', 'duplicate', 'malformed']) {
      const bad = new URLSearchParams(query);
      if (kind === 'missing') bad.delete(key);
      if (kind === 'duplicate') bad.append(key, query.get(key)!);
      if (kind === 'malformed') bad.set(key, '../other');
      assert.equal(workSessionContext(bad), null);
      assert.equal(sessionOrigin('#/session/demo/one?' + bad), '');
    }
  }
});
