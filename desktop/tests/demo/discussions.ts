// Stateful discussion actions over the same member-access demo. No provider or host writes.
import {threadDetail, threadRows, proposalDetail, workspaceCapabilities} from './threads.ts';
import {hexId, type DemoProject} from './project.ts';
import type {DemoRequest} from './index.ts';

export function discussionDemo(project: DemoProject, fallback: (input: DemoRequest) => Promise<any>) {
  const root = `/pm/workspaces/${project.ids.workspaceId}`;
  const actions = ['pm.thread.create', 'pm.thread.rename', 'pm.message.append', 'pm.conversation.propose', 'pm.conversation.adopt'];
  const state: any = {writes: [], operations: new Map(), threads: threadRows(project), details: new Map(), proposals: [], withdrawn: false, conflict: false, running: false};
  const decorate = (data: any) => ({...data, thread: {...data.thread, context: data.thread.context || []}, capabilities: {...data.capabilities, write: true, decide: true}});
  for (const row of state.threads) state.details.set(row.thread_id, decorate(threadDetail(project, row.thread_id)));
  const caps = (value: any) => ({...value, scopes: ['observe', 'manage', 'start'], actions: actions.map(action => ({action, allowed: true})),
    features: {...value.features, pm_workspaces: {...value.features.pm_workspaces, native_analysis: true}}});
  const ok = (data: any) => ({status: 200, data: structuredClone(data)});
  const context = (refs: any[]) => refs.map((ref, position) => state.withdrawn ? {kind: ref.kind, position, available: false} : {...ref, position, available: true, label: project.copy.blocker, bound_label: project.copy.blocker});
  async function dispatch(input: DemoRequest): Promise<any> {
    const url = new URL(input.path, 'http://fixture'), path = url.pathname;
    if (input.method === 'POST' && path === '/operations') {
      state.writes.push(structuredClone(input)); const body: any = input.body;
      let operation = state.operations.get(input.idempotency_key);
      if (!operation) {
        const id = body.target.thread_id, data = state.details.get(id);
        if (body.action === 'pm.conversation.adopt' && (state.conflict || data.thread.version !== state.proposals.at(-1).context.thread_version && data.thread.version !== state.proposals.at(-1).context.thread_version + state.renames))
          return {status: 409, data: {error: {code: 'VERSION_CONFLICT', message: 'Discussion changed', admission_refused: true}}};
        let result: any = {};
        if (body.action === 'pm.thread.create') {
          const thread_id = hexId('pmt_', 700 + state.threads.length, 32);
          const thread = {...state.threads[0], thread_id, title: body.params.title, version: 1, context: context(body.params.context || [])};
          state.threads.push(thread); state.details.set(thread_id, {...decorate(threadDetail(project, project.ids.threadIdea)), thread, messages: [], inputs: [], draft: null});
          result = {workspace_id: project.ids.workspaceId, thread_id, version: 1, mode: 'idea'};
        } else if (body.action === 'pm.message.append') {
          data.messages.push({message_id: hexId('pmm_', 800 + data.messages.length, 32), sequence: data.messages.length + 1, role: 'user', actor: project.ids.actor,
            content: body.params.content, created_at: project.now / 1000, operation_id: hexId('op_', 900 + state.operations.size, 32)});
          data.thread.version++; result = {thread_id: id, version: data.thread.version};
        } else if (body.action === 'pm.thread.rename') {
          data.thread.title = body.params.title; data.thread.version++; state.renames = (state.renames || 0) + 1;
          result = {thread_id: id, version: data.thread.version};
        } else if (body.action === 'pm.conversation.propose') {
          const proposal = proposalDetail(project).proposal;
          proposal.thread_id = id; proposal.context.thread_version = data.thread.version;
          Object.assign(proposal.context, {messages: structuredClone(data.messages), inputs: structuredClone(data.inputs), draft_id: data.thread.draft_id});
          // Keep the complete demo requirements while presenting a focused blocker update.
          proposal.document.summary = project.copy.proposalSummary;
          state.proposals = [proposal]; state.renames = 0; result = {outcome: {proposal_id: proposal.proposal_id}};
        } else if (body.action === 'pm.conversation.adopt') result = {accepted: true, revision_id: hexId('pmrev_', 900, 32)};
        else throw Error('Unexpected discussion action ' + body.action);
        operation = {...body, actor: project.ids.actor, idempotency_key: input.idempotency_key, operation_id: hexId('op_', 900 + state.operations.size, 32),
          status: state.running && body.action === 'pm.conversation.propose' ? 'running' : 'succeeded', created_at: Date.now() / 1000 - 42, result};
        state.operations.set(input.idempotency_key, operation);
      }
      return ok({operation});
    }
    if (path.startsWith('/operations/')) return ok({operation: [...state.operations.values()].find((op: any) => path.endsWith(op.operation_id))});
    if (path === root + '/threads') return ok({workspace_id: project.ids.workspaceId, threads: state.threads.map((row: any) => state.details.get(row.thread_id).thread), next_cursor: null});
    if (path.startsWith(root + '/threads/')) {
      const data = state.details.get(path.split('/').at(-1));
      if (state.withdrawn) data.thread.context = context(data.thread.context || []);
      return ok(data);
    }
    if (path === root + '/capabilities') return ok({...workspaceCapabilities(project), plan_adoption_available: true,
      native_analysis: {authorized: true, eligible_for_preflight: true}});
    if (path === root + '/conversation-proposals') return ok({workspace_id: project.ids.workspaceId, proposals: state.proposals.filter((p: any) => p.thread_id === url.searchParams.get('thread_id')).map((p: any) => ({proposal_id: p.proposal_id, thread_id: p.thread_id, validation: p.validation})), next_cursor: null});
    if (path.startsWith(root + '/conversation-proposals/')) return ok({workspace_id: project.ids.workspaceId, proposal: state.proposals.find((p: any) => path.endsWith(p.proposal_id))});
    const result = await fallback(input);
    if (path === '/bootstrap') result.data.capabilities = caps(result.data.capabilities);
    if (path === '/capabilities') result.data = caps(result.data);
    const own = (row: any) => ({...row, membership: {...row.membership, role: 'owner'}});
    if (path === root) result.data.workspace = own(result.data.workspace);
    if (path === '/pm/workspaces') result.data.workspaces = result.data.workspaces.map(own);
    return result;
  }
  return {dispatch, state};
}
