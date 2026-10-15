import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { uiStrings } from '../src/ui/ui-strings.js';
const ROOT = process.env.TMPDIR || os.tmpdir();
const SHOTS = path.join(ROOT, 'shots'),
  VIDEO = path.join(ROOT, 'video');
const forbidden = /§|\bF[1-7]\b|after_current|ACK|Safety invariant/;
const ids = /\b(?:wi_|att_|opk_|op_|bat_sess_|wt_|ctr_|msg_|inbox_|rev_|disc_|principal_)[\w-]+/;
const english = /\b(Send to PM|Save note only|Update plan|Start work|Send next step|Adopt, do later|Adopt and start|Self-reported|Tests passed|PR open|Accepted|Merged|Deployed|Read-only|Results|Controller|Current judgment|Not recorded|Undefined|Discussion info|Change scope)\b/;
async function health(page: Page, lang: 'en' | 'zh-TW') {
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
  expect(await page.locator('[style]').count()).toBe(0);
  const body = await page.locator('body').innerText();
  expect(body).not.toMatch(/\bundefined\b|\bnull\b|\bproto\.[\w.]+|\bui_[\w]+|session_user/);
  const product = await page.getByTestId('product-shell').innerText();
  // Closed disclosures are excluded by innerText. All ordinary reading surfaces are checked.
  const visibleCopy = await page.getByTestId('product-shell').evaluate(root => {
    // innerText needs the connected layout, so use original visible text nodes.
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const texts: string[] = [];
    while (walker.nextNode()) {
      const node = walker.currentNode;
      const parent = node.parentElement;
      if (!parent || parent.closest('details')) continue;
      const range = document.createRange();
      range.selectNodeContents(node);
      if (range.getBoundingClientRect().width && parent.checkVisibility()) texts.push(node.textContent || '');
    }
    return texts.join(' ');
  });
  expect(product).not.toMatch(forbidden);
  expect(visibleCopy).not.toMatch(ids);
  if (lang === 'zh-TW') {
    expect(product).not.toMatch(english);
    expect(visibleCopy).not.toMatch(/[\u3400-\u9fff]：?[^\n]*: /);
  }
}
async function expandBar(page: Page, open: boolean) {
  const toggle = page.getByTestId('proto-expand');
  if ((await toggle.getAttribute('aria-expanded')) !== String(open)) await toggle.click();
}
async function simulate(page: Page, id: string) {
  await expandBar(page, true);
  await page.getByTestId(id).click();
  await expandBar(page, false);
}
async function workState(page: Page, value: string) {
  await expandBar(page, true);
  await page.getByTestId('work-state-picker').selectOption(value);
  await expandBar(page, false);
}
async function showResults(page: Page, width: number) {
  if (width < 1200 && !(await page.getByTestId('results-inspector').isVisible())) await page.getByTestId('inspector-drawer-toggle').click();
}
async function shot(page: Page, name: string) {
  await expandBar(page, false);
  fs.mkdirSync(SHOTS, {
    recursive: true
  });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: path.join(SHOTS, name),
    fullPage: !(await page.locator('[aria-modal=true]').count()),
    caret: "initial"
  });
}

// Evidence directories belong to this task's TMPDIR; every run replaces old evidence.
test.beforeAll(() => {
  fs.rmSync(SHOTS, {
    recursive: true,
    force: true
  });
  fs.rmSync(VIDEO, {
    recursive: true,
    force: true
  });
  fs.mkdirSync(VIDEO, {
    recursive: true
  });
});
for (const lang of ['en', 'zh-TW'] as const) for (const width of [1440, 390] as const) {
  test(`golden flow and state-specific surfaces (${lang} @ ${width})`, async ({
    browser
  }) => {
    test.setTimeout(120_000);
    const context = await browser.newContext({
      viewport: {
        width,
        height: width === 390 ? 844 : 900
      },
      locale: lang,
      ...(width === 1440 ? {
        recordVideo: {
          dir: VIDEO,
          size: {
            width: 1440,
            height: 900
          }
        }
      } : {})
    });
    const page = await context.newPage(),
      errors: string[] = [],
      requests: string[] = [];
    page.on('console', msg => {
      if (msg.type() === 'error') errors.push(msg.text());
    });
    page.on('pageerror', err => errors.push(String(err)));
    page.on('request', request => {
      if (!request.url().includes('/prototype/v1.3/')) requests.push(request.url());
    });
    const all = lang === 'en' && width === 1440 || lang === 'zh-TW' && width === 390;
    const capture = async (name: string, extra = false) => {
      await health(page, lang);
      if (all || extra) await shot(page, `${name}-${lang}-${width}.png`);
    };
    await page.goto(`/prototype/v1.3/?lang=${lang}`);
    await expect(page.locator('meta[name=robots]')).toHaveAttribute('content', 'noindex');
    await expect(page).toHaveTitle(lang === 'en' ? 'BAT Dashboard · v1.3 prototype' : 'BAT Dashboard · v1.3 互動原型');
    await expect(page.getByTestId('view-discussions')).toBeVisible();
    await expect(page.getByTestId('counter-model-calls')).toHaveAttribute('data-count', '0');
    await capture('01-idea');
    if (lang === 'en' && width === 1440) await shot(page, 'compare-discussions-en-1440.png');
    // The shell's names come from the product dictionaries in both locales.
    if (width === 390) await page.getByTestId('nav-drawer-toggle').click();
    await expect(page.locator('.pm-project-views button span')).toHaveText(['overview', 'discussions', 'requirements', 'schedule', 'dependencies', 'issues', 'agents', 'board'].map(id => uiStrings[lang][`ui_${id}` as keyof typeof uiStrings[typeof lang]]));
    await page.keyboard.press('Escape');
    const draft = await page.getByTestId('composer-textarea').inputValue();
    for (const view of ['requirements', 'schedule', 'dependencies', 'issues', 'agents', 'board']) {
      if (width === 390) await page.getByTestId('nav-drawer-toggle').click();
      await page.getByTestId(`nav-item-${view}`).click();
      await expect(page.getByTestId(`view-placeholder-${view}`)).toBeVisible();
      await health(page, lang);
      await page.getByTestId('placeholder-return-discussions').click();
      await expect(page.getByTestId('composer-textarea')).toHaveValue(draft);
    }
    await page.getByTestId('composer-save-note').click();
    await expect(page.getByTestId('note-saved-banner')).toBeVisible();
    await expect(page.getByTestId('counter-model-calls')).toHaveAttribute('data-count', '0');
    await capture('02-note-only');
    await simulate(page, 'toggle-pm-unavailable');
    await expect(page.getByTestId('pm-unavailable-diagnostic')).toBeVisible();
    await expect(page.getByTestId('composer-send-pm')).toBeDisabled();
    await capture('03-pm-unavailable');
    await page.getByTestId('fix-pm-available').click();
    await page.getByTestId('restore-pm').click();
    await expect(page.getByTestId('composer-textarea')).toHaveValue(draft);
    await page.getByTestId('composer-send-pm').click();
    await expect(page.getByTestId('counter-model-calls')).toHaveAttribute('data-count', '1');
    await expect(page.getByTestId('counter-start-requests')).toHaveAttribute('data-count', '0');
    await page.getByTestId('suggest-switch-plan').click();
    await expect(page.getByTestId('counter-start-requests')).toHaveAttribute('data-count', '0');
    await page.getByTestId('composer-update-plan').click();
    await capture('04-short-plan');
    await page.getByTestId('plan-adopt-later').click();
    await expect(page.getByTestId('plan-adopted-later-notice')).toBeVisible();
    await expect(page.getByTestId('counter-start-requests')).toHaveAttribute('data-count', '0');
    await simulate(page, 'plan-simulate-partial');
    await expect(page.getByTestId('adopt-step-s3')).toHaveAttribute('data-step-status', 'done');
    await expect(page.getByTestId('adopt-step-s4')).toHaveAttribute('data-step-status', 'failed');
    await expect(page.getByTestId('adopt-step-s5')).toHaveAttribute('data-step-status', 'pending');
    await capture('05-adopt-partial', true);
    await page.getByTestId('continue-unfinished-step').click();
    for (let i = 1; i <= 8; i++) await expect(page.getByTestId(`adopt-step-s${i}`)).toHaveAttribute('data-step-status', 'done');
    await expect(page.getByTestId('counter-start-requests')).toHaveAttribute('data-count', '1');
    await capture('06-adopt-and-start', true);
    await page.getByTestId('adopt-open-work-page').click();
    await expect(page.getByTestId('scenario-picker')).toHaveValue('running');
    await expect(page.getByTestId('work-area-results')).toHaveCount(0);
    await expect(page.getByTestId('live-stream-output')).toBeVisible();
    await capture('07-work-running', true);
    await page.getByTestId('btn-queue-second-sentence').click();
    const receipt = page.getByTestId('queued-second-sentence-receipt');
    await expect(receipt).toHaveAttribute('data-queue-policy', 'after_current');
    await expect(receipt).toHaveAttribute('data-steered', 'false');
    await expect(page.getByTestId('work-queue-box')).toHaveAttribute('data-second-steered', 'false');
    await capture('08-work-queued-second', true);
    // Every state exposes only the areas with evidence at that point.
    for (const value of ['accepted', 'preparing_worktree', 'starting', 'running', 'waiting_input', 'turn_ended', 'awaiting_verification']) {
      await workState(page, value);
      await expect(page.getByTestId('scenario-picker')).toHaveValue(value);
      await expect(page.getByTestId('live-stream-output')).toHaveCount(value === 'running' ? 1 : 0);
      await expect(page.getByTestId('work-question-card')).toHaveCount(value === 'waiting_input' ? 1 : 0);
      await expect(page.getByTestId('turn-summary')).toHaveCount(['turn_ended', 'awaiting_verification'].includes(value) ? 1 : 0);
      await expect(page.getByTestId('work-area-results')).toHaveCount(value === 'awaiting_verification' ? 1 : 0);
      await expect(page.getByTestId('pm-assessment-card')).toHaveCount(0);
      await health(page, lang);
      if (all && ['accepted', 'preparing_worktree', 'starting', 'turn_ended'].includes(value)) await shot(page, `18-work-${value}-${lang}-${width}.png`);
    }
    await workState(page, 'waiting_input');
    await capture('09-work-waiting-input');
    await page.getByTestId('answer-question-keep').click();
    await expect(page.getByTestId('work-question-card')).toHaveCount(0);
    await expect(page.getByTestId('view-work-main')).toHaveAttribute('data-question-answered', 'true');
    await expect(page.getByTestId('scenario-picker')).toHaveValue('awaiting_verification');
    await showResults(page, width);
    await capture('10-work-awaiting-verification', true);
    const labels = [];
    for (const key of ['self_reported', 'tests_passed', 'pr_open', 'accepted', 'merged', 'deployed']) labels.push(await page.getByTestId(`delivery-state-${key}`).locator('dt').innerText());
    expect(new Set(labels).size).toBe(6);
    for (const [key, value] of [['self_reported', 'true'], ['tests_passed', 'true'], ['pr_open', 'false'], ['accepted', 'false'], ['merged', 'false'], ['deployed', 'false']]) await expect(page.getByTestId(`delivery-state-${key}`)).toHaveAttribute('data-delivery-achieved', value);
    // Details open on demand, and do not change the evidence states.
    for (const label of lang === 'en' ? ['Changed files', 'Diff', 'Tests'] : ['變更檔案', '差異', '測試']) {
      await page.getByTestId('results-inspector').getByText(label, {
        exact: true
      }).click();
      await health(page, lang);
    }
    await page.getByTestId('btn-create-pr').click();
    await expect(page.getByTestId('delivery-state-pr_open')).toHaveAttribute('data-delivery-achieved', 'true');
    await expect(page.getByTestId('delivery-state-accepted')).toHaveAttribute('data-delivery-achieved', 'false');
    await capture('11-work-pr-and-pm-backfill');
    await page.getByTestId('review-pm-summary').click();
    await expect(page.getByTestId('view-overview')).toBeVisible();
    await expect(page.getByTestId('pm-current-judgment')).toBeVisible();
    for (const assessment of ['insufficient', 'failed', 'passed']) {
      await expandBar(page, true);
      await page.getByTestId('assessment-picker').selectOption(assessment);
      await expandBar(page, false);
      await expect(page.getByTestId('pm-assessment-pill')).toHaveText(
        lang === 'en' ? {insufficient:'Insufficient evidence', failed:'Failed', passed:'Passed'}[assessment]!
          : {insufficient:'證據不足', failed:uiStrings['zh-TW'].ui_status_failed, passed:'通過'}[assessment]!);
      if (assessment !== 'passed') {
        await expect(page.getByTestId('decision-accept-overview')).toBeDisabled();
        await capture(assessment === 'insufficient' ? '21-pm-insufficient' : '22-pm-failed');
      }
    }
    if (lang === 'en' && width === 1440) await shot(page, 'compare-overview-en-1440.png');
    await page.getByTestId('decision-accept-overview').click();
    await expect(page.getByTestId('pm-owner-decision-banner')).toBeVisible();
    await expect(page.getByTestId('pm-owner-decisions')).toHaveCount(0);
    await expect(page.getByTestId('delivery-state-accepted')).toHaveAttribute('data-delivery-achieved', 'true');
    await capture('12-overview-decision-return');
    await simulate(page, 'btn-record-merge');
    await expect(page.getByTestId('delivery-state-merged')).toHaveAttribute('data-delivery-achieved','true');
    await expect(page.getByTestId('delivery-state-deployed')).toHaveAttribute('data-delivery-achieved','false');
    await capture('26-merged');
    await simulate(page, 'btn-record-deploy');
    await expect(page.getByTestId('delivery-state-deployed')).toHaveAttribute('data-delivery-achieved','true');
    await capture('27-deployed');
    await page.getByTestId('overview-open-discussion').click();
    await expect(page.getByTestId('discussion-decision-banner')).toBeInViewport();
    await capture('25-decision-in-discussion');
    await page.getByTestId('discussion-compose').click();
    await expect(page.getByTestId('composer-textarea')).toHaveValue(draft);
    await health(page, lang);
    // Manual work stays read-only; linking is a relation, deriving requires a fixed basis.
    await page.getByTestId('scenario-picker').selectOption('external_readonly');
    const manual = page.getByTestId('view-external-manual');
    await expect(manual).toHaveAttribute('data-session-readonly', 'true');
    await expect(manual.locator('input,textarea')).toHaveCount(0);
    await showResults(page, width);
    await page.getByTestId('manual-view-original').click();
    await expect(page.getByTestId('manual-view-original-notice')).toBeVisible();
    await page.getByTestId('manual-link-existing').click();
    await expect(page.getByTestId('manual-linked-notice')).toBeVisible();
    await expect(page.getByTestId('counter-manual-mutations')).toHaveAttribute('data-count', '0');
    await capture('13-external-readonly', true);
    if (lang === 'en' && width === 1440) await shot(page, 'compare-session-en-1440.png');
    await page.getByTestId('manual-derive-new').click();
    await expect(page.getByTestId('derive-confirmation')).toContainText('4b91e0c');
    await expect(page.getByTestId('derive-confirmation')).toContainText('scratch.txt');
    await expect(page.getByTestId('derive-confirmation')).toContainText('.env.local');
    await capture('19-derive-confirmation');
    await page.getByTestId('derive-cancel').click();
    await expect(page.getByTestId('derive-confirmation')).toHaveCount(0);
    await page.getByTestId('manual-derive-new').click();
    await page.getByTestId('derive-start').click();
    await expect(page.getByTestId('view-work-main')).toBeVisible();
    await expect(page.getByTestId('manual-derived-notice')).toBeVisible();
    await expect(page.locator('.ui-page-header h1')).toHaveText(lang === 'en' ? 'Continue reset-token review' : '繼續檢查重設權杖');
    await expect(page.getByTestId('counter-start-requests')).toHaveAttribute('data-count', '2');
    await capture('23-derived-work');
    await expect(page.getByTestId('counter-manual-mutations')).toHaveAttribute('data-count', '0');
    await health(page, lang);
    await simulate(page, 'proto-trigger-hermes');
    await expect(page.getByTestId('scenario-picker')).toHaveValue('hermes');
    await expect(page.getByTestId('view-hermes-work')).toHaveAttribute('data-external-controller', 'Hermes');
    await expect(page.getByTestId('view-hermes-work').locator('textarea,input')).toHaveCount(0);
    if (width === 390) await page.getByTestId('nav-drawer-toggle').click();
    await expect(page.getByTestId('nav-item-work_hermes')).toBeVisible();
    if (width === 390) await page.keyboard.press('Escape');
    await page.getByTestId('hermes-send-inbox').click();
    await expect(page.getByTestId('hermes-inbox-feedback')).toBeVisible();
    await expect(page.getByTestId('counter-hermes-mutations')).toHaveAttribute('data-count', '0');
    await capture('14-hermes-mcp-work', true);
    await page.getByTestId('hermes-start-separate').click();
    await expect(page.getByTestId('view-work-main')).toBeVisible();
    await expect(page.getByTestId('hermes-separate-notice')).toBeVisible();
    await expect(page.locator('.ui-page-header h1')).toHaveText(lang === 'en' ? 'Review cookie security' : '檢查 Cookie 安全性');
    await expect(page.getByTestId('counter-start-requests')).toHaveAttribute('data-count', '3');
    await capture('24-separate-work');
    await expect(page.getByTestId('counter-hermes-mutations')).toHaveAttribute('data-count', '0');
    await page.getByTestId('scenario-picker').selectOption('disconnect_recovery');
    await expect(page.getByTestId('disconnect-diagnostic')).toBeVisible();
    await expect(page.getByTestId('live-stream-output')).toHaveCount(0);
    await expect(page.getByTestId('work-area-results')).toHaveCount(0);
    await expect(page.getByTestId('work-question-card')).toHaveCount(0);
    await expect(page.getByTestId('work-queue-box')).toHaveCount(0);
    await capture('15-disconnect-recovery');
    await page.getByTestId('reconnect-reconcile').click();
    await expect(page.getByTestId('reconciled-notice')).toBeVisible();
    await expect(page.getByTestId('counter-start-requests')).toHaveAttribute('data-count', '1');
    await expect(page.getByTestId('counter-duplicate-starts')).toHaveAttribute('data-count', '0');
    await page.getByTestId('work-area-advanced').locator('summary').click();
    await expect(page.getByTestId('advanced-raw-events')).toContainText('opk_signin_double_submit_start_01');
    await expect(page.getByTestId('advanced-raw-events')).not.toContainText('msg_q_02');
    await health(page, lang);
    await page.getByTestId('scenario-picker').selectOption('maintenance');
    await page.getByTestId('maint-return-review').click();
    await expect(page.getByTestId('maint-review-returned-banner')).toBeVisible();
    await capture('16-maintenance-fix');
    await page.getByTestId('scenario-picker').selectOption('running');
    await simulate(page, 'proto-theme-toggle');
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await capture('17-dark-theme-work');
    // Shared drawer handles Escape and returns focus, in both mobile reading surfaces.
    if (width === 390) {
      await page.getByTestId('scenario-picker').selectOption('idea');
      await page.getByTestId('inspector-drawer-toggle').click();
      await expect(page.getByTestId('context-inspector')).toBeVisible();
      await page.keyboard.press('Escape');
      await expect(page.getByTestId('inspector-drawer-toggle')).toBeFocused();
      await page.getByTestId('nav-drawer-toggle').click();
      await page.keyboard.press('Escape');
      await expect(page.getByTestId('nav-drawer-toggle')).toBeFocused();
    }
    expect(errors).toEqual([]);
    expect(requests).toEqual([]);
    const video = page.video();
    await context.close();
    if (video && width === 1440) fs.renameSync(await video.path(), path.join(VIDEO, `golden-flow-${lang}-1440.webm`));
  });
}
test('tablet shell and explicit adopt-and-start', async ({
  page
}) => {
  await page.setViewportSize({
    width: 768,
    height: 900
  });
  await page.goto('/prototype/v1.3/?lang=en&scene=planned');
  await page.getByTestId('plan-adopt-start').click();
  await expect(page.getByTestId('counter-start-requests')).toHaveAttribute('data-count', '1');
  await health(page, 'en');
  await shot(page, '20-tablet-adopt-en-768.png');
});
