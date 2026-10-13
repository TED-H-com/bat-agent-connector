"""ISO-8601 parsing that does not change with the Python version.

``datetime.fromisoformat`` grew new forms in 3.11 (week dates, basic
``YYYYMMDDThhmmss``, compact offsets, comma fractions, other fractional
lengths) and, in 3.14, accepts ``T24:00:00`` as the next midnight. Callers
that validate input use this module. It allows the forms Python 3.10 accepts,
the inverse of ``datetime.isoformat``, and only then calls ``fromisoformat``.
An hour of 24 never reaches that call.

Strings this process wrote with ``isoformat`` or ``time.strftime`` stay on
``fromisoformat`` directly. Those strings are outside the version-dependent set.
"""
from __future__ import annotations

import re
from datetime import date, datetime

# HH[:MM[:SS[.fff|.ffffff]]] — fractional seconds are exactly 3 or 6 digits.
_CLOCK = r"(?:[01][0-9]|2[0-3])(?::[0-5][0-9](?::[0-5][0-9](?:\.(?:[0-9]{3}|[0-9]{6}))?)?)?"
# ±HH:MM[:SS[.ffffff]] — offset fractions are exactly 6 digits. Not ±HH or ±HHMM.
_ZONE = r"[+-](?:[01][0-9]|2[0-3]):[0-5][0-9](?::[0-5][0-9](?:\.[0-9]{6})?)?"
# The separator is the single character Python 3.10 skips. Week dates and basic
# dates do not match the dashed calendar.
_DATETIME = re.compile(rf"[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}(?:.(?:{_CLOCK})(?:{_ZONE})?)?")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def parse_datetime(value: str) -> datetime:
    """Parse one extended ISO datetime, or raise ValueError.

    A ``Z`` suffix is UTC. Accepted strings produce the same datetime on every
    supported Python; hour 24, week dates and basic formats are rejected.
    """
    if not isinstance(value, str):
        raise ValueError
    normalized = value.replace("Z", "+00:00")
    if _DATETIME.fullmatch(normalized) is None:
        raise ValueError
    return datetime.fromisoformat(normalized)


def parse_date(value: str) -> date:
    """Parse one ``YYYY-MM-DD`` calendar date, or raise ValueError.

    Week dates and basic ``YYYYMMDD`` values are rejected, so a date-only field
    cannot be stored as another calendar form on Python 3.11+.
    """
    if not isinstance(value, str) or _DATE.fullmatch(value) is None:
        raise ValueError
    return date.fromisoformat(value)
