import {test, expect} from '@playwright/test';
import {readFileSync} from 'node:fs';

for (const native of [false, true]) for (const locale of ['en-US', 'zh-TW']) for (const width of [390, 768, 1440]) {
  test(`delivery evidence ${native ? 'IPC' : 'HTTP'} ${locale} ${width}`, async ({page}) => {
    await page.setViewportSize({width, height: 900});
    await page.addInitScript(locale => Object.defineProperty(navigator, 'language', {value: locale}), locale);
    const caps = {actor: 'candidate-review', scopes: ['observe', 'integrate'], api_version: 1,
      contract_version: '2026-10-08', hosts: [], repositories: [{repository: 'example/repository'}],
      features: {execution_delivery: {version: 1}}, actions: []};
    const pr = {repository: 'example/repository', pull_number: 1, title: 'Delivery review', state: 'open',
      head_ref: 'feature', head_sha: 'a'.repeat(40), base_ref: 'main', base_sha: 'b'.repeat(40),
      html_url: 'https://github.com/example/repository/pull/1', checks: {}, recipes: [],
      merge: {methods: ['merge'], allowed: false}, metadata_update: {allowed: false},
      integration: {allowed: true, hosts: ['fixture']},
      merge_preview: {preview_id: 'mpv_'+'a'.repeat(32), digest: 'fixture', method: 'merge',
        target: {head_sha: 'a'.repeat(40), base_sha: 'b'.repeat(40)}, blocking: [], commits: [], affected_prs: [], warnings: []}};
    const observations = [
      {streaming: true, observed_at: '2026-10-09T23:20:00Z'},
      {streaming: false, observed_at: 1791588000},
      {streaming: false, pending: {kind: 'ask_user'}, observed_at: 1791588000},
      {streaming: true, stale: true, observed_at: 1791588000},
      {streaming: null, observed_at: null},
      {streaming: false, state: {lifecycle: 'ended'}, observed_at: 1791588000},
    ];
    const candidates = {agent_results: observations.map((session, i) => ({kind: 'checkpoint_run', id: `op_${String(i).repeat(32)}`,
      branch: `agent/result-${i}`, streaming: session.streaming, session,
      result: {status: 'unverified', commit_sha: null}, delivered_to: i === 1 ? [{repository: 'example/repository', pull_number: 2,
        operation_id: 'op_'+'9'.repeat(32), pinned_sha: 'c'.repeat(40), delivered_sha: 'd'.repeat(40)}] : []})), checkpoints: []};
    const projectId = 'prj_'+'a'.repeat(20), work = {...candidates.agent_results[0], kind: 'execution', host: 'fixture',
      session_id: 'managed-session', worktree_id: 'wt_'+'b'.repeat(32), eligible: true, status: 'succeeded',
      operation_id: candidates.agent_results[0].id, action: 'repository.continue', title: 'Implement requested work'};
    const recoveryId = 'op_'+'7'.repeat(32);
    const errors: string[] = [], posts: any[] = [], reads: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    const dispatch = async (input: any) => {
      if (input.method === 'POST') {posts.push(input); return {status: 200, data: {operation: {operation_id: 'op_'+'8'.repeat(32), status: 'failed', error_code: 'FIXTURE_STOP'}}};}
      reads.push(input.path);
      const path = input.path.split('?')[0];
      return {status: 200, data: path === '/capabilities' ? caps : path === '/bootstrap' ? {capabilities: caps,
        sync: {version: 1, server_id: 'fixture', principal_id: 'reader', checkpoint: {cursor: 0, token: 'zero'}}}
        : path === '/events' ? {events: [], next_cursor: 0, head_cursor: 0, has_more: false, sync: {checkpoint: {cursor: 0, token: 'zero'}}}
        : path === '/projects/'+projectId ? {project: {project_id: projectId, name: 'Daily trial', repositories: ['example/repository'],
          version: 1, counts: {total: 0, approved: 0}}, path: [], sub_projects: [], work_items: [], work: [work]}
        : path === '/worktrees/'+work.worktree_id ? {worktree: {resource_id: work.worktree_id, host: 'fixture',
          branch: work.branch, worktree_path: '/srv/managed/result', intent_type: 'operation', intent_id: work.id,
          known_sessions: [{host: 'fixture', session_id: work.session_id, observation: work.session}], work: [work]}}
        : path === '/operations/'+recoveryId ? {operation: {operation_id: recoveryId, action: 'worktree.merge', status: 'needs_attention',
          actor: 'fixture', entry: 'http', created_at: 1791588000, target: {host: 'fixture', session_id: work.session_id},
          external_refs: {host: 'fixture', session_id: work.session_id, carrier_paths: ['/srv/managed/result', '/srv/managed/repository']},
          steps: [{name: 'merge.reserve', status: 'succeeded'}, {name: 'merge.frame', status: 'uncertain', started_at: 1791588000}]}}
        : path.endsWith('/history') ? {events: [], next_cursor: null} : path.endsWith('/relations') ? {relations: [], next_cursor: null}
        : path === '/integrations/candidates' ? {...candidates, selected: work} : path.startsWith('/repositories/') ? {pull_request: pr}
        : {sessions: [], operations: [], hosts: [], work_items: []}};
    };
    if (native) {
      await page.exposeFunction('candidateConnector', dispatch);
      await page.addInitScript(caps => Object.assign(window, {isTauri: true, __TAURI_INTERNALS__: {invoke: async (command: string, args: any) => {
        if (command === 'native_status') return {endpoint: 'https://central.example/', credential_available: true};
        if (command === 'connector_connect') return caps;
        if (command === 'connector_disconnect') return null;
        if (command === 'connector_request') return (window as any).candidateConnector(args.input);
        throw new Error(command);
      }}}), caps);
    } else await page.addInitScript(() => sessionStorage.setItem('batc.dashboard.token', 'fixture-token'));
    await page.route('**/api/v1/**', async route => {
      if (native) throw new Error('IPC must not send browser API requests');
      const req = route.request(), url = new URL(req.url());
      const response = await dispatch({method: req.method(), path: url.pathname.slice(7)+url.search, body: req.postDataJSON()});
      await route.fulfill({status: response.status, json: response.data});
    });
    if (process.env.DELIVERY_BASELINE_ASSET) await page.route('**/dashboard/app.js', route => route.fulfill({
      contentType: 'text/javascript', body: readFileSync(process.env.DELIVERY_BASELINE_ASSET!, 'utf8')}));
    await page.goto('/dashboard/#/delivery');
    await page.getByPlaceholder('123').fill('1');
    await page.getByRole('button', {name: locale === 'zh-TW' ? '讀取 PR' : 'Load PR', exact: true}).click();
    await expect(page.getByText('agent/result-5', {exact: true})).toBeVisible();
    await page.screenshot({path: `${process.env.DELIVERY_SCREENSHOTS || 'test-results'}/candidates-${native ? 'IPC' : 'HTTP'}-${locale}-${width}.png`, fullPage: true});
    for (const [route, shot] of [['worktree/'+work.worktree_id, 'worktree'], ['op/'+recoveryId, 'merge-recovery']]) {
      await page.goto('/dashboard/#/'+route);
      await expect(page.locator('#main')).toContainText(shot === 'worktree' ? work.worktree_id : 'worktree.merge');
      if (!process.env.DELIVERY_BASELINE) {
        if (shot === 'worktree') {
          await expect(page.locator('#main').getByText('/srv/managed/result', {exact: true})).toBeVisible();
          await expect(page.locator('a[href="#/cleanup/host/fixture"]')).toBeVisible();
          await expect(page.locator('#main > .panel > details')).not.toHaveAttribute('open', '');
        } else {
          await expect(page.locator('[data-merge-recovery]')).toContainText('/srv/managed/repository');
          await expect(page.locator('[data-merge-recovery]')).toContainText(locale === 'zh-TW' ? '尚無正面 ACK 證據' : 'No positive ACK evidence');
        }
      }
      await page.screenshot({path: `${process.env.DELIVERY_SCREENSHOTS || 'test-results'}/${shot}-${native ? 'IPC' : 'HTTP'}-${locale}-${width}.png`, fullPage: true});
    }
    await page.goto('/dashboard/#/delivery');
    await expect(page.getByText('agent/result-5', {exact: true})).toBeVisible();
    if (!process.env.DELIVERY_BASELINE) {
      const row = (i: number) => page.getByText(`agent/result-${i}`, {exact: true}).locator('..');
      const labels = locale === 'zh-TW' ? ['輸出中', '未輸出', '等你回答', '觀測待更新', 'BAT 未回報活動狀態', '已結束']
        : ['streaming', 'not streaming', 'asking you', 'Observation needs updating', 'BAT did not report activity', 'ended'];
      for (let i=0; i<6; i++) {
        await expect(row(i)).toContainText(labels[i]);
        await expect(row(i)).toContainText(locale === 'zh-TW' ? '成果尚未驗證' : 'Result unverified');
        await expect(row(i)).not.toContainText(locale === 'zh-TW' ? '已完成' : 'Done');
      }
      await expect(row(1).getByRole('link')).toHaveAttribute('href', 'https://github.com/example/repository/pull/2');
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.goto('/dashboard/#/project/'+projectId);
      const dispatched = page.locator('[data-project-work]');
      await expect(dispatched).toContainText('Implement requested work');
      await expect(dispatched.locator('a[href="#/session/fixture/managed-session"]')).toBeVisible();
      await expect(dispatched.locator('a[href="#/worktree/'+work.worktree_id+'"]')).toBeVisible();
      await page.screenshot({path: `${process.env.DELIVERY_SCREENSHOTS || 'test-results'}/project-${native ? 'IPC' : 'HTTP'}-${locale}-${width}.png`, fullPage: true});
      await dispatched.getByRole('link', {name: locale === 'zh-TW' ? '檢視成果並加入 PR' : 'Review result and add to PR'}).click();
      await expect(page.getByPlaceholder('owner/name')).toHaveValue('example/repository');
      await expect(page.getByPlaceholder('123')).toHaveValue('');
      expect(posts).toEqual([]);
      await page.getByPlaceholder('123').fill('1');
      await page.getByRole('button', {name: locale === 'zh-TW' ? '讀取 PR' : 'Load PR', exact: true}).click();
      const selected = page.locator('[data-delivery-source="'+work.id+'"]');
      await expect(selected).toBeVisible();
      expect(reads.some(path => path.includes('source_kind=execution') && path.includes('source_id='+work.id))).toBe(true);
      await expect(page.getByText('agent/result-1', {exact: true})).toHaveCount(0);
      await selected.getByRole('button', {name: locale === 'zh-TW' ? '加入' : 'Add', exact: true}).click();
      await expect.poll(() => posts.length).toBe(1);
      expect(posts[0].body).toMatchObject({action: 'integration.preview', target: {host: 'fixture', repository: 'example/repository', pull_number: 1},
        params: {sources: [{kind: 'execution', id: work.id}]}});
    }
    expect(errors).toEqual([]);
  });
}
