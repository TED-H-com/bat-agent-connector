"""Bounded fixed management views and flat original-source authorization.

Capture runs in the enclosing DocumentSnapshot journal transaction. Reads only
check authority for original identities; current domain heads never replace data.
"""
from __future__ import annotations

import copy
import json

from . import pm_evidence, pm_kanban, pm_snapshots
from .operations import OperationError

VIEWS = ('roadmap', 'kanban', 'sprint', 'governance')
MAX_RECORDS = 100
MAX_REFS = 512


def _bad(message, code='PM_SNAPSHOT_VIEW_SOURCE_UNAVAILABLE', status=404):
    return OperationError(code, message, status)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _unique(refs):
    values = {_json(ref): ref for ref in refs}
    if len(values) > MAX_REFS:
        raise _bad('A fixed view record exceeds its original-source bound', 'PM_SNAPSHOT_VIEW_TOO_LARGE', 413)
    return [values[key] for key in sorted(values)]


def snapshot_refs(ops, wid, snapshot_id, expected=None):
    """Flatten already-frozen authority, without recursive snapshot reads."""
    row = ops.db.execute('SELECT document,snapshot_digest FROM pm_document_snapshots WHERE workspace_id=? AND snapshot_id=?',
                         (wid, snapshot_id)).fetchone()
    if row is None:
        raise _bad('Original snapshot is unavailable')
    doc = json.loads(row['document'])
    if pm_snapshots._digest(doc) != row['snapshot_digest'] or expected is not None and expected != row['snapshot_digest']:
        raise _bad('Original snapshot identity changed')
    refs = [{'kind': 'snapshot_identity', 'id': snapshot_id, 'digest': row['snapshot_digest']}]
    refs.extend({'kind': 'evidence', 'id': value['evidence_id'], 'digest': value['fact_digest']} for value in doc['evidence'])
    refs.extend(doc.get('maintenance', {}).get('access_refs', []))
    for view in doc.get('management_views', {}).get('views', {}).values():
        for record in view['records']:
            refs.extend(record['access_refs'])
    return _unique(refs)


def access(ops, principal, wid, refs):
    """Authorize flat originals, preserving historical state and conclusions."""
    from . import pm_maintenance_review_sources, pm_quality_projections
    for ref in refs:
        kind, ident = ref['kind'], ref.get('id')
        if kind == 'operation':
            visible = pm_evidence._read_operation(ops, principal, wid, ident) is not None
        elif kind in {'task', 'command', 'session'}:
            visible = getattr(pm_evidence, '_' + kind + '_visible')(ops, principal, wid, ident)
        elif kind == 'quality_assessment':
            pm_quality_projections.original(ops, principal, wid, ref['reference'])
            visible = True
        else:
            pm_maintenance_review_sources.access(ops, principal, wid, [ref])
            visible = True
        if not visible:
            raise _bad('An original fixed view source is unavailable')


def _entry(kind, ident, data, refs=()):
    return {'kind': kind, 'id': ident, 'data': data, 'access_refs': _unique(refs)}


def _card_refs(card):
    refs = [{'kind': 'operation', 'id': value['operation_id']} for value in card['flow']['sources'] if value.get('operation_id')]
    execution = card['execution']
    for key, kind, field in (('operations', 'operation', 'operation_id'), ('tasks', 'task', 'task_id'),
                             ('sessions', 'session', 'resource_id')):
        refs.extend({'kind': kind, 'id': item[field]} for item in execution[key])
    for attempt in execution['attempts']:
        for field, kind in (('operation_id', 'operation'), ('command_id', 'command'), ('task_id', 'task')):
            if attempt.get(field):
                refs.append({'kind': kind, 'id': attempt[field]})
    refs.extend({'kind': 'evidence', 'id': value['evidence_id'], 'digest': value['fact_digest']}
                for value in (card['latest_capture'] or {}).get('evidence', []))
    selected = card['quality'].get('selected_assessment')
    if selected:
        refs.append({'kind': 'quality_assessment', 'reference': selected['reference']})
    return refs


def _table(ops, name):
    return bool(ops.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def _roadmap(ops, principal, doc, state):
    from . import pm_roadmap
    if not _table(ops, 'pm_roadmap_heads'):
        state['coverage']['available'] = False
        return
    current = pm_roadmap.get_roadmap(ops, principal, doc['workspace_id'])
    for kind in ('baseline', 'forecast', 'observation'):
        row = current[kind]
        if row:
            refs = []
            if kind == 'observation':
                original = row['document']
                refs = snapshot_refs(ops, doc['workspace_id'], original['snapshot_id'], original['snapshot_digest'])
            yield _entry(kind, row['revision_id'], row, refs)


def _kanban(ops, principal, doc, state):
    state['coverage']['truncated'] = doc['coverage']['work_truncated']
    for work in doc['work']:
        card = pm_kanban._card(ops, principal, doc['workspace_id'], work['binding'], doc['as_of'])
        yield _entry('work', work['work_item_id'], card, _card_refs(card))


def _quality_refs(document):
    return [{'kind': 'quality_assessment', 'reference': item['quality_assessment']['reference']}
            for item in document.get('items', []) if item.get('quality_assessment')]


def _sprint(ops, principal, doc, state):
    from . import pm_meetings, pm_sprints
    wid = doc['workspace_id']
    if not _table(ops, 'pm_sprints'):
        state['coverage']['available'] = False
        return
    rows = ops.db.execute('SELECT sprint_id FROM pm_sprints WHERE workspace_id=? ORDER BY sequence LIMIT ?', (wid, MAX_RECORDS + 1)).fetchall()
    state['coverage']['truncated'] = len(rows) > MAX_RECORDS
    for row in rows[:MAX_RECORDS]:
        view = pm_sprints.get_sprint(ops, principal, wid, row['sprint_id'], history_limit=100)
        # Freeze the shared snapshot time, not the getter's independent clock.
        view['timebox_elapsed'] = doc['as_of'] >= pm_sprints._date(view['sprint']['definition']['ends_at']).timestamp()
        # These are action capabilities of the current client, not historical facts.
        for key in ('review_available', 'retro_available', 'owner_reassignment_available'):
            view.pop(key, None)
        definition = view['sprint']['definition']
        view['artifacts'] = {kind: pm_sprints._artifact(ops.db, wid, definition[kind + '_revision_id'], kind)
                             for kind in ('product_goal', 'backlog', 'dod')}
        terminal = ops.db.execute("SELECT document FROM pm_sprint_events WHERE sprint_id=? AND action IN ('pm.sprint.close','pm.sprint.cancel')",
                                  (row['sprint_id'],)).fetchone()
        view['result'] = json.loads(terminal[0]) if terminal else None
        yield _entry('sprint', row['sprint_id'], view, _quality_refs(view['result'] or {}))
    if not _table(ops, 'pm_meetings'):
        return
    rows = ops.db.execute('SELECT meeting_id FROM pm_meetings WHERE workspace_id=? ORDER BY sequence LIMIT ?', (wid, MAX_RECORDS + 1)).fetchall()
    state['coverage']['truncated'] |= len(rows) > MAX_RECORDS
    for row in rows[:MAX_RECORDS]:
        view = pm_meetings.get_meeting(ops, principal, wid, row['meeting_id'], history_limit=100)
        # Flatten every context/result represented in the bounded public record.
        refs, contexts = [], {view['meeting']['context_revision_id']}
        for item in [*view['notes'], *view['follow_up_events']]:
            if item.get('context_revision_id'):
                contexts.add(item['context_revision_id'])
            reference = item.get('document', {}).get('snapshot_ref')
            if reference:
                refs.extend(snapshot_refs(ops, wid, reference['snapshot_id'], reference['snapshot_digest']))
        for cid in contexts:
            original = ops.db.execute('SELECT document FROM pm_meeting_contexts WHERE meeting_id=? AND context_revision_id=?',
                                       (row['meeting_id'], cid)).fetchone()
            if original is None:
                raise _bad('Original meeting context is unavailable')
            reference = json.loads(original[0])['snapshot_ref']
            refs.extend(snapshot_refs(ops, wid, reference['snapshot_id'], reference['snapshot_digest']))
        view.pop('snapshot')  # No nested snapshot payload or live substitution.
        yield _entry('meeting', row['meeting_id'], view, refs)


def _governance(ops, principal, doc, state):
    from . import pm_governance
    if not _table(ops, 'pm_governance_records'):
        state['coverage']['available'] = False
        return
    wid = doc['workspace_id']
    rows = ops.db.execute('SELECT record_id FROM pm_governance_records WHERE workspace_id=? ORDER BY sequence LIMIT ?', (wid, MAX_RECORDS + 1)).fetchall()
    state['coverage']['truncated'] = len(rows) > MAX_RECORDS
    for row in rows[:MAX_RECORDS]:
        value = pm_governance.get_record(ops, principal, wid, row['record_id'])
        original = value['record']['context']['snapshot_ref']
        yield _entry('finding', row['record_id'], value,
                     snapshot_refs(ops, wid, original['snapshot_id'], original['snapshot_digest']))


def _wire_size(doc):
    return len(json.dumps(pm_snapshots._response(doc, 9223372036854775807, '0' * 64), ensure_ascii=False, allow_nan=False).encode('utf-8'))


def capture(ops, principal, document):
    """Append whole original records within the shared snapshot response bound."""
    if not ops.db.in_transaction:
        raise _bad('Fixed views require the original snapshot transaction', 'PM_SNAPSHOT_TRANSACTION_REQUIRED', 409)
    views = {name: {'records': [], 'coverage': {'available': True, 'truncated': False, 'byte_limited': False,
              'visibility_filtered': False, 'basis': 'fixed_selected_records', 'network_checked': False}}
             for name in VIEWS}
    document['management_views'] = {'version': 1, 'views': views}
    if _wire_size(document) > pm_snapshots.MAX_SNAPSHOT_BYTES:
        raise _bad('Snapshot base leaves no room for fixed views', 'PM_SNAPSHOT_VIEW_TOO_LARGE', 413)
    for name, collector in zip(VIEWS, (_roadmap, _kanban, _sprint, _governance), strict=True):
        state = views[name]
        try:
            for entry in collector(ops, principal, document, state):
                if len(state['records']) >= MAX_RECORDS:
                    state['coverage']['truncated'] = True
                    break
                try:
                    access(ops, principal, document['workspace_id'], entry['access_refs'])
                except OperationError as exc:
                    if exc.status not in (403, 404):
                        raise
                    state['coverage']['visibility_filtered'] = True
                    continue
                state['records'].append(entry)
                if _wire_size(document) > pm_snapshots.MAX_SNAPSHOT_BYTES:
                    state['records'].pop()
                    state['coverage'].update(truncated=True, byte_limited=True)
                    break
        except OperationError as exc:
            if exc.status in (403, 404):
                state['coverage'].update(visibility_filtered=True, truncated=True)
            elif exc.status == 413:
                state['coverage'].update(byte_limited=True, truncated=True)
            else:
                raise
    # Flags may have grown after the last accepted record; the complete envelope
    # is measured again, retaining deterministic whole-record prefixes only.
    for name in reversed(VIEWS):
        state = views[name]
        while state['records'] and _wire_size(document) > pm_snapshots.MAX_SNAPSHOT_BYTES:
            state['records'].pop()
            state['coverage'].update(truncated=True, byte_limited=True)
    if _wire_size(document) > pm_snapshots.MAX_SNAPSHOT_BYTES:
        raise _bad('Snapshot base exceeds the fixed-view wire bound', 'PM_SNAPSHOT_VIEW_TOO_LARGE', 413)


def filter_views(ops, principal, wid, extension):
    if extension.get('version') != 1:
        raise _bad('Unknown fixed management-view version', 'PM_EXPORT_TEMPLATE_UNAVAILABLE', 409)
    result, filtered = copy.deepcopy(extension), False
    for state in result['views'].values():
        visible = []
        for entry in state['records']:
            try:
                access(ops, principal, wid, entry['access_refs'])
            except OperationError as exc:
                if exc.status not in (403, 404):
                    raise
                filtered = True
                state['coverage']['visibility_filtered'] = True
                continue
            visible.append(entry)
        state['records'] = visible
    return result, filtered
