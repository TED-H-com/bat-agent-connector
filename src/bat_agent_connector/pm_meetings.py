"""Professional meeting records over fixed Sprint commitments and visible evidence.

All effects are local journal facts. Drafting or completing a discussion never
adopts product scope, evaluates DoD, dispatches work or controls an IDE session.
"""
from __future__ import annotations

import json
import time

from . import pm_snapshots, pm_sprints, pm_workspace
from .operations import ActionDef, OperationError

KINDS = ('planning', 'review', 'retro')
ACTIONS = tuple('pm.meeting.' + name for name in ('create', 'context.revise', 'note', 'draft', 'complete', 'follow_up.record'))
MAX_DOCUMENT_BYTES = 200_000


def _bad(code, message, status=422):
    return OperationError(code, message, status)


def _json(value):
    return pm_snapshots._json(value)


def _text(value, label, maximum=4000):
    return pm_sprints._text(value, label, maximum)


def _only(value, fields):
    if not isinstance(value, dict) or set(value) - set(fields):
        raise _bad('INVALID_PARAMS', 'Unexpected meeting fields')


def _list(value, label, maximum=20, minimum=0):
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise _bad('INVALID_PARAMS', f'{label} needs {minimum}–{maximum} entries')
    return value


def _texts(value, label, maximum=20, minimum=0):
    return [_text(item, label) for item in _list(value, label, maximum, minimum)]


def schema(journal):
    pm_sprints.schema(journal)
    pm_snapshots.schema(journal)
    with journal.tx():
        for ddl in (
            '''CREATE TABLE IF NOT EXISTS pm_meetings (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, meeting_id TEXT NOT NULL UNIQUE,
                workspace_id TEXT NOT NULL, sprint_id TEXT NOT NULL, kind TEXT NOT NULL,
                title TEXT NOT NULL, actor TEXT NOT NULL, created_at REAL NOT NULL,
                operation_id TEXT NOT NULL UNIQUE)''',
            '''CREATE TABLE IF NOT EXISTS pm_meeting_state (
                meeting_id TEXT PRIMARY KEY, version INTEGER NOT NULL, status TEXT NOT NULL,
                context_revision_id TEXT NOT NULL, draft_revision_id TEXT, completion_id TEXT)''',
            '''CREATE TABLE IF NOT EXISTS pm_meeting_contexts (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, context_revision_id TEXT NOT NULL UNIQUE,
                meeting_id TEXT NOT NULL, previous_context_revision_id TEXT, document TEXT NOT NULL,
                context_digest TEXT NOT NULL, actor TEXT NOT NULL, created_at REAL NOT NULL,
                operation_id TEXT NOT NULL UNIQUE)''',
            '''CREATE TABLE IF NOT EXISTS pm_meeting_notes (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, note_id TEXT NOT NULL UNIQUE,
                meeting_id TEXT NOT NULL, context_revision_id TEXT NOT NULL, content TEXT NOT NULL,
                source_refs TEXT NOT NULL, actor TEXT NOT NULL, created_at REAL NOT NULL,
                operation_id TEXT NOT NULL UNIQUE)''',
            '''CREATE TABLE IF NOT EXISTS pm_meeting_drafts (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, revision_id TEXT NOT NULL UNIQUE,
                meeting_id TEXT NOT NULL, context_revision_id TEXT NOT NULL, previous_revision_id TEXT,
                document TEXT NOT NULL, document_digest TEXT NOT NULL, actor TEXT NOT NULL,
                created_at REAL NOT NULL, operation_id TEXT NOT NULL UNIQUE)''',
            '''CREATE TABLE IF NOT EXISTS pm_meeting_completions (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, completion_id TEXT NOT NULL UNIQUE,
                meeting_id TEXT NOT NULL UNIQUE, context_revision_id TEXT NOT NULL,
                draft_revision_id TEXT NOT NULL, document TEXT NOT NULL,
                actor TEXT NOT NULL, created_at REAL NOT NULL, operation_id TEXT NOT NULL UNIQUE)''',
            '''CREATE TABLE IF NOT EXISTS pm_meeting_follow_up_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
                meeting_id TEXT NOT NULL, follow_up_id TEXT NOT NULL, document TEXT NOT NULL,
                actor TEXT NOT NULL, created_at REAL NOT NULL, operation_id TEXT NOT NULL UNIQUE)''',
        ):
            journal.db.execute(ddl)
        for table in ('pm_meetings', 'pm_meeting_contexts', 'pm_meeting_notes', 'pm_meeting_drafts',
                      'pm_meeting_completions', 'pm_meeting_follow_up_events'):
            for verb in ('UPDATE', 'DELETE'):
                journal.db.execute(f'CREATE TRIGGER IF NOT EXISTS {table}_{verb.lower()}_immutable BEFORE {verb} ON {table} '
                                   "BEGIN SELECT RAISE(ABORT, 'Meeting history is immutable'); END")


def install(ops):
    schema(ops.journal)
    for action in ACTIONS:
        if action not in ops.actions:
            fields = ('workspace_id', 'sprint_id') if action == 'pm.meeting.create' else ('workspace_id', 'meeting_id')
            ops.register(ActionDef(action, 'manage', 'Record a sourced professional discussion without executing work',
                                   _run, _admitter(action), fields, authorize_existing=_authorize_existing))


def _member(ops, principal, wid, *, write=False):
    return pm_sprints._member(ops, principal, wid, write=write)


def _meeting(db, wid, meeting_id):
    pm_sprints._identifier(meeting_id)
    row = db.execute('''SELECT m.*,s.version,s.status,s.context_revision_id,s.draft_revision_id,s.completion_id
        FROM pm_meetings m JOIN pm_meeting_state s USING(meeting_id)
        WHERE m.workspace_id=? AND m.meeting_id=?''', (wid, meeting_id)).fetchone()
    if row is None:
        raise _bad('PM_MEETING_NOT_FOUND', 'Meeting not found in this workspace', 404)
    return dict(row)


def _document(db, table, key, value):
    # Table and key are internal constants at every call site.
    row = db.execute(f'SELECT * FROM {table} WHERE {key}=?', (value,)).fetchone()  # noqa: S608
    if row is None:
        raise _bad('PM_MEETING_SOURCE_UNAVAILABLE', 'The fixed meeting record cannot be read', 409)
    result = {**dict(row), 'document': json.loads(row['document'])}
    digest_key = 'context_digest' if table == 'pm_meeting_contexts' else 'document_digest'
    if digest_key in result and pm_snapshots._digest(result['document']) != result[digest_key]:
        raise _bad('PM_MEETING_SOURCE_UNAVAILABLE', 'The fixed meeting digest cannot be verified', 409)
    return result


def _snapshot(ops, principal, wid, reference):
    snapshot = pm_snapshots.get_snapshot(ops, principal, wid, reference['snapshot_id'])['snapshot']
    if snapshot['visibility']['filtered']:
        # A discussion can quote its sources. Do not expose those quotes after
        # losing source access, even if the original creator could read them.
        raise _bad('PM_MEETING_SOURCE_UNAVAILABLE', 'Meeting sources are not available to this reader', 404)
    if reference.get('snapshot_digest', snapshot['snapshot_digest']) != snapshot['snapshot_digest']:
        raise _bad('PM_MEETING_SOURCE_UNAVAILABLE', 'The fixed snapshot digest changed', 409)
    return snapshot


def _access(ops, principal, meeting):
    context = _document(ops.db, 'pm_meeting_contexts', 'context_revision_id', meeting['context_revision_id'])
    snapshot = _snapshot(ops, principal, meeting['workspace_id'], context['document']['snapshot_ref'])
    return context, snapshot


def _context(ops, principal, wid, sprint_id, snapshot_id, version, kind):
    sprint = pm_sprints._sprint(ops.db, wid, sprint_id)
    if type(version) is not int or version != sprint['version']:
        raise _bad('VERSION_CONFLICT', 'Read the exact Sprint version before preparing meeting context', 409)
    if kind in {'review', 'retro'} and sprint['started_at'] is None:
        raise _bad('PM_MEETING_SPRINT_NOT_STARTED', 'Review and Retro discuss a Sprint that has actually started', 409)
    snapshot = _snapshot(ops, principal, wid, {'snapshot_id': snapshot_id})
    definition = sprint['definition']
    goal = dict(ops.db.execute('SELECT * FROM pm_sprint_goal_revisions WHERE revision_id=?', (sprint['goal_revision_id'],)).fetchone())
    artifacts = {name: pm_sprints._artifact(ops.db, wid, definition[name + '_revision_id'], name)
                 for name in ('product_goal', 'backlog', 'dod')}
    return {'sprint_id': sprint_id, 'sprint_version': sprint['version'], 'sprint_status': sprint['status'],
            'sprint_definition': definition, 'sprint_goal': goal, 'selection_revision_id': sprint['selection_revision_id'],
            'selected_items': pm_sprints._selection(ops.db, sprint), 'artifacts': artifacts,
            'snapshot_ref': {key: snapshot[key] for key in ('snapshot_id', 'snapshot_digest', 'as_of', 'journal_cursor')},
            'journal_cursor': ops.journal.api_head(), 'quality': 'unassessed', 'execution_authorized': False}


def _refs(ops, meeting, context, snapshot, refs):
    allowed = {'snapshot': {snapshot['snapshot_id']}, 'evidence': {item['evidence_id'] for item in snapshot['evidence']},
               'sprint_goal': {context['sprint_goal']['revision_id']},
               'selection': {context['selection_revision_id']} - {None},
               **{kind: {value['revision_id']} for kind, value in context['artifacts'].items()}}
    result, seen = [], set()
    for ref in _list(refs, 'source_refs', 50):
        _only(ref, ('kind', 'id'))
        kind, key = ref.get('kind'), ref.get('id')
        if not isinstance(kind, str) or not isinstance(key, str) or (kind, key) in seen:
            raise _bad('INVALID_PARAMS', 'Source references must have distinct kind and ID')
        if kind == 'note':
            row = ops.db.execute('SELECT 1 FROM pm_meeting_notes WHERE meeting_id=? AND note_id=? AND context_revision_id=?',
                                 (meeting['meeting_id'], key, meeting['context_revision_id'])).fetchone()
            if row is None:
                raise _bad('PM_MEETING_SOURCE_INVALID', 'Select a note from this fixed discussion context', 409)
        elif key not in allowed.get(kind, set()):
            raise _bad('PM_MEETING_SOURCE_INVALID', 'Select sources from this exact meeting context', 409)
        seen.add((kind, key))
        result.append({'kind': kind, 'id': key})
    return result


def _key(value, seen, label):
    if not isinstance(value, str) or not pm_sprints.ITEM_ID.fullmatch(value) or value in seen:
        raise _bad('INVALID_PARAMS', f'{label} needs distinct stable keys')
    seen.add(value)
    return value


def _output(meeting, context, output):
    if meeting['kind'] == 'planning':
        _only(output, ('why', 'delivery_approach', 'capacity_discussion', 'dependency_notes'))
        result = {key: _text(output.get(key), key) for key in ('why', 'delivery_approach', 'capacity_discussion')}
        result['dependency_notes'] = _texts(output.get('dependency_notes', []), 'dependency notes')
        # What and each Developer's How come from the original Sprint selection.
        result.update(what=context['selected_items'], how=[{'binding_id': item['binding_id'], 'assignee': item['assignee'],
            'recorded_how': item['how'], 'status': 'recorded' if item['how'].strip() else 'missing'} for item in context['selected_items']])
        return result
    if meeting['kind'] == 'review':
        _only(output, ('goal_review', 'outcomes', 'feedback', 'environment_changes', 'next_steps'))
        known = {item['binding_id'] for item in context['selected_items']}
        seen, outcomes = set(), []
        for item in _list(output.get('outcomes', []), 'outcomes', 100):
            _only(item, ('binding_id', 'observation'))
            key = item.get('binding_id')
            if not isinstance(key, str) or key not in known or key in seen:
                raise _bad('INVALID_PARAMS', 'Review each explicitly selected binding at most once')
            seen.add(key)
            outcomes.append({'binding_id': key, 'observation': _text(item.get('observation'), 'outcome'),
                             'source_status': 'discussion_claim', 'dod': 'unassessed', 'counts_as_done': False})
        return {'goal_review': _text(output.get('goal_review'), 'goal review'), 'outcomes': outcomes,
                'feedback': _texts(output.get('feedback', []), 'feedback'),
                'environment_changes': _texts(output.get('environment_changes', []), 'environment changes'),
                'next_steps': _texts(output.get('next_steps', []), 'next steps'), 'qualified_increment': None,
                'unreviewed_binding_ids': sorted(known - seen)}
    _only(output, ('observations', 'previous_improvement_review', 'no_improvement_rationale'))
    return {'observations': _texts(output.get('observations'), 'process, collaboration, tool or quality observations', minimum=1),
            'previous_improvement_review': _text(output.get('previous_improvement_review'), 'previous improvement review'),
            'no_improvement_rationale': _text(output['no_improvement_rationale'], 'reason for adopting no improvement')
                if 'no_improvement_rationale' in output else None,
            'previous_effect_verification': 'discussion_claim'}


def _draft(ops, principal, meeting, context, snapshot, params):
    _only(params, ('output', 'source_refs', 'decisions', 'follow_ups', 'unresolved_questions', 'rationale'))
    def references(refs):
        return _refs(ops, meeting, context, snapshot, refs)
    decisions, seen = [], set()
    for item in _list(params.get('decisions', []), 'decisions', 10):
        _only(item, ('id', 'text', 'rationale', 'alternatives', 'source_refs'))
        decisions.append({'id': _key(item.get('id'), seen, 'decisions'), 'text': _text(item.get('text'), 'decision'),
            'rationale': _text(item.get('rationale'), 'decision rationale'),
            'alternatives': _texts(item.get('alternatives', []), 'alternatives', 5),
            'source_refs': references(item.get('source_refs', [])), 'disposition': 'proposal', 'scope_applied': False})
    follow_ups, seen = [], set()
    for item in _list(params.get('follow_ups', []), 'follow ups', 5 if meeting['kind'] == 'retro' else 10):
        _only(item, ('id', 'text', 'owner_actor', 'expected_effect', 'verification', 'source_refs'))
        owner = item.get('owner_actor')
        if not isinstance(owner, str) or pm_sprints._role(ops.db, meeting['workspace_id'], owner) not in {'owner', 'contributor'}:
            raise _bad('INVALID_PARAMS', 'Follow-up owners must be current workspace writers')
        follow_ups.append({'id': _key(item.get('id'), seen, 'follow ups'), 'text': _text(item.get('text'), 'follow up'),
            'owner_actor': owner, 'expected_effect': _text(item.get('expected_effect'), 'expected effect'),
            'verification': _text(item.get('verification'), 'verification method'),
            'source_refs': references(item.get('source_refs', [])), 'status': 'proposed', 'verification_status': 'unassessed'})
    result = {'kind': meeting['kind'], 'output': _output(meeting, context, params.get('output')),
              'source_refs': references(params.get('source_refs', [])), 'decisions': decisions, 'follow_ups': follow_ups,
              'unresolved_questions': _texts(params.get('unresolved_questions', []), 'unresolved questions'),
              'rationale': _text(params.get('rationale'), 'rationale'), 'source_status': 'discussion_proposal',
              'quality': 'unassessed', 'meeting_completed': False, 'execution_authorized': False}
    if meeting['kind'] == 'retro' and follow_ups and result['output']['no_improvement_rationale']:
        raise _bad('INVALID_PARAMS', 'Choose improvements or explicitly record why none is adopted')
    if len(_json(result).encode('utf-8')) > MAX_DOCUMENT_BYTES:
        raise _bad('PM_MEETING_TOO_LARGE', 'Use a focused meeting draft of at most 200,000 UTF-8 bytes')
    return result


def _owner(ops, principal, meeting, context):
    pm_sprints._owner(ops.db, meeting['workspace_id'], principal, context['sprint_definition'])


def _complete(ops, principal, meeting, context, params):
    _only(params, ('draft_revision_id', 'discussion_summary', 'decision_ids', 'rationale'))
    _owner(ops, principal, meeting, context)
    if not meeting['draft_revision_id'] or params.get('draft_revision_id') != meeting['draft_revision_id']:
        raise _bad('PM_MEETING_DRAFT_CHANGED', 'Complete the exact current discussion draft', 409)
    draft = _document(ops.db, 'pm_meeting_drafts', 'revision_id', meeting['draft_revision_id'])
    note = ops.db.execute('SELECT max(sequence) FROM pm_meeting_notes WHERE meeting_id=? AND context_revision_id=?',
                          (meeting['meeting_id'], meeting['context_revision_id'])).fetchone()[0]
    if note is None:
        raise _bad('PM_MEETING_DISCUSSION_REQUIRED', 'Record the discussion before explicitly completing it', 409)
    document = draft['document']
    if meeting['kind'] == 'planning':
        current = pm_sprints._sprint(ops.db, meeting['workspace_id'], meeting['sprint_id'])
        if current['goal_revision_id'] != context['sprint_goal']['revision_id'] or current['selection_revision_id'] != context['selection_revision_id']:
            raise _bad('PM_MEETING_CONTEXT_CHANGED', 'Explicitly refresh the meeting context for the changed Planning commitments', 409)
        if not context['selected_items'] or any(not item['how'].strip() for item in context['selected_items']):
            raise _bad('PM_MEETING_PLANNING_INCOMPLETE', 'Planning completion needs selected work and each Developer’s recorded How', 409)
    if meeting['kind'] == 'review' and (not document['output']['feedback'] or not document['output']['next_steps']):
        raise _bad('PM_MEETING_REVIEW_INCOMPLETE', 'Review needs actual feedback and an explicit next direction', 409)
    if meeting['kind'] == 'retro' and not document['follow_ups'] and not document['output']['no_improvement_rationale']:
        raise _bad('PM_MEETING_RETRO_INCOMPLETE', 'Record owned improvements or a reason why this reflection adopts none', 409)
    decision_ids = pm_sprints._ids(params.get('decision_ids', []), 'decision_ids', 10)
    if set(decision_ids) - {item['id'] for item in document['decisions']}:
        raise _bad('INVALID_PARAMS', 'Record only explicitly selected decisions from this draft')
    for follow_up in document['follow_ups']:
        if pm_sprints._role(ops.db, meeting['workspace_id'], follow_up['owner_actor']) not in {'owner', 'contributor'}:
            raise _bad('FORBIDDEN', 'A proposed follow-up owner no longer has workspace write access', 403)
    return {'discussion_summary': _text(params.get('discussion_summary'), 'discussion summary', 8000),
            'rationale': params['rationale'], 'draft_revision_id': draft['revision_id'], 'draft_digest': draft['document_digest'],
            'context_revision_id': meeting['context_revision_id'], 'notes_through_sequence': note,
            'completion_basis': 'explicit_designated_owner_record', 'attendance_verified': False,
            'decisions': [{**item, 'disposition': 'recorded_discussion_decision' if item['id'] in decision_ids else 'proposal',
                           'recorded_by': principal.actor if item['id'] in decision_ids else None} for item in document['decisions']],
            'follow_ups': [{**item, 'status': 'open', 'assignment_basis': 'owner_recorded_discussion',
                           'work_item_created': False, 'scheduler_registered': False} for item in document['follow_ups']],
            'meeting_completed': True, 'quality': 'unassessed', 'counts_as_done': False,
            'scope_applied': False, 'execution_authorized': False, 'scrum_conformance': 'not_established'}


def _check(ops, principal, action, target, params, pre):
    if not all(isinstance(value, dict) for value in (target, params, pre)):
        raise _bad('INVALID_PARAMS', 'Supply meeting parameters and preconditions as objects')
    wid = target.get('workspace_id')
    _member(ops, principal, wid, write=True)
    create = action == 'pm.meeting.create'
    if set(target) != {'workspace_id', 'sprint_id' if create else 'meeting_id'}:
        raise _bad('INVALID_TARGET', 'Supply exact workspace and meeting or Sprint identities')
    _text(params.get('rationale'), 'rationale')
    if create:
        _only(params, ('kind', 'title', 'snapshot_id', 'rationale'))
        if not isinstance(params.get('kind'), str) or params['kind'] not in KINDS:
            raise _bad('INVALID_PARAMS', 'Choose Planning, Review or Retro')
        if set(pre) != {'expected_sprint_version'}:
            raise _bad('PRECONDITION_REQUIRED', 'Supply the fixed Sprint version')
        _text(params.get('title'), 'meeting title', 200)
        return None, _context(ops, principal, wid, target.get('sprint_id'), params.get('snapshot_id'), pre['expected_sprint_version'], params['kind'])
    meeting = _meeting(ops.db, wid, target.get('meeting_id'))
    context, snapshot = _access(ops, principal, meeting)
    refresh = action == 'pm.meeting.context.revise'
    if (set(pre) != ({'expected_version', 'expected_sprint_version'} if refresh else {'expected_version'})
            or type(pre.get('expected_version')) is not int or pre['expected_version'] != meeting['version']):
        raise _bad('VERSION_CONFLICT', 'Read the current meeting version before recording a change', 409)
    if action != 'pm.meeting.follow_up.record' and meeting['status'] == 'completed':
        raise _bad('PM_MEETING_COMPLETED', 'A completed discussion is immutable', 409)
    if refresh:
        _only(params, ('snapshot_id', 'rationale'))
        return meeting, _context(ops, principal, wid, meeting['sprint_id'], params.get('snapshot_id'), pre['expected_sprint_version'], meeting['kind'])
    if action == 'pm.meeting.note':
        _only(params, ('content', 'source_refs', 'rationale'))
        return meeting, {'content': _text(params.get('content'), 'discussion note', 8000),
                         'source_refs': _refs(ops, meeting, context['document'], snapshot, params.get('source_refs', []))}
    if action == 'pm.meeting.draft':
        return meeting, _draft(ops, principal, meeting, context['document'], snapshot, params)
    if action == 'pm.meeting.complete':
        return meeting, _complete(ops, principal, meeting, context['document'], params)
    _only(params, ('follow_up_id', 'status', 'result', 'snapshot_id', 'source_refs', 'rationale'))
    if meeting['status'] != 'completed':
        raise _bad('PM_MEETING_NOT_COMPLETED', 'Follow-up reporting needs an explicitly completed discussion', 409)
    complete = _document(ops.db, 'pm_meeting_completions', 'completion_id', meeting['completion_id'])['document']
    follow_up = next((item for item in complete['follow_ups'] if item['id'] == params.get('follow_up_id')), None)
    if follow_up is None:
        raise _bad('PM_MEETING_FOLLOW_UP_NOT_FOUND', 'Select an original follow-up from this meeting', 404)
    if principal.actor != follow_up['owner_actor']:
        _owner(ops, principal, meeting, context['document'])
    if not isinstance(params.get('status'), str) or params['status'] not in {'open', 'blocked', 'reported_done'}:
        raise _bad('INVALID_PARAMS', 'A result may be open, blocked or reported_done; verification remains unassessed')
    if 'snapshot_id' in params:
        snapshot = _snapshot(ops, principal, wid, {'snapshot_id': params['snapshot_id']})
    return meeting, {'follow_up_id': follow_up['id'], 'status': params['status'],
        'result': _text(params.get('result'), 'follow-up result', 8000),
        'source_refs': _refs(ops, meeting, context['document'], snapshot, params.get('source_refs', [])),
        'snapshot_ref': {key: snapshot[key] for key in ('snapshot_id', 'snapshot_digest', 'as_of', 'journal_cursor')},
        'rationale': params['rationale'], 'verification_status': 'unassessed', 'execution_authorized': False}


def _admitter(action):
    def admit(ops, principal, target, params, pre):
        _check(ops, principal, action, target, params, pre)
        return {'principal': pm_workspace.principal_binding(principal)}
    return admit


def _authorize_existing(ops, principal, op, verb):
    _member(ops, principal, op['target']['workspace_id'], write=True)
    if verb != 'replay' and principal.actor != op['actor']:
        raise _bad('FORBIDDEN', 'Only the requester may control this meeting operation', 403)
    if op['action'] != 'pm.meeting.create':
        _access(ops, principal, _meeting(ops.db, op['target']['workspace_id'], op['target']['meeting_id']))
        if op['action'] == 'pm.meeting.follow_up.record' and 'snapshot_id' in op['params']:
            _snapshot(ops, principal, op['target']['workspace_id'], {'snapshot_id': op['params']['snapshot_id']})
    else:
        _snapshot(ops, principal, op['target']['workspace_id'], {'snapshot_id': op['params']['snapshot_id']})


async def _run(ctx):
    def apply():
        ops, action, wid = ctx.service, ctx.op['action'], ctx.target['workspace_id']
        meeting, document = _check(ops, pm_workspace.admitted_principal(ctx), action, ctx.target, ctx.params, ctx.preconditions)
        suffix, now = ctx.operation_id[3:], time.time()
        meeting_id = 'pmmeet_' + suffix if meeting is None else meeting['meeting_id']
        result = {'workspace_id': wid, 'meeting_id': meeting_id, 'execution_authorized': False}
        if meeting is None:
            ops.db.execute('INSERT INTO pm_meetings(meeting_id,workspace_id,sprint_id,kind,title,actor,created_at,operation_id) VALUES(?,?,?,?,?,?,?,?)',
                (meeting_id, wid, ctx.target['sprint_id'], ctx.params['kind'], ctx.params['title'], ctx.actor, now, ctx.operation_id))
        if action in {'pm.meeting.create', 'pm.meeting.context.revise'}:
            context_id = 'pmmctx_' + suffix
            ops.db.execute('INSERT INTO pm_meeting_contexts(context_revision_id,meeting_id,previous_context_revision_id,document,context_digest,actor,created_at,operation_id) VALUES(?,?,?,?,?,?,?,?)',
                (context_id, meeting_id, meeting['context_revision_id'] if meeting else None, _json(document), pm_snapshots._digest(document), ctx.actor, now, ctx.operation_id))
            if meeting is None:
                ops.db.execute("INSERT INTO pm_meeting_state(meeting_id,version,status,context_revision_id) VALUES(?,1,'draft',?)", (meeting_id, context_id))
            else:
                ops.db.execute('UPDATE pm_meeting_state SET context_revision_id=?,draft_revision_id=NULL,version=version+1 WHERE meeting_id=?', (context_id, meeting_id))
            result['context_revision_id'] = context_id
        elif action == 'pm.meeting.note':
            note_id = 'pmmnote_' + suffix
            ops.db.execute('INSERT INTO pm_meeting_notes(note_id,meeting_id,context_revision_id,content,source_refs,actor,created_at,operation_id) VALUES(?,?,?,?,?,?,?,?)',
                (note_id, meeting_id, meeting['context_revision_id'], document['content'], _json(document['source_refs']), ctx.actor, now, ctx.operation_id))
            result['note_id'] = note_id
        elif action == 'pm.meeting.draft':
            revision_id = 'pmmrev_' + suffix
            # Local follow-up keys are stable only within their exact adopted
            # meeting draft. The completion points to that immutable revision.
            ops.db.execute('INSERT INTO pm_meeting_drafts(revision_id,meeting_id,context_revision_id,previous_revision_id,document,document_digest,actor,created_at,operation_id) VALUES(?,?,?,?,?,?,?,?,?)',
                (revision_id, meeting_id, meeting['context_revision_id'], meeting['draft_revision_id'], _json(document), pm_snapshots._digest(document), ctx.actor, now, ctx.operation_id))
            ops.db.execute('UPDATE pm_meeting_state SET draft_revision_id=? WHERE meeting_id=?', (revision_id, meeting_id))
            result['draft_revision_id'] = revision_id
        elif action == 'pm.meeting.complete':
            complete_id = 'pmmc_' + suffix
            ops.db.execute('INSERT INTO pm_meeting_completions(completion_id,meeting_id,context_revision_id,draft_revision_id,document,actor,created_at,operation_id) VALUES(?,?,?,?,?,?,?,?)',
                (complete_id, meeting_id, meeting['context_revision_id'], meeting['draft_revision_id'], _json(document), ctx.actor, now, ctx.operation_id))
            ops.db.execute("UPDATE pm_meeting_state SET status='completed',completion_id=? WHERE meeting_id=?", (complete_id, meeting_id))
            result['completion_id'] = complete_id
        else:
            event_id = 'pmfue_' + suffix
            ops.db.execute('INSERT INTO pm_meeting_follow_up_events(event_id,meeting_id,follow_up_id,document,actor,created_at,operation_id) VALUES(?,?,?,?,?,?,?)',
                (event_id, meeting_id, document['follow_up_id'], _json(document), ctx.actor, now, ctx.operation_id))
            result['event_id'] = event_id
        if action not in {'pm.meeting.create', 'pm.meeting.context.revise'}:
            ops.db.execute('UPDATE pm_meeting_state SET version=version+1 WHERE meeting_id=?', (meeting_id,))
        current = _meeting(ops.db, wid, meeting_id)
        result.update(version=current['version'], status=current['status'])
        ops.journal.api_event('pm_meeting', meeting_id, action, {**result, 'operation_id': ctx.operation_id}, actor=ctx.actor)
        return result
    return ctx.effect('pm.meeting.apply', apply)


HISTORY = {'contexts': ('pm_meeting_contexts', 'context_revision_id'), 'notes': ('pm_meeting_notes', 'note_id'),
           'drafts': ('pm_meeting_drafts', 'revision_id'), 'follow_ups': ('pm_meeting_follow_up_events', 'event_id')}


def _history(ops, principal, meeting, kind, after, limit):
    pm_sprints._page(after, limit)
    if not isinstance(kind, str) or kind not in HISTORY:
        raise _bad('INVALID_PARAMS', 'Select contexts, notes, drafts or follow_ups history')
    table, key = HISTORY[kind]
    rows = ops.db.execute(f'SELECT * FROM {table} WHERE meeting_id=? AND sequence>? ORDER BY sequence LIMIT ?',  # noqa: S608
                          (meeting['meeting_id'], after, limit + 1)).fetchall()
    records, checked_contexts = [], set()
    for row in rows[:limit]:
        result = dict(row)
        context_id = row['context_revision_id'] if 'context_revision_id' in row.keys() else meeting['context_revision_id']
        if context_id not in checked_contexts:
            context = _document(ops.db, 'pm_meeting_contexts', 'context_revision_id', context_id)
            _snapshot(ops, principal, meeting['workspace_id'], context['document']['snapshot_ref'])
            checked_contexts.add(context_id)
        if 'document' in row.keys():
            result = _document(ops.db, table, key, row[key])
            if kind == 'follow_ups':
                _snapshot(ops, principal, meeting['workspace_id'], result['document']['snapshot_ref'])
        else:
            result['source_refs'] = json.loads(row['source_refs'])
        records.append(result)
    return {'records': records, 'next_cursor': rows[limit-1]['sequence'] if len(rows) > limit else None}


def get_meeting(ops, principal, workspace_id, meeting_id, *, history_limit=50):
    with ops.journal.tx():
        _member(ops, principal, workspace_id)
        meeting = _meeting(ops.db, workspace_id, meeting_id)
        context, snapshot = _access(ops, principal, meeting)
        histories = {kind: _history(ops, principal, meeting, kind, 0, history_limit) for kind in ('notes', 'follow_ups')}
        draft = _document(ops.db, 'pm_meeting_drafts', 'revision_id', meeting['draft_revision_id']) if meeting['draft_revision_id'] else None
        completion = _document(ops.db, 'pm_meeting_completions', 'completion_id', meeting['completion_id']) if meeting['completion_id'] else None
        return {'version': 1, 'workspace_id': workspace_id, 'meeting': meeting, 'context': context,
                'snapshot': snapshot, 'draft': draft, 'completion': completion,
                'notes': histories['notes']['records'], 'follow_up_events': histories['follow_ups']['records'],
                'history_next_cursors': {kind: value['next_cursor'] for kind, value in histories.items()},
                'quality': 'unassessed', 'execution_authorized': False}


def list_meetings(ops, principal, workspace_id, *, sprint_id=None, after=0, limit=50):
    with ops.journal.tx():
        _member(ops, principal, workspace_id)
        pm_sprints._page(after, limit)
        if sprint_id is not None:
            pm_sprints._sprint(ops.db, workspace_id, sprint_id)
        rows = ops.db.execute('SELECT meeting_id,sequence FROM pm_meetings WHERE workspace_id=? AND (? IS NULL OR sprint_id=?) AND sequence>? ORDER BY sequence LIMIT ?',
                              (workspace_id, sprint_id, sprint_id, after, limit + 1)).fetchall()
        meetings = []
        for row in rows[:limit]:
            meeting = _meeting(ops.db, workspace_id, row['meeting_id'])
            try:
                _access(ops, principal, meeting)
            except OperationError as error:
                if error.status != 404:
                    raise
                continue
            meetings.append(meeting)
        return {'version': 1, 'workspace_id': workspace_id, 'meetings': meetings,
                'next_cursor': rows[limit-1]['sequence'] if len(rows) > limit else None}


def list_history(ops, principal, workspace_id, meeting_id, kind, *, after=0, limit=50):
    with ops.journal.tx():
        _member(ops, principal, workspace_id)
        meeting = _meeting(ops.db, workspace_id, meeting_id)
        _access(ops, principal, meeting)
        return {'version': 1, 'workspace_id': workspace_id, 'meeting_id': meeting_id, 'kind': kind,
                **_history(ops, principal, meeting, kind, after, limit)}
