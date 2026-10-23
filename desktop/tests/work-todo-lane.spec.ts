import {test, expect} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {httpEventDelivery} from './event-delivery.mjs';
import {installBodyCompletedCounter, countCompletedBodies, syncHeldBodyCompletion} from './page-sync.ts';
import {workStartFixture, workspaceId, workId, route} from './work-start-fixture.ts';

for (const locale of ['en', 'zh-TW']) test(`to-do lane stays distinct from a started Attempt (${locale})`, async ({page}, testInfo) => {
  await page.setViewportSize({width: 1440, height: 900});
  await page.addInitScript(value => localStorage.setItem('batc.lang', value), locale);
  await installBodyCompletedCounter(page);
  const f = workStartFixture(), work = f.board.state.cards[0];
  work.work.state = 'todo';
  work.column = 'backlog';
  work.flow.wip = false;
  work.flow.age_seconds = null;
  f.state.attempts.push({
    attempt_id: 'att_' + '1'.repeat(32), workspace_id: workspaceId, work_item_id: workId(1),
    start_operation_id: 'op_' + '1'.repeat(32), stage: 'started', status: 'succeeded',
    session: {host: 'fixture-host', session_id: 'session-123'}, created_at: 1791705600,
  });
  let release!: () => void;
  f.state.holdAttempts = new Promise<void>(resolve => {release = resolve;});
  await mountConversation(page, false, f.dispatch);
  const events = await httpEventDelivery(page);
  await page.goto(route + '/board');
  const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
  await expect(card).toBeVisible();
  await events.pause();
  await card.locator('[data-board-open]').click();
  await expect.poll(() => f.state.attemptsReads).toBe(1);
  const before = await countCompletedBodies(page, '/attempts');
  release();
  // The response body and its render have settled; the next event poll stays held.
  await syncHeldBodyCompletion(page, '/attempts', before);
  const inspector = page.locator('[data-work-inspector]');
  const started = locale === 'en' ? 'Started' : '已開始';
  const lane = locale === 'en' ? 'To do' : '待辦';
  const notStarted = locale === 'en' ? 'Not started' : '未開始';
  await expect(inspector.locator('.pm-work-start-attempt-status strong')).toHaveText(started);
  await page.screenshot({path: testInfo.outputPath(`lane-${locale}.png`), fullPage: true});
  await expect(page.locator('[data-pm-kanban-column="backlog"] .pm-board-lane-name')).toHaveText(lane);
  await expect(card.locator('.pm-board-facts > .sr-only')).toHaveText(lane);
  await expect(inspector.locator('[data-work-section="status"] [data-status]')).toContainText([lane]);
  await expect(card).not.toContainText(notStarted);
  await expect(inspector.locator('[data-work-section="status"]')).not.toContainText(notStarted);
  await expect(page.locator(`[data-pm-kanban-column="backlog"] [data-pm-kanban-work="${workId(1)}"]`)).toHaveCount(1);
  expect(work.work.state).toBe('todo');
  expect(work.column).toBe('backlog');
  expect(f.state.writes).toEqual([]);
  expect(f.board.state.writes).toEqual([]);
  const labels = await page.evaluate(async () => {
    const {workStatusKey} = await import('/src/global-labels.js');
    const {t} = await import('/src/i18n.js');
    return {key: workStatusKey('todo'), label: t('ui_status_' + workStatusKey('todo')), legacy: t('wi_state_todo')};
  });
  expect(labels).toEqual({key: 'todo', label: lane, legacy: locale === 'en' ? 'to do' : lane});
});
