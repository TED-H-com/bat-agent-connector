import {test, expect} from '@playwright/test';
import {conversationFixture, mountConversation} from './conversation-fixture.ts';
import {STRINGS} from '../src/i18n.js';
import {operationAuditWarnings} from '../src/operation-audit.js';

const id = 'op_' + '8'.repeat(32);
for (const locale of ['en', 'zh-TW']) for (const native of [false, true]) {
  test(`operation audit warning ${locale} ${native ? 'IPC' : 'HTTP'}`, async ({browser}) => {
    const context = await browser.newContext({locale: locale === 'en' ? 'en-US' : locale});
    const page = await context.newPage(), fixture = conversationFixture(), writes: any[] = [];
    let status = 'succeeded';
    const external_refs = {audit_results: Object.fromEntries(['accepted', 'error', 'unknown']
      .map((outcome, i) => [String(i).repeat(64), {state: 'failed', code: 'AUDIT_RESULT_WRITE_FAILED',
        phase: 'result', failure_stage: 'open', effect_outcome: outcome}]))};
    try {
      await mountConversation(page, native, async input => {
        if (input.method === 'POST') writes.push(input);
        if (input.method === 'GET' && input.path === `/operations/${id}`) return {status: 200, data: {operation: {
          operation_id: id, action: 'session.send', status, error_code: null, status_reason: null,
          actor: 'reader', entry: 'http', created_at: 1793664000, idempotency_key: 'original-request',
          target: {host: 'demo', session_id: 'session-a'}, external_refs, result: null, steps: [],
        }}};
        return fixture.dispatch(input);
      });
      for (status of ['succeeded', 'failed', 'uncertain', 'needs_attention', 'cancelled', 'running']) {
        await page.goto(`/dashboard/#/op/${id}`);
        const notice = page.locator('[data-operation-audit]');
        await expect(notice).toBeVisible();
        for (const warning of operationAuditWarnings({external_refs}))
          await expect(notice.locator(`[data-audit-outcome="${warning.outcome}"]`))
            .toHaveText(STRINGS[locale]['audit_result_' + warning.outcome]);
        await expect(notice).toContainText(STRINGS[locale].audit_result_repair);
        await expect(notice.locator('button')).toHaveCount(0);
      }
      expect(writes).toEqual([]);
    } finally { await context.close(); }
  });
}
