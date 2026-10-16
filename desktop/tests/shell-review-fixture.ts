import {workbenchFixture, workspaceId} from './pm-workbench-fixture.ts';
export function shellReviewFixture() {
  const base = workbenchFixture(), endpoint = `/pm/workspaces/${workspaceId}`;
  const state = {stage: 'development' as string | null, projectError: 0, holdEvidence: null as Promise<void> | null};
  const dispatch = async (input: any) => {
    const path = new URL(input.path, 'http://fixture').pathname;
    if (path === '/bootstrap' || path === '/capabilities') {
      const result = await base.dispatch(input), caps = path === '/bootstrap' ? result.data.capabilities : result.data;
      caps.features.pm_workspaces.lifecycle = {version: 1, gated_actions: [], record_actions: []};
      caps.features.pm_workspaces.work_evidence = true;
      caps.features.pm_workspaces.brief_export = {formats: ['markdown', 'html', 'svg'], locales: ['en', 'zh-TW'], renderer_version: 1};
      caps.actions.push({action: 'pm.lifecycle.set_stage', allowed: true});
      return result;
    }
    if (path === endpoint + '/lifecycle') {
      return {status: 200, data: {workspace_id: workspaceId,
        lifecycle: {stage: state.stage, status: state.stage ? 'active' : 'unclassified', revision: 2, execution_epoch: 1,
          transition_id: null, coverage: state.stage ? 'recorded' : 'legacy_unclassified'},
        transition: null, blocked_actions: [], available_stages: ['idea', 'planning', 'development'],
        inflight_summary: {visible_unresolved: 0, visible_retained: 0, visibility_filtered: false}, maintenance: {available: false}}};
    }
    if (path === endpoint + '/lifecycle/transitions') return {status: 200, data: {workspace_id: workspaceId, transitions: [], next_cursor: null}};
    if (path === endpoint + '/projects' || path === endpoint + '/work') return {status: 200, data: {workspace_id: workspaceId, bindings: [], next_cursor: null}};
    if (path === '/projects' && state.projectError) return {status: state.projectError,
      data: {error: {code: state.projectError === 403 ? 'FORBIDDEN' : state.projectError === 404 ? 'NOT_FOUND' : 'UNAVAILABLE', message: 'Fixture source unavailable'}}};
    if (path === '/projects') {
      if (state.holdEvidence) await state.holdEvidence;
      return {status: 200, data: {projects: []}};
    }
    return base.dispatch(input);
  };
  return {base, state, dispatch};
}
