import {test, expect} from '@playwright/test';
import {conversationFixture, mountConversation} from './conversation-fixture.ts';

for (const native of [false, true]) {
  test(`composer shortcut keeps Shift and IME safe and submits through the existing button (${native ? 'IPC' : 'Web'})`, async ({page}) => {
    const fixture = conversationFixture();
    await mountConversation(page, native, async input => {
      if (input.method !== 'GET') {
        fixture.data.writes.push(input);
        return {status: 200, data: {operation: {operation_id: 'op_' + 'a'.repeat(32), status: 'succeeded'}}};
      }
      return fixture.dispatch(input);
    });
    await page.goto('/dashboard/#/session/demo/session-1');
    const input = page.locator('.workspace-composer textarea').first();
    await input.fill('First draft'); await input.press('Enter');
    expect(fixture.data.writes).toHaveLength(0);
    await input.press('Control+Enter');
    await expect.poll(() => fixture.data.writes.length).toBe(1);
    expect(fixture.data.writes[0].body.action).toBe('session.send');
    await expect(input).toHaveValue('');
    await page.getByRole('button', {name: 'More actions', exact: true}).click();
    await page.getByRole('combobox', {name: 'Send shortcut', exact: true}).selectOption('enter');
    await input.fill('繁體中文');
    await input.evaluate(el => {
      el.dispatchEvent(new CompositionEvent('compositionstart', {bubbles: true}));
      el.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', isComposing: true, bubbles: true, cancelable: true}));
      el.dispatchEvent(new CompositionEvent('compositionend', {bubbles: true}));
      el.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true, cancelable: true}));
    });
    expect(fixture.data.writes).toHaveLength(1);
    await input.press('Shift+Enter'); expect(fixture.data.writes).toHaveLength(1);
    await expect(input).toHaveValue('繁體中文\n');
    await page.getByRole('combobox', {name: 'Send shortcut', exact: true}).selectOption('button');
    await input.press('Control+Enter'); expect(fixture.data.writes).toHaveLength(1);
    await page.reload();
    await page.getByRole('button', {name: 'More actions', exact: true}).click();
    await expect(page.getByRole('combobox', {name: 'Send shortcut', exact: true})).toHaveValue('button');
    await expect(input).toHaveValue('繁體中文\n');
  });
}
