"""Weekly fixed reports on the existing bounded maintenance scheduler.

This module has no timer or worker. Immutable weekly reservations bind the exact
existing prepare action; manual reports have no reservation and remain manual.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import uuid
from zoneinfo import ZoneInfo

from . import pm_maintenance_checks as checks
from . import pm_maintenance_policy as policies
from . import pm_maintenance_review as reviews
from . import pm_plans, pm_snapshots, pm_workspace
from .operations import OpContext, OperationError

UTC = dt.timezone.utc
ACTION = reviews.PREFIX + 'prepare'
TABLE = 'pm_maintenance_review_windows'
MAX_TICK = 32


def schema(journal):
    with journal.tx():
        journal.db.execute('''CREATE TABLE IF NOT EXISTS pm_maintenance_review_windows (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, schedule_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL, policy_id TEXT NOT NULL, policy_version INTEGER NOT NULL,
            window INTEGER NOT NULL, scheduled_at REAL NOT NULL, admitted_at REAL NOT NULL,
            deadline_at REAL NOT NULL, budget_period INTEGER NOT NULL, coalesced_windows INTEGER NOT NULL,
            period_start REAL NOT NULL, operation_id TEXT NOT NULL UNIQUE,
            request TEXT NOT NULL, request_digest TEXT NOT NULL, context TEXT NOT NULL, context_digest TEXT NOT NULL,
            finished_at REAL, outcome TEXT, reason TEXT, review_id TEXT,
            UNIQUE(schedule_id,window))''')
        journal.db.execute('CREATE INDEX IF NOT EXISTS pm_review_schedule_active ON pm_maintenance_review_windows(workspace_id,finished_at)')
        journal.db.execute('CREATE INDEX IF NOT EXISTS pm_review_schedule_budget ON pm_maintenance_review_windows(policy_id,budget_period)')
        journal.db.execute('''CREATE TABLE IF NOT EXISTS pm_maintenance_review_schedule_cursor (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1), sequence INTEGER NOT NULL)''')
        journal.db.execute('INSERT OR IGNORE INTO pm_maintenance_review_schedule_cursor VALUES(1,0)')
        journal.db.execute('''CREATE TRIGGER IF NOT EXISTS pm_review_schedule_identity_immutable
            BEFORE UPDATE OF sequence,schedule_id,workspace_id,policy_id,policy_version,window,scheduled_at,admitted_at,
            deadline_at,budget_period,coalesced_windows,period_start,operation_id,request,request_digest,context,context_digest
            ON pm_maintenance_review_windows
            BEGIN SELECT RAISE(ABORT, 'Weekly report identity is immutable'); END''')
        journal.db.execute('''CREATE TRIGGER IF NOT EXISTS pm_review_schedule_final_immutable
            BEFORE UPDATE ON pm_maintenance_review_windows WHEN OLD.finished_at IS NOT NULL
            BEGIN SELECT RAISE(ABORT, 'Completed weekly report receipt is immutable'); END''')
        journal.db.execute('''CREATE TRIGGER IF NOT EXISTS pm_review_schedule_delete_immutable
            BEFORE DELETE ON pm_maintenance_review_windows
            BEGIN SELECT RAISE(ABORT, 'Weekly report history is immutable'); END''')


def installed(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchone() is not None


def available(ops):
    return ACTION in ops.actions and installed(ops.db)


def _bad(code, message):
    return OperationError(code, message, 409)


def _instant(date, schedule):
    """First occurrence in a fold; advance nonexistent local minutes forward."""
    hour, minute = map(int, schedule['review_local_time'].split(':'))
    naive = dt.datetime.combine(date, dt.time(hour, minute))
    zone = ZoneInfo(schedule['timezone'])
    for advance in range(1441):
        candidate = naive + dt.timedelta(minutes=advance)
        local = candidate.replace(tzinfo=zone, fold=0)
        if local.astimezone(UTC).astimezone(zone).replace(tzinfo=None) == candidate:
            return local.timestamp(), advance
    raise _bad('PM_REVIEW_LOCAL_TIME_UNAVAILABLE', 'No valid local review instant within one day')


def _date_at_or_before(value, schedule):
    local = dt.datetime.fromtimestamp(value, ZoneInfo(schedule['timezone']))
    return local.date() - dt.timedelta(days=(local.weekday() - schedule['review_weekday']) % 7)


def timing(record, now):
    """Constant bounded calendar arithmetic; no iteration over missed weeks."""
    schedule = record['document']['policy']['schedule']
    try:
        created = record['created_at']
        if not math.isfinite(now) or now < created:
            raise ValueError
        first = _date_at_or_before(created, schedule)
        if _instant(first, schedule)[0] <= created:
            first += dt.timedelta(days=7)
        due_date = _date_at_or_before(now, schedule)
        due, adjustment = _instant(due_date, schedule)
        if due > now:
            due_date -= dt.timedelta(days=7)
            due, adjustment = _instant(due_date, schedule)
        window = (due_date - first).days // 7 + 1
        if window < 1:
            return {'window': 0, 'next_due': _instant(first, schedule)[0]}
        previous = _instant(due_date - dt.timedelta(days=7), schedule)[0]
        return {'window': window, 'scheduled_at': due, 'period_start': max(created, previous),
            'next_due': _instant(due_date + dt.timedelta(days=7), schedule)[0],
            'local_date': due_date.isoformat(), 'advanced_local_minutes': adjustment}
    except (OverflowError, ValueError) as exc:
        raise _bad('PM_REVIEW_CLOCK_CHANGED', 'The current clock cannot select an original weekly period') from exc


def _schedule_id(db, wid, policy_id):
    return 'pmrvs_' + uuid.uuid5(uuid.UUID(pm_workspace.server_id(db)), wid + ':' + policy_id + ':weekly').hex


def _row(ops, operation_id):
    if not installed(ops.db):
        return None
    raw = ops.db.execute('SELECT * FROM pm_maintenance_review_windows WHERE operation_id=?', (operation_id,)).fetchone()
    if raw is None:
        return None
    row = dict(raw)
    try:
        row['request'], row['context'] = json.loads(row['request']), json.loads(row['context'])
        if checks._digest(row['request']) != row['request_digest'] or checks._digest(row['context']) != row['context_digest']:
            raise ValueError
    except (TypeError, ValueError):
        raise _bad('PM_REVIEW_SCHEDULE_CORRUPT', 'Original weekly report context cannot be verified') from None
    return row


def guard(ops, op):
    """Only actual server-inserted reservations activate delegated authority."""
    saved = _row(ops, op['operation_id'])
    if saved is None:
        return None
    if saved['finished_at'] is not None:
        raise _bad('PM_REVIEW_SCHEDULE_FINISHED', 'Original weekly report reservation has finished')
    if {key: op[key] for key in ('action', 'target', 'params', 'preconditions')} != saved['request']:
        raise _bad('PM_REVIEW_SCHEDULE_CHANGED', 'Original weekly prepare envelope changed')
    now = checks.time.time()
    if not saved['admitted_at'] <= now < saved['deadline_at']:
        raise _bad('PM_REVIEW_SCHEDULE_DEADLINE', 'Original weekly report deadline expired or clock reversed')
    intent = saved['context']['intent']
    record = policies._record(ops.db, saved['workspace_id'], saved['policy_id'])
    live = checks._current_principal(ops, record, now)
    workload = intent['workload']
    current = checks._check(ops, live, {'workspace_id': saved['workspace_id']}, {'policy_id': saved['policy_id']}, {
        'expected_policy_version': workload['version'], 'expected_baseline_id': workload['baseline_id'],
        'expected_lifecycle_revision': workload['lifecycle_revision']})
    if current != intent or op['actor'] != live.actor:
        raise _bad('PM_REVIEW_SCHEDULE_CHANGED', 'Original weekly policy authority or execution epoch changed')
    return saved


def projection(saved):
    if saved is None:
        return None
    return {**{key: saved[key] for key in ('schedule_id', 'policy_id', 'policy_version', 'window', 'scheduled_at',
        'admitted_at', 'deadline_at', 'period_start', 'coalesced_windows')},
        **saved['context']['calendar'], 'period_selection': 'latest_due_week',
        'missed_weeks_checked': False, 'meeting_completed': False}


def finish(ops, operation_id, *, outcome, reason=None, review_id=None):
    if not installed(ops.db):
        return
    saved = _row(ops, operation_id)
    if saved is None or saved['finished_at'] is not None:
        return
    ops.db.execute('''UPDATE pm_maintenance_review_windows SET finished_at=?,outcome=?,reason=?,review_id=?
        WHERE operation_id=?''', (checks.time.time(), outcome, reason, review_id, operation_id))


def reconcile(ops):
    if not available(ops):
        return 0
    cursor = ops.db.execute('SELECT sequence FROM pm_maintenance_review_schedule_cursor WHERE singleton=1').fetchone()[0]
    rows = ops.db.execute('''SELECT sequence,operation_id FROM pm_maintenance_review_windows
        WHERE finished_at IS NULL AND sequence>? ORDER BY sequence LIMIT ?''', (cursor, MAX_TICK + 1)).fetchall()
    count = 0
    for row in rows[:MAX_TICK]:
        op = ops.get(row['operation_id'], steps=False)
        if op['status'] in checks.TERMINAL and not checks._unsettled(ops, op):
            # A successful prepared report is finalized atomically with its
            # snapshot; an unmatched terminal operation cannot invent a report.
            finish(ops, op['operation_id'], outcome='stopped', reason=op['error_code'] or 'REPORT_UNAVAILABLE')
            count += 1
    ops.db.execute('UPDATE pm_maintenance_review_schedule_cursor SET sequence=? WHERE singleton=1',
        (rows[MAX_TICK - 1]['sequence'] if len(rows) > MAX_TICK else 0,))
    return count


def maybe_schedule(ops, raw):
    """One policy visit from the existing W48 bounded rolling cursor."""
    if not available(ops):
        return False
    with ops.journal.tx():
        wid, now = raw['workspace_id'], checks.time.time()
        record = policies._record(ops.db, wid, raw['policy_id'])
        due = timing(record, now)
        if not due['window']:
            return False
        sid = _schedule_id(ops.db, wid, record['policy_id'])
        previous = ops.db.execute('SELECT MAX(window) FROM pm_maintenance_review_windows WHERE schedule_id=?', (sid,)).fetchone()[0] or 0
        if due['window'] <= previous:
            return False
        live = checks._current_principal(ops, record, now)
        _, check_request = checks._schedule(ops, live, wid)
        intent = checks._check(ops, live, check_request['target'], check_request['params'], check_request['preconditions'])
        budget_period = checks._capacity(ops, wid, intent, now)
        current = reviews.head(ops.db, wid)
        params = {'title': 'Maintenance review ' + due['local_date'], 'period_start': due['period_start'],
            'period_end': due['scheduled_at']}
        if current['review_id']:
            params['previous_review_id'] = current['review_id']
        request = {'action': ACTION, 'target': {'workspace_id': wid}, 'params': params,
            'preconditions': {**check_request['preconditions'], 'expected_policy_id': record['policy_id'],
                'expected_plan_version': pm_plans.head(ops.db, wid)['version'], 'expected_review_revision': current['revision']}}
        def admit():
            # Preflight the bounded saved-source projection in a savepoint. If
            # the old readable chain alone overflows, its public admission must
            # roll back before creating an independent current-period report.
            with ops.journal.tx():
                original, fresh = ops.create(live, **request, idempotency_key=f'{sid}:{due["window"]}', entry='maintenance-review-schedule')
                if not fresh:
                    raise _bad('PM_REVIEW_SCHEDULE_CHANGED', 'Weekly operation exists without its original reservation')
                workspace, plan, fixed, prior, refs = reviews._prepare_check(ops, live, request['target'], request['params'], request['preconditions'])
                _, projected = reviews.sources.build(ops, live, wid, fixed['workload'],
                    {'start': params['period_start'], 'end': params['period_end']}, now, prior)
                snapshot = pm_snapshots._build(OpContext(ops, original), live, workspace, plan)
                reviews.sources.unique([*refs, *projected, *[reviews.sources.ref('evidence', item['evidence_id'], item['fact_digest'])
                    for item in snapshot['evidence']]])
                return original
        continuity = 'included' if current['review_id'] else 'first_report'
        try:
            op = admit()
        except OperationError as exc:
            if exc.code != 'PM_MAINTENANCE_REVIEW_SOURCE_LIMIT' or 'previous_review_id' not in params:
                raise
            params.pop('previous_review_id')
            continuity = 'omitted_source_limit'
            op = admit()
        context = {'intent': intent, 'calendar': {'timezone': intent['schedule']['timezone'],
            'weekday': intent['schedule']['review_weekday'], 'local_time': intent['schedule']['review_local_time'],
            'local_date': due['local_date'], 'advanced_local_minutes': due['advanced_local_minutes'],
            'ambiguous_time_policy': 'first_occurrence', 'nonexistent_time_policy': 'first_valid_minute_after',
            'continuity': continuity}}
        ops.db.execute('''INSERT INTO pm_maintenance_review_windows(schedule_id,workspace_id,policy_id,policy_version,
            window,scheduled_at,admitted_at,deadline_at,budget_period,coalesced_windows,period_start,operation_id,
            request,request_digest,context,context_digest) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (sid, wid, record['policy_id'], record['revision'], due['window'], due['scheduled_at'], now,
             now + intent['limits']['run_seconds'], budget_period, due['window'] - previous - 1, due['period_start'],
             op['operation_id'], checks._json(request), checks._digest(request), checks._json(context), checks._digest(context)))
        return True


def status(ops, principal, record, now):
    schedule, wid = record['document']['policy']['schedule'], record['workspace_id']
    result = {'weekday': schedule['review_weekday'], 'local_time': schedule['review_local_time'],
        'timezone': schedule['timezone'], 'execution_available': available(ops), 'next_due': None,
        'latest_window': None, 'blocked_reason': None, 'missed_periods': 'coalesce_latest_week',
        'meeting_completion': 'explicit_owner_discussion_only'}
    if not result['execution_available']:
        return result
    sid = _schedule_id(ops.db, wid, record['policy_id'])
    raw = ops.db.execute('SELECT * FROM pm_maintenance_review_windows WHERE schedule_id=? ORDER BY window DESC LIMIT 1', (sid,)).fetchone()
    if raw:
        op = ops.get(raw['operation_id'], steps=False)
        if not reviews.operation_visible(ops.db, principal, op):
            raise OperationError('NOT_FOUND', 'Original weekly report sources unavailable', 404)
        result['latest_window'] = {key: raw[key] for key in ('operation_id', 'window', 'scheduled_at', 'admitted_at',
            'coalesced_windows', 'finished_at', 'outcome', 'reason', 'review_id')}
        result['latest_window']['operation_status'] = op['status']
    try:
        due = timing(record, now)
        result['next_due'] = due.get('scheduled_at') if due['window'] and (raw is None or due['window'] > raw['window']) else due['next_due']
        live = checks._current_principal(ops, record, now)
        _, request = checks._schedule(ops, live, wid)
        intent = checks._check(ops, live, request['target'], request['params'], request['preconditions'])
        checks._capacity(ops, wid, intent, now)
    except OperationError as exc:
        result['blocked_reason'] = exc.code
    result['overdue'] = result['next_due'] is not None and now >= result['next_due']
    # Reading a schedule does not mark any user's meeting attended/completed.
    current = reviews.current_review(ops, principal, wid, history_limit=1)
    result['pending_report'] = (current['review']['review_id'] if current.get('review') and current['review']['status'] == 'prepared' else None)
    result['visibility_filtered'] = current.get('visibility_filtered', False)
    if result['visibility_filtered'] and result['blocked_reason'] is None:
        result['blocked_reason'] = 'PM_MAINTENANCE_REVIEW_SOURCE_UNAVAILABLE'
    return result
