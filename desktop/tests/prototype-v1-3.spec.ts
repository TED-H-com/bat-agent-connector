import fs from 'node:fs';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import path from 'node:path';
import { expect, test, type Browser, type Page } from '@playwright/test';
import { uiStrings } from '../src/ui/ui-strings.js';
import { STRINGS as productStrings } from '../src/i18n.js';
const EVIDENCE = process.env.BATC_PROTOTYPE_SHOTS === '1';
const SHOTS = EVIDENCE ? process.env.BATC_PROTOTYPE_SHOT_DIR : undefined;
const VIDEO = EVIDENCE ? process.env.BATC_PROTOTYPE_VIDEO_DIR : undefined;
const SHOT_FILTER = new RegExp(process.env.BATC_PROTOTYPE_SHOT_FILTER || '');
if (EVIDENCE && !SHOTS) throw new Error('Set BATC_PROTOTYPE_SHOT_DIR when BATC_PROTOTYPE_SHOTS=1');
type Locale = 'en' | 'zh-TW';
type Flow = {page: Page; lang: Locale; width: number; all: boolean; capture: (name: string, extra?: boolean) => Promise<void>};
const forbidden = /§|\bF[1-7]\b|after_current|ACK|Safety invariant/;
const ids = /\b(?:wi_|att_|opk_|op_|bat_sess_|wt_|ctr_|msg_|inbox_|rev_|disc_|principal_)[\w-]+/;
const english = /\b(Send to PM|Save note only|Update plan|Start work|Send next step|Adopt, do later|Adopt and start|Self-reported|Tests passed|PR open|Accepted|Merged|Deployed|Read-only|Results|Controller|Current judgment|Not recorded|Undefined|Discussion info|Change scope)\b/;
for (const lang of ['en', 'zh-TW']) for (const width of [1440, 390]) {
  test(`prototype shares the scoped central status ${lang} ${width}`, async ({page}) => {
    await page.setViewportSize({width, height: 900});
    for (const scene of ['idea', 'disconnect_recovery']) {
      await page.goto(`/prototype/v1.3/?lang=${lang}&scene=${scene}`);
      const connected = scene === 'idea';
      const text = lang === 'zh-TW' ? connected ? '已連線' : '重新連線中' : connected ? 'Connected' : 'Reconnecting';
      const scoped = lang === 'zh-TW' ? `中央服務：${text}` : `Central service: ${text}`;
      const status = page.locator('.central-service-status .live');
      await expect(status).toHaveText(text);
      await expect(status).toHaveAccessibleName(scoped);
      await expect(status).toHaveAttribute('title', scoped);
      await expect(status).toHaveClass(connected ? /tone-ok/ : /tone-warn/);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    }
  });
}
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
  if (!SHOTS || !SHOT_FILTER.test(name)) return;
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
async function openBudgetDecision(page: Page, lang: Locale) {
  const budget = lang === 'en' ? 'Recovery support budget' : '復原支援預算';
  const strip = page.getByTestId('overview-open-decisions-strip');
  await expect(strip.locator('small')).toHaveText(productStrings[lang].wb_decisions);
  await expect(strip.locator('strong')).toHaveText(budget);
  const charter = page.getByTestId('overview-open-decisions-charter');
  await expect(charter).toHaveText(budget);
  await expect(charter.locator('xpath=..').getByRole('heading')).toHaveText(productStrings[lang].wb_decisions);
}

// fullyParallel is off: this file has one worker. No other spec builds the prototype.
// Build current source once before this file's first navigation; the shared server only serves files.
test.beforeAll(async () => {
  await promisify(execFile)('npm', ['run', 'build:prototype'], {cwd: new URL('..', import.meta.url)});
});

async function walk(browser: Browser, lang: Locale, width: number, run: (flow: Flow) => Promise<void>) {
  // Screenshots/video can exceed 30 s; default assertion runs retain Playwright's timeout.
  if (EVIDENCE) test.setTimeout(120_000);
  let videoDir: string | undefined;
  if (VIDEO && width === 1440) {
    fs.mkdirSync(VIDEO, {recursive: true});
    videoDir = fs.mkdtempSync(path.join(VIDEO, 'prototype-'));
  }
  const context = await browser.newContext({
    viewport: {width, height: width === 390 ? 844 : 949},
    locale: lang,
    ...(videoDir ? {recordVideo: {dir: videoDir, size: {width: 1440, height: 949}}} : {})
  });
  const page = await context.newPage(), errors: string[] = [], requests: string[] = [];
  page.on('console', msg => { if (msg.type() === 'error') errors.push(msg.text()); });
  page.on('pageerror', err => errors.push(String(err)));
  page.on('request', request => {
    if (!request.url().includes('/prototype/v1.3/')) requests.push(request.url());
  });
  const all = lang === 'en' && width === 1440 || lang === 'zh-TW' && width === 390;
  const capture = async (name: string, extra = false) => {
    if (await page.getByTestId('view-discussions').count()) {
      const texts = await page.locator('.proto-transcript .message-body').allTextContents();
      expect(new Set(texts).size).toBe(texts.length);
      await expect(page.getByTestId('discussion-message-text')).toHaveCount(name === '01-idea' ? 0 : 1);
      if (!['01-idea', '02-note-only'].includes(name)) await expect(page.getByTestId('note-saved-banner')).toHaveCount(0);
    }
    await health(page, lang);
    if (all || extra) await shot(page, `${name}-${lang}-${width}.png`);
  };
  try {
    await run({page, lang, width, all, capture});
    expect(errors).toEqual([]);
    expect(requests).toEqual([]);
  } finally {
    const video = page.video();
    await context.close();
    if (video && videoDir) fs.renameSync(await video.path(), path.join(videoDir, `golden-flow-${lang}-1440.webm`));
  }
}

async function discussionToQueue({page, lang, width, all, capture}: Flow) {
  await page.goto(`/prototype/v1.3/?lang=${lang}`);
  await expect(page.locator('meta[name=robots]')).toHaveAttribute('content', 'noindex');
  await expect(page).toHaveTitle(lang === 'en' ? 'BAT Dashboard · v1.3 prototype' : 'BAT Dashboard · v1.3 互動原型');
  await expect(page.getByTestId('view-discussions')).toBeVisible();
  await expect(page.getByTestId('composer-textarea')).toHaveAttribute('placeholder', productStrings[lang].pd_message);
  if (width === 390) await page.getByTestId('inspector-drawer-toggle').click();
  await expect(page.getByTestId('context-inspector').getByRole('tab', {name: productStrings[lang].pd_tab_inputs, exact: true})).toBeVisible();
  if (width === 390) await page.keyboard.press('Escape');
  await expect(page.getByTestId('counter-model-calls')).toHaveAttribute('data-count', '0');
  await capture('01-idea');
  if (lang === 'en' && width === 1440) await shot(page, 'compare-discussions-en-1440.png');
  // The shell's names come from the product dictionaries in both locales.
  if (width === 390) await page.getByTestId('nav-drawer-toggle').click();
  await expect(page.locator('.pm-project-views > :is(button, a) > span')).toHaveText(['overview', 'discussions', 'requirements', 'schedule', 'dependencies', 'issues', 'agents', 'board'].map(id => uiStrings[lang][`ui_${id}` as keyof typeof uiStrings[typeof lang]]));
  await page.keyboard.press('Escape');
  const draft = await page.getByTestId('composer-textarea').inputValue();
  for (const view of ['requirements', 'schedule', 'dependencies', 'issues', 'agents', 'board']) {
    await expect(page.getByTestId(`nav-item-${view}`)).toHaveAttribute('href', `../../demo/?lang=${lang}&view=${view}`);
    await expect(page.getByTestId(`nav-item-${view}`)).toHaveAttribute('target', '_blank');
    await expect(page.getByTestId('composer-textarea')).toHaveValue(draft);
  }
  await expect(page.locator('.proto-composer [role=radio]')).toHaveText(lang === 'en' ? ['Discuss', 'Plan', 'Do'] : ['討論', '規劃', '做事']);
  await expect(page.getByTestId('intent-suggestion-banner')).toHaveCount(0);
  await expect(page.locator('.proto-composer [role=radio]').first()).toBeHidden();
  await page.getByText(lang === 'en' ? 'Message options' : '訊息選項', {exact: true}).click();
  await page.getByRole('radio', {name: lang === 'en' ? 'Do' : '做事', exact: true}).click();
  await expect(page.getByTestId('suggest-switch-discuss')).toHaveText(lang === 'en' ? 'Switch to discuss' : '切換至討論');
  await page.getByTestId('suggest-switch-discuss').click();
  await page.getByTestId('composer-save-note').click();
  await expect(page.getByTestId('note-saved-banner')).toBeVisible();
  await expect(page.getByTestId('counter-model-calls')).toHaveAttribute('data-count', '0');
  await expect(page.getByTestId('composer-textarea')).toHaveValue('');
  await expect(page.getByTestId('note-kind')).toHaveText(lang === 'en' ? 'Note · not sent to PM' : '筆記・未傳送給 PM');
  await capture('02-note-only');
  // A failed send keeps a new draft while the saved note stays one unsent record.
  await page.getByTestId('composer-textarea').fill(draft);
  await simulate(page, 'toggle-pm-unavailable');
  await expect(page.getByTestId('pm-unavailable-diagnostic')).toBeVisible();
  await expect(page.getByTestId('composer-send-pm')).toBeDisabled();
  await expect(page.getByTestId('saved-note')).toBeInViewport();
  await capture('03-pm-unavailable');
  await page.getByTestId('fix-pm-available').click();
  await page.getByTestId('restore-pm').click();
  await expect(page.getByTestId('composer-textarea')).toHaveValue(draft);
  await page.getByTestId('composer-send-pm').click();
  await expect(page.getByTestId('counter-model-calls')).toHaveAttribute('data-count', '1');
  await expect(page.getByTestId('counter-start-requests')).toHaveAttribute('data-count', '0');
  await page.getByTestId('suggest-switch-plan').click();
  await expect(page.getByTestId('counter-start-requests')).toHaveAttribute('data-count', '0');
  await expect(page.getByTestId('intent-composer')).toHaveAttribute('data-active-intent', 'discuss');
  await expect(page.getByTestId('short-plan-card')).toBeVisible();
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
  await expect(page.getByTestId('view-work-main')).toBeVisible();
  await expect(page.getByTestId('scenario-picker')).toHaveValue('running');
  await expect(page.getByTestId('work-area-results')).toHaveCount(0);
  await expect(page.getByTestId('live-stream-output')).toBeVisible();
  expect(await page.locator('.proto-tools li').evaluateAll(rows => rows.map(row => row.getAttribute('data-tool-state')))).toEqual(['done', 'running']);
  await expect(page.locator('.proto-tools svg[data-icon="circle-alert"]')).toHaveCount(0);
  if (width === 1440 && lang === 'en') {
    const runtime = page.getByTestId('work-area-header').locator('dt').filter({
      hasText: 'Runtime and model'
    });
    expect(await runtime.evaluate(el => el.getBoundingClientRect().height)).toBeLessThan(30);
  }
  await capture('07-work-running', true);
  const second = await page.getByTestId('next-step-textarea').inputValue();
  await simulate(page, 'toggle-queue-failure');
  await page.getByTestId('btn-queue-second-sentence').click();
  await expect(page.getByTestId('queue-failed')).toBeVisible();
  await expect(page.getByTestId('next-step-textarea')).toHaveValue(second);
  await expect(page.getByTestId('queued-second-sentence-receipt')).toHaveCount(0);
  await simulate(page, 'toggle-queue-failure');
  await page.getByTestId('btn-queue-second-sentence').click();
  await expect(page.getByTestId('next-step-textarea')).toHaveValue('');
  const receipt = page.getByTestId('queued-second-sentence-receipt');
  await expect(receipt).toHaveCount(1);
  await expect(receipt).toContainText(second);
  await expect(receipt).toHaveAttribute('data-queue-policy', 'after_current');
  await expect(receipt).toHaveAttribute('data-steered', 'false');
  await expect(page.getByTestId('work-queue-box')).toHaveAttribute('data-second-steered', 'false');
  await capture('08-work-queued-second', true);
}

async function workToDelivery({page, lang, width, all, capture}: Flow) {
  const second = lang === 'en' ? 'Add a case that resubmits after a failure.' : '再補一個失敗後重新送出的案例。';
  // Every state exposes only the areas with evidence at that point.
  for (const value of ['accepted', 'preparing_worktree', 'starting', 'running', 'waiting_input', 'turn_ended', 'awaiting_verification']) {
    await workState(page, value);
    await expect(page.getByTestId('scenario-picker')).toHaveValue(value);
    await expect(page.getByTestId('live-stream-output')).toHaveCount(value === 'running' ? 1 : 0);
    await expect(page.getByTestId('work-question-card')).toHaveCount(value === 'waiting_input' ? 1 : 0);
    await expect(page.getByTestId('turn-summary')).toHaveCount(['turn_ended', 'awaiting_verification'].includes(value) ? 1 : 0);
    await expect(page.getByTestId('work-area-results')).toHaveCount(value === 'awaiting_verification' ? 1 : 0);
    await expect(page.getByTestId('pm-assessment-card')).toHaveCount(value === 'awaiting_verification' ? 1 : 0);
    await expect(page.getByTestId('pm-assessment-card')).not.toBeVisible();
    await expect(page.getByTestId('interrupt-work')).toHaveCount(value === 'running' ? 1 : 0);
    if (value !== 'running') await expect(page.getByTestId('product-shell').getByText(lang === 'en' ? 'Sent after the current turn ends.' : '本輪結束後才會送出。', {
      exact: true
    })).toHaveCount(0);
    if (['turn_ended', 'awaiting_verification'].includes(value)) {
      await expect(page.getByTestId('queued-second-sentence-receipt')).toHaveCount(0);
      await expect(page.getByTestId('delivered-next-step')).toHaveCount(1);
      await expect(page.getByTestId('delivered-next-step')).toContainText(second);
      await expect(page.getByTestId('next-step-textarea')).toHaveValue('');
      await expect(page.getByTestId('btn-queue-second-sentence')).toBeEnabled();
      await expect(page.getByTestId('next-step-hint')).toHaveText(lang === 'en' ? 'Starts the next turn.' : '送出後開始下一輪。');
    }
    await health(page, lang);
    if (value === 'waiting_input') await capture('09-work-waiting-input');
    if (all && ['accepted', 'preparing_worktree', 'starting', 'turn_ended'].includes(value)) await shot(page, `18-work-${value}-${lang}-${width}.png`);
  }
  await workState(page, 'waiting_input');
  await page.getByTestId('answer-question-keep').click();
  await expect(page.getByTestId('work-question-card')).toHaveCount(0);
  await expect(page.getByTestId('view-work-main')).toHaveAttribute('data-question-answered', 'true');
  await expect(page.getByTestId('scenario-picker')).toHaveValue('awaiting_verification');
  await showResults(page, width);
  await capture('10-work-awaiting-verification', true);
  if (lang === 'zh-TW') {
    await expect(page.getByTestId('delivery-state-self_reported').locator('dd')).toHaveText('已有紀錄');
    await expect(page.getByTestId('delivery-state-pr_open').locator('dd')).toHaveText('尚無紀錄');
    await expect(page.getByTestId('delivery-state-accepted').locator('dt')).toHaveText('已驗收');
  }
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
  await expect(page.getByTestId('view-work-main')).toBeVisible();
  await expect(page.getByTestId('pm-current-judgment')).toBeVisible();
  await page.getByTestId('assessment-open-overview').click();
  await expect(page.getByTestId('view-overview')).toBeVisible();
  await expect(page.getByTestId('pm-current-judgment')).toBeVisible();
  await expect(page.getByTestId('pm-assessment-card').getByRole('heading', {name: lang === 'en' ? 'Sources' : '來源', exact: true})).toBeVisible();
  for (const assessment of ['insufficient', 'failed', 'passed']) {
    await expandBar(page, true);
    await page.getByTestId('assessment-picker').selectOption(assessment);
    await expandBar(page, false);
    await expect(page.getByTestId('pm-assessment-pill')).toHaveText(lang === 'en' ? {
      insufficient: 'Insufficient evidence',
      failed: 'Failed',
      passed: 'Passed'
    }[assessment]! : {
      insufficient: '證據不足',
      failed: '未通過',
      passed: '通過'
    }[assessment]!);
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
  await expect(page.getByTestId('scenario-picker')).toHaveValue('requirement_accepted');
  await expect(page.getByTestId('view-overview').getByRole('heading', {
    name: lang === 'en' ? 'Delivery' : '交付',
    exact: true
  })).toHaveCount(0);
  await expect(page.getByTestId('pm-recommendation')).toHaveText(lang === 'en' ? 'Review PR #48 for merge.' : '建議審閱 PR #48 是否合併。');
  await openBudgetDecision(page, lang);
  await capture('12-overview-decision-return');
  await page.getByTestId('overview-open-discussion').click();
  await expect(page.getByTestId('discussion-decision-banner')).toBeInViewport();
  await expect(page.getByTestId('discussion-decision-banner').locator('xpath=ancestor::article').locator('.message-name')).toHaveText('mei.lin');
  await expect(page.getByTestId('discussion-decision-banner').locator('xpath=ancestor::article').locator('time')).toHaveAttribute('datetime', '2026-04-12T14:30:00.000Z');
  await expect(page.getByTestId('discussion-thread-state')).toHaveText(lang === 'en' ? 'Requirement accepted' : '需求已驗收');
  await expect(page.getByTestId('discussion-list-state')).toHaveText(lang === 'en' ? 'Requirement accepted' : '需求已驗收');
  await expect(page.getByTestId('work-started-summary')).toBeVisible();
  await expect(page.getByTestId('compound-step-list')).not.toBeVisible();
  await capture('25-decision-in-discussion');
  await page.getByTestId('discussion-compose').click();
  await expect(page.getByTestId('composer-textarea')).toHaveValue('');
  await health(page, lang);
  await page.getByTestId('discussion-decision-overview').click();
  await simulate(page, 'btn-record-merge');
  await expect(page.getByTestId('delivery-state-merged')).toHaveAttribute('data-delivery-achieved', 'true');
  await expect(page.getByTestId('delivery-state-deployed')).toHaveAttribute('data-delivery-achieved', 'false');
  await expect(page.getByTestId('scenario-picker')).toHaveValue('merged');
  await expect(page.getByTestId('pm-assessment-card')).toContainText(lang === 'en' ? 'PR #48 · merged' : 'PR #48・已合併');
  await expect(page.getByTestId('pm-recommendation')).toHaveText(lang === 'en' ? 'Verify the merged fix after deployment.' : '建議部署後確認已合併的修正。');
  await openBudgetDecision(page, lang);
  await capture('26-merged');
  await simulate(page, 'btn-record-deploy');
  await expect(page.getByTestId('delivery-state-deployed')).toHaveAttribute('data-delivery-achieved', 'true');
  await expect(page.getByTestId('scenario-picker')).toHaveValue('deployed');
  await expect(page.getByTestId('pm-assessment-card')).toContainText(lang === 'en' ? 'Sign-in fix · deployment recorded' : '登入修正・已記錄部署');
  await expect(page.getByTestId('pm-recommendation')).toHaveText(lang === 'en' ? 'Monitor sign-in and failure retries after deployment.' : '建議持續觀察部署後的登入與失敗重試。');
  await openBudgetDecision(page, lang);
  await capture('27-deployed');
  await page.getByTestId('overview-open-discussion').click();
  await expect(page.getByTestId('discussion-thread-state')).toHaveText(lang === 'en' ? 'Deployed' : '已部署');
  await expect(page.getByTestId('discussion-list-state')).toHaveText(lang === 'en' ? 'Deployed' : '已部署');
}

async function externalWork({page, lang, width, all, capture}: Flow) {
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
  const backdrop = await page.getByTestId('derive-backdrop').boundingBox();
  expect(backdrop).toEqual({
    x: 0,
    y: 0,
    width,
    height: width === 390 ? 844 : 949
  });
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
  if (width === 390) await page.getByTestId('nav-drawer-toggle').click();
  const rail = page.getByTestId('shell-sidebar');
  for (const item of await rail.locator('button:visible, a:visible').all()) {
    await item.scrollIntoViewIfNeeded();
    await expect(item).toBeInViewport();
    const visible = await item.evaluate(el => {
      const a = el.getBoundingClientRect(),
        b = el.closest('aside')!.getBoundingClientRect();
      return a.top >= b.top - 1 && a.bottom <= b.bottom + 1;
    });
    expect(visible).toBe(true);
  }
  await rail.evaluate(el => {
    el.scrollTop = el.scrollHeight;
  });
  for (const item of await rail.locator('.workspace-nav-footer a').all()) await expect(item).toBeInViewport();
  if (width === 390) await page.keyboard.press('Escape');
  await capture('24-separate-work');
  await expect(page.getByTestId('counter-hermes-mutations')).toHaveAttribute('data-count', '0');
}

async function recoveryAndMaintenance({page, lang, width, all, capture}: Flow) {
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
}

for (const lang of ['en', 'zh-TW'] as const) for (const width of [1440, 390] as const) {
  if (EVIDENCE) {
    test(`golden evidence walk (${lang} @ ${width})`, async ({browser}) => {
      await walk(browser, lang, width, async flow => {
        await discussionToQueue(flow);
        await workToDelivery(flow);
        await externalWork(flow);
        await recoveryAndMaintenance(flow);
      });
    });
  } else {
    test(`discussion, start and queued delivery (${lang} @ ${width})`, async ({browser}) => {
      await walk(browser, lang, width, async flow => {
        await discussionToQueue(flow);
        // Keep note -> start -> queue -> delivery continuous; no fixture reset at these boundaries.
        await workState(flow.page, 'turn_ended');
        await expect(flow.page.getByTestId('queued-second-sentence-receipt')).toHaveCount(0);
        await expect(flow.page.getByTestId('delivered-next-step')).toHaveCount(1);
        await health(flow.page, lang);
      });
    });
    test(`work states, results and decisions (${lang} @ ${width})`, async ({browser}) => {
      await walk(browser, lang, width, async flow => {
        await flow.page.goto(`/prototype/v1.3/?lang=${lang}&scene=running`);
        await flow.page.getByTestId('btn-queue-second-sentence').click();
        await workToDelivery(flow);
      });
    });
    test(`read-only session and separate work (${lang} @ ${width})`, async ({browser}) => {
      await walk(browser, lang, width, async flow => {
        await flow.page.goto(`/prototype/v1.3/?lang=${lang}&scene=external_readonly`);
        await externalWork(flow);
      });
    });
    test(`recovery, maintenance and dark theme (${lang} @ ${width})`, async ({browser}) => {
      await walk(browser, lang, width, async flow => {
        await flow.page.goto(`/prototype/v1.3/?lang=${lang}&scene=disconnect_recovery`);
        await recoveryAndMaintenance(flow);
      });
    });
  }
}
test('tablet shell and explicit adopt-and-start', async ({
  page
}) => {
  await page.setViewportSize({
    width: 768,
    height: 900
  });
  await page.clock.install();
  await page.goto('/prototype/v1.3/?lang=en&scene=planned');
  await page.clock.pauseAt(await page.evaluate(() => Date.now() + 1000));
  await page.getByTestId('plan-adopt-start').click();
  await expect(page.getByTestId('view-work-main')).toBeVisible();
  await expect(page.getByTestId('scenario-picker')).toHaveValue('preparing_worktree');
  await expect(page.getByTestId('compound-step-list')).not.toBeVisible();
  await page.clock.runFor(350);
  await expect(page.getByTestId('scenario-picker')).toHaveValue('starting');
  await page.clock.runFor(350);
  await expect(page.getByTestId('live-stream-output')).toBeVisible();
  await expect(page.getByTestId('counter-start-requests')).toHaveAttribute('data-count', '1');
  await health(page, 'en');
  await shot(page, '20-tablet-adopt-en-768.png');
});

for (const lang of ['en', 'zh-TW'] as const) for (const width of [1440, 768, 390]) for (const origin of ['work', 'discussion']) {
  test(`one PM decision from ${origin} stays in context (${lang} @ ${width})`, async ({browser}) => {
    await walk(browser, lang, width, async ({page, capture}) => {
      await page.goto(`/prototype/v1.3/?lang=${lang}&scene=awaiting_verification`);
      await showResults(page, width);
      await page.getByTestId('btn-create-pr').click();
      const identity = await page.getByTestId('pm-assessment-card').getAttribute('data-assessment-id');
      if (origin === 'discussion') {
        if (width < 1200) await page.keyboard.press('Escape');
        await page.getByTestId('work-return-discussion').click();
        if (width < 1200) await page.getByTestId('inspector-drawer-toggle').click();
        await page.getByTestId('context-inspector').getByRole('tab', {name: lang === 'en' ? 'PM summary' : 'PM 摘要', exact: true}).click();
      } else await page.getByTestId('review-pm-summary').click();
      await expect(page.getByTestId('pm-assessment-card')).toHaveAttribute('data-assessment-id', identity!);
      await expect(page.getByTestId('pm-current-judgment')).toBeVisible();
      await page.getByTestId('decision-accept-overview').scrollIntoViewIfNeeded();
      if (origin === 'work' && width === 1440) await expect(page.getByTestId('turn-summary')).toBeInViewport();
      if (width < 1200) await expect(page.getByTestId(origin === 'work' ? 'results-inspector' : 'context-inspector').locator(':scope > header')).toBeInViewport();
      await page.getByTestId('decision-accept-overview').click();
      await expect(page.getByTestId(origin === 'work' ? 'view-work-main' : 'view-discussions')).toBeVisible();
      await expect(page.getByTestId('pm-owner-decision-banner')).toBeVisible();
      await expect(page.getByTestId('pm-owner-decisions')).toHaveCount(0);
      await capture(`26-${origin}-decision`, true);
      await page.getByTestId('assessment-open-overview').click();
      await expect(page.getByTestId('pm-assessment-card')).toHaveAttribute('data-assessment-id', identity!);
      await expect(page.getByTestId('pm-owner-decision-banner')).toBeVisible();
      await expect(page.getByTestId('pm-owner-decisions')).toHaveCount(0);
      await expect(page.getByTestId('delivery-state-accepted')).toHaveAttribute('data-delivery-achieved', 'true');
      await expect(page.getByTestId('counter-start-requests')).toHaveAttribute('data-count', '1');
      await health(page, lang);
    });
  });
}

test('opening preparing work preserves the discussion draft and adopted plan', async ({page}) => {
  await page.clock.install();
  await page.goto('/prototype/v1.3/?lang=en&scene=planned');
  await page.clock.pauseAt(await page.evaluate(() => Date.now() + 1000));
  await page.getByTestId('composer-textarea').fill('Keep the password field after a failure.');
  await page.getByTestId('plan-adopt-start').click();
  await expect(page.getByTestId('view-work-main')).toBeVisible();
  await expect(page.getByTestId('work-pre-output')).toBeVisible();
  await expect(page.getByTestId('live-stream-output')).toHaveCount(0);
  await page.getByTestId('work-return-discussion').click();
  await page.getByTestId('discussion-compose').click();
  await expect(page.getByTestId('composer-textarea')).toHaveValue('Keep the password field after a failure.');
  await page.getByTestId('work-started-summary').getByText('Plan', {exact: true}).click();
  await expect(page.getByTestId('short-plan-card')).toContainText('Sign-in button and its regression tests.');
  await page.getByTestId('adopt-open-work-page').click();
  await page.clock.runFor(700);
  await expect(page.getByTestId('live-stream-output')).toBeVisible();
  await expect(page.getByTestId('counter-start-requests')).toHaveAttribute('data-count', '1');
  await expect(page.getByTestId('counter-duplicate-starts')).toHaveAttribute('data-count', '0');
});

test('discussion context remains available after reviewing a result and starting another turn', async ({page}) => {
  await page.setViewportSize({width: 1440, height: 949});
  await page.goto('/prototype/v1.3/?lang=en&scene=awaiting_verification');
  await page.getByTestId('work-return-discussion').click();
  await page.getByTestId('context-inspector').getByRole('tab', {name: 'PM summary', exact: true}).click();
  await expect(page.getByTestId('pm-current-judgment')).toBeVisible();
  await workState(page, 'running');
  await page.getByTestId('work-return-discussion').click();
  await expect(page.getByTestId('context-inspector').getByRole('tab', {name: 'Context', exact: true})).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('[data-info-tab="context"]')).toBeVisible();
});
const regression = expect.configure({
  soft: true,
  timeout: 500
});
for (const lang of ['en', 'zh-TW'] as const) {
  test(`regression queue lifecycle (${lang})`, async ({
    page
  }) => {
    await page.goto(`/prototype/v1.3/?lang=${lang}&scene=running`);
    const second = lang === 'en' ? 'Add a case that resubmits after a failure.' : '再補一個失敗後重新送出的案例。';
    await page.getByTestId('btn-queue-second-sentence').click();
    await regression(page.getByTestId('next-step-textarea')).toHaveValue('');
    await regression(page.getByTestId('queued-second-sentence-receipt')).toHaveCount(1);
    await workState(page, 'turn_ended');
    await regression(page.getByTestId('queued-second-sentence-receipt')).toHaveCount(0);
    await regression(page.getByTestId('interrupt-work')).toHaveCount(0);
    await regression(page.getByTestId('product-shell').getByText(lang === 'en' ? 'Sent after the current turn ends.' : '本輪結束後才會送出。', {
      exact: true
    })).toHaveCount(0);
    await regression(page.locator('.proto-work-transcript .message-body').filter({
      hasText: second
    })).toHaveCount(1);
    await regression(page.getByTestId('next-step-textarea')).toHaveValue('');
    await regression(page.getByTestId('btn-queue-second-sentence')).toBeEnabled();
  });
  test(`regression note lifecycle (${lang})`, async ({
    page
  }) => {
    await page.goto(`/prototype/v1.3/?lang=${lang}`);
    const draft = await page.getByTestId('composer-textarea').inputValue();
    await page.getByTestId('composer-save-note').click();
    await regression(page.getByTestId('composer-textarea')).toHaveValue('');
    await regression(page.getByTestId('saved-note')).toHaveCount(1);
    await simulate(page, 'toggle-pm-unavailable');
    await regression(page.getByTestId('note-saved-banner')).toHaveCount(0);
    await page.getByTestId('fix-pm-available').click();
    await page.getByTestId('restore-pm').click();
    await page.getByTestId('composer-textarea').fill(draft);
    await page.getByTestId('composer-send-pm').click();
    await regression(page.getByTestId('composer-textarea')).toHaveValue('');
    await regression(page.getByTestId('note-saved-banner')).toHaveCount(0);
    await regression(page.locator('.proto-transcript .message-body').filter({
      hasText: draft
    })).toHaveCount(1);
  });
}
