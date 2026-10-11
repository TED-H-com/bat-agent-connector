"""Fixed existing verifier receipts for explicit engineering batch prerequisites."""
from __future__ import annotations

import hashlib
import json
import re

from . import pm_github_observation, pm_release_baselines, pm_source_lineage
from .operations import OperationError

PROFILES = frozenset({'observed_runner_v1', 'github_reported_checks_v1'})
SHA = re.compile('[0-9a-f]{40}')
DIGEST = re.compile('[0-9a-f]{64}')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def bad(message):
    return OperationError('PM_BATCH_EVIDENCE_INVALID', message, 422)


def condition(value):
    if (not isinstance(value, dict) or value.get('kind') != 'fixed_revision_verification'
            or not isinstance(value.get('profile'), str) or value['profile'] not in PROFILES):
        raise bad('Use an explicit registered fixed revision verification condition')
    fields = {'kind', 'profile'} | ({'command_digest'} if value['profile'] == 'observed_runner_v1' else set())
    if set(value) != fields or ('command_digest' in fields and (not isinstance(value['command_digest'], str) or not DIGEST.fullmatch(value['command_digest']))):
        raise bad('The selected verification profile has invalid fields')
    return value


def evaluate(ops, principal, wid, child, selected, reference):
    """No external reads and no branch-to-execution inference."""
    condition(selected)
    if not child or child['action'] != 'repository.continue':
        raise bad('An original admitted engineering child is required')
    from .pm_access import require_visible
    require_visible(ops.db, principal, child)
    base = {'condition': selected, 'source_operation_id': child['operation_id'],
        'semantic_acceptance': 'not_evaluated', 'operation_ids': [child['operation_id']]}
    if selected['profile'] == 'observed_runner_v1':
        return {**base, **_runner(ops, principal, wid, child, selected, reference)}
    return {**base, **_github(ops, principal, wid, child, reference)}


def require_sources(ops, principal, wid, proof):
    """Gate every immutable proof source before exposing saved receipt metadata."""
    from .pm_access import require_visible
    for oid in proof.get('operation_ids', []):
        require_visible(ops.db, principal, ops.get(oid))
    if proof.get('task_id'):
        from . import pm_evidence
        if not pm_evidence._task_visible(ops, principal, wid, proof['task_id']):
            raise OperationError('NOT_FOUND', 'Original verifier source is unavailable', 404)
        pm_source_lineage.require(ops.db, principal, 'execution', proof['task_id'])


def _runner(ops, principal, wid, child, selected, ref):
    if isinstance(ref, dict) and 'verification_operation_id' in ref:
        from . import pm_execution_verification
        return pm_execution_verification.evaluate(ops, principal, wid, child, selected, ref)
    if not isinstance(ref, dict) or set(ref) != {'verification_id', 'candidate_commit', 'tree_hash'}:
        raise bad('Select an exact observed verification and full candidate commit/tree')
    if type(ref['verification_id']) is not int or not 0 < ref['verification_id'] < 2**63 or any(not isinstance(ref[k], str) or not SHA.fullmatch(ref[k]) for k in ('candidate_commit', 'tree_hash')):
        raise bad('Invalid observed verifier identity')
    row = ops.db.execute('SELECT * FROM observed_verifications WHERE verification_id=?', (ref['verification_id'],)).fetchone()
    if row is None:
        raise bad('The selected observed verification is unavailable')
    row = dict(row)
    task = ops.db.execute('SELECT * FROM tasks WHERE task_id=?', (row['task_id'],)).fetchone()
    from . import controller_authority, pm_evidence
    if not pm_evidence._task_visible(ops, principal, wid, row['task_id']):
        raise OperationError('NOT_FOUND', 'Original verifier source is unavailable', 404)
    result = child.get('result') or {}
    # These are exact central identities; neither prose nor a matching worktree
    # pathname can attach an unrelated verifier to this child.
    related = bool(task and result.get('session_id') and task['host'] == result.get('host')
        and task['session_id'] == result['session_id']
        and controller_authority.same_controller(json.loads(task['controller_authority'] or 'null'), controller_authority.operation_binding(child)))
    if related:
        pm_source_lineage.require(ops.db, principal, 'execution', row['task_id'])
    latest = ops.db.execute('SELECT verification_id FROM observed_verifications WHERE task_id=? ORDER BY verification_id DESC LIMIT 1', (row['task_id'],)).fetchone()
    passed = (related and latest[0] == row['verification_id'] and row['candidate_commit'] == ref['candidate_commit']
        and row['tree_hash'] == ref['tree_hash'] and row['exit_code'] == 0
        and DIGEST.fullmatch(row['output_sha256']) and digest(row['command']) == selected['command_digest']
        and task['verification_commit'] == ref['candidate_commit'] and task['verification_tree'] == ref['tree_hash'])
    return {'status': 'passed' if passed else 'blocked',
        'reason': None if passed else 'observed_verification_unproven', 'reference': ref,
        'candidate_commit': ref['candidate_commit'], 'tree_hash': ref['tree_hash'],
        'task_id': row['task_id'], 'verification_id': row['verification_id'],
        'receipt_digest': digest(row), 'command_digest': digest(row['command'])}


def _github(ops, principal, wid, child, ref):
    fields = {'observation_id', 'document_digest', 'integration_operation_id', 'source_seq', 'candidate_commit'}
    if not isinstance(ref, dict) or set(ref) != fields or type(ref.get('source_seq')) is not int or not 0 < ref['source_seq'] < 2**63:
        raise bad('Select one exact integration source receipt and immutable GitHub observation')
    if (not isinstance(ref['candidate_commit'], str) or not SHA.fullmatch(ref['candidate_commit'])
            or not isinstance(ref['document_digest'], str) or not DIGEST.fullmatch(ref['document_digest'])
            or not isinstance(ref['integration_operation_id'], str) or not re.fullmatch('op_[0-9a-f]{32}', ref['integration_operation_id'])
            or not isinstance(ref['observation_id'], str) or not pm_github_observation.OBSERVATION.fullmatch(ref['observation_id'])):
        raise bad('Invalid fixed revision or observation digest')
    row = ops.db.execute('SELECT * FROM operations WHERE operation_id=?', (ref['integration_operation_id'],)).fetchone()
    from .operations import OperationService
    from .pm_access import require_visible
    integration = OperationService._decode(row) if row else None
    if not integration or integration['action'] != 'integration.apply':
        raise bad('The original integration operation is unavailable')
    require_visible(ops.db, principal, integration)
    receipt = ops.db.execute('SELECT * FROM integration_receipts WHERE operation_id=? AND seq=?',
        (integration['operation_id'], ref['source_seq'])).fetchone()
    target = ((integration.get('external_refs') or {}).get('admission_binding') or {}).get('pm_delivery') or {}
    exact = bool(receipt and receipt['source_kind'] == 'execution' and receipt['source_id'] == child['operation_id']
        and receipt['status'] == 'delivered' and receipt['delivered_sha'] == ref['candidate_commit'])
    observation = pm_github_observation._record(ops.db, wid, ref['observation_id'])
    pm_github_observation._access(ops, principal, wid, observation)
    if observation['document_digest'] != ref['document_digest']:
        raise bad('The original observation digest does not match')
    document = observation['document']
    baseline = pm_release_baselines._record(ops.db, wid, observation['baseline_id'])
    config = baseline['document']['intent']['config']
    evidence = document['evidence']
    facts = evidence.get('facts') or {}
    checks = (facts.get('heads') or {}).get(ref['candidate_commit']) or {}
    runs, statuses = checks.get('checks') or [], checks.get('statuses') or []
    exact = (exact and document['scope']['repository_id'] == target.get('repository_id')
        and config['provider_digest'] == digest({'api_url': target.get('provider_origin')})
        and any(pull['number'] == target.get('pull_number') and pull['head_sha'] == ref['candidate_commit'] for pull in facts.get('pulls', [])))
    passed = (exact and evidence.get('status') == 'complete' and evidence.get('traversals_completed') == 2
        and bool(runs or statuses) and all(run['head_sha'] == ref['candidate_commit'] and run['status'] == 'completed' and run['conclusion'] == 'success' for run in runs)
        and all(status['head_sha'] == ref['candidate_commit'] and status['state'] == 'success' for status in statuses))
    return {'status': 'passed' if passed else 'blocked', 'reason': None if passed else 'github_fixed_result_unproven',
        'reference': ref, 'candidate_commit': ref['candidate_commit'], 'operation_ids': [child['operation_id'], integration['operation_id'], observation['operation_id']],
        'receipt_digest': digest(dict(receipt)) if receipt else None,
        'coverage': 'all_reported_checks_at_exact_original_head', 'semantic_acceptance': 'not_evaluated'}
