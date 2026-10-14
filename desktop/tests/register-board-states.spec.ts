import {expect, test, type Locator} from '@playwright/test';
import {reviewFixture} from './requirements-review-fixture.ts';

const locales = ['en', 'zh-TW'] as const;
const noMatches = (locale: string) => locale === 'en' ? 'No work matches these filters' : '沒有符合篩選條件的工作';
const clearName = (locale: string) => locale === 'en' ? 'Clear filters' : '清除篩選';
const rows = (board: Locator) => board.locator('[data-pm-kanban-work]:not([hidden])');

for (const locale of locales) for (const width of [1440, 390]) test(`Board clears a filtered empty result ${locale} ${width}`, async ({page}) => {
  await page.setViewportSize({width, height: 900});
  const f = reviewFixture(locale); await f.mount(page, 'board');
  const board = page.locator('[data-pm-kanban]'); await expect(rows(board)).toHaveCount(12);
  const group = board.getByLabel(locale === 'en' ? 'Group by' : '分組方式', {exact: true});
  await group.selectOption('milestone');
  await board.getByLabel(locale === 'en' ? 'Requirement' : '需求', {exact: true}).selectOption(f.ids.requirements.email);
  await board.getByLabel('Agent', {exact: true}).selectOption('codex');
  await board.getByRole('searchbox').fill('zzzz-no-such-work');
  await expect(board.getByRole('heading', {name: noMatches(locale), exact: true})).toBeVisible();
  await expect(board.locator('[data-pm-kanban-column]:visible')).toHaveCount(0);
  await expect(board.locator('.ui-empty')).toHaveCount(1);
  const clear = board.getByRole('button', {name: clearName(locale), exact: true});
  await expect(clear).toHaveClass(/secondary/); await clear.click();
  await expect(board.getByRole('searchbox')).toHaveValue('');
  await expect(board.getByLabel(locale === 'en' ? 'Requirement' : '需求', {exact: true})).toHaveValue('');
  await expect(board.getByLabel('Agent', {exact: true})).toHaveValue('');
  await expect(group).toHaveValue('milestone');
  await expect(rows(board)).toHaveCount(12); await expect(board.locator('.ui-empty')).toHaveCount(0);
  await expect(board.getByRole('searchbox')).toBeFocused();
  expect(f.writes).toEqual([]);
});

for (const locale of locales) test(`Board with no associated work offers no filter reset ${locale}`, async ({page}) => {
  const f = reviewFixture(locale);
  f.overrides.push((url, result) => {
    if (url.pathname.endsWith('/kanban')) {result.data.cards = []; result.data.next_cursor = null; result.data.wip = {count: 0, unknown: 0, cards: 0, scope: 'returned_page'};}
    return result;
  });
  await f.mount(page, 'board'); const board = page.locator('[data-pm-kanban]');
  await expect(board.getByRole('heading', {name: locale === 'en' ? 'No work has been explicitly associated with this workspace.' : '這個工作區尚未明確關聯任何工作。', exact: true})).toBeVisible();
  await expect(board.getByRole('button', {name: clearName(locale), exact: true})).toHaveCount(0);
  await expect(board.locator('[data-pm-kanban-column]:visible')).toHaveCount(0);
  expect(f.writes).toEqual([]);
});

for (const locale of locales) for (const partial of ['paged', 'byte_limited', 'visibility_filtered']) test(`Board zero loaded matches stay incomplete ${locale} ${partial}`, async ({page}) => {
  const f = reviewFixture(locale);
  f.overrides.push((url, result) => {
    if (url.pathname.endsWith('/kanban')) {
      if (partial === 'paged') result.data.next_cursor = result.data.cards.at(-1).binding.sequence;
      else result.data.coverage = {...result.data.coverage, [partial]: true};
    } return result;
  });
  await f.mount(page, 'board'); const board = page.locator('[data-pm-kanban]'); await expect(rows(board)).toHaveCount(12);
  await board.getByRole('searchbox').fill('zzzz-no-such-work');
  await expect(board.getByRole('heading', {name: locale === 'en' ? 'Work list incomplete' : '工作清單不完整', exact: true})).toBeVisible();
  await expect(board.locator('.ui-empty')).toContainText(locale === 'en' ? 'No loaded work matches' : '已載入工作沒有符合項目');
  await expect(board.getByRole('heading', {name: noMatches(locale), exact: true})).toHaveCount(0);
  await expect(page.locator('[data-pm-work-meta]')).toContainText(locale === 'en' ? '12 loaded' : '已載入 12');
  await expect(board.getByRole('button', {name: clearName(locale), exact: true})).toBeVisible();
  expect(f.writes).toEqual([]);
});

for (const locale of locales) test(`Board header describes shown work once only while filtered ${locale}`, async ({page}) => {
  const f = reviewFixture(locale); await f.mount(page, 'board');
  const board = page.locator('[data-pm-kanban]'), meta = page.locator('[data-pm-work-meta]');
  const all = locale === 'en' ? '12 work items · 1 recorded blocker' : '12 項工作 · 1 項已記錄阻礙';
  await expect(meta).toHaveText(all);
  const requirement = board.getByLabel(locale === 'en' ? 'Requirement' : '需求', {exact: true});
  await requirement.selectOption(f.ids.requirements.email);
  const shown = locale === 'en' ? '1 shown' : '顯示 1 筆';
  await expect(meta).toHaveText(`${all} · ${shown}`);
  expect((await page.locator('#main').innerText()).split(shown).length - 1).toBe(1);
  await requirement.selectOption(''); await expect(meta).toHaveText(all);
  await board.getByRole('searchbox').fill('zzzz-no-such-work');
  const none = locale === 'en' ? 'None shown' : '沒有顯示項目';
  await expect(meta).toHaveText(`${all} · ${none}`);
  expect((await page.locator('#main').innerText()).split(none).length - 1).toBe(1);
  await board.getByRole('button', {name: clearName(locale), exact: true}).click(); await expect(meta).toHaveText(all);
  expect(f.writes).toEqual([]);
});

for (const locale of locales) for (const view of ['board', 'issues']) for (const width of [1440, 390]) test(`Panel errors keep Retry adjacent ${view} ${locale} ${width}`, async ({page}) => {
  await page.setViewportSize({width, height: 900});
  const f = reviewFixture(locale); let failed = false, code: string | null = 'UNAVAILABLE';
  f.overrides.push((url, result) => failed && url.pathname.endsWith(view === 'board' ? '/kanban' : '/register')
    ? {status: 503, data: {error: {code, message: 'Private provider failure'}}} : result);
  await f.mount(page, view);
  const panel = page.locator(view === 'board' ? '[data-pm-kanban]' : '[data-pm-register]');
  await expect(panel.locator(view === 'board' ? '[data-pm-kanban-work]' : '[data-row-key]')).toHaveCount(view === 'board' ? 12 : 3);
  failed = true; await page.locator('[data-pm-refresh]').click();
  const alert = panel.getByRole('alert'), retry = alert.getByRole('button', {name: locale === 'en' ? 'Retry' : '重試', exact: true});
  await expect(alert).toHaveCount(1);
  await expect(alert).toContainText(view === 'board' ? (locale === 'en' ? 'Work records unavailable' : '無法取得工作紀錄') : (locale === 'en' ? 'Could not read records' : '無法讀取紀錄'));
  await expect(alert).not.toContainText('Private provider');
  await expect(alert.locator(':scope > span')).not.toContainText('UNAVAILABLE');
  await expect(retry).toHaveClass(/secondary/);
  expect(await alert.evaluate(node => {
    const sentence = node.querySelector('span')!.getBoundingClientRect(), action = node.querySelector('button')!.getBoundingClientRect();
    return action.left >= sentence.right && action.top < sentence.bottom && action.bottom > sentence.top;
  })).toBe(true);
  await expect(panel.locator('.ui-empty')).toHaveCount(0);
  if (view === 'board') {
    const details = panel.locator('.pm-board-error details'); await details.locator('summary').click();
    await expect(details).toContainText('UNAVAILABLE');
    code = null; await page.locator('[data-pm-refresh]').click(); await expect(details).toHaveCount(0);
  }
  failed = false; await retry.click();
  await expect(panel.locator(view === 'board' ? '[data-pm-kanban-work]' : '[data-row-key]')).toHaveCount(view === 'board' ? 12 : 3);
  await expect(alert).toHaveCount(0); expect(f.writes).toEqual([]);
});

for (const locale of locales) for (const width of [1440, 1000, 390]) test(`Board error Details stays inside the full-width banner ${locale} ${width}`, async ({page}) => {
  await page.setViewportSize({width, height: 900});
  const f = reviewFixture(locale);
  f.overrides.push((url, result) => url.pathname.endsWith('/kanban')
    ? {status: 503, data: {error: {code: 'UNAVAILABLE', message: 'Private provider failure'}}} : result);
  await f.mount(page, 'board');
  const board = page.locator('[data-pm-kanban]'), status = board.locator(':scope > [role=status]');
  const alert = status.getByRole('alert'), details = alert.locator('details');
  const retry = alert.getByRole('button', {name: locale === 'en' ? 'Retry' : '重試', exact: true});
  await expect(alert.locator(':scope > span')).toHaveText(locale === 'en' ? 'Work records unavailable' : '無法取得工作紀錄');
  await expect(alert).not.toContainText('Private provider');
  await expect(details.locator('summary')).toHaveText(locale === 'en' ? 'Details' : '詳細資料');
  const code = details.locator('p'); await expect(code).toBeHidden();
  const fullWidth = async () => {
    const banner = (await alert.boundingBox())!, container = (await status.boundingBox())!;
    expect(Math.abs(banner.x - container.x)).toBeLessThanOrEqual(1);
    expect(Math.abs(banner.width - container.width)).toBeLessThanOrEqual(1);
    const action = (await retry.boundingBox())!, summary = (await details.locator('summary').boundingBox())!;
    expect(summary.y >= action.y + action.height - 1 || summary.x >= action.x + action.width).toBe(true);
    expect(summary.x + summary.width).toBeLessThanOrEqual(banner.x + banner.width);
  };
  await fullWidth(); await details.locator('summary').click();
  await expect(code).toBeVisible(); await expect(code).toHaveText('UNAVAILABLE'); await fullWidth();
  const geometry = await alert.evaluate(node => {
    const banner = node.getBoundingClientRect(), css = getComputedStyle(node);
    const code = node.querySelector('details > p')!.getBoundingClientRect();
    const action = node.querySelector('button')!.getBoundingClientRect(), sentence = node.querySelector('span')!.getBoundingClientRect();
    return {width: code.width, available: banner.width - parseFloat(css.paddingLeft) - parseFloat(css.paddingRight),
      codeTop: code.top, rowBottom: Math.max(action.bottom, sentence.bottom), codeBottom: code.bottom, bannerBottom: banner.bottom};
  });
  expect(Math.abs(geometry.width - geometry.available)).toBeLessThanOrEqual(1);
  expect(geometry.codeTop).toBeGreaterThanOrEqual(geometry.rowBottom);
  expect(geometry.codeBottom).toBeLessThan(geometry.bannerBottom);
  await details.locator('summary').click(); await expect(code).toBeHidden(); await fullWidth();
  expect(f.writes).toEqual([]);
});

for (const locale of locales) for (const width of [1440, 390]) for (const filter of ['search', 'age']) test(`Issues empty ${filter} filter hides trends until cleared ${locale} ${width}`, async ({page}) => {
  await page.setViewportSize({width, height: 900});
  const f = reviewFixture(locale);
  if (filter === 'age') f.overrides.push((url, result) => {
    if (url.pathname.endsWith('/register')) result.data.next_cursor = null;
    return result;
  });
  await f.mount(page, 'issues'); const panel = page.locator('[data-pm-register]'), trends = panel.locator('[data-reg-trends]');
  await expect(panel.locator('[data-row-key]')).toHaveCount(3); await expect(trends).toBeVisible();
  if (width === 390) await trends.locator('summary').click();
  await expect(panel.locator('[data-reg-age="0-7d"]')).toBeVisible();
  if (filter === 'search') await panel.getByRole('searchbox').fill('zzzz-no-such-record');
  else await panel.locator('[data-reg-age-filter]').selectOption('31-90d');
  await expect(panel.getByRole('heading', {name: locale === 'en' ? 'No matching records' : '沒有符合的紀錄', exact: true})).toBeVisible();
  await expect(page.locator('[data-pm-header-meta]')).toHaveText(locale === 'en' ? 'None shown' : '沒有顯示項目');
  await expect(trends).toBeHidden(); await expect(panel.locator('[data-reg-age]:visible')).toHaveCount(0);
  await expect(panel.locator('[data-row-key]')).toHaveCount(0); await expect(panel.locator('.ui-empty')).toHaveCount(1);
  await panel.getByRole('button', {name: clearName(locale), exact: true}).click();
  await expect(panel.locator('[data-row-key]')).toHaveCount(3); await expect(trends).toBeVisible();
  await expect(panel.locator('[data-reg-age="0-7d"]')).toBeVisible(); await expect(panel.locator('.ui-empty')).toHaveCount(0);
  expect(f.writes).toEqual([]);
});

for (const locale of locales) test(`Issues meta omits zero type counts ${locale}`, async ({page}) => {
  const f = reviewFixture(locale);
  f.overrides.push((url, result) => {
    if (url.pathname.endsWith('/register')) {
      result.data.rows = result.data.rows.filter((row: {type: string}) => row.type === 'issue');
      result.data.next_cursor = null; result.data.summary = null;
      result.data.total_by_type = {risk: 0, issue: result.data.rows.length, action: 0, change: 0, decision: 0};
    } return result;
  });
  await f.mount(page, 'issues'); await expect(page.locator('[data-row-key]')).toHaveCount(1);
  await expect(page.locator('[data-pm-header-meta]')).toHaveText(locale === 'en' ? 'Issue 1' : '問題 1');
  expect(f.writes).toEqual([]);
});

for (const locale of locales) for (const width of [1440, 390]) test(`Issues zero loaded age matches retain paged trends ${locale} ${width}`, async ({page}) => {
  await page.setViewportSize({width, height: 900});
  const f = reviewFixture(locale); await f.mount(page, 'issues');
  const panel = page.locator('[data-pm-register]'), trends = panel.locator('[data-reg-trends]');
  await expect(panel.locator('[data-row-key]')).toHaveCount(3);
  await panel.locator('[data-reg-age-filter]').selectOption('31-90d');
  await expect(panel.getByRole('heading', {name: locale === 'en' ? 'Records incomplete' : '紀錄不完整', exact: true})).toBeVisible();
  await expect(panel.locator('.ui-empty')).toContainText(locale === 'en' ? 'No loaded records match' : '已載入紀錄沒有符合項目');
  await expect(trends).toBeVisible();
  if (width === 390) await trends.locator('summary').click();
  await expect(panel.locator('[data-reg-age="31-90d"]')).toBeVisible();
  await expect(panel.getByRole('heading', {name: locale === 'en' ? 'No matching records' : '沒有符合的紀錄', exact: true})).toHaveCount(0);
  expect(f.writes).toEqual([]);
});

for (const locale of locales) for (const width of [1440, 1000, 768, 390]) test(`Board lanes fit their content ${locale} ${width}`, async ({page}) => {
  await page.setViewportSize({width, height: 900});
  const f = reviewFixture(locale); await f.mount(page, 'board');
  const board = page.locator('[data-pm-kanban]'); await expect(rows(board)).toHaveCount(12);
  const sizes = () => board.locator('[data-pm-kanban-column]').evaluateAll(lanes => lanes.map(lane => {
    const css = getComputedStyle(lane), header = lane.querySelector('header')!, cards = lane.querySelector('.pm-kanban-cards')!;
    return {height: lane.getBoundingClientRect().height, x: lane.getBoundingClientRect().x,
      needed: header.getBoundingClientRect().height + parseFloat(getComputedStyle(header).marginBottom) + cards.getBoundingClientRect().height + parseFloat(css.paddingTop) + parseFloat(css.paddingBottom)};
  }));
  const checkHeight = async () => {
    const lanes = await sizes();
    if (width === 390) {
      expect(new Set(lanes.map(lane => lane.x)).size).toBe(1);
      for (const lane of lanes) expect(Math.abs(lane.height - lane.needed)).toBeLessThanOrEqual(1);
    } else {
      const tallest = Math.max(...lanes.map(lane => lane.needed));
      for (const lane of lanes) expect(Math.abs(lane.height - tallest)).toBeLessThanOrEqual(1);
    }
  };
  await checkHeight();
  if (width === 768) expect(await board.locator('.pm-kanban-list').evaluate(node => node.scrollWidth > node.clientWidth)).toBe(true);
  await board.getByLabel(locale === 'en' ? 'Requirement' : '需求', {exact: true}).selectOption(f.ids.requirements.email);
  await expect(rows(board)).toHaveCount(1); await checkHeight();
  expect(Math.max(...(await sizes()).map(lane => lane.height))).toBeLessThan(300);
  expect(f.writes).toEqual([]);
});

for (const locale of locales) test(`Issues clears every active filter from an empty result ${locale}`, async ({page}) => {
  await page.setViewportSize({width: 1440, height: 900});
  const f = reviewFixture(locale); await f.mount(page, 'issues');
  const panel = page.locator('[data-pm-register]'); await expect(panel.locator('[data-row-key]')).toHaveCount(3);
  await panel.locator('[data-reg-age-filter]').selectOption('0-7d');
  await panel.locator('[data-reg-week]').last().click();
  await panel.locator('[data-reg-type=issue]').click();
  await panel.locator('[data-reg-status]').selectOption('open');
  await panel.locator('[data-reg-owner]').fill('zzzz-no-such-owner');
  await panel.locator('[data-reg-search]').fill('zzzz-no-such-record');
  await expect(panel.getByRole('heading', {name: locale === 'en' ? 'No matching records' : '沒有符合的紀錄', exact: true})).toBeVisible();
  await page.setViewportSize({width: 390, height: 844});
  const clear = panel.getByRole('button', {name: clearName(locale), exact: true}); await expect(clear).toHaveClass(/secondary/); await clear.click();
  for (const selector of ['[data-reg-status]', '[data-reg-owner]', '[data-reg-search]', '[data-reg-age-filter]']) await expect(panel.locator(selector)).toHaveValue('');
  await expect(panel.locator('[data-reg-type=""]')).toHaveAttribute('aria-checked', 'true');
  await expect(panel.locator('[data-reg-filter]')).toHaveCount(0);
  await expect(panel.locator('[data-row-key]')).toHaveCount(3);
  await expect(panel.locator('[data-reg-search]')).toBeFocused();
  expect(f.writes).toEqual([]);
});

for (const locale of locales) for (const partial of [false, true]) test(`Issues empty read stays honest ${locale} partial=${partial}`, async ({page}) => {
  const f = reviewFixture(locale);
  f.overrides.push((url, result) => {
    if (url.pathname.endsWith('/register')) {
      result.data.rows = []; result.data.next_cursor = null; result.data.summary = null;
      result.data.coverage.complete = !partial;
      result.data.total_by_type = partial ? null : {risk: 0, issue: 0, action: 0, change: 0, decision: 0};
    } return result;
  });
  await f.mount(page, 'issues'); const panel = page.locator('[data-pm-register]');
  const title = partial ? (locale === 'en' ? 'Records incomplete' : '紀錄不完整') : (locale === 'en' ? 'No issues or changes yet' : '還沒有問題或變更');
  await expect(panel.getByRole('heading', {name: title, exact: true})).toBeVisible();
  await expect(panel.getByRole('button', {name: clearName(locale), exact: true})).toHaveCount(0);
  if (partial) {
    await panel.locator('[data-reg-search]').fill('zzzz-no-such-record');
    await expect(panel.getByRole('heading', {name: title, exact: true})).toBeVisible();
    await expect(panel.locator('.ui-empty')).toContainText(locale === 'en' ? 'No loaded records match' : '已載入紀錄沒有符合項目');
    await expect(panel.getByRole('button', {name: clearName(locale), exact: true})).toBeVisible();
  }
  expect(f.writes).toEqual([]);
});
