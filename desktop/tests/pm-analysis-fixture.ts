import {pmFixture, threadId, workspaceId} from './pm-fixture';

export const runId = 'pmr_' + 'd'.repeat(32), messageId = 'pmm_' + 'e'.repeat(32);
export const nativeModel = 'fixture-native-model';
export const answer = 'The saved sources support simplifying the first action. The tour is still a proposal. <script>never execute</script>';
export function nativeFixture() {
  const f = pmFixture(); f.state.start = true;
  const native = {available: true, eligible: true, reason: null as string|null, pending: false};
  const bat = {available: false, eligible: true, reason: null as string|null, pending: false, binding: {host:'fixture-bat', workspace_id:'workspace-source', repository:'demo/library'} as any};
  const capability = () => ({adapter: 'codex-native-linux', contract: 'codex-pm-linux-v1', execution_scope: 'frozen_context_only',
    eligible_for_preflight: native.eligible, reason: native.reason, options_confirmed: false, auth_validity: 'unverified', model_execution_verified: false});
  const complete = (adapter = 'codex-native-linux') => {
    const run = f.state.runs.find(run => run.adapter === adapter);
    if (!run || run.state === 'completed') return;
    const provenance = {run_id: runId, native_turn_id: 'native-turn', native_message_id: 'native-answer',
      sdk_session_id: 'native-thread', model: nativeModel, completeness: 'complete', context_digest: 'c'.repeat(64),
      source_refs: {adapter: run.adapter, native_thread_id: 'native-thread'}};
    f.state.messages.push({message_id: messageId, sequence: 2, actor: 'pm:' + workspaceId,
      role: 'assistant', provenance, content: answer, created_at: Date.parse('2026-10-11T12:02:00Z') / 1000} as any);
    run.state = 'completed'; run.outcome = {message_id: messageId, actual_model: nativeModel, completeness: 'complete'};
    const op = [...f.state.ops.values()].find(op => op.operation_id === run.operation_id);
    op.status = 'succeeded'; op.result = {run_id: runId, workspace_id: workspaceId, thread_id: threadId,
      state: run.state, context_digest: 'c'.repeat(64), adapter: run.adapter, execution_authorized: false, outcome: run.outcome};
    f.state.thread.version++; f.state.cursor++;
  };
  const dispatch = async(input:any) => {
    const path = new URL(input.path, 'http://fixture').pathname;
    if (input.method === 'POST' && path === '/operations' && ['pm.run.analyze','pm.run.bat.analyze'].includes(input.body.action)) {
      const selected = input.body.action === 'pm.run.bat.analyze' ? bat : native;
      const adapter = selected === bat ? 'bat-pm-linux-v1' : 'codex-native-linux';
      f.state.writes.push(structuredClone(input));
      if (f.state.reject) return {status: f.state.reject.status, data: {error: {code: f.state.reject.code, message: 'Rejected request', admission_refused: true}}};
      let op = f.state.ops.get(input.idempotency_key);
      if (!op) {
        const operation_id = 'op_' + 'f'.repeat(32);
        op = {...structuredClone(input.body), actor: f.state.actor, idempotency_key: input.idempotency_key,
          operation_id, status: 'running', result: null};
        f.state.ops.set(input.idempotency_key, op);
        f.state.runs.push({run_id: runId, workspace_id: workspaceId, thread_id: threadId,
          purpose: input.body.params.purpose, state: 'running', adapter, operation_id,
          created_at: Date.parse('2026-10-11T12:01:00Z') / 1000,
          context: {thread_version: f.state.thread.version, messages: structuredClone(f.state.messages), inputs_by_status: {}, coverage: {}}});
        if (!selected.pending) complete(adapter);
      }
      if (f.state.lost) {f.state.lost = false; return {status:503,data:{error:{code:'LOST_REPLY',message:'Reply unavailable'}}};}
      return {status: 200, data: {operation: op}};
    }
    const response = await f.dispatch(input);
    if (path === '/capabilities' || path === '/bootstrap') {
      const caps = path === '/bootstrap' ? response.data.capabilities : response.data;
      if (bat.available) {
        caps.features.pm_workspaces.bat_analysis = true;
        caps.actions.push({action:'pm.run.bat.analyze',allowed:f.state.manage && f.state.start});
      }
      if (native.available) {
        caps.features.pm_workspaces.native_analysis = true;
        caps.actions.push({action:'pm.run.analyze',allowed:f.state.manage && f.state.start});
      }
    }
    if (path.endsWith('/capabilities') && path.startsWith('/pm/')) {
      response.data.native_analysis = capability();
      response.data.bat_analysis = {adapter:'bat-pm-linux-v1', contract:'codex-pm-linux-v1', action_registered:bat.available, authorized:f.state.manage && f.state.start, eligible_for_preflight:bat.eligible, reason:bat.reason, selected_binding:bat.binding, execution_scope:'frozen_context_only', runtime_attestation_required:true, options_confirmed:false, auth_validity:'unverified', model_execution_verified:false};
    }
    return response;
  };
  return {...f, native, bat, dispatch, complete};
}
