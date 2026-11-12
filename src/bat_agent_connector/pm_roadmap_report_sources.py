"""Authorize a complete original Roadmap closure before any diagnostic/projection.

The immutable observation, not a snapshot alone, selects the schedule. Authority
lookups may read current grants; report facts never read current heads or latest.
"""
from __future__ import annotations

import json
import re

from . import (
    pm_access,
    pm_evidence,
    pm_plans,
    pm_read_cache,
    pm_roadmap,
    pm_roadmap_quality,
    pm_snapshots,
    pm_source_reads,
)
from .operations import OperationError
from .pm_release_baselines import _snapshot_read

MAX_AUTH_RECORDS = 2000
MAX_AUTH_BYTES = 16_000_000
MAX_SOURCE_BYTES = 2_000_000
OBSERVATION_ID = re.compile(r'pmro_[0-9a-f]{32}')
canonical = pm_roadmap._json
digest = pm_roadmap._digest


def unavailable():
    return OperationError('PM_REPORT_SOURCE_UNAVAILABLE', 'Original report sources are unavailable', 404)


def proof(condition):
    if not condition:
        raise OperationError('PM_REPORT_SOURCE_CHANGED', 'The fixed report source identities do not match', 409)


def bounded(value):
    if len(canonical(value).encode('utf-8')) > MAX_SOURCE_BYTES:
        raise OperationError('PM_EXPORT_TOO_LARGE', 'The complete fixed report exceeds its source byte bound', 413)
    return value


def structure(value, kind):
    """Only the containers/keys needed to enumerate original authority."""
    if not isinstance(value, kind):
        raise unavailable()
    return value


def reference(value):
    if not isinstance(value, str) or not value:
        raise unavailable()
    return value


def mappings(value):
    for item in structure(value, list):
        yield structure(item, dict)


class _Closure:
    """An authorization budget is a concealment boundary, never a size error."""
    def __init__(self, ops, principal, wid):
        self.ops, self.principal, self.wid = ops, principal, wid
        self.records, self.bytes = 0, 0

    def charge(self, value=None):
        self.records += 1
        if value is not None:
            self.bytes += len(canonical(value).encode('utf-8'))
        if self.records > MAX_AUTH_RECORDS or self.bytes > MAX_AUTH_BYTES:
            raise unavailable()

    def operations(self, ids):
        for identity in sorted({reference(identity) for identity in ids}):
            self.charge()
            if not self.ops.db.execute('SELECT 1 FROM operations WHERE operation_id=?', (identity,)).fetchone():
                raise unavailable()
            pm_access.require_operation_sources(self.ops.db, self.principal, [identity])

    def roadmap(self, identity):
        self.charge()
        row = self.ops.db.execute('SELECT * FROM pm_roadmap_revisions WHERE workspace_id=? AND revision_id=?',
                                  (self.wid, identity)).fetchone()
        if row is None:
            raise unavailable()
        value = dict(row)
        # Defer digest/kind/relationship checks until the entire closure is authorized.
        self.charge(row['document'])
        value['document'] = structure(json.loads(row['document']), dict)
        self.charge(value)
        self.operations([value['operation_id']])
        return value

    def plan(self, identity):
        self.charge()
        value = pm_plans._revision(self.ops.db, self.wid, identity)
        self.charge(value)
        document = structure(value['document'], dict)
        for item in mappings(document.get('selected_inputs', [])):
            reference(item['operation_id'])
        self.operations(pm_plans.source_operation_ids(self.ops.db, self.wid, identity, include_adoption=False))
        return value

    def adoption(self, row, adoptions):
        if row is None:
            raise unavailable()
        value = dict(row)
        if value['decision_id'] not in adoptions:
            self.charge(value)
            value['result'] = structure(json.loads(value['result']), dict)
            self.operations([value['operation_id'], reference(value['result']['commit_operation_id'])])
            adoptions[value['decision_id']] = value
        return adoptions[value['decision_id']]

    def collect(self, identity):
        observation = self.roadmap(identity)
        doc = observation['document']
        baseline = self.roadmap(reference(doc['baseline_revision_id']))
        forecast = self.roadmap(reference(doc['forecast_revision_id'])) if doc['forecast_revision_id'] is not None else None
        row = self.ops.db.execute('SELECT * FROM pm_document_snapshots WHERE workspace_id=? AND snapshot_id=?',
                                  (self.wid, reference(doc['snapshot_id']))).fetchone()
        if row is None:
            raise unavailable()
        snapshot = dict(row)
        self.charge(row['document'])
        snapshot['document'] = structure(json.loads(row['document']), dict)
        self.charge(snapshot)
        saved = snapshot['document']
        accepted = structure(saved['baseline'], dict)
        candidate = saved.get('candidate_document')
        if candidate is not None:
            structure(candidate, dict)
            reference(candidate['revision_id'])
        if accepted.get('document') is not None:
            structure(accepted['document'], dict)
        plan_id = accepted.get('document_revision')
        if plan_id is not None:
            reference(plan_id)
        self.operations([row['operation_id']])
        adoptions = {}
        original = accepted.get('adoption')
        if original is not None:
            structure(original, dict)
            self.operations([reference(original['commit_operation_id'])])
            self.adoption(self.ops.db.execute('''SELECT * FROM pm_plan_adoptions
                WHERE workspace_id=? AND decision_id=?''',
                (self.wid, reference(original['decision_id']))).fetchone(), adoptions)
        elif plan_id:
            raise unavailable()
        evidence = []
        for entry in mappings(saved['evidence']):
            self.charge(entry)
            structure(entry['document'], dict)
            evidence.append(pm_evidence.get_evidence(self.ops, self.principal, self.wid, reference(entry['evidence_id']))['evidence'])
        maintenance = saved.get('maintenance')
        if maintenance is not None:
            from . import pm_maintenance_review_sources
            for item in mappings(structure(maintenance, dict)['access_refs']):
                self.charge(item)
                pm_maintenance_review_sources.access(self.ops, self.principal, self.wid, [item])
        plans = {}
        selected = [(reference(m['plan_revision_id']), reference(m['scope_revision']))
                    for m in mappings(baseline['document']['milestones'])]
        for identity in {plan_id, candidate['revision_id'] if candidate is not None else None} - {None}:
            plans[identity] = self.plan(identity)
        for identity, scope in sorted(set(selected)):
            if identity not in plans:
                plans[identity] = self.plan(identity)
            # Include all legacy matches. Never make physical row order a fact.
            found = False
            for row in self.ops.db.execute('''SELECT * FROM pm_plan_adoptions
                WHERE workspace_id=? AND revision_id=? AND scope_revision=?''', (self.wid, identity, scope)):
                found = True
                self.adoption(row, adoptions)
            if not found:
                raise unavailable()
        return observation, baseline, forecast, snapshot, evidence, plans, adoptions


def _verify(closure):
    observation, baseline, forecast, snapshot, evidence, plans, adoptions = closure
    for value, kind in ((observation, 'observation'), (baseline, 'baseline'), (forecast, 'forecast')):
        if value is not None:
            proof(value['kind'] == kind and digest(value['document']) == value['document_digest'])
    doc, saved = observation['document'], snapshot['document']
    proof(doc['baseline_revision_id'] == observation['baseline_revision_id'] == baseline['revision_id'])
    proof(baseline['baseline_revision_id'] == baseline['revision_id'])
    if forecast:
        proof(forecast['baseline_revision_id'] == forecast['document']['baseline_revision_id'] == baseline['revision_id'])
    proof(doc['snapshot_digest'] == snapshot['snapshot_digest'] == digest(saved))
    proof(saved['snapshot_id'] == snapshot['snapshot_id'] and saved['workspace_id'] == snapshot['workspace_id'])
    proof(doc['as_of'] == saved['as_of'] and doc['journal_cursor'] == saved['journal_cursor'])
    if type(saved.get('template_version')) is not int or saved['template_version'] != 1:
        raise OperationError('PM_EXPORT_TEMPLATE_UNAVAILABLE', 'The snapshot template is unsupported', 409)
    for original, visible in zip(saved['evidence'], evidence, strict=True):
        proof(original == visible and digest(original['document']) == original['fact_digest'])
    for identity, plan in plans.items():
        proof(pm_plans._files(identity, plan['document'])['document_digest'] == plan['document_digest'])
    accepted = saved['baseline']
    if accepted['document_revision']:
        plan = plans[accepted['document_revision']]
        proof(plan['document_digest'] == accepted['document_digest'] and plan['document'] == accepted['document'])
        proof(plan['scope_digest'] == accepted['structured_scope_digest'])
    candidate = saved.get('candidate_document')
    if candidate:
        proof(plans[candidate['revision_id']]['document_digest'] == candidate['document_digest'])
    for milestone in baseline['document']['milestones']:
        plan = plans[milestone['plan_revision_id']]
        proof(plan['document_digest'] == milestone['plan_document_digest'])
        proof(set(milestone['requirement_ids']) <= {r['id'] for r in plan['document']['requirements']})
    baseline_milestones = {m['milestone_id']: m for m in baseline['document']['milestones']}
    proof(len(baseline_milestones) == len(baseline['document']['milestones']))
    observed = doc['milestones']
    proof(len(observed) == len(baseline_milestones) and {m['milestone_id'] for m in observed} == set(baseline_milestones))
    evidence_ids = {item['evidence_id'] for item in evidence}
    for milestone in observed:
        ids = milestone['evidence_ids']
        proof(isinstance(ids, list) and all(isinstance(eid, str) for eid in ids) and set(ids) <= evidence_ids)
        if 'quality_context' in milestone:  # Legacy observations have no quality context.
            proof(milestone['quality_context'] == pm_roadmap_quality.context(baseline_milestones[milestone['milestone_id']]))
    for value in adoptions.values():
        result = value['result']
        proof(all(result.get(key) == value[key] for key in
            ('workspace_id', 'revision_id', 'previous_revision_id', 'scope_revision', 'decision_id', 'adopted_by')))
        proof(result.get('document_revision') == value['revision_id'])
        plan = plans.get(value['revision_id'])
        if plan:
            proof(result.get('document_digest') == plan['document_digest'] and result.get('structured_scope_digest') == plan['scope_digest'])
    original = accepted.get('adoption')
    if original:
        value = adoptions[original['decision_id']]
        proof(value['revision_id'] == accepted['document_revision'] and value['scope_revision'] == accepted['scope_revision'])
        proof(original['commit_operation_id'] == value['result']['commit_operation_id'])


def project(closure):
    """Pure v1 projection: no authority, database, current state or clock reads."""
    observation, baseline, forecast, snapshot, _, plans, adoptions = closure
    saved = snapshot['document']
    references = [{'kind': 'snapshot', 'id': snapshot['snapshot_id'], 'digest': snapshot['snapshot_digest']}]
    for value in (baseline, forecast, observation):
        if value:
            references.append({'kind': value['kind'], 'id': value['revision_id'], 'digest': value['document_digest']})
    for identity, plan in sorted(plans.items()):
        references.append({'kind': 'plan', 'id': identity, 'digest': plan['document_digest'], 'scope_digest': plan['scope_digest']})
    for value in adoptions.values():
        references.append({'kind': 'adoption', 'id': value['decision_id'], 'digest': digest(value['result'])})
    references = sorted(references, key=canonical)
    snapshot_view = pm_snapshots._response(saved, snapshot['sequence'], snapshot['snapshot_digest'])['snapshot']
    result = {'version': 1, 'template_version': 1, 'workspace_id': saved['workspace_id'],
        'observation_revision_id': observation['revision_id'], 'snapshot_id': saved['snapshot_id'],
        'snapshot_digest': snapshot['snapshot_digest'], 'snapshot_view_digest': snapshot_view['view_digest'],
        'baseline_revision_id': baseline['revision_id'], 'forecast_revision_id': forecast['revision_id'] if forecast else None,
        'baseline_digest': baseline['document_digest'], 'forecast_digest': forecast['document_digest'] if forecast else None,
        'observation_digest': observation['document_digest'], 'source_manifest': references,
        'source_set_digest': digest({'profile': 'roadmap_report_v1', 'sources': references}),
        'snapshot': saved, 'baseline': baseline, 'forecast': forecast,
        'observation': {**observation, 'document': pm_roadmap._project_observation(observation['document'], snapshot_view)},
        'historical_plans': [{'revision_id': ident, 'document_digest': p['document_digest'], 'document': p['document']}
                             for ident, p in sorted(plans.items()) if ident != saved['baseline']['document_revision']
                             and any(m['plan_revision_id'] == ident for m in baseline['document']['milestones'])],
        'quality': pm_snapshots._quality_summary(saved),
        'coverage': {'snapshot': snapshot_view['coverage'], 'observation': observation['document']['coverage'],
                     'visibility': {'filtered': False, 'source_policy': 'whole_original_closure'},
                     'projection': 'roadmap_report', 'content_truncated': False},
        'execution_authorized': False, 'review_completed': False, 'published': False}
    result['view_digest'] = digest(result)
    return result


@_snapshot_read
def load_fixed(ops, principal, workspace_id, observation_revision_id):
    pm_roadmap._member(ops, principal, workspace_id)
    if not isinstance(observation_revision_id, str) or not OBSERVATION_ID.fullmatch(observation_revision_id):
        raise OperationError('INVALID_PARAMS', 'Select one exact Roadmap observation', 422)
    if not ops.db.execute('SELECT 1 FROM pm_roadmap_revisions WHERE workspace_id=? AND revision_id=?',
                          (workspace_id, observation_revision_id)).fetchone():
        raise OperationError('PM_ROADMAP_NOT_FOUND', 'Roadmap observation not found', 404)
    try:
        with pm_source_reads.scope(ops.db, principal):
            generation = pm_read_cache.refusal_generation()
            closure = _Closure(ops, principal, workspace_id).collect(observation_revision_id)
            if generation != pm_read_cache.refusal_generation():
                raise unavailable()
    except (OperationError, KeyError, TypeError, ValueError, RecursionError, AttributeError, IndexError):
        # No source-specific corruption/size/coverage information before full access.
        raise unavailable() from None
    _verify(closure)
    return bounded(project(closure))
