import {ApiError} from './error-presentation.js';

// The route chooses an exact Attempt. A remembered project, latest input, or a
// similarly named session is never evidence for that choice.
export async function readWorkSessionContext({api, guard, workspaceId, workItemId, attemptId, host, sessionId, missingMessage}) {
  const [record, data] = await Promise.all([
    api('GET', `/work-items/${encodeURIComponent(workItemId)}`),
    api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}/work-attempts/${encodeURIComponent(attemptId)}`),
  ]);
  guard();
  const attempt = data?.attempt;
  const linked = record?.links?.some(link => {
    const target = link.target?.found ? link.kind === 'session' ? link.target : link.kind === 'operation' ? link.target.session : null : null;
    return target?.host === host && target?.session_id === sessionId;
  });
  if (record?.work_item?.work_item_id !== workItemId || !linked || data?.workspace_id !== workspaceId
    || attempt?.workspace_id !== workspaceId || attempt?.work_item_id !== workItemId || attempt?.attempt_id !== attemptId
    || attempt?.session?.host !== host || attempt?.session?.session_id !== sessionId) {
    throw new ApiError(404, 'WORK_CONTEXT_MISMATCH', missingMessage);
  }
  return {record, attempt};
}
