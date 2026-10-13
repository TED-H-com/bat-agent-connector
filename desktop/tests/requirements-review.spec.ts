import {test, expect} from '@playwright/test';
import {reviewFixture} from './requirements-review-fixture.ts';
for (const mode of ['absent', 'partial', 'filtered', 'byte-limited', 'failed']) test(`B2 acceptance ${mode} remains unknown`, async ({page}) => {
  const f = reviewFixture();
  f.overrides.push((url, r) => {
    if (['/bootstrap', '/capabilities'].includes(url.pathname)) {
      const caps = url.pathname === '/bootstrap' ? r.data.capabilities : r.data; caps.features.pm_workspaces.quality_reviews = mode !== 'absent';
    }
    if (url.pathname.endsWith('/quality-reviews')) return mode === 'failed' ? {status: 413, data: {error: {code: 'PM_QUALITY_LIMIT', message: 'Too large'}}}
      : {status: 200, data: {workspace_id: f.ids.workspaceId, reviews: [], next_cursor: mode === 'partial' ? 5 : null,
        visibility_filtered: mode === 'filtered', coverage: {byte_limited: mode === 'byte-limited'}}};
    return r;
  });
  await f.mount(page, 'requirements');
  const row = page.locator(`[data-rtm-requirement="${f.ids.requirements.email}"]`);
  await expect(row.locator('[data-column=acceptance] [data-status=unknown]')).toHaveCount(1);
  await expect(page.locator('[data-pm-work-meta]')).not.toContainText('0 accepted');
  await row.locator('[data-column=title]').click();
  await expect(page.locator('[data-work-inspector]')).toContainText('Acceptance unknown');
  await expect(page.locator('[data-work-section=result]')).not.toContainText('No result recorded');
  expect(f.writes).toEqual([]);
});
for (const mode of ['partial', 'filtered', 'absent']) test(`B3 delivery ${mode} marks every surface`, async ({page}) => {
  const f = reviewFixture();
  f.overrides.push((url, r) => {
    if (mode === 'absent' && ['/bootstrap', '/capabilities'].includes(url.pathname)) {
      const caps = url.pathname === '/bootstrap' ? r.data.capabilities : r.data; caps.features.pm_workspaces.engineering_delivery = false;
    }
    if (url.pathname.endsWith('/deliveries') && url.pathname.includes(f.ids.work.email)) {
      r.data.deliveries = r.data.deliveries.filter((row: any) => row.target.pull_number === 91);
      r.data.next_after = mode === 'partial' ? 2 : null; r.data.source_visibility_filtered = mode === 'filtered';
    } return r;
  });
  await f.mount(page, 'requirements');
  const row = page.locator(`[data-rtm-requirement="${f.ids.requirements.email}"]`), state = mode === 'absent' ? 'unknown' : 'partial';
  await expect(row.locator(`[data-column=pr] [data-status=${state}]`)).toHaveCount(1);
  await expect(row.locator(`[data-column=deploy] [data-status=${state}]`)).toHaveCount(1);
  await page.locator(`[data-rtm-toggle="${f.ids.requirements.email}"]`).click();
  await page.locator(`[data-rtm-select="${f.ids.work.email}"]`).click();
  await expect(page.locator('[data-work-section=delivery]')).toContainText(mode === 'absent' ? 'Delivery unknown' : 'Partial');
  await expect(page.locator('[data-work-section=delivery]')).not.toContainText('No delivery recorded');
  await page.locator('[data-pm-view=board]').click();
  await expect(page.locator(`[data-pm-kanban-work="${f.ids.work.email}"] [data-status=${state}]`)).toHaveCount(1);
  expect(f.writes).toEqual([]);
});
for (const view of ['requirements', 'board']) test(`B5 ${view} live ages preserve actions and menus`, async ({page}) => {
  const f = reviewFixture(); let tick = 0;
  f.overrides.push((url, r) => {
    if (url.pathname.includes('/kanban')) for (const card of r.data.cards || [r.data.card].filter(Boolean)) {
      card.flow.age_seconds = 432017.81 + tick++; card.flow.waiting_seconds = tick; card.work.updated_at = tick;
    } return r;
  });
  await f.mount(page, view, f.ids.work.audit);
  if (view === 'board') await page.locator(`[data-board-open="${f.ids.work.audit}"]`).click();
  const inspector = page.locator('[data-work-inspector]'), discuss = inspector.locator('[data-work-discuss]');
  await expect(discuss).toBeVisible(); await discuss.focus();
  const oldReads = f.reads.length; f.emit(); await expect.poll(() => f.reads.length).toBeGreaterThan(oldReads + 5);
  await page.waitForTimeout(500); await expect(discuss).toBeFocused();
  await inspector.getByRole('button', {name: 'More actions', exact: true}).click(); await expect(inspector.getByRole('menu')).toBeVisible();
  const old = f.reads.length; f.emit(); await expect.poll(() => f.reads.length).toBeGreaterThan(old + 5);
  await page.waitForTimeout(500); await expect(inspector.getByRole('menu')).toBeVisible(); await expect(inspector.getByRole('menuitem').first()).toBeFocused();
  expect(f.writes).toEqual([]);
});

test('B4 M5 Board cards expose list semantics and described facts', async ({page}) => {
  const f = reviewFixture(); await f.mount(page, 'board');
  const card = page.locator(`[data-pm-kanban-work="${f.ids.work.audit}"]`);
  await expect(card).toHaveAttribute('role', 'listitem');
  const title = card.getByRole('button', {name: 'Audit log', exact: true});
  await expect(title).toHaveAttribute('aria-describedby', /.+/);
  await expect(title).toHaveAccessibleDescription(/Waiting.*audit.*blocked.*5 d/i);
  await expect(card.locator('.pm-board-blocker')).not.toHaveAttribute('tabindex', '0');
  await title.focus(); await page.keyboard.press('Enter'); await expect(page.locator('[data-work-inspector]')).toBeVisible();
  await page.keyboard.press('Escape'); await expect(title).toBeFocused();
  await page.keyboard.press('ArrowRight'); await expect(title).not.toBeFocused();
  await expect(page.locator('.pm-kanban-column > header h2')).toHaveCount(5);
});

test('S2 Board lane counts describe loaded cards; V9 empty hides lanes', async ({page}) => {
  const f = reviewFixture(); let empty = false;
  f.overrides.push((url, r) => {
    if (url.pathname.endsWith('/kanban')) {r.data.next_cursor = empty ? null : 12; if (empty) {r.data.cards = []; r.data.wip = {count: 0, unknown: 0, cards: 0, scope: 'returned_page'};}}
    return r;
  });
  await f.mount(page, 'board');
  await expect(page.locator('.pm-kanban-count').first()).toHaveText(/loaded/);
  await expect(page.locator('.pm-kanban-count').first()).toHaveAttribute('aria-label', /more may exist/);
  await expect(page.locator('[data-board-partial]')).toBeVisible();
  empty = true; await page.locator('[data-pm-refresh]').click();
  await expect(page.locator('[data-pm-kanban-column]:visible')).toHaveCount(0);
  await expect(page.locator('[data-pm-kanban] .ui-empty')).toHaveCount(1);
});

for (const source of ['session', 'delivery', 'reviews', 'detail']) for (const view of ['requirements', 'board']) test(`S1 ${view} isolates ${source} withdrawal`, async ({page}) => {
  const f = reviewFixture(); let failed = false;
  f.overrides.push((url, r) => {
    if (failed && (source === 'session' && url.pathname.startsWith('/sessions/') && !url.pathname.endsWith('/messages')
      || source === 'delivery' && url.pathname.endsWith('/deliveries') && url.pathname.includes(f.ids.work.email)
      || source === 'reviews' && url.pathname.endsWith('/quality-reviews')
      || source === 'detail' && url.pathname.endsWith('/kanban/' + f.ids.work.email))) return {status: source === 'reviews' ? 413 : 403, data: {error: {code: 'PROBE_WITHDRAWN', message: 'Private raw code'}}};
    return r;
  });
  await f.mount(page, view);
  const opener = view === 'board' ? page.locator(`[data-board-open="${f.ids.work.email}"]`) : page.locator(`[data-rtm-select="${f.ids.work.email}"]`);
  if (view === 'requirements') await page.locator(`[data-rtm-toggle="${f.ids.requirements.email}"]`).click();
  await opener.click(); await expect(page.locator('[data-rtm-pr="87"]')).toBeVisible();
  failed = true; await page.locator('[data-pm-refresh]').click();
  await expect(page.locator(view === 'board' ? '[data-pm-kanban-work]' : '[data-rtm-requirement]')).toHaveCount(view === 'board' ? source === 'detail' ? 11 : 12 : 8);
  if (source === 'detail') {
    // A detail 403 is authoritative withdrawal of this one work, not the whole view.
    await expect(page.locator('[data-work-inspector]')).not.toContainText('PR #87');
    await expect(page.locator('[data-pm-work-meta]')).toContainText('loaded');
    if (view === 'requirements') await expect(page.locator(`[data-rtm-requirement="${f.ids.requirements.email}"] [data-column=work]`)).toHaveText('0 loaded');
    else await expect(page.locator('.pm-kanban-count').first()).toContainText('loaded');
  } else if (source === 'delivery') {
    await expect(page.locator('[data-work-section=delivery]')).toContainText('Delivery unknown');
    for (const node of await page.locator((view === 'board' ? '[data-pm-kanban]' : '[data-pm-rtm]') + ', [data-work-inspector]').all()) await expect(node).not.toContainText('PR #87');
  } else if (source === 'reviews' && view === 'requirements') {
    await expect(page.locator(`[data-rtm-requirement="${f.ids.requirements.email}"] [data-column=acceptance] [data-status=unknown]`)).toHaveCount(1);
    await expect(page.locator('[data-rtm-requirement]')).toHaveCount(8);
  } else if (source === 'session') {
    await expect(page.locator('[data-work-section=status]')).toContainText('Session unavailable');
    await expect(page.locator('[data-work-section=status] a[href^="#/session/"]')).toHaveCount(0);
  }
  for (const node of await page.locator('[data-pm-rtm] > [role=status], [data-pm-kanban] > [role=status]').all()) await expect(node).not.toContainText('PROBE_WITHDRAWN');
  expect(f.writes).toEqual([]);
});

test('B2 S3 requirement inspector shows criteria, work, review, decisions and delivery', async ({page}) => {
  const f = reviewFixture();
  f.overrides.push((url, r) => {
    if (url.pathname.endsWith('/register')) {r.data.rows = [{row_id: 'decision.review', type: 'decision', title: 'Approve sign-in', related: {requirement_ids: [f.ids.requirements.email]}}]; r.data.next_cursor = null; r.data.coverage.complete = true;}
    return r;
  });
  await f.mount(page, 'requirements', f.ids.requirements.email);
  const inspector = page.locator('[data-work-inspector]');
  await expect(inspector.locator('[data-work-section]')).toHaveCount(6);
  expect(await inspector.locator('[data-work-section]').evaluateAll(nodes => nodes.map(node => node.getAttribute('data-work-section')))).toEqual(['criteria', 'work', 'result', 'decisions', 'delivery', 'details']);
  await expect(inspector.locator('[data-work-section=criteria] [data-status=accepted]')).toHaveCount(2);
  await expect(inspector.locator('[data-work-section=result]')).toContainText('Quality review');
  await expect(inspector.locator('[data-work-section=result]')).not.toContainText('No result recorded');
  await expect(inspector.getByRole('link', {name: 'Approve sign-in'})).toHaveAttribute('href', /issues\?select=decision.review/);
  await expect(inspector.locator('[data-work-section=delivery]')).toContainText('PR #87');
  await expect(inspector.locator('[data-work-section=delivery] > [data-status=partial]')).toHaveCount(1);
  await inspector.getByRole('button', {name: 'Email sign-in', exact: true}).click();
  await expect(inspector.locator('[data-work-section=status]')).toBeVisible();
});

for (const view of ['requirements', 'board']) test(`S4 ${view} work details clicks through the existing item route`, async ({page}) => {
  const f = reviewFixture();
  f.overrides.push((url, r) => url.pathname === '/work-items/' + f.ids.work.email ? {status: 200, data: {project: {project_id: f.ids.projectId, name: 'Member sign-in', archived: false}, work_item: {work_item_id: f.ids.work.email, project_id: f.ids.projectId, title: 'Email sign-in', state: 'done', version: 1, archived: false}, children: [], links: [], steps: [], history: [], breadcrumbs: []}} : r);
  await f.mount(page, view, f.ids.work.email);
  if (view === 'board') await page.locator(`[data-board-open="${f.ids.work.email}"]`).click();
  await page.locator('[data-work-inspector]').getByRole('link', {name: 'Open work details'}).click();
  await expect(page).toHaveURL(new RegExp('#/item/' + f.ids.work.email));
  await expect(page.getByRole('heading', {name: 'Email sign-in', exact: true}).first()).toBeVisible();
  await expect.poll(() => f.reads.includes('/work-items/' + f.ids.work.email)).toBe(true);
  expect(f.reads).not.toContain('/projects/' + f.ids.work.email);
});

for (const view of ['requirements', 'board']) test(`S5 M7 ${view} changes opener, persists selection and restores it on return`, async ({page}) => {
  const f = reviewFixture(); await f.mount(page, view);
  const open = async (id: string) => {
    if (view === 'requirements') {
      const req = id === f.ids.work.email ? f.ids.requirements.email : f.ids.requirements.audit;
      const toggle = page.locator(`[data-rtm-toggle="${req}"]`); if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click();
      await page.locator(`[data-rtm-select="${id}"]`).click();
    } else await page.locator(`[data-board-open="${id}"]`).click();
  };
  await open(f.ids.work.email); await open(f.ids.work.audit);
  await expect(page).toHaveURL(new RegExp('select=' + f.ids.work.audit));
  await page.keyboard.press('Escape');
  await expect(page.locator(view === 'board' ? `[data-board-open="${f.ids.work.audit}"]` : `[data-rtm-select="${f.ids.work.audit}"]`)).toBeFocused();
  await open(f.ids.work.email);
  await page.locator('[data-work-discuss]').click();
  await page.getByRole('link', {name: view === 'board' ? 'Back to Board' : 'Back to Requirements'}).click();
  await expect(page.locator('[data-work-inspector] > header h2')).toHaveText('Email sign-in');
  await page.keyboard.press('Escape');
  await expect(page.locator(view === 'board' ? `[data-board-open="${f.ids.work.email}"]` : `[data-rtm-select="${f.ids.work.email}"]`)).toBeFocused();
});

test('S7 Requirements resolves milestones and clears unknown selection through Back', async ({page}) => {
  const f = reviewFixture(); await f.mount(page, 'requirements', f.ids.requirements.email);
  await expect(page.locator('[data-work-inspector]')).toBeVisible();
  await page.evaluate(hash => {location.hash = hash;}, `#/pm/${f.ids.workspaceId}/requirements?select=missing`);
  await expect(page.locator('[data-work-inspector]')).toHaveCount(0);
  await expect(page.locator('[data-rtm-requirement][aria-selected=true]')).toHaveCount(0);
  await expect(page.locator('[data-pm-rtm] > [role=status]')).toContainText('Not in the current plan');
  await page.goBack(); await expect(page.locator('[data-work-inspector]')).toBeVisible();
  await page.evaluate(hash => {location.hash = hash;}, `#/pm/${f.ids.workspaceId}/requirements?select=milestone:design_review`);
  await expect(page.locator('[data-work-inspector]')).toHaveCount(0);
  await expect(page.locator('.pm-requirement-group:focus')).toHaveText('Design review');
  const search = page.locator('[data-pm-rtm] input[type=search]'); await search.fill('Email');
  let before = f.reads.length; f.emit();
  await expect.poll(() => f.reads.slice(before).some(path => path.includes('/kanban?'))).toBe(true);
  await expect(page.locator('[data-pm-rtm] > .actions > button').first()).toBeEnabled();
  await expect(search).toHaveValue('Email'); await expect(search).toBeFocused();
  const toggle = page.locator(`[data-rtm-toggle="${f.ids.requirements.email}"]`);
  if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click();
  await page.locator('.pm-requirement-group').filter({hasText: 'Design review'}).focus();
  f.overrides.push((url, r) => {if (url.pathname.endsWith('/kanban')) r.data.cards.find((card: any) => card.work_item_id === f.ids.work.email).work.title = 'Email sign-in updated'; return r;});
  before = f.reads.length; f.emit();
  await expect(page.locator(`[data-rtm-select="${f.ids.work.email}"]`)).toHaveText('Email sign-in updated');
  await expect(page.locator('.pm-requirement-group:focus')).toHaveText('Design review');
  expect(f.writes).toEqual([]);
});

test('M1 hidden Board does not fan out on a Requirements event', async ({page}) => {
  const f = reviewFixture(); await f.mount(page, 'requirements'); await expect(page.locator('[data-rtm-requirement]')).toHaveCount(8);
  await page.waitForTimeout(1200); const before = f.reads.length; f.emit(); await expect.poll(() => f.reads.slice(before).filter(path => path.includes('/deliveries')).length).toBeGreaterThan(0);
  await page.waitForTimeout(600);
  expect(f.reads.slice(before).filter(path => /\/kanban\?/.test(path))).toHaveLength(1);
  expect(f.reads.slice(before).filter(path => path.includes('/deliveries'))).toHaveLength(12);
});

test('M2 Board retains all selected delivery pages after live refresh', async ({page}) => {
  const f = reviewFixture();
  f.overrides.push(async (url, r) => {
    if (url.pathname.endsWith('/deliveries') && url.pathname.includes(f.ids.work.email)) {
      const rows = (await f.base.dispatch({method: 'GET', path: url.pathname + '?after=0&limit=20'})).data.deliveries; r.data.deliveries = Number(url.searchParams.get('after')) ? rows.filter((row: any) => row.target.pull_number === 87) : rows.filter((row: any) => row.target.pull_number === 91);
      r.data.next_after = Number(url.searchParams.get('after')) ? null : 1;
    } return r;
  });
  await f.mount(page, 'board'); await page.locator(`[data-board-open="${f.ids.work.email}"]`).click();
  await page.locator('[data-work-section=delivery]').locator('[data-delivery-more]').click();
  await expect(page.locator('[data-rtm-pr]')).toHaveCount(2);
  const old = f.reads.length; f.emit(); await expect.poll(() => f.reads.length).toBeGreaterThan(old + 5); await page.waitForTimeout(500);
  await expect(page.locator('[data-rtm-pr]')).toHaveCount(2);
  await expect(page.locator('[data-work-section=delivery]').locator('[data-delivery-more]')).toHaveCount(0);
});

test('M6 earlier-plan work names the revision without leaking IDs into chips or options', async ({page}) => {
  const f = reviewFixture();
  f.overrides.push((url, r) => {
    if (url.pathname.includes('/kanban')) for (const card of r.data.cards || [r.data.card].filter(Boolean)) if (card.work_item_id === f.ids.work.email) card.binding.plan_revision_id = f.ids.planV2;
    return r;
  });
  await f.mount(page, 'board');
  const card = page.locator(`[data-pm-kanban-work="${f.ids.work.email}"]`);
  await expect(card.locator('.pm-board-requirements')).toContainText('Earlier plan');
  await expect(card.locator('.pm-board-requirements')).not.toContainText(f.ids.requirements.email);
  await expect(page.locator('.pm-board-toolbar select').first().locator(`option[value="${f.ids.requirements.email}"]`)).not.toHaveText(f.ids.requirements.email);
  await card.locator('[data-board-open]').click();
  await expect(page.locator('[data-work-section=requirements]')).toContainText('From an earlier plan');
  await expect(page.locator('[data-work-section=requirements]')).toContainText(f.copy.requirements[0].text);
  await page.locator('[data-pm-view=requirements]').click();
  await expect(page.locator(`[data-rtm-work="${f.ids.work.email}"]`)).toContainText('From an earlier plan');
});

test('M8 Board requirement chips and inspector link to Requirements; work links back to Board', async ({page}) => {
  const f = reviewFixture(); await f.mount(page, 'board');
  const card = page.locator(`[data-pm-kanban-work="${f.ids.work.email}"]`);
  const link = card.locator('.pm-board-requirements a');
  await expect(link).toHaveAttribute('href', new RegExp('requirements\\?select=' + f.ids.requirements.email));
  await card.locator('[data-board-open]').click();
  await expect(page.locator('[data-work-section=requirements] a[href*=requirements]')).toHaveCount(1);
  await page.locator('[data-work-section=requirements] a[href*=requirements]').click();
  await expect(page.locator('[data-work-section=criteria]')).toBeVisible();
  await page.locator('[data-work-section=work]').getByRole('button', {name: 'Email sign-in', exact: true}).click();
  await page.locator('[data-work-inspector]').getByRole('link', {name: 'Show on Board'}).click();
  await expect(page.locator('[data-pm-view=board]')).toHaveAttribute('aria-current', 'page');
  await expect(page.locator('[data-work-inspector] > header h2')).toHaveText('Email sign-in');
});

for (const locale of ['en', 'zh-TW']) test(`V1 V2 V3 V4 V5 M9 inspector concise copy ${locale}`, async ({page}) => {
  const f = reviewFixture(locale); await f.mount(page, 'requirements', f.ids.work.email);
  const inspector = page.locator('[data-work-inspector]');
  await expect(inspector.locator('[data-work-section=delivery]')).not.toContainText('— – —');
  await expect(inspector.locator('[data-work-section=delivery]')).not.toContainText('CI');
  await expect(inspector.locator('[data-work-discuss]')).toHaveCount(1);
  await expect(inspector.locator('[data-work-section=blocker] a')).toHaveCount(0);
  await expect(inspector.locator('[data-work-attempt]').first()).toContainText(locale === 'en' ? 'Send not confirmed' : '受理情況未知');
  await expect(inspector.locator('[data-work-section=result]')).toContainText(locale === 'en' ? 'Definition of done not assessed' : '完成定義尚未評估');
  await expect(inspector.locator('[data-work-section=status]')).not.toContainText(locale === 'en' ? 'Content approved' : '內容已核准');
  await expect(page.locator('[data-pm-rtm] > .ui-disclosure')).not.toContainText(locale === 'en' ? 'requirement acceptance' : '需求已通過驗收');
  await expect(page.locator(`[data-rtm-work="${f.ids.work.unlock}"]`)).not.toContainText('×0');
  await page.keyboard.press('Escape');
  await page.evaluate(hash => {location.hash = hash;}, `#/pm/${f.ids.workspaceId}/requirements?select=${f.ids.work.audit}`);
  const detail = page.locator('[data-work-section=details]'); await detail.locator('summary').click();
  await expect(detail.locator('p').filter({hasText: locale === 'en' ? 'Work age' : '進行時間'})).toHaveText(locale === 'en' ? 'Work age: 5 d' : '進行時間: 5 天');
});

for (const locale of ['en', 'zh-TW']) test(`V6 V8 compact facts are labelled and selected work is visible ${locale}`, async ({page}) => {
  const f = reviewFixture(locale); await page.setViewportSize({width: 390, height: 844});
  await f.mount(page, 'requirements');
  const row = page.locator(`[data-rtm-requirement="${f.ids.requirements.email}"]`);
  await expect(row.locator('.pm-requirement-facts')).toContainText(locale === 'en' ? 'Acceptance: Accepted' : '驗收: 已驗收');
  await expect(row.locator('.pm-requirement-facts')).toContainText(locale === 'en' ? 'Deploy: Partial' : '部署: 部分');
  await expect(row.locator('.pm-requirement-facts')).toContainText(locale === 'en' ? '1 work item' : '1 項工作');
  expect(await row.locator('.pm-requirement-facts > *').evaluateAll(nodes => nodes.every(node => getComputedStyle(node, '::before').content !== '"·"'))).toBe(true);
  await row.click(); const work = page.locator(`[data-rtm-select="${f.ids.work.email}"]`); await work.click();
  await page.keyboard.press('Escape');
  // Keep the work selected while the inspector is docked at the wide breakpoint.
  await page.setViewportSize({width: 1440, height: 900}); await work.click();
  await expect(work).toHaveAttribute('aria-pressed', 'true');
  await expect.poll(() => work.evaluate(node => getComputedStyle(node).backgroundColor)).not.toBe('rgba(0, 0, 0, 0)');
});

for (const locale of ['en', 'zh-TW']) test(`V7 docked Board reveals the selected card ${locale}`, async ({page}) => {
  const f = reviewFixture(locale); await page.setViewportSize({width: 1000, height: 900}); await f.mount(page, 'board');
  const card = page.locator(`[data-pm-kanban-work="${f.ids.work.audit}"]`); await page.getByRole('button', {name: f.copy.work.audit, exact: true}).click();
  await expect(page.locator('[data-work-inspector]')).toBeVisible();
  await page.setViewportSize({width: 1280, height: 900});
  await expect(page.locator('[data-work-inspector]')).toHaveAttribute('role', 'complementary');
  await expect.poll(() => card.evaluate(node => {
    const board = node.closest('.pm-kanban-list')!.getBoundingClientRect(), box = node.getBoundingClientRect();
    return box.left >= board.left - 1 && box.right <= board.right + 1;
  })).toBe(true);
});

test('V4 paged sources expose short load buttons with descriptive names', async ({page}) => {
  const f = reviewFixture();
  f.overrides.push((url, r) => {
    if (url.pathname.endsWith('/quality-reviews')) r.data.next_cursor = 3;
    if (url.pathname.endsWith('/issue-mappings')) r.data.next_cursor = 6;
    if (url.pathname.endsWith('/deliveries') && url.pathname.includes(f.ids.work.email)) r.data.next_after = 2;
    return r;
  });
  await f.mount(page, 'requirements', f.ids.work.email);
  for (const name of ['More quality records', 'More issue links', 'More delivery records']) {
    const button = page.getByRole('button', {name, exact: true}); await expect(button).toHaveText('Load more');
  }
});

for (const view of ['requirements', 'board']) test(`S5 ${view} withdrawal clears the route and focuses the heading`, async ({page}) => {
  const f = reviewFixture(); let failed = false;
  f.overrides.push((url, r) => failed && (url.pathname.endsWith('/plans/accepted') || url.pathname.endsWith('/kanban'))
    ? {status: 403, data: {error: {code: 'FORBIDDEN', message: 'Withdrawn'}}} : r);
  await f.mount(page, view, f.ids.work.email);
  if (view === 'board') await page.getByRole('button', {name: 'Email sign-in', exact: true}).click();
  await expect(page.locator('[data-work-inspector]')).toBeVisible();
  failed = true; await page.locator('[data-pm-refresh]').click();
  await expect(page.locator('[data-work-inspector]')).toHaveCount(0);
  await expect(page.locator('[data-pm-work-meta]')).toBeEmpty();
  await expect(page.getByRole('heading', {level: 1, name: view === 'board' ? 'Board' : 'Requirements', exact: true})).toBeFocused();
  await expect(page).not.toHaveURL(/select=/); expect(f.writes).toEqual([]);
});

for (const view of ['requirements', 'board']) test(`S1 ${view} snapshot conflicts use concise status and recover`, async ({page}) => {
  const f = reviewFixture(); let failed = false;
  f.overrides.push((url, r) => failed && url.pathname.endsWith('/kanban')
    ? {status: 409, data: {error: {code: 'SNAPSHOT_CHANGED', message: 'Private raw journal error'}}} : r);
  await f.mount(page, view); await expect(page.locator(view === 'board' ? '[data-pm-kanban-work]' : '[data-rtm-requirement]')).toHaveCount(view === 'board' ? 12 : 8);
  failed = true; await page.locator('[data-pm-refresh]').click();
  const status = page.locator(view === 'board' ? '[data-pm-kanban] > [role=status]' : '[data-pm-rtm] > [role=status]');
  await expect(status.locator('p').first()).not.toContainText('SNAPSHOT_CHANGED');
  await expect(status.locator('p').first()).not.toContainText('Private raw');
  await expect(status.locator('details')).toContainText('SNAPSHOT_CHANGED');
  failed = false; await status.getByRole('button', {name: /^Retry/}).click();
  await expect(page.locator(view === 'board' ? '[data-pm-kanban-work]' : '[data-rtm-requirement]')).toHaveCount(view === 'board' ? 12 : 8);
  expect(f.writes).toEqual([]);
});

test('S3 saved evidence remains visible without a qualifying review', async ({page}) => {
  const f = reviewFixture(); f.overrides.push((url, r) => {if (url.pathname.endsWith('/quality-reviews')) r.data.reviews = []; return r;});
  await f.mount(page, 'requirements', f.ids.requirements.email);
  await expect(page.locator('[data-work-section=criteria] > [data-status=not_assessed]')).toBeVisible();
  await expect(page.locator('[data-requirement-capture]')).toContainText('Email sign-in');
  await expect(page.locator('[data-requirement-capture]')).toContainText('Verification · Passed');
  await expect(page.locator('[data-work-section=result]')).not.toContainText('No result recorded');
  expect(f.writes).toEqual([]);
});

for (const locale of ['en', 'zh-TW']) test(`B2 B3 compact rows and requirement inspector keep partial and unknown ${locale}`, async ({page}) => {
  const f = reviewFixture(locale); let absent = false;
  f.overrides.push((url, r) => {
    if (absent && ['/bootstrap', '/capabilities'].includes(url.pathname)) {
      const caps = url.pathname === '/bootstrap' ? r.data.capabilities : r.data;
      caps.features.pm_workspaces.quality_reviews = false; caps.features.pm_workspaces.engineering_delivery = false;
    }
    if (url.pathname.endsWith('/quality-reviews')) {r.data.reviews = []; r.data.next_cursor = 3;}
    if (url.pathname.endsWith('/deliveries') && url.pathname.includes(f.ids.work.email)) {r.data.deliveries = r.data.deliveries.filter((row: any) => row.target.pull_number === 91); r.data.next_after = 2;}
    return r;
  });
  await page.setViewportSize({width: 390, height: 900}); await f.mount(page, 'requirements');
  const row = page.locator(`[data-rtm-requirement="${f.ids.requirements.email}"]`), facts = row.locator('.pm-requirement-facts');
  await expect(facts).toContainText(locale === 'en' ? 'Acceptance: Unknown' : '驗收: 未知');
  await expect(facts).toContainText(locale === 'en' ? 'Deploy: Partial' : '部署: 部分');
  await expect(facts).toContainText(locale === 'en' ? '1 PR loaded' : '已載入 1 個 PR');
  await expect(page.locator('[data-pm-work-meta]')).toHaveText(locale === 'en' ? '8 loaded' : '已載入 8 筆');
  await row.click(); await page.getByRole('button', {name: locale === 'en' ? 'Open requirement' : '開啟需求', exact: true}).click();
  await expect(page.locator('[data-work-section=criteria] > [data-status=unknown]')).toBeVisible();
  await expect(page.locator('[data-work-section=delivery] > [data-status=partial]')).toBeVisible();
  await page.keyboard.press('Escape'); absent = true; await page.reload();
  await expect(facts).toContainText(locale === 'en' ? 'Deploy: Unknown' : '部署: 未知');
  await expect(facts).toContainText(locale === 'en' ? 'PRs unknown' : 'PR 未知');
  await expect(facts).not.toContainText(locale === 'en' ? '0 PRs' : '0 個 PR');
  expect(f.writes).toEqual([]);
});

for (const locale of ['en', 'zh-TW']) test(`S3 criteria show the current recorded rejection without claiming acceptance ${locale}`, async ({page}) => {
  const f = reviewFixture(locale);
  f.overrides.push((url, r) => {
    if (url.pathname.endsWith('/quality-reviews')) {
      const review = r.data.reviews[0]; review.qualified = false; review.assessment.document.qualified = false;
      review.assessment.document.criteria[0].status = 'rejected';
    } return r;
  });
  await f.mount(page, 'requirements', f.ids.requirements.email);
  const criteria = page.locator('[data-work-section=criteria]');
  await expect(criteria.locator('> [data-status=not_assessed]')).toBeVisible();
  await expect(criteria.locator('li [data-quality-result=rejected]')).toHaveText(locale === 'en' ? 'Rejected' : '不符合');
  await expect(criteria.locator('[data-status=accepted]')).toHaveCount(0);
  expect(f.writes).toEqual([]);
});
