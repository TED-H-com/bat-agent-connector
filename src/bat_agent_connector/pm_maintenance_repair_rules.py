"""Owner-adopted fixed engineering recipes, never inferred low-risk authority."""
from __future__ import annotations

import re

from . import pm_package_handoff, pm_work_batch_evidence, pm_work_packages, work_items
from .operations import OperationError

KEY = re.compile('[a-z][a-z0-9_]{0,47}')
FIELDS = {'key', 'work_kind', 'labels_all', 'release', 'paths', 'handoff', 'verification'}
RELEASE_FIELDS = {'baseline_id', 'baseline_digest', 'commit_sha', 'release_version'}


def bad(message, code='INVALID_PARAMS', status=422):
    return OperationError(code, message, status)


def path(value):
    if (not isinstance(value, str) or not value or len(value) > 512
            or any(ord(c) < 32 for c in value) or any(c in value for c in '\\:*?[]')):
        raise bad('Select exact repository-relative files or trailing-slash directories')
    if value != '.' and any(part in ('', '.', '..') for part in value.rstrip('/').split('/')):
        raise bad('Select exact repository-relative files or trailing-slash directories')
    return value


def covered(value, allowed):
    return any(root == '.' or value == root or root.endswith('/') and value.startswith(root) for root in allowed)


def validate(policy, baseline):
    """Validate optional recipe syntax without allocating/changing canonical defaults."""
    rules = policy.get('repair_rules', [])
    if not isinstance(rules, list) or len(rules) > 16:
        raise bad('Select at most 16 explicit fixed repair rules')
    keys, work_ids = set(), set()
    config = policy['observation'].get('maintenance_sources') or {}
    issues = config.get('issues')
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) != FIELDS or not KEY.fullmatch(str(rule.get('key'))):
            raise bad('Use exact fixed repair rule fields and a stable key')
        if rule['key'] in keys or not isinstance(rule['work_kind'], str) or rule['work_kind'] not in policy['allowed_work_kinds']:
            raise bad('Rule keys must be distinct and work kinds explicitly allowed')
        keys.add(rule['key'])
        labels = rule['labels_all']
        if (not isinstance(issues, dict) or not isinstance(labels, list) or not 1 <= len(labels) <= 20
                or any(not isinstance(label, str) or not label or len(label) > 100 or any(ord(c) < 32 for c in label) for label in labels)
                or len(set(labels)) != len(labels) or not set(issues['labels_all']) <= set(labels)):
            raise bad('Repair rules must narrow the configured exact Issue labels')
        paths = rule['paths']
        if (not isinstance(paths, list) or not 1 <= len(paths) <= 100
                or any(not covered(path(p), policy['allowed_paths']) for p in paths) or len(set(paths)) != len(paths)):
            raise bad('Rule paths must narrow the adopted policy paths')
        release = rule['release']
        if (not isinstance(release, dict) or set(release) != RELEASE_FIELDS
                or release != release_predicate(baseline)):
            raise bad('Bind the exact verified baseline, released version and commit')
        handoff = rule['handoff']
        if (not isinstance(handoff, dict) or set(handoff) != {'target', 'params', 'preconditions'}
                or any(not isinstance(value, dict) for value in handoff.values())):
            raise bad('Select the full existing repository continuation envelope')
        ref = pm_package_handoff._ref(handoff['params'].get('pm_package_ref'))
        if ref['workspace_id'] != baseline['workspace_id']:
            raise bad('Repair package and policy must belong to the same workspace')
        work_id = handoff['params'].get('work_item_id')
        if not isinstance(work_id, str) or not work_items.WORK_ITEM_ID.fullmatch(work_id) or work_id in work_ids:
            raise bad('One policy cannot assign a work item to multiple repair rules')
        work_ids.add(work_id)
        condition = pm_work_batch_evidence.condition(rule['verification'])
        if condition['profile'] != 'observed_runner_v1':
            raise bad('An observed fixed revision verifier is required; reported checks alone are not acceptance')


def release_predicate(baseline):
    return {'baseline_id': baseline['baseline_id'], 'baseline_digest': baseline['document_digest'],
            'commit_sha': baseline['document']['commit_sha'], 'release_version': baseline['document']['release_version']}


def sources(ops, principal, wid, rule, baseline, *, ready=True):
    """Prove the adopted package/version/path relation under original source access."""
    from . import repository_sync
    handoff = rule['handoff']
    ref = pm_package_handoff._ref(handoff['params'].get('pm_package_ref'))
    if ref['workspace_id'] != wid or rule['release'] != release_predicate(baseline):
        raise bad('This repair rule belongs to another verified release', 'PM_REPAIR_RELEASE_CHANGED', 409)
    if ready:
        package = pm_package_handoff._package(ops, principal, ref, write=True)
    else:
        package = pm_work_packages._record(ops.db, wid, ref['package_id'])
        if package['document_digest'] != ref['document_digest']:
            raise bad('Original fixed package digest changed', 'NOT_FOUND', 404)
    document = package['document']
    code, branch = document['code_source'], baseline['document']['proof']['maintenance_branch']
    if (code['source_sha'] != baseline['document']['commit_sha'] or code['source_ref'] != branch.get('ref')
            or document['delivery']['base_ref'] != branch.get('ref')
            or code['repository_id'] != baseline['document']['repository_id']
            or code['repository'].lower() != baseline['document']['repository'].lower()):
        raise bad('The accepted package must start at the exact release on its adopted maintenance branch', 'PM_REPAIR_RELEASE_UNPROVEN', 409)
    pm_package_handoff._matches(document, handoff['target'], handoff['params'], handoff['preconditions'])
    if ready:
        repository_sync.admit(ops, principal, handoff['target'], handoff['params'], handoff['preconditions'])
    else:
        pm_work_packages._access(ops, principal, package)
    return package


def validate_sources(ops, principal, wid, policy, baseline):
    if policy.get('repair_rules') and not principal.allows('start'):
        raise bad('Adopting executable repair rules requires original start authority', 'FORBIDDEN', 403)
    for rule in policy.get('repair_rules', []):
        sources(ops, principal, wid, rule, baseline)
