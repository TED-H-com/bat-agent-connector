import {test, expect} from '@playwright/test';
import {conversationFixture, mountConversation} from './conversation-fixture.ts';
for (const native of [false, true]) test(`message-local metadata and visible exceptional tool results (${native ? 'IPC' : 'Web'})`, async ({page}) => {
  const f = conversationFixture(), errors: string[] = []; const reads: string[] = [];
  f.data.messages = [
    {id: 'old', role: 'assistant', ts: '2026-10-09T12:00:00Z', text: 'No historic model is known', status: 'failed'},
    {id: 'new', role: 'assistant', ts: '2026-10-09T12:01:00Z', text: 'Exact original message', model: 'message-model', agent: 'codex', duration_ms: 2500},
    ...['completed', 'error', 'running', 'unknown'].map((status, i) => ({id: 'tool-'+status, role: 'tool', tool: 'Bash', status,
      ts: '2026-10-09T12:02:00Z', text: `Tool ${status} original result`, duration_ms: i ? undefined : 1500}))
  ] as any;
  const dispatch = async (input: any) => {reads.push(input.path); return f.dispatch(input);};
  page.on('pageerror', error => errors.push(error.message)); await mountConversation(page, native, dispatch);
  await page.goto('/dashboard/#/session/demo/session-1');
  const row = (id: string) => page.locator(`[data-message-id="${id}"]`);
  await expect(row('new').locator('.message-name')).toHaveText('codex');
  await expect(row('new').locator('.message-chip')).toHaveText('message-model');
  await expect(row('new').locator('.message-chip')).toHaveAttribute('title', 'message-model');
  await expect(row('new')).not.toContainText('Message model:'); await expect(row('new')).not.toContainText('Message agent:');
  await expect(row('new')).toContainText('2.5 s'); await expect(row('old')).not.toContainText('Message model:');
  await expect(row('old').locator('.message-chip')).toBeHidden();
  await expect(row('old').locator('[data-status="failed"]')).toHaveText('Failed');
  await expect(row('tool-completed').locator('[data-status="done"]')).toHaveText('Done');
  await expect(row('tool-completed')).not.toContainText('Status:');
  await expect(row('tool-completed').locator('details')).not.toHaveAttribute('open');
  for (const [status, label, key] of [['error', 'Failed', 'failed'], ['running', 'Running', 'running'], ['unknown', 'Unknown', 'unknown']] as const) {
    await expect(row('tool-'+status).locator('[data-status="'+key+'"]')).toHaveText(label);
    await expect(row('tool-'+status).locator('details')).toHaveAttribute('open', '');
    await expect(row('tool-'+status).locator('details > div').getByText(`Tool ${status} original result`, {exact: true})).toBeVisible();
  }
  await row('tool-completed').locator('summary').click();
  f.data.cursor++; await expect.poll(() => f.data.reads).toBeGreaterThan(1);
  await expect(row('tool-completed').locator('details')).toHaveAttribute('open', '');
  expect(reads.some(path => path.includes('/messages?') && path.includes('include_tools=true'))).toBe(true);
  expect(f.data.writes).toEqual([]); expect(errors).toEqual([]);
});
