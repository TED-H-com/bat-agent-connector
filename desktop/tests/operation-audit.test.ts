import assert from 'node:assert/strict';
import {test} from 'node:test';
import {operationAuditWarnings, operationAuditNotice} from '../src/operation-audit.js';
import {operationAuditStrings} from '../src/operation-audit-strings.js';

const warning = (outcome: string) => ({state: 'failed', code: 'AUDIT_RESULT_WRITE_FAILED', effect_outcome: outcome});
for (const status of ['accepted', 'running', 'waiting_external', 'uncertain', 'needs_attention', 'succeeded', 'failed', 'cancelled']) {
  test(`audit warnings survive ${status} independently of the result`, () => {
    const op = {status, external_refs: {audit_results: Object.fromEntries(
      ['accepted', 'error', 'unknown'].map((outcome, i) => [String(i).repeat(64), warning(outcome)]))}};
    assert.deepEqual(operationAuditWarnings(op).map(w => w.outcome), ['accepted', 'error', 'unknown']);
    for (const locale of ['en', 'zh-TW']) {
      const strings = operationAuditStrings[locale];
      const tree = operationAuditNotice(op, {h: (...args: any[]) => args, t: (key: string) => strings[key]});
      const text = JSON.stringify(tree);
      for (const outcome of ['accepted', 'error', 'unknown']) assert.ok(text.includes(strings['audit_result_' + outcome]));
      assert.ok(text.includes(strings.audit_result_repair));
    }
  });
}
test('no warning or malformed warning does not invent a receipt', () => {
  assert.equal(operationAuditNotice({}, {}), null);
  assert.deepEqual(operationAuditWarnings({external_refs: {audit_results: {
    private: warning('accepted'), ['a'.repeat(64)]: warning('private'),
  }}}), []);
  assert.deepEqual(Object.keys(operationAuditStrings.en).sort(), Object.keys(operationAuditStrings['zh-TW']).sort());
});
