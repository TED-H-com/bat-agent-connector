import {test, expect} from '@playwright/test';
async function mount(page: any) {
  await page.goto('/ui-components.html');
  await page.evaluate(async () => {
    const ui = await import('/src/ui/components.js');
    const {h} = await import('/src/ui/dom.js');
    const root = document.getElementById('fixture')!;
    root.append(ui.segmented({label: 'Period', options: [{value: 'day', label: 'Day'}, {value: 'week', label: 'Week'}, {value: 'month', label: 'Month'}], value: 'day'}),
      ui.tabs({label: 'Details', options: [{value: 'context', label: 'Context', panel: 'context'}, {value: 'plan', label: 'Plan', panel: 'plan'}], value: 'context'}),
      ui.menu({button: ui.button({label: 'Actions'}), items: [{label: 'First'}, {label: 'Second'}]}),
      ui.button({label: 'Inspect', onClick: () => {const panel = ui.inspector({title: 'Work'}); panel.body.append(ui.button({label: 'Last action'})); root.append(panel);}}),
      ui.disclosure({label: 'Sources', body: h('p', {}, 'Source evidence'), key: 'test-sources'}),
      ui.button({label: 'Remount', onClick: () => {root.querySelector('details')!.replaceWith(ui.disclosure({label: 'Sources', body: 'Source evidence', key: 'test-sources'}));}}),
      ui.metric({label: 'Unknown metric'}), ui.metric({label: 'Known zero', value: 0}),
      ui.dataTable({columns: [{key: 'title', label: 'Work'}, {key: 'number', label: 'Count', numeric: true}], rows: [{id: 'one', title: 'First work', number: 0}, {id: 'two', title: 'Second work'}], rowKey: 'id', expandable: row => row.title + ' details'}));
    for (const status of Object.keys(ui.statusVocabulary)) root.append(ui.statusPill(status));
  });
}
test('segmented and tabs have roving keyboard selection', async ({page}) => {
  await mount(page);
  await page.getByRole('radio', {name: 'Day', exact: true}).focus(); await page.keyboard.press('ArrowRight');
  await expect(page.getByRole('radio', {name: 'Week'})).toBeFocused(); await expect(page.getByRole('radio', {name: 'Week'})).toHaveAttribute('aria-checked', 'true');
  await page.keyboard.press('End'); await expect(page.getByRole('radio', {name: 'Month'})).toBeFocused();
  await page.getByRole('tab', {name: 'Context'}).focus(); await page.keyboard.press('ArrowLeft');
  await expect(page.getByRole('tab', {name: 'Plan'})).toBeFocused(); await expect(page.getByRole('tab', {name: 'Plan'})).toHaveAttribute('aria-selected', 'true');
});
test('menu arrows and Escape return focus to its trigger', async ({page}) => {
  await mount(page); await page.getByRole('button', {name: 'Actions', exact: true}).click();
  await expect(page.getByRole('menuitem', {name: 'First'})).toBeFocused(); await page.keyboard.press('ArrowDown');
  await expect(page.getByRole('menuitem', {name: 'Second'})).toBeFocused(); await page.keyboard.press('Escape');
  await expect(page.getByRole('menu')).toBeHidden(); await expect(page.getByRole('button', {name: 'Actions', exact: true})).toBeFocused();
});
for (const width of [390, 900, 1440]) test(`inspector closes and returns focus at ${width}`, async ({page}) => {
  await page.setViewportSize({width, height: 900}); await mount(page); await page.getByRole('button', {name: 'Inspect', exact: true}).click();
  const panel = page.locator('.ui-inspector'); await expect(panel).toBeVisible();
  await expect(panel).toHaveAttribute('role', width < 1200 ? 'dialog' : 'complementary');
  if (width < 1200) {
    await page.getByRole('button', {name: 'Last action'}).focus(); await page.keyboard.press('Tab');
    await expect(page.getByRole('button', {name: width < 800 ? 'Back' : 'Close', exact: true})).toBeFocused();
  }
  await page.keyboard.press('Escape'); await expect(panel).toHaveCount(0); await expect(page.getByRole('button', {name: 'Inspect', exact: true})).toBeFocused();
});
test('disclosure remembers open state; table rows navigate and expand; unknown differs from zero', async ({page}) => {
  await mount(page); await page.getByText('Sources', {exact: true}).click(); await page.getByRole('button', {name: 'Remount'}).click();
  await expect(page.locator('details')).toHaveAttribute('open', ''); await expect(page.getByText('Source evidence')).toBeVisible();
  await page.locator('[data-row-key="one"]').focus(); await page.keyboard.press('ArrowDown'); await page.keyboard.press('Enter');
  await expect(page.locator('[data-row-key="two"]')).toHaveAttribute('aria-selected', 'true');
  await page.getByRole('button', {name: 'Expand Second work'}).click(); await expect(page.getByText('Second work details')).toBeVisible();
  await expect(page.locator('.ui-metric').first()).toContainText('—Unknown'); await expect(page.locator('.ui-metric').nth(1)).toContainText('0');
});
for (const locale of ['en', 'zh-TW']) test(`canonical vocabulary ${locale} and text-only content`, async ({browser}) => {
  const context = await browser.newContext({locale}); const page = await context.newPage(); await mount(page);
  const expected = locale === 'en' ? ['Not started','In progress','Waiting','In review','Accepted','Blocked','Done','Merged','Deployed','Unknown','Not assessed','Partial','Stale','Read-only','Draft','Adopted','Historical']
    : ['未開始','進行中','等待中','審閱中','已驗收','受阻','完成','已合併','已部署','未知','未評估','部分','過期','唯讀','草稿','已採用','歷史'];
  await expect(page.locator('#fixture > [data-status]')).toHaveText(expected);
  await page.evaluate(async () => {const {button} = await import('/src/ui/components.js'); document.body.append(button({label: '<img src=x onerror=alert(1)>'}));});
  await expect(page.locator('img')).toHaveCount(0); await expect(page.locator('[style]')).toHaveCount(0); await context.close();
});
test('nested menu Escape closes only the menu and disabled links cannot activate', async ({page}) => {
  await mount(page); await page.getByRole('button', {name: 'Inspect', exact: true}).click();
  await page.evaluate(async () => {
    const ui = await import('/src/ui/components.js');
    document.querySelector('.ui-inspector-body')!.append(ui.menu({button: ui.button({label: 'Panel actions'}),
      items: [{label: 'Unavailable', href: '#forbidden', disabled: true}, {label: 'Available'}]}));
  });
  const trigger = page.getByRole('button', {name: 'Panel actions'});
  await trigger.focus(); await page.keyboard.press('ArrowUp'); await expect(page.getByRole('menuitem', {name: 'Available', exact: true})).toBeFocused();
  await expect(page.getByRole('menuitem', {name: 'Unavailable', exact: true})).toHaveAttribute('aria-disabled', 'true');
  await page.keyboard.press('Escape'); await expect(page.locator('.ui-inspector')).toBeVisible(); await expect(trigger).toBeFocused();
  await page.keyboard.press('Escape'); await expect(page.locator('.ui-inspector')).toHaveCount(0);
});

test('controlled table expansion restores the saved row without replacing its toggle', async ({page}) => {
  await mount(page);
  await page.evaluate(async () => {
    const {dataTable} = await import('/src/ui/components.js');
    document.querySelector('.ui-data-table')!.replaceWith(dataTable({columns: [{key: 'title', label: 'Work'}],
      rows: [{id: 'saved', title: 'Saved work'}], rowKey: 'id', expanded: new Set(['saved']), expandable: () => 'Saved criteria',
      onToggle: (key, open) => {document.body.dataset.expansion = JSON.stringify([key, open]);}}));
  });
  const toggle = page.locator('[data-row-key=saved] button'); await expect(page.getByText('Saved criteria')).toBeVisible();
  await toggle.focus(); await page.keyboard.press('Enter'); await expect(toggle).toBeFocused();
  await expect(page.locator('body')).toHaveAttribute('data-expansion', '["saved",false]');
});
