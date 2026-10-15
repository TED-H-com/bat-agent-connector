import {test, expect} from '@playwright/test';
import {demoFixture} from './demo/index.ts';
import {mountConversation} from './conversation-fixture.ts';
import {openThreadTab, openThreadBrief} from './pm-navigation.ts';
import {pmFixture, pmRoute} from './pm-fixture.ts';

for (const locale of ['en', 'zh-TW']) {
  const zh = locale === 'zh-TW';
  for (const width of [1440, 1000, 390]) test(`a plain conversation has room and context focus returns ${locale} ${width}`, async ({browser, baseURL}) => {
    const context = await browser.newContext({baseURL, locale, viewport: {width, height: width === 390 ? 844 : 900}}), page = await context.newPage();
    const f = demoFixture({locale, discussions: true});
    f.discussion.details.get(f.ids.threadIdea).thread.context = [{kind: 'governance', id: 'pmgov_' + '1'.padStart(32, '0'), available: true, label: f.copy.blocker}];
    await mountConversation(page, false, f.dispatch); await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}`);
    await expect(page.locator('[data-discussion-composer]')).toBeEnabled();
    await expect(page.locator('[data-discussion-panel]')).toBeHidden();
    await expect(page.locator('[data-pm-plan-proposals]')).toBeHidden();
    if (width === 1440) expect((await page.locator('.pd-transcript').boundingBox())!.width).toBeGreaterThanOrEqual(760);
    await expect(page.locator('[data-discussion-back]')).toBeVisible({visible: width < 800});
    const chip = page.locator('.pd-thread-header [data-discussion-context]');
    await chip.click(); await expect(page.getByRole('tab', {name: zh ? '脈絡' : 'Context', exact: true})).toBeFocused();
    if (width === 1440) for (const panelWidth of [1000, 1440]) {
      await page.setViewportSize({width: panelWidth, height: 900});
      await expect(page.getByRole('tab', {name: zh ? '脈絡' : 'Context', exact: true})).toBeFocused();
    }
    await expect(page.locator('[data-discussion-tab=context]')).toBeVisible();
    await expect(page.getByRole('tab', {name: zh ? '會議' : 'Meeting', exact: true})).toHaveCount(0);
    await page.keyboard.press('Escape'); await expect(chip).toBeFocused();
    await page.getByRole('button', {name: zh ? '重新整理' : 'Refresh', exact: true}).click();
    await expect(page.locator('[data-discussion-composer]')).toBeEnabled(); await expect(page.locator('[data-discussion-panel]')).toBeHidden();
    expect(f.discussion.writes).toEqual([]); await context.close();
  });

  test(`search ignores dates and modes and clearing restores threads ${locale}`, async ({browser, baseURL}) => {
    const context = await browser.newContext({baseURL, locale}), page = await context.newPage(), f = demoFixture({locale, discussions: true});
    await mountConversation(page, false, f.dispatch); await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions`);
    const search = page.getByRole('searchbox', {name: zh ? '搜尋' : 'Search', exact: true});
    const date = await page.locator('.pm-thread-link time').first().innerText();
    for (const query of [date, zh ? '追蹤討論' : 'Follow-up']) {
      await search.fill(query); await expect(page.locator('.pm-thread-link:visible')).toHaveCount(0);
      await expect(page.locator('[data-discussion-search-empty]')).toHaveText(new RegExp(zh ? '沒有符合的討論' : 'No matching discussions'));
      await page.getByRole('button', {name: zh ? '清除搜尋' : 'Clear search'}).click();
      await expect(search).toHaveValue(''); await expect(page.locator('.pm-thread-link:visible')).toHaveCount(3);
    }
    const contextText = await page.locator('.pm-thread-link [data-discussion-context]').first().innerText();
    await search.fill(contextText); await expect(page.locator('.pm-thread-link:visible')).toHaveCount(1); await search.fill('');
    for (const [threadId, title] of [[f.ids.threadIdea, zh ? '想法' : 'Idea'], [f.ids.threadPlanning, zh ? '規劃草稿' : 'Planning draft']]) {
      f.discussion.threads.find((row: any) => row.thread_id === threadId).title = title;
      f.discussion.details.get(threadId).thread.title = title;
      await page.getByRole('button', {name: zh ? '重新整理' : 'Refresh', exact: true}).click();
      const thread = page.locator(`.pm-thread-link[href$="${threadId}"]`);
      await expect(thread.locator('strong')).toHaveText(title); await expect(thread.locator('.ui-pill')).toHaveCount(0);
      await thread.click(); await expect(page.locator('[data-discussion-title]')).toHaveText(title);
      await expect(page.locator('.pd-thread-header .pd-thread-meta > .ui-pill')).toBeHidden();
      await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions`);
    }
    expect(f.discussion.writes).toEqual([]); await context.close();
  });

  test(`an inline proposal keeps the panel closed and has one primary action ${locale}`, async ({browser, baseURL}) => {
    const context = await browser.newContext({baseURL, locale, viewport: {width: 1440, height: 900}}), page = await context.newPage(), f = demoFixture({locale, discussions: true});
    await mountConversation(page, false, f.dispatch); await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}`);
    await page.locator('[data-discussion-ask]').click();
    await expect(page.locator('[data-discussion-panel]')).toBeHidden();
    const card = page.locator('[data-pm-plan-proposals]'); await expect(card.locator('[data-proposal-review]')).toBeEnabled();
    await expect(card.locator('[data-proposal-adopt-panel]')).toBeHidden();
    await expect(card.getByRole('heading')).toHaveCount(1);
    await expect(page.locator('.ui-button.primary:visible')).toHaveCount(1);
    await expect(page.locator('.ui-button.primary:visible')).toHaveText(zh ? '審閱並採用' : 'Review & adopt');
    await card.locator('[data-proposal-review]').click(); await expect(card.locator('[data-proposal-adopt-panel]')).toBeVisible();
    await expect(card.locator('[data-proposal-details]')).toHaveCount(1);
    await expect(card.locator('[data-proposal-adopt-panel] > details')).toHaveCount(0);
    await expect(card.locator('[data-proposal-candidate]').first()).toContainText(f.discussion.proposals.at(-1).document.candidates[0].text);
    await expect(page.locator('.ui-button.primary:visible')).toHaveCount(1);
    await expect(page.locator('.ui-button.primary:visible')).toHaveText(zh ? '採用' : 'Adopt');
    expect(f.discussion.writes.map((w: any) => w.body.action)).toEqual(['pm.conversation.propose']);
    await context.close();
  });

  test(`saving without a PM explains that no reply will arrive ${locale}`, async ({browser, baseURL}) => {
    const context = await browser.newContext({baseURL, locale}), page = await context.newPage(), f = demoFixture({locale, discussions: true});
    await mountConversation(page, false, async input => {
      const result = await f.dispatch(input), caps = input.path === '/bootstrap' ? result.data.capabilities : input.path === '/capabilities' ? result.data : null;
      if (caps) for (const key of ['conversation_proposals', 'native_analysis', 'bat_analysis']) delete caps.features.pm_workspaces[key];
      return result;
    });
    const route = `#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}`;
    await page.goto('/dashboard/' + route);
    const message = zh ? '訊息會儲存；PM 尚未連線，不會回覆。' : 'Messages are saved. The PM is not connected and will not reply.';
    await expect(page.locator('.pd-composer')).toContainText(message);
    const setup = page.getByRole('link', {name: zh ? '設定 PM' : 'Set up PM'});
    await expect(setup).toHaveAttribute('href', '#/settings?section=agents&from=' + encodeURIComponent(route));
    await page.locator('[data-discussion-composer]').fill(zh ? '保留這則訊息。' : 'Keep this note.');
    await page.getByRole('button', {name: zh ? '傳送' : 'Send', exact: true}).click();
    const note = page.locator('.pm-note').last(); await expect(note).toContainText(zh ? '已儲存' : 'Saved');
    await expect(page.locator('[data-discussion-working]')).toHaveCount(0);
    if (zh) expect(await page.locator('[data-discussions]').innerText()).not.toContain('Agent');
    expect(f.discussion.writes.map((w: any) => w.body.action)).toEqual(['pm.message.append']);
    await setup.click(); await expect(page.locator('#main')).toBeVisible(); await context.close();
  });

  test(`a title-only Agents launch has a cosmetic context chip ${locale}`, async ({browser, baseURL}) => {
    const context = await browser.newContext({baseURL, locale}), page = await context.newPage(), f = demoFixture({locale, discussions: true});
    await mountConversation(page, false, f.dispatch);
    await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/agents`);
    await page.locator('[data-resource-row=codex-implementer]').click();
    const discuss = page.locator('[data-resource-discuss]');
    const title = await discuss.locator('xpath=ancestor::aside').locator('header h2').innerText();
    await discuss.click();
    const chip = page.locator('.pd-thread-header [data-discussion-context-title]');
    await expect(chip).toHaveText(title); await chip.click();
    await expect(page.getByRole('tab', {name: zh ? '脈絡' : 'Context', exact: true})).toBeFocused();
    await page.keyboard.press('Escape'); await expect(chip).toBeFocused();
    await page.locator('[data-discussion-composer]').fill('Keep this source name'); await page.getByRole('button', {name: zh ? '傳送' : 'Send', exact: true}).click();
    await expect(page).toHaveURL(/discussions\/pmt_/);
    expect(f.discussion.writes[0].body.params.context).toBeUndefined();
    await expect.poll(() => f.discussion.writes.map((w: any) => w.body.action)).toEqual(['pm.thread.create', 'pm.message.append']); await context.close();
  });
}

test('IME Escape preserves the rename draft while plain Escape cancels', async ({page}) => {
  const f = demoFixture({discussions: true}); await mountConversation(page, false, f.dispatch);
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}`);
  await page.getByRole('button', {name: 'Rename', exact: true}).click(); const field = page.getByLabel('Discussion title', {exact: true});
  await field.fill('尚未完成的輸入');
  for (const init of [{isComposing: true}, {keyCode: 229}]) {
    await field.evaluate((node, init) => node.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true, ...init})), init);
    await expect(field).toBeVisible(); await expect(field).toHaveValue('尚未完成的輸入');
  }
  await page.reload(); await expect(field).toHaveValue('尚未完成的輸入');
  await field.press('Escape'); await expect(field).toBeHidden(); await expect(page.getByRole('button', {name: 'Rename', exact: true})).toBeFocused();
  await page.getByRole('button', {name: 'Rename', exact: true}).click(); await expect(field).toHaveValue(f.discussion.details.get(f.ids.threadIdea).thread.title);
  expect(f.discussion.writes).toEqual([]);
});

for (const locale of ['en', 'zh-TW']) for (const native of [false, true]) test(`${native ? 'Native' : 'Web'} restored rename can cancel while initial proposal reads are pending ${locale}`, async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale}), page = await context.newPage();
  const f = demoFixture({locale, discussions: true}), zh = locale === 'zh-TW';
  let hold = false, held = false, release!: () => void;
  const response = new Promise<void>(resolve => {release = resolve;});
  try {
    await mountConversation(page, native, async input => {
      const result = await f.dispatch(input);
      if (hold && input.method === 'GET' && input.path.includes('/conversation-proposals?')) {held = true; await response;}
      return result;
    });
    await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}`);
    const rename = page.getByRole('button', {name: zh ? '重新命名' : 'Rename', exact: true});
    const field = page.getByLabel(zh ? '討論標題' : 'Discussion title', {exact: true});
    const save = page.getByRole('button', {name: zh ? '儲存' : 'Save', exact: true});
    await rename.click(); await field.fill('尚未完成的輸入');
    hold = true; await page.reload();
    await expect.poll(() => held).toBe(true);
    await expect(field).toBeVisible(); await expect(field).toHaveValue('尚未完成的輸入');
    await expect(save).toBeDisabled(); await expect(page.locator('[data-discussion-rename]')).toBeDisabled();
    await field.focus(); await expect(field).toBeFocused(); await expect(field).not.toBeEditable();
    for (const init of [{isComposing: true}, {keyCode: 229}]) {
      await field.evaluate((node, init) => node.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true, ...init})), init);
      await expect(field).toBeVisible(); await expect(field).toHaveValue('尚未完成的輸入');
    }
    await field.press('Enter'); expect(f.discussion.writes).toEqual([]);
    await field.press('Escape'); await expect(field).toBeHidden();
    release(); await expect(rename).toBeEnabled(); await expect(rename).toBeFocused();
    await expect(field).toBeHidden();
    await page.reload(); await expect(rename).toBeEnabled(); await expect(field).toBeHidden();
    await rename.click(); await expect(field).toHaveValue(f.discussion.details.get(f.ids.threadIdea).thread.title);
    expect(f.discussion.writes).toEqual([]);
  } finally {release(); await context.close();}
});

for (const width of [1440, 1000]) test(`rename receipts survive Plan and a closed panel with original-key recovery ${width}`, async ({page}) => {
  await page.setViewportSize({width, height: 900}); const f = demoFixture({discussions: true}); let lost = true;
  await mountConversation(page, false, async input => {
    const response = await f.dispatch(input);
    if (lost && input.body?.action === 'pm.thread.rename') {lost = false; return {status: 503, data: {error: {code: 'LOST_REPLY', message: 'Could not confirm the save.'}}};}
    return response;
  });
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}`);
  if (width === 1440) await openThreadTab(page, 'plan');
  await page.getByRole('button', {name: 'Rename', exact: true}).click();
  await page.getByLabel('Discussion title', {exact: true}).fill('Reviewed name'); await page.getByRole('button', {name: 'Save', exact: true}).click();
  await expect(page.getByRole('button', {name: 'Check original request', exact: true})).toBeEnabled();
  const key = f.discussion.writes[0].idempotency_key;
  await page.reload(); await expect(page.getByRole('button', {name: 'Check original request', exact: true})).toBeEnabled();
  await page.getByRole('button', {name: 'Check original request', exact: true}).click();
  await expect(page.locator('[data-discussion-details]')).not.toHaveAttribute('open', '');
  await page.locator('[data-discussion-details] > summary').click();
  await expect(page.locator('[data-discussion-details] [data-discussion-receipt=rename]')).toBeVisible();
  await expect(page.locator('[data-discussion-details] [data-discussion-receipt=rename]')).toContainText('Completed');
  await expect(page.locator('[data-discussion-title]')).toHaveText('Reviewed name');
  expect(f.discussion.writes).toHaveLength(2); expect(f.discussion.writes[1].idempotency_key).toBe(key);
  expect(f.discussion.writes[1].body).toEqual(f.discussion.writes[0].body);
});

test('Brief and charter sources read as names and times with identities in Details', async ({page}) => {
  const f = demoFixture({discussions: true}); await mountConversation(page, false, f.dispatch);
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}`); await openThreadBrief(page);
  const brief = page.locator('[data-pm-brief]'); await brief.locator('[data-pm-brief-fact]').first().locator(':scope > summary').click();
  await expect(brief.locator('[data-readable-sources]').first()).toContainText('Plan input');
  await expect(brief.locator('[data-readable-sources]').first().locator(':scope > article > .ui-meta > time')).toBeVisible();
  expect(await brief.innerText()).not.toContain('{'); expect(await brief.innerText()).not.toMatch(/pmrev_|pmi_|pmsnap_/);
  await brief.locator('[data-readable-sources]').first().locator('details > summary').click();
  await expect(brief.locator('[data-readable-sources]').first().locator('code, .mono')).toContainText([f.ids.planV3]);
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/overview`);
  const sources = page.locator('.pm-overview-sources').first(); await sources.locator(':scope > summary').click();
  await expect(sources.locator('[data-readable-sources]')).toBeVisible();
  expect(await sources.innerText()).not.toContain('{'); expect(await sources.innerText()).not.toMatch(/pmi_/);
  expect(f.discussion.writes).toEqual([]);
});

test('explicit panel choices last across inline proposals and refreshes', async ({page}) => {
  const f = demoFixture({discussions: true}); await page.setViewportSize({width: 1440, height: 900}); await mountConversation(page, false, f.dispatch);
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}`);
  await page.locator('[data-discussion-ask]').click(); await expect(page.locator('[data-proposal-review]')).toBeEnabled(); await expect(page.locator('[data-discussion-panel]')).toBeHidden();
  await openThreadTab(page, 'inputs'); await page.getByRole('button', {name: 'Refresh', exact: true}).click();
  await expect(page.locator('[data-discussion-tab=inputs]')).toBeVisible();
  // An inline conversation proposal does not override an explicit tab choice or reopen a closed panel.
  const proposal = structuredClone(f.discussion.proposals.at(-1)); proposal.proposal_id = 'pmcp_' + '8'.repeat(32); proposal.sequence++;
  f.discussion.proposals.push(proposal); await page.getByRole('button', {name: 'Refresh', exact: true}).click();
  await expect(page.locator('[data-discussion-tab=inputs]')).toBeVisible();
  await page.keyboard.press('Escape'); await page.getByRole('button', {name: 'Refresh', exact: true}).click();
  await expect(page.locator('[data-discussion-panel]')).toBeHidden();
  expect(f.discussion.writes.map((row: any) => row.body.action)).toEqual(['pm.conversation.propose']);
});

test('a message receipt remains visible with Context closed', async ({page}) => {
  await page.setViewportSize({width: 1000, height: 900}); const f = demoFixture({discussions: true}); let lost = true;
  await mountConversation(page, false, async input => {
    const result = await f.dispatch(input);
    if (lost && input.body?.action === 'pm.message.append') {lost = false; return {status: 503, data: {error: {code: 'LOST_REPLY', message: 'Could not confirm the save.'}}};}
    return result;
  });
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}`);
  await page.locator('[data-discussion-composer]').fill('Keep this original note'); await page.getByRole('button', {name: 'Send', exact: true}).click();
  await expect(page.getByRole('button', {name: 'Check original request', exact: true})).toBeEnabled();
  await page.reload(); await expect(page.locator('[data-discussion-panel]')).toBeHidden();
  await page.getByRole('button', {name: 'Check original request', exact: true}).click();
  await expect(page.locator('[data-discussion-details]')).not.toHaveAttribute('open', '');
  await page.locator('[data-discussion-details] > summary').click();
  await expect(page.locator('[data-discussion-details] [data-discussion-receipt=message]')).toContainText('Completed');
  await expect(page.locator('.pm-notes')).toContainText('Keep this original note');
  expect(f.discussion.writes).toHaveLength(2); expect(f.discussion.writes[1]).toEqual(f.discussion.writes[0]);
});

for (const mode of ['sprint_planning', 'sprint_review', 'retrospective', 'maintenance_review']) test(`only a ${mode} thread mounts Meeting in its own tab`, async ({page}) => {
  const f = demoFixture({discussions: true}); f.discussion.details.get(f.ids.threadIdea).thread.mode = mode;
  await mountConversation(page, false, async input => {
    if (input.path.includes('/sprints')) return {status: 200, data: {workspace_id: f.ids.workspaceId, sprints: [], next_cursor: null}};
    if (input.path.includes('/meetings')) return {status: 200, data: {workspace_id: f.ids.workspaceId, meetings: [], next_cursor: null}};
    const result = await f.dispatch(input), caps = input.path === '/bootstrap' ? result.data.capabilities : input.path === '/capabilities' ? result.data : null;
    if (caps) caps.features.pm_workspaces.meetings = true; return result;
  });
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}`); await openThreadTab(page, 'context');
  await expect(page.locator('[data-discussion-tab=context] [data-pm-meetings]')).toHaveCount(0);
  await page.getByRole('tab', {name: 'Meeting', exact: true}).click();
  await expect(page.locator('[data-discussion-tab=meeting] [data-pm-meetings]')).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  const tabs = await page.locator('[data-discussion-panel] [role=tab]').evaluateAll(nodes => nodes.map(node => ({label: node.textContent,
    top: Math.round(node.getBoundingClientRect().top), width: node.getBoundingClientRect().width, available: node.parentElement!.clientWidth, padding: getComputedStyle(node).paddingInline, font: getComputedStyle(node).fontSize})));
  expect(new Set(tabs.map(tab => tab.top)).size, JSON.stringify(tabs)).toBe(1);
  expect(f.discussion.writes).toEqual([]);
});

test('Follow-ups has one populated mount in Issues and a thread handoff', async ({page}) => {
  const f = demoFixture({discussions: true});
  await mountConversation(page, false, async input => {
    if (input.path.includes('/follow-ups')) return {status: 200, data: {workspace_id: f.ids.workspaceId, head_version: 0, intakes: [], intake: null, next_cursor: null}};
    const result = await f.dispatch(input), caps = input.path === '/bootstrap' ? result.data.capabilities : input.path === '/capabilities' ? result.data : null;
    if (caps) caps.features.pm_workspaces.follow_up_prepare = true; return result;
  });
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadFollow}`); await openThreadTab(page, 'context');
  await expect(page.locator('[data-pm-follow-up]')).toHaveCount(0); const handoff = page.locator('[data-discussion-follow-up]'); await expect(handoff).toHaveText('Follow-ups');
  await handoff.click(); await expect(page).toHaveURL(/\/issues$/);
  await page.locator('[data-pm-overflow]').click(); await page.locator('[data-menu-key=follow]').click();
  const card = page.locator('[data-pm-follow-up]'); await expect(card).toBeVisible(); await expect(card.getByRole('heading', {name: 'No saved preparation', exact: true})).toBeVisible();
  await expect(card.locator('[data-pm-action="pm.follow_up.prepare"]')).toHaveCount(1); expect(f.discussion.writes).toEqual([]);
});


test('a pending input request opens Inputs and recovers its original key', async ({page}) => {
  const f = pmFixture(); await mountConversation(page, false, f.dispatch); await page.goto(pmRoute); await openThreadTab(page, 'inputs');
  await page.locator('[data-pm-add-input] > summary').click(); const form = page.locator('[data-pm-action="pm.input.record"]');
  await form.getByLabel('Statement or question', {exact: true}).fill('Keep the original input'); f.state.lost = true;
  await form.getByRole('button', {name: 'Save input', exact: true}).click(); await expect(form).toContainText('Reply unavailable');
  await page.reload(); await expect(page.locator('[data-discussion-tab=inputs]')).toBeVisible();
  await expect(form.getByRole('button', {name: 'Check original request', exact: true})).toBeEnabled();
  await form.getByRole('button', {name: 'Check original request', exact: true}).click();
  expect(f.state.writes).toHaveLength(2); expect(f.state.writes[1]).toEqual(f.state.writes[0]);
  expect(f.state.inputs.filter(row => row.value === 'Keep the original input')).toHaveLength(1);
});
