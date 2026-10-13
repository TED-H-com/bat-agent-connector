"""Memoization limited to one synchronous, immutable database read snapshot."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from functools import wraps

_CURRENT = ContextVar('pm_read_cache', default=None)


@contextmanager
def scope(db):
    # Never retain source access or content across requests / transaction changes.
    token = _CURRENT.set((db, {}))
    try:
        yield
    finally:
        _CURRENT.reset(token)


def memoized(fn):
    @wraps(fn)
    def read(first, *args, **kwargs):
        current = _CURRENT.get()
        db = getattr(first, 'db', first)
        if current is None or current[0] is not db:
            return fn(first, *args, **kwargs)
        # Refs are small ordered dictionaries; principal and workspace remain in
        # the key. Copies prevent a caller's projection annotations mutating data.
        key = (fn, repr(args), repr(sorted(kwargs.items())))
        cache = current[1]
        if key not in cache:
            from .operations import OperationError
            try:
                cache[key] = (True, fn(first, *args, **kwargs))
            except OperationError as exc:
                cache[key] = (False, (exc.code, exc.message, exc.status))
        available, value = cache[key]
        if not available:
            from .operations import OperationError
            raise OperationError(*value)
        return deepcopy(value)
    return read
