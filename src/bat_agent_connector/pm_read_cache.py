"""Memoization limited to one owner's immutable database read snapshot."""
import asyncio
import json
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from functools import wraps
from inspect import signature

from .api_auth import Principal

_CURRENT = ContextVar('pm_read_cache', default=None)
# Depth of active traversal guards. A context copied into another thread or
# task keeps the depth, which can only prevent storing, never add an entry.
_GUARDS = ContextVar('pm_read_cache_guards', default=0)
_GUARD_TOKEN = ContextVar('pm_read_cache_guard_token', default=None)
_REFUSALS = ContextVar('pm_read_cache_refusals', default=0)


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


@dataclass(frozen=True)
class _Frame:
    scope: _Scope
    principal: object
    parent: object = None


@dataclass(frozen=True)
class _SavedValue:
    value: object
    thaw: object


def json_snapshot(value):
    """Freeze readers whose public values already consist entirely of JSON."""
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


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
    request-wide answer, so nothing loaded while any guard is active is stored.
    Entries stored outside every guard may still be served.
    """
    token = _GUARDS.set(_GUARDS.get() + 1)
    identity = _GUARD_TOKEN.set(object())
    try:
        yield
    finally:
        _GUARD_TOKEN.reset(identity)
        _GUARDS.reset(token)


def is_guarded():
    """Whether new answers would depend on an active traversal guard."""
    return _GUARDS.get() > 0


def guard_identity():
    """Identify one live traversal; never promote its partial grants."""
    return _GUARD_TOKEN.get()


def refusal_generation():
    """Identify loads affected by a traversal-local cycle or budget refusal."""
    return _REFUSALS.get()


def refuse():
    """Keep every enclosing load from storing a traversal-dependent answer."""
    _REFUSALS.set(_REFUSALS.get() + 1)


def _entry(entry):
    available, value = entry
    if not available:
        from .operations import OperationError
        raise OperationError(*value)
    return value.thaw(value.value) if isinstance(value, _SavedValue) else deepcopy(value)


def recall(kind, key):
    """Return (found, value) without adding loader frames to a bounded walk."""
    frame = _CURRENT.get()
    if frame is None or not frame.scope.usable():
        return False, None
    try:
        identity = (kind, frame.principal, _key(key))
    except TypeError:
        return False, None
    entry = frame.scope.values.get(identity)
    return (True, _entry(entry)) if entry is not None else (False, None)


def remember(kind, key, load, *, cache_errors=True, cache_if=None, guard_independent=False,
             freeze=deepcopy, thaw=deepcopy):
    """Remember a namespaced value; include explicit visibility inputs in key.

    The selected scope principal is also part of every key. Unsupported key
    types bypass caching, preserving the underlying reader's validation.
    Readers with traversal-local refusals can retain only completed positive
    answers with cache_if=bool and leave exceptions uncached.
    guard_independent is only for raw snapshot metadata, never visibility,
    sanitized projections or answers depending on a traversal guard.
    freeze/thaw may optimize copies of known value types; each returned value
    must remain isolated from the loader, other callers and the saved envelope.
    """
    frame = _CURRENT.get()
    if frame is None or not frame.scope.usable():
        return load()
    current = frame.scope
    try:
        identity = (kind, frame.principal, _key(key))
    except TypeError:
        return load()
    if identity not in current.values:
        if _GUARDS.get() > 0 and not guard_independent:
            return load()
        from .operations import OperationError
        generation = refusal_generation()
        try:
            value = load()
        except OperationError as exc:
            if (not cache_errors or not current.usable()
                    or not guard_independent and generation != refusal_generation()):
                raise
            current.values[identity] = (False, (exc.code, exc.message, exc.status))
        else:
            # Loading can itself write. Do not repopulate an invalidated memo.
            if (not current.usable() or cache_if is not None and not cache_if(value)
                    or not guard_independent and generation != refusal_generation()):
                return value
            saved = freeze(value)
            if not current.usable():
                return value
            current.values[identity] = (True, _SavedValue(saved, thaw))
    return _entry(current.values[identity])


def memoized(fn=None, *, guard_independent=False, freeze=deepcopy, thaw=deepcopy):
    if fn is None:
        return lambda reader: memoized(reader, guard_independent=guard_independent, freeze=freeze, thaw=thaw)
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
        return remember(fn, key, lambda: fn(*args, **kwargs), guard_independent=guard_independent,
                        freeze=freeze, thaw=thaw)
    return read


def _copy_columns(row):
    # Journal uses native SQLite scalar columns (including encoded JSON text),
    # so a new envelope suffices: no column contains a mutable source document.
    return row.copy() if row is not None else None


@memoized(guard_independent=True, freeze=_copy_columns, thaw=_copy_columns)
def operation_row(db, operation_id):
    """Raw canonical columns only; callers still decode and authorize each read.

    SQL strings are immutable and independent of visibility/backedge guards.
    Copies protect the saved envelope, and any write disables their reuse.
    """
    row = db.execute('SELECT * FROM operations WHERE operation_id=?', (operation_id,)).fetchone()
    return dict(row) if row is not None else None
