// Real shared frontend + synthetic API fixtures. No external services or DOM replacement.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {createServer} from 'node:http';
import {mkdir, readFile, writeFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium, expect} from '../desktop/node_modules/@playwright/test/index.mjs';
import {workspaceFixture, mountWorkspace, workspaceRoute} from '../desktop/tests/workspace-fixture.ts';
import {publishedFixture, dispatchProject, selected} from '../desktop/tests/repository-start-fixture.ts';
import {mountConversation} from '../desktop/tests/conversation-fixture.ts';
import {pmFixture, pmRoute, workspaceId, threadId} from '../desktop/tests/pm-fixture.ts';
import {fixture as kanbanFixture} from '../desktop/tests/pm-kanban-fixture.ts';

const root = fileURLToPath(new URL('../', import.meta.url));
const source = '662460311345db7dd0a9fe65ea8010dc3e5e4897';
const output = path.join(root, 'site/images');
const review = process.env.CAPTURE_REVIEW_DIR;
const fixtureClock = '2026-10-12T12:00:00Z';
const viewport = {width: 1440, height: 960};
const assets = ['index.html', 'app.js', 'app.css', 'i18n.js'];
const git = (...args) => execFileSync('git', args, {cwd: root, encoding: 'utf8'}).trim();
const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');
// A later documentation commit may rerun this exact capture; product bytes must stay pinned.
git('diff', '--exit-code', source, '--', 'desktop/src', 'src/bat_agent_connector/dashboard');
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
const origin = 'http://127.0.0.1:' + server.address().port;

function pmDemo(zh) {
  const f = pmFixture(), s = f.state;
  s.workspaces[0].name = zh ? '文件搜尋' : 'Documentation search';
  s.thread.title = zh ? '搜尋沒有結果時，下一步是什麼？' : 'What happens when search finds nothing?';
  s.messages[0].content = zh ? '保留原本的搜尋文字，提供清楚的修改提示，讓讀者可以繼續找文件。' : 'Keep the search text and offer a clear suggestion so readers can continue finding documentation.';
  s.inputs[0].value = zh ? '保留搜尋文字與輸入焦點' : 'Preserve the search text and input focus';
  s.inputs[1].value = zh ? '是否需要提供推薦文件，仍待確認' : 'Whether to suggest related documents remains undecided';
  s.inputs[1].source_ref.ref = zh ? '討論中的候選方向' : 'Candidate from the discussion';
  return f;
}

async function prepare(page, scenario, language, locale) {
  const zh = language === 'zh';
  if (scenario === 'workspace') {
    const f = workspaceFixture(locale);
    await mountWorkspace(page, false, f);
    await page.goto(origin + '/dashboard/' + workspaceRoute);
    await page.locator('.conversation .msg').last().waitFor();
    await page.locator('.workspace-tree a[href^="#/work/"]').first().waitFor();
    return {route: '/dashboard/' + workspaceRoute, fixtures: ['desktop/tests/workspace-fixture.ts'],
      composition: 'Project tree, original conversation and result inspector; ongoing synthetic work.', check: () => assert.equal(f.data.posts.length, 0)};
  }
  if (scenario === 'dispatch') {
    const f = await publishedFixture(page, false, {dispatch: true, scopes: ['observe', 'start', 'manage'],
      project: {project_id: dispatchProject, name: zh ? '文件搜尋' : 'Documentation search',
        description: zh ? '讓讀者更快找到需要的文件。' : 'Help readers find the right documentation.',
        repositories: ['example/project'], version: 1, archived: false, counts: {total: 0, approved: 0}}});
    await page.goto(origin + '/dashboard/#/project/' + dispatchProject);
    await page.locator('a[href^="#/dispatch/"]').click();
    const form = page.locator('[data-published-start]');
    await form.waitFor();
    await form.locator('select').first().selectOption(JSON.stringify(selected));
    await form.locator('input').first().fill('main');
    await form.locator('textarea').first().fill(zh
      ? '改善文件搜尋的空結果提示，補上相關測試，並提供變更摘要供審閱。'
      : 'Improve the empty-results message in documentation search. Add focused tests and provide a change summary for review.');
    return {route: new URL(page.url()).pathname + new URL(page.url()).hash, fixtures: ['desktop/tests/repository-start-fixture.ts'],
      composition: 'Unsubmitted project dispatch form; no preview or execution requested.', check: () => assert.equal(f.posts.length, 0)};
  }
  const f = pmDemo(zh);
  if (scenario === 'pm-planning') {
    const s = f.state;
    s.plans = true; s.start = true; s.thread.mode = 'planning'; s.thread.draft_id = 'pmd_' + '6'.repeat(32);
    s.draft = {draft_id: s.thread.draft_id, title: s.thread.title,
      rationale: zh ? '先釐清搜尋體驗與待決問題。' : 'Clarify the search experience and open questions first.',
      selected_inputs: [s.inputs[0]], candidate_inputs: [s.inputs[1]], unknowns: [{slot: 'finance'}]};
    const title = zh ? '文件搜尋：空結果體驗' : 'Documentation search: empty results';
    const markdown = zh
      ? '# 文件搜尋：空結果體驗\n\n讓讀者在搜尋沒有結果時，能修改查詢並繼續找到文件。\n\n## 本輪範圍\n\n- 保留搜尋文字與輸入焦點。\n- 提供清楚的修改查詢提示。\n- 推薦文件不列入本輪。\n\n## 驗收條件草案\n\n- 空結果畫面保留原始查詢。\n- 使用鍵盤即可修改並重新搜尋。\n- 中英文介面都有可理解的下一步。\n\n## 待確認\n\n驗收條件與投入預算仍待負責人審閱。本版尚未採用。'
      : '# Documentation search: empty results\n\nHelp readers refine an empty search and continue finding documentation.\n\n## Scope for this iteration\n\n- Preserve the search text and input focus.\n- Offer a clear suggestion for changing the query.\n- Keep document recommendations outside this iteration.\n\n## Proposed acceptance criteria\n\n- The empty-results view retains the original query.\n- Readers can edit and search again using the keyboard.\n- English and Chinese views offer a clear next step.\n\n## Open decisions\n\nAcceptance criteria and budget still need an owner review. This revision has not been adopted.';
    await f.dispatch({method: 'POST', path: '/operations', idempotency_key: 'public-synthetic-plan',
      body: {action: 'pm.plan.draft', target: {workspace_id: workspaceId, thread_id: threadId},
        params: {title, markdown, previous_revision_id: null}, preconditions: {expected_version: 1, expected_plan_version: 0}}});
    await mountConversation(page, false, f.dispatch);
    await page.goto(origin + pmRoute);
    await expect(page.locator('.pm-plans > h3')).toHaveText(zh ? '專案計畫' : 'Project plan');
    await page.locator('[data-pm-revision] > summary').click();
    await page.locator('.pm-plan-document').waitFor();
    return {route: pmRoute, anchor: '.pm-plans', fixtures: ['desktop/tests/pm-fixture.ts'],
      composition: 'Fixed, unadopted Planning revision with unresolved decisions; original plan disclosure opened and page scrolled to it.',
      check: () => {assert.equal(s.writes.length, 1); assert.equal(s.planHead.accepted_revision_id, null);}};
  }
  const board = kanbanFixture();
  const examples = [
    ['backlog', '確認鍵盤操作的驗收條件', 'Define keyboard acceptance criteria'],
    ['backlog', '整理繁體中文提示文字', 'Prepare Traditional Chinese copy'],
    ['in_progress', '保留搜尋文字與輸入焦點', 'Preserve search text and input focus'],
    ['in_progress', '補上空結果回歸測試', 'Add empty-results regression tests'],
    ['waiting', '確認推薦文件是否列入範圍', 'Decide the scope of recommendations'],
    ['review', '審閱空結果提示文字', 'Review the empty-results message'],
    ['accepted', '整理現有搜尋流程', 'Document the current search flow'],
  ];
  board.state.cards = examples.map(([column, chinese, english], i) => {
    const card = structuredClone(board.state.cards[column === 'accepted' ? 2 : column === 'review' ? 1 : 0]);
    const id = 'wi_' + String(i + 1).repeat(20);
    card.work_item_id = card.binding.work_item_id = card.work.work_item_id = id;
    card.binding.sequence = i + 1;
    card.binding.binding_id = 'pmwb_' + String(i + 1).repeat(32);
    card.binding.requirement_ids = ['search.empty_results'];
    card.work.title = zh ? chinese : english;
    card.work.state = column === 'accepted' || column === 'review' ? 'done' : column === 'backlog' ? 'todo' : 'doing';
    card.column = column;
    card.completion = {approved: column === 'accepted', pending: column === 'review'};
    card.flow.wip = ['in_progress', 'waiting', 'review'].includes(column);
    card.flow.age_seconds = card.flow.wip ? 7800 + i * 3600 : null;
    card.latest_capture = null;
    if (column !== 'in_progress') {
      card.execution.accepted_attempts = 0;
      card.execution.tasks = []; card.execution.attempts = []; card.execution.operations = [];
    }
    return card;
  });
  await mountConversation(page, false, async input => {
    const pathname = new URL(input.path, 'http://fixture').pathname;
    if (pathname.includes('/kanban')) return board.dispatch(input);
    const result = await f.dispatch(input);
    const caps = pathname === '/bootstrap' ? result.data.capabilities : pathname === '/capabilities' ? result.data : null;
    if (caps) caps.features.pm_workspaces.kanban = true;
    return result;
  });
  const route = '/dashboard/#/pm/' + workspaceId;
  await page.goto(origin + route);
  await expect(page.locator('[data-pm-kanban-work]')).toHaveCount(examples.length);
  await expect(page.locator('[data-pm-kanban] h2')).toHaveText(zh ? '工作看板' : 'Work board');
  return {route, fixtures: ['desktop/tests/pm-fixture.ts', 'desktop/tests/pm-kanban-fixture.ts'],
    composition: 'Seven synthetic work items across the existing five workflow columns; quality remains unassessed, partial sources explicit.',
    check: () => {assert.equal(board.state.writes.length, 0); assert.equal(f.state.writes.length, 0);}};
}

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
  for (const [language, locale] of [['en', 'en-US'], ['zh', 'zh-TW']]) {
    for (const scenario of ['workspace', 'dispatch', 'pm-planning', 'pm-kanban']) {
      const theme = 'light';
      const context = await browser.newContext({locale, viewport, timezoneId: 'UTC', colorScheme: theme, reducedMotion: 'reduce', serviceWorkers: 'block'});
      try {
        const blocked = [], errors = [];
        await context.route('**/*', route => {
          if (new URL(route.request().url()).origin !== origin) {blocked.push(route.request().url()); return route.abort();}
          return route.continue();
        });
        const page = await context.newPage();
        page.setDefaultTimeout(15000);
        page.on('pageerror', error => errors.push(error.message));
        await page.clock.setFixedTime(new Date(fixtureClock));
        const scene = await prepare(page, scenario, language, locale);
        const responsive = [];
        for (const width of [390, 768, 1440]) {
          await page.setViewportSize({width, height: viewport.height});
          await page.evaluate(async () => {
            await document.fonts.ready;
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
          });
          if (scene.anchor) await page.locator(scene.anchor).evaluate(element => window.scrollTo(0,
            element.getBoundingClientRect().top + window.scrollY - document.querySelector('header.top').getBoundingClientRect().height));
          else await page.evaluate(() => window.scrollTo(0, 0));
          await page.evaluate(() => document.fonts.ready);
          const dimensions = await page.evaluate(() => ({viewport: window.innerWidth, content: document.documentElement.scrollWidth}));
          assert.ok(dimensions.content <= dimensions.viewport, `${scenario}-${language} overflows at ${width}: ${JSON.stringify(dimensions)}`);
          responsive.push({...dimensions, horizontal_overflow: false});
          if (review) await page.screenshot({path: path.join(review, `${scenario}-${language}-${width}.png`), animations: 'disabled', caret: 'hide'});
        }
        scene.check();
        assert.deepEqual(errors, [], 'Browser errors'); assert.deepEqual(blocked, [], 'Unexpected external requests');
        const file = `${scenario}-${language}.png`;
        await page.screenshot({path: path.join(output, file), animations: 'disabled', caret: 'hide'});
        for (const fixture of scene.fixtures) await include(fixture);
        images.push({file, sha256: sha256(await readFile(path.join(output, file))), scenario, locale, viewport, theme,
          route: scene.route, fixture_sources: scene.fixtures, composition: scene.composition, responsive_checks: responsive});
        process.stdout.write(`Captured ${file}; 390/768/1440: no horizontal overflow\n`);
      } finally {await context.close();}
    }
  }
  assert.equal(images.length, 8);
  const sourceFiles = await Promise.all([...sources].sort().map(async file => ({file, sha256: sha256(await readFile(path.join(root, file)))})));
  const manifest = {schema: 'public_ui_screenshots_v1', frontend_source_commit: source, frontend_source_tree: git('rev-parse', source + ':desktop/src'),
    captured_at: new Date().toISOString(), fixture_clock: fixtureClock, timezone: 'UTC', browser: browser.version(), node: process.version,
    synthetic_data: true, native_or_live_acceptance: false, dom_content_modified: false, source_files: sourceFiles, images};
  await writeFile(path.join(output, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
} finally {
  if (browser) await browser.close();
  await new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
}
