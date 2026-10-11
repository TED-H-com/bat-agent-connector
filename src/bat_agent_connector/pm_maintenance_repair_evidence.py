"""Original verifier/delivery receipts for maintenance; never infer acceptance."""
from __future__ import annotations

import json
import re
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
    if kind == 'required_ci':
        from . import pm_required_ci
        receipt, _ = owned_creation(ops, principal, parent)
        proof = pm_required_ci.historical(ops, principal, parent, receipt, ref)
        return {'kind': kind, **proof}
    if kind == 'quality':
        original = quality_assessment(ops, principal, parent, ref)
        return {'kind': kind, 'reference': ref, 'context_digest': original['context_digest'],
            'candidate_commit': original['context']['native_result']['candidate_commit'],
            'original_outcome': {key: original['assessment']['document'][key] for key in ('acceptance', 'dod', 'qualified')},
            'operation_id': ref['operation_id']}
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
        if 'verification_operation_id' in selected['reference']:
            from . import pm_execution_verification
            child = repairs._child(ops, parent)
            pm_execution_verification.evaluate(ops, principal, parent['target']['workspace_id'], child, proof['condition'], selected['reference'])
            return
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
    elif selected['kind'] == 'required_ci':
        from . import pm_required_ci
        receipt, _ = owned_creation(ops, principal, parent)
        if pm_required_ci.historical(ops, principal, parent, receipt, selected['reference']) != {key: value for key, value in evidence.items() if key != 'kind'}:
            raise _bad('The original required-CI receipt changed')
    elif selected['kind'] == 'quality':
        original = quality_assessment(ops, principal, parent, selected['reference'])
        if original['context_digest'] != evidence['context_digest']:
            raise _bad('Original quality context changed')
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
    from . import pm_maintenance_repair_publication as publication
    published = publication.child(ops, parent)
    row = repairs._record_for(ops, parent)
    reason = 'PM_REPAIR_EXECUTION_PENDING'
    if child and child['status'] in {'failed', 'cancelled', 'needs_attention'}:
        reason = child.get('error_code') or 'PM_REPAIR_EXECUTION_STOPPED'
    elif child and child['status'] == 'succeeded':
        reason = ('PM_REPAIR_OWNED_PR_UNPROVEN' if verifications and verifications[-1]['status'] == 'passed'
                  else 'PM_REPAIR_VERIFICATION_REQUIRED')
    if published:
        reason = 'PM_REPAIR_PUBLICATION_' + published['status'].upper()
    if time.time() >= row['deadline_at']:
        reason = 'PM_REPAIR_DEADLINE'
    if row['finished_at'] is not None:
        reason = 'PM_REPAIR_STOPPED_WITHOUT_START'
    try:
        repairs._guard(ops, parent)
        investment_blocked = None
    except OperationError as exc:
        investment_blocked = exc.code
    return {'dispatch': child['status'] if child else 'not_admitted',
        'verification': verifications[-1] if verifications else None,
        'quality': [doc['evidence'] for doc in docs if doc['evidence']['kind'] == 'quality'],
        'publication': published['status'] if published else 'not_admitted',
        'acceptance': [doc['evidence'] for doc in docs if doc['evidence']['kind'] == 'acceptance'],
        'delivery_receipts': delivery, 'blocked_reason': reason, 'new_investment_blocked_reason': investment_blocked,
        'required_ci': 'unproven',
        'required_ci_receipts': [doc['evidence'] for doc in docs if doc['evidence']['kind'] == 'required_ci'],
        'release_publication': 'not_inferred'}


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



def quality_assessment(ops, principal, parent, ref):
    from . import pm_package_handoff
    from . import pm_quality_reviews as quality
    if not isinstance(ref, dict) or set(ref) != {'review_id', 'operation_id', 'version', 'document_digest'}:
        raise _bad('Select an exact original quality assessment', 'PM_REPAIR_QUALITY_REQUIRED')
    # Historical repair readers deliberately expose only the DB. The quality
    # reader adds its own snapshot journal without using current actor authority.
    reader = ops if hasattr(ops, 'journal') else quality._Reader(ops.db)
    original = quality.get_assessment(reader, principal, parent['target']['workspace_id'], **ref)
    context, child = original['context'], repairs._child(ops, parent)
    binding = pm_package_handoff.binding(child)
    package = binding['contract']['package']
    if (context['execution_operation_id'] != child['operation_id']
            or context['package_ref'] != repairs._intent(parent)['rule']['handoff']['params']['pm_package_ref']
            or context['handoff_contract_digest'] != binding['contract_digest']
            or context['work_ref'] != package['context']['work_ref']
            or context['plan_ref'] != package['context']['plan_ref']):
        raise _bad('Quality must retain the original repair child, package, binding and scope', 'PM_REPAIR_QUALITY_MISMATCH')
    return original


def selected_quality(ops, parent):
    rows = [doc for doc in _receipts(ops, parent) if doc['selection']['kind'] == 'quality']
    if not rows:
        raise _bad('Select an original accountable quality assessment', 'PM_REPAIR_QUALITY_REQUIRED')
    return rows[-1]['selection']['reference']


def require_quality(ops, principal, parent, ref, *, candidate_sha, candidate_tree=None):
    from . import pm_quality_reviews as quality
    original = quality_assessment(ops, principal, parent, ref)
    context = original['context']
    if context['dod_ref'] is None:
        raise _bad('The original acceptance and DoD require complete assessment', 'PM_REPAIR_QUALITY_UNPROVEN')
    current = quality.evaluate_review(ops, principal, parent['target']['workspace_id'], ref['review_id'],
        binding_id=context['work_ref']['binding_id'], scope_revision=context['plan_ref']['scope_revision'],
        dod_revision_id=context['dod_ref']['revision_id'], candidate_commit=candidate_sha)['review']
    assessment = current['assessment'] or {}
    if (not current['qualified'] or any(assessment.get(key) != ref[key] for key in ('operation_id', 'version', 'document_digest'))):
        raise _bad('This exact assessment no longer proves current complete acceptance and DoD', 'PM_REPAIR_QUALITY_UNPROVEN')
    # Reuse the adopted observed-runner condition, independently of the reviewer
    # conclusion. A different passing command or candidate cannot satisfy it.
    rule = repairs._intent(parent)['rule']
    proofs = [item['verification'] for item in assessment['document']['criteria']
        if item.get('verification') and item['verification']['condition'] == rule['verification']]
    trees = {proof.get('tree_hash') for proof in proofs if proof['status'] == 'passed'
        and proof['candidate_commit'] == candidate_sha}
    if len(trees) != 1 or None in trees or (candidate_tree is not None and candidate_tree not in trees):
        raise _bad('The exact candidate tree lacks the adopted original verifier', 'PM_REPAIR_VERIFICATION_REQUIRED')
    return {**original, 'candidate_tree': next(iter(trees))}


def _paths(paths, intent, policy):
    if not isinstance(paths, list) or not paths or len(paths) > 500 or len(json.dumps(paths).encode()) > 131072:
        raise _bad('A bounded complete original diff is required', 'PM_REPAIR_PATHS_UNPROVEN')
    for path in paths:
        repairs.rules.path(path)
        if not repairs.rules.covered(path, intent['rule']['paths']) or not repairs.rules.covered(path, policy['allowed_paths']):
            raise _bad('A changed path is outside the adopted repair rule or policy', 'PM_REPAIR_PATH_OUTSIDE_POLICY')


def _ceiling(policy, level):
    if level > {'report': 0, 'pull_request': 1, 'merge': 2, 'deploy': 3}[policy['delivery']['maximum']]:
        raise _bad('This delivery exceeds the original maintenance policy ceiling', 'PM_REPAIR_DELIVERY_CEILING')


def require_publication(ops, principal, parent, *, child_operation_id, candidate_sha, candidate_tree,
                        base_ref, base_sha, files, files_complete, quality_ref):
    live = repairs._guard(ops, parent, scopes=repairs.REQUIRED | {'integrate'})
    if principal.actor != live.actor or any(not principal.allows(scope) for scope in {'observe', 'manage', 'integrate'}):
        raise _bad('Publication requires the original owner current authority', 'FORBIDDEN')
    intent, policy = repairs._intent(parent), policies_for(ops, parent)
    _ceiling(policy, 1)
    if repairs._child(ops, parent)['operation_id'] != child_operation_id:
        raise _bad('Publication changed the original owned repair execution')
    if (not isinstance(candidate_sha, str) or not re.fullmatch('[0-9a-f]{40}', candidate_sha)
            or not isinstance(candidate_tree, str) or not re.fullmatch('[0-9a-f]{40}', candidate_tree)
            or 'refs/heads/' + base_ref != intent['rule']['handoff']['params']['source_ref']
            or base_sha != intent['rule']['release']['commit_sha'] or files_complete is not True):
        raise _bad('Publication requires the exact candidate and complete adopted branch diff', 'PM_REPAIR_PUBLICATION_UNPROVEN')
    if not any(doc['selection'] == {'kind': 'quality', 'reference': quality_ref} for doc in _receipts(ops, parent)):
        raise _bad('The repair owner has not selected this exact quality assessment', 'PM_REPAIR_QUALITY_REQUIRED')
    _paths(files, intent, policy)
    return require_quality(ops, principal, parent, quality_ref, candidate_sha=candidate_sha, candidate_tree=candidate_tree)


def owned_creation(ops, principal, parent):
    from . import pm_maintenance_repair_publication as publication
    child = publication.child(ops, parent)
    if child is None:
        raise _bad('No original server-owned repair pull-request creation receipt is available', 'PM_REPAIR_OWNED_PR_UNPROVEN')
    try:
        from . import pr_publication
    except ImportError:
        raise _bad('The original PR creation receipt adapter is unavailable', 'PM_REPAIR_OWNED_PR_UNPROVEN') from None
    source = repairs._child(ops, parent)
    original = json.loads(ops.db.execute("SELECT response FROM operation_steps WHERE operation_id=? AND name='source.resolve' AND status='succeeded'", (source['operation_id'],)).fetchone()[0])
    receipt = pr_publication.require_creation_receipt(ops, principal, child['operation_id'],
        child_operation_id=source['operation_id'], provider_origin=original['config']['provider_origin'],
        repository_id=original['repository_id'], repair_parent_id=parent['operation_id'])
    return receipt, child


def require_owned_pr(ops, principal, parent, operation):
    receipt, child = owned_creation(ops, principal, parent)
    selected = ((operation.get('external_refs') or {}).get('admission_binding') or {}).get('pm_delivery') or {}
    if any(selected.get(key) != receipt[key] for key in ('provider_origin', 'repository_id', 'pull_number')):
        raise _bad('Delivery must retain the original central PR creation target', 'PM_REPAIR_OWNED_PR_UNPROVEN')
    return receipt, child


def require_delivery(ops, principal, parent, operation):
    """Gate only new effects. Existing engines reconcile original sent receipts."""
    from . import integration, pr_delivery
    action, policy = operation['action'], policies_for(ops, parent)
    if action == 'github.pr.update':
        raise _bad('Updating a repair PR has no complete original revision contract', 'PM_REPAIR_UPDATE_UNSUPPORTED')
    if action == 'github.pr.publish':
        # The publication engine runs the fixed prepared-path gate in its final
        # branch mutex / POST frame. Initial local preparation remains possible.
        repairs.require_publication_child(ops, operation)
        _ceiling(policy, 1)
        return
    level = (3 if action.startswith('deployment.') or action == 'delivery.merge_and_deploy' else
             2 if action == 'github.pr.merge' else 1 if action == 'integration.apply' else 0)
    if not level:
        return
    _ceiling(policy, level)
    receipt, published = require_owned_pr(ops, principal, parent, operation)
    if action == 'integration.apply':
        preview = json.loads(integration.get_preview(ops.db, operation['params']['preview_id'])['document'])
        source = repairs._child(ops, parent)
        rows = preview['sources']
        if (len(rows) != 1 or rows[0]['kind'] != 'execution' or rows[0]['id'] != source['operation_id']
                or rows[0]['files_total'] != len(rows[0]['files'])):
            raise _bad('Integration requires the complete original owned source')
        # This slice publishes one already fixed candidate; it cannot authorize
        # an additional mutation whose post-integration candidate is not fixed.
        raise _bad('A published repair candidate cannot be silently replaced', 'PM_REPAIR_UPDATE_UNSUPPORTED')
    if action not in {'github.pr.merge', 'delivery.merge_and_deploy'}:
        raise _bad('Deployment requires a proven original merged repair receipt', 'PM_REPAIR_DEPLOYMENT_UNPROVEN')
    preview = pr_delivery.get_preview(ops.db, operation['params']['preview_id'])
    target = preview['target']
    if (preview.get('files_may_be_truncated') is not False or preview.get('blocking')
            or target.get('native_pr_id') != receipt['native_pr_id'] or target.get('head_sha') != receipt['candidate_sha']
            or target.get('base_ref') != receipt['base_ref'] or target.get('base_sha') != receipt['base_sha']):
        raise _bad('Delivery must retain the complete original native PR and candidate', 'PM_REPAIR_PUBLICATION_UNPROVEN')
    _paths([path for item in preview['files'] for path in (item.get('filename'), item.get('previous_filename')) if path is not None], repairs._intent(parent), policy)
    require_quality(ops, principal, parent, published['params']['quality_ref'], candidate_sha=receipt['candidate_sha'])
    if policy['verification']['required_ci']:
        from . import pm_required_ci
        bound = ((operation.get('external_refs') or {}).get('admission_binding') or {}).get('pm_delivery') or {}
        ref = (bound.get('pm_repair_required_ci') or {}).get(parent['operation_id'])
        pm_required_ci.require(ops, principal, parent, receipt, ref)
    if level == 3:
        from . import deployment
        recipe = deployment.recipe(ops, operation['target']['recipe'])
        environment = deployment.bound_environment(ops, recipe)
        if environment.get('environment') != policy['delivery']['environment']:
            raise _bad('Deployment changed the original explicit environment')


def policies_for(ops, parent):
    intent = repairs._intent(parent)
    return repairs.policies._record(ops.db, parent['target']['workspace_id'], intent['check']['workload']['policy_id'])['document']['policy']


def delivery_binding(ops, principal, action, target, params, selected, originals):
    """Freeze only server-selected original repair references during admission."""
    if action not in {'github.pr.merge', 'delivery.merge_and_deploy'} or not repairs.installed(ops.db):
        return selected
    parents = [repairs._operation(ops, oid) for oid in sorted(originals)]
    parents = [parent for parent in parents if parent['action'] == repairs.ACTION]
    if not parents:
        return selected
    references = {}
    for parent in parents:
        repairs._guard(ops, parent, scopes=repairs.REQUIRED | {ops.actions[action].scope})
        if policies_for(ops, parent)['verification']['required_ci']:
            rows = [doc for doc in _receipts(ops, parent) if doc['selection']['kind'] == 'required_ci']
            if not rows:
                raise _bad('Select the original required-CI observation first', 'PM_REPAIR_REQUIRED_CI_UNPROVEN')
            references[parent['operation_id']] = rows[-1]['selection']['reference']
    result = {**selected, **({'pm_repair_required_ci': references} if references else {})}
    original = {'action': action, 'target': target, 'params': params,
        'external_refs': {'admission_binding': {'pm_delivery': result}}}
    for parent in parents:
        require_delivery(ops, principal, parent, original)
    return result
