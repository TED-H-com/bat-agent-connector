"""Exact reader sources from main 30b823db, plus the private receipt at 957372c8.

Do not refresh with implementation changes. Namespace helpers stay production;
only the four pagination/filtering functions are frozen for response equivalence.
"""

OPERATIONS = r'''
class ReferenceOperations(OperationService):
    def list(self, *, statuses: list[str] | None = None, actor: str | None = None, action: str | None = None,
             before: float | None = None, before_id: str | None = None, limit: int = 50, principal: Principal | None = None) -> dict:
        limit = max(1, min(200, int(limit)))
        before, before_id = operation_cursor(before, before_id)
        sql, args = "SELECT * FROM operations WHERE 1=1", []
        if statuses:
            bad = [s for s in statuses if s not in STATES]
            if bad:
                raise OperationError("INVALID_FILTER", f"unknown status {bad[0]!r}", 422)
            sql += f" AND status IN ({','.join('?' * len(statuses))})"  # noqa: S608 - placeholders only
            args += statuses
        if actor:
            sql, args = sql + " AND actor=?", [*args, actor]
        if action:
            sql, args = sql + " AND action=?", [*args, action]
        if before is not None:
            if before_id is not None:
                sql += " AND (created_at<? OR (created_at=? AND operation_id<?))"
                args += [float(before), float(before), before_id]
            else:
                sql, args = sql + " AND created_at<?", [*args, float(before)]
        rows = self.db.execute(sql + " ORDER BY created_at DESC, operation_id DESC LIMIT ?", (*args, limit + 1)).fetchall()
        ops = [self._decode(r) for r in rows[:limit]]
        next_before = ops[-1]["created_at"] if len(rows) > limit else None
        next_before_id = ops[-1]["operation_id"] if len(rows) > limit else None
        if principal is not None:
            from .pm_access import visible
            ops = [op for op in ops if visible(self.db, principal, op)]
        return {"operations": ops, "next_before": next_before, "next_before_id": next_before_id}
'''

PM_ACCESS = r'''
def filter_events(db, principal, page):
    return {**page, "events": [event for event in page["events"] if event_visible(db, principal, event)]}
'''

PM_RUNS = r'''
def list_runs(ops, principal, workspace_id, *, thread_id=None, limit=50, after=None):
    with ops.journal.tx():
        pm_discussions._read(ops, principal, workspace_id)
        pm_discussions._limit(limit)
        if thread_id is not None:
            pm_discussions._thread(ops.db, workspace_id, thread_id)
        if after is not None and get(ops.db, after)["workspace_id"] != workspace_id:
            raise _error("PM run not found", "PM_RUN_NOT_FOUND", 404)
        rows = ops.db.execute("""SELECT run_id FROM pm_runs WHERE workspace_id=?
            AND (? IS NULL OR thread_id=?) AND (? IS NULL OR run_id>?) ORDER BY run_id LIMIT ?""",
            (workspace_id, thread_id, thread_id, after, after, limit + 1)).fetchall()
        result = {"workspace_id": workspace_id, "runs": [], "next_cursor": None,
                  "coverage": {"visibility_filtered": False, "scanned": 0}}
        cursor = after
        for row in rows[:limit]:
            run = get(ops.db, row[0])
            visible = source_visible(ops.db, principal, run)
            candidate = {**result, "runs": result["runs"] + ([_summary(run)] if visible else []),
                "next_cursor": row[0],  # Budget continuation and filtered-row metadata too.
                "coverage": {"visibility_filtered": result["coverage"]["visibility_filtered"] or not visible,
                             "scanned": result["coverage"]["scanned"] + 1}}
            try:
                _read_bound(candidate)
            except OperationError:
                if cursor == after:
                    raise
                result["next_cursor"] = cursor
                return _read_bound(result)
            result = candidate
            cursor = row[0]
        result["next_cursor"] = cursor if len(rows) > limit else None
        return _read_bound(result)
'''

PM_ROADMAP_REPORT_MATERIALIZATION = r'''
@_snapshot_read
def list_materializations(ops, principal, workspace_id, *, after=0, limit=20):
    _read(ops, principal, workspace_id)
    if type(after) is not int or not 0 <= after <= 2**63 - 1 or type(limit) is not int or not 1 <= limit <= 50:
        raise OperationError('INVALID_PARAMS', 'Select a signed 64-bit cursor and a limit of 1–50', 422)
    with pm_source_reads.scope(ops.db, principal):
        rows = ops.db.execute('SELECT * FROM pm_roadmap_report_materializations WHERE workspace_id=? AND sequence>? ORDER BY sequence LIMIT ?',
            (workspace_id, after, limit + 1)).fetchall()
        values = []
        for row in rows[:limit]:
            with pm_source_reads.record(ops.db, principal):
                if source_visible(ops.db, principal, dict(row)):
                    values.append(row)
        return {'version': 1, 'workspace_id': workspace_id,
            'materializations': [_record(ops.db, workspace_id, row['materialization_id']) for row in values],
            'next_after': rows[limit - 1]['sequence'] if len(rows) > limit else None,
            'source_visibility_filtered': len(values) != len(rows[:limit])}
'''
