import {openSession, checkpointHost, type Override} from './session-fixture.ts';
import type {Browser} from '@playwright/test';
export async function pathsFixture(browser: Browser, options: any = {}) {
  const s = await openSession(browser, options);
  const state = {checkpoints: true, start: true, managed: false, native: {action_registered: true, eligible_for_preflight: true, reason: null}, status: 200, ...options};
  const checkpoints = checkpointHost(s, s.ids.sessions.manual);
  s.overrides.push((async (input, url) => {
    if (['/bootstrap', '/capabilities'].includes(url.pathname)) {
      const response = structuredClone(await s.fixture.dispatch(input));
      const caps = url.pathname === '/bootstrap' ? response.data.capabilities : response.data;
      caps.scopes = ['manage', ...caps.scopes.filter((x: string) => x !== 'start'), ...(state.start ? ['start'] : [])];
      caps.actions = [...caps.actions.filter((x: any) => !['session.start', 'checkpoint.create', 'checkpoint.continue'].includes(x.action)),
        {action: 'session.start', allowed: state.start}, {action: 'checkpoint.create', allowed: true}, {action: 'checkpoint.continue', allowed: state.start}];
      caps.features.checkpoints = state.checkpoints ? [s.ids.host] : [];
      caps.features.pm_workspaces.installation_eligibility = true;
      if (state.managed) caps.managed_installation = {installation_id: 'fixture-install', runtime_version: '0.1'};
      return response;
    }
    if (url.pathname === `/pm/workspaces/${s.ids.workspaceId}/capabilities`) return {status: state.status, data: {native_analysis: state.native, ...(state.bat ? {bat_analysis: state.bat} : {})}};
    if (url.pathname === '/pm/installation-capabilities') return {status: state.status, data: state.status === 200 ? {native_analysis: state.native, workspace_required: true} : {error: {code: 'FORBIDDEN', message: 'Unavailable'}}};
    if (url.pathname === '/workspaces') return {status: 200, data: {workspaces: [{host: s.ids.host, workspace_id: 'demo-workspace', name: 'Demo', folder: '/srv/demo'}], has_more: false, errors: {}}};
    return checkpoints(input, url);
  }) as Override);
  return {...s, state};
}
