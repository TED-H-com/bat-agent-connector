import {workStartFixture, workspaceId, workId, route, revisionId} from './work-start-fixture.ts';

export {workspaceId, workId, route, revisionId};

export function buildResultEnvelope(opts: any = {}) {
  const wsId = opts.workspace_id || workspaceId;
  const attId = opts.attempt_id || ('att_' + '1'.repeat(32));
  const contract = opts.wrong_contract ? 'pm-work-attempt-result/v0' : (opts.contract || 'pm-work-attempt-result/v1');
  const targetAttId = opts.wrong_attempt ? ('att_' + '9'.repeat(32)) : attId;
  const state = opts.wrong_state ? 'invalid_state' : (opts.state || 'turn-ended');

  return {
    contract,
    workspace_id: wsId,
    attempt_id: targetAttId,
    cursor: opts.cursor ?? 'cur_1',
    events_read: opts.events_read ?? 1,
    wait: opts.wait ?? null,
    collection: opts.collection ?? {
      status: 'checked',
      checked_at: 1791705600,
      outcome: 'completed',
    },
    results: opts.results ?? [
      {
        result_id: 'res_01',
        turn_marker: 'turn_01',
        last_message_id: 'msg_01',
        head_commit: 'abcdef1234567890abcdef1234567890abcdef12',
        observed_at: 1791705600,
        collected_at: 1791705600,
        digest: 'd1234567890abcdef1234567890abcdef',
        sequence: 1,
      },
    ],
    results_has_more: opts.results_has_more ?? false,
    identity: opts.identity ?? {
      start_operation_id: opts.start_op_id || ('op_' + '1'.repeat(32)),
      execution_operation_id: opts.exec_op_id || ('op_' + '2'.repeat(32)),
    },
    sources: opts.sources ?? {
      result_id: 'res_01',
      source_key: 'sk_turn_1',
      digest: 'd1234567890abcdef1234567890abcdef',
    },
    conversation: opts.conversation ?? {
      latest_assistant_summary: opts.summary_text !== undefined ? (opts.summary_text === null ? null : {
        text: opts.summary_text,
        message_id: 'msg_01',
        claim_status: 'self_reported',
      }) : {
        text: 'Implemented login flow and validated credentials handling.',
        message_id: 'msg_01',
        claim_status: 'self_reported',
      },
      turn_done: 'unknown',
      turn_phase: 'unknown',
    },
    execution: opts.execution ?? {
      state,
      state_reason: opts.state_reason ?? null,
      observed_at: opts.observed_at ?? 1791705600,
      stage: opts.stage ?? 'completed',
      operation_status: 'succeeded',
      attention: opts.attention ?? null,
      pending: opts.pending ?? null,
      exit_code: 'unknown',
      exit_code_reason: 'not_exposed_by_bat',
      state_facets: {
        self_reported: opts.self_reported !== undefined ? opts.self_reported : 'claimed_done',
        verification: opts.verification_status ?? 'unverified',
        review_decision: opts.review_decision ?? {kind: 'unassessed'},
        acceptance: opts.acceptance_status ? {status: opts.acceptance_status} : {status: 'unassessed', semantic_acceptance: 'not_assessed'},
        merge: opts.merge_status ?? 'not_started',
        deployment: {
          state: opts.deployment_state ?? 'not_deployed',
          verified: opts.deployment_verified ?? false,
          is_current: null,
        },
      },
    },
    changes: opts.changes ?? {
      base_commit: opts.base_commit ?? '1234567890abcdef1234567890abcdef12345678',
      live_head_commit: opts.head_commit ?? 'unknown',
      head_freshness: 'cached_receipt',
      fixed_result_commit: opts.fixed_commit !== undefined ? opts.fixed_commit : {
        commit_sha: 'abcdef1234567890abcdef1234567890abcdef12',
        source_kind: 'turn_end_observation',
        source_id: 'src_01',
        recorded_by: 'system',
        recorded_at: 1791705600,
        uncommitted: null,
      },
      uncommitted: opts.uncommitted !== undefined ? opts.uncommitted : {
        count: 'unknown',
        files: [],
        evidence: null,
        reason: 'bat_empty_status_not_clean_proof',
      },
      summary: opts.changes_summary ?? {
        aggregation: 'per_commit',
        base_commit: '1234567890abcdef1234567890abcdef12345678',
        head_commit: 'abcdef1234567890abcdef1234567890abcdef12',
        commits: ['c1', 'c2'],
        commit_count: 2,
        files: [
          {path: 'src/login.ts', status: 'M', additions: 8, deletions: 1, commits_touched: 2},
          {path: 'src/auth.ts', status: 'A', additions: 2, deletions: 0, commits_touched: 1},
          {path: 'docs/login.md', status: 'M', additions: 0, deletions: 1, commits_touched: 1},
        ],
        file_count: 3,
        additions: 10,
        deletions: 2,
        unavailable: false,
        complete: true,
        reason: null,
        reasons: [],
      },
    },
    verification: opts.verification ?? {
      status: opts.verification_status ?? 'unverified',
      runs: [],
      quality_reviews: {reviews: [], current: null},
    },
    delivery: opts.delivery ?? {
      delivery_goal: opts.delivery_goal ?? 'pull_request',
      pull_requests: opts.pull_requests ?? [],
      integrations: [],
      merges: [],
      deployments: [],
    },
    usage: opts.usage ?? {
      context_tokens: 1500,
      context_window: 128000,
      num_turns: 3,
    },
    completeness: opts.completeness ?? {
      status: opts.completeness_status ?? 'complete',
      reasons: opts.completeness_reasons ?? [],
      coverage: null,
      last_successful_observation: 1791705600,
    },
    next_allowed_actions: opts.next_allowed_actions ?? [],
    blocked_actions: opts.blocked_actions ?? [],
    links: opts.links ?? {
      start_operation_hash: '#/op/' + (opts.start_op_id || ('op_' + '1'.repeat(32))),
      execution_operation_hash: null,
      session_hash: '#/session/fixture-host/session-123',
      work_item_hash: '#/work/' + workId(1),
    },
  };
}

export function workResultFixture() {
  const sf = workStartFixture();

  // Add default attempt with session for workId(1)
  const defaultAttemptId = 'att_' + '1'.repeat(32);
  const defaultAttempt = {
    attempt_id: defaultAttemptId,
    workspace_id: workspaceId,
    work_item_id: workId(1),
    start_operation_id: 'op_' + '1'.repeat(32),
    stage: 'completed',
    status: 'succeeded',
    session: {host: 'fixture-host', session_id: 'session-123'},
    created_at: 1791705600,
  };
  sf.state.attempts.push(defaultAttempt);

  const state = {
    ...sf.state,
    work_attempt_results_available: true,
    resultRequests: [] as Array<{method: string; path: string; attempt_id: string}>,
    holdResult: null as Promise<void> | null,
    holdResultAttemptId: null as string | null,
    resultStatus: null as number | null,
    resultStatusCount: 0,
    resultEnvelopes: new Map<string, any>(),
    currentEnvelope: buildResultEnvelope({attempt_id: defaultAttemptId}),
    changesCount: 0,
    diffCount: 0,
  };

  const dispatch = async (input: any): Promise<any> => {
    const url = new URL(input.path, 'http://fixture');
    const path = url.pathname;

    // Track capabilities
    if (path === '/bootstrap' || path === '/capabilities') {
      const res = await sf.dispatch(input);
      const caps = path === '/bootstrap' ? res.data.capabilities : res.data;
      if (state.work_attempt_results_available) {
        caps.features.pm_workspaces.work_attempt_results = {
          version: 1,
          contract: 'pm-work-attempt-result/v1',
          routes: {
            result: `/pm/workspaces/${workspaceId}/work-attempts/{attempt_id}/result`,
            changes: `/pm/workspaces/${workspaceId}/work-attempts/{attempt_id}/changes`,
            diff: `/pm/workspaces/${workspaceId}/work-attempts/{attempt_id}/diff`,
          },
          reads: true,
          mcp_tools: true,
          adapters: {bat: {read_results: true}},
          wait_ms_max: 10000,
          reads_never_write: true,
        };
      } else {
        delete caps.features.pm_workspaces.work_attempt_results;
      }
      return res;
    }

    // Attempts endpoint: filter by wid
    if (input.method === 'GET' && path.endsWith('/attempts')) {
      const res = await sf.dispatch(input);
      const wid = path.split('/').at(-2);
      if (res.data?.attempts && wid) {
        res.data.attempts = res.data.attempts.filter((a: any) => a.work_item_id === wid);
      }
      return res;
    }

    // BAT calls tracking
    if (path.includes('/changes')) {
      state.changesCount++;
      return {status: 200, data: {changes: []}};
    }
    if (path.includes('/diff')) {
      state.diffCount++;
      return {status: 200, data: {diff: ''}};
    }

    // Result endpoint: /pm/workspaces/{ws}/work-attempts/{att}/result
    const resultMatch = path.match(/\/work-attempts\/([^/]+)\/result$/);
    if (input.method === 'GET' && resultMatch) {
      const attId = resultMatch[1];
      state.resultRequests.push({method: input.method, path: input.path, attempt_id: attId});

      if (state.holdResult && (!state.holdResultAttemptId || state.holdResultAttemptId === attId)) {
        await state.holdResult;
      }

      if (state.resultStatus) {
        const status = state.resultStatus;
        state.resultStatusCount++;
        return {
          status,
          data: {
            error: {
              code: status === 404 ? 'NOT_FOUND' : (status === 403 ? 'FORBIDDEN' : 'SERVER_ERROR'),
              message: `Error status ${status}`,
            },
          },
        };
      }

      const env = state.resultEnvelopes.get(attId) || state.currentEnvelope;
      return {status: 200, data: structuredClone(env)};
    }

    return sf.dispatch(input);
  };

  const emitResultRecorded = (attId: string, updatedEnvelope?: any) => {
    if (updatedEnvelope) {
      state.resultEnvelopes.set(attId, updatedEnvelope);
      state.currentEnvelope = updatedEnvelope;
    }
    sf.emitAttempt({
      attempt_id: attId,
      workspace_id: workspaceId,
      work_item_id: workId(1),
      start_operation_id: 'op_' + '1'.repeat(32),
      stage: 'completed',
      status: 'succeeded',
      session: {host: 'fixture-host', session_id: 'session-123'},
      created_at: 1791705600,
    });
  };

  return {
    ...sf,
    state,
    dispatch,
    emitResultRecorded,
  };
}
