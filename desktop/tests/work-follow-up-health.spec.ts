import {test, expect} from '@playwright/test';
import {readFileSync} from 'node:fs';
import {followUpFixture, openWork, WS, WI, ATTEMPT} from './work-next-turn-fixture.ts';

test.describe.configure({mode: 'parallel'});

// Generated and pinned by test_pm_work_follow_up_golden.py using the real result
// producer. *_boundary cases explicitly inject producer facts (see that test).
const golden = JSON.parse(readFileSync(new URL('../../tests/fixtures/work-follow-up-decisions.json', import.meta.url), 'utf8'));
// Forward-compatibility mutation of a real producer row, never a new wire shape.
const future = structuredClone(golden.cases.missing_scope);
future.blocked_actions.find((row: any) => row.action === 'session.send').reason = 'future_reason_example';
const cases = {...golden.cases, future_reason: future};
const copy = {
  en: {
    codex_boundary: ['A next step is not available for this Codex session.', 'Review the current result. Ask the workspace owner about a supported session.'],
    different_controller: ["You are not this work's controller.", 'Ask the work’s controller to send it.'],
    original_actor_without_write_access: ["You are not this work's controller.", 'Ask the work’s controller to send it.'],
    not_writer_member_boundary: ['Your workspace membership does not allow writing.', 'Ask a workspace owner for write access.'],
    missing_scope: ['This credential does not have operate scope.', 'Reconnect with a credential that has operate scope.'],
    no_send_row: ['This attempt does not currently offer a next step.', 'Refresh, or ask the workspace owner.'],
    future_reason: ['The server blocked the next step.', 'Refresh, or ask the workspace owner.'],
  },
  'zh-TW': {
    codex_boundary: ['這個 Codex 工作階段無法排入下一步。', '請查看本輪結果，並向工作區擁有者詢問支援的工作階段。'],
    different_controller: ['你不是這項工作的控制者。', '請這項工作的控制者代為送出。'],
    original_actor_without_write_access: ['你不是這項工作的控制者。', '請這項工作的控制者代為送出。'],
    not_writer_member_boundary: ['你在這個工作區的成員權限不允許寫入。', '請向工作區擁有者申請寫入權限。'],
    missing_scope: ['這組憑證沒有操作權限。', '請使用具備操作權限的憑證重新連線。'],
    no_send_row: ['這次嘗試目前沒有提供下一步。', '請重新整理，或詢問工作區擁有者。'],
    future_reason: ['伺服器已阻擋下一步。', '請重新整理，或詢問工作區擁有者。'],
  },
};

for (const native of [false, true]) for (const lang of ['en', 'zh-TW'] as const) for (const width of [390, 1440]) {
  test.describe(`${native ? 'IPC' : 'HTTP'} ${lang} ${width}`, () => {
    test.use({locale: lang === 'en' ? 'en-US' : lang, viewport: {width, height: 900}});
    for (const [name, result] of Object.entries(cases)) {
      test(`${name}: explains admission without changing receipts or drafts`, async ({page}) => {
        const f = followUpFixture(lang, {native});
        const requests: any[] = [];
        const dispatch = f.dispatch;
        let response = golden.cases.allowed;
        f.dispatch = async input => {
          requests.push(structuredClone(input));
          const path = new URL(input.path, 'http://fixture').pathname;
          if (input.method === 'GET' && path === `/pm/workspaces/${WS}/work-attempts/${ATTEMPT}/result`)
            return {status: 200, data: structuredClone(response)};
          if (input.method === 'GET' && path === `/pm/workspaces/${WS}/work/${WI}/inputs`)
            return {status: 200, data: structuredClone(golden.inputs)};
          return dispatch(input);
        };
        await openWork(page, f, lang);
        const field = page.locator('[data-follow-up-input]');
        const draft = '  Third unsent draft 第三句\nKeep these bytes.  ';
        await field.fill(draft);
        const saved = await page.evaluate(() => {
          const key = Object.keys(localStorage).find(key => key.startsWith('batc.pm.') && key.includes('.session.send.'))!;
          return {key, raw: localStorage.getItem(key)};
        });
        expect(saved.key).toContain(`.session.send.${JSON.stringify({workspace_id: WS, work_item_id: WI})}`);
        expect(JSON.parse(saved.raw!).values.text).toBe(draft);
        const staleForm = await field.locator('..').locator('..').elementHandle();
        expect(await staleForm!.evaluate(node => node.tagName)).toBe('FORM');
        const before = requests.length;
        response = result;
        f.pushEvent();
        await expect.poll(() => requests.slice(before).filter(row => row.method === 'GET' && row.path.includes('/inputs')).length).toBeGreaterThan(0);
        const box = page.locator('[data-work-section="next-turn"]');
        const expected = copy[lang][name as keyof typeof copy.en];
        if (expected) {
          for (const text of expected) await expect(box.locator('[data-follow-up-note]')).toContainText(text);
          await expect(field).toHaveCount(0);
          await expect(box.locator('[data-follow-up-action]')).toHaveCount(0);
          // A retired form cannot send after the authoritative result blocks it.
          await staleForm!.evaluate(node => node.dispatchEvent(new Event('submit', {bubbles: true, cancelable: true})));
        } else {
          await expect(field).toHaveValue(draft);
        }
        if (name === 'future_reason') await expect(box.locator('[data-follow-up-note] code')).toHaveText('future_reason_example');
        if (name === 'not_writer_member_boundary') {
          await expect(box.locator('[data-follow-up-pill]')).toContainText(lang === 'en' ? 'Workspace read-only' : '工作區唯讀');
          await expect(box).not.toContainText(lang === 'en' ? 'API read-only' : 'API 唯讀');
        }
        if (name === 'missing_scope') await expect(box.locator('[data-follow-up-pill]')).toContainText(lang === 'en' ? 'API read-only' : 'API 唯讀');
        const original = golden.inputs.items[0];
        const receipt = box.locator(`[data-follow-up-receipt="${original.operation_id}"]`);
        await expect(receipt).toHaveCount(1);
        await expect(receipt).toContainText(original.labels.state[lang]);
        await expect(receipt).toContainText(original.labels.detail[lang]);
        await expect(receipt.locator('a').first()).toHaveAttribute('href', `#/op/${original.operation_id}`);
        expect(await page.evaluate(key => localStorage.getItem(key), saved.key)).toBe(saved.raw);
        expect(requests.filter(row => row.method === 'POST' && new URL(row.path, 'http://fixture').pathname === '/operations')).toEqual([]);
        expect(f.follow.errors).toEqual([]);
      });
    }
  });
}
