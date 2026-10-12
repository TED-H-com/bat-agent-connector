"""Immutable, accountable acceptance/DoD conclusions over original fixed results."""
from __future__ import annotations

import json
import re
import time

from . import pm_evidence, pm_plans, pm_sprints, pm_workspace, work_items
from . import pm_quality_sources as sources
from . import pm_release_baselines as snapshots
from .operations import ActionDef, OperationError, OperationService
from .pm_work_batch_evidence import digest

CREATE = 'pm.quality.review.create'
RECORD = 'pm.quality.review.record'
REVOKE = 'pm.quality.review.revoke'
ACTIONS = (CREATE, RECORD, REVOKE)
REVIEW = re.compile('pmqr_[0-9a-f]{32}')
MAX_BYTES = 512_000
MAX_HISTORY = 32


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _size(value):
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode())


def _bounded(value):
    if _size(value) > MAX_BYTES:
        raise sources.bad('The whole original quality review exceeds the byte budget', 'PM_QUALITY_LIMIT', 413)
    return value


def installed(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pm_quality_reviews'").fetchone() is not None


def schema(journal):
    with journal.tx():
        journal.db.execute('''CREATE TABLE IF NOT EXISTS pm_quality_reviews(
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,review_id TEXT UNIQUE NOT NULL,
            operation_id TEXT UNIQUE NOT NULL,workspace_id TEXT NOT NULL,work_item_id TEXT NOT NULL,
            subject_key TEXT NOT NULL,context TEXT NOT NULL,context_digest TEXT NOT NULL,
            created_at REAL NOT NULL,version INTEGER NOT NULL DEFAULT 1)''')
        journal.db.execute('''CREATE TABLE IF NOT EXISTS pm_quality_review_heads(
            workspace_id TEXT NOT NULL,subject_key TEXT NOT NULL,review_id TEXT NOT NULL,
            PRIMARY KEY(workspace_id,subject_key))''')
        journal.db.execute('''CREATE TABLE IF NOT EXISTS pm_quality_review_records(
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,review_id TEXT NOT NULL,operation_id TEXT UNIQUE NOT NULL,
            version INTEGER NOT NULL,kind TEXT NOT NULL,document TEXT NOT NULL,document_digest TEXT NOT NULL,
            actor TEXT NOT NULL,created_at REAL NOT NULL,UNIQUE(review_id,version))''')
        journal.db.execute('CREATE INDEX IF NOT EXISTS pm_quality_reviews_work ON pm_quality_reviews(workspace_id,work_item_id,sequence)')
        journal.db.execute('CREATE INDEX IF NOT EXISTS pm_quality_review_record_lookup ON pm_quality_review_records(review_id,version)')
        journal.db.execute('''CREATE TRIGGER IF NOT EXISTS pm_quality_reviews_identity_immutable
            BEFORE UPDATE OF review_id,operation_id,workspace_id,work_item_id,subject_key,context,context_digest,created_at ON pm_quality_reviews
            BEGIN SELECT RAISE(ABORT,'Quality review context is immutable'); END''')
        journal.db.execute('''CREATE TRIGGER IF NOT EXISTS pm_quality_reviews_delete_immutable
            BEFORE DELETE ON pm_quality_reviews BEGIN SELECT RAISE(ABORT,'Quality review context is immutable'); END''')
        for verb in ('UPDATE', 'DELETE'):
            journal.db.execute(f'CREATE TRIGGER IF NOT EXISTS pm_quality_review_records_{verb.lower()}_immutable '
                f"BEFORE {verb} ON pm_quality_review_records BEGIN SELECT RAISE(ABORT,'Quality assessment history is immutable'); END")


def install(ops):
    schema(ops.journal)
    for action in ACTIONS:
        if action not in ops.actions:
            ops.register(ActionDef(action, 'manage', 'Review original acceptance and Definition of Done', _run,
                _admitter(action), ('workspace_id', 'work_item_id' if action == CREATE else 'review_id'), authorize_existing=_authorize_existing))


def capabilities():
    return {'actions': list(ACTIONS), 'version': 1, 'verification_profiles': sorted(sources.verification.PROFILES),
        'max_criteria': sources.MAX_CRITERIA, 'max_assessments': MAX_HISTORY, 'max_records': MAX_HISTORY + 1, 'responsibility': 'owner_appointed_original_reviewer',
        'verification_alone_is_acceptance': False, 'execution_authorized': False}


def _member(ops, principal, wid, *, write=False, decide=False):
    if not principal.allows('observe') or write and not principal.allows('manage'):
        raise sources.bad('Quality reads need observe; records also need manage', 'FORBIDDEN', 403)
    return pm_workspace.require_member(ops.db, principal, wid, write=write, decide=decide)


def _op(ops, oid):
    row = ops.db.execute('SELECT * FROM operations WHERE operation_id=?', (oid,)).fetchone()
    if not row:
        raise sources.bad('Original quality operation is unavailable', 'NOT_FOUND', 404)
    return OperationService._decode(row)


def _review(ops, wid, rid):
    if not isinstance(rid, str) or not REVIEW.fullmatch(rid):
        raise sources.bad('Quality review is unavailable', 'NOT_FOUND', 404)
    row = ops.db.execute('SELECT * FROM pm_quality_reviews WHERE workspace_id=? AND review_id=?', (wid, rid)).fetchone()
    if row is None:
        raise sources.bad('Quality review is unavailable', 'NOT_FOUND', 404)
    row = dict(row)
    row['context'] = json.loads(row['context'])
    binding = (_op(ops, row['operation_id']).get('external_refs') or {}).get('admission_binding') or {}
    if digest(row['context']) != row['context_digest'] or binding.get('quality_context') != row['context']:
        raise sources.bad('Original quality context changed', 'PM_QUALITY_CORRUPT', 409)
    return row


def _head(ops, wid, subject_key):
    row = ops.db.execute('SELECT review_id FROM pm_quality_review_heads WHERE workspace_id=? AND subject_key=?', (wid, subject_key)).fetchone()
    return row[0] if row else None


def _records(ops, row):
    result = []
    for value in ops.db.execute('SELECT * FROM pm_quality_review_records WHERE review_id=? ORDER BY version', (row['review_id'],)):
        value = dict(value)
        value['document'] = json.loads(value['document'])
        if digest(value['document']) != value['document_digest']:
            raise sources.bad('An original assessment changed', 'PM_QUALITY_CORRUPT', 409)
        result.append(value)
    if len(result) > MAX_HISTORY and not (len(result) == MAX_HISTORY + 1 and result[-1]['kind'] == 'revocation'):
        raise sources.bad('The original assessment history exceeds its bound', 'PM_QUALITY_CORRUPT', 409)
    return result


def _access(ops, principal, row):
    from . import pm_source_lineage
    from .pm_maintenance_repairs import source_backedge
    from .pm_source_reads import check, scope
    with scope(ops.db, principal):
        _member(ops, principal, row['workspace_id'])
        original = _review(ops, row['workspace_id'], row['review_id'])
        if row != original:
            raise sources.bad('Original quality context changed', 'NOT_FOUND', 404)
        history = _source_history(ops, original)
        def visible():
            for current in history:
                sources.access(ops, principal, original['workspace_id'], current['context'])
                for record in _records(ops, current):
                    if record['kind'] == 'assessment':
                        sources.proof_access(ops, principal, original['workspace_id'], record['document'])
            return True
        # A saved assessment can return through its owned execution to the
        # repair whose complete sources are already being checked. That exact
        # domain backedge is not a completed authorization and must not be cached.
        for root in pm_source_lineage.roots(ops.db, 'operation', original['operation_id']):
            if source_backedge(ops.db, principal, root):
                op = ops.db.execute('SELECT action FROM operations WHERE operation_id=?', (root,)).fetchone()
                if op and op[0] == 'pm.maintenance.repair':
                    visible()
                    return len(history)
        if not check(ops.db, principal, 'quality_sources', original['review_id'], visible):
            raise sources.bad('Original quality sources are unavailable', 'NOT_FOUND', 404)
        return len(history)


def _source_history(ops, row):
    """Rebuild bounded canonical history; only its source boolean is reusable."""
    current, seen, result = row, set(), []
    while current:
        if current['review_id'] in seen or len(seen) >= MAX_HISTORY:
            raise sources.bad('The original quality history exceeds its bound', 'PM_QUALITY_LIMIT', 409)
        seen.add(current['review_id'])
        result.append(current)
        previous = current['context']['previous_review_id']
        current = _review(ops, row['workspace_id'], previous) if previous else None
    return result


def _create_input(ops, principal, target, params, pre):
    sources.exact(target, ('workspace_id', 'work_item_id'))
    sources.exact(params, ('package_ref', 'execution_operation_id', 'candidate_commit', 'dod_revision_id', 'reviewer_actor', 'criteria', 'rationale'))
    sources.exact(pre, ('expected_previous_review_id',))
    wid = target['workspace_id']
    _member(ops, principal, wid, write=True, decide=True)
    if not isinstance(target['work_item_id'], str) or not work_items.WORK_ITEM_ID.fullmatch(target['work_item_id']):
        raise sources.bad('Select an original Connector WorkItem')
    sources.text(params['rationale'], 'Policy rationale')
    reviewer = sources.text(params['reviewer_actor'], 'Responsible reviewer actor', 200)
    if pm_sprints._role(ops.db, wid, reviewer) not in {'owner', 'contributor'}:
        raise sources.bad('Appoint one current workspace writer as the responsible reviewer')
    context = sources.subject(ops, principal, wid, target['work_item_id'], params)
    subject = digest({'package_ref': context['package_ref'], 'candidate_commit': context['native_result']['candidate_commit'],
        'dod_revision_id': context['dod_ref']['revision_id'] if context['dod_ref'] else None})
    previous = _head(ops, wid, subject)
    if pre['expected_previous_review_id'] != previous:
        raise sources.bad('The original review policy head changed', 'PM_QUALITY_VERSION_CONFLICT', 409)
    if previous:
        old = _review(ops, wid, previous)
        if _access(ops, principal, old) >= MAX_HISTORY:
            raise sources.bad('The original review history cannot be extended', 'PM_QUALITY_LIMIT', 409)
        context['operation_ids'] = sorted(set(context['operation_ids']) | {old['operation_id']})
    context.update(reviewer_actor=reviewer, appointed_by=principal.actor, self_review=reviewer == _op(ops, context['execution_operation_id'])['actor'],
        policy_rationale=params['rationale'], previous_review_id=previous, subject_key=subject)
    _bounded({'context': context})
    return context


def _record_input(ops, principal, action, target, params, pre):
    sources.exact(target, ('workspace_id', 'review_id'))
    sources.exact(pre, ('expected_version',))
    if type(pre['expected_version']) is not int:
        raise sources.bad('Select the exact original review version')
    row = _review(ops, target['workspace_id'], target['review_id'])
    member = _member(ops, principal, row['workspace_id'], write=True)
    _access(ops, principal, row)
    if row['version'] != pre['expected_version'] or _head(ops, row['workspace_id'], row['subject_key']) != row['review_id']:
        raise sources.bad('The original review revision or policy head changed', 'PM_QUALITY_VERSION_CONFLICT', 409)
    history = _records(ops, row)
    if history and history[-1]['kind'] == 'revocation':
        raise sources.bad('An explicitly revoked review cannot receive new conclusions', 'PM_QUALITY_REVOKED', 409)
    if action != REVOKE and len(history) >= MAX_HISTORY:
        raise sources.bad('The original review assessment history is full', 'PM_QUALITY_LIMIT', 409)
    reviewer = row['context']['reviewer_actor']
    if principal.actor != reviewer and not (action == REVOKE and member['membership']['role'] == 'owner'):
        raise sources.bad('Only the original responsible reviewer may record conclusions', 'FORBIDDEN', 403)
    if action == REVOKE:
        sources.exact(params, ('reason',))
        result = {'reason': sources.text(params['reason'], 'Revocation reason'), 'qualified': False}
    else:
        sources.exact(params, ('conclusions', 'rationale'))
        sources.text(params['rationale'], 'Assessment rationale')
        result = {**sources.conclusions(ops, principal, row['workspace_id'], row['context'], params['conclusions']), 'rationale': params['rationale']}
    _bounded({'review_id': row['review_id'], 'context': row['context'], 'assessment': result})
    return row, result


def _admitter(action):
    def admit(ops, principal, target, params, pre):
        with ops.journal.tx():
            binding = {'principal': pm_workspace.principal_binding(principal), 'pm_lineage': {'workspace_id': target.get('workspace_id')}}
            if action == CREATE:
                context = _create_input(ops, principal, target, params, pre)
                binding.update(quality_context=context, quality_context_digest=digest(context))
            else:
                row, result = _record_input(ops, principal, action, target, params, pre)
                intent = {'review_id': row['review_id'], 'version': row['version'], 'document': result,
                    'operation_ids': [row['operation_id']]}
                binding.update(quality_record=intent, quality_record_digest=digest(intent))
            return binding
    return admit


async def _run(ctx):
    def apply():
        ops, principal = ctx.service, pm_workspace.admitted_principal(ctx)
        now = time.time()
        if ctx.op['action'] == CREATE:
            context = _create_input(ops, principal, ctx.target, ctx.params, ctx.preconditions)
            if context != ctx.admission_binding['quality_context'] or digest(context) != ctx.admission_binding['quality_context_digest']:
                raise sources.bad('Original review sources or responsibility changed', 'PM_QUALITY_CONTEXT_CHANGED', 409)
            rid, wid = 'pmqr_' + ctx.operation_id[3:], ctx.target['workspace_id']
            ops.db.execute('INSERT INTO pm_quality_reviews(review_id,operation_id,workspace_id,work_item_id,subject_key,context,context_digest,created_at) VALUES(?,?,?,?,?,?,?,?)',
                (rid, ctx.operation_id, wid, ctx.target['work_item_id'], context['subject_key'], _json(context), digest(context), now))
            ops.db.execute('INSERT INTO pm_quality_review_heads VALUES(?,?,?) ON CONFLICT(workspace_id,subject_key) DO UPDATE SET review_id=excluded.review_id',
                (wid, context['subject_key'], rid))
            result = {'workspace_id': wid, 'review_id': rid, 'version': 1, 'qualified': False, 'assessment': 'unassessed'}
        else:
            row, document = _record_input(ops, principal, ctx.op['action'], ctx.target, ctx.params, ctx.preconditions)
            saved = ctx.admission_binding['quality_record']
            if digest(saved) != ctx.admission_binding['quality_record_digest'] or saved['document'] != document:
                raise sources.bad('Original selected proof facts changed', 'PM_QUALITY_EVIDENCE_CHANGED', 409)
            cur = ops.db.execute('UPDATE pm_quality_reviews SET version=version+1 WHERE review_id=? AND version=?', (row['review_id'], row['version']))
            if cur.rowcount != 1:
                raise sources.bad('The review revision changed', 'PM_QUALITY_VERSION_CONFLICT', 409)
            kind = 'assessment' if ctx.op['action'] == RECORD else 'revocation'
            ops.db.execute('INSERT INTO pm_quality_review_records(review_id,operation_id,version,kind,document,document_digest,actor,created_at) VALUES(?,?,?,?,?,?,?,?)',
                (row['review_id'], ctx.operation_id, row['version'] + 1, kind, _json(document), digest(document), principal.actor, now))
            result = {'workspace_id': row['workspace_id'], 'review_id': row['review_id'], 'version': row['version'] + 1,
                'qualified': document['qualified'], 'kind': kind, 'document_digest': digest(document)}
        # Refuse an unreadable whole publication atomically, including metadata
        # and history that were not present in the caller's admission document.
        _publication(ops, principal, result['workspace_id'], result['review_id'], reserve_revocation=ctx.op['action'] != REVOKE)
        ops.journal.api_event('pm_quality_review', result['review_id'], ctx.op['action'], {**result, 'operation_id': ctx.operation_id}, actor=ctx.actor)
        return result
    return ctx.effect('pm.quality.record', apply)


class _Reader:
    def __init__(self, db):
        self.db = db
        self.journal = self

    def tx(self):
        return snapshots._snapshot(self.db)

    def get(self, oid, **kwargs):
        return _op(self, oid)


def operation_visible(db, principal, operation):
    if not installed(db):
        return False
    with snapshots._snapshot(db):
        try:
            ops = _Reader(db)
            op = _op(ops, operation['operation_id'])
            binding = op['external_refs']['admission_binding']
            if op['action'] == CREATE:
                context = binding['quality_context']
                if digest(context) != binding['quality_context_digest']:
                    return False
                _member(ops, principal, op['target']['workspace_id'])
                sources.access(ops, principal, op['target']['workspace_id'], context)
                if context['previous_review_id']:
                    _access(ops, principal, _review(ops, op['target']['workspace_id'], context['previous_review_id']))
            elif op['action'] in {RECORD, REVOKE}:
                saved = binding['quality_record']
                if digest(saved) != binding['quality_record_digest']:
                    return False
                _access(ops, principal, _review(ops, op['target']['workspace_id'], saved['review_id']))
                if op['action'] == RECORD:
                    sources.proof_access(ops, principal, op['target']['workspace_id'], saved['document'])
            else:
                return False
            return True
        except (OperationError, KeyError, TypeError, ValueError):
            return False


def _authorize_existing(ops, principal, op, verb):
    if not operation_visible(ops.db, principal, op):
        raise sources.bad('Original quality sources are unavailable', 'NOT_FOUND', 404)
    _member(ops, principal, op['target']['workspace_id'], write=True)
    if verb != 'replay' and op['actor'] != principal.actor:
        raise sources.bad('Only the original requester may steer this operation', 'FORBIDDEN', 403)


def _view(ops, principal, row, *, constraints=None):
    history = _records(ops, row)
    latest = next((value for value in reversed(history) if value['kind'] == 'assessment'), None)
    context = row['context']
    reasons = []
    if _head(ops, row['workspace_id'], row['subject_key']) != row['review_id']:
        reasons.append('review_superseded')
    if history and history[-1]['kind'] == 'revocation':
        reasons.append('review_revoked')
    if pm_sprints._role(ops.db, row['workspace_id'], context['reviewer_actor']) not in {'owner', 'contributor'}:
        reasons.append('review_responsibility_unavailable')
    if latest and not sources.proof_current(ops, principal, row['workspace_id'], context, latest['document']):
        reasons.append('original_verification_no_longer_proven')
    if constraints:
        matches = {'binding_id': context['work_ref']['binding_id'], 'scope_revision': context['plan_ref']['scope_revision'],
            'dod_revision_id': context['dod_ref']['revision_id'] if context['dod_ref'] else None,
            'candidate_commit': context['native_result']['candidate_commit']}
        for key, value in constraints.items():
            if matches[key] != value:
                reasons.append(key + '_changed')
    original = latest['document'] if latest else {'acceptance': 'unassessed', 'dod': 'unassessed' if context['dod_ref'] else 'not_defined', 'qualified': False}
    return {key: row[key] for key in ('sequence', 'review_id', 'operation_id', 'workspace_id', 'work_item_id', 'created_at', 'version', 'context_digest')} | {
        'context': context, 'assessment': latest, 'revocation': history[-1] if history and history[-1]['kind'] == 'revocation' else None, 'history': [{key: value[key] for key in ('sequence', 'operation_id', 'version', 'kind', 'document_digest', 'actor', 'created_at')} for value in history],
        'original_outcome': {key: original[key] for key in ('acceptance', 'dod', 'qualified')},
        'applicable': not reasons, 'applicability_reasons': reasons,
        'acceptance': original['acceptance'] if not reasons else 'unassessed', 'dod': original['dod'] if not reasons else 'unassessed',
        'qualified': original['qualified'] and not reasons}


def _publication(ops, principal, wid, rid, *, reserve_revocation):
    payload = get_review(ops, principal, wid, rid)
    if not reserve_revocation:
        return
    # Keep one terminal revocation possible at both the history and byte limits.
    # These are size reservations only, never stored or returned as evidence.
    value = payload['review']
    terminal = {'sequence': 2**63 - 1, 'review_id': rid, 'operation_id': 'op_' + 'f' * 32,
        'version': MAX_HISTORY + 2, 'kind': 'revocation', 'document_digest': 'f' * 64,
        'actor': 'a' * 64, 'created_at': 1.7976931348623157e308,
        # JSON control-character escapes are wider than any UTF-8 code point.
        'document': {'reason': '\x01' * 4000, 'qualified': False}}
    reserved = {**value, 'version': MAX_HISTORY + 2, 'revocation': terminal,
        'history': [*value['history'], {key: item for key, item in terminal.items() if key not in {'review_id', 'document'}}],
        'applicable': False, 'qualified': False, 'acceptance': 'unassessed', 'dod': 'unassessed',
        'applicability_reasons': ['review_revoked', 'review_superseded', 'review_responsibility_unavailable',
            'original_verification_no_longer_proven', 'work_binding_changed', 'scope_revision_changed',
            'binding_id_changed', 'dod_revision_id_changed', 'candidate_commit_changed']}
    _bounded({'workspace_id': wid, 'review': reserved})
    _bounded({'workspace_id': wid, 'reviews': [reserved], 'next_cursor': 2**63 - 1,
        'visibility_filtered': False, **_work_envelope(value['work_item_id'])})


def _work_envelope(work_item_id):
    return {'work_item_id': work_item_id, 'aggregation': 'explicit_original_review_selection_required',
        'requirement_counting': 'distinct_original_scope_and_requirement_only'}


@snapshots._snapshot_read
def evaluate_review(ops, principal, workspace_id, review_id, *, binding_id, scope_revision, dod_revision_id, candidate_commit):
    row = _review(ops, workspace_id, review_id)
    _access(ops, principal, row)
    return _bounded({'workspace_id': workspace_id, 'review': _view(ops, principal, row, constraints={
        'binding_id': binding_id, 'scope_revision': scope_revision, 'dod_revision_id': dod_revision_id, 'candidate_commit': candidate_commit})})


@snapshots._snapshot_read
def get_review(ops, principal, workspace_id, review_id):
    row = _review(ops, workspace_id, review_id)
    _access(ops, principal, row)
    return _bounded({'workspace_id': workspace_id, 'review': _view(ops, principal, row)})


@snapshots._snapshot_read
def get_assessment(ops, principal, workspace_id, review_id, *, operation_id, version, document_digest):
    """Return one exact original assessment, never a newer outcome substitution."""
    if (not isinstance(operation_id, str) or not re.fullmatch('op_[0-9a-f]{32}', operation_id)
            or type(version) is not int or not 2 <= version <= MAX_HISTORY + 1
            or not isinstance(document_digest, str) or not sources.verification.DIGEST.fullmatch(document_digest)):
        raise sources.bad('Select the exact original assessment receipt')
    row = _review(ops, workspace_id, review_id)
    _access(ops, principal, row)
    record = next((value for value in _records(ops, row) if value['kind'] == 'assessment'
        and value['operation_id'] == operation_id and value['version'] == version
        and value['document_digest'] == document_digest), None)
    if record is None:
        raise sources.bad('The original quality assessment is unavailable', 'NOT_FOUND', 404)
    return _bounded({'workspace_id': workspace_id, 'review_id': review_id, 'context': row['context'],
        'context_digest': row['context_digest'], 'assessment': record,
        'semantics': 'immutable_original_assessment', 'current_applicability': 'not_evaluated'})


def _list_reviews(ops, principal, workspace_id, *, after, limit, work_item_id=None, extra=None, work_context=None):
    _member(ops, principal, workspace_id)
    if type(after) is not int or not 0 <= after < 2**63 or type(limit) is not int or not 1 <= limit <= 100:
        raise sources.bad('Use a signed 64-bit cursor and page size 1–100')
    if work_item_id is not None and (not isinstance(work_item_id, str) or not work_items.WORK_ITEM_ID.fullmatch(work_item_id)):
        raise sources.bad('Select an original Connector WorkItem')
    rows = ops.db.execute('SELECT review_id,sequence FROM pm_quality_reviews WHERE workspace_id=? AND sequence>? '
        'AND (? IS NULL OR work_item_id=?) ORDER BY sequence LIMIT ?', (workspace_id, after, work_item_id, work_item_id, limit + 1)).fetchall()
    result = {'workspace_id': workspace_id, 'reviews': [], 'next_cursor': None, 'visibility_filtered': False, **(extra or {})}
    scanned = after
    for index, stub in enumerate(rows[:limit]):
        row = _review(ops, workspace_id, stub['review_id'])
        # A known final page uses null; an intermediate prefix reserves the wider
        # of its actual cursor and a final null after later invisible rows.
        cursor = row['sequence'] if index + 1 < len(rows) and row['sequence'] >= 1000 else None
        try:
            _access(ops, principal, row)
        except OperationError as exc:
            if exc.status != 404:
                raise
            candidate = {**result, 'visibility_filtered': True, 'next_cursor': cursor}
            if _size(candidate) > MAX_BYTES:
                if scanned == after:
                    raise sources.bad('The whole list envelope exceeds the read budget', 'PM_QUALITY_LIMIT', 413) from exc
                result['next_cursor'] = scanned
                return _bounded(result)
            result['visibility_filtered'] = True
            scanned = row['sequence']
            continue
        item = _view(ops, principal, row)
        if work_context is not None:
            binding, scope = work_context
            if item['context']['work_ref']['binding_id'] != binding:
                item['applicability_reasons'].append('work_binding_changed')
            if item['context']['plan_ref']['scope_revision'] != scope:
                item['applicability_reasons'].append('scope_revision_changed')
            if item['applicability_reasons']:
                item.update(applicable=False, qualified=False, acceptance='unassessed', dod='unassessed')
        if _size({**result, 'reviews': [*result['reviews'], item], 'next_cursor': cursor}) > MAX_BYTES:
            if scanned == after:
                raise sources.bad('One whole review exceeds the read budget', 'PM_QUALITY_LIMIT', 413)
            result['next_cursor'] = scanned
            return _bounded(result)
        result['reviews'].append(item)
        scanned = row['sequence']
    result['next_cursor'] = scanned if len(rows) > limit else None
    return _bounded(result)


@snapshots._snapshot_read
def list_reviews(ops, principal, workspace_id, *, after=0, limit=50, work_item_id=None):
    return _list_reviews(ops, principal, workspace_id, after=after, limit=limit, work_item_id=work_item_id)


@snapshots._snapshot_read
def for_work(ops, principal, workspace_id, work_item_id, *, after=0, limit=50):
    _member(ops, principal, workspace_id)
    current = pm_evidence._binding(ops.db, workspace_id, work_item_id)
    plan = pm_plans.accepted_plan(ops, principal, workspace_id)['plan']
    scope = plan['adoption']['scope_revision'] if plan and plan['adoption'] else None
    return _list_reviews(ops, principal, workspace_id, after=after, limit=limit, work_item_id=work_item_id,
        work_context=(current['binding_id'] if current else None, scope),
        extra=_work_envelope(work_item_id))
