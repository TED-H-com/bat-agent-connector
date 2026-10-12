"""Bounded boolean reuse inside one synchronous original-source read.

This extracts the conversation reader's existing recursion guard. It is not an
authority cache: no state survives the read, and live/current/effect checks are
outside this interface. Predicates must be synchronous, read-only functions.
"""
from __future__ import annotations

import asyncio
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from contextvars import ContextVar

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


@contextmanager
def scope(db, principal):
    """Join only a live same-caller synchronous scope; otherwise open a new one.

Callers perform source reads only. No await, transaction boundary or
write belongs inside this scope. A loop turn, mutation, closed transaction or
copied asynchronous context invalidates reuse rather than granting access.
"""
    parent, identity = _READ.get(), _identity(db, principal)
    if parent is not None and parent['open']:
        if not _valid(parent):
            raise _changed()
        if parent['identity'] == identity:
            yield parent
            if not _valid(parent):
                raise _changed()
            return
        if parent['scope_depth'] >= MAX_DEPTH:
            raise _changed()
    owned = not db.in_transaction
    if owned:
        db.execute('BEGIN')
    anchor = 'pm_source_read_' + uuid.uuid4().hex
    db.execute('SAVEPOINT ' + anchor)
    state = {'db': db, 'identity': identity, 'owner': _owner(), 'open': True,
        'yielded': False, 'changes': db.total_changes, 'active': set(), 'memo': {},
        'scope_depth': parent['scope_depth'] + 1 if parent and parent['open'] else 1,
        'budget': parent['budget'] if parent and parent['open'] else {'nodes': 0}}
    token, handle = _READ.set(state), None
    try:
        # Establish the WAL read point before any nested source predicate.
        db.execute('SELECT name FROM sqlite_schema LIMIT 1').fetchone()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            # Any event-loop turn invalidates an accidentally retained context,
            # including same-task await (task identity alone cannot detect it).
            handle = loop.call_soon(state.__setitem__, 'yielded', True)
        yield state
        if not _valid(state):
            raise _changed()
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
        original_transaction = anchor is None
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
        state['open'] = False
        state['memo'].clear()
        state['active'].clear()
        if handle is not None:
            handle.cancel()
        _READ.reset(token)


def check(db, principal, kind, identity, predicate):
    """Reuse only completed booleans; never share mutable source documents."""
    with scope(db, principal) as state:
        key = kind, identity
        if key in state['memo']:
            return state['memo'][key]
        # Typed source wrappers must not reduce the previously supported
        # original-root or conversation depth. All share the same node budget.
        def group(value):
            return ('conversation' if value.startswith('conversation.') else value
                    if value in {'execution_verifier', 'quality_sources', 'workload_sources', 'repair_sources'} else 'original')
        depth = sum(group(active_kind) == group(kind) for active_kind, _ in state['active'])
        if key in state['active'] or depth >= MAX_DEPTH or state['budget']['nodes'] >= MAX_NODES:
            return False
        state['budget']['nodes'] += 1
        state['active'].add(key)
        try:
            result = bool(predicate())
            if not _valid(state):
                raise _changed()
            state['memo'][key] = result
            return result
        finally:
            state['active'].remove(key)
