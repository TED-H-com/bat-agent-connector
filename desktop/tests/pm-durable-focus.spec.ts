import {openThreadTab} from './pm-navigation.ts';
import {test, expect} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {pmFixture, pmRoute, workspaceId, threadId} from './pm-fixture.ts';

const action = (page: any) => page.locator('[data-pm-action="pm.input.record"]');

test.describe('PM durable form focus and busy navigation', () => {
  test('1. Enter on the submit button, with the POST held', async ({page}) => {
    const f = pmFixture();
    let releasePost = () => {};
    const postHeld = new Promise<void>(resolve => { releasePost = resolve; });
    let postCount = 0;

    await mountConversation(page, false, async (input: any) => {
      const path = new URL(input.path, 'http://fixture').pathname;
      if (input.method === 'POST' && path === '/operations') {
        postCount++;
        await postHeld;
        return {
          status: 200,
          data: {
            operation: {
              operation_id: 'op_' + '1'.repeat(32),
              actor: f.state.actor,
              idempotency_key: input.idempotency_key,
              action: input.body.action,
              target: input.body.target,
              params: input.body.params,
              preconditions: input.body.preconditions,
              status: 'succeeded',
              result: {input_id: 'pmi_' + '9'.repeat(32)},
            },
          },
        };
      }
      return f.dispatch(input);
    });

    await page.goto(pmRoute);
    await openThreadTab(page, 'inputs');
    await page.locator('[data-pm-add-input] > summary').click();

    const form = action(page);
    await form.getByLabel('Topic', {exact: true}).selectOption('problem');
    await form.getByLabel('Statement or question', {exact: true}).fill('Enter on submit button test');

    const saveButton = form.getByRole('button', {name: 'Save input', exact: true});
    const status = form.locator(':scope > [role="status"]');

    await saveButton.focus();
    await expect(saveButton).toBeFocused();
    await page.keyboard.press('Enter');

    // Sync point: POST is sent and held
    await expect.poll(() => postCount).toBe(1);

    // While held:
    // - document.activeElement is that button
    // - it has aria-disabled="true"
    // - status has aria-busy="true"
    await expect(saveButton).toBeFocused();
    await expect(saveButton).toHaveAttribute('aria-disabled', 'true');
    expect(await saveButton.evaluate((b: HTMLButtonElement) => b.disabled)).toBe(false);
    await expect(status).toHaveAttribute('aria-busy', 'true');

    // A second Enter and a click make no second POST
    await page.keyboard.press('Enter');
    await saveButton.click({force: true});
    expect(postCount).toBe(1);

    // After release with succeeded: exactly one POST, and both attributes are gone
    releasePost();
    await expect(saveButton).not.toHaveAttribute('aria-disabled');
    await expect(status).not.toHaveAttribute('aria-busy');
    expect(postCount).toBe(1);

    // Report where focus is (item 5)
    const activeFocus = await page.evaluate(() => ({
      tagName: document.activeElement?.tagName,
      className: document.activeElement?.className,
      id: document.activeElement?.id,
      text: document.activeElement?.textContent?.slice(0, 30),
      isBody: document.activeElement === document.body,
    }));
  });

  test('2. Enter in a text field (implicit submission), with the POST held', async ({page}) => {
    const f = pmFixture();
    let releasePost = () => {};
    const postHeld = new Promise<void>(resolve => { releasePost = resolve; });
    let postCount = 0;

    await mountConversation(page, false, async (input: any) => {
      const path = new URL(input.path, 'http://fixture').pathname;
      if (input.method === 'POST' && path === '/operations') {
        postCount++;
        await postHeld;
        return {
          status: 200,
          data: {
            operation: {
              operation_id: 'op_' + '2'.repeat(32),
              actor: f.state.actor,
              idempotency_key: input.idempotency_key,
              action: input.body.action,
              target: input.body.target,
              params: input.body.params,
              preconditions: input.body.preconditions,
              status: 'succeeded',
              result: {input_id: 'pmi_' + '8'.repeat(32)},
            },
          },
        };
      }
      return f.dispatch(input);
    });

    await page.goto(pmRoute);
    await openThreadTab(page, 'inputs');
    await page.locator('[data-pm-add-input] > summary').click();

    const form = action(page);
    await form.getByLabel('Topic', {exact: true}).selectOption('problem');
    await form.getByLabel('Statement or question', {exact: true}).fill('Implicit submit content');

    const refInput = form.getByLabel('Source reference', {exact: true});
    await refInput.fill('https://example.com/spec');
    await refInput.focus();
    await expect(refInput).toBeFocused();

    // Implicit submission via Enter in text field
    await page.keyboard.press('Enter');

    // Sync point: POST is sent and held
    await expect.poll(() => postCount).toBe(1);

    // Focus is on the submit button while the POST is held
    const saveButton = form.getByRole('button', {name: 'Save input', exact: true});
    await expect(saveButton).toBeFocused();
    await expect(saveButton).toHaveAttribute('aria-disabled', 'true');

    // Another Enter sends nothing. Exactly one POST.
    await page.keyboard.press('Enter');
    expect(postCount).toBe(1);

    releasePost();
    await expect(saveButton).not.toHaveAttribute('aria-disabled');
    expect(postCount).toBe(1);
  });

  test('3. A terminal failure moves focus to Edit request', async ({page}) => {
    const f = pmFixture();
    let releasePost = () => {};
    const postHeld = new Promise<void>(resolve => { releasePost = resolve; });

    await mountConversation(page, false, async (input: any) => {
      const path = new URL(input.path, 'http://fixture').pathname;
      if (input.method === 'POST' && path === '/operations') {
        await postHeld;
        return {
          status: 200,
          data: {
            operation: {
              operation_id: 'op_' + '3'.repeat(32),
              actor: f.state.actor,
              idempotency_key: input.idempotency_key,
              action: input.body.action,
              target: input.body.target,
              params: input.body.params,
              preconditions: input.body.preconditions,
              status: 'failed',
              error: {code: 'OPERATION_FAILED', message: 'Terminal failure occurred'},
            },
          },
        };
      }
      return f.dispatch(input);
    });

    await page.goto(pmRoute);
    await openThreadTab(page, 'inputs');
    await page.locator('[data-pm-add-input] > summary').click();

    const form = action(page);
    await form.getByLabel('Topic', {exact: true}).selectOption('problem');
    await form.getByLabel('Statement or question', {exact: true}).fill('Terminal failure test');

    const saveButton = form.getByRole('button', {name: 'Save input', exact: true});
    await saveButton.focus();
    await page.keyboard.press('Enter');

    // Verify button is focused while held
    await expect(saveButton).toBeFocused();

    // Release POST with failed operation
    releasePost();

    // Focus moves to "Edit request" (visible as "New request" via t('pm_edit_request'))
    const editButton = form.getByRole('button', {name: 'New request', exact: true});
    await expect(editButton).toBeVisible();
    await expect(editButton).toBeFocused();
    await expect(saveButton).toBeHidden();
  });

  test('4. An initial admission refusal moves focus to Edit request', async ({page}) => {
    const f = pmFixture();
    let releasePost = () => {};
    const postHeld = new Promise<void>(resolve => { releasePost = resolve; });

    await mountConversation(page, false, async (input: any) => {
      const path = new URL(input.path, 'http://fixture').pathname;
      if (input.method === 'POST' && path === '/operations') {
        await postHeld;
        return {
          status: 403,
          data: {
            error: {
              code: 'FORBIDDEN',
              message: 'Earlier request refused',
              admission_refused: true,
            },
          },
        };
      }
      return f.dispatch(input);
    });

    await page.goto(pmRoute);
    await openThreadTab(page, 'inputs');
    await page.locator('[data-pm-add-input] > summary').click();

    const form = action(page);
    await form.getByLabel('Topic', {exact: true}).selectOption('problem');
    await form.getByLabel('Statement or question', {exact: true}).fill('Admission refusal test');

    const saveButton = form.getByRole('button', {name: 'Save input', exact: true});
    await saveButton.focus();
    await page.keyboard.press('Enter');

    // On base, this fails immediately because saveButton is disabled and focus is dropped to body
    await expect(saveButton).toBeFocused();

    releasePost();

    // Focus moves to "Edit request" (visible as "New request" via t('pm_edit_request'))
    const editButton = form.getByRole('button', {name: 'New request', exact: true});
    await expect(editButton).toBeVisible();
    await expect(editButton).toBeFocused();
    await expect(saveButton).toBeHidden();
  });

  test('5. The automatic receipt check blocks manual GET and keeps button focusable', async ({page}) => {
    const f = pmFixture();
    const requests: {method: string, path: string}[] = [];
    const opId = 'op_' + '5'.repeat(32);

    await page.clock.install();

    let releaseGet: () => void = () => {};
    let holdGet = false;
    let lastPost: any = null;
    const gate = new Promise<void>(resolve => { releaseGet = resolve; });

    await mountConversation(page, false, async (input: any) => {
      const path = new URL(input.path, 'http://fixture').pathname;
      requests.push({method: input.method, path});
      if (input.method === 'POST' && path === '/operations') {
        lastPost = input;
        const response = await f.dispatch(input);
        return {
          status: 202,
          data: {
            operation: {
              ...response.data.operation,
              operation_id: opId,
              status: 'running',
              result: null,
            },
          },
        };
      }
      if (input.method === 'GET' && path === `/operations/${opId}`) {
        if (holdGet) await gate;
        return {
          status: 200,
          data: {
            operation: {
              operation_id: opId,
              actor: f.state.actor,
              idempotency_key: lastPost?.idempotency_key,
              action: lastPost?.body.action,
              target: lastPost?.body.target,
              params: lastPost?.body.params,
              preconditions: lastPost?.body.preconditions,
              status: 'running',
            },
          },
        };
      }
      return f.dispatch(input);
    });

    await page.goto(pmRoute);
    await page.clock.pauseAt(await page.evaluate(() => Date.now() + 1000));
    await openThreadTab(page, 'inputs');
    await page.locator('[data-pm-add-input] > summary').click();

    const form = action(page);
    await form.getByLabel('Topic', {exact: true}).selectOption('problem');
    await form.getByLabel('Statement or question', {exact: true}).fill('Auto check test input');

    const saveButton = form.getByRole('button', {name: 'Save input', exact: true});
    await saveButton.click();

    // POST has completed, intent is saved, autoChecking timer is scheduled (250ms)
    await expect.poll(() => requests.filter(r => r.method === 'POST' && r.path === '/operations').length).toBe(1);

    // While autoChecking:
    // Submit button must have aria-disabled="true", and NOT disabled
    await expect(saveButton).toHaveAttribute('aria-disabled', 'true');
    expect(await saveButton.evaluate((el: HTMLButtonElement) => el.disabled)).toBe(false);

    const status = form.locator(':scope > [role="status"]');
    await expect(status).not.toHaveAttribute('aria-busy');

    const getsBefore = () => requests.filter(r => r.method === 'GET' && r.path === `/operations/${opId}`).length;
    expect(getsBefore()).toBe(0);

    // Any manual request during autoChecking would be held and trigger busy state
    holdGet = true;

    // A click and an Enter on the button while autoChecking start no manual GET /operations/{id}
    await saveButton.click({force: true});
    await expect(status).not.toHaveAttribute('aria-busy');
    expect(getsBefore()).toBe(0);

    await saveButton.press('Enter');
    await expect(status).not.toHaveAttribute('aria-busy');
    expect(getsBefore()).toBe(0);

    // Advance clock by 250ms to allow automatic receipt poll to fire
    await page.clock.runFor(250);
    await expect.poll(() => getsBefore()).toBe(1);
    await expect(status).toHaveAttribute('aria-busy', 'true');

    // Release held GET so automatic receipt check completes
    releaseGet();

    // After the automatic GET has been answered and rendered, the total GET count is exactly 1
    const receipt = form.locator(':scope > .pm-receipt');
    await expect(receipt.getByRole('link', {name: 'View operation', exact: true})).toBeVisible();
    await expect(status).not.toHaveAttribute('aria-busy');

    // Exactly 1 POST and 1 automatic GET
    const postTotal = requests.filter(r => r.method === 'POST' && r.path === '/operations').length;
    expect(postTotal).toBe(1);
    expect(getsBefore()).toBe(1);
  });

  test('6. Tab while busy reaches the busy submit button', async ({page}) => {
    const f = pmFixture();
    let releasePost = () => {};
    const postHeld = new Promise<void>(resolve => { releasePost = resolve; });

    await mountConversation(page, false, async (input: any) => {
      const path = new URL(input.path, 'http://fixture').pathname;
      if (input.method === 'POST' && path === '/operations') {
        await postHeld;
        return {
          status: 200,
          data: {
            operation: {
              operation_id: 'op_' + '6'.repeat(32),
              actor: f.state.actor,
              idempotency_key: input.idempotency_key,
              action: input.body.action,
              target: input.body.target,
              params: input.body.params,
              preconditions: input.body.preconditions,
              status: 'succeeded',
              result: null,
            },
          },
        };
      }
      return f.dispatch(input);
    });

    await page.goto(pmRoute);
    await openThreadTab(page, 'inputs');
    await page.locator('[data-pm-add-input] > summary').click();

    const form = action(page);
    await form.getByLabel('Topic', {exact: true}).selectOption('problem');
    await form.getByLabel('Statement or question', {exact: true}).fill('Tab while busy test');

    const saveButton = form.getByRole('button', {name: 'Save input', exact: true});
    const summary = page.locator('[data-pm-add-input] > summary');

    // Submit via Enter on the button
    await saveButton.focus();
    await page.keyboard.press('Enter');

    // While busy: button has aria-disabled and is not disabled
    await expect(saveButton).toBeFocused();
    await expect(saveButton).toHaveAttribute('aria-disabled', 'true');

    // From the control before the button, Tab reaches the busy button
    await summary.focus();
    await expect(summary).toBeFocused();

    await page.keyboard.press('Tab');
    await expect(saveButton).toBeFocused();

    releasePost();
  });
});
