"""Fixed retained history references and connection-local current reader grants."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager

from .operations import OperationError
from .pm_workspace import require_member, server_id

SCHEMA = 'external_history_ref/v1'
MAX_CODEPOINTS = 16000
MAX_PREVIEW_BYTES = 128 * 1024


def unavailable():
    return OperationError('PM_HISTORY_SOURCE_UNAVAILABLE', 'History source is unavailable', 404)


def invalid():
    return OperationError('PM_HISTORY_REFERENCE_INVALID', 'Select one bounded retained entry or text range', 422)


def changed():
    return OperationError('PM_HISTORY_REFERENCE_CHANGED', 'The fixed history reference differs', 409)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(',', ':'))


def _digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def grant_schema(db):
    db.execute('''CREATE TEMP TABLE IF NOT EXISTS session_history_grant_state (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1), generation TEXT NOT NULL)''')
    db.execute('''CREATE TEMP TABLE IF NOT EXISTS session_history_current_grants (
        source_id TEXT NOT NULL, workspace_id TEXT NOT NULL, binding TEXT NOT NULL,
        actor TEXT NOT NULL, adapter TEXT NOT NULL, interval_s REAL NOT NULL,
        position INTEGER NOT NULL, generation TEXT NOT NULL, PRIMARY KEY(source_id,actor))''')


def _generation(db):
    # Explicit TEMP names prevent a durable table from impersonating activation.
    try:
        if db.execute("SELECT COUNT(*) FROM sqlite_temp_master WHERE type='table' AND name IN "
                "('session_history_grant_state','session_history_current_grants')").fetchone()[0] != 2:
            raise unavailable()
        row = db.execute('SELECT generation FROM temp.session_history_grant_state '
            'WHERE singleton=1 AND generation=history_active_generation()').fetchone()
    except sqlite3.OperationalError:
        raise unavailable() from None
    if row is None:
        raise unavailable()
    return row[0]


@contextmanager
def current_grants(db, principal, workspace_id):
    """No positive cache: recheck activation and membership before releasing a read."""
    generation = _generation(db)
    if not principal.allows('observe'):
        raise OperationError('FORBIDDEN', 'History reads require observe', 403)
    require_member(db, principal, workspace_id)
    rows = db.execute('SELECT * FROM temp.session_history_current_grants '
        'WHERE workspace_id=? AND actor=? AND generation=? ORDER BY position',
        (workspace_id, principal.actor, generation)).fetchall()
    yield rows
    if _generation(db) != generation:
        raise unavailable()
    require_member(db, principal, workspace_id)


def preview(db, principal, workspace_id, source_id, entry_id, expected_content_digest, selection):
    """Resolve only retained bytes; no collection, file access, write or ownership."""
    from . import session_history

    with current_grants(db, principal, workspace_id) as grants:
        source = next((s for s in grants if s['source_id'] == source_id), None)
        if source is None or not session_history._row(db, source)['readable']:
            raise unavailable()
        if (not isinstance(entry_id, str) or not re.fullmatch(r'shi_[0-9a-f]{32}', entry_id)
                or not isinstance(expected_content_digest, str) or not re.fullmatch(r'[0-9a-f]{64}', expected_content_digest)):
            raise invalid()
        saved = db.execute('SELECT content,content_digest FROM session_history_entries WHERE source_id=? AND entry_id=?',
            (source_id, entry_id)).fetchone()
        if saved is None:
            raise unavailable()
        entry = json.loads(saved['content'])
        try:
            content = _select(entry, selection)
            actual = _digest({k: v for k, v in entry.items() if k != 'source_offset'})
            if expected_content_digest != saved['content_digest'] or actual != saved['content_digest']:
                raise changed()
            ref = {'schema': SCHEMA, 'server_id': server_id(db), 'workspace_id': workspace_id,
                'source_id': source_id, 'entry_id': entry_id, 'source_digest': entry['source_digest'],
                'content_digest': actual, 'selection': dict(selection)}
            excerpt_digest = _digest({'ref': ref, 'content': content})
            result = {'ref': {**ref, 'excerpt_digest': excerpt_digest}, 'content': content,
                'history_complete': False, 'execution_status': 'unknown'}
            if len(json.dumps(result, ensure_ascii=True).encode('utf-8')) > MAX_PREVIEW_BYTES:
                raise invalid()
        except (UnicodeError, ValueError, TypeError, KeyError):
            raise invalid() from None
        return result


def _select(entry, selection):
    if not isinstance(selection, dict):
        raise invalid()
    parts = entry['parts']
    texts = [p['text'] for p in parts if p['kind'] == 'text']
    if sum(len(t) for t in texts) > MAX_CODEPOINTS or len(parts) > 128:
        raise invalid()
    for text in texts:
        text.encode('utf-8')  # Reject surrogates; Python slicing counts codepoints.
    if selection == {'kind': 'entry'}:
        chosen = parts
    elif (set(selection) == {'kind', 'part_index', 'start_codepoint', 'end_codepoint'}
            and selection['kind'] == 'text'):
        index, start, end = (selection[k] for k in ('part_index', 'start_codepoint', 'end_codepoint'))
        if (any(type(v) is not int for v in (index, start, end)) or not 0 <= index < len(parts)
                or parts[index]['kind'] != 'text' or not 0 <= start < end <= len(parts[index]['text'])):
            raise invalid()
        chosen = [{'kind': 'text', 'text': parts[index]['text'][start:end]}]
    else:
        raise invalid()
    return {'role': entry['role'], 'parts': chosen, 'clipped': entry['clipped'], 'omitted_parts': entry['omitted_parts']}


def resolve(db, principal, ref):
    """Verify all fields of a previously previewed reference against its original."""
    fields = {'schema', 'server_id', 'workspace_id', 'source_id', 'entry_id', 'source_digest',
        'content_digest', 'selection', 'excerpt_digest'}
    if not isinstance(ref, dict) or set(ref) != fields or ref['schema'] != SCHEMA:
        raise invalid()
    result = preview(db, principal, ref['workspace_id'], ref['source_id'], ref['entry_id'],
        ref['content_digest'], ref['selection'])
    if result['ref'] != ref:
        raise changed()
    return result
