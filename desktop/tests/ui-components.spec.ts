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
  const expected = locale === 'en' ? ['Not started','In progress','Waiting','In review','Accepted','Blocked','Done','Merged','Deployed','Rolled back','Unconfirmed','Unknown','Not assessed','Partial','Stale','Read-only','Draft','Adopted','Historical']
    : ['未開始','進行中','等待中','審閱中','已驗收','受阻','完成','已合併','已部署','已回復','待確認','未知','未評估','部分','過期','唯讀','草稿','已採用','歷史'];
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

test('progress segments retain blocker and warning tones', async ({page}) => {
  await mount(page);
  await page.evaluate(async () => {
    const {metric} = await import('/src/ui/components.js');
    document.getElementById('fixture')!.append(metric({label: 'Progress', value: '3 of 8 accepted', bar: [{tone: 'bad', value: 1}, {tone: 'warn', value: 2}]}));
  });
  await expect(page.locator('.ui-metric-bar .tone-bad')).toHaveCount(1);
  await expect(page.locator('.ui-metric-bar .tone-warn')).toHaveCount(1);
});

test('shared disclosure can retain existing lazy content and listeners', async ({page}) => {
  await mount(page);
  await page.evaluate(async () => {
    const {disclosure} = await import('/src/ui/components.js');
    const {h} = await import('/src/ui/dom.js');
    const input = h('input', {'aria-label': 'Retained draft'}); input.value = 'Keep this draft';
    const existing = h('details', {'data-existing-disclosure': '', open: true}, h('summary', {}, 'Existing tools'), input);
    existing.addEventListener('toggle', () => existing.dataset.openState = String(existing.open));
    document.getElementById('fixture')!.append(disclosure({element: existing}));
    disclosure({element: existing});
  });
  const disclosure = page.locator('[data-existing-disclosure]');
  await expect(disclosure.getByLabel('Retained draft')).toHaveValue('Keep this draft');
  await expect(disclosure.locator(':scope > summary > svg')).toHaveCount(1);
  await disclosure.locator(':scope > summary').press('Enter');
  await expect(disclosure).toHaveAttribute('data-open-state', 'false');
  await disclosure.locator(':scope > summary').press('Enter');
  await expect(disclosure.getByLabel('Retained draft')).toHaveValue('Keep this draft');
});

test('inspector accepts an explicit return target when its opener becomes hidden', async ({page}) => {
  await mount(page);
  await page.evaluate(async () => {
    const ui = await import('/src/ui/components.js');
    const heading = document.createElement('h2'); heading.textContent = 'Destination'; heading.tabIndex = -1; document.body.append(heading);
    const opener = document.querySelector('button')!; opener.focus();
    const panel = ui.inspector({title: 'Related', returnFocus: heading}); opener.hidden = true; document.body.append(panel);
  });
  await expect(page.locator('.ui-inspector')).toBeVisible(); await page.keyboard.press('Escape');
  await expect(page.getByRole('heading', {name: 'Destination'})).toBeFocused();
});


test('button supports a trailing icon without changing default icon order', async ({page}) => {
  await mount(page);
  await page.evaluate(async () => {
    const {button} = await import('/src/ui/components.js');
    document.getElementById('fixture')!.append(button({label: 'Report', icon: 'chevron-down', iconPosition: 'end'}));
  });
  const button = page.getByRole('button', {name: 'Report', exact: true});
  expect(await button.evaluate(node => node.firstChild?.nodeType === Node.TEXT_NODE && node.lastChild?.nodeName === 'svg')).toBe(true);
});


test('inspector resolves callback return focus after its close callback replaces a row', async ({page}) => {
  await mount(page);
  await page.evaluate(async () => {
    const ui = await import('/src/ui/components.js');
    let row = document.createElement('button'); row.textContent = 'Old row'; document.getElementById('fixture')!.append(row);
    row.focus();
    const panel = ui.inspector({title: 'Row details', returnFocus: () => row, onClose: () => {
      const next = document.createElement('button'); next.textContent = 'Updated row'; row.replaceWith(next); row = next;
    }});
    document.getElementById('fixture')!.append(panel);
  });
  await expect(page.locator('.ui-inspector')).toBeVisible(); await page.keyboard.press('Escape');
  await expect(page.getByRole('button', {name: 'Updated row', exact: true})).toBeFocused();
});

for (const form of ['element', 'callback']) for (const state of ['detached', 'unrendered', 'hidden', 'inert']) {
  test(`inspector ${form} ${state} target falls back to its visible opener`, async ({page}) => {
    await mount(page);
    await page.evaluate(async ({form, state}) => {
      const ui = await import('/src/ui/components.js');
      const opener = document.createElement('button'); opener.textContent = 'Visible opener'; document.getElementById('fixture')!.append(opener);
      const parent = document.createElement('div'), target = document.createElement('button'); target.textContent = 'Return target'; parent.append(target);
      if (state !== 'detached') document.getElementById('fixture')!.append(parent);
      if (state === 'unrendered') parent.style.display = 'none';
      if (state === 'hidden') parent.hidden = true;
      if (state === 'inert') parent.inert = true;
      opener.focus();
      document.getElementById('fixture')!.append(ui.inspector({title: 'Details', returnFocus: form === 'callback' ? () => target : target}));
    }, {form, state});
    await expect(page.locator('.ui-inspector')).toBeVisible(); await page.keyboard.press('Escape');
    await expect(page.getByRole('button', {name: 'Visible opener', exact: true})).toBeFocused();
  });
}

test('inspector does not focus a hidden fallback opener', async ({page}) => {
  await mount(page);
  await page.evaluate(async () => {
    const ui = await import('/src/ui/components.js');
    const opener = document.querySelector('button')!; opener.focus();
    document.getElementById('fixture')!.append(ui.inspector({title: 'Details', returnFocus: () => null}));
    opener.hidden = true;
  });
  await expect(page.locator('.ui-inspector')).toBeVisible(); await page.keyboard.press('Escape');
  await expect(page.locator('.ui-inspector')).toHaveCount(0);
  expect(await page.evaluate(() => document.activeElement?.closest('[hidden], [inert]') === null)).toBe(true);
});

test('tabs associate supplied panel elements without changing string panel compatibility', async ({page}) => {
  await mount(page); await page.evaluate(async () => {
    const {tabs} = await import('/src/ui/components.js'), {h} = await import('/src/ui/dom.js');
    const panels = {one: h('section', {}, 'First context'), two: h('section', {}, 'Second context')};
    const controls = tabs({label: 'Bound panels', value: 'one', options: [{value: 'one', label: 'First', panel: panels.one}, {value: 'two', label: 'Second', panel: panels.two}],
      onChange: key => {for (const [value, node] of Object.entries(panels)) node.hidden = value !== key;}});
    panels.two.hidden = true; document.getElementById('fixture')!.append(controls, ...Object.values(panels));
  });
  const first = page.getByRole('tab', {name: 'First', exact: true}), second = page.getByRole('tab', {name: 'Second', exact: true});
  await expect(page.getByRole('tabpanel', {name: 'First', exact: true})).toHaveAttribute('id', (await first.getAttribute('aria-controls'))!);
  await first.focus(); await page.keyboard.press('ArrowRight'); await expect(second).toBeFocused();
  await expect(page.getByRole('tabpanel', {name: 'Second', exact: true})).toHaveAttribute('aria-labelledby', (await second.getAttribute('id'))!);
  await expect(page.getByRole('tab', {name: 'Context', exact: true})).toHaveAttribute('aria-controls', 'context');
});
