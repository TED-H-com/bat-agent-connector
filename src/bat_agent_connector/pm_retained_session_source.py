"""Exact saved BAT conversation sources for fixed PM model context.

Reads release no BAT frames and grant no execution authority. The connection-local
binding function checks current configuration; durable discovery rows alone cannot
keep a removed or retargeted host readable.
"""
from __future__ import annotations

import json
import re
import sqlite3

from . import pm_read_cache
from .operations import OperationError
from .pm_source_lineage import require_session
from .pm_workspace import server_id
from .session_archive import MAX_MESSAGES, digest

MAX_SELECTED_MESSAGES = 20
MAX_SELECTED_CHARS = 16000
_FIELDS = {'version', 'server_id', 'host', 'session_id', 'binding', 'snapshot_id'}
_HEX = re.compile(r'[0-9a-f]{64}')


def unavailable():
    return OperationError('PM_RETAINED_SOURCE_UNAVAILABLE', 'Saved conversation source is unavailable', 404)


def invalid():
    return OperationError('PM_RETAINED_REFERENCE_INVALID', 'Select one exact saved conversation snapshot', 422)


def validate(ref):
    if (not isinstance(ref, dict) or set(ref) != _FIELDS or type(ref['version']) is not int
            or ref['version'] != 1):
        raise invalid()
    for name in ('server_id', 'host', 'session_id'):
        value = ref[name]
        if (not isinstance(value, str) or not value or len(value) > 512
                or any(ord(c) < 32 or ord(c) == 127 for c in value)):
            raise invalid()
    if any(not isinstance(ref[name], str) or not _HEX.fullmatch(ref[name])
            for name in ('binding', 'snapshot_id')):
        raise invalid()
    return dict(ref)


def reference(db, host, session_id, binding, snapshot_id):
    return validate({'version': 1, 'server_id': server_id(db), 'host': host,
        'session_id': session_id, 'binding': binding, 'snapshot_id': snapshot_id})


def register(archive):
    def current_binding(host, session_id):
        try:
            return archive.identity(host, session_id)[0]
        except (OperationError, ValueError, TypeError, KeyError):
            return None
    archive.db.create_function('session_archive_current_binding', 2, current_binding)


def _snapshot(db, principal, ref):
    ref = validate(ref)
    if not principal.allows('observe'):
        raise OperationError('FORBIDDEN', 'Saved conversation needs observe scope', 403)
    if ref['server_id'] != server_id(db):
        raise unavailable()
    # Current host configuration is outside SQLite's version proof. Taint every
    # enclosing cached source decision so it cannot survive another transport
    # frame, including a second parent reusing this read in the same traversal.
    pm_read_cache.refuse()
    try:
        current = db.execute('SELECT session_archive_current_binding(?,?)',
            (ref['host'], ref['session_id'])).fetchone()[0]
    except sqlite3.OperationalError:
        raise unavailable() from None
    if current != ref['binding']:
        raise unavailable()
    require_session(db, principal, ref['host'], ref['session_id'])
    key = ref['host'], ref['session_id'], ref['binding']
    saved = db.execute('SELECT document,saved_at FROM session_archive_snapshots '
        'WHERE host=? AND session_id=? AND binding=? AND digest=?', (*key, ref['snapshot_id'])).fetchone()
    if saved is None:
        raise unavailable()
    try:
        document = json.loads(saved['document'])
        members = document['members']
        if (digest(document) != ref['snapshot_id'] or digest(document['identity']) != ref['binding']
                or not isinstance(document['coverage'], dict) or not isinstance(members, list)
                or len(members) > MAX_MESSAGES or not _members_valid(members)
                or len({member[0] for member in members}) != len(members)):
            raise unavailable()
    except (ValueError, TypeError, KeyError):
        raise unavailable() from None
    return ref, key, document, saved['saved_at']


def _members_valid(members):
    return all(isinstance(member, list) and len(member) == 2
        and isinstance(member[0], str) and 0 < len(member[0]) <= 512
        and isinstance(member[1], str) and _HEX.fullmatch(member[1]) for member in members)


def selection(ref, members):
    """Build an exact selection after a caller applies its final context budget."""
    ref = validate(ref)
    if (not isinstance(members, list) or len(members) > MAX_SELECTED_MESSAGES
            or not _members_valid(members) or len({member[0] for member in members}) != len(members)):
        raise invalid()
    members = [list(member) for member in members]
    return {'version': 1, 'members': members, 'digest': digest({'ref': ref, 'members': members})}


def _message(db, key, member):
    saved = db.execute('SELECT body FROM session_archive_messages WHERE host=? AND session_id=? '
        'AND binding=? AND message_id=? AND revision=?', (*key, *member)).fetchone()
    if saved is None:
        raise unavailable()
    try:
        body = json.loads(saved[0])
        if (digest(body) != member[1] or body['id'] != member[0] or body['role'] not in {'user', 'assistant'}
                or not isinstance(body['text'], str) or len(body['text']) > MAX_SELECTED_CHARS
                or type(body['clipped']) is not bool):
            raise unavailable()
    except (ValueError, TypeError, KeyError):
        raise unavailable() from None
    return {**body, 'revision': member[1]}


def resolve(db, principal, ref, *, max_messages=MAX_SELECTED_MESSAGES, max_chars=MAX_SELECTED_CHARS):
    """Select a bounded chronological suffix of exact stored message revisions."""
    if (type(max_messages) is not int or not 1 <= max_messages <= MAX_SELECTED_MESSAGES
            or type(max_chars) is not int or not 1 <= max_chars <= MAX_SELECTED_CHARS):
        raise invalid()
    ref, key, document, saved_at = _snapshot(db, principal, ref)
    messages, used = [], 0
    for member in reversed(document['members'][-max_messages:]):
        message = _message(db, key, member)
        if used + len(message['text']) > max_chars:
            break
        messages.append(message)
        used += len(message['text'])
    messages.reverse()
    chosen = selection(ref, [[m['id'], m['revision']] for m in messages])
    return {'ref': ref, 'saved_at': saved_at, 'messages': messages, 'selection': chosen,
        'selection_digest': chosen['digest'], 'execution_status': 'unknown',
        'coverage': {**document['coverage'], 'included_message_count': len(messages),
            'omitted_message_count': len(document['members']) - len(messages),
            'clipped_message_count': sum(m['clipped'] for m in messages), 'partial': True}}


def _selection_valid(ref, chosen):
    return (isinstance(chosen, dict) and set(chosen) == {'version', 'members', 'digest'}
        and type(chosen['version']) is int and chosen['version'] == 1
        and chosen == selection(ref, chosen['members']))


def visible(db, principal, ref, *, selection=None):
    """Revalidate current authority and any frozen per-message selection."""
    try:
        ref, key, document, _ = _snapshot(db, principal, ref)
        if selection is not None:
            if not _selection_valid(ref, selection):
                return False
            expected = iter(document['members'])
            for member in selection['members']:
                if not any(found == member for found in expected):
                    return False
                _message(db, key, member)
        return True
    except (OperationError, sqlite3.DatabaseError, ValueError, TypeError, KeyError):
        return False
