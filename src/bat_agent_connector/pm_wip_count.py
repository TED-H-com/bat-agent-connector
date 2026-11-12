"""Bounded, read-only original-record projection; no scheduler or reservations."""
from __future__ import annotations

from . import (
    artifact_managed,
    pm_attempt_results,
    pm_direct_inputs,
    pm_evidence,
    pm_execution_runtime,
    pm_kanban,
    pm_lifecycle_gates,
    pm_package_handoff,
    pm_source_lineage,
    pm_work_adopt_and_start,
    pm_work_batches,
    pm_work_follow_up,
    pm_work_start,
    task_journal,
    work_items,
)
from . import (
    pm_workspace_wip as policy,
)
from .operations import TERMINAL, OperationError

REASONS = ("management_wip", "unresolved_execution", "accepted_selection")
OUTSIDE_SCOPE = ["unassociated_external_work", "removed_link_activity", "host_resource_capacity", "model_cost"]
UNAVAILABLE = "coverage_unavailable"
ROOT_SQL = f"""SELECT operation_id FROM operations WHERE ({policy.WORKSPACE_SQL})=?
    AND json_extract(target,'$.execution_operation_id') IS NULL
    AND action IN ('work.start','pm.work_batch.start','work.adopt_and_start','repository.continue') LIMIT ?"""  # noqa: S608 - constant indexed expression
HISTORY_SQL = """SELECT * FROM api_events WHERE resource_type='work_item' AND resource_id=?
    AND kind IN ('work_item.created','work_item.state','work_item.updated','work_item.approved','work_item.continued')
    ORDER BY seq LIMIT ?"""
MANAGEMENT_SQL = """SELECT o.operation_id FROM management_applied m JOIN operations o USING(operation_id)
    WHERE json_extract(m.result,'$.work_item_id')=? AND COALESCE(json_extract(m.result,'$.changed'),1)=1
    AND (o.action IN ('work_item.create','work_item.approve','work_item.continue')
        OR o.action='work_item.update' AND json_type(o.params,'$.archived') IS NULL)
    ORDER BY m.rowid LIMIT ?"""
LINKS_SQL = "SELECT * FROM work_item_links WHERE work_item_id=? AND removed_at IS NULL ORDER BY kind,ref LIMIT ?"
FIRST_BINDING_SQL = """SELECT * FROM pm_work_bindings WHERE workspace_id=?
    ORDER BY work_item_id DESC,sequence DESC LIMIT 1"""
BINDING_SQL = """SELECT * FROM pm_work_bindings WHERE workspace_id=? AND work_item_id<?
    ORDER BY work_item_id DESC,sequence DESC LIMIT 1"""
LINEAGE_SQL = """SELECT resource_id FROM pm_source_lineage
    WHERE original_operation_id=? AND resource_type='operation' LIMIT ?"""
TASK_EVENTS_SQL = "SELECT * FROM events WHERE task_id=? ORDER BY kind,event_id LIMIT ?"


# Explicit admission-lineage inventory. Never infer newer actions as no-hold.
# The inventory test derives its expected set from the admission sources.
LINEAGE_ACTIONS = {
    "artifact.accept": "no_hold",
    "artifact.capture.managed": "no_hold",
    "delivery.merge_and_deploy": "no_hold",
    "deployment.rollback": "no_hold",
    "deployment.start": "no_hold",
    "github.pr.merge": "no_hold",
    "github.pr.publish": "no_hold",
    "integration.apply": "no_hold",
    "integration.handoff": "no_hold",
    "integration.preview": "no_hold",
    "pm.ahp.observe": "no_hold",
    "pm.backlog.revise": "no_hold",
    "pm.continuation.prepare": "no_hold",
    "pm.conversation.adopt": "no_hold",
    "pm.conversation.propose": "no_hold",
    "pm.dependencies.capture": "no_hold",
    "pm.dependencies.declare": "no_hold",
    "pm.dependencies.retract": "no_hold",
    "pm.dod.revise": "no_hold",
    "pm.evidence.capture": "no_hold",
    "pm.execution.runtime.observe": "no_hold",
    "pm.execution.verify": "no_hold",
    "pm.follow_up.analyze": "no_hold",
    "pm.follow_up.prepare": "no_hold",
    "pm.github.import": "no_hold",
    "pm.github.observe": "no_hold",
    "pm.governance.adoption.link": "no_hold",
    "pm.governance.decide": "no_hold",
    "pm.governance.record": "no_hold",
    "pm.governance.revise": "no_hold",
    "pm.history.link": "no_hold",
    "pm.history.unlink": "no_hold",
    "pm.input.confirm": "no_hold",
    "pm.input.record": "no_hold",
    "pm.issue.cancel": "no_hold",
    "pm.issue.publish": "no_hold",
    "pm.lifecycle.close": "no_hold",
    "pm.lifecycle.pause": "no_hold",
    "pm.lifecycle.reopen": "no_hold",
    "pm.lifecycle.retain_inflight": "no_hold",
    "pm.lifecycle.set_stage": "no_hold",
    "pm.maintenance.check": "no_hold",
    "pm.maintenance.repair": "no_hold",
    "pm.maintenance.repair.evidence.record": "no_hold",
    "pm.maintenance.repair.runtime.observe": "no_hold",
    "pm.maintenance_policy.adopt": "no_hold",
    "pm.maintenance_review.candidate.record": "no_hold",
    "pm.maintenance_review.complete": "no_hold",
    "pm.maintenance_review.follow_up.record": "no_hold",
    "pm.maintenance_review.note": "no_hold",
    "pm.maintenance_review.prepare": "no_hold",
    "pm.meeting.complete": "no_hold",
    "pm.meeting.context.revise": "no_hold",
    "pm.meeting.create": "no_hold",
    "pm.meeting.draft": "no_hold",
    "pm.meeting.follow_up.record": "no_hold",
    "pm.meeting.note": "no_hold",
    "pm.message.append": "no_hold",
    "pm.overview.assess": "no_hold",
    "pm.plan.adopt": "no_hold",
    "pm.plan.draft": "no_hold",
    "pm.product_goal.revise": "no_hold",
    "pm.project.bind": "no_hold",
    "pm.quality.review.create": "no_hold",
    "pm.quality.review.record": "no_hold",
    "pm.quality.review.revoke": "no_hold",
    "pm.release_baseline.register": "no_hold",
    "pm.roadmap.baseline.set": "no_hold",
    "pm.roadmap.forecast.set": "no_hold",
    "pm.roadmap.observe": "no_hold",
    "pm.run.analyze": "no_hold",
    "pm.run.bat.analyze": "no_hold",
    "pm.run.prepare": "no_hold",
    "pm.session.quiescence.observe": "no_hold",
    "pm.snapshot.create": "no_hold",
    "pm.spec.materialize": "no_hold",
    "pm.sprint.cancel": "no_hold",
    "pm.sprint.close": "no_hold",
    "pm.sprint.create": "no_hold",
    "pm.sprint.goal.revise": "no_hold",
    "pm.sprint.select": "no_hold",
    "pm.sprint.start": "no_hold",
    "pm.thread.create": "no_hold",
    "pm.thread.plan": "no_hold",
    "pm.thread.plan.revise": "no_hold",
    "pm.thread.rename": "no_hold",
    "pm.wip_policy.save": "no_hold",
    "pm.work.bind": "no_hold",
    "pm.work_batch.evidence.record": "no_hold",
    "pm.work_batch.start": "no_hold",
    "pm.work_input.defer": "no_hold",
    "pm.work_package.create": "no_hold",
    "pm.work_profile.save": "no_hold",
    "pm.workload.capture": "no_hold",
    "pm.workspace.adapter.select": "no_hold",
    "pm.workspace.create": "no_hold",
    "pm.workspace.member.set": "no_hold",
    "repository.continue": "no_hold",
    "session.answer": "control",
    "session.interrupt": "control",
    "session.labels.set": "no_hold",
    "session.position": "no_hold",
    "session.read": "no_hold",
    "session.record_verification": "no_hold",
    "session.send": "input",
    "work.adopt_and_start": "no_hold",
    "work.start": "no_hold",
    "work_item.approve": "no_hold",
    "work_item.continue": "no_hold",
    "work_item.order": "no_hold",
    "work_item.pin": "no_hold",
    "work_item.update": "no_hold",
}

class _Unavailable(Exception):
    pass


class _Collector:
    def __init__(self, ops, principal, wid, now):
        self.ops, self.db, self.principal, self.wid, self.now = ops, ops.db, principal, wid, now
        self.used_records = 0
        self.works, self.operations, self.executions = {}, {}, {}
        self.groups = {reason: set() for reason in REASONS}
        self.reasons = set()

    def rows(self, sql, args=(), *, bound=None):
        # LIMIT applies before materialization. No OFFSET or unbounded history fetch.
        room = policy.MAX_RECORDS - self.used_records
        if bound is not None:
            room = min(room, bound)
        rows = self.db.execute(sql, (*args, room + 1)).fetchall()
        self.used_records += len(rows)
        if len(rows) > room:
            raise _Unavailable
        return rows

    def source(self, oid):
        if oid not in self.operations:
            pm_source_lineage.require(self.db, self.principal, "operation", oid)
            self.operations[oid] = self.ops.get(oid, steps=False, principal=self.principal)
            self.used_records += 1
            if self.used_records > policy.MAX_RECORDS:
                raise _Unavailable
        return self.operations[oid]

    def work(self, wi):
        if wi in self.works:
            return self.works[wi]
        if len(self.works) >= policy.MAX_WORKS:
            raise _Unavailable
        pm_source_lineage.require(self.db, self.principal, "work_item", wi)
        item = work_items._get_item(self.db, wi)
        project = work_items._get_project(self.db, item["project_id"])
        closed = bool(item["archived"] or project["archived"] or work_items.completion(item)["approved"]
                      or work_items.source_lane(item) in work_items.GITHUB_CLOSED_LANES)
        self.works[wi] = item, closed
        return item, closed

    def management(self, binding):
        wi = binding["work_item_id"]
        self.source(binding["operation_id"])
        item, closed = self.work(wi)
        rows = self.rows(HISTORY_SQL, (wi,), bound=pm_kanban.MAX_HISTORY)
        expected = self.rows(MANAGEMENT_SQL, (wi,), bound=pm_kanban.MAX_HISTORY)
        originals = {}
        for row in rows:
            oid = pm_evidence._body(row["body"]).get("operation_id")
            op = self.source(oid)
            receipt = self.db.execute("SELECT result FROM management_applied WHERE operation_id=?", (oid,)).fetchone()
            originals[oid] = op, pm_evidence._body(receipt[0]) if receipt else {}
        # Check every retained original, even when an event was pruned or Work closed.
        for row in expected:
            self.source(row["operation_id"])
        completion = work_items.completion(item)
        seen = set(originals)
        if len(rows) > pm_kanban.MAX_HISTORY or len(expected) > pm_kanban.MAX_HISTORY:
            # A per-source bound is indistinguishable from any other overflow.
            raise _Unavailable
        if not rows or rows[0]["kind"] != "work_item.created" or any(row[0] not in seen for row in expected):
            flow = pm_kanban._unknown_flow(item, completion, "missing_history")
        else:
            flow = pm_kanban.replay_flow(item, completion, self.now, rows, originals)
        if flow["history"] == "unavailable_history":
            raise _Unavailable
        if not closed:
            if flow["wip"] is None:
                self.reasons.add(flow["history"])
            elif flow["wip"]:
                self.groups["management_wip"].add(wi)

    def stopped(self, op):
        if op["status"] in TERMINAL:
            return True
        saved = pm_lifecycle_gates.operation_binding(self.db, op["operation_id"])
        if saved:
            current = pm_lifecycle_gates.head(self.db, self.wid)
            return saved["execution_epoch"] != current["execution_epoch"]
        return False

    def steps(self, op):
        return self.rows("SELECT * FROM operation_steps WHERE operation_id=? ORDER BY seq LIMIT ?", (op["operation_id"],))

    def unsent(self, op, steps):
        if op["status"] not in {"failed", "cancelled"} or pm_work_start._uncertain_db(self.db, op["operation_id"]):
            return False
        sends = [row for row in steps if row["name"] == "send"]
        return not sends or all(row["status"] == "failed" and
            pm_evidence._body(row["response"]).get("accepted") is False for row in sends)

    def execution(self, wi, op, *, attempt=None):
        self.work(wi)
        self.source(op["operation_id"])
        key = wi, op["operation_id"], attempt["attempt_id"] if attempt else None
        if key in self.executions:
            return self.executions[key]
        associated = ([self.source(row[0]) for row in self.rows(LINEAGE_SQL, (op['operation_id'],))]
                      if attempt else [])
        input_kinds = {}
        if attempt:
            for later in associated:
                if later['action'] not in LINEAGE_ACTIONS:
                    raise _Unavailable
                if LINEAGE_ACTIONS[later['action']] == 'input':
                    input_kinds[later['operation_id']] = self.input_kind(attempt, op, later)
        if sum(kind == 'direct' for kind in input_kinds.values()) > pm_direct_inputs.MAX_DIRECT_INPUTS:
            raise _Unavailable
        steps = self.steps(op)
        released = self.unsent(op, steps)
        if not released and self.works[wi][1] and op["status"] == "succeeded":
            if not pm_work_start._uncertain_db(self.db, op["operation_id"]):
                if attempt:
                    released = self.attempt_complete(attempt, op, associated, input_kinds)
                elif pm_execution_runtime.relation(self.ops, op) is not None:
                    proof = pm_execution_runtime.proof(self.ops, self.principal, op)
                    released = bool(proof and pm_execution_runtime._settled(self.ops, op, proof["observation_operation_id"]))
        self.executions[key] = released
        if not released:
            self.groups["unresolved_execution"].add(wi)
        return released

    def input_kind(self, attempt, handoff, op):
        session = pm_work_start._child_session(handoff)
        if session and op['target'] == session:
            return 'direct'
        target = {'workspace_id': self.wid, 'work_item_id': attempt['work_item_id']}
        refs = op.get('external_refs') or {}
        binding = (refs.get('admission_binding') or {}).get('work_follow_up') or {}
        if op['target'] == target and session:
            rows = self.rows('SELECT * FROM work_inputs WHERE operation_id=? LIMIT ?',
                             (op['operation_id'],), bound=1)
            if rows:
                row = rows[0]
                expected = {**target, 'attempt_id': attempt['attempt_id'], **session}
                if (all(row[k] == v and binding.get(k) == v for k, v in expected.items())
                        and binding.get('execution_operation_id') == handoff['operation_id']
                        and refs.get('resolved_target') == session):
                    return 'work'
        raise _Unavailable

    def attempt_complete(self, attempt, handoff, associated, input_kinds):
        """Apply only saved original generation/turn evidence; never query runtime."""
        session = pm_work_start._child_session(handoff)
        if not session:
            return False
        room = min(policy.MAX_RECORDS - self.used_records, pm_direct_inputs.MAX_DIRECT_INPUTS)
        direct = pm_direct_inputs.eligible(self.db, handoff['operation_id'], session, limit=room + 1)
        self.used_records += len(direct)
        if len(direct) > room:
            raise _Unavailable
        eligible = {row['operation_id']: row for row in direct}
        later_markers = {row['marker'] for row in direct}
        queued_markers = {row['marker'] for row in direct if row['queued']}
        for op in associated:
            if op['operation_id'] == handoff['operation_id']:
                continue
            if LINEAGE_ACTIONS[op['action']] != 'input' or input_kinds.get(op['operation_id']) == 'work':
                continue  # Work inputs retain their existing row/marker path below.
            steps = self.steps(op)
            if pm_work_start._uncertain_db(self.db, op['operation_id']):
                return False  # Only the original reconciliation can settle this send.
            if op['status'] in TERMINAL:
                sends = [row for row in steps if row['name'] == 'send']
                # REFUSED is the existing WriteRefused pre-dispatch code. A
                # generic failed step is not proof that no frame was written.
                if not sends or all(
                    pm_evidence._body(row['response']).get('accepted') is False
                    or row['status'] == 'failed' and pm_evidence._body(row['error']).get('code') == 'REFUSED'
                    for row in sends
                ):
                    continue
                if op['operation_id'] not in eligible:
                    raise _Unavailable
            elif op['operation_id'] not in eligible:
                return False
        if not pm_attempt_results.installed(self.db) or not pm_work_follow_up.installed(self.db):
            return False
        aid = attempt["attempt_id"]
        host, sid = session["host"], session["session_id"]
        observed = self.db.execute("SELECT body FROM sessions_observed WHERE host=? AND session_id=?", (host, sid)).fetchone()
        origin = pm_evidence._body(observed[0]).get("registry_origin") if observed else None
        if not isinstance(origin, dict) or origin.get("start_operation_id") != handoff["operation_id"]:
            return False
        generation = pm_attempt_results.source_generation_from_registry(host, sid, origin)
        if generation == "unknown":
            return False
        marker = pm_work_follow_up._initial_attempt_turn_marker(self.db, aid)
        inputs = self.rows("SELECT * FROM work_inputs WHERE attempt_id=? ORDER BY position LIMIT ?", (aid,))
        for row in inputs:
            op = self.source(row["operation_id"])
            steps = self.steps(op)
            send = next((dict(step) for step in steps if step["name"] == "send"), None)
            state = pm_work_follow_up.receipt_state(op, send)
            if state in {"failed", "cancelled"}:
                continue
            if state != "sent" or op["status"] not in TERMINAL or pm_work_start._uncertain_db(self.db, op["operation_id"]):
                return False
            marker = row["message_id"]
        receipts = self.rows("SELECT * FROM session_results WHERE workspace_id=? AND attempt_id=? ORDER BY sequence DESC LIMIT ?",
                             (self.wid, aid))
        required = later_markers | {marker}
        matched, terminal = set(), set()
        for row in receipts:
            self.source(row["operation_id"])
            terminal.update(pm_work_follow_up.terminal_receipt_markers(row))
            doc = pm_evidence._body(row["document"])
            pos = pm_attempt_results.parse_source_position(row["source_position"])
            result_marker = pos.get("turn_marker") or doc.get("turn_marker")
            if result_marker not in required:
                continue
            attribution = doc.get("turn_attribution", (doc.get("turn_progress") or {}).get("turn_attribution"))
            allowed = {'correlated', 'correlated_after_prior_turn'} if result_marker in queued_markers else {'correlated'}
            if (row["source_key"] != f"bat:{host}:{sid}" or row["source_generation"] != generation
                    or attribution not in allowed):
                return False
            matched.add(result_marker)
        # Existing terminal boundary predicate, after exact original-source checks.
        return required <= matched and required <= terminal

    def start(self, op, attempt=None):
        intent = pm_work_start._intent(op)
        wi = op["target"]["work_item_id"]
        self.work(wi)
        if intent.get("workspace_id") != self.wid or intent.get("work_item_id") != wi:
            raise _Unavailable
        if attempt is None:
            row = self.db.execute("SELECT * FROM work_attempts WHERE workspace_id=? AND start_operation_id=?",
                                  (self.wid, op["operation_id"])).fetchone()
            if row is None:
                raise _Unavailable
            attempt = dict(row)
        if attempt["admission_digest"] != (op.get("external_refs") or {}).get("admission_binding", {}).get("work_start_digest"):
            raise _Unavailable
        children = pm_work_start._children(self.ops, op)
        for child in children.values():
            self.source(child["operation_id"])
        handoff = children.get("handoff")
        if handoff:
            self.execution(wi, handoff, attempt=attempt)
        elif not self.stopped(op) or any(pm_work_start._uncertain_db(self.db, c["operation_id"]) for c in children.values()):
            self.groups["accepted_selection"].add(wi)

    def root(self, op):
        action = op["action"]
        if action == pm_work_start.ACTION:
            self.start(op)
        elif action == pm_work_batches.START:
            intent = pm_work_batches._intent(op)
            for entry in intent["entries"]:
                self.used_records += 1
                if self.used_records > policy.MAX_RECORDS:
                    raise _Unavailable
                package = pm_package_handoff._package(self.ops, self.principal, entry["handoff"]["params"]["pm_package_ref"])
                wi = package["document"]["work_item_id"]
                self.work(wi)
                child = pm_work_batches._child(self.ops, op, entry)
                if child:
                    self.execution(wi, child)
                elif not self.stopped(op):
                    self.groups["accepted_selection"].add(wi)
        elif action == pm_work_adopt_and_start.ACTION:
            intent = pm_work_adopt_and_start._intent(op)
            for i, selected in enumerate(intent["works"]):
                self.used_records += 1
                if self.used_records > policy.MAX_RECORDS:
                    raise _Unavailable
                wi = selected["work_item_id"]
                self.work(wi)
                child = pm_work_adopt_and_start._child(self.ops, op, f"work:{i}")
                if child:
                    self.source(child["operation_id"])
                    self.start(child)
                elif not self.stopped(op):
                    self.groups["accepted_selection"].add(wi)
        else:
            saved = pm_package_handoff.binding(op)
            if not saved or not pm_package_handoff.source_visible(self.db, self.principal, op):
                raise _Unavailable
            wi = saved["contract"]["package"]["work_item_id"]
            # Work-start execution is evaluated with its original Attempt once.
            link = self.db.execute("SELECT parent_id FROM pm_work_start_children WHERE operation_id=? AND slot='handoff'",
                                   (op["operation_id"],)).fetchone() if pm_work_start.installed(self.db) else None
            if link:
                self.start(self.source(link[0]))
            else:
                self.execution(wi, op)

    def task_unsent(self, command, kinds):
        """Read the original coordinator's pre-dispatch/reconciliation outcomes."""
        cid = command['command_id']
        reconciled = self.rows('SELECT * FROM command_reconciliations WHERE command_id=? LIMIT ?', (cid,))
        oid = pm_evidence._body(command['payload']).get('operation_id')
        if oid:
            op = self.source(oid)
            steps = self.steps(op)
            if pm_work_start._uncertain_db(self.db, oid):
                return False
            for step in steps:
                if step['name'] == 'task_dispatch' and (step['status'] in {'started', 'uncertain'} or
                        step['status'] == 'succeeded' and pm_evidence._body(step['response']).get('accepted') is not False):
                    return False
        if reconciled:
            # Only the existing exact-command not-delivered decision proves no send.
            return (command['status'] == 'resolved_not_delivered' and reconciled[0]['task_id'] == command['task_id']
                    and reconciled[0]['outcome'] == 'not_delivered')
        if command['status'] not in {'cancelled', 'rejected'} or command['marker']:
            return False
        # These local outcomes are the ones TaskCoordinator._recover_unsent_send replays.
        # A contradictory historical ACK/fence/uncertainty never becomes unsent by status alone.
        return ('command_intent' in kinds and 'command_' + command['status'] in kinds and
                not kinds & {'command_accepted', 'command_running', 'command_settled', 'command_uncertain',
                             'command_reconciled', 'send_reconciled_delivered'})

    def links(self, wi):
        for link in self.rows(LINKS_SQL, (wi,)):
            kind, ref = link["kind"], link["ref"]
            if kind not in {"operation", "task", "session"}:
                continue
            self.source(link["link_operation"])
            if kind == "operation":
                op = self.source(ref)
                if pm_evidence._read_operation(self.ops, self.principal, self.wid, ref) is None:
                    raise _Unavailable
                if op["action"] in artifact_managed.EXECUTIONS:
                    self.root(op) if op["action"] == "repository.continue" and pm_package_handoff.binding(op) else self.execution(wi, op)
            elif kind == "task":
                if not pm_evidence._task_visible(self.ops, self.principal, self.wid, ref):
                    raise _Unavailable
                commands = self.rows("SELECT * FROM commands WHERE task_id=? AND kind='send' LIMIT ?", (ref,))
                for command in commands:
                    if not pm_evidence._command_visible(self.ops, self.principal, self.wid, command["command_id"]):
                        raise _Unavailable
                events = self.rows(TASK_EVENTS_SQL, (ref,)) if commands else []
                event_kinds = {}
                for event in events:
                    cid = pm_evidence._body(event['body']).get('command_id')
                    event_kinds.setdefault(cid, set()).add(event['kind'])
                unsent = [self.task_unsent(command, event_kinds.get(command['command_id'], set())) for command in commands]
                tasks = self.rows('SELECT state FROM tasks WHERE task_id=? LIMIT ?', (ref,)) if commands else []
                if commands and (not self.works[wi][1] or not tasks or tasks[0][0] not in task_journal.TERMINAL
                                 or not all(unsent)):
                    self.groups["unresolved_execution"].add(wi)
            else:
                if not pm_evidence._session_visible(self.ops, self.principal, self.wid, ref):
                    raise _Unavailable
                # A mere session/idle observation has no correlated completion proof.
                self.groups["unresolved_execution"].add(wi)

    def scan(self):
        for table in ("pm_work_bindings", "work_attempts", "pm_work_batch_children", "pm_work_adopt_and_start_children"):
            if not pm_source_lineage._table(self.db, table):
                raise _Unavailable
        binding = self.db.execute(FIRST_BINDING_SQL, (self.wid,)).fetchone()
        while binding:
            # A reverse index seek lands on the next Work's newest sequence directly.
            self.used_records += 1
            if self.used_records > policy.MAX_RECORDS:
                raise _Unavailable
            self.management(binding)
            binding = self.db.execute(BINDING_SQL, (self.wid, binding['work_item_id'])).fetchone()
        for row in self.rows(ROOT_SQL, (self.wid,)):
            self.root(self.source(row["operation_id"]))
        # Do not silently drop older Attempts absent from the current root query.
        for row in self.rows("SELECT * FROM work_attempts WHERE workspace_id=? LIMIT ?", (self.wid,)):
            op = self.source(row["start_operation_id"] or row["origin_id"])
            self.start(op, dict(row))
        if pm_work_follow_up.installed(self.db):
            for row in self.rows("SELECT * FROM work_inputs WHERE workspace_id=? LIMIT ?", (self.wid,)):
                wi = row["work_item_id"]
                self.work(wi)
                op = self.source(row["operation_id"])
                steps = self.steps(op)
                send = next((dict(step) for step in steps if step["name"] == "send"), None)
                if pm_work_follow_up.receipt_state(op, send) not in {"failed", "cancelled", "sent"}:
                    self.groups["unresolved_execution"].add(wi)
        if pm_attempt_results.installed(self.db):
            for row in self.rows("SELECT operation_id FROM session_results WHERE workspace_id=? LIMIT ?", (self.wid,)):
                self.source(row["operation_id"])
        for wi in list(self.works):
            self.links(wi)


def collect(ops, principal, workspace_id, now):
    collector = _Collector(ops, principal, workspace_id, now)
    try:
        collector.scan()
    except (_Unavailable, OperationError, KeyError, TypeError, ValueError):
        return {"used": None, "coverage": {"status": "unknown", "reasons": [UNAVAILABLE], "outside_scope": OUTSIDE_SCOPE}}
    if collector.reasons:
        return {"used": None, "coverage": {"status": "unknown", "reasons": sorted(collector.reasons), "outside_scope": OUTSIDE_SCOPE}}
    counted, breakdown = set(), {}
    for reason in REASONS:
        distinct = collector.groups[reason] - counted
        breakdown[reason] = len(distinct)
        counted.update(distinct)
    return {"used": len(counted), "breakdown": breakdown,
            "coverage": {"status": "complete", "reasons": [], "outside_scope": OUTSIDE_SCOPE}}
