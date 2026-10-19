"""Stable public API for external housekeeping automation.

Exported names and signatures change only with a CHANGELOG entry and a
deprecation period. API_VERSION identifies this facade's contract. Re-exported
operations retain their existing semantics and safety gates; this module grants
no additional write or orchestration authority.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from .config import Config as _Config
from .config import load_config
from .errors import BatError
from .fleet import Fleet as _Fleet
from .jev import Jev as _Jev
from .lifecycle import session_cleanup, session_failover, session_set_permissions
from .registry import list_entries
from .relay import parse_status, status_footer
from .service import session_answer, session_read, session_send, sessions_list
from .triage import QUOTA_PATTERNS, sessions_triage

API_VERSION = 1

__all__ = [
    "API_VERSION",
    "BatError",
    "QUOTA_PATTERNS",
    "jev_for",
    "list_entries",
    "load_config",
    "open_fleet",
    "parse_status",
    "session_answer",
    "session_cleanup",
    "session_failover",
    "session_read",
    "session_send",
    "session_set_permissions",
    "sessions_list",
    "sessions_triage",
    "status_footer",
]


@asynccontextmanager
async def open_fleet(
    config: _Config | None = None,
    *,
    actor: str = "automation",
    read_only: bool = False,
    idle_timeout: float = 120.0,
) -> AsyncIterator[_Fleet]:
    """Yield a Fleet using the supplied or default config, then always close it."""
    fleet = _Fleet(
        load_config() if config is None else config,
        actor=actor,
        read_only=read_only,
        idle_timeout=idle_timeout,
    )
    try:
        yield fleet
    finally:
        await fleet.close()


def jev_for(fleet: _Fleet) -> _Jev | None:
    """Return the configured Jev when enabled and available, otherwise None.

    The returned object's async ``ask(state, questions)`` retains Jev's existing
    optional-result semantics: unavailable or invalid judgments return None.
    """
    jev = _Jev(fleet.config.jev)
    return jev if jev.enabled else None
