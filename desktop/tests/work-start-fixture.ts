import {workbenchFixture, workspaceId, workId, route, revisionId} from './pm-workbench-fixture.ts';

export {workspaceId, workId, route, revisionId};

export function workStartFixture() {
  const wb = workbenchFixture();
  const base = wb.base;
  const board = wb.board;

  // Configure work items
  const workItems = new Map<string, any>([
    [workId(1), {work_item_id: workId(1), title: 'Member login', goal: 'Allow members to log in', request: 'Implement login and authentication flow'}],
    [workId(2), {work_item_id: workId(2), title: 'Password recovery', goal: 'Account recovery via email', request: 'Send one-time recovery token to email'}],
    [workId(3), {work_item_id: workId(3), title: 'Account settings', goal: 'Manage user settings', request: 'Configure account profile'}],
  ]);

  const state = {
    ...wb.state,
    work_start_available: true,
    role: 'owner',
    scopes: ['observe', 'manage', 'start'],
    actions: [{action: 'work.start', allowed: true}],
    candidatesCount: 1, // 0, 1, or 2
    readinessStatus: 'ready' as 'ready' | 'blocked',
    invalidPreview: false,
    attempts: [] as any[],
    attemptsNextCursor: null as string | null,
    visibilityFiltered: false,
    partialAttempts: false,
    failWorkItem: false,
    refusalCode: null as string | null,
    lostReply: false,
    failHandoff: false,
    writes: [] as any[],
    ops: new Map<string, any>(),
  };

  const dispatch = async (input: any): Promise<any> => {
    const url = new URL(input.path, 'http://fixture');
    const path = url.pathname;
    const endpoint = `/pm/workspaces/${workspaceId}`;

    if (input.method === 'GET') {
      state.reads.push(input.path);
    }

    if (path === '/bootstrap' || path === '/capabilities') {
      const res = await wb.dispatch(input);
      const caps = path === '/bootstrap' ? res.data.capabilities : res.data;
      caps.scopes = [...state.scopes];
      if (state.work_start_available) {
        caps.features.pm_workspaces.work_start = {
          version: 1,
          available: true,
          action: 'work.start',
          routes: {
            preview: `/pm/workspaces/${workspaceId}/work/{wi}/start-preview`,
            attempts_list: `/pm/workspaces/${workspaceId}/work/{wi}/attempts`,
            attempt_get: `/pm/workspaces/${workspaceId}/work-attempts/{attempt_id}`,
          },
          required_scopes: ['manage', 'observe', 'start'],
          delivery_goals: ['pull_request', 'reviewable_changes'],
          agents: ['claude', 'codex'],
        };
      } else {
        delete caps.features.pm_workspaces.work_start;
      }
      for (const act of state.actions) {
        const idx = caps.actions.findIndex((a: any) => a.action === act.action);
        if (idx >= 0) caps.actions[idx] = act;
        else caps.actions.push(act);
      }
      return res;
    }

    if (path === endpoint) {
      const res = await wb.dispatch(input);
      if (res.data?.workspace) {
        res.data.workspace.membership = {
          actor: base.state.actor,
          role: state.role,
          controller_id: 'ctr_' + '4'.repeat(32),
        };
      }
      return res;
    }

    // GET /work-items/{wi}
    if (input.method === 'GET' && path.startsWith('/work-items/')) {
      if (state.failWorkItem) {
        return {status: 500, data: {error: {code: 'SERVER_ERROR', message: 'Failed to read work item'}}};
      }
      const wid = path.slice('/work-items/'.length);
      const item = workItems.get(wid) || {work_item_id: wid, title: 'Work ' + wid, goal: 'Goal ' + wid, request: 'Request ' + wid};
      return {status: 200, data: {work_item: structuredClone(item)}};
    }

    // POST start-preview
    if (input.method === 'POST' && path.endsWith('/start-preview')) {
      const parts = path.split('/');
      const wid = parts[parts.indexOf('work') + 1];

      if (state.invalidPreview) {
        return {status: 200, data: {workspace_id: workspaceId, work_item_id: wid, code_target: null, candidates: null, readiness: null}};
      }

      if (state.candidatesCount === 0) {
        return {
          status: 200,
          data: {
            workspace_id: workspaceId,
            work_item_id: wid,
            code_target: null,
            source: null,
            package_plan: null,
            preconditions: null,
            candidates: [],
            readiness: {
              status: 'blocked',
              checks: [{check: 'code_target', status: 'blocked', detail: 'No repository binding matches this project'}],
            },
            observed_at: 1791705600,
          },
        };
      }

      if (state.candidatesCount === 2 && !input.body?.code_target) {
        return {
          status: 200,
          data: {
            workspace_id: workspaceId,
            work_item_id: wid,
            code_target: null,
            source: null,
            package_plan: null,
            preconditions: null,
            candidates: [
              {repository: 'example/library', host: 'fixture-host', workspace_id: 'fixture-workspace'},
              {repository: 'example/other', host: 'fixture-host-2', workspace_id: 'fixture-workspace-2'},
            ],
            readiness: {
              status: 'blocked',
              checks: [{check: 'code_target', status: 'blocked', detail: 'Multiple repository bindings'}],
            },
            observed_at: 1791705600,
          },
        };
      }

      // 1 candidate or candidate selected
      const chosen = input.body?.code_target || {
        repository: 'example/library',
        host: 'fixture-host',
        workspace_id: 'fixture-workspace',
      };

      const isBlocked = state.readinessStatus === 'blocked';
      const latestAtt = state.attempts.length > 0 ? state.attempts[state.attempts.length - 1] : null;

      const checks = isBlocked ? [
        {check: 'design', status: 'passed'},
        {check: 'acceptance:membership.login', status: 'blocked', detail: 'Acceptance criteria not met'},
      ] : [
        {check: 'design', status: 'passed'},
        {check: 'delivery_plan', status: 'passed'},
      ];

      return {
        status: 200,
        data: {
          workspace_id: workspaceId,
          work_item_id: wid,
          code_target: chosen,
          candidates: null,
          source: {
            source_ref: 'refs/heads/main',
            source_sha: 'a'.repeat(40),
            repository_id: 7,
            binding_digest: 'b'.repeat(64),
          },
          binding: {
            bound: true,
            bind_needed: Boolean(input.body?.requirement_ids),
            binding_id: 'pmwb_' + '1'.repeat(32),
            plan_revision_id: revisionId,
            scope_revision: 'pmscope_' + '5'.repeat(32),
            requirement_ids: input.body?.requirement_ids || ['membership.login'],
          },
          package_plan: {
            mode: 'compose',
            package_reused: false,
            package_id: 'pmpkg_' + '1'.repeat(32),
          },
          readiness: {
            status: isBlocked ? 'blocked' : 'ready',
            checks,
          },
          preconditions: {
            expected_work_item_fingerprint: 'c'.repeat(64),
            expected_work_version: 2,
            expected_binding_id: 'pmwb_' + '1'.repeat(32),
            expected_scope_revision: 'pmscope_' + '5'.repeat(32),
            expected_previous_package_id: null,
            expected_project_version: 1,
            repository_id: 7,
            binding_digest: 'b'.repeat(64),
            expected_source_sha: 'a'.repeat(40),
            expected_latest_attempt_id: latestAtt ? latestAtt.attempt_id : null,
          },
          observed_at: 1791705600,
        },
      };
    }

    // GET attempts
    if (input.method === 'GET' && path.endsWith('/attempts')) {
      const wid = path.split('/').at(-2);
      return {
        status: 200,
        data: {
          workspace_id: workspaceId,
          work_item_id: wid,
          attempts: structuredClone(state.attempts),
          next_cursor: state.attemptsNextCursor,
          visibility_filtered: state.visibilityFiltered,
        },
      };
    }

    // POST /operations
    if (input.method === 'POST' && path === '/operations') {
      state.writes.push(structuredClone(input));
      const key = input.idempotency_key;

      if (state.ops.has(key)) {
        return {status: 200, data: {operation: structuredClone(state.ops.get(key))}};
      }

      if (state.refusalCode) {
        const code = state.refusalCode;
        const status = code === 'PM_HANDOFF_TOO_LARGE' ? 413 : 409;
        return {
          status,
          data: {
            error: {
              code,
              message: `Operation refused with ${code}`,
              admission_refused: true,
            },
          },
        };
      }

      if (state.lostReply) {
        state.lostReply = false;
        const opId = 'op_' + '1'.repeat(32);
        const op = {
          operation_id: opId,
          action: input.body.action,
          target: input.body.target,
          params: input.body.params,
          preconditions: input.body.preconditions,
          actor: base.state.actor,
          idempotency_key: key,
          status: 'running',
        };
        state.ops.set(key, op);
        return {status: 503, data: {error: {code: 'LOST_REPLY', message: 'Reply unavailable'}}};
      }

      if (state.failHandoff) {
        const opId = 'op_' + '1'.repeat(32);
        const op = {
          operation_id: opId,
          action: input.body.action,
          target: input.body.target,
          params: input.body.params,
          preconditions: input.body.preconditions,
          actor: base.state.actor,
          idempotency_key: key,
          status: 'failed',
          error: {code: 'PM_HANDOFF_FAILED', message: 'Handoff to BAT failed'},
        };
        state.ops.set(key, op);
        return {status: 200, data: {operation: op}};
      }

      // Normal success
      const opId = 'op_' + '1'.repeat(32);
      const op = {
        operation_id: opId,
        action: input.body.action,
        target: input.body.target,
        params: input.body.params,
        preconditions: input.body.preconditions,
        actor: base.state.actor,
        idempotency_key: key,
        status: 'succeeded',
        result: {host: 'fixture-host', session_id: 'session-123'},
      };
      state.ops.set(key, op);

      // Create or update attempt
      const attemptId = 'att_' + '1'.repeat(32);
      const newAtt = {
        attempt_id: attemptId,
        workspace_id: workspaceId,
        work_item_id: input.body.target.work_item_id,
        start_operation_id: opId,
        stage: 'started',
        status: 'succeeded',
        session: {host: 'fixture-host', session_id: 'session-123'},
        created_at: 1791705600,
      };
      state.attempts.push(newAtt);

      return {status: 200, data: {operation: op}};
    }

    if (input.method === 'GET' && path.startsWith('/operations/')) {
      const opId = path.slice('/operations/'.length);
      const op = [...state.ops.values()].find(o => o.operation_id === opId);
      if (op) {
        return {status: 200, data: {operation: structuredClone(op)}};
      }
    }

    return wb.dispatch(input);
  };

  const emitAttempt = (att: any) => {
    const idx = state.attempts.findIndex((a: any) => a.attempt_id === att.attempt_id);
    if (idx >= 0) {
      state.attempts[idx] = att;
    } else {
      state.attempts.push(att);
    }
    base.state.cursor++;
  };

  return {
    ...wb,
    state,
    dispatch,
    emitAttempt,
  };
}
