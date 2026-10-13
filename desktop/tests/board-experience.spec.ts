import {test, expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {mountConversation} from './conversation-fixture.ts';
import {demoFixture} from './demo/index.ts';
import {parseDiscussRefs} from '../src/pm-discuss-link.js';

for (const locale of ['en', 'zh-TW']) for (const width of [1440, 768, 390]) test(`board filtering and inspector ${locale} ${width}`, async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale, reducedMotion: 'reduce', viewport: {width, height: 900}}), page = await context.newPage();
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {if (message.type() === 'error') errors.push(message.text() + ' ' + message.location().url);});
  const fixture = demoFixture({locale, managedSession: true}), {ids} = fixture; await mountConversation(page, false, fixture.dispatch);
  await page.goto(`/dashboard/#/pm/${ids.workspaceId}/board`);
  const board = page.locator('[data-pm-kanban]'); await expect(board.locator('[data-pm-kanban-work]')).toHaveCount(12);
  await expect(page.locator('[data-pm-view="board"]')).toHaveAttribute('aria-current', 'page');
  const out = `${process.env.TMPDIR || '/tmp'}/shots`; await mkdir(out, {recursive: true});
  await page.screenshot({path: `${out}/board-${locale}-${width}.png`, fullPage: true});
  await board.getByLabel('Agent', {exact: true}).selectOption('codex');
  await expect(board.locator('[data-pm-kanban-work]:visible')).toHaveCount(1);
  await expect(board.locator(`[data-pm-kanban-work="${ids.work.email}"]`)).toBeVisible();
  await board.getByLabel('Agent', {exact: true}).selectOption('');
  const requirement = board.getByLabel(locale === 'en' ? 'Requirement' : '需求', {exact: true});
  await requirement.selectOption(ids.requirements.email); await expect(board.locator('[data-pm-kanban-work]:visible')).toHaveCount(1);
  await requirement.selectOption('');
  const grouping = board.getByLabel(locale === 'en' ? 'Group by' : '分組方式', {exact: true});
  await grouping.selectOption('milestone'); await expect(board.locator('[data-pm-kanban-column]')).toHaveCount(3);
  await grouping.selectOption('status');
  const card = board.locator(`[data-pm-kanban-work="${ids.work.audit}"]`);
  await expect(card.locator('.pm-board-blocker')).toHaveAttribute('title', locale === 'en' ? 'A linked task is blocked' : '連結的任務受阻');
  await card.focus(); await page.keyboard.press('Enter');
  const inspector = page.locator('[data-work-inspector]');
  await expect(inspector.locator('[data-work-section=status] [data-status=read_only]')).toBeVisible();
  await expect(inspector.locator('.ui-inspector-body > :not([data-work-section])')).toHaveCount(0);
  expect(await inspector.locator('[data-work-section]').evaluateAll(nodes => nodes.map(node => node.getAttribute('data-work-section')))).toEqual(['requirements', 'status', 'result', 'blocker', 'delivery', 'details']);
  expect(parseDiscussRefs(new URLSearchParams((await inspector.locator('[data-work-discuss]').getAttribute('href'))!.split('?')[1])).refs).toEqual([{kind: 'work_item', id: ids.work.audit}]);
  await expect(inspector.locator('[data-work-section=blocker]')).toContainText(locale === 'en' ? 'A linked task is blocked' : '連結的任務受阻');
  await page.screenshot({path: `${out}/board-inspector-${locale}-${width}.png`, fullPage: false});
  await page.keyboard.press('Escape'); await expect(card).toBeFocused();
  await card.click();
  await inspector.locator('[data-work-actions]').getByRole('button').click();
  await inspector.locator('[data-menu-key=ui_publication]').click();
  await expect(page.locator('[data-pm-issue-mappings]')).toBeVisible();
  await expect(inspector).toHaveCount(0);
  await page.keyboard.press('Escape'); await expect(card).toBeFocused();
  await page.keyboard.press('ArrowLeft'); await expect(card).not.toBeFocused();
  if (width < 800) {
    const lanes = board.locator('[data-pm-kanban-column]');
    const boxes = await lanes.evaluateAll(nodes => nodes.map(node => node.getBoundingClientRect().x)); expect(new Set(boxes).size).toBe(1);
    const toggle = lanes.first().getByRole('button').first(); await toggle.click(); await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(await page.locator('body').innerText()).not.toMatch(/\b(?:undefined|null|NaN|wv_\w+)\b/);
  if (width === 1440) {await page.emulateMedia({colorScheme: 'dark'}); await page.screenshot({path: `${out}/board-dark-${locale}-1440.png`, fullPage: true});}
  expect(errors).toEqual([]); await context.close();
});
