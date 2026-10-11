"""Bounded Follow-up preparation from fixed evidence, never autonomous PM work."""
from __future__ import annotations

import datetime as dt
import json
import re
import time

from . import (
    pm_discussions,
    pm_governance,
    pm_meetings,
    pm_plans,
    pm_profiles,
    pm_snapshots,
    pm_sprints,
    pm_workspace,
)
from .operations import ActionDef, OperationError, OperationService

ACTION = 'pm.follow_up.prepare'
MAX_READ_BYTES = 2_000_000
MAX_INPUTS = 100
MAX_SOURCES = 20
MAX_ACCESS_REFS = 200
_ID = re.compile(r'pmfu_[0-9a-f]{32}')
_json, _digest = pm_snapshots._json, pm_snapshots._digest


def _bad(code, message, status=422):
    return OperationError(code, message, status)


def _only(value, fields):
    if not isinstance(value, dict) or set(value) - set(fields):
        raise _bad('INVALID_PARAMS', 'Unexpected Follow-up fields')


def _member(ops, principal, wid, *, write=False):
    if not principal.allows('observe') or write and not principal.allows('manage'):
        raise _bad('FORBIDDEN', 'Follow-up requires observe; preparation also requires manage', 403)
    return pm_workspace.require_member(ops.db, principal, wid, write=write)


def schema(journal):
    pm_governance.schema(journal)
    pm_meetings.schema(journal)
    with journal.tx():
        journal.db.execute('''CREATE TABLE IF NOT EXISTS pm_follow_up_intakes (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, intake_id TEXT NOT NULL UNIQUE,
            workspace_id TEXT NOT NULL, version INTEGER NOT NULL, document TEXT NOT NULL,
            document_digest TEXT NOT NULL, created_by TEXT NOT NULL, created_at REAL NOT NULL,
            operation_id TEXT NOT NULL UNIQUE, UNIQUE(workspace_id,version))''')
        journal.db.execute('CREATE INDEX IF NOT EXISTS pm_follow_up_workspace ON pm_follow_up_intakes(workspace_id,sequence)')
        for verb in ('UPDATE', 'DELETE'):
            journal.db.execute(f'''CREATE TRIGGER IF NOT EXISTS pm_follow_up_immutable_{verb.lower()}
                BEFORE {verb} ON pm_follow_up_intakes BEGIN SELECT RAISE(ABORT,'Follow-up history is immutable'); END''')


def install(ops):
    schema(ops.journal)
    if ACTION not in ops.actions:
        ops.register(ActionDef(ACTION, 'manage', 'Prepare a sourced Follow-up agenda without execution', _run,
                               _admit, ('workspace_id',), authorize_existing=_authorize_existing))


def head(db, wid):
    row = db.execute('SELECT version,intake_id FROM pm_follow_up_intakes WHERE workspace_id=? ORDER BY sequence DESC LIMIT 1', (wid,)).fetchone()
    return dict(row) if row else {'version': 0, 'intake_id': None}


def _row(db, wid, intake_id):
    if not isinstance(intake_id, str) or not _ID.fullmatch(intake_id):
        raise _bad('PM_FOLLOW_UP_NOT_FOUND', 'Follow-up intake not found', 404)
    row = db.execute('SELECT * FROM pm_follow_up_intakes WHERE workspace_id=? AND intake_id=?', (wid, intake_id)).fetchone()
    if row is None:
        raise _bad('PM_FOLLOW_UP_NOT_FOUND', 'Follow-up intake not found', 404)
    value = {**dict(row), 'document': json.loads(row['document'])}
    if _digest(value['document']) != row['document_digest']:
        raise _bad('PM_FOLLOW_UP_UNAVAILABLE', 'The fixed Follow-up document cannot be verified', 409)
    return value


def _visible(db, principal, wid, refs):
    """Flattened original authority, including any explicitly compared intake."""
    reader = pm_meetings._SourceReader(db)
    for ref in refs:
        if ref['kind'] == 'snapshot':
            row = db.execute('SELECT * FROM pm_document_snapshots WHERE workspace_id=? AND snapshot_id=?', (wid, ref['id'])).fetchone()
            if row is None or row['snapshot_digest'] != ref['digest']:
                return False
            if pm_snapshots._out(reader, principal, row)['snapshot']['visibility']['filtered']:
                return False
        elif ref['kind'] in {'governance', 'meeting'}:
            row = db.execute('SELECT * FROM operations WHERE operation_id=?', (ref['id'],)).fetchone()
            if row is None:
                return False
            op = OperationService._decode(row)
            if op['target'].get('workspace_id') != wid:
                return False
            domain = pm_governance if ref['kind'] == 'governance' else pm_meetings
            if not domain.operation_visible(db, principal, op):
                return False
        else:
            return False
    return bool(refs)


def _access(ops, principal, wid, refs):
    if not _visible(ops.db, principal, wid, refs):
        raise _bad('PM_FOLLOW_UP_SOURCE_UNAVAILABLE', 'Original Follow-up sources are unavailable to this reader', 404)


def _ref(snapshot):
    return {key: snapshot[key] for key in ('snapshot_id', 'snapshot_digest', 'as_of', 'journal_cursor')}


def _governance(ops, principal, wid, identifiers):
    identifiers = pm_sprints._ids(identifiers, 'governance_revision_ids', MAX_SOURCES)
    selected, access = [], []
    for identifier in identifiers:
        if not ops.db.execute('''SELECT 1 FROM pm_governance_revisions v JOIN pm_governance_records r USING(record_id)
            WHERE v.revision_id=? AND r.workspace_id=?''', (identifier, wid)).fetchone():
            raise _bad('PM_FOLLOW_UP_SOURCE_UNAVAILABLE', 'Selected governance revision is unavailable', 404)
        revision = pm_governance._revision(ops.db, identifier)
        record = pm_governance._record(ops.db, wid, revision['record_id'])
        pm_governance._access(ops, principal, record)
        ref = {'kind': 'governance', 'id': revision['operation_id']}
        _access(ops, principal, wid, [ref])
        access.append(ref)
        selected.append({'record_id': record['record_id'], 'kind': record['kind'], 'root_cause_key': record['root_cause_key'],
                         'context': record['context'], 'revision_id': identifier, 'version': revision['version'],
                         'document_digest': revision['document_digest'], 'document': revision['document'],
                         'actor': revision['actor'], 'created_at': revision['created_at'], 'operation_id': revision['operation_id']})
    if len({item['record_id'] for item in selected}) != len(selected):
        raise _bad('INVALID_PARAMS', 'Select one explicit revision per governance record')
    return selected, access


def _meetings(ops, principal, wid, selections):
    if not isinstance(selections, list) or len(selections) > MAX_SOURCES:
        raise _bad('INVALID_PARAMS', 'Select at most twenty fixed meeting follow-ups')
    result, access, seen = [], [], set()
    for item in selections:
        _only(item, ('completion_id', 'follow_up_id', 'result_event_id'))
        if not all(isinstance(item.get(key), str) for key in ('completion_id', 'follow_up_id')):
            raise _bad('INVALID_PARAMS', 'A meeting source needs a completion_id and follow_up_id')
        if 'result_event_id' in item and not isinstance(item['result_event_id'], str):
            raise _bad('INVALID_PARAMS', 'result_event_id must name one fixed result')
        identity = (item['completion_id'], item['follow_up_id'])
        if identity in seen:
            raise _bad('INVALID_PARAMS', 'Select each meeting follow-up once')
        seen.add(identity)
        if not ops.db.execute('''SELECT 1 FROM pm_meeting_completions c JOIN pm_meetings m USING(meeting_id)
            WHERE c.completion_id=? AND m.workspace_id=?''', (item['completion_id'], wid)).fetchone():
            raise _bad('PM_FOLLOW_UP_SOURCE_UNAVAILABLE', 'Selected meeting completion is unavailable', 404)
        completion = pm_meetings._document(ops.db, 'pm_meeting_completions', 'completion_id', item['completion_id'])
        meeting = pm_meetings._meeting(ops.db, wid, completion['meeting_id'])
        context = pm_meetings._document(ops.db, 'pm_meeting_contexts', 'context_revision_id', completion['context_revision_id'])
        follow = next((value for value in completion['document']['follow_ups'] if value['id'] == item['follow_up_id']), None)
        if follow is None:
            raise _bad('PM_FOLLOW_UP_NOT_FOUND', 'Follow-up does not belong to the selected completion', 404)
        refs = [{'kind': 'meeting', 'id': completion['operation_id']}]
        event = None
        if item.get('result_event_id'):
            if not ops.db.execute('''SELECT 1 FROM pm_meeting_follow_up_events WHERE event_id=? AND meeting_id=? AND follow_up_id=?''',
                                  (item['result_event_id'], meeting['meeting_id'], follow['id'])).fetchone():
                raise _bad('PM_FOLLOW_UP_SOURCE_UNAVAILABLE', 'Selected follow-up result is unavailable', 404)
            event = pm_meetings._document(ops.db, 'pm_meeting_follow_up_events', 'event_id', item['result_event_id'])
            if event['meeting_id'] != meeting['meeting_id'] or event['follow_up_id'] != follow['id']:
                raise _bad('INVALID_PARAMS', 'The result must belong to this exact completed follow-up')
            refs.append({'kind': 'meeting', 'id': event['operation_id']})
        _access(ops, principal, wid, refs)
        access.extend(refs)
        result.append({**item, 'meeting_id': meeting['meeting_id'], 'meeting_kind': meeting['kind'],
                       'original_snapshot_ref': context['document']['snapshot_ref'],
                       'completion_digest': _digest(completion['document']), 'follow_up': follow,
                       'decisions': completion['document']['decisions'], 'result': event,
                       'completion_operation_id': completion['operation_id']})
    return result, access


def _input_chain(db, wid, value):
    """Only explicit immutable supersedes edges; no text or slot similarity."""
    chain, current = [value['input_id']], value
    while current.get('supersedes_input_id') and len(chain) <= MAX_INPUTS:
        old = db.execute('SELECT * FROM pm_inputs WHERE workspace_id=? AND thread_id=? AND input_id=?',
                         (wid, value['thread_id'], current['supersedes_input_id'])).fetchone()
        if old is None or old['input_id'] in chain:
            return chain, False
        current = dict(old)
        chain.append(current['input_id'])
    return chain, not current.get('supersedes_input_id')


def _thread_inputs(ops, wid, thread_id):
    if thread_id is None:
        return None, [], {'selected': False, 'truncated': False, 'messages': 'not_interpreted'}
    thread = pm_discussions._thread(ops.db, wid, thread_id)
    rows = ops.db.execute('''SELECT * FROM pm_inputs i WHERE i.workspace_id=? AND i.thread_id=?
        AND NOT EXISTS(SELECT 1 FROM pm_inputs n WHERE n.supersedes_input_id=i.input_id)
        ORDER BY sequence DESC LIMIT ?''', (wid, thread_id, MAX_INPUTS + 1)).fetchall()
    values = [pm_discussions._input_out(row) for row in rows[:MAX_INPUTS]]
    return {'thread_id': thread_id, 'version': thread['version']}, values, {
        'selected': True, 'truncated': len(rows) > MAX_INPUTS, 'returned': len(values),
        'selection': 'latest_current_input_records_by_sequence', 'messages': 'not_interpreted'}


def _issue(wid, kind, identity, text, status, refs, *, basis=None, tier=None, **details):
    result = {'issue_id': 'pmfui_' + _digest([wid, kind, identity])[:32], 'kind': kind,
              'text': text, 'status': status, 'source_refs': refs, 'details': details,
              'ordering_basis': basis, 'agenda_tier': tier, 'business_priority': 'unassessed',
              'verification_status': 'unassessed', 'is_user_question': False}
    result['fingerprint'] = _fingerprint(result)
    return result


def _fingerprint(issue):
    # A refreshed envelope time does not change the original event or answer.
    # Keep freshness visible in details, outside semantic change comparison.
    value = {key: item for key, item in issue.items() if key != 'fingerprint'}
    value['details'] = {key: item for key, item in issue['details'].items() if key != 'snapshot_as_of'}
    return _digest(value)


def _knowledge(ops, wid, snapshot, supplemental):
    baseline = snapshot['baseline']['document']
    selected = {value['input_id']: value for value in baseline['selected_inputs']}
    slot_domains = {slot['key']: slot['domain'] for mode in ('idea', 'planning') for slot in pm_profiles.get(mode)['input_slots']}
    knowledge = {domain: {'baseline_inputs': baseline['sections'][domain]['statements'], 'supplemental_inputs': [],
                         'answer_links': [], 'coverage': baseline['sections'][domain]['coverage']}
                 for domain in pm_profiles.DOMAINS}
    chains = {value['input_id']: _input_chain(ops.db, wid, value) for value in supplemental}
    links, unmapped = {}, []
    for value in supplemental:
        domain = slot_domains.get(value['slot'])
        if domain and value['input_id'] not in selected:
            knowledge[domain]['supplemental_inputs'].append(value)
        elif domain is None:
            unmapped.append(value)
        chain, complete = chains[value['input_id']]
        if value['status'] != 'user_confirmed' or not value.get('confirmed_by'):
            continue
        for old in chain[1:]:
            link = {'source_input_id': old, 'answer_input_id': value['input_id'], 'basis': 'explicit_user_confirmed_supersedes',
                    'confirmed_by': value['confirmed_by'], 'chain_complete': complete, 'accepted_plan_changed': False}
            links[old] = link
            for entry in knowledge.values():
                if any(source['input_id'] == old for source in entry['baseline_inputs']):
                    entry['answer_links'].append(link)
    candidates = {**selected, **{value['input_id']: value for value in supplemental}}
    issues = []
    for identifier, value in candidates.items():
        if value['status'] not in {'unresolved', 'conflicting'}:
            continue
        chain, complete = _input_chain(ops.db, wid, value)
        # A selected baseline and its current successor are one explicit issue family.
        identity = chain[-1] if complete else identifier
        answer = links.get(identifier)
        source = {'kind': 'plan_input' if identifier in selected else 'thread_input', 'input_id': identifier,
                  'plan_revision_id': snapshot['baseline']['document_revision'] if identifier in selected else None}
        issues.append(_issue(wid, 'input', identity, value['value'], 'answer_recorded' if answer else value['status'], [source],
                             basis=None if answer else 'explicit_conflicting_input' if value['status'] == 'conflicting' else 'explicit_unresolved_input',
                             tier=None if answer else 2 if value['status'] == 'conflicting' else 3,
                             answer_link=answer, source_status=value['status'], supersedes_chain_complete=complete))
    grouped = {}
    for issue in issues:
        previous = grouped.get(issue['issue_id'])
        if previous:
            issue['source_refs'] = previous['source_refs'] + issue['source_refs']
            issue['fingerprint'] = _fingerprint(issue)
        grouped[issue['issue_id']] = issue
    return knowledge, list(grouped.values()), unmapped, all(complete for _, complete in chains.values())


def _issues(wid, snapshot, governance, meetings, as_of):
    issues = []
    for item in governance:
        doc = item['document']
        due = bool(doc['review_at'] and dt.datetime.fromisoformat(doc['review_at'].replace('Z', '+00:00')).timestamp() <= as_of)
        decision = doc['decision']
        basis, tier = ('explicit_review_at_reached', 0) if due else (None, None)
        status = doc['status']
        if decision:
            status, basis, tier = 'direction_recorded', None, None
        elif status == 'reported_resolved' and not due:
            basis, tier = 'reported_resolution_verification_unassessed', 4
        issues.append(_issue(wid, 'governance', item['record_id'], doc['title'], status,
            [{'kind': 'governance_revision', 'record_id': item['record_id'], 'revision_id': item['revision_id']}],
            basis=basis, tier=tier, review_at=doc['review_at'], owner_actor=doc['owner_actor'],
            source_kind=item['kind'], assessment_basis=doc['assessment_basis'], answer_link=decision,
            adoption=doc['adoption'], accepted_plan_changed=False))
    for item in meetings:
        follow, event = item['follow_up'], item['result']
        status = event['document']['status'] if event else 'open'
        basis, tier = {'blocked': ('explicit_follow_up_blocked_report', 1),
                       'reported_done': ('reported_done_verification_unassessed', 4)}.get(status, (None, None))
        issues.append(_issue(wid, 'meeting_follow_up', [item['completion_id'], item['follow_up_id']], follow['text'], status,
            [{'kind': 'meeting_follow_up', 'completion_id': item['completion_id'], 'follow_up_id': item['follow_up_id'],
              'result_event_id': item.get('result_event_id')}], basis=basis, tier=tier,
            owner_actor=follow['owner_actor'], expected_effect=follow['expected_effect'], verification=follow['verification'],
            result=event['document']['result'] if event else None, assessment_basis='attributed_follow_up_report'))
    observed = {}
    for item in snapshot['evidence']:
        facts = item['document']['facts']
        status = facts.get('state') or facts.get('status')
        if item['source_kind'] not in {'execution_state', 'operation_status'}:
            continue
        observed.setdefault((item['source_kind'], item['source_id']), []).append((item, status))
    for identity, values in observed.items():
        states = {state for _, state in values}
        if not states & {'needs_ted', 'needs_attention', 'failed', 'waiting'}:
            continue
        basis, tier = ('observed_central_attention_state', 1) if states & {'needs_ted', 'needs_attention', 'failed'} else (None, None)
        status = next(iter(states)) if len(states) == 1 else 'multiple_captured_states'
        issues.append(_issue(wid, 'evidence', identity, f'{identity[0]}: {status}', status,
            [{'kind': 'evidence', 'evidence_id': item['evidence_id'], 'fact_digest': item['fact_digest']} for item, _ in values],
            basis=basis, tier=tier, observations=[{'evidence_id': item['evidence_id'], 'status': state,
                'source_observed_at': item['source_observed_at']} for item, state in values],
            snapshot_as_of=snapshot['as_of'], current_state='not_checked'))
    return issues


def _check(ops, principal, target, params, pre):
    if not all(isinstance(value, dict) for value in (target, params, pre)):
        raise _bad('INVALID_PARAMS', 'Supply Follow-up target, params and preconditions as objects')
    wid = target.get('workspace_id')
    _member(ops, principal, wid, write=True)
    if set(target) != {'workspace_id'}:
        raise _bad('INVALID_TARGET', 'Select exactly one workspace')
    _only(params, ('snapshot_id', 'thread_id', 'governance_revision_ids', 'meeting_follow_up_refs', 'previous_intake_id'))
    expected = {'expected_version', 'expected_plan_version'} | ({'expected_thread_version'} if 'thread_id' in params else set())
    if set(pre) != expected or any(type(pre[key]) is not int or pre[key] < 0 for key in expected):
        raise _bad('PRECONDITION_REQUIRED', 'Provide exact Follow-up and plan head versions, and the selected thread version')
    if head(ops.db, wid)['version'] != pre['expected_version'] or pm_plans.head(ops.db, wid)['version'] != pre['expected_plan_version']:
        raise _bad('VERSION_CONFLICT', 'Read current Follow-up and plan versions before preparation', 409)
    snapshot = pm_governance._snapshot(ops, principal, wid, params.get('snapshot_id'))
    if 'thread_id' in params and not isinstance(params['thread_id'], str):
        raise _bad('INVALID_PARAMS', 'thread_id must identify one selected discussion')
    thread, inputs, input_coverage = _thread_inputs(ops, wid, params.get('thread_id'))
    if thread and thread['version'] != pre['expected_thread_version']:
        raise _bad('VERSION_CONFLICT', 'Read the exact thread version before selecting its InputRecords', 409)
    governance, gov_access = _governance(ops, principal, wid, params.get('governance_revision_ids', []))
    meetings, meeting_access = _meetings(ops, principal, wid, params.get('meeting_follow_up_refs', []))
    access = [{'kind': 'snapshot', 'id': snapshot['snapshot_id'], 'digest': snapshot['snapshot_digest']}, *gov_access, *meeting_access]
    previous = None
    if 'previous_intake_id' in params:
        previous = _row(ops.db, wid, params['previous_intake_id'])
        access.extend(previous['document']['access_refs'])
    access = list({_json(ref): ref for ref in access}.values())
    if len(access) > MAX_ACCESS_REFS:
        raise _bad('PM_FOLLOW_UP_TOO_LARGE', 'Select a shorter comparison chain with at most 200 original authority references', 413)
    _access(ops, principal, wid, access)
    return snapshot, thread, inputs, input_coverage, governance, meetings, access, previous


def _admit(ops, principal, target, params, pre):
    *_, access, _ = _check(ops, principal, target, params, pre)
    return {'principal': pm_workspace.principal_binding(principal), 'follow_up_access_refs': access}


def _authorize_existing(ops, principal, op, verb):
    _member(ops, principal, op['target']['workspace_id'], write=True)
    if verb != 'replay' and principal.actor != op['actor']:
        raise _bad('FORBIDDEN', 'Only the original requester controls this preparation operation', 403)
    if not operation_visible(ops.db, principal, op):
        raise _bad('PM_FOLLOW_UP_SOURCE_UNAVAILABLE', 'Original Follow-up sources are unavailable', 404)


def _prepare(ops, wid, sources):
    snapshot, thread, inputs, input_coverage, governance, meetings, access, previous = sources
    now = time.time()
    knowledge, issues, unmapped, complete = _knowledge(ops, wid, snapshot, inputs)
    issues += _issues(wid, snapshot, governance, meetings, now)
    prior = {issue['issue_id']: issue['fingerprint'] for issue in previous['document']['issues']} if previous else {}
    present = {issue['issue_id']: issue['fingerprint'] for issue in issues}
    changes = {key: [] for key in ('added', 'changed', 'unchanged', 'no_longer_selected')}
    for identifier, fingerprint in present.items():
        changes['added' if identifier not in prior else 'unchanged' if prior[identifier] == fingerprint else 'changed'].append(identifier)
    changes['no_longer_selected'] = sorted(set(prior) - set(present))
    ordered = sorted((issue for issue in issues if issue['agenda_tier'] is not None), key=lambda v: (v['agenda_tier'], v['issue_id']))
    agenda = [{'issue_id': value['issue_id'], 'text': value['text'], 'ordering_basis': value['ordering_basis'],
               'source_refs': value['source_refs'], 'is_user_question': False} for value in ordered[:3]]
    baseline = snapshot['baseline']
    return {'as_of': now, 'journal_cursor': ops.journal.api_head(), 'profile_id': 'pm.follow_up', 'profile_version': pm_profiles.VERSION,
        'snapshot_ref': _ref(snapshot), 'plan_ref': {key: baseline[key] for key in ('document_revision', 'scope_revision', 'document_digest', 'plan_head_version')},
        'thread_ref': thread, 'selected_governance_refs': governance, 'selected_meeting_follow_up_refs': meetings,
        'previous_intake_id': previous['intake_id'] if previous else None, 'knowledge': knowledge, 'unmapped_inputs': unmapped,
        'issues': issues, 'agenda': agenda, 'changes': changes, 'access_refs': access,
        'coverage': {'scope': 'explicitly_selected_sources_only', 'snapshot': snapshot['coverage'], 'thread': input_coverage,
                     'supersedes_chains_complete': complete, 'agenda_candidates': len(ordered), 'agenda_limit': 3,
                     'agenda_order': 'explicit_review_time_then_attention_then_conflicting_then_unresolved_then_unverified_report; stable_id_ties',
                     'business_priority': 'unassessed', 'question_generation': 'not_performed', 'unselected_sources': 'not_scanned',
                     'network_checked': False, 'quality': 'unassessed', 'cost': 'unknown', 'max_bytes': MAX_READ_BYTES},
        'accepted_plan_changed': False, 'execution_authorized': False, 'runtime_invoked': False}


def _checked(value):
    if pm_sprints._read_size(value) > MAX_READ_BYTES:
        raise _bad('PM_FOLLOW_UP_TOO_LARGE', 'A complete Follow-up wire document must fit 2,000,000 UTF-8 bytes; select fewer sources', 413)
    return value


async def _run(ctx):
    def apply():
        ops, wid = ctx.service, ctx.target['workspace_id']
        sources = _check(ops, pm_workspace.admitted_principal(ctx), ctx.target, ctx.params, ctx.preconditions)
        document = _prepare(ops, wid, sources)
        intake_id, version = 'pmfu_' + ctx.operation_id[3:], ctx.preconditions['expected_version'] + 1
        value = {'intake_id': intake_id, 'workspace_id': wid, 'version': version, 'document': document,
                 'document_digest': _digest(document), 'created_by': ctx.actor, 'created_at': document['as_of'], 'operation_id': ctx.operation_id}
        # The real GET wrapper, not compact DB JSON or character counts. Reserve a
        # maximal SQLite sequence so later reads cannot exceed the admitted bound.
        _checked({'version': 1, 'workspace_id': wid, 'head_version': 9223372036854775807,
                  'intake': {**value, 'sequence': 9223372036854775807}})
        ops.db.execute('''INSERT INTO pm_follow_up_intakes(intake_id,workspace_id,version,document,document_digest,created_by,created_at,operation_id)
            VALUES(?,?,?,?,?,?,?,?)''', (intake_id, wid, version, _json(document), value['document_digest'], ctx.actor, value['created_at'], ctx.operation_id))
        result = {'workspace_id': wid, 'intake_id': intake_id, 'version': version, 'document_digest': value['document_digest'],
                  'agenda_count': len(document['agenda']), 'execution_authorized': False}
        ops.journal.api_event('pm_follow_up', intake_id, ACTION, {**result, 'operation_id': ctx.operation_id}, actor=ctx.actor)
        return result
    return ctx.effect('pm.follow_up.prepare', apply)


def operation_visible(db, principal, operation):
    try:
        row = db.execute('SELECT * FROM operations WHERE operation_id=?', (operation['operation_id'],)).fetchone()
        if row is not None and row['action'] == 'pm.follow_up.analyze':
            from .pm_follow_up_analysis import operation_visible as analysis_visible
            return analysis_visible(db, principal, operation)
        if row is None or row['action'] != ACTION or not principal.allows('observe'):
            return False
        op = OperationService._decode(row)
        wid = op['target']['workspace_id']
        pm_workspace.require_member(db, principal, wid)
        refs = ((op['external_refs'] or {}).get('admission_binding') or {}).get('follow_up_access_refs')
        return isinstance(refs, list) and _visible(db, principal, wid, refs)
    except (OperationError, KeyError, TypeError, ValueError):
        return False


def event_visible(db, principal, event):
    identifier = (event.get('body') or {}).get('operation_id')
    return bool(identifier and operation_visible(db, principal, {'operation_id': identifier}))


def _view(ops, principal, wid, identifier):
    value = _row(ops.db, wid, identifier)
    _access(ops, principal, wid, value['document']['access_refs'])
    return value


def get_intake(ops, principal, workspace_id, intake_id):
    with ops.journal.tx():
        _member(ops, principal, workspace_id)
        return _checked({'version': 1, 'workspace_id': workspace_id, 'head_version': head(ops.db, workspace_id)['version'],
                         'intake': _view(ops, principal, workspace_id, intake_id)})


def latest_intake(ops, principal, workspace_id):
    with ops.journal.tx():
        _member(ops, principal, workspace_id)
        current = head(ops.db, workspace_id)
        return _checked({'version': 1, 'workspace_id': workspace_id, 'head_version': current['version'],
                         'intake': _view(ops, principal, workspace_id, current['intake_id']) if current['intake_id'] else None})


def list_intakes(ops, principal, workspace_id, *, after=0, limit=50):
    with ops.journal.tx():
        _member(ops, principal, workspace_id)
        pm_sprints._page(after, limit)
        rows = ops.db.execute('SELECT * FROM pm_follow_up_intakes WHERE workspace_id=? AND sequence>? ORDER BY sequence LIMIT ?',
                              (workspace_id, after, limit + 1)).fetchall()
        result = {'version': 1, 'workspace_id': workspace_id, 'head_version': head(ops.db, workspace_id)['version'],
                  'intakes': [], 'next_cursor': None, 'coverage': {'visibility_filtered': False, 'scanned': 0, 'max_bytes': MAX_READ_BYTES}}
        for row in rows[:limit]:
            value = _row(ops.db, workspace_id, row['intake_id'])
            if _visible(ops.db, principal, workspace_id, value['document']['access_refs']):
                result['intakes'].append({key: value[key] for key in value if key != 'document'})
            else:
                result['coverage']['visibility_filtered'] = True
            result['coverage']['scanned'] += 1
        result['next_cursor'] = rows[limit - 1]['sequence'] if len(rows) > limit else None
        result['coverage'].update(returned=len(result['intakes']), has_more=result['next_cursor'] is not None)
        return _checked(result)
