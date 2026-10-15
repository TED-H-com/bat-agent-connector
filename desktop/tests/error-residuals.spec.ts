import {test, expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';

const outDir = '/home/ted-h/agent-work/bac-ux/tmp/error-residuals';
const serverMsg = 'Requested entity not found on remote BAT host';

for (const {locale, summaryText, lang, width} of [
  {locale: 'en-US', summaryText: 'This record is unavailable. Refresh the view.', lang: 'en', width: 1440},
  {locale: 'zh-TW', summaryText: '找不到這筆資料，請重新整理。', lang: 'zh', width: 390},
]) {
  test.describe(`error residuals ${locale}`, () => {
    test.use({locale, viewport: {width, height: 900}});

    test(`site1: inlineError shows localized summary with code in closed details (${locale})`, async ({page}) => {
      await mkdir(outDir, {recursive: true});
      await page.goto('/ui-components.html');
      await page.evaluate(async ({msg}) => {
        const {inlineError} = await import('/src/ui/components.js');
        const {ApiError} = await import('/src/error-presentation.js');
        const err = new ApiError(404, 'NOT_FOUND', msg);
        const root = document.getElementById('fixture')!;
        root.replaceChildren(inlineError({error: err}));
      }, {msg: serverMsg});

      const alert = page.locator('.ui-inline-error');
      await expect(alert).toBeVisible();
      await expect(alert.locator('[data-error-summary]')).toHaveText(summaryText);

      // Closed details check
      const details = alert.locator('details.ui-disclosure');
      await expect(details).toHaveCount(1);
      await expect(details).not.toHaveAttribute('open', '');

      // Outside details, in zh-TW no English server message
      if (locale === 'zh-TW') {
        const outsideText = await alert.evaluate(node => {
          const clone = node.cloneNode(true) as HTMLElement;
          clone.querySelectorAll('details').forEach(d => d.remove());
          return clone.textContent || '';
        });
        expect(outsideText).not.toContain(serverMsg);
        expect(outsideText).not.toContain('NOT_FOUND');
      }

      await page.screenshot({path: `${outDir}/after-site1-inline-error-${lang}-${width}.png`});

      // Expand details and check code and message
      await details.locator('summary').click();
      await expect(alert.locator('[data-error-details] code')).toHaveText('NOT_FOUND');
      await expect(alert.locator('[data-error-details] p')).toHaveText(serverMsg);
    });

    test(`site2: operations list read failure shows localized summary and code in details (${locale})`, async ({page}) => {
      await mkdir(outDir, {recursive: true});
      await page.goto('/ui-components.html');
      await page.evaluate(async ({msg}) => {
        const {operationList} = await import('/src/operation-list.js');
        const {ApiError} = await import('/src/error-presentation.js');
        const {h} = await import('/src/ui/dom.js');
        const {t} = await import('/src/i18n.js');
        const api = async () => {
          throw new ApiError(500, 'NOT_FOUND', msg);
        };
        const ops = operationList({h, t, api, guard: () => {}, statuses: '', namespace: 'demo'});
        const root = document.getElementById('fixture')!;
        root.replaceChildren(ops.box);
        try { await ops.load(); } catch {}
      }, {msg: serverMsg});

      const statusBox = page.locator('#fixture [role="status"]');
      await expect(statusBox.locator('[data-error-summary]')).toHaveText(summaryText);

      const details = statusBox.locator('details.ui-disclosure');
      await expect(details).toHaveCount(1);
      await expect(details).not.toHaveAttribute('open', '');

      if (locale === 'zh-TW') {
        const outsideText = await statusBox.evaluate(node => {
          const clone = node.cloneNode(true) as HTMLElement;
          clone.querySelectorAll('details').forEach(d => d.remove());
          return clone.textContent || '';
        });
        expect(outsideText).not.toContain(serverMsg);
        expect(outsideText).not.toContain('NOT_FOUND');
      }

      await page.screenshot({path: `${outDir}/after-site2-operations-list-${lang}-${width}.png`});

      await details.locator('summary').click();
      await expect(statusBox.locator('[data-error-details] code')).toHaveText('NOT_FOUND');
      await expect(statusBox.locator('[data-error-details] p')).toHaveText(serverMsg);
    });

    test(`site2: operations detail failure shows localized summary with code in details (${locale})`, async ({page}) => {
      await mkdir(outDir, {recursive: true});
      await page.goto('/ui-components.html');
      const opId = 'op_' + '2'.repeat(32);
      await page.evaluate(async ({msg, id}) => {
        const {operationList} = await import('/src/operation-list.js');
        const {ApiError} = await import('/src/error-presentation.js');
        const {h} = await import('/src/ui/dom.js');
        const {t} = await import('/src/i18n.js');
        const api = async (method: string, path: string) => {
          if (path.startsWith('/operations?')) {
            return {
              operations: [{
                operation_id: id,
                action: 'task.pause',
                actor: 'tester',
                status: 'running',
                created_at: Date.now() / 1000,
              }],
              next_before: null,
            };
          }
          throw new ApiError(500, 'NOT_FOUND', msg);
        };
        const ops = operationList({h, t, api, guard: () => {}, statuses: '', namespace: 'demo'});
        const root = document.getElementById('fixture')!;
        root.replaceChildren(ops.box);
        await ops.load();
        const row = root.querySelector(`[data-row-key="${id}"]`) as HTMLElement;
        row?.click();
      }, {msg: serverMsg, id: opId});

      const inspector = page.locator('.ui-inspector');
      await expect(inspector).toBeVisible();
      const inlineErr = inspector.locator('.ui-inline-error');
      await expect(inlineErr.locator('[data-error-summary]')).toHaveText(summaryText);

      const details = inlineErr.locator('details.ui-disclosure');
      await expect(details).toHaveCount(1);
      await expect(details).not.toHaveAttribute('open', '');

      if (locale === 'zh-TW') {
        const outsideText = await inlineErr.evaluate(node => {
          const clone = node.cloneNode(true) as HTMLElement;
          clone.querySelectorAll('details').forEach(d => d.remove());
          return clone.textContent || '';
        });
        expect(outsideText).not.toContain(serverMsg);
        expect(outsideText).not.toContain('NOT_FOUND');
      }

      await page.screenshot({path: `${outDir}/after-site2-operations-detail-${lang}-${width}.png`});

      await details.locator('summary').click();
      await expect(inlineErr.locator('[data-error-details] code')).toHaveText('NOT_FOUND');
      await expect(inlineErr.locator('[data-error-details] p')).toHaveText(serverMsg);
    });

    test(`site4: model preferences shows localized summary in status (${locale})`, async ({page}) => {
      await mkdir(outDir, {recursive: true});
      await page.goto('/ui-components.html');
      await page.evaluate(async ({msg}) => {
        const {modelPreferencesPanel} = await import('/src/model-preferences.js');
        const {ApiError} = await import('/src/error-presentation.js');
        const {h} = await import('/src/ui/dom.js');
        const {t} = await import('/src/i18n.js');
        const api = async () => {
          return {
            version: 1, host: 'demo',
            model_preferences: {initial_agent: null, initial_model: null, last_agent: null, last_model: null, hidden: [], order: [], revision: 1},
            model_catalog: {agent: 'claude', status: 'available', source: 'bat_host', observed_at: Date.now() / 1000, stale: false, models: []},
            unknown_models: [], usage: {source: 'bat_host', status: 'available', observed_at: Date.now() / 1000, providers: []},
          };
        };
        const submit = async () => {
          throw new ApiError(404, 'NOT_FOUND', msg);
        };
        const caps = () => ({
          actor: 'tester',
          hosts: [{host: 'demo', writes: true, orchestrate: true}],
          actions: [{action: 'preferences.models.update', allowed: true}],
          features: {host_preferences: {version: 1}},
        });
        const panel = modelPreferencesPanel({
          h, t, api, caps, guard: () => {}, submit,
          storageKey: 'test.models.error', onEvents: () => () => {}, errorBox: () => {},
        });
        panel.box.open = true;
        const root = document.getElementById('fixture')!;
        root.replaceChildren(panel.box);
        const hostSelect = panel.box.querySelector('select[aria-label]') as HTMLSelectElement;
        hostSelect.value = 'demo';
        hostSelect.dispatchEvent(new Event('change', {bubbles: true}));
        await new Promise(r => setTimeout(r, 100));
        // Trigger save
        const saveBtn = panel.box.querySelector('button.primary') as HTMLButtonElement;
        saveBtn?.click();
      }, {msg: serverMsg});

      const status = page.locator('[data-model-preferences] [role="status"]');
      await expect(status).toHaveText(summaryText);
      if (locale === 'zh-TW') {
        expect(await status.innerText()).not.toContain(serverMsg);
        expect(await status.innerText()).not.toContain('NOT_FOUND');
      }

      await page.screenshot({path: `${outDir}/after-site4-model-preferences-${lang}-${width}.png`});
    });

    test(`site4: project skills shows localized summary in status (${locale})`, async ({page}) => {
      await mkdir(outDir, {recursive: true});
      await page.goto('/ui-components.html');
      await page.evaluate(async ({msg}) => {
        const {projectSkillsPanel} = await import('/src/project-skills.js');
        const {ApiError} = await import('/src/error-presentation.js');
        const {h} = await import('/src/ui/dom.js');
        const {t} = await import('/src/i18n.js');
        const api = async (method: string, path: string) => {
          if (path.startsWith('/workspaces')) {
            return {workspaces: [{host: 'demo', workspace_id: 'ws-1', name: 'Demo Workspace'}], errors: {}};
          }
          return {
            version: 1, project_id: 'prj_test', host: 'demo', workspace_id: 'ws-1',
            catalog: {source: 'host_workspace', status: 'available', complete: true, stale: false, catalog_digest: 'a'.repeat(64), observed_at: Date.now() / 1000, skills: []},
            selection: {revision: 1, host: 'demo', workspace_id: 'ws-1', selected: [], application: 'applied', unresolved: []},
          };
        };
        const submit = async () => {
          throw new ApiError(404, 'NOT_FOUND', msg);
        };
        const caps = () => ({
          hosts: [{host: 'demo'}],
          actions: [{action: 'project.skills.update', allowed: true}],
          features: {project_skills: {version: 1}},
        });
        const panel = projectSkillsPanel({
          h, t, api, caps, guard: () => {}, submit,
          projectId: 'prj_test', storageKey: 'test.skills.error', onEvents: () => () => {}, errorBox: () => {},
        });
        panel.box.open = true;
        const root = document.getElementById('fixture')!;
        root.replaceChildren(panel.box);
        const hostSelect = panel.box.querySelector('select[aria-label]') as HTMLSelectElement;
        hostSelect.value = 'demo';
        hostSelect.dispatchEvent(new Event('change', {bubbles: true}));
        await new Promise(r => setTimeout(r, 100));
        const wsSelect = panel.box.querySelectorAll('select[aria-label]')[1] as HTMLSelectElement;
        wsSelect.value = 'ws-1';
        wsSelect.dispatchEvent(new Event('change', {bubbles: true}));
        await new Promise(r => setTimeout(r, 100));
        const saveBtn = panel.box.querySelector('button.primary') as HTMLButtonElement;
        saveBtn?.click();
      }, {msg: serverMsg});

      const status = page.locator('[data-project-skills] [role="status"]');
      await expect(status).toHaveText(summaryText);
      if (locale === 'zh-TW') {
        expect(await status.innerText()).not.toContain(serverMsg);
        expect(await status.innerText()).not.toContain('NOT_FOUND');
      }

      await page.screenshot({path: `${outDir}/after-site4-project-skills-${lang}-${width}.png`});
    });

    test(`site5: composer shortcut shows localized summary in status (${locale})`, async ({page}) => {
      await mkdir(outDir, {recursive: true});
      await page.goto('/ui-components.html');
      await page.evaluate(async ({msg}) => {
        const {composerShortcut} = await import('/src/composer-shortcut.js');
        const {ApiError} = await import('/src/error-presentation.js');
        const {h} = await import('/src/ui/dom.js');
        const {t} = await import('/src/i18n.js');
        const input = document.createElement('textarea');
        const btn = document.createElement('button');
        const originalSet = localStorage.setItem.bind(localStorage);
        localStorage.setItem = () => {
          throw new ApiError(404, 'NOT_FOUND', msg);
        };
        const comp = composerShortcut({h, t, input, button: btn, storageKey: 'test.shortcut', guard: () => {}});
        const root = document.getElementById('fixture')!;
        root.replaceChildren(comp.box);
        const select = comp.box.querySelector('select') as HTMLSelectElement;
        select.value = 'enter';
        select.dispatchEvent(new Event('change', {bubbles: true}));
        localStorage.setItem = originalSet;
      }, {msg: serverMsg});

      const status = page.locator('.composer-shortcut [role="status"]');
      await expect(status).toHaveText(summaryText);
      if (locale === 'zh-TW') {
        expect(await status.innerText()).not.toContain(serverMsg);
        expect(await status.innerText()).not.toContain('NOT_FOUND');
      }

      await page.screenshot({path: `${outDir}/after-site5-composer-shortcut-${lang}-${width}.png`});
    });

    test(`site5: artifact content fail shows localized summary in status (${locale})`, async ({page}) => {
      await mkdir(outDir, {recursive: true});
      const artId = 'art_' + '3'.repeat(32);
      await page.addInitScript(() => sessionStorage.setItem('batc.dashboard.token', 'fixture-token'));
      await page.route('**/api/v1/**', async route => {
        const url = new URL(route.request().url());
        const path = url.pathname.replace('/api/v1', '');
        if (path === '/capabilities' || path === '/bootstrap') {
          return route.fulfill({status: 200, json: {
            actor: 'reviewer', scopes: ['observe', 'approve'],
            capabilities: {actor: 'reviewer', scopes: ['observe', 'approve'], features: {checkpoints: []}, actions: [{action: 'artifact.accept', allowed: true}]},
            features: {checkpoints: []},
            artifacts: {capture: {managed_single_file: true}, limits: {max_file_bytes: 1024}},
            actions: [{action: 'artifact.accept', allowed: true}],
            sync: {version: 1, server_id: 'srv', principal_id: 'p1', checkpoint: {cursor: 0, token: 't'}},
          }});
        }
        if (path === `/artifacts/${artId}/revisions/1` || path === `/artifacts/${artId}`) {
          const executionId = 'op_' + '3'.repeat(32);
          return route.fulfill({status: 200, json: {
            artifact: {
              artifact_id: artId,
              revision: 1,
              digest: 'b'.repeat(64),
              size_bytes: 20,
              display_name: 'test.txt',
              state: 'ready',
              operation_id: executionId,
              source: {
                kind: 'managed_capture',
                operation_id: executionId,
                fingerprint: 'f'.repeat(64),
                source: {
                  host: 'demo', session_id: 's1', root: '/tmp', repository_root: '/tmp',
                  provenance: 'connector_managed',
                  selector: {execution_operation_id: executionId},
                  lineage: {kind: 'execution_operation', execution_operation_id: executionId},
                },
                evidence: {size_bytes: 20, digest: 'b'.repeat(64), head_sha: '1'.repeat(40)},
                relative_path: 'test.txt',
              },
              acceptances: [],
            },
          }});
        }
        return route.fulfill({status: 200, json: {}});
      });
      await page.goto(`/dashboard/#/artifact-review/artifact/${artId}/1`);
      const previewBtn = page.getByRole('button', {name: locale === 'zh-TW' ? '預覽' : 'Preview', exact: true});
      await previewBtn.waitFor({state: 'visible'});

      // Next request to artifact fails with NOT_FOUND
      await page.route(`**/api/v1/artifacts/${artId}/revisions/1`, async route => {
        return route.fulfill({status: 404, json: {error: {code: 'NOT_FOUND', message: serverMsg}}});
      });
      await previewBtn.click();

      const status = page.locator('.artifact-content [role="status"]');
      await expect(status).toHaveText(summaryText);
      if (locale === 'zh-TW') {
        expect(await status.innerText()).not.toContain(serverMsg);
        expect(await status.innerText()).not.toContain('NOT_FOUND');
      }

      await page.screenshot({path: `${outDir}/after-site5-artifact-content-${lang}-${width}.png`});
    });

    test(`site6: maintenance repair policy shows localized summary on error (${locale})`, async ({page}) => {
      await mkdir(outDir, {recursive: true});
      await page.goto('/ui-components.html');
      await page.evaluate(async ({msg}) => {
        const {maintenanceRepairPolicyControls} = await import('/src/pm-maintenance-repair-policy.js');
        const {ApiError} = await import('/src/error-presentation.js');
        const {h} = await import('/src/ui/dom.js');
        const {t} = await import('/src/i18n.js');
        const input = (name: string, attrs = {}) => h('input', {name, ...attrs});
        const labelled = (label: string, field: any) => h('label', {}, t(label), field);
        const controls = maintenanceRepairPolicyControls({
          h, t, input, labelled,
          available: () => true,
          ciAvailable: () => true,
          reviewedHandoff: () => ({
            handoff: {
              params: {
                pm_package_ref: {workspace_id: 'ws_demo', package_id: 'pmwp_' + '1'.repeat(32), document_digest: 'a'.repeat(64)},
                source_sha: '1'.repeat(40),
                source_ref: 'refs/heads/maintenance',
                work_item_id: 'wi_1',
              },
              preconditions: {repository_id: 'repo_1'},
            },
          }),
          baseline: () => ({
            workspace_id: 'ws_demo',
            baseline_id: 'pmrb_' + '1'.repeat(32),
            document_digest: 'b'.repeat(64),
            document: {
              commit_sha: '1'.repeat(40),
              repository_id: 'repo_1',
              proof: {maintenance_branch: {ref: 'refs/heads/maintenance'}},
              release_version: 1,
            },
          }),
          editable: () => true,
          changed: () => {},
        });
        controls.update(true);
        const root = document.getElementById('fixture')!;
        root.replaceChildren(controls.element);
        controls.element.open = true;

        controls.fields.repair_key.value = 'fix_rule';
        controls.fields.repair_labels.value = 'bug';
        controls.fields.repair_paths.value = 'src/test.js';
        controls.fields.repair_command.value = 'npm test';

        const originalDigest = crypto.subtle.digest.bind(crypto.subtle);
        crypto.subtle.digest = () => {
          throw new ApiError(404, 'NOT_FOUND', msg);
        };
        const addBtn = controls.element.querySelector('button.ui-button') as HTMLButtonElement;
        addBtn?.click();
        await new Promise(r => setTimeout(r, 100));
        crypto.subtle.digest = originalDigest;
      }, {msg: serverMsg});

      const status = page.locator('[data-policy-group="pmr_rules"] [role="status"]');
      await expect(status).toHaveText(summaryText);
      if (locale === 'zh-TW') {
        expect(await status.innerText()).not.toContain(serverMsg);
        expect(await status.innerText()).not.toContain('NOT_FOUND');
      }

      await page.screenshot({path: `${outDir}/after-site6-maintenance-repair-policy-${lang}-${width}.png`});
    });
  });
}
