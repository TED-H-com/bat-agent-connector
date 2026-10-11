"""Original verifier/delivery receipts for maintenance; never infer acceptance."""
from __future__ import annotations

import json
import time

from . import pm_delivery_lineage, pm_source_lineage, pm_work_batch_evidence, pm_workspace, work_items
from . import pm_maintenance_repairs as repairs
from .operations import OperationError


def _bad(message, code='PM_REPAIR_EVIDENCE_UNPROVEN'):
    return OperationError(code, message, 409)


def _receipts(ops, parent):
    result = []
    for row in ops.db.execute('SELECT * FROM pm_maintenance_repair_evidence WHERE parent_id=? ORDER BY sequence', (parent['operation_id'],)):
        doc = json.loads(row['document'])
        if repairs.checks._digest(doc) != row['document_digest']:
            raise _bad('Original evidence receipt changed')
        result.append(doc)
        if len(result) > 64:
            raise _bad('Original evidence exceeds the bounded history')
    return result


def _source_operation(ops, principal, parent, oid):
    from .pm_access import require_visible
    original = repairs._operation(ops, oid)
    require_visible(ops.db, principal, original)
    child = repairs._child(ops, parent)
    if not child or parent['operation_id'] not in pm_source_lineage.roots(ops.db, 'operation', oid):
        raise _bad('Receipt has no original repair source relation')
    if child['operation_id'] not in pm_source_lineage.roots(ops.db, 'operation', oid):
        raise _bad('Receipt does not belong to the original owned repair execution')
    return original


def evaluate(ops, principal, parent, selected):
    if not isinstance(selected, dict) or set(selected) != {'kind', 'reference'}:
        raise _bad('Select one exact original central evidence reference')
    intent = repairs._intent(parent)
    child = repairs._child(ops, parent)
    if child is None:
        raise _bad('No original repair execution exists')
    kind, ref = selected['kind'], selected['reference']
    if kind == 'verification':
        proof = pm_work_batch_evidence.evaluate(ops, principal, parent['target']['workspace_id'], child,
                                               intent['rule']['verification'], ref)
        value = {'kind': kind, 'proof': proof, 'semantic_acceptance': 'not_evaluated', 'required_ci': 'unproven'}
        require_selected_sources(ops, principal, parent, selected, value)
        return value
    if not isinstance(ref, dict) or set(ref) != {'operation_id'}:
        raise _bad('Select an exact central operation receipt')
    op = _source_operation(ops, principal, parent, ref['operation_id'])
    if kind == 'acceptance':
        item = work_items._get_item(ops.db, intent['work_item_id'])
        result = op.get('result') or {}
        if (op['action'] != 'work_item.approve' or op['status'] != 'succeeded'
                or op['target'].get('work_item_id') != intent['work_item_id']
                or result.get('fingerprint') != work_items.fingerprint(item)
                or not work_items.completion(item)['approved']
                or result.get('approved_by') != intent['check']['controller']['actor']):
            raise _bad('Acceptance requires the original owner\'s exact completed work approval')
        return {'kind': kind, 'operation_id': op['operation_id'], 'fingerprint': result['fingerprint'],
                'approved_by': result['approved_by'], 'status': 'accepted', 'basis': 'explicit_work_item_approval'}
    if kind == 'delivery':
        if op['action'] not in pm_delivery_lineage.ACTIONS:
            raise _bad('Select an original fixed delivery operation')
        return {'kind': kind, 'operation_id': op['operation_id'], 'facts': pm_delivery_lineage._facts(ops, op),
                'release_publication': 'not_inferred', 'requirement_acceptance': 'not_evaluated_by_delivery'}
    raise _bad('Unsupported repair evidence kind')


def require_selected_sources(ops, principal, parent, selected, evidence):
    # Availability gates original identities, not current success/approval.
    if selected['kind'] == 'verification':
        proof = evidence['proof']
        receipt = ops.db.execute('SELECT * FROM observed_verifications WHERE verification_id=?',
                              (selected['reference']['verification_id'],)).fetchone()
        task = ops.db.execute('SELECT host,session_id FROM tasks WHERE task_id=?', (proof['task_id'],)).fetchone()
        child = repairs._child(ops, parent)
        result = child.get('result') or {}
        if (not receipt or receipt['task_id'] != proof['task_id']
                or repairs.checks._digest(dict(receipt)) != proof['receipt_digest']
                or not task or task['host'] != result.get('host') or task['session_id'] != result.get('session_id')):
            raise OperationError('NOT_FOUND', 'Original verifier identity unavailable', 404)
        pm_source_lineage.require(ops.db, principal, 'execution', proof['task_id'])
        from .pm_access import require_visible
        require_visible(ops.db, principal, repairs._child(ops, parent))
    else:
        _source_operation(ops, principal, parent, selected['reference']['operation_id'])


def require_evidence_sources(ops, principal, parent):
    for doc in _receipts(ops, parent):
        require_selected_sources(ops, principal, parent, doc['selection'], doc['evidence'])


def progress(ops, parent):
    docs = _receipts(ops, parent)
    verifications = [doc['evidence']['proof'] for doc in docs if doc['evidence']['kind'] == 'verification']
    delivery = [doc['evidence']['facts'] for doc in docs if doc['evidence']['kind'] == 'delivery']
    child = repairs._child(ops, parent)
    row = repairs._record_for(ops, parent)
    reason = 'PM_REPAIR_EXECUTION_PENDING'
    if child and child['status'] in {'failed', 'cancelled', 'needs_attention'}:
        reason = child.get('error_code') or 'PM_REPAIR_EXECUTION_STOPPED'
    elif child and child['status'] == 'succeeded':
        reason = ('PM_REPAIR_OWNED_PR_UNPROVEN' if verifications and verifications[-1]['status'] == 'passed'
                  else 'PM_REPAIR_VERIFICATION_REQUIRED')
    if time.time() >= row['deadline_at']:
        reason = 'PM_REPAIR_DEADLINE'
    try:
        repairs._guard(ops, parent)
        investment_blocked = None
    except OperationError as exc:
        investment_blocked = exc.code
    return {'dispatch': child['status'] if child else 'not_admitted',
        'verification': verifications[-1] if verifications else None,
        'acceptance': [doc['evidence'] for doc in docs if doc['evidence']['kind'] == 'acceptance'],
        'delivery_receipts': delivery, 'blocked_reason': reason, 'new_investment_blocked_reason': investment_blocked,
        'required_ci': 'unproven', 'release_publication': 'not_inferred'}


def _check(ops, principal, target, params, pre):
    if (set(target) != {'workspace_id', 'repair_id'} or set(params) != {'evidence'}
            or set(pre) != {'expected_repair_version'} or type(pre['expected_repair_version']) is not int):
        raise _bad('Use the exact repair version and central evidence selector', 'INVALID_PARAMS')
    if not principal.allows('observe') or not principal.allows('manage'):
        raise OperationError('FORBIDDEN', 'Receipt recording requires observe and manage', 403)
    member = pm_workspace.require_member(ops.db, principal, target['workspace_id'], decide=True)
    if member['membership']['role'] != 'owner':
        raise OperationError('FORBIDDEN', 'A current actual owner records acceptance evidence', 403)
    row = repairs._row(ops, target['workspace_id'], target['repair_id'])
    if row['version'] != pre['expected_repair_version']:
        raise _bad('Read the current repair version first', 'VERSION_CONFLICT')
    parent = repairs._operation(ops, row['operation_id'])
    repairs._full_access(ops, principal, parent)
    if len(_receipts(ops, parent)) >= 64:
        raise _bad('The bounded original repair evidence history is full')
    return parent, evaluate(ops, principal, parent, params['evidence'])


def admit(ops, principal, target, params, pre):
    with ops.journal.tx():
        parent, proof = _check(ops, principal, target, params, pre)
    return {'principal': pm_workspace.principal_binding(principal),
        'pm_repair_evidence': {'parent_id': parent['operation_id'], 'proof': proof,
                               'operation_ids': sorted({parent['operation_id'], *(proof.get('proof') or {}).get('operation_ids', []),
                                   *([proof['operation_id']] if proof.get('operation_id') else [])}),
                               'task_id': (proof.get('proof') or {}).get('task_id')},
        'pm_lineage': {'workspace_id': target['workspace_id']}}


async def run(ctx):
    def record():
        principal = pm_workspace.admitted_principal(ctx)
        parent, proof = _check(ctx.service, principal, ctx.target, ctx.params, ctx.preconditions)
        bound = (ctx.admission_binding or {}).get('pm_repair_evidence') or {}
        if proof != bound.get('proof') or parent['operation_id'] != bound.get('parent_id'):
            raise _bad('The exact original evidence changed before recording')
        version, now = ctx.preconditions['expected_repair_version'] + 1, time.time()
        doc = {'selection': ctx.params['evidence'], 'evidence': proof, 'operation_id': ctx.operation_id,
               'version': version, 'created_at': now}
        ctx.service.db.execute('INSERT INTO pm_maintenance_repair_evidence(parent_id,operation_id,version,document,document_digest,created_at) VALUES(?,?,?,?,?,?)',
            (parent['operation_id'], ctx.operation_id, version, repairs.checks._json(doc), repairs.checks._digest(doc), now))
        ctx.service.db.execute('UPDATE pm_maintenance_repairs SET version=? WHERE operation_id=?', (version, parent['operation_id']))
        result = {'workspace_id': ctx.target['workspace_id'], 'repair_id': ctx.target['repair_id'],
                  'version': version, 'evidence': proof, 'repair_fixed': False, 'release_publication': 'not_inferred'}
        ctx.service.journal.api_event('pm_maintenance_repair', ctx.target['repair_id'], 'pm.maintenance.repair.evidence',
            {**result, 'operation_id': ctx.operation_id}, actor=ctx.actor)
        return result
    return ctx.effect('pm.repair.evidence', record, receipt_only=True)


def _paths(paths, intent):
    allowed = intent['rule']['paths']
    for path in paths:
        repairs.rules.path(path)
        if not repairs.rules.covered(path, allowed):
            raise _bad('A changed path is outside the adopted repair rule', 'PM_REPAIR_PATH_OUTSIDE_POLICY')


def require_owned_pr(ops, principal, parent, operation):
    # A new integration preview's own association cannot establish that its target
    # was created for this repair. No registered creation receipt exists yet.
    raise _bad('No original server-owned repair pull-request creation receipt is available',
               'PM_REPAIR_OWNED_PR_UNPROVEN')


def require_delivery(ops, principal, parent, operation):
    """Gate only new effects. The delivery engine owns original sent read-back."""
    from . import integration, pr_delivery
    intent, action = repairs._intent(parent), operation['action']
    policy = policies_for(ops, parent)
    maximum = policy['delivery']['maximum']
    rank = {'report': 0, 'pull_request': 1, 'merge': 2, 'deploy': 3}
    level = (3 if action.startswith('deployment.') or action == 'delivery.merge_and_deploy' else
             2 if action == 'github.pr.merge' else 1 if action in {'integration.apply', 'github.pr.update'} else 0)
    if level > rank[maximum]:
        raise _bad('This delivery exceeds the original maintenance policy ceiling', 'PM_REPAIR_DELIVERY_CEILING')
    if not level:
        return
    require_owned_pr(ops, principal, parent, operation)
    if action in {'integration.apply', 'github.pr.merge', 'delivery.merge_and_deploy'}:
        if action == 'integration.apply':
            row = integration.get_preview(ops.db, operation['params']['preview_id'])
            preview = json.loads(row['document'])
            child = repairs._child(ops, parent)
            sources = preview['sources']
            selected = [source for source in sources if source['kind'] == 'execution' and source['id'] == child['operation_id']]
            if len(selected) != 1 or len(sources) != 1 or selected[0]['files_total'] != len(selected[0]['files']):
                raise _bad('Integration requires one exact owned repair source and complete paths')
            _paths(selected[0]['files'], intent)
            base_ref = preview['target']['base_ref']
        else:
            preview = pr_delivery.get_preview(ops.db, operation['params']['preview_id'])
            if preview.get('files_may_be_truncated') is not False or preview.get('blocking'):
                raise _bad('A complete original fixed-head delivery diff is required')
            _paths([path for item in preview['files'] for path in (item.get('filename'), item.get('previous_filename')) if path is not None], intent)
            base_ref = preview['target']['base_ref']
        if 'refs/heads/' + base_ref != intent['rule']['handoff']['params']['source_ref']:
            raise _bad('Delivery changed the adopted maintenance branch')
    if level >= 2:
        # No available receipt currently proves the policy's required CI set.
        # Passing arbitrary reported checks is not promoted to that guarantee.
        if policy['verification']['required_ci']:
            raise _bad('Required CI policy coverage is not proven by the available receipt profile', 'PM_REPAIR_REQUIRED_CI_UNPROVEN')
        receipts = _receipts(ops, parent)
        if not any(doc['evidence']['kind'] == 'verification' and doc['evidence']['proof']['status'] == 'passed' for doc in receipts):
            raise _bad('The fixed repair revision has no passed original verifier receipt')
        if policy['verification']['review_required'] and not any(doc['evidence']['kind'] == 'acceptance' for doc in receipts):
            raise _bad('The adopted owner review requirement has no original acceptance receipt')
    if level == 3:
        selected = (operation.get('external_refs') or {}).get('admission_binding', {}).get('pm_delivery') or {}
        from . import deployment
        recipe = deployment.recipe(ops, operation['target']['recipe'])
        environment = deployment.bound_environment(ops, recipe)
        if environment.get('environment') != policy['delivery']['environment'] or not selected:
            raise _bad('Deployment changed the original explicit environment')


def policies_for(ops, parent):
    intent = repairs._intent(parent)
    return repairs.policies._record(ops.db, parent['target']['workspace_id'], intent['check']['workload']['policy_id'])['document']['policy']
