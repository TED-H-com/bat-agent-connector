// Issue mappings and engineering receipts. RTM joins these to the kanban and the adopted plan.
import {digest, hexId, sha40, unix, type DemoProject} from './project.ts';

export function issueMappings(project: DemoProject) {
  const email = project.work.find(row => row.key === 'email');
  if (!email) throw new Error('Demo email work is missing');
  const document = {
    revision_id: project.ids.issueRevision, mapping_id: project.ids.issueMapping, workspace_id: project.ids.workspaceId,
    work_item_id: email.id, operation_id: project.ids.mergeOp, state: 'tracking', execution_authorized: false, counts_as_done: false,
    relation: {work_item_id: email.id, binding_id: email.bindingId, repository: project.ids.repository, repository_id: project.ids.repositoryId,
      requirement_ids: [email.requirementId]},
    package_ref: {package_id: hexId('pmwp_', 1, 32), document_digest: digest(42)},
    selected: {public_title: project.copy.issueTitle},
    issue: {repository: project.ids.repository, number: 41},
  };
  return [{
    sequence: 1, revision_id: project.ids.issueRevision, mapping_id: project.ids.issueMapping, workspace_id: project.ids.workspaceId,
    work_item_id: email.id, previous_revision_id: null, document_digest: digest(41), operation_id: project.ids.mergeOp,
    created_at: unix(project.now, -3), actor: project.ids.actor, document,
  }];
}

export function pageIssues(project: DemoProject, url: URL) {
  const after = Number(url.searchParams.get('after') || 0);
  const limit = Number(url.searchParams.get('limit') || 20);
  const rows = issueMappings(project).filter(row => row.sequence > after);
  const slice = rows.slice(0, limit);
  return {version: 1, workspace_id: project.ids.workspaceId, mappings: slice,
    next_cursor: rows.length > slice.length && slice.length ? slice[slice.length - 1].sequence : null,
    coverage: {returned: slice.length, scanned: slice.length, visibility_filtered: false, byte_limited: false, has_more: rows.length > slice.length}};
}

function receipt(project: DemoProject, operationId: string, action: string, pull: number, deployed: boolean) {
  const body: Record<string, unknown> = {
    operation_id: operationId, action, status: 'succeeded', requirement_acceptance: 'not_evaluated_by_delivery',
    target: {provider_origin: project.ids.providerOrigin, repository: project.ids.repository, repository_id: project.ids.repositoryId, pull_number: pull},
    merge: {merged: true, verified: true, merged_sha: sha40(pull)},
  };
  if (deployed) body.deployment = {environment: 'production', verified: true, is_current: true, source_sha: sha40(pull)};
  return body;
}

export function deliveries(project: DemoProject, workItemId: string) {
  const email = project.work.find(row => row.key === 'email');
  if (workItemId !== email?.id) return [];
  return [
    {seq: 1, body: receipt(project, project.ids.mergeOp, 'github.pr.merge', 87, false)},
    {seq: 2, body: receipt(project, project.ids.deployOp, 'delivery.merge_and_deploy', 91, true)},
  ];
}

export function deliveryPage(project: DemoProject, workItemId: string, url: URL) {
  const after = Number(url.searchParams.get('after') || 0);
  const limit = Number(url.searchParams.get('limit') || 20);
  const rows = deliveries(project, workItemId).filter(row => row.seq > after);
  const slice = rows.slice(0, limit);
  return {
    workspace_id: project.ids.workspaceId, work_item_id: workItemId, contract: 'pm-engineering-delivery/v1', original_packages: [],
    deliveries: slice.map(row => row.body), next_after: rows.length > slice.length && slice.length ? slice[slice.length - 1].seq : null,
    requirement_acceptance: 'not_evaluated_by_delivery', source_visibility_filtered: false,
  };
}
