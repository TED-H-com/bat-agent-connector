import {test, expect} from '@playwright/test';
import {attentionFixture, mountAttention} from './attention-fixture.ts';

for (const native of [false, true]) {
  const transport = native ? 'IPC' : 'HTTP';
  test(`${transport}: independent categories, unread pages and no passive read writes`, async ({page}) => {
    const fixture = attentionFixture(); fixture.data.pageSize = 1;
    await mountAttention(page, native, fixture); await page.goto('/dashboard/');
    const replies = page.getByRole('region', {name: 'Replies and permissions'});
    const completion = page.getByRole('region', {name: 'Completion review'});
    await expect(replies).toContainText('Question from agent'); await expect(replies).toContainText('Permission requested');
    await expect(completion).toContainText('Review delivery');
    await expect(page.getByRole('region', {name: 'Operations needing attention'})).toContainText('uncertain');
    await expect(page.getByRole('button', {name: 'To confirm', exact: true})).toHaveCount(0);
    await page.getByRole('button', {name: 'Active operations', exact: true}).click();
    await expect(page.getByRole('region', {name: 'Active operations'})).toContainText('artifact.upload');
    await expect(page.getByRole('link', {name: 'Review delivery'})).toHaveCount(0);
    await page.getByRole('button', {name: 'Unread work updates', exact: true}).click();
    const updates = page.getByRole('region', {name: 'Unread work updates'});
    await expect(updates).toContainText('1 loaded (not a total)');
    await updates.getByRole('button', {name: 'Load more'}).click();
    await expect(updates).toContainText('2 loaded (not a total)');
    await updates.getByRole('button', {name: 'Load more'}).click();
    await expect(updates).toContainText('3 loaded (not a total)');
    await expect(updates.getByRole('button', {name: 'Load more'})).toBeHidden();
    await expect(updates.getByText('your decision', {exact: true})).toHaveCount(1);
    fixture.data.cursor++;
    await expect.poll(() => fixture.data.reads.filter(p => p.includes('cursor=2')).length).toBeGreaterThan(1);
    expect(fixture.data.writes).toEqual([]);
  });

  test(`${transport}: explicit read keeps pending review and a newer version unread`, async ({page}) => {
    const fixture = attentionFixture(); let newer = true;
    await mountAttention(page, native, fixture, async input => {
      if (input.method === 'POST' && newer) {newer = false; fixture.data.items[0].version = 2;}
      return fixture.dispatch(input);
    });
    await page.goto('/dashboard/#/item/' + fixture.data.items[0].work_item_id);
    const mark = page.getByRole('button', {name: 'Mark this version read'});
    await expect(mark).toBeEnabled(); expect(fixture.data.writes).toEqual([]);
    await mark.click();
    await expect.poll(() => fixture.data.writes.length).toBe(1);
    await expect(mark).toBeEnabled(); await expect(page.locator('.reading-state')).toContainText('Unread update');
    expect(fixture.data.writes[0].body.preconditions).toEqual({expected_version: 1});
    await mark.click(); await expect(mark).toBeDisabled();
    await expect(page.locator('.reading-state')).toContainText('This version is read');
    expect(fixture.data.writes[1].body.preconditions).toEqual({expected_version: 2});
    expect(fixture.data.writes[0].idempotency_key).not.toBe(fixture.data.writes[1].idempotency_key);
    await expect(page.getByRole('button', {name: 'Accept as done'})).toBeDisabled(); // observe is not approve
    expect(fixture.data.items[0].completion.pending).toBe(true);
    await page.locator('[data-shell-pending]').click();
    await expect(page.getByRole('region', {name: 'Completion review'})).toContainText('Review delivery');
    await expect(page.getByRole('region', {name: 'Replies and permissions'})).toContainText('Question from agent');
  });

  test(`${transport}: a failed refresh retains prior rows and its event checkpoint`, async ({page}) => {
    const fixture = attentionFixture(); await mountAttention(page, native, fixture); await page.goto('/dashboard/');
    const completion = page.getByRole('region', {name: 'Completion review'});
    await expect(completion).toContainText('Review delivery');
    fixture.data.failure = '/work-items'; fixture.data.cursor = 1;
    await expect(completion).toContainText('Could not refresh.');
    await expect(completion).toContainText('Review delivery');
    await expect(page.getByText('Nothing needs you right now.')).toBeHidden();
    expect(await page.evaluate(() => Object.entries(localStorage).filter(([key]) => key.startsWith('batc.sync.')).some(([, v]) => JSON.parse(v).cursor === 1))).toBe(false);
    fixture.data.failure = ''; await expect(completion).not.toContainText('Could not refresh.');
    await expect.poll(() => page.evaluate(() => Object.entries(localStorage).filter(([key]) => key.startsWith('batc.sync.')).some(([, v]) => JSON.parse(v).cursor === 1))).toBe(true);
  });

  for (const locale of ['en-US', 'zh-TW']) for (const width of [390, 768, 1440]) {
    test(`${transport}: attention layout ${locale} ${width}`, async ({page}) => {
      const fixture = attentionFixture(); await page.setViewportSize({width, height: 900});
      await page.addInitScript(locale => Object.defineProperty(navigator, 'language', {value: locale}), locale);
      await mountAttention(page, native, fixture); await page.goto('/dashboard/');
      await expect(page.getByRole('link', {name: 'Review delivery'})).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.screenshot({path: `test-results/attention-${transport}-${locale}-${width}.png`, fullPage: true});
      await page.goto('/dashboard/#/item/' + fixture.data.items[0].work_item_id);
      await page.getByRole('button', {name: locale === 'zh-TW' ? '標記此版本已讀' : 'Mark this version read'}).click();
      await expect(page.locator('.reading-state')).toContainText(locale === 'zh-TW' ? '此版本已讀' : 'This version is read');
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.screenshot({path: `test-results/reading-${transport}-${locale}-${width}.png`, fullPage: true});
    });
  }
}

test('Web and desktop share read state while another principal remains unread', async ({browser}) => {
  const fixture = attentionFixture();
  const web = await browser.newPage(), desktop = await browser.newPage();
  try {
    await mountAttention(web, false, fixture); await mountAttention(desktop, true, fixture);
    const path = '/dashboard/#/item/' + fixture.data.items[0].work_item_id;
    await web.goto(path); await desktop.goto(path);
    await expect(desktop.locator('.reading-state')).toContainText('Unread update');
    await web.getByRole('button', {name: 'Mark this version read'}).click();
    await expect(web.locator('.reading-state')).toContainText('This version is read');
    await expect(desktop.locator('.reading-state')).toContainText('This version is read');
    await expect(desktop.getByRole('button', {name: 'Mark this version read'})).toBeDisabled();
    fixture.data.principal = 'two'; await desktop.reload();
    await expect(desktop.locator('.reading-state')).toContainText('Unread update');
    expect(fixture.data.writes).toHaveLength(1);
  } finally {await web.close(); await desktop.close();}
});

test('older central keeps the categories without offering unsupported read markers', async ({page}) => {
  const fixture = attentionFixture(); fixture.data.supported = false;
  await mountAttention(page, false, fixture); await page.goto('/dashboard/');
  await expect(page.getByRole('button', {name: 'Unread work updates', exact: true})).toHaveCount(0);
  await page.getByRole('link', {name: 'Review delivery'}).click();
  await expect(page.getByRole('button', {name: 'Mark this version read'})).toHaveCount(0);
});

test('a delayed reading response cannot repaint another view', async ({page}) => {
  const fixture = attentionFixture(), errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  let release: () => void = () => {}, requested = false;
  const blocked = new Promise<void>(resolve => {release = resolve;});
  await mountAttention(page, false, fixture, async input => {
    if (input.method === 'POST') {requested = true; await blocked;}
    return fixture.dispatch(input);
  });
  await page.goto('/dashboard/#/item/' + fixture.data.items[0].work_item_id);
  await page.getByRole('button', {name: 'Mark this version read'}).click();
  await expect.poll(() => requested).toBe(true);
  await page.locator('[data-shell-pending]').click();
  await expect(page.getByRole('heading', {name: 'Completion review'})).toBeVisible();
  release();
  await expect.poll(() => fixture.data.writes.length).toBe(1);
  await expect(page.getByRole('heading', {name: 'Completion review'})).toBeVisible();
  await expect(page.locator('.reading-state')).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('marking the displayed version read preserves an open work-item draft', async ({page}) => {
  const fixture = attentionFixture(), originalCaps = fixture.caps;
  fixture.caps = () => ({...originalCaps(), scopes: ['observe', 'manage']});
  const dispatch = async (input: any) => {
    const response = await fixture.dispatch(input);
    if (input.path === '/bootstrap') response.data.capabilities = fixture.caps();
    return response;
  };
  await mountAttention(page, false, fixture, dispatch);
  await page.goto('/dashboard/#/item/' + fixture.data.items[0].work_item_id);
  await page.getByRole('button', {name: 'More', exact: true}).first().click();
  const title = page.locator('input[maxlength="120"]').first();
  await title.fill('My unsaved draft');
  fixture.data.items[0].version = 2;
  await page.getByRole('button', {name: 'Mark this version read'}).click();
  await expect(page.locator('.reading-state')).toContainText('Central has a newer version.');
  await expect(page.getByRole('button', {name: 'Mark this version read'})).toBeDisabled();
  await expect(title).toHaveValue('My unsaved draft');
  await expect(title).toBeVisible();
  expect(fixture.data.writes).toHaveLength(1);
  expect(fixture.data.writes[0].body.preconditions.expected_version).toBe(1);
});
test('pending badge reflects only loaded attention evidence and withdraws with a forbidden source', async ({page}) => {
  const fixture=attentionFixture(); let denied=false;
  await mountAttention(page,false,fixture,async input=>{
    if(denied && input.path.startsWith('/work-items?')) return {status:403,data:{error:{code:'FORBIDDEN',message:'Source access removed'}}};
    const result=await fixture.dispatch(input);
    if(input.path.startsWith('/work-items?pending')) result.data.next_cursor='more';
    return result;
  });
  await page.goto('/dashboard/');const count=page.locator('[data-shell-pending] [data-pending-count]');
  await expect(count).toHaveText('5+'); await expect(count).toHaveAttribute('title','5 pending loaded+');
  denied=true;fixture.data.cursor++;
  await expect(page.getByRole('region',{name:'Completion review'})).not.toContainText('Review delivery');
  await expect(count).toBeHidden(); expect(fixture.data.writes).toEqual([]);
});

for(const width of [390,768,1440])test(`Pending badge follows its breakpoint and hides known zero ${width}`,async({page})=>{
  await page.setViewportSize({width,height:900});const f=attentionFixture();let empty=false;
  await mountAttention(page,false,f,async input=>empty&&['/hosts','/work-items','/sessions','/operations'].includes(input.path.split('?')[0])
    ?{status:200,data:{hosts:[],work_items:[],sessions:[],operations:[],next_cursor:null,next_before:null}}:f.dispatch(input));
  await page.goto('/dashboard/#/home');
  const badge=page.locator(width<1000?'.shell-pending [data-pending-count]':'[data-shell-pending] [data-pending-count]');
  await expect(badge).toBeVisible();await expect(badge).toHaveText('5');
  empty=true;f.data.cursor++;
  await expect(page.locator('#main')).not.toContainText('Review delivery');
  await expect(badge).toBeHidden();await expect(badge).toHaveText('');expect(f.data.writes).toEqual([]);
});
