"""Immutable discussion origins, resolved with current source access on every read.

Labels and summaries are data the user was looking at, never instructions or an
adoption. Unavailable chips expose only their kind and original position.
"""
from __future__ import annotations

import json
import re

from . import pm_access, pm_source_lineage, pm_workspace
from .operations import OperationError, OperationService

MAX_CONTEXTS = 8
KINDS = {
    'snapshot': (r'pmsnap_[0-9a-f]{32}', {}),
    'plan_revision': (r'pmrev_[0-9a-f]{32}', {}),
    'requirement': (r'[a-z][a-z0-9_.-]{0,79}', {'revision_id': r'pmrev_[0-9a-f]{32}'}),
    'milestone': (r'[a-z][a-z0-9_.-]{0,79}', {'revision_id': r'pmrb_[0-9a-f]{32}'}),
    'work_item': (r'wi_[0-9a-f]{20}', {}),
    'governance': (r'pmgov_[0-9a-f]{32}', {}),
    'finding': (r'pmfn_[0-9a-f]{32}', {'observation_id': r'pmgh_[0-9a-f]{32}', 'document_digest': r'[0-9a-f]{64}'}),
    'repair': (r'pmrp_[0-9a-f]{32}', {}),
    'follow_up': (r'pmfu_[0-9a-f]{32}', {}),
    'proposal': (r'pmcp_[0-9a-f]{32}', {}),
    'issue_mapping': (r'pmim_[0-9a-f]{32}', {}),
    'meeting': (r'pmmeet_[0-9a-f]{32}', {}),
}


def unavailable():
    return OperationError('PM_CONTEXT_SOURCE_UNAVAILABLE',
        'Discussion context is unavailable in this workspace / 此工作空間無法存取討論來源', 404)


def schema(ops):
    ops.db.execute('''CREATE TABLE IF NOT EXISTS pm_thread_contexts (
        thread_id TEXT NOT NULL REFERENCES pm_threads(thread_id), position INTEGER NOT NULL,
        kind TEXT NOT NULL, ref_id TEXT NOT NULL, extra_json TEXT NOT NULL,
        label_at_bind TEXT NOT NULL, bound_at REAL NOT NULL, operation_id TEXT NOT NULL,
        PRIMARY KEY(thread_id,position), UNIQUE(operation_id,position))''')
    for verb in ('UPDATE', 'DELETE'):
        ops.db.execute(f'''CREATE TRIGGER IF NOT EXISTS pm_thread_contexts_{verb.lower()}_immutable
            BEFORE {verb} ON pm_thread_contexts BEGIN SELECT RAISE(ABORT,'PM context is immutable'); END''')


def validate(refs):
    if not isinstance(refs, list) or len(refs) > MAX_CONTEXTS:
        raise OperationError('PM_CONTEXT_INVALID', 'Select at most eight context items / 最多可選八個討論來源', 422)
    seen = set()
    for ref in refs:
        spec = KINDS.get(ref.get('kind')) if isinstance(ref, dict) and isinstance(ref.get('kind'), str) else None
        if not spec or set(ref) != {'kind', 'id', *spec[1]} or any(
            not isinstance(ref.get(key), str) or not re.fullmatch(pattern, ref[key])
            for key, pattern in {'id': spec[0], **spec[1]}.items()
        ):
            raise OperationError('PM_CONTEXT_INVALID', 'Select exact context identities / 請選擇完整的討論來源識別資料', 422)
        identity = json.dumps(ref, sort_keys=True)
        if identity in seen:
            raise OperationError('PM_CONTEXT_INVALID', 'Context items must be distinct / 討論來源不可重複', 422)
        seen.add(identity)
    return refs


def _table(ops, name):
    if not pm_source_lineage._table(ops.db, name):
        raise unavailable()


def resolve(ops, principal, wid, ref):
    """Use original domain readers; never resolve an ID globally or by title."""
    from . import (
        pm_conversation_proposals,
        pm_evidence,
        pm_follow_up,
        pm_governance,
        pm_maintenance_findings,
        pm_maintenance_repairs,
        pm_meetings,
        pm_plans,
        pm_roadmap,
        pm_snapshots,
        work_items,
    )
    if not principal.allows('observe'):
        raise unavailable()
    pm_workspace.require_member(ops.db, principal, wid)
    kind, identity = ref['kind'], ref['id']
    sources = []
    if kind in {'plan_revision', 'requirement'}:
        _table(ops, 'pm_plan_revisions')
        rid = identity if kind == 'plan_revision' else ref['revision_id']
        record = pm_plans.get_plan(ops, principal, wid, rid)['plan']
        sources = pm_plans.source_operation_ids(ops.db, wid, rid)
        doc = record['document']
        if kind == 'requirement':
            summary = next((r for r in doc['requirements'] if r['id'] == identity), None)
            if summary is None:
                raise unavailable()
            label = summary['text']
        else:
            label = doc['title']
            summary = {k: doc[k] for k in ('title', 'sections', 'requirements')}
    elif kind == 'snapshot':
        _table(ops, 'pm_document_snapshots')
        record = pm_snapshots.get_snapshot(ops, principal, wid, identity)['snapshot']
        label = record['title']
        summary = {'title': label, 'as_of': record['as_of'], 'sections': list(record['brief'])}
        sources = [record['operation_id']]
    elif kind == 'milestone':
        _table(ops, 'pm_roadmap_revisions')
        record = pm_roadmap.get_revision(ops, principal, wid, ref['revision_id'])['revision']
        summary = next((m for m in record['document']['milestones'] if m['milestone_id'] == identity), None)
        if summary is None:
            raise unavailable()
        # The roadmap reader preserves history; authorize its explicit plan/work roots too.
        resolve(ops, principal, wid, {'kind': 'plan_revision', 'id': summary['plan_revision_id']})
        for bid in summary['work_binding_ids']:
            binding = pm_roadmap._binding(ops, wid, bid)
            pm_source_lineage.require(ops.db, principal, 'work_item', binding['work_item_id'])
            pm_access.require_operation_sources(ops.db, principal, [binding['operation_id']])
        label, sources = summary['title'], [record['operation_id']]
    elif kind == 'work_item':
        _table(ops, 'pm_work_bindings')
        binding = pm_evidence._binding(ops.db, wid, identity)
        if binding is None:
            raise unavailable()
        pm_source_lineage.require(ops.db, principal, 'work_item', identity)
        pm_access.require_operation_sources(ops.db, principal, [binding['operation_id']])
        record = work_items._get_item(ops.db, identity)
        label, sources = record['title'], [binding['operation_id']]
        summary = {k: record[k] for k in ('title', 'goal', 'state', 'version')}
    elif kind == 'governance':
        _table(ops, 'pm_governance_records')
        record = pm_governance.get_record(ops, principal, wid, identity)
        summary = record['revision']['document']
        label, sources = summary['title'], [record['record']['operation_id'], record['revision']['operation_id']]
    elif kind == 'finding':
        _table(ops, 'pm_github_observations')
        record = pm_maintenance_findings.get_finding(ops, principal, wid,
            ref['observation_id'], ref['document_digest'], identity)
        summary = record['finding']
        # Findings have opaque native identities, not an invented human title.
        label = str(summary['native']['number'])
        row = ops.db.execute('SELECT operation_id FROM pm_github_observations WHERE observation_id=?', (ref['observation_id'],)).fetchone()
        sources = [row[0]]
    elif kind == 'repair':
        _table(ops, 'pm_maintenance_repairs')
        record = pm_maintenance_repairs.get_repair(ops, principal, wid, identity)['repair']
        summary = {k: record[k] for k in ('finding_id', 'work_item_id', 'operation_status', 'outcome')}
        work = resolve(ops, principal, wid, {'kind': 'work_item', 'id': record['work_item_id']})
        label, sources = work['label'], [record['operation_id']]
    elif kind == 'follow_up':
        _table(ops, 'pm_follow_up_intakes')
        record = pm_follow_up.get_intake(ops, principal, wid, identity)['intake']
        summary = record['document']
        label, sources = record['intake_id'], [record['operation_id']]
    elif kind == 'proposal':
        _table(ops, 'pm_conversation_proposals')
        record = pm_conversation_proposals.get_proposal(ops, principal, wid, identity)['proposal']
        summary = record['document'] or {'validation': record['validation']}
        label, sources = summary.get('title', identity), [record['operation_id']]
    elif kind == 'issue_mapping':
        from . import pm_issue_mappings
        _table(ops, 'pm_issue_revisions')
        # The neighbouring historical reader has no page parameter. Cap its input.
        rows = ops.db.execute('SELECT sequence FROM pm_issue_revisions WHERE workspace_id=? AND mapping_id=? LIMIT 101', (wid, identity)).fetchall()
        if len(rows) > 100:
            raise OperationError('PM_CONTEXT_TOO_LARGE', 'Context history exceeds its bound / 討論來源歷程超出上限', 413)
        record = pm_issue_mappings.get_mapping(ops, principal, wid, identity)['current']
        summary = record['document']
        work = resolve(ops, principal, wid, {'kind': 'work_item', 'id': record['work_item_id']})
        label, sources = work['label'], [record['operation_id']]
    else:  # meeting
        _table(ops, 'pm_meetings')
        record = pm_meetings.get_meeting(ops, principal, wid, identity, history_limit=1)
        meeting = record['meeting']
        label, sources = meeting['title'], [meeting['operation_id']]
        summary = {'title': label, 'kind': meeting['kind'], 'draft': record['draft'], 'completion': record['completion']}
    pm_access.require_operation_sources(ops.db, principal, sources)
    pm_source_lineage.require_document(ops.db, principal, summary)
    return {'label': label, 'summary': summary, 'source_operation_ids': sources}


def admission(ops, principal, wid, refs):
    result = []
    for ref in validate(refs):
        try:
            value = resolve(ops, principal, wid, ref)
        except OperationError as exc:
            if exc.status in (403, 404):
                raise unavailable() from None
            raise
        result.append({'ref': ref, 'label': value['label']})
    return result


def bind(ctx, thread_id):
    # _check has just re-authorized every source inside this same transaction.
    saved = (ctx.admission_binding or {}).get('pm_thread_contexts', [])
    for position, item in enumerate(saved):
        ref = item['ref']
        ctx.service.db.execute('INSERT INTO pm_thread_contexts VALUES(?,?,?,?,?,?,?,?)',
            (thread_id, position, ref['kind'], ref['id'], json.dumps({k: v for k, v in ref.items() if k not in {'kind', 'id'}}),
             item['label'], ctx.op['created_at'], ctx.operation_id))


def read(ops, principal, wid, thread_id, *, summaries=False):
    rows = ops.db.execute('SELECT * FROM pm_thread_contexts WHERE thread_id=? ORDER BY position LIMIT 8', (thread_id,)).fetchall()
    result = []
    for row in rows:
        ref = {'kind': row['kind'], 'id': row['ref_id'], **json.loads(row['extra_json'])}
        item = {'kind': row['kind'], 'position': row['position'], 'available': False}
        try:
            value = resolve(ops, principal, wid, ref)
        except OperationError as exc:
            if exc.status not in (403, 404, 409, 413):
                raise
        else:
            item.update(**ref, available=True, label=value['label'], bound_label=row['label_at_bind'])
            if summaries:
                item.update(summary=value['summary'], source_operation_ids=value['source_operation_ids'])
        result.append(item)
    return result


class Reader:
    """Read facade for access checks that receive the central DB only."""
    def __init__(self, db):
        self.db = db
        self.journal = self

    def tx(self):
        from .pm_release_baselines import _snapshot
        return _snapshot(self.db)

    def _row(self, oid):
        row = self.db.execute('SELECT * FROM operations WHERE operation_id=?', (oid,)).fetchone()
        if row is None:
            raise unavailable()
        return OperationService._decode(row)

    def get(self, oid, *, steps=False, principal=None):
        value = self._row(oid)
        if principal is not None:
            pm_access.require_visible(self.db, principal, value)
        return value


def frozen_visible(db, principal, wid, context):
    """Previously included bytes are withdrawn if their original source is lost."""
    for item in context.get('bound_context', []):
        if not item['available']:
            continue
        ref = {key: item[key] for key in ('kind', 'id', *KINDS[item['kind']][1])}
        try:
            resolve(Reader(db), principal, wid, ref)
        except OperationError:
            return False
    return True


def operation_visible(db, principal, op):
    # Thread-create envelopes retain private origin IDs/labels in admission.
    try:
        original = Reader(db)._row(op['operation_id'])
        admission(Reader(db), principal, original['target']['workspace_id'], original['params'].get('context', []))
        return True
    except OperationError:
        return False
