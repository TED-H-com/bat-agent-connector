"""Sourced RAID, change proposals and direction decisions; never work execution."""
from __future__ import annotations

import datetime as dt
import json
import re
import time

from . import pm_plans, pm_snapshots, pm_sprints, pm_workspace
from .operations import ActionDef, OperationError, OperationService

KINDS = ('risk', 'assumption', 'issue', 'dependency', 'drift', 'change')
ACTIONS = tuple('pm.governance.' + name for name in ('record', 'revise', 'decide', 'adoption.link'))
MAX_READ_BYTES = 2_000_000
MAX_DOCUMENT_BYTES = 200_000
_RECORD = re.compile(r'pmgov_[0-9a-f]{32}')
_JSON = pm_snapshots._json
_DIGEST = pm_snapshots._digest


def _bad(code, message, status=422):
    return OperationError(code, message, status)


def _only(value, fields):
    if not isinstance(value, dict) or set(value) - set(fields):
        raise _bad('INVALID_PARAMS', 'Unexpected governance fields')


def _text(value, label, limit=4000):
    return pm_sprints._text(value, label, limit)


def _member(ops, principal, wid, *, write=False, decide=False):
    if not principal.allows('observe') or write and not principal.allows('manage'):
        raise _bad('FORBIDDEN', 'Governance reads need observe; writes also need manage', 403)
    result = pm_workspace.require_member(ops.db, principal, wid, write=write)
    if decide and pm_sprints._role(ops.db, wid, principal.actor) != 'owner':
        raise _bad('FORBIDDEN', 'A current workspace owner must record direction decisions', 403)
    return result


def schema(journal):
    pm_snapshots.schema(journal)
    with journal.tx():
        for statement in (
            '''CREATE TABLE IF NOT EXISTS pm_governance_records (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, record_id TEXT NOT NULL UNIQUE,
                workspace_id TEXT NOT NULL, kind TEXT NOT NULL, root_cause_key TEXT NOT NULL,
                context TEXT NOT NULL, context_digest TEXT NOT NULL, created_by TEXT NOT NULL,
                created_at REAL NOT NULL, operation_id TEXT NOT NULL UNIQUE,
                UNIQUE(workspace_id,kind,root_cause_key))''',
            '''CREATE TABLE IF NOT EXISTS pm_governance_heads (
                record_id TEXT PRIMARY KEY, version INTEGER NOT NULL, revision_id TEXT NOT NULL)''',
            '''CREATE TABLE IF NOT EXISTS pm_governance_revisions (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, revision_id TEXT NOT NULL UNIQUE,
                record_id TEXT NOT NULL, previous_revision_id TEXT, version INTEGER NOT NULL,
                action TEXT NOT NULL, document TEXT NOT NULL, document_digest TEXT NOT NULL,
                actor TEXT NOT NULL, created_at REAL NOT NULL, operation_id TEXT NOT NULL UNIQUE)''',
            '''CREATE TABLE IF NOT EXISTS pm_governance_sources (
                workspace_id TEXT NOT NULL, kind TEXT NOT NULL, identity TEXT NOT NULL,
                record_id TEXT NOT NULL, PRIMARY KEY(workspace_id,kind,identity,record_id))''',
            'CREATE INDEX IF NOT EXISTS pm_governance_history ON pm_governance_revisions(record_id,sequence)',
        ):
            journal.db.execute(statement)
        for table in ('pm_governance_records', 'pm_governance_revisions', 'pm_governance_sources'):
            for action in ('UPDATE', 'DELETE'):
                journal.db.execute(f'''CREATE TRIGGER IF NOT EXISTS {table}_immutable_{action.lower()}
                    BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'governance history is immutable'); END''')


def install(ops):
    schema(ops.journal)
    for action in ACTIONS:
        if action not in ops.actions:
            keys = ('workspace_id',) if action == ACTIONS[0] else ('workspace_id', 'record_id')
            ops.register(ActionDef(action, 'manage', 'Record sourced PM governance without execution', _run,
                                   _admitter(action), keys, authorize_existing=_authorize_existing))


def _record(db, wid, record_id):
    if not isinstance(record_id, str) or not _RECORD.fullmatch(record_id):
        raise _bad('PM_GOVERNANCE_NOT_FOUND', 'Governance record not found', 404)
    row = db.execute('''SELECT r.*,h.version,h.revision_id FROM pm_governance_records r
        JOIN pm_governance_heads h USING(record_id) WHERE r.workspace_id=? AND r.record_id=?''', (wid, record_id)).fetchone()
    if row is None:
        raise _bad('PM_GOVERNANCE_NOT_FOUND', 'Governance record not found', 404)
    result = dict(row)
    result['context'] = json.loads(row['context'])
    if _DIGEST(result['context']) != row['context_digest']:
        raise _bad('PM_GOVERNANCE_UNAVAILABLE', 'Original governance context cannot be verified', 409)
    return result


def _revision(db, revision_id):
    row = db.execute('SELECT * FROM pm_governance_revisions WHERE revision_id=?', (revision_id,)).fetchone()
    if row is None:
        raise _bad('PM_GOVERNANCE_NOT_FOUND', 'Governance revision not found', 404)
    result = {**dict(row), 'document': json.loads(row['document'])}
    if _DIGEST(result['document']) != row['document_digest']:
        raise _bad('PM_GOVERNANCE_UNAVAILABLE', 'Original governance revision cannot be verified', 409)
    return result


def _snapshot(ops, principal, wid, snapshot_id, digest=None):
    # Deliberately reject the whole derived narrative if any original snapshot
    # source is withdrawn: arbitrary discussion text may quote that source.
    snapshot = pm_snapshots.get_snapshot(ops, principal, wid, snapshot_id)['snapshot']
    if snapshot['visibility']['filtered']:
        raise _bad('PM_GOVERNANCE_SOURCE_UNAVAILABLE', 'Original governance sources are unavailable', 404)
    if digest is not None and digest != snapshot['snapshot_digest']:
        raise _bad('PM_GOVERNANCE_SOURCE_UNAVAILABLE', 'Original snapshot identity cannot be verified', 404)
    if not snapshot['baseline']['document']:
        raise _bad('PM_ADOPTED_PLAN_REQUIRED', 'Governance needs a snapshot with an explicitly adopted plan', 409)
    return snapshot


def _access(ops, principal, record):
    ref = record['context']['snapshot_ref']
    return _snapshot(ops, principal, record['workspace_id'], ref['snapshot_id'], ref['snapshot_digest'])


def _sources(snapshot, refs):
    if not isinstance(refs, list) or not 1 <= len(refs) <= 50:
        raise _bad('INVALID_PARAMS', 'Select 1–50 fixed governance source references')
    baseline = snapshot['baseline']
    maps = {
        'plan_input': {v['input_id']: v for v in baseline['document']['selected_inputs']},
        'requirement': {v['id']: v for v in baseline['document']['requirements']},
        'evidence': {v['evidence_id']: v for v in snapshot['evidence']},
        'work_binding': {v['binding']['binding_id']: v['binding'] for v in snapshot['work']},
    }
    result, seen = [], set()
    for ref in refs:
        _only(ref, ('kind', 'id'))
        if set(ref) != {'kind', 'id'} or not isinstance(ref['kind'], str) or not isinstance(ref['id'], str):
            raise _bad('INVALID_PARAMS', 'A source reference needs kind and id')
        kind, identifier = ref['kind'], ref['id']
        if kind not in maps or identifier not in maps[kind] or (kind, identifier) in seen:
            raise _bad('INVALID_PARAMS', 'Select distinct original sources from this fixed snapshot and plan')
        seen.add((kind, identifier))
        source = maps[kind][identifier]
        identity = f'{kind}:{identifier}'
        if kind == 'requirement':
            identity = f'{kind}:{baseline["document_revision"]}:{identifier}'
        if kind == 'evidence':
            identity = 'evidence:' + _DIGEST({key: source[key] for key in ('source_kind', 'source_id', 'fact_digest')})
        result.append({**ref, 'identity': identity, 'source': source,
                       'plan_revision_id': baseline['document_revision'] if kind in {'plan_input', 'requirement'} else None})
    return result


def _review_time(value):
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})', value):
        raise _bad('INVALID_PARAMS', 'review_at must be an ISO timestamp with seconds and timezone, or null')
    try:
        if value[-1] != 'Z' and (int(value[-5:-3]) > 23 or int(value[-2:]) > 59):
            raise ValueError
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        if not -24 * 3600 < parsed.utcoffset().total_seconds() < 24 * 3600:
            raise ValueError
    except ValueError:
        raise _bad('INVALID_PARAMS', 'review_at must be a real calendar timestamp') from None
    return value


FIELDS = ('title', 'description', 'owner_actor', 'affected_requirement_ids', 'trigger', 'response',
          'review_at', 'status', 'dimension', 'alternatives', 'proposed_revision_id', 'source_refs', 'rationale', 'disambiguation')


def _document(ops, principal, wid, kind, snapshot, params):
    owner = _text(params.get('owner_actor'), 'owner_actor', 200)
    if pm_sprints._role(ops.db, wid, owner) not in {'owner', 'contributor'}:
        raise _bad('FORBIDDEN', 'The accountable owner must currently have workspace write membership', 403)
    affected = pm_sprints._ids(params.get('affected_requirement_ids', []), 'affected requirements', 50)
    requirements = {r['id'] for r in snapshot['baseline']['document']['requirements']}
    if set(affected) - requirements:
        raise _bad('INVALID_PARAMS', 'Affected requirements must belong to this exact accepted plan')
    status = params.get('status', 'open')
    if not isinstance(status, str) or status not in {'open', 'monitoring', 'reported_resolved'}:
        raise _bad('INVALID_PARAMS', 'Status is open, monitoring or reported_resolved; verification is separate')
    dimension = params.get('dimension')
    if dimension is not None and dimension not in ('scope', 'schedule', 'quality', 'resources'):
        raise _bad('INVALID_PARAMS', 'Select a scope, schedule, quality or resources dimension')
    sources = _sources(snapshot, params.get('source_refs'))
    if kind == 'drift' and (dimension is None or not any(s['kind'] == 'evidence' for s in sources)
                            or not any(s['kind'] in {'plan_input', 'requirement'} for s in sources)):
        raise _bad('INVALID_PARAMS', 'A reported drift needs a dimension, fixed plan source and observed evidence')
    proposed = params.get('proposed_revision_id')
    if proposed is not None:
        if kind != 'change':
            raise _bad('INVALID_PARAMS', 'Only change proposals select a proposed plan revision')
        plan = pm_plans.get_plan(ops, principal, wid, proposed)['plan']
        proposed = {'revision_id': proposed, 'document_digest': plan['document_digest'], 'scope_digest': plan['structured_scope_digest']}
    alternatives = params.get('alternatives', [])
    if not isinstance(alternatives, list) or len(alternatives) > 10:
        raise _bad('INVALID_PARAMS', 'Provide at most ten considered alternatives')
    result = {'title': _text(params.get('title'), 'title', 200), 'description': _text(params.get('description'), 'description', 8000),
              'owner_actor': owner, 'affected_requirement_ids': affected, 'trigger': _text(params['trigger'], 'trigger') if params.get('trigger') is not None else None,
              'response': _text(params['response'], 'response') if params.get('response') is not None else None,
              'review_at': _review_time(params.get('review_at')), 'status': status, 'dimension': dimension,
              'alternatives': [_text(v, 'alternative', 2000) for v in alternatives], 'proposed_revision': proposed,
              'sources': sources, 'rationale': _text(params.get('rationale'), 'rationale'),
              'assessment_basis': 'attributed_assumption' if kind == 'assumption' else 'attributed_management_finding',
              'verification_status': 'unassessed', 'decision': None, 'adoption': None,
              'scope_applied': False, 'execution_authorized': False}
    return result


def _overlaps(ops, principal, wid, kind, sources, *, exclude=None):
    found = set()
    for source in sources:
        found.update(r[0] for r in ops.db.execute('SELECT record_id FROM pm_governance_sources WHERE workspace_id=? AND kind=? AND identity=?',
                                                (wid, kind, source['identity'])))
    found.discard(exclude)
    for identifier in sorted(found):
        _access(ops, principal, _record(ops.db, wid, identifier))
    return sorted(found)


def _disambiguate(params, overlaps, *, prior=None):
    value = params.get('disambiguation')
    if value is None and prior and prior['record_ids'] == overlaps:
        return prior
    if not overlaps:
        if value is not None:
            raise _bad('INVALID_PARAMS', 'Disambiguation only relates actual overlapping source findings')
        return None
    if value is None:
        raise _bad('PM_GOVERNANCE_DUPLICATE_CANDIDATE', 'Sources already support these records; supply explicit disambiguation: ' + ', '.join(overlaps), 409)
    _only(value, ('record_ids', 'rationale'))
    ids = pm_sprints._ids(value.get('record_ids'), 'related record_ids', 100)
    if sorted(ids) != overlaps:
        raise _bad('PM_GOVERNANCE_DUPLICATE_CANDIDATE', 'Disambiguation must identify every overlapping record', 409)
    return {'record_ids': overlaps, 'rationale': _text(value.get('rationale'), 'disambiguation rationale')}


def _context(snapshot):
    baseline = snapshot['baseline']
    return {'snapshot_ref': {key: snapshot[key] for key in ('snapshot_id', 'snapshot_digest', 'as_of', 'journal_cursor')},
            'plan_revision_id': baseline['document_revision'], 'scope_revision': baseline['scope_revision'],
            'plan_document_digest': baseline['document_digest'], 'coverage': snapshot['coverage']}


def _check(ops, principal, action, target, params, pre):
    if not all(isinstance(v, dict) for v in (target, params, pre)):
        raise _bad('INVALID_PARAMS', 'Supply governance parameters and preconditions as objects')
    create = action == ACTIONS[0]
    if set(target) != ({'workspace_id'} if create else {'workspace_id', 'record_id'}):
        raise _bad('INVALID_TARGET', 'Select exact workspace and governance record identities')
    wid = target['workspace_id']
    _member(ops, principal, wid, write=True, decide=action in ACTIONS[2:])
    if create:
        _only(params, (*FIELDS, 'kind', 'root_cause_key', 'snapshot_id'))
        if set(pre) != {'expected_plan_version'} or type(pre['expected_plan_version']) is not int:
            raise _bad('PRECONDITION_REQUIRED', 'Supply the current plan head version')
        if pm_plans.head(ops.db, wid)['version'] != pre['expected_plan_version']:
            raise _bad('VERSION_CONFLICT', 'The plan head changed before this finding was recorded', 409)
        kind = params.get('kind')
        if not isinstance(kind, str) or kind not in KINDS:
            raise _bad('INVALID_PARAMS', 'Select risk, assumption, issue, dependency, drift or change')
        key = _text(params.get('root_cause_key'), 'root_cause_key', 120)
        snapshot = _snapshot(ops, principal, wid, params.get('snapshot_id'))
        document = _document(ops, principal, wid, kind, snapshot, params)
        prior = ops.db.execute('SELECT record_id FROM pm_governance_records WHERE workspace_id=? AND kind=? AND root_cause_key=?', (wid, kind, key)).fetchone()
        if prior:
            record = _record(ops.db, wid, prior[0])
            _access(ops, principal, record)
            return record, None, True
        overlaps = _overlaps(ops, principal, wid, kind, document['sources'])
        document['disambiguation'] = _disambiguate(params, overlaps)
        return {'workspace_id': wid, 'kind': kind, 'root_cause_key': key, 'context': _context(snapshot)}, document, False
    record = _record(ops.db, wid, target['record_id'])
    snapshot = _access(ops, principal, record)
    if set(pre) != {'expected_version'} or type(pre['expected_version']) is not int or pre['expected_version'] != record['version']:
        raise _bad('VERSION_CONFLICT', 'Read the current governance version before changing it', 409)
    previous = _revision(ops.db, record['revision_id'])['document']
    if action == ACTIONS[1]:
        _only(params, FIELDS)
        if principal.actor != previous['owner_actor'] and pm_sprints._role(ops.db, wid, principal.actor) != 'owner':
            raise _bad('FORBIDDEN', 'The assigned writer or a workspace owner must revise this finding', 403)
        document = _document(ops, principal, wid, record['kind'], snapshot, params)
        if {s['identity'] for s in previous['sources']} - {s['identity'] for s in document['sources']}:
            raise _bad('INVALID_PARAMS', 'Original sources cannot be removed from a finding')
        overlaps = _overlaps(ops, principal, wid, record['kind'], document['sources'], exclude=record['record_id'])
        document['disambiguation'] = _disambiguate(params, overlaps, prior=previous.get('disambiguation'))
        return record, document, False
    if record['kind'] != 'change':
        raise _bad('INVALID_PARAMS', 'Direction decisions and adoption links belong to change proposals')
    if pm_sprints._role(ops.db, wid, previous['owner_actor']) not in {'owner', 'contributor'}:
        raise _bad('FORBIDDEN', 'Assign a current workspace writer before deciding this proposal', 403)
    document = {**previous, 'rationale': _text(params.get('rationale'), 'rationale')}
    if action == ACTIONS[2]:
        _only(params, ('disposition', 'rationale'))
        if params.get('disposition') not in ('adopted_direction', 'rejected'):
            raise _bad('INVALID_PARAMS', 'Record adopted_direction or rejected, not accepted_plan')
        document.update(decision={'disposition': params['disposition'], 'rationale': document['rationale'],
                                 'recorded_by': principal.actor, 'proposal_revision_id': record['revision_id']},
                        adoption=None, scope_applied=False)
    else:
        _only(params, ('adoption_operation_id', 'rationale'))
        if not previous['decision'] or previous['decision']['disposition'] != 'adopted_direction' or not previous['proposed_revision']:
            raise _bad('PM_GOVERNANCE_DIRECTION_REQUIRED', 'Link adoption only to an adopted direction with a fixed proposed revision', 409)
        operation_id = pm_sprints._identifier(params.get('adoption_operation_id'))
        operation = ops.get(operation_id, steps=False, principal=principal)
        receipt = ops.db.execute('SELECT * FROM pm_plan_adoptions WHERE operation_id=? AND workspace_id=?', (operation_id, wid)).fetchone()
        proposed = previous['proposed_revision']
        if (operation['action'] != 'pm.plan.adopt' or operation['status'] != 'succeeded' or receipt is None
                or receipt['revision_id'] != proposed['revision_id'] or receipt['previous_revision_id'] != record['context']['plan_revision_id']
                or operation['target'] != {'workspace_id': wid, 'revision_id': proposed['revision_id']}):
            raise _bad('PM_GOVERNANCE_ADOPTION_MISMATCH', 'Select the successful adoption of this exact proposal from its fixed baseline', 409)
        fact = json.loads(receipt['result'])
        if fact['document_digest'] != proposed['document_digest']:
            raise _bad('PM_GOVERNANCE_ADOPTION_MISMATCH', 'The adopted document digest differs from this proposal', 409)
        document.update(adoption={'operation_id': operation_id, 'revision_id': receipt['revision_id'],
                                  'previous_revision_id': receipt['previous_revision_id'], 'decision_id': receipt['decision_id'],
                                  'scope_revision': receipt['scope_revision'], 'document_digest': fact['document_digest'],
                                  'adopted_by': receipt['adopted_by'], 'adopted_at': receipt['created_at'],
                                  'basis': 'original_successful_plan_adoption', 'is_current_accepted': 'not_assessed'}, scope_applied=True)
    return record, document, False


def _admitter(action):
    def admit(ops, principal, target, params, pre):
        record, _, _ = _check(ops, principal, action, target, params, pre)
        refs = []
        if action == ACTIONS[0] and params['snapshot_id'] != record['context']['snapshot_ref']['snapshot_id']:
            snap = _snapshot(ops, principal, target['workspace_id'], params['snapshot_id'])
            refs.append(_context(snap)['snapshot_ref'])
        return {'principal': pm_workspace.principal_binding(principal), 'governance_context': record['context'],
                'additional_snapshot_refs': refs, 'record_id': record.get('record_id')}
    return admit


def _authorize_existing(ops, principal, op, verb):
    _member(ops, principal, op['target']['workspace_id'], write=True)
    if verb != 'replay' and principal.actor != op['actor']:
        raise _bad('FORBIDDEN', 'Only the original requester controls this governance operation', 403)
    if not operation_visible(ops.db, principal, op):
        raise _bad('PM_GOVERNANCE_SOURCE_UNAVAILABLE', 'Original governance sources are unavailable', 404)


async def _run(ctx):
    def apply():
        ops, wid, action = ctx.service, ctx.target['workspace_id'], ctx.op['action']
        record, document, duplicate = _check(ops, pm_workspace.admitted_principal(ctx), action, ctx.target, ctx.params, ctx.preconditions)
        ctx.set_refs(governance_result_context=record['context'])
        if duplicate:
            return {'workspace_id': wid, 'record_id': record['record_id'], 'revision_id': record['revision_id'],
                    'version': record['version'], 'deduplicated': True, 'input_applied': False, 'execution_authorized': False}
        if pm_sprints._read_size(document) > MAX_DOCUMENT_BYTES:
            raise _bad('PM_GOVERNANCE_TOO_LARGE', 'Use a focused finding of at most 200,000 UTF-8 bytes', 413)
        suffix, now = ctx.operation_id[3:], time.time()
        create = action == ACTIONS[0]
        rid = 'pmgov_' + suffix if create else record['record_id']
        revision_id, version = 'pmgrev_' + suffix, 1 if create else record['version'] + 1
        if create:
            ops.db.execute('''INSERT INTO pm_governance_records(record_id,workspace_id,kind,root_cause_key,context,context_digest,created_by,created_at,operation_id)
                VALUES(?,?,?,?,?,?,?,?,?)''', (rid, wid, record['kind'], record['root_cause_key'], _JSON(record['context']), _DIGEST(record['context']), ctx.actor, now, ctx.operation_id))
        ops.db.execute('''INSERT INTO pm_governance_revisions(revision_id,record_id,previous_revision_id,version,action,document,document_digest,actor,created_at,operation_id)
            VALUES(?,?,?,?,?,?,?,?,?,?)''', (revision_id, rid, None if create else record['revision_id'], version, action, _JSON(document), _DIGEST(document), ctx.actor, now, ctx.operation_id))
        ops.db.execute('''INSERT INTO pm_governance_heads VALUES(?,?,?) ON CONFLICT(record_id)
            DO UPDATE SET version=excluded.version,revision_id=excluded.revision_id''', (rid, version, revision_id))
        for source in document['sources']:
            ops.db.execute('INSERT OR IGNORE INTO pm_governance_sources VALUES(?,?,?,?)', (wid, record['kind'], source['identity'], rid))
        result = {'workspace_id': wid, 'record_id': rid, 'revision_id': revision_id, 'version': version,
                  'deduplicated': False, 'input_applied': True, 'execution_authorized': False}
        ops.journal.api_event('pm_governance', rid, action, {**result, 'operation_id': ctx.operation_id}, actor=ctx.actor)
        return result
    return ctx.effect('pm.governance.apply', apply)


class _SourceReader:
    """Read existing source authority without starting an OperationService/DDL."""
    def __init__(self, db):
        self.db = db

    def get(self, operation_id, *, steps=False, principal=None):
        row = self.db.execute('SELECT * FROM operations WHERE operation_id=?', (operation_id,)).fetchone()
        if row is None:
            raise _bad('NOT_FOUND', 'operation not found', 404)
        operation = OperationService._decode(row)
        if principal is not None:
            from .pm_access import require_visible
            require_visible(self.db, principal, operation)
        return operation


def operation_visible(db, principal, operation):
    """Shared operation/feed integration hook; always check original admission."""
    try:
        row = db.execute('SELECT * FROM operations WHERE operation_id=?', (operation['operation_id'],)).fetchone()
        if row is None:
            return False
        original = OperationService._decode(row)
        if original['action'] not in ACTIONS:
            return False
        wid = original['target']['workspace_id']
        pm_workspace.require_member(db, principal, wid)
        binding = (original['external_refs'] or {}).get('admission_binding') or {}
        context = binding.get('governance_context')
        if not isinstance(context, dict):
            return False
        refs = [context['snapshot_ref'], *binding.get('additional_snapshot_refs', [])]
        result_context = (original['external_refs'] or {}).get('governance_result_context')
        if result_context:
            refs.append(result_context['snapshot_ref'])
        return all(_snapshot_visible(db, principal, wid, ref) for ref in refs)
    except (OperationError, KeyError, ValueError, TypeError):
        return False


def _snapshot_visible(db, principal, wid, ref):
    try:
        saved = db.execute('SELECT * FROM pm_document_snapshots WHERE workspace_id=? AND snapshot_id=?', (wid, ref['snapshot_id'])).fetchone()
        if saved is None or saved['snapshot_digest'] != ref['snapshot_digest']:
            return False
        view = pm_snapshots._out(_SourceReader(db), principal, saved)['snapshot']
        return not view['visibility']['filtered']
    except (OperationError, KeyError, ValueError, TypeError):
        return False


def event_visible(db, principal, event):
    operation_id = (event.get('body') or {}).get('operation_id')
    return bool(operation_id and operation_visible(db, principal, {'operation_id': operation_id}))


def _checked(value):
    if pm_sprints._read_size(value) > MAX_READ_BYTES:
        raise _bad('PM_GOVERNANCE_READ_TOO_LARGE', 'One complete governance record exceeds the read byte limit', 413)
    return value


def _view(ops, principal, wid, rid):
    record = _record(ops.db, wid, rid)
    _access(ops, principal, record)
    return {'record': record, 'revision': _revision(ops.db, record['revision_id'])}


def get_record(ops, principal, workspace_id, record_id):
    with ops.journal.tx():
        _member(ops, principal, workspace_id)
        return _checked({'version': 1, 'workspace_id': workspace_id, **_view(ops, principal, workspace_id, record_id),
                         'coverage': {'source_refresh': 'not_performed', 'semantic_verification': 'unassessed',
                                      'automatic_follow_up': 'unavailable', 'max_bytes': MAX_READ_BYTES}, 'execution_authorized': False})


def _page(ops, principal, wid, rows, decode, *, after, limit, key, **extra):
    coverage = {'max_bytes': MAX_READ_BYTES, 'after': after, 'returned': 0, 'scanned': 0,
                'has_more': bool(rows), 'byte_limited': False, 'visibility_filtered': False}
    result = {'version': 1, 'workspace_id': wid, **extra, key: [], 'next_cursor': after if rows else None, 'coverage': coverage}
    for index, row in enumerate(rows[:limit]):
        previous, cursor = dict(coverage), result['next_cursor']
        try:
            value = decode(row)
        except OperationError as error:
            if error.status != 404:
                raise
            value = None
        if value is not None:
            result[key].append(value)
        more = index + 1 < len(rows)
        result['next_cursor'] = row['sequence'] if more else None
        coverage.update(returned=len(result[key]), scanned=index + 1, has_more=more,
                        visibility_filtered=coverage['visibility_filtered'] or value is None)
        if pm_sprints._read_size(result) > MAX_READ_BYTES:
            if value is not None:
                result[key].pop()
            result['next_cursor'], coverage = cursor, previous
            coverage.update(has_more=True, byte_limited=True)
            result['coverage'] = coverage
            if not coverage['scanned']:
                raise _bad('PM_GOVERNANCE_READ_TOO_LARGE', 'One complete governance record exceeds the read byte limit', 413)
            break
    return _checked(result)


def list_records(ops, principal, workspace_id, *, kind=None, after=0, limit=50):
    with ops.journal.tx():
        _member(ops, principal, workspace_id)
        pm_sprints._page(after, limit)
        if kind is not None and (not isinstance(kind, str) or kind not in KINDS):
            raise _bad('INVALID_PARAMS', 'Unknown governance kind')
        rows = ops.db.execute('''SELECT sequence,record_id FROM pm_governance_records WHERE workspace_id=?
            AND (? IS NULL OR kind=?) AND sequence>? ORDER BY sequence LIMIT ?''', (workspace_id, kind, kind, after, limit + 1)).fetchall()
        return _page(ops, principal, workspace_id, rows, lambda r: _view(ops, principal, workspace_id, r['record_id']),
                     after=after, limit=limit, key='records')


def list_history(ops, principal, workspace_id, record_id, *, after=0, limit=50):
    with ops.journal.tx():
        _member(ops, principal, workspace_id)
        pm_sprints._page(after, limit)
        record = _record(ops.db, workspace_id, record_id)
        _access(ops, principal, record)
        rows = ops.db.execute('SELECT sequence,revision_id FROM pm_governance_revisions WHERE record_id=? AND sequence>? ORDER BY sequence LIMIT ?',
                              (record_id, after, limit + 1)).fetchall()
        return _page(ops, principal, workspace_id, rows, lambda r: _revision(ops.db, r['revision_id']),
                     after=after, limit=limit, key='revisions', record_id=record_id, context=record['context'])
