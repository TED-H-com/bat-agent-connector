"""Cancel one proven-unsent Work input, then retain its source in an Idea.

Both effects use the central journal. A failed draft save never undoes a
cancellation or creates another send; resume only retries the unsaved effect.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time

from . import controller_authority, pm_discussions, pm_thread_contexts, pm_workspace
from . import pm_work_follow_up as follow
from .operations import ActionDef, NeedsAttention, OperationError, Wait

ACTION = 'pm.work_input.defer'


def install(ops):
    with ops.journal.tx():
        ops.db.execute("CREATE INDEX IF NOT EXISTS pm_work_input_ideas ON operations "
            "(json_extract(target,'$.source_operation_id'),created_at) WHERE action='pm.work_input.defer'")
    if ACTION not in ops.actions:
        ops.register(ActionDef(ACTION, 'manage', 'Retain an unsent Work instruction as an Idea',
            _run, _admit, ('workspace_id', 'work_item_id', 'source_operation_id'),
            authorize_existing=_authorize_existing))


def receipt(db, source_id):
    """Only a committed cancellation claim can link its draft-save operation."""
    row = db.execute("""SELECT o.operation_id,o.status,o.result FROM operations o
        JOIN operation_steps s ON s.operation_id=o.operation_id
        AND s.name='work_input.cancel' AND s.status='succeeded'
        WHERE o.action='pm.work_input.defer' AND json_extract(o.target,'$.source_operation_id')=?
        ORDER BY o.created_at LIMIT 1""", (source_id,)).fetchone()
    if row is None:
        return None
    value = {'operation_id': row['operation_id'], 'status': row['status']}
    result = json.loads(row['result'] or '{}')
    if row['status'] == 'succeeded' and result.get('source_operation_id') == source_id:
        value['thread_id'] = result['thread_id']
    return value


def _changed():
    return OperationError('PM_WORK_INPUT_NOT_QUEUED',
        'Only a queued, proven-unsent instruction can be retained for later / 只有尚未送出的排隊指令能留到稍後', 409)


def source(ops, principal, wid, identity, *, work_item_id=None):
    """Read the exact original input with its current source access."""
    op = ops.get(identity, steps=False, principal=principal)
    receipt = follow.build_input_receipt(ops.db, op)
    if (not receipt or receipt['workspace_id'] != wid
            or (work_item_id is not None and receipt['work_item_id'] != work_item_id)):
        raise pm_thread_contexts.unavailable()
    return op, receipt


def operation_visible(db, principal, operation):
    try:
        reader = pm_thread_contexts.Reader(db)
        original = reader.get(operation['operation_id'])
        target = original['target']
        source(reader, principal, target['workspace_id'], target['source_operation_id'],
            work_item_id=target['work_item_id'])
        return True
    except (OperationError, KeyError, TypeError):
        return False


def _authority(ops, principal, target):
    if not all(principal.allows(scope) for scope in ('observe', 'manage', 'operate')):
        raise OperationError('FORBIDDEN', 'Retaining a queued input needs observe, manage and operate scopes', 403)
    pm_workspace.require_member(ops.db, principal, target['workspace_id'], write=True)
    op, receipt = source(ops, principal, target['workspace_id'], target['source_operation_id'],
        work_item_id=target['work_item_id'])
    follow.authorize_existing(ops, principal, op, 'defer')
    return op, receipt


def _queued(ops, principal, target):
    op, receipt = _authority(ops, principal, target)
    if receipt['state'] != 'queued' or op['cancel_requested']:
        raise _changed()
    follow.check_cancel_allowed(ops, principal, op)
    return op


def _admit(ops, principal, target, params, pre):
    if (set(target) != {'workspace_id', 'work_item_id', 'source_operation_id'}
            or not isinstance(target['source_operation_id'], str)
            or not re.fullmatch(r'op_[0-9a-f]{32}', target['source_operation_id'])):
        raise OperationError('INVALID_TARGET', 'Select the exact original Work input', 422)
    if set(params) != {'reason'}:
        raise OperationError('INVALID_PARAMS', 'Supply a reason for retaining the input', 422)
    pm_discussions._text(params['reason'], 'reason', 4000)
    if pre != {'expected_state': 'queued'}:
        raise OperationError('PRECONDITION_REQUIRED', 'Require the original queued state', 422)
    _queued(ops, principal, target)
    return {'principal': pm_workspace.principal_binding(principal),
        'pm_work_input_idea': {'source_operation_id': target['source_operation_id']},
        'pm_lineage': {'workspace_id': target['workspace_id']},
        'controller_authority': controller_authority.binding(ops.db, principal,
            pm_workspace_id=target['workspace_id'])}


def _authorize_existing(ops, principal, op, control):
    _authority(ops, principal, op['target'])
    if op['actor'] != principal.actor:
        raise OperationError('FORBIDDEN', 'Use the original deferral actor', 403)


async def _run(ctx):
    ops = ctx.service
    principal = pm_workspace.admitted_principal(ctx)

    def cancel_original():
        ctx.require_controller()
        original = _queued(ops, principal, ctx.target)
        ops.cancel(principal, original['operation_id'])
        return {'source_operation_id': original['operation_id'], 'cancel_requested': True}

    ctx.effect('work_input.cancel', cancel_original)
    original, receipt = _authority(ops, principal, ctx.target)
    if receipt['state'] == 'queued' and original['cancel_requested']:
        raise Wait('waiting_checks', 'Waiting for the original unsent input cancellation', delay_s=1)
    if receipt['state'] != 'cancelled':
        raise _changed()
    follow.check_cancel_allowed(ops, principal, original)

    def save_idea():
        ctx.require_controller()
        current, proof = _authority(ops, principal, ctx.target)
        if proof['state'] != 'cancelled':
            raise _changed()
        follow.check_cancel_allowed(ops, principal, current)
        contexts = pm_thread_contexts.admission(ops, principal, ctx.target['workspace_id'], [
            {'kind': 'work_item', 'id': ctx.target['work_item_id']},
            {'kind': 'work_input', 'id': current['operation_id']}])
        result = pm_discussions.create_idea(ctx, current['params']['text'].strip()[:160],
            initial_message=ctx.params['reason'], contexts=contexts)
        result.update(workspace_id=ctx.target['workspace_id'], work_item_id=ctx.target['work_item_id'],
            source_operation_id=current['operation_id'], execution_authorized=False)
        ops.db.execute('INSERT INTO pm_discussion_applied VALUES(?,?,?)',
            (ctx.operation_id, json.dumps(result, ensure_ascii=False), time.time()))
        ops.journal.api_event('pm_thread', result['thread_id'], ACTION,
            {**result, 'operation_id': ctx.operation_id}, actor=ctx.actor)
        return result

    try:
        return ctx.effect('work_input.idea', save_idea)
    except (OSError, sqlite3.Error) as exc:
        raise NeedsAttention('PM_WORK_IDEA_SAVE_FAILED',
            'The original input is cancelled. Resume this request to save its Idea / 原指令已取消；繼續此請求以補存 Idea') from exc
