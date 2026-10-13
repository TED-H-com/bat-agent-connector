"""Bounded live project overview from visible central records, never saved reports."""
from __future__ import annotations

import datetime as dt
import json
import time

from . import pm_read_cache, pm_source_lineage, pm_thread_contexts, pm_workspace
from .operations import OperationError

SOURCE_LIMIT = 50
ATTENTION_LIMIT = 20
MILESTONE_LIMIT = 20
SEVERITY = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}


def capabilities():
    return {'version': 1, 'read_only': True}


class Projection:
    def __init__(self, ops, principal, wid):
        self.ops, self.principal, self.wid = ops, principal, wid
        self.unknown, self.coverage, self.attention = [], {}, []

    def missing(self, field, reason):
        item = {'field': field, 'reason': reason}
        if item not in self.unknown:
            self.unknown.append(item)

    def source(self, field, table, read):
        if not pm_source_lineage._table(self.ops.db, table):
            self.missing(field, 'source_unavailable')
            self.coverage[field] = {'complete': False, 'next_cursor': None}
            return None
        try:
            value = read()
        except OperationError as exc:
            if exc.status not in (403, 404, 409, 413):
                raise
            self.missing(field, 'source_unavailable' if exc.status in (403, 404) else 'source_incomplete')
            self.coverage[field] = {'complete': False, 'next_cursor': None}
            return None
        cursor = value.get('next_cursor', value.get('next_after'))
        self.coverage[field] = {'complete': cursor is None, 'next_cursor': cursor}
        if cursor is not None:
            self.missing(field, 'partial_page')
        return value

    def incomplete(self, field, reason='source_incomplete'):
        self.coverage.setdefault(field, {'next_cursor': None})['complete'] = False
        self.missing(field, reason)

    def current(self, field, table, sql, decode):
        """Read at most fifty current records; historical rows consume no slots."""
        page = self.source(field, table, lambda: {'rows': self.ops.db.execute(sql,
            (self.wid, SOURCE_LIMIT + 1)).fetchall()})
        if page is None:
            return []
        rows, result = page['rows'], []
        if len(rows) > SOURCE_LIMIT:
            self.coverage[field].update(complete=False, next_cursor=rows[SOURCE_LIMIT - 1]['sequence'])
            self.missing(field, 'partial_page')
        for row in rows[:SOURCE_LIMIT]:
            try:
                value = decode(row)
                if value is not None:
                    result.append(value)
            except OperationError as exc:
                if exc.status not in (403, 404, 409, 413):
                    raise
                if exc.status in (409, 413):
                    self.incomplete(field)
        return result

    def visible(self, value, field):
        try:
            pm_source_lineage.require_document(self.ops.db, self.principal, value)
            return True
        except OperationError as exc:
            if exc.status not in (403, 404, 409, 413):
                raise
            if exc.status in (409, 413):
                self.incomplete(field)
            return False

    def add(self, kind, title, ref, since, *, severity='medium', context=None, **extra):
        key = ':'.join([kind, ref['kind'], ref['id'], *[str(ref[k]) for k in ('revision_id', 'observation_id') if k in ref]])
        self.attention.append({'key': key, 'kind': kind, 'title': title, 'severity': severity,
            'since': since, 'context': context or {}, 'ref': ref, **extra})

    def plan(self):
        from . import pm_plans
        self.coverage['plan_candidate'] = {'complete': True, 'next_cursor': None}
        if not pm_source_lineage._table(self.ops.db, 'pm_plan_heads'):
            self.incomplete('plan', 'source_unavailable')
            self.incomplete('plan_candidate', 'source_unavailable')
            self.missing('progress', 'no_adopted_plan')
            return None, None
        head = pm_plans.head(self.ops.db, self.wid)
        # A withdrawn newer draft must not hide the otherwise visible adopted plan.
        rid = head['accepted_revision_id']
        if not rid:
            self.coverage['plan'] = {'complete': True, 'next_cursor': None}
        page = self.source('plan', 'pm_plan_revisions', lambda: pm_plans.get_plan(
            self.ops, self.principal, self.wid, rid)) if rid else None
        plan = page['plan'] if page else None
        if not plan:
            self.missing('progress', 'no_adopted_plan' if rid is None else 'source_unavailable')
        latest = head['latest_revision_id']
        if latest and latest != rid:
            candidate = self.source('plan_candidate', 'pm_plan_revisions', lambda: pm_plans.get_plan(
                self.ops, self.principal, self.wid, latest))
            if candidate:
                row = candidate['plan']
                self.add('decision', row['document']['title'], {'kind': 'plan_revision', 'id': latest}, row['created_at'])
        if not plan:
            return None, None
        goal = next((item['value'] for item in plan['document']['selected_inputs']
            if item['slot'] in {'value', 'goal', 'problem'} and item['status'] in {'user_confirmed', 'repo_evidence'}), None)
        if goal is None:
            self.missing('plan.goal', 'goal_unconfirmed' if any(item['slot'] in {'value', 'goal', 'problem'}
                for item in plan['document']['selected_inputs']) else 'no_explicit_goal')
        adopted = plan['adoption']
        return plan, {'revision_id': rid, 'number': plan['sequence'], 'title': plan['document']['title'],
            'goal': ' '.join(goal.splitlines()) if goal else None,
            'adopted_at': adopted['created_at'], 'adopted_by': adopted['adopted_by']}

    def work(self):
        from . import pm_evidence, work_items
        page = self.source('work', 'pm_work_bindings', lambda: pm_evidence.list_work(
            self.ops, self.principal, self.wid, limit=SOURCE_LIMIT))
        result = {}
        for binding in page['bindings'] if page else []:
            ref = {'kind': 'work_item', 'id': binding['work_item_id']}
            try:
                pm_thread_contexts.resolve(self.ops, self.principal, self.wid, ref)
                if binding['plan_revision_id']:
                    pm_thread_contexts.resolve(self.ops, self.principal, self.wid,
                        {'kind': 'plan_revision', 'id': binding['plan_revision_id']})
            except OperationError as exc:
                if exc.status not in (403, 404, 409, 413):
                    raise
                if exc.status in (409, 413):
                    self.incomplete('work')
                continue
            item = work_items._get_item(self.ops.db, binding['work_item_id'])
            result[item['work_item_id']] = {'binding': binding, 'work': item}
        return result

    def governance(self, plan):
        from . import pm_governance
        entries = self.current('governance', 'pm_governance_records', """SELECT r.sequence,r.record_id
            FROM pm_governance_records r JOIN pm_governance_heads h USING(record_id)
            JOIN pm_governance_revisions v ON v.revision_id=h.revision_id
            WHERE r.workspace_id=? AND json_extract(v.document,'$.status')!='reported_resolved'
            ORDER BY r.sequence LIMIT ?""", lambda r: pm_governance._view(self.ops, self.principal, self.wid, r['record_id']))
        blocked = set()
        for entry in entries:
            if not self.visible(entry, 'governance'):
                continue
            row, revision = entry['record'], entry['revision']
            doc = revision['document']
            if doc['status'] == 'reported_resolved':
                continue
            kind = ('decision' if row['kind'] == 'change' and not doc['decision'] else
                    'blocker' if row['kind'] in {'issue', 'dependency'} and doc['status'] != 'reported_resolved' else
                    'risk' if row['kind'] in {'risk', 'assumption'} else None)
            if kind is None:
                continue
            context = {'requirement_ids': doc['affected_requirement_ids'], 'revision_id': row['context']['plan_revision_id']}
            if kind == 'blocker' and plan and context['revision_id'] == plan['revision_id']:
                blocked.update(doc['affected_requirement_ids'])
            self.add(kind, doc['title'], {'kind': 'governance', 'id': row['record_id']}, row['created_at'],
                severity='high' if kind == 'blocker' else 'medium', context=context)
        return blocked

    def quality(self, plan, work):
        from . import pm_quality_reviews
        def decode(stub):
            row = pm_quality_reviews._review(self.ops, self.wid, stub['review_id'])
            pm_quality_reviews._access(self.ops, self.principal, row)
            return pm_quality_reviews._view(self.ops, self.principal, row)
        rows = self.current('quality', 'pm_quality_reviews', """SELECT r.review_id,r.sequence
            FROM pm_quality_reviews r JOIN pm_quality_review_heads h ON h.review_id=r.review_id
            WHERE r.workspace_id=? ORDER BY r.sequence LIMIT ?""", decode)
        accepted, reviewing, deliveries, revoked = set(), set(), [], {}
        if not plan:
            return accepted, reviewing, deliveries
        requirements = {r['id']: r for r in plan['document']['requirements']}
        for row in rows:
            if ('review_superseded' in row['applicability_reasons']
                    or row['work_item_id'] not in work or not self.visible(row, 'quality')):
                continue
            binding = work[row['work_item_id']]['binding']
            context = row['context']
            if (context['plan_ref']['revision_id'] != plan['revision_id']
                    or context['plan_ref']['scope_revision'] != plan['adoption']['scope_revision']
                    or context['work_ref']['binding_id'] != binding['binding_id']):
                continue
            assessment = row['assessment']
            selected = assessment['document']['criteria'] if assessment else []
            for rid in binding['requirement_ids']:
                req = requirements.get(rid)
                if req is None:
                    continue
                reviewing.add(rid)
                ids = {a['source_input_id'] for a in req['acceptance']}
                covered = bool(ids) and all(any(c['criterion'] == {'kind': 'acceptance', 'requirement_id': rid, 'input_id': iid}
                    and c['status'] == 'accepted' for c in selected) for iid in ids)
                if row['qualified'] and covered:
                    accepted.add(rid)
                if row['revocation'] and row['original_outcome']['qualified'] and covered:
                    revoked[rid] = row
            if row['qualified'] and assessment:
                deliveries.append({'work_item_id': row['work_item_id'], 'title': work[row['work_item_id']]['work']['title'],
                    'state': 'accepted', 'at': assessment['created_at']})
        for rid, row in revoked.items():
            if rid not in accepted:
                self.add('deviation', requirements[rid]['text'], {'kind': 'requirement', 'id': rid, 'revision_id': plan['revision_id']},
                    row['revocation']['created_at'], severity='high', context={'requirement_id': rid, 'work_item_id': row['work_item_id']},
                    reason='quality_revoked')
        return accepted, reviewing, deliveries

    def progress(self, plan, work, blocked, accepted, reviewing):
        if plan is None:
            return None, {}
        counts = {key: 0 for key in ('accepted', 'in_review', 'in_progress', 'blocked', 'not_started')}
        states = {}
        for req in plan['document']['requirements']:
            rid = req['id']
            items = [v['work'] for v in work.values() if v['binding']['plan_revision_id'] == plan['revision_id']
                and rid in v['binding']['requirement_ids']]
            state = ('blocked' if rid in blocked else 'accepted' if rid in accepted else
                'in_review' if rid in reviewing or any(w['state'] == 'done' for w in items) else
                'in_progress' if any(w['state'] in {'doing', 'waiting'} for w in items) else 'not_started')
            states[rid] = state
            counts[state] += 1
        complete = all(self.coverage.get(k, {}).get('complete', False) for k in ('work', 'governance', 'quality'))
        if not complete:
            self.missing('progress', 'incomplete_sources')
        return {'basis': {'revision_id': plan['revision_id']}, 'requirements': len(states),
            **(counts if complete else {k: None for k in counts}), 'complete': complete,
            'unknown_reason': None if complete else 'incomplete_sources'}, states if complete else {}

    def roadmap(self, plan, states):
        from . import pm_roadmap
        self.coverage['forecast'] = {'complete': True, 'next_cursor': None}
        if not pm_source_lineage._table(self.ops.db, 'pm_roadmap_heads'):
            self.missing('milestones', 'source_unavailable')
            self.coverage['roadmap'] = {'complete': False, 'next_cursor': None}
            return []
        head = pm_roadmap.head(self.ops.db, self.wid)
        rid = head['baseline_revision_id']
        if not rid:
            self.coverage['roadmap'] = {'complete': True, 'next_cursor': None}
            self.missing('next_milestone', 'no_roadmap_baseline')
            return []
        page = self.source('roadmap', 'pm_roadmap_revisions', lambda: pm_roadmap.get_revision(self.ops, self.principal, self.wid, rid))
        if not page:
            return []
        forecast = self.source('forecast', 'pm_roadmap_revisions', lambda: pm_roadmap.get_revision(
            self.ops, self.principal, self.wid, head['forecast_revision_id'])) if head['forecast_revision_id'] else None
        if forecast and not self.visible(forecast, 'forecast'):
            self.incomplete('forecast', 'source_unavailable')
            forecast = None
        forecast = forecast['revision'] if forecast else None
        dates = {m['milestone_id']: m['forecast_date'] for m in forecast['document']['milestones']} if forecast else {}
        milestones = []
        for item in page['revision']['document']['milestones']:
            ref = {'kind': 'milestone', 'id': item['milestone_id'], 'revision_id': rid}
            try:
                pm_thread_contexts.resolve(self.ops, self.principal, self.wid, ref)
            except OperationError as exc:
                if exc.status not in (403, 404, 409, 413):
                    raise
                if exc.status in (409, 413):
                    self.incomplete('roadmap')
                continue
            date = dates.get(item['milestone_id'])
            if date is None:
                self.missing('milestones.forecast_date', 'source_unavailable' if head['forecast_revision_id'] and forecast is None else 'not_recorded')
            delta = (dt.date.fromisoformat(date) - dt.date.fromisoformat(item['baseline_date'])).days if date and item['baseline_date'] else None
            status = 'not_assessed'
            if plan and item['plan_revision_id'] == plan['revision_id'] and item['requirement_ids'] and states:
                values = [states.get(r) for r in item['requirement_ids']]
                status = ('accepted' if all(v == 'accepted' for v in values) else 'blocked' if 'blocked' in values
                    else 'in_progress' if any(v in {'accepted', 'in_progress', 'in_review'} for v in values) else 'not_started')
            milestone = {'milestone_id': item['milestone_id'], 'title': item['title'], 'baseline_date': item['baseline_date'],
                'forecast_date': date, 'delta_days': delta, 'status': status, 'ref': ref}
            milestones.append(milestone)
            if delta is not None and delta > 0:
                self.add('deviation', item['title'], ref, forecast['created_at'],
                    context={'milestone_id': item['milestone_id']}, delta_days=delta, reason='forecast_later_than_baseline')
        return sorted(milestones, key=lambda m: (m['forecast_date'] or m['baseline_date'] or '9999-12-31', m['milestone_id']))

    def proposals(self):
        from . import pm_conversation_proposals, pm_runs
        def decode(row):
            pm_conversation_proposals.require_visible(self.ops.db, self.principal, row)
            context = json.loads(row['context'])
            if pm_runs._digest(context) != row['context_digest']:
                raise OperationError('PM_CONVERSATION_CHANGED', 'Original proposal context cannot be verified', 409)
            if not pm_conversation_proposals.adoptability(self.ops.db, row, context)['adoptable']:
                return None
            document = json.loads(row['document'])
            if pm_runs._digest(document) != row['document_digest']:
                raise OperationError('PM_CONVERSATION_CHANGED', 'Original proposal cannot be verified', 409)
            return {**dict(row), 'document': document}
        rows = self.current('proposals', 'pm_conversation_proposals', """SELECT p.*,r.context
            FROM pm_conversation_proposals p JOIN pm_runs r USING(run_id)
            JOIN pm_threads t ON t.thread_id=p.thread_id
            LEFT JOIN pm_plan_heads h ON h.workspace_id=p.workspace_id
            WHERE p.workspace_id=? AND json_extract(p.validation,'$.status')='format_valid'
            AND COALESCE(h.version,0)=json_extract(r.context,'$.plan_head.version')
            AND h.latest_revision_id IS json_extract(r.context,'$.plan_head.latest_revision_id')
            AND h.accepted_revision_id IS json_extract(r.context,'$.plan_head.accepted_revision_id')
            AND t.version-json_extract(r.context,'$.thread_version')=(
                SELECT COUNT(DISTINCT json_extract(a.result,'$.version')) FROM pm_discussion_applied a
                JOIN operations o USING(operation_id) WHERE json_extract(a.result,'$.thread_id')=t.thread_id
                AND json_extract(a.result,'$.version')>json_extract(r.context,'$.thread_version')
                AND json_extract(a.result,'$.version')<=t.version AND o.action='pm.thread.rename')
            ORDER BY p.sequence LIMIT ?""", decode)
        for row in rows:
            self.add('decision', row['document']['title'], {'kind': 'proposal', 'id': row['proposal_id']}, row['created_at'])

    def findings(self):
        from . import pm_maintenance_findings, pm_maintenance_policy
        if (pm_source_lineage._table(self.ops.db, 'pm_maintenance_policies')
                and pm_maintenance_policy.head(self.ops.db, self.wid)['policy_id'] is None):
            self.coverage['findings'] = {'complete': True, 'next_cursor': None, 'status': 'not_configured'}
            return
        page = self.source('findings', 'pm_github_observations', lambda: pm_maintenance_findings.current_findings(
            self.ops, self.principal, self.wid))
        if not page:
            return
        if all(source['status'] in {'disabled', 'stopped'} for source in page['sources'].values()):
            self.coverage['findings']['status'] = 'not_applicable'
        for source in page['sources'].values():
            if source['status'] in {'disabled', 'stopped'}:
                continue
            if source['status'] not in {'complete', 'incomplete'}:
                self.missing('findings', source['status'])
                self.coverage['findings']['complete'] = False
                continue
            if source['status'] == 'incomplete' or len(source['findings']) > SOURCE_LIMIT:
                self.coverage['findings']['complete'] = False
                self.missing('findings', 'partial_page')
            for finding in source['findings'][:SOURCE_LIMIT]:
                if finding['resolved'] or not page['observation_ref']:
                    continue
                ref = {'kind': 'finding', 'id': finding['finding_id'], **page['observation_ref']}
                if self.visible(finding, 'findings'):
                    severity = finding['facts'].get('severity', 'low')
                    self.add('risk', str(finding['native']['number']), ref, None,
                        severity=severity if severity in SEVERITY else 'low')

    def deliveries(self, work, accepted):
        from . import pm_delivery_lineage
        entries = list(accepted)
        for wid, item in work.items():
            page = self.source('deliveries.' + wid, 'pm_source_lineage', lambda wid=wid: pm_delivery_lineage.for_work(
                self.ops, self.principal, self.wid, wid, limit=20))
            for row in page['deliveries'] if page else []:
                if not self.visible(row, 'deliveries.' + wid):
                    continue
                state = (('rolled_back' if row['action'] == 'deployment.rollback' else 'deployed')
                         if row.get('deployment', {}).get('verified') is True else
                         'merged' if row.get('merge', {}).get('merged') is True else None)
                if state is None:
                    continue
                entry = {'work_item_id': wid, 'title': item['work']['title'], 'state': state,
                    'at': row['updated_at'], 'operation_id': row['operation_id']}
                target = row['target']
                if target.get('repository') and target.get('pull_number'):
                    entry['pr'] = {'repository': target['repository'], 'number': target['pull_number']}
                entries.append(entry)
        # A work item appears once, with its most recently recorded delivery fact.
        unique = {}
        for entry in sorted(entries, key=lambda e: (-e['at'], e['work_item_id'], e['state'])):
            unique.setdefault(entry['work_item_id'], entry)
        return list(unique.values())[:3]

    def lifecycle(self):
        from . import pm_lifecycle, pm_lifecycle_gates
        self.coverage['lifecycle_inflight'] = {'complete': True, 'next_cursor': None}
        head = pm_lifecycle_gates.head(self.ops.db, self.wid)
        if head['stage'] is None:
            self.missing('lifecycle', 'not_recorded')
            return None
        if head['transition_id']:
            row = pm_lifecycle._transition(self.ops.db, self.wid, head['transition_id'])
            if not pm_lifecycle._transition_visible(self.ops.db, self.principal, row):
                self.missing('lifecycle', 'source_unavailable')
                self.incomplete('lifecycle_inflight', 'source_unavailable')
                self.coverage['lifecycle_inflight']['omitted'] = None
                return None
            page = self.source('lifecycle_inflight', 'pm_lifecycle_inflight', lambda: pm_lifecycle.list_inflight(
                self.ops, self.principal, self.wid, limit=20))
            if page and page.get('visibility_filtered'):
                self.incomplete('lifecycle_inflight', 'source_unavailable')
                self.coverage['lifecycle_inflight']['omitted'] = page['omitted']
            pending = [item for item in page['inflight'] if item and item['disposition'] in {'unresolved', 'held'}] if page else []
            visible = []
            for item in pending:
                try:
                    allowed = pm_source_lineage.visible(self.ops.db, self.principal, 'operation', item['operation_id'])
                except OperationError as exc:
                    if exc.status not in (403, 404, 409, 413):
                        raise
                    allowed = False
                if allowed:
                    visible.append(item)
            omitted = len(pending) - len(visible)
            if omitted:
                self.incomplete('lifecycle_inflight', 'source_unavailable')
                self.coverage['lifecycle_inflight']['omitted'] = omitted + (page.get('omitted') or 0)
            pending = visible
            if pending:
                self.add('decision', row['reason'], {'kind': 'lifecycle_transition', 'id': head['transition_id']},
                    row['created_at'], severity='high', reason='lifecycle_inflight',
                    pending_operations=[{'operation_id': item['operation_id'], 'status': item['operation_status'],
                        'disposition': item['disposition']} for item in pending])
        return {'stage': head['stage'], 'since': head['updated_at']}

    def fit_sources(self, output):
        """Budget the union, not just individual rows, for the final read barrier."""
        def fits():
            try:
                pm_source_lineage.document_roots(self.ops.db, output)
                return True
            except OperationError as exc:
                if exc.status not in (409, 413):
                    raise
                return False
        candidates = []
        for item in output['attention']:
            candidates.extend((row, 'operation_id', 'lifecycle_inflight')
                for row in item.get('pending_operations', []))
        candidates.extend((item['context'], 'work_item_id', 'quality')
            for item in output['attention'] if item.get('reason') == 'quality_revoked')
        candidates.extend((row, 'operation_id', 'deliveries.' + row['work_item_id'])
            for row in output['recent_deliveries'])
        for row, key, field in candidates:
            if fits():
                return
            if key in row:
                del row[key]
                self.incomplete(field, 'source_limit')
        # Even three individually readable work items may have disjoint roots.
        # A delivery without its work identity is unusable: omit that whole row.
        while not fits() and output['recent_deliveries']:
            row = output['recent_deliveries'].pop()
            self.incomplete('deliveries.' + row['work_item_id'], 'source_limit')

    def read(self):
        lifecycle = self.lifecycle()
        plan, plan_out = self.plan()
        work = self.work()
        blocked = self.governance(plan)
        accepted, reviewing, deliveries = self.quality(plan, work)
        progress, states = self.progress(plan, work, blocked, accepted, reviewing)
        milestones = self.roadmap(plan, states)
        self.proposals()
        self.findings()
        recent = self.deliveries(work, deliveries)
        unique = {item['key']: item for item in self.attention}
        attention = sorted(unique.values(), key=lambda a: (SEVERITY[a['severity']], a['since'] is None, a['since'] or 0, a['key']))
        output = {'version': 1, 'workspace_id': self.wid, 'as_of': time.time(), 'lifecycle': lifecycle,
            'plan': plan_out, 'progress': progress,
            'next_milestone': next((m for m in milestones if m['status'] != 'accepted'), None),
            'milestones': milestones[:MILESTONE_LIMIT], 'milestones_truncated': len(milestones) > MILESTONE_LIMIT,
            'attention': attention[:ATTENTION_LIMIT], 'recent_deliveries': recent}
        self.fit_sources(output)
        if progress and not self.coverage.get('quality', {}).get('complete', False):
            output['progress'], _ = self.progress(plan, work, blocked, accepted, reviewing)
        complete = all(value['complete'] for value in self.coverage.values())
        groups = {'blocker': ('governance',), 'decision': ('governance', 'plan_candidate', 'proposals', 'lifecycle_inflight'),
                  'deviation': ('plan', 'work', 'roadmap', 'forecast', 'quality'), 'risk': ('governance', 'findings')}
        attention_complete = {kind: all(self.coverage.get(k, {'complete': False})['complete'] for k in sources)
                              for kind, sources in groups.items()}
        return {**output,
            'attention_truncated': len(attention) > ATTENTION_LIMIT or not all(attention_complete.values())
                or any(item['reason'] == 'source_limit' for item in self.unknown),
            'attention_counts': {k: sum(a['kind'] == k for a in attention) if attention_complete[k] else None
                for k in ('blocker', 'decision', 'deviation', 'risk')},
            'recent_deliveries_complete': all(v['complete'] for k, v in self.coverage.items() if k in {'work', 'quality'} or k.startswith('deliveries.')),
            'coverage': {'complete': complete, 'sources': self.coverage,
                'basis': 'current_visible_central_records', 'network_checked': False}, 'unknown': self.unknown}


def read(ops, principal, workspace_id):
    if not principal.allows('observe'):
        raise OperationError('FORBIDDEN', 'Overview requires observe scope / 專案總覽需要檢視權限', 403)
    from .pm_release_baselines import _snapshot
    with _snapshot(ops.db), pm_read_cache.scope(ops.db):
        pm_workspace.require_member(ops.db, principal, workspace_id)
        return Projection(ops, principal, workspace_id).read()
