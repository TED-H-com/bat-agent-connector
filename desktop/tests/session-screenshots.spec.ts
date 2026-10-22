// Reviewed mock-only Session screenshots. Store artifacts in Playwright's per-test output directory.
import {expect, test, type Page} from '@playwright/test';
import {checkpointHost, consoleNoise, fromRequirements, notFound, openSession} from './session-fixture.ts';

async function capture(page: Page, name: string) {
  const testInfo = test.info();
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
  await page.screenshot({path: testInfo.outputPath(`${name}.png`), animations: 'disabled'});
}
for (const locale of ['en', 'zh-TW'] as const) {
  const zh = locale === 'zh-TW';
  test(`Session review screenshots ${locale}`, async ({browser}) => {
    const permission = {kind: 'permission', toolUseId: 'toolu_layout_p', toolName: 'Bash',
      input_preview: 'Run the rate-limit tests with npm run test -- --grep "lockout"'};
    const questions = {kind: 'ask_user', toolUseId: 'toolu_layout_q', questions: zh ? [
      {header: '門檻', question: '登入失敗幾次後鎖定帳號？', options: ['3', '5', '10']},
      {header: '提示', question: '會員應看到哪種鎖定提示？', options: ['簡短', '詳細']},
      {header: '解鎖', question: '客服是否能解除鎖定？', options: ['是', '否']},
    ] : [
      {header: 'Threshold', question: 'How many failed attempts before lockout?', options: ['3', '5', '10']},
      {header: 'Copy', question: 'Which lockout message should members see?', options: ['Short', 'Detailed']},
      {header: 'Unlock', question: 'Should support be able to unlock accounts?', options: ['Yes', 'No']},
    ]};
    for (const [width, height] of [[1440, 900], [390, 844]] as const) {
      for (const [name, pending] of [['permission', permission], ['ask_user', questions]] as const) {
        const s = await openSession(browser, {locale, width, height});
        s.overrides.push(async (input, url) => {
          if (url.pathname !== `/sessions/${s.ids.host}/${s.ids.sessions.review}`) return undefined;
          const base = await s.fixture.dispatch(input); base.data.session.pending = pending; return base;
        });
        await fromRequirements(s.page, s.ids, 'review');
        await expect(s.page.getByRole('button', {name: zh ? '送出' : 'Send', exact: true})).toBeInViewport({ratio: 1});
        await expect(s.page.getByRole('button', {name: zh ? (name === 'permission' ? '允許' : '回答') : (name === 'permission' ? 'Allow' : 'Answer'), exact: true})).toBeInViewport({ratio: 1});
        await capture(s.page, `approval-${name}-${locale}-${width}`);
        expect(s.errors.filter(line => !consoleNoise(line))).toEqual([]);
        await s.context.close();
      }
      const s = await openSession(browser, {locale, width, height});
      await fromRequirements(s.page, s.ids, 'manual');
      await expect(s.page.locator('[data-session-readonly]')).toBeVisible();
      await capture(s.page, `manual-${locale}-${width}`);
      expect(s.writes).toEqual([]);
      await s.context.close();
    }
    for (const [width, height] of [[1440, 900], [1000, 900], [768, 900], [390, 844]] as const) {
      const s = await openSession(browser, {locale, width, height});
      await fromRequirements(s.page, s.ids, 'implement');
      await expect(s.page.locator('[data-session-pr]')).toHaveCount(2);
      await expect(s.page.getByRole('button', {name: zh ? '送出' : 'Send', exact: true})).toBeInViewport({ratio: 1});
      if (width < 1200 && width !== 768) {
        await s.page.getByRole('button', {name: zh ? '成果' : 'Results', exact: true}).click();
        await expect(s.page.locator('.ui-inspector')).toBeVisible();
        await capture(s.page, `inspector-${locale}-${width}`);
      } else await capture(s.page, `session-${locale}-${width}`);
      if (width === 1440) {
        s.overrides.push((input, url) => url.pathname === `/sessions/${s.ids.host}/${s.ids.sessions.implement}` ? notFound : undefined);
        s.sessionEvent('implement');
        await expect(s.page.locator('[data-session-withdrawn]')).toBeVisible();
        await capture(s.page, `withdrawn-${locale}-${width}`);
      }
      await s.context.close();
    }
    for (const [width, height] of [[1440, 900], [390, 844]] as const) {
      const s = await openSession(browser, {locale, width, height});
      s.overrides.push(checkpointHost(s, s.ids.sessions.manual));
      await fromRequirements(s.page, s.ids, 'manual');
      if (width < 1200) await s.page.getByRole('button', {name: zh ? '成果' : 'Results', exact: true}).click();
      const section = s.page.locator('.session-section').filter({has: s.page.locator('.session-checkpoints')});
      await expect(section.locator('.session-checkpoint-row')).toHaveCount(1);
      await capture(s.page, `checkpoint-${locale}-${width}`);
      await section.getByRole('button', {name: zh ? '從這裡接續' : 'Start from here', exact: true}).click();
      await section.locator('[data-checkpoint-form] textarea').fill(zh ? '依照筆記完成重設密碼流程。' : 'Finish the reset flow from these notes.');
      await capture(s.page, `checkpoint-form-${locale}-${width}`);
      await s.context.close();
    }
  });
}
