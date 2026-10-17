import {test,expect} from '@playwright/test';
import {followUpFixture,openWork} from './work-next-turn-fixture.ts';

for(const native of [false,true])for(const locale of ['en','zh-TW']){
  test(`${native?'native':'web'} ${locale}: Work Result keeps session tools available in one disclosure`,async({page},info)=>{
    const zh=locale==='zh-TW';
    await page.setViewportSize({width:zh?390:1440,height:900});
    const f=followUpFixture(locale,{native});await openWork(page,f,locale);
    await page.locator('[data-follow-up-input]').fill('Keep this Work draft');
    await page.getByRole('button',{name:zh?'成果':'Results',exact:true}).click();
    await expect(page.locator('[data-work-result]')).toBeVisible();
    const tools=page.locator('[data-work-result-tools]');
    await expect(tools).toBeVisible();
    await expect(tools).not.toHaveAttribute('open');
    await expect(page.locator('[data-session-sources]')).toBeHidden();
    await tools.locator(':scope > summary').click();
    await expect(page.locator('[data-session-sources]')).toBeVisible();
    await expect(page.locator('[data-session-details]')).toBeVisible();
    await tools.locator(':scope > summary').click();
    await expect(page.locator('[data-work-result]')).toBeVisible();
    await expect(page.locator('[data-follow-up-input]')).toHaveValue('Keep this Work draft');
    expect(f.follow.posts).toHaveLength(0);
    await page.screenshot({path:info.outputPath('result-tools.png'),animations:'disabled'});
  });
}

test('an ordinary session retains its existing result tools',async({page})=>{
  const f=followUpFixture();await openWork(page,f);
  await page.evaluate(()=>{location.hash='#/session/demo/search-results';});
  await expect(page.locator('[data-session-view]')).toBeVisible();
  await page.getByRole('button',{name:'Results',exact:true}).click();
  await expect(page.locator('[data-work-result-tools]')).toHaveCount(0);
  await expect(page.locator('[data-session-sources]')).toBeVisible();
  expect(f.follow.posts).toHaveLength(0);
});

test('an unavailable Work Result leaves the existing tools directly accessible',async({page})=>{
  const f=followUpFixture('en',{resultStatus:404});await openWork(page,f);
  await page.getByRole('button',{name:'Results',exact:true}).click();
  await expect(page.locator('[data-work-result]')).toBeHidden();
  await expect(page.locator('[data-work-result-tools]')).toHaveCount(0);
  await expect(page.locator('[data-session-sources]')).toBeVisible();
  expect(f.follow.posts).toHaveLength(0);
});

test('a newly available Result keeps the active session tool open and focused',async({page})=>{
  const f=followUpFixture('en',{resultStatus:404});await openWork(page,f);
  await page.getByRole('button',{name:'Results',exact:true}).click();
  await page.locator('[data-session-labels] > details > summary').click();
  const draft=page.getByLabel('Labels (one per line)',{exact:true});
  await draft.fill('Preserve this session label draft');
  f.follow.resultStatus=200;f.pushEvent('work_item');
  await expect(page.locator('[data-work-result]')).toBeVisible();
  await expect(page.locator('[data-work-result-tools]')).toHaveAttribute('open','');
  await expect(draft).toBeVisible();await expect(draft).toBeFocused();
  await expect(draft).toHaveValue('Preserve this session label draft');
  expect(f.follow.posts).toHaveLength(0);
});
