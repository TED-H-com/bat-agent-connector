"""Exact original direct-send evidence shared by the collector and WIP read."""
from __future__ import annotations

from . import pm_evidence

MAX_DIRECT_INPUTS = 500
# The uncertainty exclusion is the original _uncertain_db external-step predicate.
# Start from one original's lineage index; no global operations walk or SQL sort.
ELIGIBLE_SQL = """SELECT o.operation_id, o.rowid AS admission_order, s.request, s.response
    FROM pm_source_lineage l
    CROSS JOIN operations o ON o.operation_id=l.resource_id
    CROSS JOIN operation_steps s ON s.operation_id=o.operation_id AND s.name='send'
    WHERE l.original_operation_id=? AND l.resource_type='operation'
      AND o.action='session.send' AND o.status IN ('succeeded','failed','cancelled')
      AND json_extract(o.target,'$.host')=? AND json_extract(o.target,'$.session_id')=?
      AND json_remove(o.target,'$.host','$.session_id')='{}'
      AND s.status='succeeded' AND json_extract(s.response,'$.accepted')=1
      AND json_type(s.response,'$.accepted')='true'
      AND json_type(s.request,'$.message_id')='text' AND json_extract(s.request,'$.message_id')!=''
      AND json_extract(s.request,'$.message_id')=json_extract(s.response,'$.message_id')
      AND NOT EXISTS (
          SELECT 1 FROM operation_steps u LEFT JOIN pm_lifecycle_steps g
          ON g.operation_id=u.operation_id AND g.name=u.name
          WHERE u.operation_id=o.operation_id AND u.status IN ('started','uncertain')
            AND (g.kind IS NULL OR g.kind='external'))
    LIMIT ?"""


def eligible(db, original_operation_id, session, *, limit=MAX_DIRECT_INPUTS + 1):
    """Return bounded eligible original sends, in durable admission order.

    operations.rowid is allocated by the serialized admission INSERT. Sort only
    the bounded result in memory; operation_id is the deterministic tie-breaker.
    The caller detects overflow before selecting any marker. Authorization and
    record charging belong to the WIP read; the internal collector uses originals.
    """
    rows = db.execute(ELIGIBLE_SQL,
        (original_operation_id, session['host'], session['session_id'], limit)).fetchall()
    values = [{'operation_id': r['operation_id'], 'admission_order': r['admission_order'],
               'marker': pm_evidence._body(r['request'])['message_id'],
               'queued': pm_evidence._body(r['response']).get('queued') is True} for r in rows]
    return sorted(values, key=lambda row: (row['admission_order'], row['operation_id']))
