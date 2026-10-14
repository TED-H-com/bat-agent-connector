import {expect, type Locator, type Page} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {join} from 'node:path';

export async function expectShellEmpty(empty: Locator, title: string, explanation: string) {
  const heading = empty.getByRole('heading', {name: title, exact: true});
  await expect(heading).toBeVisible();
  await expect(empty.locator(':scope > p')).toHaveText(explanation);
  await expect(empty.locator(':scope > p')).toBeVisible();
  expect(await empty.locator(':scope > p').evaluate(node => {
    const height = node.getBoundingClientRect().height, line = parseFloat(getComputedStyle(node).lineHeight);
    return height <= line + 1;
  })).toBe(true);
  expect(title).not.toMatch(/[.。]$/);
  if (/[\u3400-\u9fff]/.test(title)) expect(title.length).toBeLessThanOrEqual(8);
  else expect(title.split(' ').length).toBeLessThanOrEqual(6);
  expect(explanation).not.toMatch(/[\u3400-\u9fff][,:]/);
  expect(await empty.evaluate(node => {
    const title = node.querySelector('h2,h3')!, text = node.querySelector('p .muted')!;
    return Boolean(text && getComputedStyle(text).color !== getComputedStyle(title).color);
  })).toBe(true);
}

export async function shellShot(page: Page, state: string, locale: string, width: number) {
  const directory = process.env.BATC_FIX1_SHOTS;
  if (!directory) return;
  await mkdir(directory, {recursive: true});
  await page.screenshot({path: join(directory, `fix1-${state}-${locale}-${width}.png`), animations: 'disabled', fullPage: true});
}
