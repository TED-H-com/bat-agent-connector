"""One fixed maintenance report and explicit, attributed review decisions.

Preparing a report is a local observation. Discussion, decisions and follow-up
remain separate records; no command here adopts scope, repairs or starts a model.
"""
from __future__ import annotations

import json
import re
import time
from urllib.parse import urlsplit

from . import pm_maintenance_review_sources as sources
from . import pm_plans, pm_snapshots, pm_sprints, pm_workspace
from . import pm_release_baselines as baselines
from . import pm_workload as workload
from .operations import ActionDef, OperationError, OperationService

PREFIX = 'pm.maintenance_review.'
ACTIONS = tuple(PREFIX + name for name in ('prepare', 'note', 'candidate.record', 'complete', 'follow_up.record'))
REVIEW = re.compile(r'pmrevw_[0-9a-f]{32}')
EVENT = re.compile(r'pmrve_[0-9a-f]{32}')
MAX_READ_BYTES = 2_000_000
MAX_EVENT_BYTES = 100_000
MAX_PERIOD_SECONDS = 31 * 86400
DIRECTIONS = ('continue_maintenance', 'bounded_repair_proposal', 'explore_idea', 'request_planning',
              'revise_policy', 'pause_proposal', 'close_proposal', 'defer')


def _bad(code, message, status=422):
    return OperationError(code, message, status)


def _exact(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= set(value) or set(value) - set(required) - set(optional):
        raise _bad('INVALID_PARAMS', 'Use the exact maintenance review fields')


def _text(value, label, maximum=4000):
    return pm_sprints._text(value, label, maximum)


def _integer(value, label):
    if type(value) is not int or not 0 <= value < 2**63:
        raise _bad('INVALID_PARAMS', label + ' must be a nonnegative signed 64-bit integer')
    return value


def _items(value, label, maximum=20, minimum=0):
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise _bad('INVALID_PARAMS', f'{label} needs {minimum}–{maximum} entries')
    return value


def _texts(value, label, maximum=10):
    return [_text(item, label) for item in _items(value, label, maximum)]


def _bounded(value, *, maximum=None):
    if baselines._size(value) > (MAX_READ_BYTES if maximum is None else maximum):
        raise _bad('PM_MAINTENANCE_REVIEW_TOO_LARGE', 'The complete review record exceeds its byte bound', 413)
    return value


def _member(ops, principal, wid, *, write=False, owner=False):
    if not principal.allows('observe') or write and not principal.allows('manage'):
        raise _bad('FORBIDDEN', 'Reviews require observe; recording also requires manage', 403)
    workspace = pm_workspace.require_member(ops.db, principal, wid, write=write)
    if owner:
        actual = ops.db.execute('SELECT role FROM pm_workspace_members WHERE workspace_id=? AND actor=?', (wid, principal.actor)).fetchone()
        if actual is None or actual[0] != 'owner':
            raise _bad('FORBIDDEN', 'Only a current actual workspace owner can complete this review', 403)
    return workspace


def schema(journal):
    pm_snapshots.schema(journal)
    with journal.tx():
        journal.db.execute('''CREATE TABLE IF NOT EXISTS pm_maintenance_reviews (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, review_id TEXT NOT NULL UNIQUE,
            workspace_id TEXT NOT NULL, revision INTEGER NOT NULL, title TEXT NOT NULL,
            snapshot_id TEXT NOT NULL UNIQUE, document TEXT NOT NULL, document_digest TEXT NOT NULL,
            source_binding TEXT NOT NULL, actor TEXT NOT NULL, created_at REAL NOT NULL,
            operation_id TEXT NOT NULL UNIQUE, UNIQUE(workspace_id,revision))''')
        journal.db.execute('''CREATE TABLE IF NOT EXISTS pm_maintenance_review_state (
            review_id TEXT PRIMARY KEY, version INTEGER NOT NULL, status TEXT NOT NULL, completion_id TEXT)''')
        journal.db.execute('''CREATE TABLE IF NOT EXISTS pm_maintenance_review_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
            review_id TEXT NOT NULL, workspace_id TEXT NOT NULL, kind TEXT NOT NULL,
            document TEXT NOT NULL, document_digest TEXT NOT NULL, source_binding TEXT NOT NULL,
            actor TEXT NOT NULL, created_at REAL NOT NULL, operation_id TEXT NOT NULL UNIQUE)''')
        for table in ('pm_maintenance_reviews', 'pm_maintenance_review_events'):
            for verb in ('UPDATE', 'DELETE'):
                journal.db.execute(f'CREATE TRIGGER IF NOT EXISTS {table}_{verb.lower()}_immutable '
                    f'BEFORE {verb} ON {table} '
                    "BEGIN SELECT RAISE(ABORT, 'Maintenance review history is immutable'); END")


def install(ops):
    schema(ops.journal)
    from . import pm_maintenance_review_schedule
    pm_maintenance_review_schedule.schema(ops.journal)
    for action in ACTIONS:
        if action not in ops.actions:
            target = ('workspace_id',) if action == PREFIX + 'prepare' else ('workspace_id', 'review_id')
            ops.register(ActionDef(action, 'manage', 'Record a sourced maintenance review without adopting or executing work',
                _run, _admitter(action), target, authorize_existing=_authorize_existing))


def capabilities():
    return {'version': 1, 'actions': list(ACTIONS), 'deterministic_report': True, 'model_required': False,
        'planning_candidate_source': {'version': 1, 'kind': 'maintenance_review_candidate', 'initial_status': 'unresolved'},
        'period_max_seconds': MAX_PERIOD_SECONDS, 'automatic_scope_adoption': False,
        'automatic_meetings': False, 'research_verification': 'attributed_source_claims_only'}


def head(db, wid):
    row = db.execute('SELECT review_id,revision FROM pm_maintenance_reviews WHERE workspace_id=? ORDER BY revision DESC LIMIT 1', (wid,)).fetchone()
    return dict(row) if row else {'review_id': None, 'revision': 0}


def _decode(row):
    result = dict(row)
    try:
        result['document'] = json.loads(row['document'])
        result['source_binding'] = json.loads(row['source_binding'])
        if (baselines.sources.digest(result['document']) != row['document_digest']
                or baselines.sources.digest(result['source_binding']) != result['document']['source_binding_digest']):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise _bad('PM_MAINTENANCE_REVIEW_CORRUPT', 'Original review record cannot be verified', 409) from None
    return result


def _row(db, wid, rid):
    if not isinstance(rid, str) or not REVIEW.fullmatch(rid):
        raise _bad('NOT_FOUND', 'Maintenance review unavailable', 404)
    row = db.execute('''SELECT r.*,s.version,s.status,s.completion_id FROM pm_maintenance_reviews r
        JOIN pm_maintenance_review_state s USING(review_id) WHERE r.workspace_id=? AND r.review_id=?''', (wid, rid)).fetchone()
    if row is None:
        raise _bad('NOT_FOUND', 'Maintenance review unavailable', 404)
    return _decode(row)


def _event(db, wid, rid, eid):
    if not isinstance(eid, str) or not EVENT.fullmatch(eid):
        raise _bad('NOT_FOUND', 'Review event unavailable', 404)
    row = db.execute('SELECT * FROM pm_maintenance_review_events WHERE workspace_id=? AND review_id=? AND event_id=?',
        (wid, rid, eid)).fetchone()
    if row is None:
        raise _bad('NOT_FOUND', 'Review event unavailable', 404)
    return _decode(row)


def _access(ops, principal, row):
    sources.access(ops, principal, row['workspace_id'], row['source_binding'])


def _snapshot(ops, principal, row):
    _access(ops, principal, row)
    stored = ops.db.execute('SELECT * FROM pm_document_snapshots WHERE workspace_id=? AND snapshot_id=?',
        (row['workspace_id'], row['snapshot_id'])).fetchone()
    if stored is None:
        raise _bad('NOT_FOUND', 'Original complete review snapshot unavailable', 404)
    snapshot = pm_snapshots._out(ops, principal, stored)['snapshot']
    if (snapshot['snapshot_digest'] != row['document']['snapshot_ref']['snapshot_digest'] or snapshot['visibility']['filtered']):
        raise _bad('NOT_FOUND', 'Original complete review snapshot unavailable', 404)
    return snapshot


def _previous(ops, principal, wid, rid):
    if rid is None:
        return None
    row = _row(ops.db, wid, rid)
    snapshot = _snapshot(ops, principal, row)
    events = ops.db.execute('''SELECT * FROM pm_maintenance_review_events
        WHERE workspace_id=? AND review_id=? ORDER BY sequence LIMIT 101''', (wid, rid)).fetchall()
    refs = [*row['source_binding'], sources.ref('snapshot_identity', snapshot['snapshot_id'], snapshot['snapshot_digest'])]
    notes, candidates, results, completion = [], [], [], None
    for event in events[:100]:
        saved = _decode(event)
        _access(ops, principal, saved)
        refs.extend(saved['source_binding'])
        view = _event_view(saved)
        if saved['kind'] == 'note':
            notes.append(view)
        elif saved['kind'] == 'candidate.record':
            candidates.append(view)
        elif saved['kind'] == 'follow_up.record':
            results.append(view)
        elif saved['kind'] == 'complete':
            completion = view
    if row['completion_id'] and completion is None:
        saved = _event(ops.db, wid, rid, row['completion_id'])
        _access(ops, principal, saved)
        refs.extend(saved['source_binding'])
        completion = _event_view(saved)
    # All discussion content in the comparison has exact original authors and IDs.
    context = {'review_id': rid, 'version': row['version'], 'status': row['status'],
        'snapshot_ref': row['document']['snapshot_ref'], 'notes': notes, 'candidates': candidates,
        'completion': completion, 'follow_up_results': results, 'history_truncated': len(events) > 100,
        'unattended_report_remains_pending': row['status'] == 'prepared'}
    _bounded(context, maximum=300_000)
    return {'context': context, 'report': snapshot['maintenance']['report'], 'access_refs': sources.unique(refs)}


def _prepare_check(ops, principal, target, params, pre):
    _exact(target, {'workspace_id'})
    _exact(params, {'title', 'period_start', 'period_end'}, {'previous_review_id'})
    _exact(pre, {'expected_plan_version', 'expected_review_revision', 'expected_policy_id', 'expected_policy_version',
        'expected_baseline_id', 'expected_lifecycle_revision'})
    wid = target['workspace_id']
    workspace = _member(ops, principal, wid, write=True)
    _text(params['title'], 'report title', 200)
    start, end = sources.timestamp(params['period_start'], 'period_start'), sources.timestamp(params['period_end'], 'period_end')
    now = time.time()
    if not 0 < end - start <= MAX_PERIOD_SECONDS or end > now:
        raise _bad('INVALID_PARAMS', 'Select a past half-open period of at most 31 days')
    _integer(pre['expected_plan_version'], 'plan version')
    _integer(pre['expected_review_revision'], 'review revision')
    plan_head = pm_plans.head(ops.db, wid)
    if plan_head['version'] != pre['expected_plan_version'] or head(ops.db, wid)['revision'] != pre['expected_review_revision']:
        raise _bad('VERSION_CONFLICT', 'Read the current plan and maintenance review revisions', 409)
    intent = workload._check(ops, principal, target, {'policy_id': pre['expected_policy_id']},
        {key: pre[key] for key in ('expected_policy_version', 'expected_baseline_id', 'expected_lifecycle_revision')})
    previous = _previous(ops, principal, wid, params.get('previous_review_id'))
    source_refs = [sources.ref('policy', intent['policy_id'], intent['document_digest']),
        sources.ref('baseline', intent['baseline_id'], intent['baseline_digest'])]
    if previous:
        source_refs.extend(previous['access_refs'])
    fixed = {'workload': intent, 'plan_version': plan_head['version'], 'review_revision': pre['expected_review_revision'],
        'previous': {key: previous['context'][key] for key in ('review_id', 'version', 'snapshot_ref')} if previous else None}
    return workspace, plan_head, fixed, previous, sources.unique(source_refs)


def _source_ids(snapshot, value, *, minimum=0):
    values = _items(value, 'source references', 20, minimum)
    available = {item['source_id'] for item in snapshot['maintenance']['report']['sources']}
    if any(not isinstance(item, str) or item not in available for item in values) or len(values) != len(set(values)):
        raise _bad('INVALID_PARAMS', 'Select distinct original report source IDs')
    return values


def _follow_ups(ops, wid, snapshot, values):
    result, seen = [], set()
    for item in _items(values, 'follow ups', 10):
        _exact(item, {'id', 'text', 'owner_actor', 'expected_effect', 'verification', 'source_refs'})
        ident = item['id']
        if not isinstance(ident, str) or not re.fullmatch('[A-Za-z0-9_-]{1,64}', ident) or ident in seen:
            raise _bad('INVALID_PARAMS', 'Use distinct short follow-up keys')
        seen.add(ident)
        _text(item['owner_actor'], 'follow-up owner', 200)
        if pm_sprints._role(ops.db, wid, item['owner_actor']) not in {'owner', 'contributor'}:
            raise _bad('INVALID_PARAMS', 'Follow-up owners must be current workspace writers')
        result.append({'id': ident, 'text': _text(item['text'], 'follow-up'), 'owner_actor': item['owner_actor'],
            'expected_effect': _text(item['expected_effect'], 'expected effect'),
            'verification': _text(item['verification'], 'verification method'),
            'source_refs': _source_ids(snapshot, item['source_refs']), 'status': 'open',
            'verification_status': 'unassessed', 'assignment_accepted': 'not_proven'})
    return result


def _public_sources(values, now):
    result = []
    for item in _items(values, 'public source claims', 5):
        _exact(item, {'url', 'title', 'checked_at', 'claim'})
        url = _text(item['url'], 'public source URL', 2000)
        try:
            parsed = urlsplit(url)
            valid = parsed.scheme == 'https' and parsed.hostname and not (parsed.username or parsed.password or parsed.fragment)
        except ValueError:
            valid = False
        if not valid:
            raise _bad('INVALID_PARAMS', 'Use an HTTPS source reference without credentials or fragments')
        checked_at = sources.timestamp(item['checked_at'], 'source check date')
        if checked_at > now:
            raise _bad('INVALID_PARAMS', 'A source check date cannot be in the future')
        result.append({'url': url, 'title': _text(item['title'], 'source title', 200), 'checked_at': checked_at,
            'claim': _text(item['claim'], 'source claim'), 'verification': 'attributed_unverified_claim',
            'fetched_by_this_operation': False})
    return result


def _mutation_check(ops, principal, action, target, params, pre):
    _exact(target, {'workspace_id', 'review_id'})
    _exact(pre, {'expected_version'})
    _integer(pre['expected_version'], 'review version')
    wid, rid = target['workspace_id'], target['review_id']
    _member(ops, principal, wid, write=True, owner=action == PREFIX + 'complete')
    row = _row(ops.db, wid, rid)
    snapshot = _snapshot(ops, principal, row)
    if row['version'] != pre['expected_version']:
        raise _bad('VERSION_CONFLICT', 'Read the current review version before recording', 409)
    if row['status'] == 'completed' and action != PREFIX + 'follow_up.record':
        raise _bad('PM_MAINTENANCE_REVIEW_COMPLETED', 'The original completed review cannot be rewritten', 409)
    if not isinstance(params, dict):
        raise _bad('INVALID_PARAMS', 'Use an object for review fields')
    _text(params.get('rationale'), 'rationale')
    refs = list(row['source_binding'])
    if action == PREFIX + 'note':
        _exact(params, {'content', 'rationale'}, {'source_refs'})
        document = {'content': _text(params['content'], 'discussion note', 8000),
            'source_refs': _source_ids(snapshot, params.get('source_refs', [])), 'source_status': 'attributed_discussion'}
    elif action == PREFIX + 'candidate.record':
        _exact(params, {'title', 'problem', 'goal_relation', 'expected_effect', 'options', 'hypotheses', 'source_refs', 'rationale'}, {'public_sources'})
        document = {'title': _text(params['title'], 'candidate title', 200), 'problem': _text(params['problem'], 'observed problem'),
            'goal_relation': _text(params['goal_relation'], 'relationship to the project goal'),
            'expected_effect': _text(params['expected_effect'], 'expected effect'),
            'options': _texts(params['options'], 'options', 5), 'hypotheses': _texts(params['hypotheses'], 'hypotheses', 5),
            'source_refs': _source_ids(snapshot, params['source_refs'], minimum=1),
            'public_sources': _public_sources(params.get('public_sources', []), time.time()),
            'disposition': 'proposal', 'scope_adopted': False, 'quality': 'unassessed'}
    elif action == PREFIX + 'complete':
        _exact(params, {'acknowledged_note_ids', 'discussion_acknowledged', 'discussion_summary', 'direction',
            'direction_rationale', 'follow_ups', 'next_observation_focus', 'rationale'}, {'candidate_ids'})
        if params['discussion_acknowledged'] is not True or params['direction'] not in DIRECTIONS:
            raise _bad('INVALID_PARAMS', 'Explicitly acknowledge the recorded discussion and select its proposed direction')
        notes = _items(params['acknowledged_note_ids'], 'acknowledged discussion notes', 20, 1)
        candidates = _items(params.get('candidate_ids', []), 'candidate IDs', 10)
        for values, kind in ((notes, 'note'), (candidates, 'candidate.record')):
            if any(not isinstance(value, str) for value in values) or len(values) != len(set(values)):
                raise _bad('INVALID_PARAMS', 'Select distinct saved review records')
            for ident in values:
                event = _event(ops.db, wid, rid, ident)
                _access(ops, principal, event)
                if event['kind'] != kind:
                    raise _bad('INVALID_PARAMS', 'The selected event is not the requested discussion record')
                refs.extend(event['source_binding'])
        document = {'discussion_summary': _text(params['discussion_summary'], 'discussion summary', 8000),
            'acknowledged_note_ids': notes, 'acknowledged_by': principal.actor,
            'completion_basis': 'explicit_current_owner_record', 'attendance_verified': False,
            'direction': params['direction'], 'direction_rationale': _text(params['direction_rationale'], 'direction rationale'),
            'candidate_ids': candidates, 'candidates_adopted': False,
            'follow_ups': _follow_ups(ops, wid, snapshot, params['follow_ups']),
            'next_observation_focus': _texts(params['next_observation_focus'], 'next observation focus', 10),
            'meeting_completed': True, 'scope_adopted': False, 'lifecycle_changed': False}
    else:
        _exact(params, {'follow_up_id', 'status', 'result', 'rationale'}, {'snapshot_id'})
        if row['status'] != 'completed':
            raise _bad('PM_MAINTENANCE_REVIEW_NOT_COMPLETED', 'Record the discussion assignment before reporting its result', 409)
        completion = _event(ops.db, wid, rid, row['completion_id'])
        _access(ops, principal, completion)
        selected = next((item for item in completion['document']['follow_ups'] if item['id'] == params['follow_up_id']), None)
        if selected is None or params['status'] not in ('open', 'blocked', 'reported_done'):
            raise _bad('INVALID_PARAMS', 'Select an original follow-up and a supported reported status')
        if principal.actor != selected['owner_actor'] and pm_sprints._role(ops.db, wid, principal.actor) != 'owner':
            raise _bad('FORBIDDEN', 'Only the assigned writer or a current actual owner reports this follow-up', 403)
        result_snapshot = None
        if 'snapshot_id' in params:
            result_snapshot = pm_snapshots.get_snapshot(ops, principal, wid, params['snapshot_id'])['snapshot']
            if result_snapshot['visibility']['filtered']:
                raise _bad('NOT_FOUND', 'The full selected result snapshot is unavailable', 404)
            refs.extend(sources.snapshot_refs(result_snapshot, result_snapshot['snapshot_digest']))
        document = {'follow_up_id': selected['id'], 'completion_id': row['completion_id'], 'status': params['status'],
            'result': _text(params['result'], 'reported result', 8000), 'owner_actor': selected['owner_actor'],
            'snapshot_ref': {key: result_snapshot[key] for key in ('snapshot_id', 'snapshot_digest', 'as_of', 'journal_cursor')}
                if result_snapshot else row['document']['snapshot_ref'], 'verification_status': 'unassessed'}
    document.update(rationale=params['rationale'], execution_authorized=False)
    _bounded(document, maximum=MAX_EVENT_BYTES)
    references = sources.unique(refs)
    sources.access(ops, principal, wid, references)
    return row, document, references


def _admitter(action):
    def admit(ops, principal, target, params, pre):
        with ops.journal.tx():
            if action == PREFIX + 'prepare':
                _, _, fixed, _, refs = _prepare_check(ops, principal, target, params, pre)
            else:
                row, _, refs = _mutation_check(ops, principal, action, target, params, pre)
                fixed = {'review_id': row['review_id'], 'snapshot_ref': row['document']['snapshot_ref']}
            return {'principal': pm_workspace.principal_binding(principal), 'maintenance_review_intent': fixed,
                'maintenance_review_intent_digest': baselines.sources.digest(fixed), 'maintenance_review_sources': refs,
                'maintenance_review_sources_digest': baselines.sources.digest(refs)}
    return admit


def operation_visible(db, principal, operation):
    from .pm_source_reads import scope
    with scope(db, principal):
        row = db.execute('SELECT * FROM operations WHERE operation_id=?', (operation.get('operation_id'),)).fetchone()
        if row is None or row['action'] not in ACTIONS:
            return False
        op = OperationService._decode(row)
        binding = (op.get('external_refs') or {}).get('admission_binding') or {}
        try:
            wid, reader = op['target']['workspace_id'], workload._Reader(db)
            pm_workspace.require_member(db, principal, wid)
            fixed, refs = binding['maintenance_review_intent'], binding['maintenance_review_sources']
            if (baselines.sources.digest(fixed) != binding['maintenance_review_intent_digest']
                    or baselines.sources.digest(refs) != binding['maintenance_review_sources_digest']):
                return False
            sources.access(reader, principal, wid, refs)
            if op['action'] == PREFIX + 'prepare':
                saved = db.execute('SELECT review_id FROM pm_maintenance_reviews WHERE operation_id=?', (op['operation_id'],)).fetchone()
                if saved:
                    _snapshot(reader, principal, _row(db, wid, saved[0]))
            else:
                _snapshot(reader, principal, _row(db, wid, op['target']['review_id']))
                saved = db.execute('SELECT * FROM pm_maintenance_review_events WHERE operation_id=?', (op['operation_id'],)).fetchone()
                if saved:
                    _access(reader, principal, _decode(saved))
            return True
        except (OperationError, KeyError, TypeError, ValueError):
            return False


def _authorize_existing(ops, principal, op, verb):
    _member(ops, principal, op['target']['workspace_id'], write=verb != 'replay')
    if not principal.allows('manage') or not operation_visible(ops.db, principal, op):
        raise _bad('NOT_FOUND', 'Original maintenance review operation unavailable', 404)


def _view(row):
    return {key: value for key, value in row.items() if key != 'source_binding'}


def _event_view(row):
    result = _view(row)
    if row['kind'] == 'candidate.record':
        from .pm_review_candidate_sources import reference
        result['planning_source'] = reference(row)
    return result


async def _run(ctx):
    def effect():
        ctx.check_cancel()
        principal = pm_workspace.admitted_principal(ctx)
        ops, wid = ctx.service, ctx.target['workspace_id']
        binding = ctx.admission_binding or {}
        now = time.time()
        if ctx.op['action'] == PREFIX + 'prepare':
            from . import pm_maintenance_review_schedule as weekly
            scheduled = weekly.guard(ops, ctx.op)
            workspace, plan_head, fixed, previous, refs = _prepare_check(ops, principal, ctx.target, ctx.params, ctx.preconditions)
            if (fixed != binding.get('maintenance_review_intent')
                    or baselines.sources.digest(fixed) != binding.get('maintenance_review_intent_digest')):
                raise _bad('PM_MAINTENANCE_REVIEW_CONTEXT_CHANGED', 'Original review preparation context changed', 409)
            period = {'start': ctx.params['period_start'], 'end': ctx.params['period_end'], 'bounds': '[start,end)',
                'selection_basis': 'saved_observations_and_check_completion_times'}
            report, source_refs = sources.build(ops, principal, wid, fixed['workload'], period, now, previous)
            report['schedule'] = weekly.projection(scheduled)
            if scheduled and scheduled['context']['calendar']['continuity'] == 'omitted_source_limit':
                report['previous_context'] = {'status': 'omitted_source_limit', 'comparison_available': False}
                report['changes'] = {**sources._changes(None, None), 'scope_changed': False}
                report['report_status'] = 'data_gaps'
            snapshot = pm_snapshots._build(ctx, principal, workspace, plan_head)
            # Every original project fact is a source of the complete report too;
            # withdrawal must not leave a partial Brief beside quoted report text.
            source_refs = sources.unique([*refs, *source_refs,
                *[sources.ref('evidence', item['evidence_id'], item['fact_digest']) for item in snapshot['evidence']]])
            rid = 'pmrevw_' + ctx.operation_id[3:]
            snapshot['maintenance'] = {'schema_version': 1, 'review_id': rid, 'report': report, 'access_refs': source_refs}
            weekly.guard(ops, ctx.op)
            snap_result = pm_snapshots._insert(ctx, workspace, snapshot)
            snapshot_ref = {key: snap_result[key] for key in ('snapshot_id', 'snapshot_digest', 'as_of', 'journal_cursor')}
            document = {'schema_version': 1, 'review_id': rid, 'workspace_id': wid, 'snapshot_ref': snapshot_ref,
                'period': period, 'report_digest': baselines.sources.digest(report), 'source_binding_digest': baselines.sources.digest(source_refs),
                'previous_review_id': ctx.params.get('previous_review_id'), 'meeting_completed': False, 'execution_authorized': False}
            digest = baselines.sources.digest(document)
            revision = fixed['review_revision'] + 1
            ops.db.execute('''INSERT INTO pm_maintenance_reviews(review_id,workspace_id,revision,title,snapshot_id,
                document,document_digest,source_binding,actor,created_at,operation_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                (rid, wid, revision, ctx.params['title'], snapshot_ref['snapshot_id'], baselines._json(document), digest,
                 baselines._json(source_refs), ctx.actor, now, ctx.operation_id))
            ops.db.execute('INSERT INTO pm_maintenance_review_state VALUES(?,1,?,NULL)', (rid, 'prepared'))
            weekly.finish(ops, ctx.operation_id, outcome='prepared', review_id=rid)
            # Reserve the actual public core wrapper before this atomic effect commits.
            _core(ops, principal, _row(ops.db, wid, rid))
            result = {'workspace_id': wid, 'review_id': rid, 'revision': revision, 'version': 1,
                'snapshot_id': snapshot_ref['snapshot_id'], 'document_digest': digest, 'meeting_completed': False,
                'execution_authorized': False}
        else:
            row, document, refs = _mutation_check(ops, principal, ctx.op['action'], ctx.target, ctx.params, ctx.preconditions)
            fixed = {'review_id': row['review_id'], 'snapshot_ref': row['document']['snapshot_ref']}
            if fixed != binding.get('maintenance_review_intent') or refs != binding.get('maintenance_review_sources'):
                raise _bad('PM_MAINTENANCE_REVIEW_CONTEXT_CHANGED', 'Original review source context changed', 409)
            rid, eid = row['review_id'], 'pmrve_' + ctx.operation_id[3:]
            kind = ctx.op['action'].removeprefix(PREFIX)
            document.update(event_id=eid, review_id=rid, recorded_by=ctx.actor, recorded_at=now,
                source_binding_digest=baselines.sources.digest(refs))
            digest = baselines.sources.digest(document)
            ops.db.execute('''INSERT INTO pm_maintenance_review_events(event_id,review_id,workspace_id,kind,document,
                document_digest,source_binding,actor,created_at,operation_id) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                (eid, rid, wid, kind, baselines._json(document), digest, baselines._json(refs), ctx.actor, now, ctx.operation_id))
            version = row['version'] + 1
            ops.db.execute('UPDATE pm_maintenance_review_state SET version=?,status=?,completion_id=? WHERE review_id=?',
                (version, 'completed' if kind == 'complete' else row['status'], eid if kind == 'complete' else row['completion_id'], rid))
            _core(ops, principal, _row(ops.db, wid, rid))
            result = {'workspace_id': wid, 'review_id': rid, 'event_id': eid, 'version': version,
                'document_digest': digest, 'meeting_completed': kind == 'complete' or row['status'] == 'completed',
                'execution_authorized': False}
        ops.journal.api_event('pm_maintenance_review', rid, ctx.op['action'], {**result, 'operation_id': ctx.operation_id}, actor=ctx.actor)
        return result
    return ctx.effect('pm.maintenance_review.record', effect)


def _core(ops, principal, row):
    snapshot = _snapshot(ops, principal, row)
    completion = _event(ops.db, row['workspace_id'], row['review_id'], row['completion_id']) if row['completion_id'] else None
    if completion:
        _access(ops, principal, completion)
    result = {'workspace_id': row['workspace_id'], 'review': _view(row), 'snapshot': snapshot,
        'completion': _event_view(completion) if completion else None}
    # Current/detail history wrappers must remain readable even at the bound.
    _bounded({**result, 'events': [], 'next_cursor': 9223372036854775807,
        'revision': 9223372036854775807, 'visibility_filtered': False, 'history_byte_limited': False})
    return result


@baselines._snapshot_read
def get_review(ops, principal, workspace_id, review_id, *, history_limit=20):
    _member(ops, principal, workspace_id)
    _page(0, history_limit)
    row = _row(ops.db, workspace_id, review_id)
    result = _core(ops, principal, row)
    # History is independently pageable. A mandatory core that leaves no room
    # for one whole event exposes cursor zero rather than truncating prose.
    history = _events(ops, principal, workspace_id, review_id, 0, history_limit,
        base={**result, 'revision': 9223372036854775807}, embedded=True)
    return _bounded({**result, **history})


@baselines._snapshot_read
def current_review(ops, principal, workspace_id, *, history_limit=20):
    _member(ops, principal, workspace_id)
    current = head(ops.db, workspace_id)
    if not current['review_id']:
        return {'workspace_id': workspace_id, 'review': None, 'revision': 0, 'visibility_filtered': False}
    try:
        return _bounded({**get_review(ops, principal, workspace_id, current['review_id'], history_limit=history_limit),
            'revision': current['revision']})
    except OperationError as exc:
        if exc.status not in (403, 404):
            raise
        return {'workspace_id': workspace_id, 'review': None, 'revision': current['revision'], 'visibility_filtered': True}


def _page(after, limit):
    _integer(after, 'cursor')
    if type(limit) is not int or not 1 <= limit <= 100:
        raise _bad('INVALID_PARAMS', 'Use a page limit from 1 to 100')


def _events(ops, principal, wid, rid, after, limit, *, base=None, embedded=False):
    _page(after, limit)
    rows = ops.db.execute('''SELECT sequence,event_id FROM pm_maintenance_review_events
        WHERE workspace_id=? AND review_id=? AND sequence>? ORDER BY sequence LIMIT ?''', (wid, rid, after, limit + 1)).fetchall()
    result = {'events': [], 'next_cursor': None, 'visibility_filtered': False, 'history_byte_limited': False}
    consumed = after
    for stub in rows[:limit]:
        row = _event(ops.db, wid, rid, stub['event_id'])
        try:
            _access(ops, principal, row)
        except OperationError as exc:
            if exc.status not in (403, 404):
                raise
            result['visibility_filtered'], consumed = True, stub['sequence']
            continue
        candidate = {**result, 'events': [*result['events'], _event_view(row)], 'next_cursor': stub['sequence']}
        if baselines._size({**(base or {}), **candidate}) + max(0, 4 - len(str(stub['sequence']))) > MAX_READ_BYTES:
            result.update(next_cursor=consumed, history_byte_limited=True)
            if not embedded and consumed == after:
                raise _bad('PM_MAINTENANCE_REVIEW_TOO_LARGE', 'One whole review event exceeds the read bound', 413)
            return result
        result['events'].append(_event_view(row))
        consumed = stub['sequence']
    result['next_cursor'] = consumed if len(rows) > limit else None
    return result


@baselines._snapshot_read
def list_events(ops, principal, workspace_id, review_id, *, after=0, limit=20):
    _member(ops, principal, workspace_id)
    _snapshot(ops, principal, _row(ops.db, workspace_id, review_id))
    base = {'workspace_id': workspace_id, 'review_id': review_id}
    return _bounded({**base, **_events(ops, principal, workspace_id, review_id, after, limit, base=base)})


@baselines._snapshot_read
def list_reviews(ops, principal, workspace_id, *, after=0, limit=20):
    _member(ops, principal, workspace_id)
    _page(after, limit)
    rows = ops.db.execute('SELECT sequence,review_id FROM pm_maintenance_reviews WHERE workspace_id=? AND sequence>? ORDER BY sequence LIMIT ?',
        (workspace_id, after, limit + 1)).fetchall()
    result = {'workspace_id': workspace_id, 'reviews': [], 'next_cursor': None, 'visibility_filtered': False}
    for stub in rows[:limit]:
        row = _row(ops.db, workspace_id, stub['review_id'])
        try:
            _snapshot(ops, principal, row)
        except OperationError as exc:
            if exc.status not in (403, 404):
                raise
            result['visibility_filtered'] = True
            continue
        result['reviews'].append({key: row[key] for key in ('sequence', 'review_id', 'workspace_id', 'revision', 'version',
            'status', 'title', 'snapshot_id', 'created_at', 'actor', 'operation_id')})
    result['next_cursor'] = rows[limit - 1]['sequence'] if len(rows) > limit else None
    return _bounded(result)
