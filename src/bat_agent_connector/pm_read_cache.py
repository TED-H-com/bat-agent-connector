"""Memoization limited to one owner's immutable database read snapshot."""
import asyncio
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from functools import wraps
from inspect import signature

from .api_auth import Principal

_CURRENT = ContextVar('pm_read_cache', default=None)
# Depth of active traversal guards. Inherited contexts still cannot use the
# owner's memo; guarded answers stay private to their exact traversal depth.
_GUARDS = ContextVar('pm_read_cache_guards', default=0)


def _task():
    try:
        return asyncio.current_task()
    except RuntimeError:  # Synchronous reads need no running event loop.
        return None


@dataclass
class _Scope:
    db: object
    thread: object = field(default_factory=threading.current_thread)
    task: object = field(default_factory=_task)
    values: dict = field(default_factory=dict)
    active: bool = True
    disabled: bool = False
    guard_depth: int = 0

    def __post_init__(self):
        self.changes = 0 if self.disabled else self.db.total_changes
        self.disabled = self.disabled or not self.db.in_transaction

    def usable(self):
        # ContextVars are inherited by asyncio tasks and copy_context(). Neither
        # may consult the owner's connection or use its memo, even while live.
        if not self.active or self.thread is not threading.current_thread() or self.task is not _task():
            return False
        if not self.disabled and (not self.db.in_transaction or self.changes != self.db.total_changes):
            self.values.clear()
            self.disabled = True
        return not self.disabled


@dataclass(frozen=True)
class _Frame:
    scope: _Scope
    principal: object
    parent: object = None


def _key(value):
    # Principal equality/repr intentionally omit credential_id; neither is a
    # sufficient identity for a source visibility decision.
    if isinstance(value, Principal):
        return (Principal, value.actor, value.scopes, value.admin, value.credential_id)
    if isinstance(value, dict):
        return (dict, tuple((_key(k), _key(v)) for k, v in value.items()))
    if isinstance(value, (list, tuple)):
        return (type(value), tuple(_key(item) for item in value))
    if isinstance(value, (set, frozenset)):
        return (type(value), frozenset(_key(item) for item in value))
    hash(value)
    return (type(value), value)


@contextmanager
def scope(db, principal=None):
    """Join this database's scope; callers hold the snapshot for its lifetime.

    A nested scope can select another principal without changing the owner's
    memo. A scope entered without a transaction stays disabled throughout.
    """
    parent = frame = _CURRENT.get()
    while frame is not None and frame.scope.db is not db:
        frame = frame.parent
    opened = frame is None
    identity = frame.principal if principal is None and not opened else _key(principal)
    current = _Scope(db) if opened else frame.scope
    token = _CURRENT.set(_Frame(current, identity, parent))
    try:
        yield
    finally:
        if opened:
            current.active = False
            current.values.clear()
        _CURRENT.reset(token)


@contextmanager
def guarded():
    """Mark a traversal guard (re-entry suppression, cycle or budget refusal).

    Answers computed inside a guard can assume that an in-progress back edge is
    visible, or refuse because a traversal budget ran out. Neither is the
    request-wide answer, so guarded loads cannot populate the request memo.
    Entries stored outside every guard may still be served. An isolated
    guarded_scope may store answers relative to its own unchanged guard only.
    """
    token = _GUARDS.set(_GUARDS.get() + 1)
    try:
        yield
    finally:
        _GUARDS.reset(token)


@contextmanager
def guarded_scope(db, principal):
    """Fresh snapshot memo private to one active traversal guard.

    Back-edge assumptions stay fixed for this guard's lifetime. Never join an
    outer memo or export these conditional answers to it. Nested guards cannot
    reuse or store answers in this scope; copied tasks/threads and writes retain
    the usual owner and snapshot barriers.
    """
    depth = _GUARDS.get()
    if depth == 0:
        raise RuntimeError('A guarded memo requires an active traversal guard')
    parent = frame = _CURRENT.get()
    while frame is not None and frame.scope.db is not db:
        frame = frame.parent
    disabled = frame is not None and not frame.scope.usable()
    current = _Scope(db, guard_depth=depth, disabled=disabled)
    token = _CURRENT.set(_Frame(current, _key(principal), parent))
    try:
        yield
    finally:
        current.active = False
        current.values.clear()
        _CURRENT.reset(token)


def remember(kind, key, load):
    """Remember a namespaced value; include explicit visibility inputs in key.

    The selected scope principal is also part of every key. Unsupported key
    types bypass caching, preserving the underlying reader's validation.
    """
    frame = _CURRENT.get()
    if frame is None or not frame.scope.usable():
        return load()
    current = frame.scope
    if current.guard_depth and _GUARDS.get() != current.guard_depth:
        return load()  # Conditional answers require this exact traversal guard.
    try:
        identity = (kind, frame.principal, _key(key))
    except TypeError:
        return load()
    if identity not in current.values:
        if _GUARDS.get() > current.guard_depth:
            return load()
        from .operations import OperationError
        try:
            value = load()
        except OperationError as exc:
            if not current.usable():
                raise
            current.values[identity] = (False, (exc.code, exc.message, exc.status))
        else:
            # Loading can itself write. Do not repopulate an invalidated memo.
            if not current.usable():
                return value
            current.values[identity] = (True, deepcopy(value))
    available, value = current.values[identity]
    if not available:
        from .operations import OperationError
        raise OperationError(*value)
    return deepcopy(value)


def memoized(fn):
    first_name = next(iter(signature(fn).parameters))

    @wraps(fn)
    def read(*args, **kwargs):
        first = args[0] if args else kwargs.get(first_name)
        current = _CURRENT.get()
        db = getattr(first, 'db', first)
        if current is None or current.scope.db is not db:
            return fn(*args, **kwargs)
        key = (args[1:], sorted(kwargs.items())) if args else (
            (), sorted((name, value) for name, value in kwargs.items() if name != first_name))
        return remember(fn, key, lambda: fn(*args, **kwargs))
    return read
