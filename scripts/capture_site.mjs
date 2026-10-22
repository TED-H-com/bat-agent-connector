// Real shared frontend + one synthetic demo project. No external services or DOM rewriting.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {createServer} from 'node:http';
import {mkdir, readFile, writeFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium, expect} from '../desktop/node_modules/@playwright/test/index.mjs';
import {demoFixture, demoRoute} from '../desktop/tests/demo/index.ts';
import {DEMO_NOW} from '../desktop/tests/demo/project.ts';
import {mountConversation} from '../desktop/tests/conversation-fixture.ts';

const root = fileURLToPath(new URL('../', import.meta.url));
// Published merged source for Connector 0.2.13 / desktop 0.1.9 preview. Documentation-only commits may recapture this tree.
const source = '437ee6a7143a82e32eae1d6a008203b2df072aaf';
const output = path.join(root, 'site/images');
const review = process.env.CAPTURE_REVIEW_DIR;
const fixtureClock = new Date(DEMO_NOW).toISOString();
const viewport = {width: 1440, height: 960};
const assets = ['index.html', 'app.js', 'app.css', 'i18n.js'];
const scenes = ['overview', 'discussions', 'requirements', 'issues', 'schedule', 'dependencies', 'board', 'session'];
const reviewScenes = ['agents', 'overview-unknown', 'overview-empty'];
const git = (...args) => execFileSync('git', args, {cwd: root, encoding: 'utf8'}).trim();
const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');
const lockfile = 'desktop/package-lock.json';
const lockfileDigestName = 'without_root_version';
export function lockfileDigest(bytes) {
  let value;
  try {
    value = JSON.parse(Buffer.isBuffer(bytes) ? bytes.toString('utf8') : String(bytes));
  } catch (error) {
    throw new Error(`Malformed lockfile JSON: ${error.message}`);
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('Malformed lockfile JSON: root must be an object');
  }
  if (value.packages !== undefined && (!value.packages || typeof value.packages !== 'object' || Array.isArray(value.packages))) {
    throw new Error('Malformed lockfile JSON: packages must be an object');
  }
  const rootPackage = value.packages?.[''];
  if (rootPackage !== undefined && (!rootPackage || typeof rootPackage !== 'object' || Array.isArray(rootPackage))) {
    throw new Error('Malformed lockfile JSON: root package must be an object');
  }
  for (const [holder, label] of [[value, 'root'], [rootPackage, 'root package']]) {
    if (holder && 'version' in holder) {
      if (typeof holder.version !== 'string') {
        throw new Error(`Malformed lockfile JSON: ${label} version must be a string`);
      }
      delete holder.version;
    }
  }
  return sha256(Buffer.from(JSON.stringify(value), 'utf8'));
}
export function sourceEntry(file, bytes) {
  return file === lockfile
    ? {file, digest: lockfileDigestName, sha256: lockfileDigest(bytes)}
    : {file, sha256: sha256(bytes)};
}
let origin;
async function prepare(page, scenario, locale) {
  const zh = locale === 'zh-TW';
  // The real managed-browser-session fixture avoids an optional bearer-only 404 probe.
  const fixture = demoFixture({locale, now: DEMO_NOW, discussions: scenario === 'discussions', managedSession: true});
  // The older demo Overview omits #134's history. Project its existing synthetic
  // plan adoption receipts using the central read shape, without inventing decisions.
  const base = `/pm/workspaces/${fixture.ids.workspaceId}`;
  const decisions = [];
  for (const id of [fixture.ids.planV3, fixture.ids.planV2]) {
    const {data: {plan}} = await fixture.dispatch({method: 'GET', path: `${base}/plans/${id}`});
    decisions.push({key: `plan_revision.${id}`, title: plan.document.title, decided_at: plan.adoption.created_at,
      actor: plan.adoption.adopted_by, status: 'adopted', ref: {kind: 'plan_revision', id}});
  }
  const writes = [];
  await mountConversation(page, false, async input => {
    if (input.method !== 'GET') writes.push(input);
    const result = await fixture.dispatch(input);
    if (new URL(input.path, 'http://fixture').pathname === `${base}/overview`) {
      Object.assign(result.data, {recent_decisions: decisions, recent_decisions_count: decisions.length,
        recent_decisions_complete: true, recent_decisions_truncated: false});
      if (scenario === 'overview-unknown' || scenario === 'overview-empty') {
        const complete = scenario === 'overview-empty';
        Object.assign(result.data, {milestones: [], next_milestone: null, recent_deliveries: [], recent_deliveries_complete: complete});
        result.data.coverage.sources.roadmap.complete = complete;
      }
    }
    // Discussion mock operations otherwise use the capture machine's wall clock.
    if (result.data.operation) result.data.operation.created_at = DEMO_NOW / 1000 - 42;
    return result;
  });
  await page.goto(origin + demoRoute());
  await expect(page.locator('[data-overview-status]')).toContainText(zh ? '8 項中 3 項已驗收' : '3 of 8 accepted');
  await expect(page.locator('.shell-workspace-switch')).toContainText(fixture.copy.name);
  async function openView(view) {
    const entry = page.locator(`[data-pm-view="${view}"]`);
    await entry.waitFor({state: 'attached'});
    if (!await page.locator('.workspace-nav').isVisible()) await page.locator('#workspace-menu').click();
    if (!await entry.isVisible()) await page.locator('[data-project-management] > summary').click();
    await entry.click();
    await expect(page.locator(`[data-pm-region="${view}"]`)).toBeVisible();
  }
  let composition;
  if (scenario.startsWith('overview')) {
    await expect(page.locator('[data-overview-row]')).toHaveCount(6);
    await expect(page.locator('[data-overview-recent-decisions]')).toContainText(zh ? '近期決策' : 'Recently decided');
    await expect(page.locator('[data-overview-decided]')).toHaveCount(decisions.length);
    await expect(page.locator('[data-overview-row]').filter({hasText: 'lodash'})).toHaveCount(1);
    if (scenario !== 'overview') {
      await expect(page.locator('.pm-overview-side')).toContainText(scenario === 'overview-empty'
        ? zh ? '尚無紀錄' : 'None recorded' : zh ? '未知' : 'Unknown');
      await expect(page.locator('.pm-overview-side').locator('[data-status=unknown]')).toHaveCount(scenario === 'overview-empty' ? 0 : 2);
    }
    composition = 'Live adopted-plan progress, one blocker, two pending decisions, named maintenance finding and recorded milestone dates; Recently decided derives from the demo plan adoption receipts, not the pending count. Not a saved report.';
  } else if (scenario === 'discussions') {
    const row = page.locator('[data-overview-row]').filter({hasText: fixture.copy.blocker});
    await row.getByRole('link', {name: zh ? '討論' : 'Discuss', exact: true}).click();
    await expect(page.locator('[data-discussion-title]')).toHaveText(fixture.copy.blocker);
    await expect(page.locator('[data-discussion-context=governance]').first()).toContainText(fixture.copy.blocker);
    assert.equal(writes.length, 0, 'Opening a discussion must not write');
    await page.locator('[data-discussion-composer]').fill(zh
      ? '請整理這個阻礙，保留尚未確認的預算。'
      : 'Review this blocker and keep the budget uncertainty explicit.');
    await page.getByRole('button', {name: zh ? '只存筆記' : 'Save note only', exact: true}).click();
    await expect(page.locator('.pm-notes')).toContainText(zh ? '請整理這個阻礙' : 'Review this blocker');
    await page.locator('[data-discussion-ask]').click();
    await expect(page.locator('[data-proposal-review]')).toBeVisible();
    await expect(page.locator('[data-pm-plan-proposals]')).toContainText(fixture.copy.proposalSummary);
    assert.deepEqual(writes.map(input => input.body.action), ['pm.thread.create', 'pm.message.append', 'pm.conversation.propose']);
    const proposal = fixture.discussion.proposals.at(-1);
    assert.equal(proposal.adoptable, true);
    assert.equal(proposal.adopted_revision_id, null);
    assert.equal(proposal.execution_authorized, false);
    composition = 'Opened from the Overview blocker using its real Discuss link; automatic title, bound context chip and reviewable unadopted proposal. Three synthetic discussion operations; no adoption or dispatch.';
  } else if (scenario === 'requirements') {
    await openView('requirements');
    const rtm = page.locator('[data-pm-rtm]'), narrow = page.viewportSize().width < 800;
    await rtm.locator(narrow ? `[data-rtm-requirement="${fixture.ids.requirements.email}"]` : `[data-rtm-toggle="${fixture.ids.requirements.email}"]`).click();
    await rtm.locator(`[data-rtm-select="${fixture.ids.work.email}"]`).click();
    await expect(rtm.locator('[data-rtm-pr]')).toHaveCount(2);
    composition = 'Email sign-in requirement expanded, bound work inspector open; recorded acceptance, PR and deployment evidence stay distinct.';
  } else if (scenario === 'issues') {
    await openView('issues');
    await expect(page.locator('[data-reg-table] [data-row-key]')).toHaveCount(3);
    await page.locator('[data-reg-more]').click();
    await expect(page.locator('[data-reg-table] [data-row-key]')).toHaveCount(12);
    await expect(page.locator('[data-reg-table]')).toContainText('lodash');
    composition = 'Both demo register pages loaded: twelve records, including named maintenance findings, risks, issues, an action, proposed changes and an adopted decision. Open records remain open.';
  } else if (scenario === 'schedule') {
    await openView('schedule');
    await expect(page.locator('.schedule-chart [data-schedule-row]').first()).toBeVisible();
    await page.locator('[data-schedule-row="milestone:design_review"] button[aria-expanded=true]').click();
    await expect(page.locator('[data-schedule-row="milestone:recovery_review"] [data-schedule-delta]')).toContainText('+3');
    if (page.viewportSize().width < 800) await expect(page.locator('[data-schedule-axis-caption]')).toBeVisible();
    else assert.ok(await page.locator('[data-schedule-tick]').count() > 0, 'Schedule must retain date-axis labels');
    composition = 'Recorded baseline, forecast and actual timeline with readable date axis; Design review collapsed through its normal control to show Recovery review forecast three days late. Phone tracks share a fitted range. Undated session attempts remain unscheduled.';
  } else if (scenario === 'dependencies') {
    await openView('dependencies');
    await page.getByRole('radio', {name: zh ? '關係圖' : 'Graph', exact: true}).click();
    await expect(page.locator('[data-dep-graph] svg')).toBeVisible();
    await expect(page.locator('.ui-page-header [data-status=partial]')).toBeVisible();
    composition = 'Bounded graph of the pinned synthetic inventory; Partial remains visible, evidence styles distinguish verified lock versions, declarations and inferences.';
  } else if (scenario === 'board') {
    await openView('board');
    await expect(page.locator('[data-pm-kanban-work]')).toHaveCount(12);
    composition = 'Twelve synthetic work items in five workflow lanes; accepted content, work in review and waiting work keep their recorded states.';
  } else if (scenario === 'agents') {
    await openView('agents');
    await expect(page.locator('[data-resource-row]')).toHaveCount(3);
    await expect(page.locator('[data-resource-metrics]')).toContainText(zh ? '已觀測用量' : 'Tokens observed');
    await expect(page.locator('[data-resource-chart]')).toBeVisible();
    composition = 'Review only: sourced usage rows, daily trend and reported quota windows; unavailable source remains unknown.';
  } else if (scenario === 'session') {
    // A hash navigation keeps the selected project and its in-app connection.
    await page.evaluate(hash => {location.hash = hash;}, new URL(origin + demoRoute('manual')).hash);
    await expect(page.locator('.shell-crumb-workspace')).toContainText(fixture.copy.name);
    await expect(page.locator('.shell-workspace-switch')).toContainText(fixture.copy.name);
    await expect(page.locator('[data-session-readonly]')).toContainText(zh ? '僅供檢視' : 'View only');
    await expect(page.locator('#main')).toContainText(fixture.copy.manualNote);
    await expect(page.locator('#workspace-message-input')).toBeHidden();
    await expect(page.getByRole('button', {name: zh ? '送出' : 'Send', exact: true})).toHaveCount(0);
    await expect(page.getByRole('button', {name: zh ? '中斷這一輪' : 'Interrupt turn', exact: true})).toHaveCount(0);
    composition = 'Manual BAT session on demo-host, read-only transcript; no reply, answer or interrupt controls and no writes.';
  }
  if (scenario !== 'discussions') assert.equal(writes.length, 0, 'Browsing must not write');
  return {route: new URL(page.url()).pathname + new URL(page.url()).hash, composition};
}

if (path.resolve(process.argv[1] ?? '') === fileURLToPath(import.meta.url)) {
  const sourceCommit = git('rev-parse', source);
  // Documentation commits may rerun the capture, but product and demo bytes must stay pinned.
  git('diff', '--exit-code', sourceCommit, '--', 'desktop/src', 'desktop/tests/demo', 'src/bat_agent_connector/dashboard');
  await mkdir(output, {recursive: true});
  if (review) await mkdir(review, {recursive: true});
  const server = createServer(async (request, response) => {
    const pathname = new URL(request.url, 'http://127.0.0.1').pathname;
    const file = pathname === '/dashboard/' ? 'index.html' : pathname.slice('/dashboard/'.length);
    if (!pathname.startsWith('/dashboard/') || !assets.includes(file)) {response.writeHead(404).end(); return;}
    try {
      const body = await readFile(path.join(root, 'src/bat_agent_connector/dashboard', file));
      response.writeHead(200, {'Content-Type': file.endsWith('.html') ? 'text/html' : file.endsWith('.css') ? 'text/css' : 'text/javascript',
        'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'"});
      response.end(body);
    } catch {response.writeHead(404).end();}
  });
  await new Promise((resolve, reject) => {server.once('error', reject); server.listen(0, '127.0.0.1', resolve);});
  origin = 'http://127.0.0.1:' + server.address().port;
  let browser;
  try {
    browser = await chromium.launch({args: ['--disable-dev-shm-usage']});
    const images = [], sources = new Set(['scripts/capture_site.mjs', 'desktop/package-lock.json', ...assets.map(file => 'src/bat_agent_connector/dashboard/' + file)]);
    async function include(file) {
      if (sources.has(file)) return;
      sources.add(file);
      const content = await readFile(path.join(root, file), 'utf8');
      for (const match of content.matchAll(/from\s+['"]([^'"]+)['"]/g)) {
        if (match[1].startsWith('.')) await include(path.posix.normalize(path.posix.join(path.posix.dirname(file), match[1])));
      }
    }
    const fixtures = ['desktop/tests/demo/index.ts', 'desktop/tests/conversation-fixture.ts'];
    for (const fixture of fixtures) await include(fixture);
    for (const [language, locale] of [['en', 'en-US'], ['zh', 'zh-TW']]) for (const scenario of [...scenes, ...(review ? reviewScenes : [])]) {
      const responsive = [];
      // The work inspector scrolls independently; give its delivery/attempts room.
      const sceneViewport = {...viewport, height: scenario === 'requirements' ? 1600 : viewport.height};
      let scene;
      for (const width of [390, 768, 1440]) {
        const context = await browser.newContext({locale, viewport: {...sceneViewport, width}, timezoneId: 'UTC', colorScheme: 'light', reducedMotion: 'reduce', serviceWorkers: 'block'});
        try {
          const blocked = [], errors = [];
          await context.route('**/*', route => {
            if (new URL(route.request().url()).origin !== origin) {blocked.push(route.request().url()); return route.abort();}
            return route.continue();
          });
          const page = await context.newPage();
          page.setDefaultTimeout(15000);
          page.on('pageerror', error => errors.push(error.message));
          page.on('console', message => {if (message.type() === 'error') errors.push(message.text());});
          await page.clock.setFixedTime(new Date(fixtureClock));
          scene = await prepare(page, scenario, locale === 'zh-TW' ? locale : 'en');
          await page.evaluate(async () => {
            await document.fonts.ready;
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
          });
          const dimensions = await page.evaluate(() => ({viewport: innerWidth, content: document.documentElement.scrollWidth}));
          assert.ok(dimensions.content <= dimensions.viewport, `${scenario}-${language} overflows at ${width}: ${JSON.stringify(dimensions)}`);
          assert.doesNotMatch(await page.locator('#main').innerText(), /\b(?:undefined|null|NaN)\b/);
          assert.deepEqual(errors, [], 'Browser errors'); assert.deepEqual(blocked, [], 'Unexpected external requests');
          responsive.push({...dimensions, horizontal_overflow: false});
          // Include the whole page so the charter and delivery details are not cut mid-line.
          // This uses browser capture, without changing the app's DOM or styles.
          if (review) await page.screenshot({path: path.join(review, `${scenario}-${language}-${width}.png`), fullPage: true, animations: 'disabled', caret: 'hide'});
          if (scenes.includes(scenario) && width === viewport.width) await page.screenshot({path: path.join(output, `${scenario}-${language}.png`), fullPage: true, animations: 'disabled', caret: 'hide'});
        } finally {await context.close();}
      }
      if (!scenes.includes(scenario)) {
        process.stdout.write(`Reviewed ${scenario}-${language}; 390/768/1440: no horizontal overflow\n`);
        continue;
      }
      const file = `${scenario}-${language}.png`;
      const png = await readFile(path.join(output, file));
      images.push({file, sha256: sha256(png), scenario, locale, viewport: sceneViewport, image_size: {width: png.readUInt32BE(16), height: png.readUInt32BE(20)}, full_page: true, theme: 'light',
        route: scene.route, fixture_sources: fixtures, composition: scene.composition, responsive_checks: responsive});
      process.stdout.write(`Captured ${file}; 390/768/1440: no horizontal overflow\n`);
    }
    assert.equal(images.length, 16);
    const sourceFiles = await Promise.all([...sources].sort().map(async file => sourceEntry(file, await readFile(path.join(root, file)))));
    const manifest = {schema: 'public_ui_screenshots_v1', frontend_source_commit: sourceCommit, source_pin_kind: 'merged', frontend_source_tree: git('rev-parse', sourceCommit + ':desktop/src'),
      captured_at: new Date().toISOString(), fixture_clock: fixtureClock, timezone: 'UTC', browser: browser.version(), node: process.version,
      synthetic_data: true, native_or_live_acceptance: false, dom_content_modified: false, source_files: sourceFiles, images};
    await writeFile(path.join(output, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
  } finally {
    if (browser) await browser.close();
    await new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
  }
}
