"""Bounded boolean reuse inside one synchronous original-source read.

This extracts the conversation reader's existing recursion guard. Completed
positives may join one immutable request/step snapshot. The GitHub observer may
carry completed historical booleans to another fresh snapshot only with its
operation-local SQLite version proof. No transaction or partial guard crosses
an await. Live/current/effect checks remain outside this interface. Predicates
must be synchronous, read-only functions.
"""
from __future__ import annotations

import asyncio
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from contextvars import ContextVar

from . import pm_read_cache, pm_source_history
from .operations import OperationError

MAX_DEPTH = 64
MAX_NODES = 2048
_READ = ContextVar('pm_original_source_read', default=None)


def _owner():
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return threading.get_ident(), task


def _identity(db, principal):
    # Principal equality intentionally ignores credential_id; this scope cannot.
    return db, principal.actor, principal.scopes, principal.admin, principal.credential_id


def _valid(state):
    return (state['open'] and not state['yielded'] and state['owner'] == _owner()
        and state['db'].in_transaction and state['db'].total_changes == state['changes'])


def _changed():
    return OperationError('PM_SOURCE_READ_CHANGED', 'Original source read must restart in a fresh snapshot', 409)


def _parent_scope(db, principal):
    parent, identity = _READ.get(), _identity(db, principal)
    if parent is not None and parent['open']:
        if not _valid(parent):
            raise _changed()
        if parent['identity'] != identity and parent['scope_depth'] >= MAX_DEPTH:
            raise _changed()
    return parent, identity


def validate_scope(db, principal):
    """Reject invalid borrowed source state before serving a completed read."""
    _parent_scope(db, principal)


@contextmanager
def scope(db, principal):
    """Join only a live same-caller synchronous scope; otherwise open a new one.

Callers perform source reads only. No await, transaction boundary or
write belongs inside this scope. A loop turn, mutation, closed transaction or
copied asynchronous context invalidates reuse rather than granting access.
"""
    parent, identity = _parent_scope(db, principal)
    if parent is not None and parent['open']:
        if parent['identity'] == identity:
            yield parent
            if not _valid(parent):
                raise _changed()
            return
    owned = not db.in_transaction
    # Only a fresh transaction can establish a versioned cross-frame proof.
    # A caller's older transaction remains on the original cold-read path.
    history = (pm_source_history.before(db, (MAX_DEPTH, MAX_NODES)) if owned else
        parent.get('history') if parent and parent['db'] is db else
        pm_source_history.transaction_snapshot(db, (MAX_DEPTH, MAX_NODES)))
    anchor = 'pm_source_read_' + uuid.uuid4().hex
    state = token = handle = None
    try:
        # Everything after our BEGIN, including partial scope entry, must
        # unwind here before the observer tears down its TEMP trackers.
        if owned:
            db.execute('BEGIN')
        db.execute('SAVEPOINT ' + anchor)
        state = {'db': db, 'identity': identity, 'owner': _owner(), 'open': True,
            'yielded': False, 'changes': db.total_changes, 'active': set(), 'memo': {}, 'visibility': {}, 'history': history,
            'remembering': False, 'local_only': set(), 'refused': set(), 'guarded_visibility': {},
            'guard_contexts': {}, 'partial_sources': {}, 'repair_walks': set(), 'repair_completed': set(), 'repair_generation': None,
            'scope_depth': parent['scope_depth'] + 1 if parent and parent['open'] else 1,
            'budget': parent['budget'] if parent and parent['open'] else {'nodes': 0}}
        token = _READ.set(state)
        # Establish the WAL read point before any nested source predicate.
        db.execute('SELECT name FROM sqlite_schema LIMIT 1').fetchone()
        state['memo'].update(pm_source_history.seed(history, principal))
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            # Any event-loop turn invalidates an accidentally retained context,
            # including same-task await (task identity alone cannot detect it).
            handle = loop.call_soon(state.__setitem__, 'yielded', True)
        with pm_read_cache.scope(db, principal):
            yield state
            if not _valid(state):
                raise _changed()
            if not pm_read_cache.is_guarded():
                pm_source_history.save(history, principal, state, MAX_NODES)
        try:
            db.execute('RELEASE ' + anchor)
        except sqlite3.OperationalError:
            # COMMIT/ROLLBACK followed by another BEGIN cannot masquerade as
            # the same snapshot merely because in_transaction is true again.
            raise _changed() from None
        anchor = None
        if owned:
            db.execute('COMMIT')
    except BaseException:
        pm_source_history.reject(history)
        # Before entry completes there is no source body that could replace
        # the transaction. A failed SAVEPOINT must still roll back our BEGIN.
        original_transaction = token is None or anchor is None
        if anchor is not None:
            try:
                db.execute('RELEASE ' + anchor)
                original_transaction = True
            except sqlite3.OperationalError:
                pass
        if owned and original_transaction and db.in_transaction:
            db.execute('ROLLBACK')
        raise
    finally:
        if state is not None:
            state['open'] = False
            state['memo'].clear()
            state['visibility'].clear()
            state['guarded_visibility'].clear()
            state['local_only'].clear()
            state['refused'].clear()
            state['guard_contexts'].clear()
            state['partial_sources'].clear()
            state['repair_walks'].clear()
            state['repair_completed'].clear()
            state['active'].clear()
        if handle is not None:
            handle.cancel()
        if token is not None:
            _READ.reset(token)


def _guard_context(db, principal):
    from .pm_maintenance_repairs import source_read_context
    return pm_read_cache.guard_identity(), source_read_context(db, principal)


def complete_repair_sources(db, principal, state):
    """Release booleans only after the entire entered repair DAG is proved."""
    if (not _valid(state) or pm_read_cache.is_guarded() or _guard_context(db, principal)[1]
            or not state['repair_walks'] or state['repair_walks'] != state['repair_completed']
            or state['repair_generation'] != pm_read_cache.refusal_generation()):
        return
    for key, (_, repairs) in tuple(state['guard_contexts'].items()):
        if (repairs and repairs <= state['repair_completed'] and key not in state['refused']
                and state['memo'].get(key) is True):
            state['local_only'].discard(key)
            del state['guard_contexts'][key]
            kind, identity = key
            pm_read_cache.remember(check, (principal, kind, identity), lambda: True,
                                   cache_errors=False, cache_if=bool)
    for (_, repairs, key), value in state['guarded_visibility'].items():
        if repairs <= state['repair_completed'] and len(state['visibility']) < MAX_NODES:
            state['visibility'][key] = value


def check(db, principal, kind, identity, predicate):
    """Reuse only completed booleans; never share mutable source documents."""
    with scope(db, principal) as state:
        key = kind, identity
        context = _guard_context(db, principal) if key in state['guard_contexts'] else None
        if (key in state['guard_contexts'] and key not in state['refused']
                and state['guard_contexts'][key] != context):
            # A partial grant from another traversal is not a completed answer.
            previous = state['guard_contexts'][key], key
            if state['memo'].get(key) is True and len(state['partial_sources']) < MAX_NODES:
                state['partial_sources'][previous] = True
            state['memo'].pop(key, None)
            state['local_only'].discard(key)
            del state['guard_contexts'][key]
            # A nested guard must not evict its caller's completed partial
            # proof. Reuse only on returning to that exact live guard/repair
            # context. These bounded entries never enter the ordinary memo.
            if state['partial_sources'].get((context, key)):
                state['memo'][key] = True
                state['local_only'].add(key)
                state['guard_contexts'][key] = context
        if key in state['memo']:
            if key in state['refused'] or key in state['local_only'] and not pm_read_cache.is_guarded():
                pm_read_cache.refuse()
            return state['memo'][key]
        found, result = pm_read_cache.recall(check, (principal, kind, identity))
        if found and result:
            state['memo'][key] = True
            return True
        # Typed source wrappers must not reduce the previously supported
        # original-root or conversation depth. All share the same node budget.
        def group(value):
            return ('conversation' if value.startswith('conversation.') else value
                    if value in {'execution_verifier', 'quality_sources', 'workload_sources', 'repair_sources',
                                 'work_batch_sources', 'execution_runtime_sources', 'baseline_sources',
                                 'package_sources', 'requirement_sources'} else 'original')
        depth = sum(group(active_kind) == group(kind) for active_kind, _ in state['active'])
        if key in state['active'] or depth >= MAX_DEPTH or state['budget']['nodes'] >= MAX_NODES:
            pm_read_cache.refuse()
            return False
        if not state['active'] and not state['remembering']:
            def load():
                state['remembering'] = True
                try:
                    return check(db, principal, kind, identity, predicate)
                finally:
                    state['remembering'] = False
            result = pm_read_cache.remember(check, (principal, kind, identity), load,
                                           cache_errors=False, cache_if=bool)
            state['memo'][key] = result
            return result
        # Keep the nested traversal flat: an extra loader frame at every node
        # would shorten the supported independent 64-node depth allowances.
        state['budget']['nodes'] += 1
        state['active'].add(key)
        generation = pm_read_cache.refusal_generation()
        guarded = pm_read_cache.is_guarded()
        context = _guard_context(db, principal) if guarded else None
        try:
            result = bool(predicate())
            if not _valid(state):
                raise _changed()
            state['memo'][key] = result
            if guarded or generation != pm_read_cache.refusal_generation():
                state['local_only'].add(key)
            if context is not None:
                state['guard_contexts'][key] = context
            if generation != pm_read_cache.refusal_generation():
                state['refused'].add(key)
            # Publish completed nested positives after their stack unwinds.
            # A cycle/budget refusal taints every enclosing load. Active domain
            # guards still exclude partial repair/conversation/spec answers.
            if (result and not pm_read_cache.is_guarded()
                    and generation == pm_read_cache.refusal_generation()):
                pm_read_cache.remember(check, (principal, kind, identity), lambda: result,
                                       cache_errors=False, cache_if=bool)
            return result
        finally:
            state['active'].remove(key)
