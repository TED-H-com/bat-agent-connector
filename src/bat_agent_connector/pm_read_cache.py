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
_PRINCIPAL = ContextVar('pm_read_principal', default=None)


def _task():
    try:
        return asyncio.current_task()
    except RuntimeError:  # Synchronous reads need no running event loop.
        return None


@dataclass
class _Scope:
    db: object
    parent: object = None
    thread: object = field(default_factory=threading.current_thread)
    task: object = field(default_factory=_task)
    values: dict = field(default_factory=dict)
    active: bool = True
    disabled: bool = False

    def __post_init__(self):
        self.changes = self.db.total_changes
        self.disabled = not self.db.in_transaction

    def usable(self):
        # ContextVars are inherited by asyncio tasks and copy_context(). Neither
        # may consult the owner's connection or use its memo, even while live.
        if not self.active or self.thread is not threading.current_thread() or self.task is not _task():
            return False
        if not self.disabled and (not self.db.in_transaction or self.changes != self.db.total_changes):
            self.values.clear()
            self.disabled = True
        return not self.disabled


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
    parent = current = _CURRENT.get()
    while current is not None and current.db is not db:
        current = current.parent
    opened = current is None
    identity = _PRINCIPAL.get() if principal is None and not opened else _key(principal)
    current = _Scope(db, parent=parent) if opened else current
    token = _CURRENT.set(current)
    principal_token = _PRINCIPAL.set(identity)
    try:
        yield
    finally:
        _PRINCIPAL.reset(principal_token)
        if opened:
            current.active = False
            current.values.clear()
        _CURRENT.reset(token)


def remember(kind, key, load):
    """Remember a namespaced value; include explicit visibility inputs in key.

    The selected scope principal is also part of every key. Unsupported key
    types bypass caching, preserving the underlying reader's validation.
    """
    current = _CURRENT.get()
    if current is None or not current.usable():
        return load()
    try:
        identity = (kind, _PRINCIPAL.get(), _key(key))
    except TypeError:
        return load()
    if identity not in current.values:
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
        if current is None or current.db is not db:
            return fn(*args, **kwargs)
        key = (args[1:], sorted(kwargs.items())) if args else (
            (), sorted((name, value) for name, value in kwargs.items() if name != first_name))
        return remember(fn, key, lambda: fn(*args, **kwargs))
    return read
