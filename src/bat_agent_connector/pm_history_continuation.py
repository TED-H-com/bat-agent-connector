"""Read-only association of retained context and an independently published basis.

This module has no action, durable state, executor or source ownership transfer.
"""
from __future__ import annotations

import copy
import re

from . import pm_external_history as refs
from . import (
    pm_external_history_links,
    pm_plans,
    pm_work_packages,
    pm_work_start,
    repository_sync,
    work_items,
)
from .errors import BatError
from .github import GitHubAmbiguous
from .operations import OperationError

SCHEMA = 'external_history_published/v1'
ASSOCIATION = 'requester_selected_context_and_basis'
REASONS = {
    'PM_EXTERNAL_CONTINUATION_UNSUPPORTED': ('Select retained context and an explicit published branch head.', '請選取保留的內容及明確已發布的分支頂端。'),
    'PM_HISTORY_SOURCE_UNAVAILABLE': ('History source is unavailable.', '歷史來源無法使用。'),
    'PM_HISTORY_REFERENCE_INVALID': ('Select one bounded retained entry or text range.', '請選取一則範圍有限的保留項目或文字範圍。'),
    'PM_HISTORY_REFERENCE_CHANGED': ('The fixed history reference differs.', '固定的歷史參照已不符。'),
    'PM_EXTERNAL_CONTEXT_UNUSABLE': ('Select context containing nonempty text.', '請選取包含非空文字的內容。'),
    'PM_EXTERNAL_CONTINUATION_CHANGED': ('Preview the current project, scope and binding again.', '請重新預覽目前的專案、範圍及綁定。'),
    'PM_EXTERNAL_CONTINUATION_OWNER_REQUIRED': ('A workspace owner with manage and start is required for new Work.', '建立新 Work 需要具備 manage 與 start 權限的工作區擁有者。'),
    'PM_EXTERNAL_CONTINUATION_NOT_AVAILABLE': ('Continuation is planned and is not yet available.', '延續操作已規劃，目前尚未提供。'),
    'PM_EXTERNAL_CONTEXT_ASSOCIATION': ('Requester-selected context and independently verified published code basis; producer attribution is unproven.', '由請求者選取的內容與獨立驗證的已發布程式碼基礎；尚未證明產生者歸屬。'),
    'PM_WORK_START_NOT_READY': ('The selected requirements are not ready for a thin work package.', '所選需求尚未準備好建立精簡工作套件。'),
    'PM_PROJECT_BINDING_REQUIRED': ('Select an active project explicitly bound to this workspace.', '請選取明確綁定此工作區的有效專案。'),
    'PM_ADOPTED_PLAN_REQUIRED': ('Adopt a scope before previewing continuation.', '請先採納範圍，再預覽延續。'),
    'PROJECT_REPOSITORY_CHANGED': ('The selected repository does not belong to this project.', '所選儲存庫不屬於此專案。'),
    'REPOSITORY_NOT_BOUND': ('Select an explicitly bound repository, host and workspace.', '請選取明確綁定的儲存庫、主機及工作區。'),
    'REPOSITORY_HOST_UNAVAILABLE': ('The configured execution host or read client is unavailable.', '設定的執行主機或讀取用戶端無法使用。'),
    'REPOSITORY_WORKSPACE_CHANGED': ('The exact bound workspace is unavailable.', '確切綁定的工作區無法使用。'),
    'REPOSITORY_SOURCE_UNAVAILABLE': ('The exact published branch head could not be proved.', '無法證明確切的已發布分支頂端。'),
    'REPOSITORY_ID_CHANGED': ('The published repository identity differs.', '已發布儲存庫的身分不符。'),
    'REPOSITORY_REF_CHANGED': ('The branch head differs from the selected SHA; review the original selection.', '分支頂端與所選 SHA 不符；請檢查原始選取。'),
    'REPOSITORY_BINDING_CHANGED': ('The repository or workspace binding differs.', '儲存庫或工作區綁定不符。'),
    'INVALID_REF': ('Select an explicit refs/heads/ branch.', '請選取明確的 refs/heads/ 分支。'),
    'INVALID_PARAMS': ('Supply the exact bounded preview fields.', '請提供確切且範圍有限的預覽欄位。'),
    'FORBIDDEN': ('Preview requires observe and explicit workspace membership.', '預覽需要 observe 權限及明確的工作區成員資格。'),
    'PROJECT_NOT_FOUND': ('The selected project is unavailable.', '所選專案無法使用。'),
    'PROJECT_ARCHIVED': ('The selected project is archived.', '所選專案已封存。'),
}


def reason(code):
    en, zh = REASONS[code]
    return {'code': code, 'message': {'en': en, 'zh-TW': zh}}


def _error(code='PM_EXTERNAL_CONTINUATION_UNSUPPORTED', status=422):
    text = reason(code)['message']
    return OperationError(code, text['en'] + ' / ' + text['zh-TW'], status)


def _exact(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise _error()


def _matches(value, pattern):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def _text(value, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise _error()
    try:
        value.encode('utf-8')
    except UnicodeError:
        raise _error() from None


def _target(target):
    _exact(target, ('repository', 'host', 'workspace_id', 'source_ref'))
    for value in target.values():
        _text(value, 256)
        if len(value.encode('utf-8')) > 256 or any(ord(c) < 32 or 127 <= ord(c) < 160 for c in value):
            raise _error()
    if (not _matches(target['repository'], r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+')
            or not _matches(target['host'], r'[A-Za-z0-9_.-]+')):
        raise _error()
    repository_sync.check_ref(target['source_ref'])


def validate_descriptor(descriptor):
    """Shape only; a digest is neither a grant nor published proof. Use resolve."""
    _exact(descriptor, ('schema', 'server_id', 'workspace_id', 'context_ref', 'basis', 'association'))
    if descriptor['schema'] != SCHEMA or descriptor['association'] != ASSOCIATION:
        raise _error()
    ref = descriptor['context_ref']
    _exact(ref, ('schema', 'server_id', 'workspace_id', 'source_id', 'entry_id', 'source_digest',
        'content_digest', 'selection', 'excerpt_digest'))
    if (ref['schema'] != refs.SCHEMA or descriptor['server_id'] != ref['server_id']
            or descriptor['workspace_id'] != ref['workspace_id']):
        raise _error()
    _text(ref['server_id'], 128)
    if (not _matches(ref['workspace_id'], r'ws_[0-9a-f]{32}')
            or not _matches(ref['source_id'], r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}')
            or not _matches(ref['entry_id'], r'shi_[0-9a-f]{32}')
            or any(not _matches(ref[k], r'[0-9a-f]{64}') for k in
                ('source_digest', 'content_digest', 'excerpt_digest'))):
        raise _error()
    selection = ref['selection']
    if selection != {'kind': 'entry'}:
        _exact(selection, ('kind', 'part_index', 'start_codepoint', 'end_codepoint'))
        if (selection['kind'] != 'text' or any(type(selection[k]) is not int for k in
                ('part_index', 'start_codepoint', 'end_codepoint'))
                or not 0 <= selection['part_index'] < 128
                or not 0 <= selection['start_codepoint'] < selection['end_codepoint'] <= refs.MAX_CODEPOINTS):
            raise _error()
    basis = descriptor['basis']
    _exact(basis, ('repository', 'repository_id', 'host', 'workspace_id', 'source_ref', 'source_sha', 'binding_digest'))
    _target({k: basis[k] for k in ('repository', 'host', 'workspace_id', 'source_ref')})
    if (type(basis['repository_id']) is not int or basis['repository_id'] <= 0
            or not _matches(basis['source_sha'], r'[0-9a-f]{40}')
            or not _matches(basis['binding_digest'], r'[0-9a-f]{64}')):
        raise _error()
    return copy.deepcopy(descriptor)


def require_observe(principal):
    if not principal.allows('observe'):
        raise _error('FORBIDDEN', 403)


def _context(ops, principal, wid, ref):
    # Do this before any repository/project detail, including for administrators.
    require_observe(principal)
    if not _matches(wid, r'ws_[0-9a-f]{32}'):
        raise refs.unavailable()
    pm_external_history_links._member(ops.db, principal, wid)
    if not isinstance(ref, dict) or ref.get('workspace_id') != wid:
        raise refs.unavailable()
    selected = refs.resolve(ops.db, principal, ref)
    if not any(p['kind'] == 'text' and p['text'] for p in selected['content']['parts']):
        raise _error('PM_EXTERNAL_CONTEXT_UNUSABLE')
    return selected


async def _published(ops, principal, wid, ref, target):
    selected = _context(ops, principal, wid, ref)
    generation = refs._generation(ops.db)
    _target(target)
    logical = {k: target[k] for k in ('repository', 'host', 'workspace_id')}
    config = repository_sync.config_snapshot(ops, logical)
    try:
        # Contract A: unchanged proof, including exactly one read-only workspace:load.
        published = await repository_sync.source_preview(ops, target)
    except (BatError, GitHubAmbiguous, OSError):
        raise _error('REPOSITORY_SOURCE_UNAVAILABLE', 409) from None
    finally:
        # Also fence failed provider replies; withdrawal/regrant cannot leak basis errors.
        if refs._generation(ops.db) != generation:
            raise refs.unavailable()
        _context(ops, principal, wid, ref)
    if config != repository_sync.config_snapshot(ops, logical):
        raise _error('REPOSITORY_BINDING_CHANGED', 409)
    basis = {**published['target'], **{k: published[k] for k in
        ('repository_id', 'source_ref', 'source_sha', 'binding_digest')}}
    return selected, basis


async def resolve(ops, principal, workspace_id, descriptor):
    """Re-prove a fixed descriptor without retargeting it or writing a receipt."""
    try:
        _context(ops, principal, workspace_id, descriptor.get('context_ref') if isinstance(descriptor, dict) else None)
        fixed = validate_descriptor(descriptor)
        selected, basis = await _published(ops, principal, workspace_id, fixed['context_ref'],
            {k: fixed['basis'][k] for k in ('repository', 'host', 'workspace_id', 'source_ref')})
        if basis['source_sha'] != fixed['basis']['source_sha']:
            raise _error('REPOSITORY_REF_CHANGED', 409)
        if basis != fixed['basis']:
            raise _error('REPOSITORY_BINDING_CHANGED', 409)
        return _result(fixed, selected)
    except OperationError as exc:
        raise _localized(exc) from None


def _localized(exc):
    return _error(exc.code, exc.status) if exc.code in REASONS else exc


def _result(descriptor, selected):
    return {'descriptor': descriptor, 'descriptor_digest': refs._digest(descriptor),
        'excerpt': selected['content'], 'history_complete': False, 'execution_status': 'unknown',
        'producer_attribution': 'unproven', 'association': ASSOCIATION,
        'meaning': reason('PM_EXTERNAL_CONTEXT_ASSOCIATION'),
        'target': {k: descriptor['basis'][k] for k in ('repository', 'host', 'workspace_id')}}


def _planning(ops, principal, wid, body):
    role = pm_external_history_links._member(ops.db, principal, wid)
    pid = body['project_id']
    if not _matches(pid, r'prj_[0-9a-f]{20}'):
        raise _error()
    if not ops.db.execute('SELECT 1 FROM pm_project_bindings WHERE workspace_id=? AND project_id=?', (wid, pid)).fetchone():
        raise _error('PM_PROJECT_BINDING_REQUIRED', 409)
    project = work_items._get_project(ops.db, pid, active=True)
    if body['code_target']['repository'].lower() not in {r.lower() for r in project['repositories']}:
        raise _error('PROJECT_REPOSITORY_CHANGED', 409)
    plan = pm_plans.accepted_plan(ops, principal, wid)['plan']
    if plan is None:
        raise _error('PM_ADOPTED_PLAN_REQUIRED', 409)
    ids, _ = pm_work_start._resolve_requirement_ids(plan, None, body['requirement_ids'])
    work = work_items._check_item_create(ops, {'project_id': pid}, {**body['new_work'], 'request': body['instruction']})
    return {'plan': plan, 'requirements': ids, 'work': work, 'project_version': project['version'],
        'owner_ready': role == 'owner' and principal.allows('manage') and principal.allows('start')}


def validate_preview_request(body):
    """Pure wire validation, also exercised by the shared native request corpus."""
    required = {'context_ref', 'code_target', 'expected_source_sha', 'project_id', 'requirement_ids',
        'new_work', 'instruction', 'agent', 'delivery'}
    if not isinstance(body, dict) or not required <= set(body) or set(body) - required - {'model'}:
        raise _error()
    _exact(body['new_work'], ('title', 'goal', 'acceptance'))
    _target(body['code_target'])
    _text(body['instruction'], 4000)
    _text(body['new_work']['title'], 120)
    for field in ('goal', 'acceptance'):
        if not isinstance(body['new_work'][field], str):
            raise _error()
        try:
            body['new_work'][field].encode('utf-8')
        except UnicodeError:
            raise _error() from None
    if (body['agent'] not in ('claude', 'codex') or body['delivery'] not in ('pull_request', 'reviewable_changes')
            or not _matches(body['expected_source_sha'], r'[0-9a-f]{40}')):
        raise _error()
    if 'model' in body:
        _text(body['model'], 256)
    ids = body['requirement_ids']
    if (not isinstance(ids, list) or not 1 <= len(ids) <= 50
            or any(not isinstance(rid, str) or not rid.strip() or len(rid) > 128 for rid in ids)
            or len(set(ids)) != len(ids)):
        raise _error()
    if not _matches(body['project_id'], r'prj_[0-9a-f]{20}'):
        raise _error()
    ref = body['context_ref']
    if not isinstance(ref, dict):
        raise _error()
    validate_descriptor({'schema': SCHEMA, 'server_id': ref.get('server_id'),
        'workspace_id': ref.get('workspace_id'), 'context_ref': ref, 'association': ASSOCIATION,
        'basis': {**body['code_target'], 'repository_id': 1, 'source_sha': body['expected_source_sha'],
            'binding_digest': '0' * 64}})
    try:
        work_items._text(body['instruction'], 'instruction', 4000)
        work_items._one_line(body['new_work']['title'], 'title', 120)
        for field in ('goal', 'acceptance'):
            work_items._text(body['new_work'][field], field)
    except OperationError:
        raise _error() from None


async def preview(ops, principal, workspace_id, body):
    """Advisory read only. There is no continuation operation in this slice."""
    try:
        # Copy caller values before awaits; the fixed selection must never move.
        body = copy.deepcopy(body)
        _context(ops, principal, workspace_id, body.get('context_ref') if isinstance(body, dict) else None)
        validate_preview_request(body)
        local = _planning(ops, principal, workspace_id, body)
        selected, basis = await _published(ops, principal, workspace_id, body['context_ref'], body['code_target'])
        if local != _planning(ops, principal, workspace_id, body):
            raise _error('PM_EXTERNAL_CONTINUATION_CHANGED', 409)
        if basis['source_sha'] != body['expected_source_sha']:
            raise _error('REPOSITORY_REF_CHANGED', 409)
        descriptor = validate_descriptor({'schema': SCHEMA, 'server_id': body['context_ref']['server_id'],
            'workspace_id': workspace_id, 'context_ref': body['context_ref'], 'basis': basis, 'association': ASSOCIATION})
        _, doc = pm_work_start.compose_thin_package(ops, local['work'], local['plan'], local['requirements'],
            basis, body['delivery'], body['instruction'])
        checks = pm_work_packages.document_readiness_checks(doc)
        codes = []
        if not local['owner_ready']:
            codes.append('PM_EXTERNAL_CONTINUATION_OWNER_REQUIRED')
        if any(c['status'] != 'passed' for c in checks):
            codes.append('PM_WORK_START_NOT_READY')
        return {**_result(descriptor, selected),
            'readiness': {'status': 'blocked' if codes else 'ready', 'reasons': [reason(c) for c in codes],
                'checks': [{k: c[k] for k in ('check', 'status')} for c in checks]},
            'preconditions': {'expected_project_version': local['project_version'],
                'expected_scope_revision': local['plan']['adoption']['scope_revision']},
            'notice': reason('PM_EXTERNAL_CONTINUATION_NOT_AVAILABLE')}
    except OperationError as exc:
        raise _localized(exc) from None
