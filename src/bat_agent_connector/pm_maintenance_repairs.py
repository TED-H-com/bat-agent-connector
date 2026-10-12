"""Fixed policy repair admission composed on the existing durable execution engine."""
from __future__ import annotations

import json
import re
import time
from contextvars import ContextVar

from . import pm_github_observation as github
from . import pm_lifecycle_gates, pm_source_lineage, pm_workspace, work_items
from . import pm_maintenance_checks as checks
from . import pm_maintenance_findings as findings
from . import pm_maintenance_policy as policies
from . import pm_maintenance_repair_rules as rules
from . import pm_release_baselines as baselines
from . import pm_workload as workload
from .operations import ActionDef, OperationError, OperationService, Wait

ACTION = 'pm.maintenance.repair'
RECORD = 'pm.maintenance.repair.evidence.record'
REPAIR = re.compile('pmrp_[0-9a-f]{32}')
REQUIRED = frozenset({'observe', 'manage', 'start'})
MAX_READ_BYTES = 384_000
TERMINAL = frozenset({'succeeded', 'failed', 'cancelled'})
_READING = ContextVar('pm_maintenance_repair_reading', default=frozenset())
_RESERVING_WORKLOAD = ContextVar('pm_maintenance_repair_reserving_workload', default=None)


def _bad(code, message, status=409):
    return OperationError(code, message, status)


def installed(db):
    return pm_source_lineage._table(db, 'pm_maintenance_repairs')


def schema(journal):
    from . import pm_maintenance_repair_delivery as delivery
    from . import pm_maintenance_repair_publication as publication
    with journal.tx():
        publication.schema(journal)
        delivery.schema(journal)
        journal.db.execute('''CREATE TABLE IF NOT EXISTS pm_maintenance_repairs (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,repair_id TEXT NOT NULL UNIQUE,
            operation_id TEXT NOT NULL UNIQUE,workspace_id TEXT NOT NULL,policy_id TEXT NOT NULL,
            finding_id TEXT NOT NULL,work_item_id TEXT NOT NULL,context TEXT NOT NULL,context_digest TEXT NOT NULL,
            created_at REAL NOT NULL,deadline_at REAL NOT NULL,budget_period INTEGER NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,finished_at REAL,UNIQUE(workspace_id,finding_id),UNIQUE(work_item_id))''')
        journal.db.execute('''CREATE TABLE IF NOT EXISTS pm_maintenance_repair_children (
            parent_id TEXT PRIMARY KEY,operation_id TEXT NOT NULL UNIQUE,request TEXT NOT NULL,request_digest TEXT NOT NULL)''')
        journal.db.execute('''CREATE TABLE IF NOT EXISTS pm_maintenance_repair_evidence (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,parent_id TEXT NOT NULL,operation_id TEXT NOT NULL UNIQUE,
            version INTEGER NOT NULL,document TEXT NOT NULL,document_digest TEXT NOT NULL,created_at REAL NOT NULL)''')
        journal.db.execute('CREATE TABLE IF NOT EXISTS pm_maintenance_repair_cursor (singleton INTEGER PRIMARY KEY CHECK(singleton=1), policy_sequence INTEGER NOT NULL, run_sequence INTEGER NOT NULL)')
        journal.db.execute('INSERT OR IGNORE INTO pm_maintenance_repair_cursor VALUES(1,0,0)')
        journal.db.execute('CREATE INDEX IF NOT EXISTS pm_maintenance_repair_evidence_parent ON pm_maintenance_repair_evidence(parent_id,sequence)')
        journal.db.execute('''CREATE TABLE IF NOT EXISTS pm_maintenance_repair_retries (
            parent_id TEXT NOT NULL,operation_id TEXT NOT NULL,transition_digest TEXT NOT NULL,step_name TEXT NOT NULL,
            created_at REAL NOT NULL,PRIMARY KEY(parent_id,operation_id,transition_digest))''')
        for table in ('pm_maintenance_repair_children', 'pm_maintenance_repair_evidence', 'pm_maintenance_repair_retries'):
            for verb in ('UPDATE', 'DELETE'):
                journal.db.execute(f'CREATE TRIGGER IF NOT EXISTS {table}_{verb.lower()}_immutable BEFORE {verb} ON {table} '
                                  "BEGIN SELECT RAISE(ABORT,'Repair history is immutable'); END")
        journal.db.execute('''CREATE TRIGGER IF NOT EXISTS pm_maintenance_repairs_identity_immutable
            BEFORE UPDATE OF repair_id,operation_id,workspace_id,policy_id,finding_id,work_item_id,context,context_digest,
                created_at,deadline_at,budget_period ON pm_maintenance_repairs
            BEGIN SELECT RAISE(ABORT,'Repair identity is immutable'); END''')
        journal.db.execute('''CREATE TRIGGER IF NOT EXISTS pm_maintenance_repairs_delete_immutable BEFORE DELETE ON pm_maintenance_repairs
            BEGIN SELECT RAISE(ABORT,'Repair identity is immutable'); END''')


def install(ops):
    schema(ops.journal)
    from . import pm_maintenance_repair_runtime as runtime
    runtime.install(ops)
    from . import pm_maintenance_repair_evidence as evidence
    for action, run, admit, targets in ((ACTION, _run, _admit, ('workspace_id',)),
            (RECORD, evidence.run, evidence.admit, ('workspace_id', 'repair_id'))):
        if action not in ops.actions:
            ops.register(ActionDef(action, 'start' if action == ACTION else 'manage', 'Execute an explicit fixed adopted maintenance repair rule',
                run, admit, targets, authorize_existing=_authorize_existing))


def capabilities(ops=None):
    return {'version': 1, 'actions': [ACTION, RECORD], 'policy_field': 'repair_rules',
        'sources': ['issues'], 'engine': 'repository.continue', 'automatic_new_work_item': False,
        'automatic_pr_creation': ops is not None and 'github.pr.publish' in ops.actions, 'alert_applicability_supported': False,
        'cost_coverage': 'model_provider_unmeasured', 'hard_cost_ceiling_supported': False,
        'automatic_semantic_acceptance': False, 'automatic_release': False,
        'available': ops is not None and ACTION in ops.actions and 'repository.continue' in ops.actions}


def _bounded(value):
    if len(json.dumps(value, ensure_ascii=False).encode()) > MAX_READ_BYTES:
        raise _bad('PM_REPAIR_READ_TOO_LARGE', 'One whole repair record exceeds the read bound', 413)
    return value


def _operation(ops, oid):
    row = ops.db.execute('SELECT * FROM operations WHERE operation_id=?', (oid,)).fetchone()
    if not row:
        raise _bad('NOT_FOUND', 'Original repair operation unavailable', 404)
    return OperationService._decode(row)


def _intent(op):
    binding = (op.get('external_refs') or {}).get('admission_binding') or {}
    value = binding.get('pm_maintenance_repair')
    if not isinstance(value, dict) or checks._digest(value) != binding.get('pm_maintenance_repair_digest'):
        raise _bad('PM_REPAIR_CONTEXT_INVALID', 'Original repair context is unavailable')
    return value


def _row(ops, wid, rid):
    if not isinstance(rid, str) or not REPAIR.fullmatch(rid):
        raise _bad('NOT_FOUND', 'Repair unavailable', 404)
    row = ops.db.execute('SELECT * FROM pm_maintenance_repairs WHERE workspace_id=? AND repair_id=?', (wid, rid)).fetchone()
    if not row:
        raise _bad('NOT_FOUND', 'Repair unavailable', 404)
    result = {**dict(row), 'context': json.loads(row['context'])}
    if checks._digest(result['context']) != row['context_digest']:
        raise _bad('PM_REPAIR_CONTEXT_INVALID', 'Original repair context changed')
    return result


def _record_for(ops, op):
    return _row(ops, op['target']['workspace_id'], 'pmrp_' + op['operation_id'][3:])


def _access(ops, principal, op):
    wid, intent = op['target']['workspace_id'], _intent(op)
    workload._intent_access(ops, principal, wid, intent['check']['workload'])
    original = github._record(ops.db, wid, intent['finding_ref']['observation_id'])
    github._access(ops, principal, wid, original)
    if original['document_digest'] != intent['finding_ref']['document_digest']:
        raise _bad('NOT_FOUND', 'Original finding unavailable', 404)
    selected = findings.get_finding(ops, principal, wid, **intent['finding_ref'])
    if selected['finding'] != intent['finding']:
        raise _bad('NOT_FOUND', 'Original finding changed', 404)
    baseline = baselines._record(ops.db, wid, intent['check']['workload']['baseline_id'])
    rules.sources(ops, principal, wid, intent['rule'], baseline, ready=False)
    snapshot = workload._record(ops.db, wid, intent['workload_ref']['snapshot_id'])
    workload._access(ops, principal, wid, snapshot)
    if snapshot['document_digest'] != intent['workload_ref']['document_digest']:
        raise _bad('NOT_FOUND', 'Original workload source changed', 404)


def _source_read_key(db, principal, operation_id):
    return (id(db), principal.actor, principal.scopes, principal.admin,
        principal.credential_id, operation_id)


def source_backedge(db, principal, operation_id):
    """Only an existing full-source traversal may suppress its own backedge."""
    return _source_read_key(db, principal, operation_id) in _READING.get()


def _full_access(ops, principal, op):
    # A receipt may refer to this repair through its owned child. Check every
    # source on the outer traversal, while suppressing only that same back edge.
    key = _source_read_key(ops.db, principal, op['operation_id'])
    checking = _READING.get()
    if key in checking:
        return
    if len(checking) >= 64:
        raise _bad('NOT_FOUND', 'Repair source traversal is unavailable', 404)
    token = _READING.set(checking | {key})
    try:
        _access(ops, principal, op)
        from . import pm_maintenance_repair_evidence as evidence
        evidence.require_evidence_sources(ops, principal, op)
        from . import pm_maintenance_repair_delivery as delivery
        delivery.require_sources(ops, principal, op)
    finally:
        _READING.reset(token)


def source_visible(db, principal, original):
    try:
        reader = workload._Reader(db)
        op = _operation(reader, original['operation_id'])
        if op['action'] != ACTION:
            return False
        with baselines._snapshot(db):
            _full_access(reader, principal, op)
        return True
    except (OperationError, KeyError, TypeError, ValueError):
        return False


def operation_visible(db, principal, original):
    try:
        reader = workload._Reader(db)
        op = _operation(reader, original['operation_id'])
        if op['action'] == RECORD:
            row = _row(reader, op['target']['workspace_id'], op['target']['repair_id'])
            parent = _operation(reader, row['operation_id'])
            _full_access(reader, principal, parent)
            from . import pm_maintenance_repair_evidence as evidence
            bound = (op['external_refs'].get('admission_binding') or {})['pm_repair_evidence']
            evidence.require_selected_sources(reader, principal, parent, op['params']['evidence'], bound['proof'])
            return True
        return source_visible(db, principal, op)
    except (OperationError, KeyError, TypeError, ValueError):
        return False


def _authorize_existing(ops, principal, op, control):
    if not principal.allows('manage') or not operation_visible(ops.db, principal, op):
        raise _bad('NOT_FOUND', 'Original repair source unavailable', 404)


def _principal(ops, record, *, scopes=REQUIRED):
    return checks._current_principal(ops, record, time.time(), required=scopes)


def _existing(ops, wid, finding_id, work_id):
    return ops.db.execute('SELECT repair_id,operation_id FROM pm_maintenance_repairs '
        'WHERE (workspace_id=? AND finding_id=?) OR work_item_id=? ORDER BY sequence LIMIT 1',
        (wid, finding_id, work_id)).fetchone()


def _unstarted(ops, work_id):
    item = work_items._get_item(ops.db, work_id, active=True)
    if work_items.completion(item)['approved'] or item['state'] not in ('todo', 'waiting'):
        raise _bad('PM_REPAIR_WORK_EXISTS', 'The selected work is already active or accepted')
    if ops.db.execute("SELECT 1 FROM work_item_links WHERE work_item_id=? AND removed_at IS NULL "
                      "AND kind IN ('session','task','operation','pull_request') LIMIT 1", (work_id,)).fetchone():
        raise _bad('PM_REPAIR_WORK_EXISTS', 'Existing work or delivery must be reconciled first')
    # Durable original attempts count even if their operation has no final reply.
    if ops.db.execute("SELECT 1 FROM operations WHERE action='repository.continue' "
                      "AND json_extract(params,'$.work_item_id')=? LIMIT 1", (work_id,)).fetchone():
        raise _bad('PM_REPAIR_WORK_EXISTS', 'An original execution already exists for this work')


def _check(ops, principal, target, params, pre, *, reserving=False):
    if (set(target) != {'workspace_id'} or set(params) != {'policy_id', 'rule_key', 'finding_ref'}
            or not isinstance(params['rule_key'], str)):
        raise _bad('INVALID_PARAMS', 'Select one fixed policy rule and finding occurrence', 422)
    if any(not principal.allows(scope) for scope in REQUIRED):
        raise _bad('FORBIDDEN', 'Repair dispatch requires observe, manage and start together', 403)
    wid = target['workspace_id']
    check = checks._check(ops, principal, target, {'policy_id': params['policy_id']}, pre)
    record = policies._record(ops.db, wid, params['policy_id'])
    live = _principal(ops, record)
    if principal.actor != live.actor:
        raise _bad('PM_REPAIR_OWNER_CHANGED', 'Only the original policy owner can dispatch this rule', 403)
    policy = record['document']['policy']
    if policy['limits']['cost_microusd'] is not None:
        raise _bad('PM_REPAIR_COST_UNSUPPORTED', 'A hard model/provider cost ceiling cannot be enforced by this adapter')
    if 'repository.continue' not in ops.actions:
        raise _bad('PM_REPAIR_EXECUTION_UNAVAILABLE', 'The existing owned repository execution capability is unavailable')
    rule = next((value for value in policy.get('repair_rules', []) if value['key'] == params['rule_key']), None)
    if rule is None:
        raise _bad('PM_REPAIR_RULE_UNAVAILABLE', 'The selected explicit repair rule is not currently adopted')
    ref = params['finding_ref']
    if not isinstance(ref, dict) or set(ref) != {'observation_id', 'document_digest', 'finding_id'}:
        raise _bad('INVALID_PARAMS', 'Select the exact original finding reference', 422)
    occurrence = findings.get_finding(ops, live, wid, **ref)
    found = occurrence['finding']
    observation = github._record(ops.db, wid, ref['observation_id'])
    doc = observation['document']
    current = ops.db.execute('SELECT observation_id FROM pm_github_observations WHERE workspace_id=? ORDER BY sequence DESC LIMIT 1', (wid,)).fetchone()
    if (current is None or current[0] != ref['observation_id'] or observation['source_binding'] != check['workload']
            or doc['evidence']['status'] != 'complete' or occurrence['source_status'] != 'complete'
            or not doc['as_of'] <= time.time() < doc['expires_at']):
        raise _bad('PM_REPAIR_FINDING_STALE', 'The finding requires a fresh complete current policy observation')
    if occurrence['source'] != 'issues' or not set(rule['labels_all']) <= set(found['facts']['labels']):
        raise _bad('PM_REPAIR_APPLICABILITY_UNPROVEN', 'Only the adopted exact Issue predicate authorizes this fixed package')
    if found['existing_pull_request'] is not None or found.get('existing_work'):
        raise _bad('PM_REPAIR_WORK_EXISTS', 'The finding already has a known work or pull-request association')
    baseline = baselines._record(ops.db, wid, check['workload']['baseline_id'])
    package = rules.sources(ops, live, wid, rule, baseline)
    work_id = package['work_item_id']
    old = _existing(ops, wid, found['finding_id'], work_id)
    if old:
        raise _bad('PM_REPAIR_ALREADY_RESERVED', 'Read the existing repair ' + old['repair_id'])
    _unstarted(ops, work_id)
    row = ops.db.execute('SELECT snapshot_id FROM pm_workload_snapshots WHERE workspace_id=? ORDER BY sequence DESC LIMIT 1', (wid,)).fetchone()
    if not row:
        raise _bad('PM_REPAIR_WORKLOAD_UNAVAILABLE', 'A current complete stable idle workload is required')
    active = policies.active_record(ops, live, wid)
    gate = workload.evaluate_for_maintenance(ops, live, wid, row[0], active)
    if not gate['eligible']:
        raise _bad('PM_REPAIR_WORKLOAD_UNAVAILABLE', ', '.join(gate['reasons']))
    snapshot = workload._record(ops.db, wid, row[0])
    if reserving:
        checks._capacity(ops, wid, check, time.time())
    return {'check': check, 'rule': rule, 'finding_ref': ref, 'finding': found, 'work_item_id': work_id,
        'workload_ref': {'snapshot_id': snapshot['snapshot_id'], 'document_digest': snapshot['document_digest']}}


def _admit(ops, principal, target, params, pre):
    with ops.journal.tx():
        intent = _check(ops, principal, target, params, pre)
    return {'principal': pm_workspace.principal_binding(principal), 'pm_maintenance_repair': intent,
        'pm_maintenance_repair_digest': checks._digest(intent), 'pm_lineage': {'workspace_id': target['workspace_id']}}


def _guard(ops, parent, *, scopes=REQUIRED):
    if parent['action'] != ACTION or parent['cancel_requested'] or parent['status'] in TERMINAL | {'needs_attention'}:
        raise _bad('PM_REPAIR_STOPPED', 'The original repair stopped new investment')
    intent, wid = _intent(parent), parent['target']['workspace_id']
    record = policies._record(ops.db, wid, intent['check']['workload']['policy_id'])
    live = _principal(ops, record, scopes=scopes)
    current = checks._check(ops, live, parent['target'], {'policy_id': record['policy_id']}, parent['preconditions'])
    if current != intent['check']:
        raise _bad('PM_REPAIR_CONTEXT_CHANGED', 'The original policy, baseline or lifecycle epoch changed')
    _full_access(ops, live, parent)
    baseline = baselines._record(ops.db, wid, intent['check']['workload']['baseline_id'])
    rules.sources(ops, live, wid, intent['rule'], baseline)
    row = _record_for(ops, parent)
    if row['finished_at'] is not None or not row['created_at'] <= time.time() < row['deadline_at']:
        raise _bad('PM_REPAIR_DEADLINE', 'This repair cannot admit another effect after its original deadline')
    return live


def _request(parent):
    return {'action': 'repository.continue', **_intent(parent)['rule']['handoff'],
            'idempotency_key': 'pm-repair:' + parent['operation_id']}


def _child(ops, parent):
    link = ops.db.execute('SELECT * FROM pm_maintenance_repair_children WHERE parent_id=?', (parent['operation_id'],)).fetchone()
    if link is None:
        return None
    child, request = _operation(ops, link['operation_id']), json.loads(link['request'])
    if (request != _request(parent) or checks._digest(request) != link['request_digest']
            or any(child.get(key) != value for key, value in request.items()) or child['actor'] != parent['actor']
            or any((((child.get('external_refs') or {}).get('admission_binding') or {}).get('controller_authority') or {}).get(key)
                   != _intent(parent)['check']['controller'][key] for key in ('server_id', 'controller_id', 'actor'))
            or (child.get('external_refs') or {}).get('pm_maintenance_repair_parent') != parent['operation_id']):
        raise _bad('PM_REPAIR_CHILD_CHANGED', 'The child lacks its exact original private repair relation')
    return child


def require_child(ops, operation):
    link = (operation.get('external_refs') or {}).get('pm_maintenance_repair_parent')
    if not installed(ops.db):
        if link is None:
            return
        raise _bad('PM_REPAIR_CHILD_CHANGED', 'Unproven repair parent')
    row = ops.db.execute('SELECT parent_id FROM pm_maintenance_repair_children WHERE operation_id=?', (operation['operation_id'],)).fetchone()
    if row is None and link is None:
        return
    if row is None or link != row[0]:
        raise _bad('PM_REPAIR_CHILD_CHANGED', 'Unproven repair parent')
    parent = _operation(ops, row[0])
    child = _child(ops, parent)
    if any(child.get(key) != operation.get(key) for key in ('operation_id', 'action', 'target', 'params', 'preconditions', 'actor', 'idempotency_key')):
        raise _bad('PM_REPAIR_CHILD_CHANGED', 'Repair child envelope changed')
    _guard(ops, parent)
    return _record_for(ops, parent)['deadline_at']


def admit_restart(ops, operation_id, step_name, request):
    """Charge only proven-unsent restarts in the existing atomic step transition."""
    operation = _operation(ops, operation_id)
    if _local_record_or_stop(operation):
        return
    if not installed(ops.db):
        require_child(ops, operation)
        return
    original_ids = pm_source_lineage.roots(ops.db, 'operation', operation_id)
    parents = [_operation(ops, oid) for oid in sorted(original_ids)]
    parents = [parent for parent in parents if parent['action'] == ACTION]
    if not parents:
        require_child(ops, operation)  # Reject an invented private parent claim.
        return
    if not ops.db.in_transaction:
        raise _bad('PM_REPAIR_RETRY_TRANSACTION', 'Retry reservation requires the original journal transaction')
    require_new_effect(ops, operation)
    step = ops.db.execute('SELECT * FROM operation_steps WHERE operation_id=? AND name=?', (operation_id, step_name)).fetchone()
    if (not step or step['status'] not in {'started', 'uncertain'} or json.loads(step['request']) != request):
        raise _bad('PM_REPAIR_RETRY_UNPROVEN', 'Only the same unresolved original request may reserve a proven-unsent retry')
    event = ops.db.execute("SELECT MAX(seq) FROM api_events WHERE resource_type='operation' AND resource_id=? "
        "AND kind LIKE 'operation.step.%' AND json_extract(body,'$.step')=?", (operation_id, step_name)).fetchone()[0]
    transition = checks._digest({'step': dict(step), 'original_step_event': event})
    for parent in parents:
        if ops.db.execute('SELECT 1 FROM pm_maintenance_repair_retries WHERE parent_id=? AND operation_id=? AND transition_digest=?',
                          (parent['operation_id'], operation_id, transition)).fetchone():
            continue
        count = ops.db.execute('SELECT COUNT(*) FROM pm_maintenance_repair_retries WHERE parent_id=?', (parent['operation_id'],)).fetchone()[0]
        if count >= _intent(parent)['check']['limits']['retries']:
            raise _bad('PM_REPAIR_RETRIES_EXHAUSTED', 'The original repair proved-unsent retry budget is exhausted')
        ops.db.execute('INSERT INTO pm_maintenance_repair_retries VALUES(?,?,?,?,?)',
                       (parent['operation_id'], operation_id, transition, step_name, time.time()))


def capacity_counts(ops, wid, policy_id, period):
    if not installed(ops.db):
        return 0, 0
    concurrent = ops.db.execute('SELECT COUNT(*) FROM pm_maintenance_repairs WHERE workspace_id=? AND finished_at IS NULL', (wid,)).fetchone()[0]
    count = ops.db.execute('SELECT COUNT(*) FROM pm_maintenance_repairs WHERE policy_id=? AND budget_period=?', (policy_id, period)).fetchone()[0]
    return concurrent, count


def _only_current_local_reservation(ops, operation):
    """The exact reserve callback is not an unresolved effect on its baseline.

    This private synchronous scope never applies to an ordinary workload read,
    another operation, a committed reservation or any unknown/external step.
    The enclosing _check still proves current authority, sources and workload.
    """
    ctx = _RESERVING_WORKLOAD.get()
    if (ctx is None or ctx.service is not ops or not ops.db.in_transaction
            or ctx.operation_id != operation['operation_id']):
        return False
    current = ops._row(ctx.operation_id)
    if (current['action'] != ACTION or current['status'] != 'running' or current['cancel_requested']
            or _intent(current) != _intent(ctx.op)
            or ops.db.execute('SELECT 1 FROM pm_maintenance_repairs WHERE operation_id=?', (ctx.operation_id,)).fetchone()
            or ops.db.execute('SELECT 1 FROM pm_maintenance_repair_children WHERE parent_id=?', (ctx.operation_id,)).fetchone()):
        return False
    steps = ops.db.execute('''SELECT s.*,l.kind FROM operation_steps s LEFT JOIN pm_lifecycle_steps l
        ON l.operation_id=s.operation_id AND l.name=s.name WHERE s.operation_id=? LIMIT 2''', (ctx.operation_id,)).fetchall()
    return (len(steps) == 1 and steps[0]['name'] == 'pm.repair.reserve' and steps[0]['status'] == 'started'
        and steps[0]['kind'] == 'local' and steps[0]['request'] == '{}'
        and steps[0]['response'] is None and steps[0]['external_ref'] is None and steps[0]['error'] is None)


def _reserve(ctx):
    def reserve():
        principal = pm_workspace.admitted_principal(ctx)
        token = _RESERVING_WORKLOAD.set(ctx)
        try:
            intent = _check(ctx.service, principal, ctx.target, ctx.params, ctx.preconditions, reserving=True)
        finally:
            _RESERVING_WORKLOAD.reset(token)
        if intent != _intent(ctx.op):
            raise _bad('PM_REPAIR_CONTEXT_CHANGED', 'The original repair selection changed before reservation')
        now = time.time()
        period = checks._capacity(ctx.service, ctx.target['workspace_id'], intent['check'], now)
        rid = 'pmrp_' + ctx.operation_id[3:]
        ctx.service.db.execute('''INSERT INTO pm_maintenance_repairs(repair_id,operation_id,workspace_id,policy_id,
            finding_id,work_item_id,context,context_digest,created_at,deadline_at,budget_period)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)''', (rid, ctx.operation_id, ctx.target['workspace_id'], ctx.params['policy_id'],
            intent['finding']['finding_id'], intent['work_item_id'], checks._json(intent), checks._digest(intent),
            now, now + intent['check']['limits']['run_seconds'], period))
        # The existing handoff inherits this original repair source through its
        # selected work. It remains beside the package assignment, never replaces it.
        ctx.service.db.execute('INSERT INTO pm_source_lineage VALUES(?,?,?,?)',
            ('work_item', intent['work_item_id'], ctx.operation_id, ctx.operation_id))
        return {'repair_id': rid}
    return ctx.effect('pm.repair.reserve', reserve)


def _admit_child(ctx):
    def create():
        parent = _operation(ctx.service, ctx.operation_id)
        principal = _guard(ctx.service, parent)
        request = _request(parent)
        child, fresh = ctx.service.create(principal, **request, entry='pm-maintenance-repair')
        if not fresh:
            raise _bad('PM_REPAIR_CHILD_CHANGED', 'A child key exists without its original relation')
        ctx.service.db.execute('INSERT INTO pm_maintenance_repair_children VALUES(?,?,?,?)',
            (ctx.operation_id, child['operation_id'], checks._json(request), checks._digest(request)))
        ctx.service._merge_refs(child['operation_id'], {'pm_maintenance_repair_parent': ctx.operation_id})
        return {'operation_id': child['operation_id']}
    return ctx.effect('pm.repair.child', create)


def _never_started(ops, parent):
    child = _child(ops, parent)
    if child is None:
        return parent['status'] in TERMINAL | {'needs_attention'} or bool(parent['cancel_requested'])
    return (child['status'] in {'failed', 'cancelled'} and not checks._unsettled(ops, child)
        and ops.db.execute("SELECT 1 FROM operation_steps WHERE operation_id=? AND name='session.start'",
                           (child['operation_id'],)).fetchone() is None)


def _finish_without_start(ctx):
    def finish():
        parent = _operation(ctx.service, ctx.operation_id)
        if not _never_started(ctx.service, parent):
            raise _bad('PM_REPAIR_EXECUTION_UNSETTLED', 'Original runtime effects still reserve capacity')
        ctx.service.db.execute('UPDATE pm_maintenance_repairs SET finished_at=? WHERE operation_id=? AND finished_at IS NULL',
                               (time.time(), ctx.operation_id))
        return {'repair_id': 'pmrp_' + ctx.operation_id[3:], 'outcome': 'no_runtime_started',
                'repair_verified': False, 'capacity_held': False}
    return ctx.effect('pm.repair.no_start', finish, receipt_only=True)


def reconcile(ops):
    from .operations import OpContext
    with ops.journal.tx():
        cursor = ops.db.execute('SELECT run_sequence FROM pm_maintenance_repair_cursor WHERE singleton=1').fetchone()[0]
        rows = ops.db.execute('SELECT * FROM pm_maintenance_repairs WHERE finished_at IS NULL AND sequence>? ORDER BY sequence LIMIT 33', (cursor,)).fetchall()
        for row in rows[:32]:
            op = _operation(ops, row['operation_id'])
            if _never_started(ops, op):
                _finish_without_start(OpContext(ops, op))
            else:
                from . import pm_maintenance_repair_runtime as runtime
                try:
                    runtime.ensure_observer(ops, op)
                except OperationError:
                    pass  # Read authority/source recovery never frees unknown capacity.
        ops.db.execute('UPDATE pm_maintenance_repair_cursor SET run_sequence=? WHERE singleton=1',
                       (rows[31]['sequence'] if len(rows) > 32 else 0,))


async def _run(ctx):
    _reserve(ctx)
    child = _child(ctx.service, ctx.op)
    if child is None:
        _admit_child(ctx)
        raise Wait('waiting_external', 'Original owned repair execution admitted', 1)
    if _never_started(ctx.service, ctx.op):
        return _finish_without_start(ctx)
    if child['status'] in {'failed', 'cancelled'} and not checks._unsettled(ctx.service, child):
        return ctx.effect('pm.repair.stopped', lambda: {'repair_id': 'pmrp_' + ctx.operation_id[3:],
            'outcome': 'execution_failed', 'operation_id': child['operation_id'],
            'repair_verified': False, 'capacity_held': True}, receipt_only=True)
    if child['status'] == 'succeeded':
        from . import pm_maintenance_repair_publication as publication
        return await publication.advance(ctx)
    # A succeeded repository.continue is only a start receipt. Evidence recording
    # and the parent do not manufacture coding, verification or publication success.
    raise Wait('waiting_external', 'Awaiting original fixed verification and delivery receipts', 15)


def _local_record_or_stop(operation):
    bound = (operation.get('external_refs') or {}).get('admission_binding')
    return pm_lifecycle_gates.kind(operation['action'], bound) == 'record' or operation['action'] == 'session.interrupt'


def require_publication_child(ops, operation):
    from . import pm_maintenance_repair_publication as publication
    if installed(ops.db):
        return publication.require_child(ops, operation)


def require_delivery_child(ops, operation):
    from . import pm_maintenance_repair_delivery as delivery
    return delivery.require_child(ops, operation)


def delivery_child_timeout(ops, operation, maximum):
    from . import pm_maintenance_repair_delivery as delivery
    return delivery.child_timeout(ops, operation, maximum)


def require_new_effect(ops, operation):
    if _local_record_or_stop(operation):
        return  # Existing local records/stop still pass the shared original-source gate.
    require_child(ops, operation)
    require_publication_child(ops, operation)
    require_delivery_child(ops, operation)
    if not installed(ops.db):
        return
    from . import pm_maintenance_repair_evidence as evidence
    originals = pm_source_lineage.roots(ops.db, 'operation', operation['operation_id'])
    for oid in originals:
        parent = _operation(ops, oid)
        if parent['action'] != ACTION or parent['operation_id'] == operation['operation_id']:
            continue
        scope = ops.actions[operation['action']].scope
        principal = _guard(ops, parent, scopes=REQUIRED | {scope})
        evidence.require_delivery(ops, principal, parent, operation)


def get_repair(ops, principal, workspace_id, repair_id):
    with baselines._snapshot(ops.db):
        workload._member(ops, principal, workspace_id)
        row = _row(ops, workspace_id, repair_id)
        parent = _operation(ops, row['operation_id'])
        _full_access(ops, principal, parent)
        from . import pm_maintenance_repair_evidence as evidence
        child = _child(ops, parent)
        from . import pm_maintenance_repair_publication as publication
        published = publication.child(ops, parent)
        from . import pm_maintenance_repair_delivery as delivery
        delivered = delivery.child(ops, parent)
        from . import pm_maintenance_repair_runtime as runtime
        proofs = [json.loads(r[0]) for r in ops.db.execute('SELECT document FROM pm_maintenance_repair_evidence WHERE parent_id=? ORDER BY sequence LIMIT 64', (parent['operation_id'],))]
        return _bounded({'workspace_id': workspace_id, 'repair': {**row,
            'operation_status': parent['status'], 'child': ({key: child[key] for key in ('operation_id', 'action', 'status', 'result', 'error_code')} if child else None),
            'publication_child': ({key: published[key] for key in ('operation_id', 'action', 'status', 'result', 'error_code')} if published else None),
            'delivery_child': ({key: delivered[key] for key in ('operation_id', 'action', 'status', 'result', 'error_code')} if delivered else None),
            **delivery.projection(ops, parent), **runtime.projection(ops, principal, parent),
            'evidence': proofs, 'capacity_held': row['finished_at'] is None,
            'outcome': (parent.get('result') or {}).get('outcome'),
            'repair_fixed': False, 'release_status': 'not_inferred', 'cost_coverage': 'model_provider_unmeasured',
            'progress': evidence.progress(ops, parent),
            'retries_used': ops.db.execute('SELECT COUNT(*) FROM pm_maintenance_repair_retries WHERE parent_id=?', (parent['operation_id'],)).fetchone()[0]}})


def list_repairs(ops, principal, workspace_id, *, after=0, limit=20):
    with baselines._snapshot(ops.db):
        workload._member(ops, principal, workspace_id)
        if type(after) is not int or not 0 <= after < 2**63 or type(limit) is not int or not 1 <= limit <= 50:
            raise _bad('INVALID_PARAMS', 'Use a signed 64-bit cursor and limit 1–50', 422)
        rows = ops.db.execute('SELECT repair_id,sequence FROM pm_maintenance_repairs WHERE workspace_id=? AND sequence>? ORDER BY sequence LIMIT ?', (workspace_id, after, limit + 1)).fetchall()
        result = {'workspace_id': workspace_id, 'repairs': [], 'next_after': None, 'source_visibility_filtered': False}
        consumed = after
        for stub in rows[:limit]:
            try:
                row = get_repair(ops, principal, workspace_id, stub['repair_id'])['repair']
            except OperationError as exc:
                if exc.status not in (403, 404):
                    raise
                result['source_visibility_filtered'] = True
                consumed = stub['sequence']
                continue
            candidate = {**result, 'repairs': [*result['repairs'], row], 'next_after': stub['sequence']}
            if len(json.dumps(candidate, ensure_ascii=False).encode()) > MAX_READ_BYTES:
                if not result['repairs']:
                    raise _bad('PM_REPAIR_READ_TOO_LARGE', 'One whole repair record exceeds the read bound', 413)
                result['next_after'] = consumed
                return _bounded(result)
            result['repairs'].append(row)
            consumed = stub['sequence']
        result['next_after'] = consumed if len(rows) > limit else None
        return _bounded(result)


def current_repair(ops, principal, workspace_id):
    with baselines._snapshot(ops.db):
        workload._member(ops, principal, workspace_id)
        row = ops.db.execute('SELECT repair_id FROM pm_maintenance_repairs WHERE workspace_id=? ORDER BY sequence DESC LIMIT 1', (workspace_id,)).fetchone()
        if row is None:
            return {'workspace_id': workspace_id, 'repair': None, 'source_visibility_filtered': False}
        try:
            return {**get_repair(ops, principal, workspace_id, row[0]), 'source_visibility_filtered': False}
        except OperationError as exc:
            if exc.status not in (403, 404):
                raise
            return {'workspace_id': workspace_id, 'repair': None, 'source_visibility_filtered': True}


def tick(ops):
    """Bounded rule evaluation on the existing server tick; no provider/model IO."""
    if ACTION not in ops.actions:
        return []
    reconcile(ops)
    admitted = []
    cursor = ops.db.execute('SELECT policy_sequence FROM pm_maintenance_repair_cursor WHERE singleton=1').fetchone()[0]
    rows = ops.db.execute("""SELECT p.* FROM pm_maintenance_policies p JOIN
        (SELECT workspace_id,MAX(revision) AS revision FROM pm_maintenance_policies GROUP BY workspace_id) h
        ON p.workspace_id=h.workspace_id AND p.revision=h.revision WHERE p.sequence>? ORDER BY p.sequence LIMIT 33""", (cursor,)).fetchall()
    consumed = cursor
    for stub in rows[:32]:
        if len(admitted) >= 4:
            break
        consumed = stub['sequence']
        wid = stub['workspace_id']
        if pm_lifecycle_gates.head(ops.db, wid)['stage'] not in ('development', 'maintain'):
            continue
        try:
            record = policies._record(ops.db, wid, stub['policy_id'])
            if not record['document']['policy']['enabled'] or not record['document']['policy'].get('repair_rules'):
                continue
            principal = _principal(ops, record)
            view = findings.current_findings(ops, principal, wid)
            source = view['sources']['issues']
            if source['status'] != 'complete' or not view['observation_ref']:
                continue
            active = policies.active_record(ops, principal, wid)
            for rule in record['document']['policy']['repair_rules']:
                if len(admitted) >= 4:
                    break
                work_id = rule['handoff']['params']['work_item_id']
                if ops.db.execute('SELECT 1 FROM pm_maintenance_repairs WHERE work_item_id=?', (work_id,)).fetchone():
                    continue
                # The provider's existing 4096-row bound caps this local scan.
                # Skip all reserved IDs before spending any admission work.
                examined = 0
                for found in source['findings']:
                    if not set(rule['labels_all']) <= set(found['facts']['labels']):
                        continue
                    if found.get('existing_work') or found['existing_pull_request'] is not None:
                        continue
                    if _existing(ops, wid, found['finding_id'], work_id):
                        continue
                    examined += 1
                    if examined > 32:
                        break
                    params = {'policy_id': record['policy_id'], 'rule_key': rule['key'],
                              'finding_ref': {**view['observation_ref'], 'finding_id': found['finding_id']}}
                    pre = {'expected_policy_version': active['version'], 'expected_baseline_id': active['baseline_id'],
                           'expected_lifecycle_revision': pm_lifecycle_gates.head(ops.db, wid)['revision']}
                    try:
                        with ops.journal.tx():
                            op, fresh = ops.create(principal, action=ACTION, target={'workspace_id': wid}, params=params,
                                preconditions=pre, idempotency_key='maintenance-repair:' + found['finding_id'], entry='maintenance-repair-schedule')
                            if fresh:
                                from .operations import OpContext
                                _reserve(OpContext(ops, op))
                                admitted.append(op['operation_id'])
                    except OperationError:
                        continue
                    break
        except OperationError:
            continue
    with ops.journal.tx():
        ops.db.execute('UPDATE pm_maintenance_repair_cursor SET policy_sequence=? WHERE singleton=1',
                       (consumed if len(rows) > 32 or consumed != (rows[-1]['sequence'] if rows else cursor) else 0,))
    return admitted
