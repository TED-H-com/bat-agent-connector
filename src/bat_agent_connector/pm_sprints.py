"""Versioned Sprint commitments and management state, without execution control."""
from __future__ import annotations

import calendar
import datetime as dt
import json
import re
import time

from . import pm_evidence, pm_plans, pm_workspace, work_items
from .operations import ActionDef, OperationError

KINDS = ('product_goal', 'dod', 'backlog')
ACTIONS = tuple(f'pm.{kind}.revise' for kind in KINDS) + (
    'pm.sprint.create', 'pm.sprint.goal.revise', 'pm.sprint.select',
    'pm.sprint.start', 'pm.sprint.close', 'pm.sprint.cancel')
ITEM_ID = re.compile(r'[a-z][a-z0-9_.-]{0,79}')
MAX_ITEMS = 100
MAX_READ_BYTES = 2_000_000


def _bad(code, message, status=422):
    return OperationError(code, message, status)


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _only(value, fields):
    if not isinstance(value, dict) or set(value) - set(fields):
        raise _bad('INVALID_PARAMS', 'Unexpected Sprint management fields')


def _text(value, label, maximum=4000):
    return pm_plans._text(value, label, maximum)


def _identifier(value):
    if not isinstance(value, str) or not value or len(value) > 256 or '\x00' in value:
        raise _bad('INVALID_PARAMS', 'Supply a bounded explicit identifier')
    try:
        value.encode('utf-8')
    except UnicodeError:
        raise _bad('INVALID_PARAMS', 'Identifiers must contain valid UTF-8 text') from None
    return value


def _ids(value, label, maximum=MAX_ITEMS):
    if (not isinstance(value, list) or len(value) > maximum or not all(isinstance(v, str) for v in value)
            or len(set(value)) != len(value)):
        raise _bad('INVALID_PARAMS', f'{label} must be distinct bounded IDs')
    return [_identifier(item) for item in value]


def schema(journal):
    pm_plans.schema(journal)
    pm_evidence.schema(journal)
    with journal.tx():
        for ddl in (
            '''CREATE TABLE IF NOT EXISTS pm_sprint_artifact_revisions (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, revision_id TEXT NOT NULL UNIQUE,
                workspace_id TEXT NOT NULL, kind TEXT NOT NULL, previous_revision_id TEXT,
                document TEXT NOT NULL, actor TEXT NOT NULL, created_at REAL NOT NULL,
                operation_id TEXT NOT NULL UNIQUE)''',
            '''CREATE TABLE IF NOT EXISTS pm_sprint_artifact_heads (
                workspace_id TEXT NOT NULL, kind TEXT NOT NULL, revision_id TEXT NOT NULL,
                PRIMARY KEY(workspace_id,kind))''',
            '''CREATE TABLE IF NOT EXISTS pm_sprints (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, sprint_id TEXT NOT NULL UNIQUE,
                workspace_id TEXT NOT NULL, definition TEXT NOT NULL, actor TEXT NOT NULL,
                created_at REAL NOT NULL, operation_id TEXT NOT NULL UNIQUE)''',
            '''CREATE TABLE IF NOT EXISTS pm_sprint_state (
                sprint_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, version INTEGER NOT NULL,
                status TEXT NOT NULL, goal_revision_id TEXT NOT NULL, selection_revision_id TEXT,
                started_goal_revision_id TEXT, started_selection_revision_id TEXT,
                started_at REAL, ended_at REAL)''',
            "CREATE UNIQUE INDEX IF NOT EXISTS pm_sprint_one_active ON pm_sprint_state(workspace_id) WHERE status='active'",
            '''CREATE TABLE IF NOT EXISTS pm_sprint_goal_revisions (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, revision_id TEXT NOT NULL UNIQUE,
                sprint_id TEXT NOT NULL, previous_revision_id TEXT, text TEXT NOT NULL,
                rationale TEXT NOT NULL, actor TEXT NOT NULL, created_at REAL NOT NULL,
                operation_id TEXT NOT NULL UNIQUE)''',
            '''CREATE TABLE IF NOT EXISTS pm_sprint_selections (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, revision_id TEXT NOT NULL UNIQUE,
                sprint_id TEXT NOT NULL, previous_revision_id TEXT, items TEXT NOT NULL,
                rationale TEXT NOT NULL, actor TEXT NOT NULL, created_at REAL NOT NULL,
                operation_id TEXT NOT NULL UNIQUE)''',
            '''CREATE TABLE IF NOT EXISTS pm_sprint_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, sprint_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL, action TEXT NOT NULL, document TEXT NOT NULL,
                actor TEXT NOT NULL, created_at REAL NOT NULL, operation_id TEXT NOT NULL UNIQUE)''',
        ):
            journal.db.execute(ddl)
        for table in ('pm_sprint_artifact_revisions', 'pm_sprints', 'pm_sprint_goal_revisions',
                      'pm_sprint_selections', 'pm_sprint_events'):
            for verb in ('UPDATE', 'DELETE'):
                journal.db.execute(f'CREATE TRIGGER IF NOT EXISTS {table}_{verb.lower()}_immutable BEFORE {verb} ON {table} '
                                   "BEGIN SELECT RAISE(ABORT, 'Sprint history is immutable'); END")


def install(ops):
    schema(ops.journal)
    for action in ACTIONS:
        if action not in ops.actions:
            fields = ('workspace_id', 'sprint_id') if action.startswith('pm.sprint.') and action != 'pm.sprint.create' else ('workspace_id',)
            ops.register(ActionDef(action, 'manage', 'Record explicit Sprint commitments and management state', _run,
                                   _admitter(action), fields, authorize_existing=_authorize_existing))


def _member(ops, principal, wid, *, write=False, decide=False):
    if not principal.allows('observe') or write and not principal.allows('manage'):
        raise _bad('FORBIDDEN', 'Sprint reads need observe; writes also need manage', 403)
    return pm_workspace.require_member(ops.db, principal, wid, write=write, decide=decide)


def _role(db, wid, actor):
    if not isinstance(actor, str):
        return None
    row = db.execute('SELECT role FROM pm_workspace_members WHERE workspace_id=? AND actor=?', (wid, actor)).fetchone()
    return row[0] if row else None


def _head(db, wid, kind):
    row = db.execute('SELECT revision_id FROM pm_sprint_artifact_heads WHERE workspace_id=? AND kind=?', (wid, kind)).fetchone()
    return row[0] if row else None


def _artifact(db, wid, revision_id, kind):
    _identifier(revision_id)
    if kind not in KINDS:
        raise _bad('INVALID_PARAMS', 'Unknown Sprint artifact kind')
    row = db.execute('SELECT * FROM pm_sprint_artifact_revisions WHERE workspace_id=? AND revision_id=? AND kind=?',
                     (wid, revision_id, kind)).fetchone()
    if row is None:
        raise _bad('PM_SPRINT_ARTIFACT_NOT_FOUND', 'Select an artifact revision in this workspace', 404)
    return {**dict(row), 'document': json.loads(row['document'])}


def _plan(ops, principal, wid, revision_id):
    _identifier(revision_id)
    plan = pm_plans.get_plan(ops, principal, wid, revision_id)['plan']
    if not plan['adoption']:
        raise _bad('PM_ADOPTED_PLAN_REQUIRED', 'Sprint commitments require a fixed historically adopted plan', 409)
    return {'revision_id': revision_id, 'document_digest': plan['document_digest'],
            'scope_revision': plan['adoption']['scope_revision']}


def _binding(ops, principal, wid, binding_id, *, current=False):
    _identifier(binding_id)
    row = ops.db.execute('SELECT * FROM pm_work_bindings WHERE workspace_id=? AND binding_id=?', (wid, binding_id)).fetchone()
    if row is None:
        raise _bad('PM_WORK_BINDING_REQUIRED', 'Select an explicit work binding in this workspace', 409)
    if not row['plan_revision_id']:
        raise _bad('PM_ADOPTED_PLAN_REQUIRED', 'Selected work must reference a fixed adopted plan', 409)
    _plan(ops, principal, wid, row['plan_revision_id'])
    if current:
        latest = pm_evidence._binding(ops.db, wid, row['work_item_id'])
        item = work_items._get_item(ops.db, row['work_item_id'], active=True)
        work_items._get_project(ops.db, item['project_id'], active=True)
        if latest['binding_id'] != binding_id or item['project_id'] != row['project_id']:
            raise _bad('PM_WORK_BINDING_CHANGED', 'Read the current explicit work binding before a new selection', 409)
    return {'binding_id': binding_id, 'work_item_id': row['work_item_id'], 'project_id': row['project_id'],
            'plan_revision_id': row['plan_revision_id'], 'requirement_ids': json.loads(row['requirement_ids']),
            'execution_spec': 'not_assigned'}


def _date(value):
    try:
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else None
        if parsed is None or parsed.tzinfo is None or parsed.utcoffset() != dt.timedelta(0):
            raise ValueError
        return parsed
    except (ValueError, OverflowError):
        raise _bad('INVALID_PARAMS', 'Sprint dates must be explicit UTC ISO-8601 timestamps') from None


def _interval(start, end):
    start, end = _date(start), _date(end)
    try:
        year, month = (start.year + 1, 1) if start.month == 12 else (start.year, start.month + 1)
        maximum = start.replace(year=year, month=month, day=min(start.day, calendar.monthrange(year, month)[1]))
    except ValueError:
        raise _bad('INVALID_PARAMS', 'Sprint interval is outside the supported calendar') from None
    if not start < end <= maximum:
        raise _bad('PM_SPRINT_INTERVAL_INVALID', 'Use a positive fixed period no longer than one calendar month')
    return start.isoformat().replace('+00:00', 'Z'), end.isoformat().replace('+00:00', 'Z')


def _sprint(db, wid, sprint_id):
    _identifier(sprint_id)
    row = db.execute('''SELECT s.*, t.version,t.status,t.goal_revision_id,t.selection_revision_id,
        t.started_goal_revision_id,t.started_selection_revision_id,t.started_at,t.ended_at
        FROM pm_sprints s JOIN pm_sprint_state t USING(sprint_id) WHERE s.workspace_id=? AND s.sprint_id=?''', (wid, sprint_id)).fetchone()
    if row is None:
        raise _bad('PM_SPRINT_NOT_FOUND', 'Sprint not found in this workspace', 404)
    return {**dict(row), 'definition': json.loads(row['definition'])}


def _selection(db, sprint):
    row = db.execute('SELECT items FROM pm_sprint_selections WHERE revision_id=?', (sprint['selection_revision_id'],)).fetchone()
    return json.loads(row[0]) if row else []


def _owner(db, wid, principal, definition):
    # Admin credentials do not silently acquire this product decision role.
    if principal.actor != definition['owner_actor'] or _role(db, wid, principal.actor) != 'owner':
        raise _bad('FORBIDDEN', 'Only the designated current workspace owner may make this Sprint decision', 403)


def _artifact_document(ops, principal, wid, kind, params):
    if kind == 'backlog':
        _only(params, ('product_goal_revision_id', 'binding_ids', 'rationale'))
        goal = _artifact(ops.db, wid, params.get('product_goal_revision_id'), 'product_goal')
        bindings = [_binding(ops, principal, wid, value, current=True) for value in _ids(params.get('binding_ids'), 'binding_ids')]
        if len({value['work_item_id'] for value in bindings}) != len(bindings):
            raise _bad('INVALID_PARAMS', 'A Product Backlog contains each work item once')
        return {'product_goal_revision_id': goal['revision_id'], 'items': bindings,
                'coverage': 'explicit_adopted_work_bindings_only'}
    _only(params, ('plan_revision_id', 'text' if kind == 'product_goal' else 'criteria', 'rationale'))
    plan = _plan(ops, principal, wid, params.get('plan_revision_id'))
    if kind == 'product_goal':
        return {'text': _text(params.get('text'), 'Product Goal'), 'plan_ref': plan,
                'basis': 'explicit_direction_decision', 'commitment_for': 'product_backlog'}
    criteria = params.get('criteria')
    if not isinstance(criteria, list) or not 1 <= len(criteria) <= 50:
        raise _bad('INVALID_PARAMS', 'DoD needs 1–50 distinct shared quality criteria')
    seen, result = set(), []
    for item in criteria:
        _only(item, ('id', 'text'))
        key = item.get('id')
        if not isinstance(key, str) or not ITEM_ID.fullmatch(key) or key in seen:
            raise _bad('INVALID_PARAMS', 'DoD criterion IDs must be distinct stable keys')
        seen.add(key)
        result.append({'id': key, 'text': _text(item.get('text'), 'DoD criterion')})
    return {'criteria': result, 'plan_ref': plan, 'commitment_for': 'increment',
            'acceptance_criteria': 'separate_requirement_data', 'evaluation': 'unassessed'}


def _definition(ops, principal, wid, params):
    _only(params, ('title', 'mode', 'owner_actor', 'developers', 'scrum_master', 'backlog_revision_id',
                   'dod_revision_id', 'goal', 'starts_at', 'ends_at', 'capacity_note', 'rationale'))
    mode = params.get('mode')
    if not isinstance(mode, str) or mode not in {'scrum', 'personal_iteration'}:
        raise _bad('INVALID_PARAMS', 'Choose Scrum artifacts or personal iteration explicitly')
    if _role(ops.db, wid, params.get('owner_actor')) != 'owner':
        raise _bad('INVALID_PARAMS', 'Designate an existing workspace owner for product direction')
    developers = _ids(params.get('developers'), 'developers', 50)
    if not developers or any(_role(ops.db, wid, actor) not in {'owner', 'contributor'} for actor in developers):
        raise _bad('INVALID_PARAMS', 'Developers must be current workspace writers')
    master = params.get('scrum_master')
    if master is not None and _role(ops.db, wid, master) not in {'owner', 'contributor'}:
        raise _bad('INVALID_PARAMS', 'The optional Scrum Master must be a current workspace writer')
    backlog = _artifact(ops.db, wid, params.get('backlog_revision_id'), 'backlog')
    dod = _artifact(ops.db, wid, params.get('dod_revision_id'), 'dod')
    starts, ends = _interval(params.get('starts_at'), params.get('ends_at'))
    return {'title': _text(params.get('title'), 'Sprint title', 200), 'mode': mode,
            'mode_label': 'Scrum artifacts (conformance not established)' if mode == 'scrum' else 'Personal iteration',
            'scrum_conformance': 'not_established', 'owner_actor': params['owner_actor'],
            'developers': developers, 'scrum_master': master, 'backlog_revision_id': backlog['revision_id'],
            'product_goal_revision_id': backlog['document']['product_goal_revision_id'], 'dod_revision_id': dod['revision_id'],
            'starts_at': starts, 'ends_at': ends, 'capacity_note': _text(params.get('capacity_note'), 'capacity note'),
            'selection_domain': 'fixed_product_backlog', 'execution_authorized': False}


def _selected(ops, principal, wid, sprint, params):
    _only(params, ('items', 'rationale'))
    raw = params.get('items')
    if not isinstance(raw, list) or len(raw) > MAX_ITEMS or sprint['status'] == 'active' and not raw:
        raise _bad('INVALID_PARAMS', 'Supply bounded selected work; an active Sprint cannot empty its Goal work')
    definition = sprint['definition']
    original = {item['binding_id']: item for item in _selection(ops.db, sprint)}
    domain = _artifact(ops.db, wid, definition['backlog_revision_id'], 'backlog')['document']['items']
    allowed = {item['binding_id'] for item in domain}
    is_owner = principal.actor == definition['owner_actor'] and _role(ops.db, wid, principal.actor) == 'owner'
    if not is_owner and principal.actor not in definition['developers']:
        raise _bad('FORBIDDEN', 'Only this Sprint owner or its current Developers may select work', 403)
    selected, seen = [], set()
    for value in raw:
        _only(value, ('binding_id', 'assignee', 'how'))
        binding_id = value.get('binding_id')
        if not isinstance(binding_id, str) or binding_id not in allowed or binding_id in seen:
            raise _bad('INVALID_PARAMS', 'Select distinct bindings from the fixed Product Backlog')
        seen.add(binding_id)
        binding = _binding(ops, principal, wid, binding_id, current=binding_id not in original)
        assignee = value.get('assignee')
        if assignee not in definition['developers'] or _role(ops.db, wid, assignee) not in {'owner', 'contributor'}:
            raise _bad('INVALID_PARAMS', 'Selected work needs a current designated Developer')
        how = value.get('how', '')
        if not isinstance(how, str) or len(how) > 4000 or '\x00' in how:
            raise _bad('INVALID_PARAMS', 'How must be bounded text')
        if how:
            _text(how, 'How')
        prior = original.get(binding_id)
        candidate = {**binding, 'assignee': assignee, 'how': how}
        if assignee != principal.actor and (prior is None or prior['assignee'] != assignee):
            if not is_owner or how:
                raise _bad('FORBIDDEN', 'Only the owner may assign others; their implementation How remains their own', 403)
        elif assignee != principal.actor and prior != candidate:
            raise _bad('FORBIDDEN', 'Do not rewrite another Developer’s How', 403)
        selected.append(candidate)
    if not is_owner:
        preserved = [value for value in selected if value['assignee'] != principal.actor]
        expected = [value for value in original.values() if value['assignee'] != principal.actor]
        if preserved != expected:
            raise _bad('FORBIDDEN', 'A Developer may change only their own selection and How', 403)
    return selected


def _check(ops, principal, action, target, params, pre):
    if not all(isinstance(value, dict) for value in (target, params, pre)):
        raise _bad('INVALID_PARAMS', 'Supply object parameters and preconditions')
    wid = target.get('workspace_id')
    artifact = action in {f'pm.{kind}.revise' for kind in KINDS}
    create = action == 'pm.sprint.create'
    _member(ops, principal, wid, write=True, decide=artifact or create)
    if set(target) != ({'workspace_id'} if artifact or create else {'workspace_id', 'sprint_id'}):
        raise _bad('INVALID_TARGET', 'Supply exact workspace and Sprint identities')
    _text(params.get('rationale'), 'rationale')
    if artifact:
        kind = action.split('.')[1]
        if set(pre) != {'expected_revision_id'} or pre['expected_revision_id'] != _head(ops.db, wid, kind):
            raise _bad('VERSION_CONFLICT', 'Read the current artifact revision before changing it', 409)
        return kind, _artifact_document(ops, principal, wid, kind, params)
    if create:
        if set(pre) != {'expected_workspace_version'} or type(pre['expected_workspace_version']) is not int:
            raise _bad('PRECONDITION_REQUIRED', 'Supply the current workspace version')
        if pre['expected_workspace_version'] != pm_workspace._workspace(ops.db, wid)['version']:
            raise _bad('VERSION_CONFLICT', 'Workspace responsibilities changed', 409)
        _text(params.get('goal'), 'Sprint Goal')
        return None, _definition(ops, principal, wid, params)
    sprint = _sprint(ops.db, wid, target.get('sprint_id'))
    if set(pre) != {'expected_version'} or type(pre['expected_version']) is not int or pre['expected_version'] != sprint['version']:
        raise _bad('VERSION_CONFLICT', 'Read the current Sprint version before changing it', 409)
    if sprint['status'] in {'closed', 'cancelled'}:
        raise _bad('PM_SPRINT_TERMINAL', 'The original Sprint history is closed', 409)
    if action == 'pm.sprint.select':
        if sprint['status'] == 'active' and time.time() >= _date(sprint['definition']['ends_at']).timestamp():
            raise _bad('PM_SPRINT_TIMEBOX', 'The fixed Sprint period ended; close or cancel its preserved selection', 409)
        return sprint, _selected(ops, principal, wid, sprint, params)
    _owner(ops.db, wid, principal, sprint['definition'])
    if action == 'pm.sprint.goal.revise':
        _only(params, ('goal', 'rationale'))
        if sprint['status'] != 'planned':
            raise _bad('PM_SPRINT_GOAL_FIXED', 'An active Sprint Goal is fixed; cancel an obsolete Goal explicitly', 409)
        return sprint, _text(params.get('goal'), 'Sprint Goal')
    if action == 'pm.sprint.start':
        _only(params, ('rationale',))
        if sprint['status'] != 'planned':
            raise _bad('PM_SPRINT_STATE', 'Only a planned Sprint can start', 409)
        definition = sprint['definition']
        if not _date(definition['starts_at']).timestamp() <= time.time() < _date(definition['ends_at']).timestamp():
            raise _bad('PM_SPRINT_TIMEBOX', 'Start within the fixed Sprint interval', 409)
        selected = _selection(ops.db, sprint)
        if not selected:
            raise _bad('PM_SPRINT_SELECTION_REQUIRED', 'Explicitly select work before starting', 409)
        for value in selected:
            _binding(ops, principal, wid, value['binding_id'], current=True)
            if _role(ops.db, wid, value['assignee']) not in {'owner', 'contributor'}:
                raise _bad('FORBIDDEN', 'A selected Developer no longer has workspace write membership', 403)
        if definition['scrum_master'] is not None and _role(ops.db, wid, definition['scrum_master']) not in {'owner', 'contributor'}:
            raise _bad('FORBIDDEN', 'The designated Scrum Master no longer has workspace write membership', 403)
        if ops.db.execute("SELECT 1 FROM pm_sprint_state WHERE workspace_id=? AND status='active'", (wid,)).fetchone():
            raise _bad('PM_SPRINT_ACTIVE', 'This workspace already has an active Sprint', 409)
        return sprint, None
    _only(params, ('rationale', 'work_notes', 'quality_refs'))
    notes = params.get('work_notes', {})
    if not isinstance(notes, dict) or len(notes) > MAX_ITEMS:
        raise _bad('INVALID_PARAMS', 'work_notes must be bounded selected-work reasons')
    known = {value['work_item_id'] for value in _selection(ops.db, sprint)}
    if any(key not in known for key in notes):
        raise _bad('INVALID_PARAMS', 'Work notes must name currently selected work')
    for value in notes.values():
        _text(value, 'work note')
    refs = params.get('quality_refs', {})
    if not isinstance(refs, dict) or set(refs) - known or len(refs) > MAX_ITEMS:
        raise _bad('INVALID_PARAMS', 'quality_refs must select original assessments for currently selected work')
    _selected_quality(ops, principal, sprint, refs)
    if action == 'pm.sprint.close' and (sprint['status'] != 'active' or time.time() < _date(sprint['definition']['ends_at']).timestamp()):
        raise _bad('PM_SPRINT_TIMEBOX', 'Close an active Sprint when its fixed period has ended; cancel an obsolete Goal explicitly', 409)
    return sprint, notes


def _admitter(action):
    def admit(ops, principal, target, params, pre):
        _check(ops, principal, action, target, params, pre)
        return {'principal': pm_workspace.principal_binding(principal)}
    return admit


def _authorize_existing(ops, principal, op, verb):
    _member(ops, principal, op['target']['workspace_id'], write=True)
    from .pm_quality_projections import operation_visible
    if not operation_visible(ops.db, principal, op):
        raise _bad('NOT_FOUND', 'Original selected quality sources are unavailable', 404)
    if verb != 'replay' and principal.actor != op['actor']:
        raise _bad('FORBIDDEN', 'Only the original requester may control this management operation', 403)


def _selected_quality(ops, principal, sprint, refs):
    if not refs:
        return {}
    from . import pm_quality_projections as quality
    wid = sprint['workspace_id']
    selected = {item['work_item_id']: item for item in _selection(ops.db, sprint)}
    result = {}
    for work_id, reference in refs.items():
        binding = _binding(ops, principal, wid, selected[work_id]['binding_id'])
        scope = _plan(ops, principal, wid, binding['plan_revision_id'])['scope_revision']
        result[work_id] = quality.select(ops, principal, wid, reference, work_item_id=work_id,
            binding_id=binding['binding_id'], scope_revision=scope, dod_revision_id=sprint['definition']['dod_revision_id'])
    return result


def _end_document(ops, sprint, notes, quality=None):
    selected = _selection(ops.db, sprint)
    items = []
    for value in selected:
        item = {**value, 'quality': 'unassessed', 'counts_as_done': False,
                'disposition': 'requires_product_backlog_consideration',
                'reason': notes.get(value['work_item_id'], 'DoD and item acceptance have not been assessed by this service')}
        fact = (quality or {}).get(value['work_item_id'])
        if fact:
            item.update(quality='qualified' if fact['qualified'] else 'not_qualified', quality_assessment=fact,
                counts_as_done=fact['qualified'],
                disposition='qualified_increment' if fact['qualified'] else 'requires_product_backlog_consideration',
                reason=notes.get(value['work_item_id'], 'Original acceptance and Sprint DoD assessed against the selected fixed result'))
        items.append(item)
    return {'goal_revision_id': sprint['goal_revision_id'], 'started_goal_revision_id': sprint['started_goal_revision_id'],
            'selection_revision_id': sprint['selection_revision_id'], 'dod_revision_id': sprint['definition']['dod_revision_id'],
            'items': items,
            'automatic_carryover': False, 'execution_changed': False, 'review_completed': False, 'retro_completed': False}


async def _run(ctx):
    def apply():
        ops, action, wid = ctx.service, ctx.op['action'], ctx.target['workspace_id']
        principal = pm_workspace.admitted_principal(ctx)
        source, document = _check(ops, principal, action, ctx.target, ctx.params, ctx.preconditions)
        suffix, now = ctx.operation_id[3:], time.time()
        if isinstance(source, str):
            revision_id = {'product_goal': 'pmgoal_', 'dod': 'pmdod_', 'backlog': 'pmbkl_'}[source] + suffix
            doc = {**document, 'rationale': ctx.params['rationale'], 'execution_authorized': False}
            ops.db.execute('''INSERT INTO pm_sprint_artifact_revisions(revision_id,workspace_id,kind,previous_revision_id,
                document,actor,created_at,operation_id) VALUES(?,?,?,?,?,?,?,?)''',
                (revision_id, wid, source, _head(ops.db, wid, source), _json(doc), ctx.actor, now, ctx.operation_id))
            ops.db.execute('''INSERT INTO pm_sprint_artifact_heads VALUES(?,?,?) ON CONFLICT(workspace_id,kind)
                DO UPDATE SET revision_id=excluded.revision_id''', (wid, source, revision_id))
            result = {'workspace_id': wid, 'kind': source, 'revision_id': revision_id, 'execution_authorized': False}
        else:
            sprint_id = ctx.target.get('sprint_id', 'pms_' + suffix)
            detail = {'rationale': ctx.params['rationale']}
            if action == 'pm.sprint.create':
                goal_id = 'pmsg_' + suffix
                ops.db.execute('INSERT INTO pm_sprints(sprint_id,workspace_id,definition,actor,created_at,operation_id) VALUES(?,?,?,?,?,?)',
                               (sprint_id, wid, _json(document), ctx.actor, now, ctx.operation_id))
                ops.db.execute('INSERT INTO pm_sprint_goal_revisions(revision_id,sprint_id,text,rationale,actor,created_at,operation_id) VALUES(?,?,?,?,?,?,?)',
                               (goal_id, sprint_id, ctx.params['goal'], ctx.params['rationale'], ctx.actor, now, ctx.operation_id))
                ops.db.execute("INSERT INTO pm_sprint_state(sprint_id,workspace_id,version,status,goal_revision_id) VALUES(?,?,1,'planned',?)", (sprint_id, wid, goal_id))
            elif action == 'pm.sprint.goal.revise':
                goal_id = 'pmsg_' + suffix
                ops.db.execute('INSERT INTO pm_sprint_goal_revisions(revision_id,sprint_id,previous_revision_id,text,rationale,actor,created_at,operation_id) VALUES(?,?,?,?,?,?,?,?)',
                               (goal_id, sprint_id, source['goal_revision_id'], document, ctx.params['rationale'], ctx.actor, now, ctx.operation_id))
                ops.db.execute('UPDATE pm_sprint_state SET goal_revision_id=?,version=version+1 WHERE sprint_id=?', (goal_id, sprint_id))
            elif action == 'pm.sprint.select':
                selection_id = 'pmssel_' + suffix
                ops.db.execute('INSERT INTO pm_sprint_selections(revision_id,sprint_id,previous_revision_id,items,rationale,actor,created_at,operation_id) VALUES(?,?,?,?,?,?,?,?)',
                               (selection_id, sprint_id, source['selection_revision_id'], _json(document), ctx.params['rationale'], ctx.actor, now, ctx.operation_id))
                ops.db.execute('UPDATE pm_sprint_state SET selection_revision_id=?,version=version+1 WHERE sprint_id=?', (selection_id, sprint_id))
            elif action == 'pm.sprint.start':
                ops.db.execute("UPDATE pm_sprint_state SET status='active',version=version+1,started_goal_revision_id=goal_revision_id,started_selection_revision_id=selection_revision_id,started_at=? WHERE sprint_id=?", (now, sprint_id))
            else:
                quality = _selected_quality(ops, principal, source, ctx.params.get('quality_refs', {}))
                detail.update(_end_document(ops, source, document, quality))
                _checked_read(detail)
                ops.db.execute('UPDATE pm_sprint_state SET status=?,version=version+1,ended_at=? WHERE sprint_id=?',
                               ('closed' if action == 'pm.sprint.close' else 'cancelled', now, sprint_id))
            current = _sprint(ops.db, wid, sprint_id)
            result = {'workspace_id': wid, 'sprint_id': sprint_id, 'version': current['version'], 'status': current['status'],
                      'goal_revision_id': current['goal_revision_id'], 'selection_revision_id': current['selection_revision_id'],
                      'execution_authorized': False}
            ops.db.execute('INSERT INTO pm_sprint_events(sprint_id,workspace_id,action,document,actor,created_at,operation_id) VALUES(?,?,?,?,?,?,?)',
                           (sprint_id, wid, action, _json({**result, **detail}), ctx.actor, now, ctx.operation_id))
        ops.journal.api_event('pm_sprint', result.get('sprint_id', wid), action, {**result, 'operation_id': ctx.operation_id}, actor=ctx.actor)
        return result
    return ctx.effect('pm.sprint.apply', apply)


def _page(after, limit):
    if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise _bad('INVALID_PARAMS', 'Use a nonnegative sequence and limit 1–100')


def _read_size(value):
    # Match HTTP's UTF-8 JSON separators, including spaces and escaped controls.
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8'))


def _checked_read(value):
    if _read_size(value) > MAX_READ_BYTES:
        raise _bad('PM_SPRINT_READ_TOO_LARGE', 'Complete Sprint state or one history record exceeds the read byte limit', 413)
    return value


def _coverage(after, rows):
    return {'after': after, 'returned': 0, 'has_more': bool(rows), 'byte_limited': bool(rows)}


def _fill_page(response, records, rows, decode, limit, cursors, cursor_key, coverage):
    # rows contain only bounded identifiers/sequences. Materialize one full record
    # at a time; a large history must not first be loaded in its entirety.
    for index, row in enumerate(rows[:limit]):
        previous = dict(coverage)
        previous_cursor = cursors[cursor_key]
        records.append(decode(row))
        more = index + 1 < len(rows)
        cursors[cursor_key] = row['sequence'] if more else None
        coverage.update(returned=index + 1, has_more=more, byte_limited=index + 1 < min(limit, len(rows)))
        if _read_size(response) > MAX_READ_BYTES:
            records.pop()
            cursors[cursor_key] = previous_cursor
            coverage.update(previous)
            break


def _list_page(envelope, key, rows, decode, after, limit):
    coverage = {'max_bytes': MAX_READ_BYTES, **_coverage(after, rows)}
    result = {**envelope, key: [], 'next_cursor': after if rows else None, 'coverage': coverage}
    _checked_read(result)
    _fill_page(result, result[key], rows, decode, limit, result, 'next_cursor', coverage)
    if rows and not result[key]:
        raise _bad('PM_SPRINT_READ_TOO_LARGE', 'One complete Sprint history record exceeds the read byte limit', 413)
    return result


def get_artifacts(ops, principal, workspace_id):
    with ops.journal.tx():
        _member(ops, principal, workspace_id)
        return _checked_read({'version': 1, 'workspace_id': workspace_id,
                **{kind: _artifact(ops.db, workspace_id, revision, kind) if (revision := _head(ops.db, workspace_id, kind)) else None for kind in KINDS},
                'execution_authorized': False, 'quality_evaluation': 'unavailable'})


def list_revisions(ops, principal, workspace_id, kind, *, after=0, limit=50):
    with ops.journal.tx():
        _member(ops, principal, workspace_id)
        _page(after, limit)
        if kind not in KINDS:
            raise _bad('INVALID_PARAMS', 'Unknown Sprint artifact kind')
        rows = ops.db.execute('SELECT revision_id,sequence FROM pm_sprint_artifact_revisions WHERE workspace_id=? AND kind=? AND sequence>? ORDER BY sequence LIMIT ?',
                              (workspace_id, kind, after, limit + 1)).fetchall()
        return _list_page({'version': 1}, 'revisions', rows,
                          lambda row: _artifact(ops.db, workspace_id, row['revision_id'], kind), after, limit)


def get_artifact(ops, principal, workspace_id, kind, revision_id):
    with ops.journal.tx():
        _member(ops, principal, workspace_id)
        return _checked_read({'version': 1, 'artifact': _artifact(ops.db, workspace_id, revision_id, kind)})


HISTORY = {'goals': ('pm_sprint_goal_revisions', None), 'selections': ('pm_sprint_selections', 'items'),
           'events': ('pm_sprint_events', 'document')}


def _history_rows(db, sprint_id, kind, after, limit):
    _page(after, limit)
    if not isinstance(kind, str) or kind not in HISTORY:
        raise _bad('INVALID_PARAMS', 'Unknown Sprint history kind')
    table, _ = HISTORY[kind]
    # The table name comes only from the closed HISTORY map above.
    return db.execute(f'SELECT sequence FROM {table} WHERE sprint_id=? AND sequence>? ORDER BY sequence LIMIT ?',  # noqa: S608
                      (sprint_id, after, limit + 1)).fetchall()


def _history_record(db, kind, sequence):
    table, field = HISTORY[kind]
    row = db.execute(f'SELECT * FROM {table} WHERE sequence=?', (sequence,)).fetchone()  # noqa: S608
    return {**dict(row), **({field: json.loads(row[field])} if field else {})}


def _quality_access(ops, principal, workspace_id, sprint_id):
    from .pm_quality_projections import sprint_sources
    quality = None
    # There is at most one terminal event; check it even beyond the first page.
    for row in ops.db.execute("SELECT document FROM pm_sprint_events WHERE sprint_id=? AND action IN ('pm.sprint.close','pm.sprint.cancel')", (sprint_id,)):
        document = json.loads(row['document'])
        sprint_sources(ops, principal, workspace_id, document)
        if any(item.get('quality_assessment') for item in document.get('items', [])):
            quality = {'basis': 'fixed_original_close_assessments', 'total_selected_items': len(document['items']),
                'qualified_items': sum(item['counts_as_done'] is True for item in document['items']),
                'current_applicability': 'not_evaluated'}
    return quality


def list_history(ops, principal, workspace_id, sprint_id, kind, *, after=0, limit=50):
    with ops.journal.tx():
        _member(ops, principal, workspace_id)
        _sprint(ops.db, workspace_id, sprint_id)
        _quality_access(ops, principal, workspace_id, sprint_id)
        rows = _history_rows(ops.db, sprint_id, kind, after, limit)
        return _list_page({'version': 1, 'workspace_id': workspace_id, 'sprint_id': sprint_id, 'kind': kind},
                          'records', rows, lambda row: _history_record(ops.db, kind, row['sequence']), after, limit)


def get_sprint(ops, principal, workspace_id, sprint_id, *, history_limit=50):
    with ops.journal.tx():
        workspace = _member(ops, principal, workspace_id)
        sprint = _sprint(ops.db, workspace_id, sprint_id)
        quality = _quality_access(ops, principal, workspace_id, sprint_id)
        meetings_available = ('pm.meeting.create' in ops.actions and sprint['started_at'] is not None
                              and principal.allows('manage')
                              and (principal.admin or workspace['membership']['role'] in {'owner', 'contributor'}))
        rows = {kind: _history_rows(ops.db, sprint_id, kind, 0, history_limit) for kind in HISTORY}
        goal = dict(ops.db.execute('SELECT * FROM pm_sprint_goal_revisions WHERE revision_id=?', (sprint['goal_revision_id'],)).fetchone())
        coverage = {kind: _coverage(0, entries) for kind, entries in rows.items()}
        result = _checked_read({'version': 1, 'sprint': sprint, 'goal': goal, 'goal_revisions': [],
                'selection_revisions': [], 'selected_items': _selection(ops.db, sprint),
                'history': [], 'history_next_cursors': {kind: 0 if entries else None for kind, entries in rows.items()},
                'timebox_elapsed': time.time() >= _date(sprint['definition']['ends_at']).timestamp(),
                'quality': 'fixed_original_assessments' if quality else 'unassessed',
                **({'quality_summary': quality} if quality else {}), 'review_available': meetings_available, 'retro_available': meetings_available,
                'execution_authorized': False, 'owner_reassignment_available': False,
                'coverage': {'max_bytes': MAX_READ_BYTES, 'histories': coverage}})
        # Deterministic prefix order: Goals, selections, events. Each history has
        # its own continuation; an omitted first record keeps that cursor at zero.
        keys = {'goals': 'goal_revisions', 'selections': 'selection_revisions', 'events': 'history'}
        for kind, entries in rows.items():
            _fill_page(result, result[keys[kind]], entries,
                       lambda row, kind=kind: _history_record(ops.db, kind, row['sequence']), history_limit,
                       result['history_next_cursors'], kind, coverage[kind])
        return result


def list_sprints(ops, principal, workspace_id, *, after=0, limit=50):
    with ops.journal.tx():
        _member(ops, principal, workspace_id)
        _page(after, limit)
        rows = ops.db.execute('SELECT sprint_id,sequence FROM pm_sprints WHERE workspace_id=? AND sequence>? ORDER BY sequence LIMIT ?', (workspace_id, after, limit + 1)).fetchall()
        return _list_page({'version': 1}, 'sprints', rows,
                          lambda row: _sprint(ops.db, workspace_id, row['sprint_id']), after, limit)
