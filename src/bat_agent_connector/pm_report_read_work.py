"""Report-closure accounting for synchronous, cursor-aware collection reads.

This is work accounting only. It never decides visibility, interrupts a proof,
or stores a permission result. Nested lists finish inside their enclosing atomic
record; only a subsequent record boundary may return a shorter page.
"""
from __future__ import annotations

import asyncio
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

MAX_OBSERVATIONS = 20
MAX_RECORDS = 2000
MAX_BYTES = 16_000_000
_CURRENT = ContextVar('pm_report_read_work', default=None)


def _owner():
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return threading.get_ident(), task


@dataclass
class Work:
    db: object
    owner: object = field(default_factory=_owner)
    observations: set = field(default_factory=set)
    records: int = 0
    bytes: int = 0
    active_records: int = 0
    open: bool = True

    def usable(self, db):
        return self.open and self.db is db and self.owner == _owner()

    @property
    def stopped(self):
        return not self.active_records and (len(self.observations) >= MAX_OBSERVATIONS
            or self.records >= MAX_RECORDS or self.bytes >= MAX_BYTES)

    @contextmanager
    def record(self):
        self.active_records += 1
        try:
            yield
        finally:
            self.active_records -= 1


@contextmanager
def scope(db):
    parent = _CURRENT.get()
    if parent is not None and parent.usable(db):
        yield parent
        return
    work = Work(db)
    token = _CURRENT.set(work)
    try:
        yield work
    finally:
        work.open = False
        _CURRENT.reset(token)


def observation(db, principal, workspace_id, identity):
    work = _CURRENT.get()
    if work is not None and work.usable(db):
        work.observations.add((principal.actor, principal.scopes, principal.admin,
            principal.credential_id, workspace_id, identity))


def charge(db, records, byte_length):
    work = _CURRENT.get()
    if work is not None and work.usable(db):
        work.records += records
        work.bytes += byte_length
