import {test, expect} from '@playwright/test';
import {STRINGS} from '../src/i18n.js';

const serverMsg = 'Requested entity not found on remote BAT host';

for (const {
  locale,
  summaryText,
  shortGenericText,
  storageFailedText,
  refreshRetainText,
  deliveryUnavailableText,
  readingSaveFailedText,
  messagesUnavailableText,
  repoLabel,
  prNumLabel,
  loadPrBtn,
  markReadBtn,
  loadEarlierBtn,
} of [
  {
    locale: 'en-US',
    summaryText: 'This record is unavailable. Refresh the view.',
    shortGenericText: 'This request could not be completed.',
    storageFailedText: 'This setting could not be saved in this browser.',
    refreshRetainText: 'Not refreshed; retaining the last observation.',
    deliveryUnavailableText: 'An eligible result source could not be confirmed.',
    readingSaveFailedText: 'Reading state unavailable',
    messagesUnavailableText: 'Messages unavailable',
    repoLabel: 'Repository or GitHub PR URL',
    prNumLabel: 'PR number',
    loadPrBtn: 'Load PR',
    markReadBtn: 'Mark read',
    loadEarlierBtn: 'Load earlier',
  },
  {
    locale: 'zh-TW',
    summaryText: '找不到這筆資料，請重新整理。',
    shortGenericText: '無法完成此要求。',
    storageFailedText: '無法在這個瀏覽器儲存這項設定。',
    refreshRetainText: '這次未能更新；保留上次觀察。',
    deliveryUnavailableText: '目前無法確認可整合的成果來源。',
    readingSaveFailedText: '無法儲存閱讀狀態',
    messagesUnavailableText: '無法載入訊息',
    repoLabel: '儲存庫或 GitHub PR 網址',
    prNumLabel: 'PR 編號',
    loadPrBtn: '讀取 PR',
    markReadBtn: '標為已讀',
    loadEarlierBtn: '載入更早',
  },
]) {
  const width = locale === 'zh-TW' ? 390 : 1440;
  test.describe(`error residuals ${locale}`, () => {
    test.use({locale, viewport: {width, height: 900}});

    test(`site1: inlineError shows summary and Retry on line 1, Details at full width on line 2 (${locale})`, async ({page}) => {
      await page.goto('/ui-components.html');
      await page.evaluate(async ({msg}) => {
        const {inlineError} = await import('/src/ui/components.js');
        const {ApiError} = await import('/src/error-presentation.js');
        const err = new ApiError(404, 'NOT_FOUND', msg);
        const root = document.getElementById('fixture')!;
        root.replaceChildren(inlineError({error: err, retry: () => {}}));
      }, {msg: serverMsg});

      const alert = page.locator('.ui-inline-error');
      await expect(alert).toBeVisible();
      const summaryEl = alert.locator('[data-error-summary]');
      const retryBtn = alert.locator('button');
      const details = alert.locator('details.ui-disclosure');

      await expect(summaryEl).toHaveText(summaryText);
      await expect(retryBtn).toBeVisible();
      await expect(details).toHaveCount(1);
      await expect(details).not.toHaveAttribute('open', '');

      // Assert that Retry stays on the summary's line at 390
      await page.setViewportSize({width: 390, height: 900});
      const summaryBox = (await summaryEl.boundingBox())!;
      const retryBox = (await retryBtn.boundingBox())!;
      const detailsBox = (await details.boundingBox())!;
      expect(summaryBox.y).toBeLessThan(retryBox.y + retryBox.height);
      expect(retryBox.y).toBeLessThan(summaryBox.y + summaryBox.height);
      expect(detailsBox.y).toBeGreaterThanOrEqual(summaryBox.y + summaryBox.height - 2);

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

      // Expand details and check code and message
      await details.locator('summary').click();
      await expect(alert.locator('[data-error-details] code')).toHaveText('NOT_FOUND');
      await expect(alert.locator('[data-error-details] p')).toHaveText(serverMsg);
    });

    test(`site2: operations list read failure shows localized summary and code in details (${locale})`, async ({page}) => {
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

      await details.locator('summary').click();
      await expect(statusBox.locator('[data-error-details] code')).toHaveText('NOT_FOUND');
      await expect(statusBox.locator('[data-error-details] p')).toHaveText(serverMsg);
    });

    test(`site2: operations detail failure shows localized summary with code in details (${locale})`, async ({page}) => {
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

      await details.locator('summary').click();
      await expect(inlineErr.locator('[data-error-details] code')).toHaveText('NOT_FOUND');
      await expect(inlineErr.locator('[data-error-details] p')).toHaveText(serverMsg);
    });

    test(`site4: model preferences shows localized summary with details (${locale})`, async ({page}) => {
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

      const panel = page.locator('[data-model-preferences]');
      const status = panel.locator('[role="status"]');
      await expect(status).toHaveText(summaryText);
      if (locale === 'zh-TW') {
        expect(await status.innerText()).not.toContain(serverMsg);
        expect(await status.innerText()).not.toContain('NOT_FOUND');
      }

      const details = panel.locator('details.ui-disclosure');
      await expect(details).toHaveCount(1);
      await expect(details).not.toHaveAttribute('open', '');
      await details.locator('summary').click();
      await expect(details.locator('[data-error-details] code')).toHaveText('NOT_FOUND');
      await expect(details.locator('[data-error-details] p')).toHaveText(serverMsg);
    });

    test(`site4: project skills shows localized summary with details (${locale})`, async ({page}) => {
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

      const panel = page.locator('[data-project-skills]');
      const status = panel.locator('[role="status"]');
      await expect(status).toHaveText(summaryText);
      if (locale === 'zh-TW') {
        expect(await status.innerText()).not.toContain(serverMsg);
        expect(await status.innerText()).not.toContain('NOT_FOUND');
      }

      const details = panel.locator('details.ui-disclosure');
      await expect(details).toHaveCount(1);
      await expect(details).not.toHaveAttribute('open', '');
      await details.locator('summary').click();
      await expect(details.locator('[data-error-details] code')).toHaveText('NOT_FOUND');
      await expect(details.locator('[data-error-details] p')).toHaveText(serverMsg);
    });

    test(`site5: composer shortcut shows storage failure without Details (${locale})`, async ({page}) => {
      await page.goto('/ui-components.html');
      await page.evaluate(async () => {
        const {composerShortcut} = await import('/src/composer-shortcut.js');
        const {h} = await import('/src/ui/dom.js');
        const {t} = await import('/src/i18n.js');
        const input = document.createElement('textarea');
        const btn = document.createElement('button');
        const originalSet = localStorage.setItem.bind(localStorage);
        localStorage.setItem = () => {
          throw new Error('QuotaExceededError');
        };
        const comp = composerShortcut({h, t, input, button: btn, storageKey: 'test.shortcut', guard: () => {}});
        const root = document.getElementById('fixture')!;
        root.replaceChildren(comp.box);
        const select = comp.box.querySelector('select') as HTMLSelectElement;
        select.value = 'enter';
        select.dispatchEvent(new Event('change', {bubbles: true}));
        localStorage.setItem = originalSet;
      });

      const status = page.locator('.composer-shortcut [role="status"]');
      await expect(status).toHaveText(storageFailedText);
      const text = await status.innerText();
      expect(text).not.toContain('Details');
      expect(text).not.toContain('詳細資料');
      expect(await page.locator('.composer-shortcut details').count()).toBe(0);
    });

    test(`site5: artifact content fail shows short generic summary without Details (${locale})`, async ({page}) => {
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

      // Next request to artifact fails with unknown code so fallback is triggered
      await page.route(`**/api/v1/artifacts/${artId}/revisions/1`, async route => {
        return route.fulfill({status: 500, json: {error: {code: 'INTERNAL_UNKNOWN', message: serverMsg}}});
      });
      await previewBtn.click();

      const status = page.locator('.artifact-content [role="status"]');
      await expect(status).toHaveText(shortGenericText);
      const text = await status.innerText();
      expect(text).not.toContain('Details');
      expect(text).not.toContain('詳細資料');
      expect(text).not.toContain(serverMsg);
      expect(text).not.toContain('INTERNAL_UNKNOWN');
    });

    test(`site6: maintenance repair policy shows short generic summary without Details (${locale})`, async ({page}) => {
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
          throw new ApiError(500, 'UNREGISTERED_ERROR', msg);
        };
        const addBtn = controls.element.querySelector('button.ui-button') as HTMLButtonElement;
        addBtn?.click();
        await new Promise(r => setTimeout(r, 100));
        crypto.subtle.digest = originalDigest;
      }, {msg: serverMsg});

      const status = page.locator('[data-policy-group="pmr_rules"] [role="status"]');
      await expect(status).toHaveText(shortGenericText);
      const text = await status.innerText();
      expect(text).not.toContain('Details');
      expect(text).not.toContain('詳細資料');
      expect(text).not.toContain(serverMsg);
      expect(text).not.toContain('UNREGISTERED_ERROR');
      expect(await page.locator('[data-policy-group="pmr_rules"] details.ui-disclosure').count()).toBe(0);
    });

    test(`site5: result-sources shows localized summary with details (${locale})`, async ({page}) => {
      await page.goto('/ui-components.html');
      await page.evaluate(async ({msg}) => {
        const {resultSourcesPanel} = await import('/src/result-sources.js');
        const {ApiError} = await import('/src/error-presentation.js');
        const {h} = await import('/src/ui/dom.js');
        const {t} = await import('/src/i18n.js');
        const api = async () => {
          throw new ApiError(404, 'NOT_FOUND', msg);
        };
        const panel = resultSourcesPanel({
          h, t, api, guard: () => {},
          workItemId: 'wi_' + '1'.repeat(32),
          linkTarget: () => h('span', {}, 'target'),
          onEvents: () => () => {},
          errorBox: () => {},
        });
        const root = document.getElementById('fixture')!;
        root.replaceChildren(panel.box);
        (panel.box.querySelector('details') as HTMLDetailsElement).open = true;
      }, {msg: serverMsg});

      const panel = page.locator('[data-result-sources]');
      const status = panel.locator('[role="status"]');
      await expect(status).toHaveText(`${refreshRetainText} ${summaryText}`);
      if (locale === 'zh-TW') {
        expect(await status.innerText()).not.toContain(serverMsg);
        expect(await status.innerText()).not.toContain('NOT_FOUND');
      }

      const details = panel.locator('details.ui-disclosure');
      await expect(details).toHaveCount(1);
      await expect(details).not.toHaveAttribute('open', '');
      await details.locator('summary').click();
      await expect(details.locator('[data-error-details] code')).toHaveText('NOT_FOUND');
      await expect(details.locator('[data-error-details] p')).toHaveText(serverMsg);
    });

    test(`site7: delivery source unavailable shows localized note with details (${locale})`, async ({page}) => {
      await page.addInitScript(() => sessionStorage.setItem('batc.dashboard.token', 'fixture-token'));
      await page.route('**/api/v1/**', async route => {
        const url = new URL(route.request().url());
        const path = url.pathname.replace('/api/v1', '');
        if (path === '/capabilities' || path === '/bootstrap') {
          return route.fulfill({status: 200, json: {
            actor: 'integrator', scopes: ['observe', 'operate', 'integrate'],
            capabilities: {
              actor: 'integrator', scopes: ['observe', 'operate', 'integrate'],
              repositories: [{repository: 'example/project'}],
              features: {execution_delivery: {version: 1}},
              actions: [{action: 'integration.handoff', allowed: true}],
            },
            repositories: [{repository: 'example/project'}],
            features: {execution_delivery: {version: 1}},
            actions: [{action: 'integration.handoff', allowed: true}],
            sync: {version: 1, server_id: 'srv', principal_id: 'p1', checkpoint: {cursor: 0, token: 't'}},
          }});
        }
        if (path.startsWith('/repositories/example/project/pulls/24')) {
          return route.fulfill({status: 200, json: {
            pull_request: {
              repository: 'example/project',
              pull_number: 24,
              title: 'Example Pull Request',
              head_ref: 'feat-branch',
              head_sha: '1'.repeat(40),
              base_ref: 'main',
              base_sha: '2'.repeat(40),
              state: 'open',
              mergeable_state: 'clean',
              checks: {},
              recipes: [],
              merge: {methods: ['merge'], allowed: false},
              metadata_update: {allowed: false},
              integration: {
                allowed: true,
                hosts: ['demo'],
              },
              merge_preview: {
                preview_id: 'mpv_' + 'a'.repeat(32),
                digest: 'fixture',
                method: 'merge',
                target: {head_sha: '1'.repeat(40), base_sha: '2'.repeat(40)},
                commits: [],
                affected_prs: [],
                blocking: [],
                warnings: [],
              },
            },
          }});
        }
        if (path.startsWith('/integrations/candidates')) {
          return route.fulfill({status: 200, json: {
            agent_results: [{
              id: 'exec_source_1',
              kind: 'checkpoint_run',
              branch: 'agent-patch',
              eligible: false,
              unavailable: {
                code: 'WORKTREE_UNAVAILABLE',
                message: serverMsg,
              },
              delivered_to: [],
            }],
            checkpoints: [],
          }});
        }
        return route.fulfill({status: 200, json: {}});
      });

      await page.goto('/dashboard/#/delivery');
      const repoInput = page.getByRole('textbox', {name: repoLabel});
      await expect(repoInput).toHaveValue('example/project');
      const prInput = page.getByRole('textbox', {name: prNumLabel, exact: true});
      await prInput.fill('24');
      const loadBtn = page.getByRole('button', {name: loadPrBtn, exact: true});
      await loadBtn.click();

      const deliveryRow = page.locator('[data-delivery-source="exec_source_1"]');
      await expect(deliveryRow).toBeVisible();

      // Note sentence
      await expect(deliveryRow.locator('p.note')).toHaveText(deliveryUnavailableText);

      // Details closed
      const details = deliveryRow.locator('details.ui-disclosure');
      await expect(details).toHaveCount(1);
      await expect(details).not.toHaveAttribute('open', '');

      // At 390 width, assert no horizontal overflow
      await page.setViewportSize({width: 390, height: 900});
      const overflow = await page.evaluate(() => {
        const row = document.querySelector('[data-delivery-source="exec_source_1"]') as HTMLElement | null;
        return {
          docScroll: document.documentElement.scrollWidth,
          docClient: document.documentElement.clientWidth,
          rowScroll: row ? row.scrollWidth : 0,
          rowClient: row ? row.clientWidth : 0,
        };
      });
      expect(overflow.docScroll).toBeLessThanOrEqual(overflow.docClient);
      expect(overflow.rowScroll).toBeLessThanOrEqual(overflow.rowClient);

      // Outside details, in zh-TW no English server text or code
      if (locale === 'zh-TW') {
        const outsideText = await deliveryRow.evaluate(node => {
          const clone = node.cloneNode(true) as HTMLElement;
          clone.querySelectorAll('details').forEach(d => d.remove());
          return clone.textContent || '';
        });
        expect(outsideText).not.toContain(serverMsg);
        expect(outsideText).not.toContain('WORKTREE_UNAVAILABLE');
      }

      // Expand details
      await details.locator('summary').click();
      await expect(deliveryRow.locator('[data-error-details] code')).toHaveText('WORKTREE_UNAVAILABLE');
      await expect(deliveryRow.locator('[data-error-details] p')).toHaveText(serverMsg);
    });

    test(`site8: session reading action error during refresh retains connection and shows details (${locale})`, async ({page}) => {
      let eventCursor = 0;
      let session1RequestsAfterEvent1 = 0;
      let released = false;
      const afterValuesAfterRelease: number[] = [];
      let shouldHoldSessionRefresh = false;
      let sessionRefreshHeld = false;
      let releaseRefresh: () => void = () => {};
      const refreshGate = new Promise<void>(r => { releaseRefresh = r; });
      let failMarkRead = false;
      let failOlder = false;

      const messages = Array.from({length: 35}, (_, i) => ({
        id: `msg-${i}`,
        role: i % 2 === 0 ? 'user' : 'assistant',
        ts: '2026-10-10T12:00:00Z',
        text: `Message content ${i}.`,
      }));

      await page.addInitScript(() => sessionStorage.setItem('batc.dashboard.token', 'fixture-token'));
      await page.route('**/api/v1/**', async route => {
        const url = new URL(route.request().url());
        const path = url.pathname.replace('/api/v1', '');
        if (path === '/capabilities' || path === '/bootstrap') {
          return route.fulfill({status: 200, json: {
            actor: 'tester', scopes: ['observe', 'operate'],
            capabilities: {
              actor: 'tester', scopes: ['observe', 'operate'],
              hosts: [{host: 'demo', writes: true, orchestrate: true}],
              actions: [
                {action: 'session.read', allowed: true},
                {action: 'session.position', allowed: true},
              ],
              features: {session_reading: {version: 1}},
            },
            hosts: [{host: 'demo', writes: true, orchestrate: true}],
            actions: [
              {action: 'session.read', allowed: true},
              {action: 'session.position', allowed: true},
            ],
            features: {session_reading: {version: 1}},
            sync: {version: 1, server_id: 'srv', principal_id: 'p1', checkpoint: {cursor: 0, token: 't0'}},
          }});
        }
        if (path.startsWith('/events')) {
          const after = Number(url.searchParams.get('after') || 0);
          if (released) {
            afterValuesAfterRelease.push(after);
          }
          if (after < eventCursor) {
            return route.fulfill({status: 200, json: {
              events: [{seq: eventCursor, resource_type: 'session', resource_id: 'demo/session-1', kind: 'session.updated'}],
              next_cursor: eventCursor,
              head_cursor: eventCursor,
              has_more: false,
              sync: {checkpoint: {cursor: eventCursor, token: `t${eventCursor}`}},
            }});
          }
          return route.fulfill({status: 200, json: {
            events: [],
            next_cursor: after,
            head_cursor: eventCursor,
            has_more: false,
            sync: {checkpoint: {cursor: after, token: `t${after}`}},
          }});
        }
        if (path === '/sessions/demo/session-1') {
          if (route.request().method() === 'GET' && eventCursor === 1) {
            session1RequestsAfterEvent1++;
          }
          if (shouldHoldSessionRefresh) {
            sessionRefreshHeld = true;
            await refreshGate;
          }
          return route.fulfill({status: 200, json: {
            session: {
              host: 'demo', session_id: 'session-1', title: 'Review reading action errors',
              workspace: 'Dashboard', agent_kind: 'codex', api_access: 'managed', provenance: 'connector_managed',
            },
          }});
        }
        if (path === '/sessions/demo/session-1/messages') {
          const offset = Number(url.searchParams.get('offset') || 0);
          if (offset > 0 && failOlder) {
            return route.fulfill({status: 500, json: {error: {code: 'NOT_FOUND', message: serverMsg}}});
          }
          const end = messages.length - offset;
          const slice = messages.slice(Math.max(0, end - 30), end);
          return route.fulfill({status: 200, json: {
            messages: slice.map(m => ({
              ...m,
              reading: {revision: `rev_${m.id}`, unread: true, can_mark: true},
            })),
            offset,
            next_offset: end > 30 ? offset + 30 : null,
            reading: {
              unread_count: messages.length,
              known_count: messages.length,
              complete: true,
              observed_at: Date.now() / 1000,
            },
          }});
        }
        if (path === '/operations' && route.request().method() === 'POST') {
          if (failMarkRead) {
            return route.fulfill({status: 500, json: {error: {code: 'NOT_FOUND', message: serverMsg}}});
          }
          return route.fulfill({status: 200, json: {operation: {operation_id: 'op_test', status: 'succeeded'}}});
        }
        return route.fulfill({status: 200, json: {}});
      });

      await page.goto('/dashboard/#/session/demo/session-1');
      const mark = page.getByRole('button', {name: markReadBtn, exact: true});
      await mark.scrollIntoViewIfNeeded();
      await expect(mark).toBeEnabled();

      // Step 1: hold an event-triggered session refresh open
      shouldHoldSessionRefresh = true;
      eventCursor = 1;
      await expect.poll(() => sessionRefreshHeld, {timeout: 10000}).toBe(true);

      // Check 3 setup: Before clicking Mark read, install a MutationObserver in the page on #live that records every title and data-connection value
      await page.evaluate(() => {
        const live = document.getElementById('live')!;
        const records: {title: string | null; connection: string | null}[] = [
          {title: live.title || live.getAttribute('title'), connection: live.getAttribute('data-connection') || live.dataset.connection || null},
        ];
        (window as any).__liveMutations = records;
        const observer = new MutationObserver(() => {
          records.push({
            title: live.title || live.getAttribute('title'),
            connection: live.getAttribute('data-connection') || live.dataset.connection || null,
          });
        });
        observer.observe(live, {attributes: true, attributeFilter: ['title', 'data-connection']});
      });

      // Step 2: make Mark read fail with NOT_FOUND and an English server message
      failMarkRead = true;
      await mark.click();

      // Assert summary and closed details appear in status line
      const statusLine = page.locator('.conversation-toolbar [data-api-error]');
      await expect(statusLine).toBeVisible();
      await expect(statusLine.locator('[data-error-summary]')).toHaveText(readingSaveFailedText);

      const markDetails = statusLine.locator('details.ui-disclosure');
      await expect(markDetails).toHaveCount(1);
      await expect(markDetails).not.toHaveAttribute('open', '');

      // In zh-TW, no English server text appears outside Details
      if (locale === 'zh-TW') {
        const outsideText = await statusLine.evaluate(node => {
          const clone = node.cloneNode(true) as HTMLElement;
          clone.querySelectorAll('details').forEach(d => d.remove());
          return clone.textContent || '';
        });
        expect(outsideText).not.toContain(serverMsg);
        expect(outsideText).not.toContain('NOT_FOUND');
      }

      // Step 3: release the refresh
      released = true;
      releaseRefresh();

      // 1. No replay of the cursor. Record the after value of every /events request made after releaseRefresh().
      // Wait until one arrives with after=1. Then assert that no request after the release used after=0.
      await expect.poll(() => afterValuesAfterRelease.includes(1), {timeout: 10000}).toBe(true);
      expect(afterValuesAfterRelease).not.toContain(0);

      // 2. No second refresh for the same event. Count the GET /sessions/demo/session-1 requests made after you set eventCursor = 1.
      // Assert there is exactly one: the held request. A replay would make a second.
      expect(session1RequestsAfterEvent1).toBe(1);

      // Expand details and check diagnostics
      await markDetails.locator('summary').click({force: true});
      await expect(statusLine.locator('[data-error-details] code')).toHaveText('NOT_FOUND');
      await expect(statusLine.locator('[data-error-details] p')).toHaveText(serverMsg);

      // Failed "Load earlier" shows the message-read sentence and does not say anything about saving
      failOlder = true;
      const older = page.getByRole('button', {name: loadEarlierBtn, exact: true});
      await older.scrollIntoViewIfNeeded();
      await older.click({force: true});

      await expect(statusLine.locator('[data-error-summary]')).toHaveText(messagesUnavailableText);
      const olderSummaryText = await statusLine.locator('[data-error-summary]').innerText();
      expect(olderSummaryText.toLowerCase()).not.toContain('save');
      expect(olderSummaryText).not.toContain('儲存');
      const olderDetails = statusLine.locator('details.ui-disclosure');
      await expect(olderDetails).not.toHaveAttribute('open', '');
      await olderDetails.locator('summary').click({force: true});
      await expect(olderDetails.locator('[data-error-details] code')).toHaveText('NOT_FOUND');
      await expect(olderDetails.locator('[data-error-details] p')).toHaveText(serverMsg);

      // 3. Actions never pause. Assert:
      // - no recorded title equals the locale's sync_actions_paused text (read it from desktop/src/i18n.js; do not hard-code a guess);
      // - every recorded data-connection is connected.
      const mutations = await page.evaluate(() => (window as any).__liveMutations as {title: string | null; connection: string | null}[]);
      expect(mutations.length).toBeGreaterThan(0);
      const syncActionsPausedText = STRINGS[locale.startsWith('zh') ? 'zh-TW' : 'en'].sync_actions_paused;
      expect(mutations.map(m => m.title)).not.toContain(syncActionsPausedText);
      for (const m of mutations) {
        expect(m.connection).toBe('connected');
      }
    });
  });
}
