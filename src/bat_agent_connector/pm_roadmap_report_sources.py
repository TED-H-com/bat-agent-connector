"""Authorize a complete original Roadmap closure before any diagnostic/projection.

The immutable observation, not a snapshot alone, selects the schedule. Authority
lookups may read current grants; report facts never read current heads or latest.
"""
from __future__ import annotations

import json
import re

from . import pm_access, pm_evidence, pm_plans, pm_read_cache, pm_roadmap, pm_snapshots, pm_source_reads
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
        for identity in sorted(set(ids)):
            self.charge()
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
        value['document'] = json.loads(row['document'])
        self.charge(value)
        self.operations([value['operation_id']])
        return value

    def plan(self, identity):
        self.charge()
        value = pm_plans._revision(self.ops.db, self.wid, identity)
        self.charge(value)
        self.operations(pm_plans.source_operation_ids(self.ops.db, self.wid, identity, include_adoption=False))
        return value

    def collect(self, identity):
        observation = self.roadmap(identity)
        doc = observation['document']
        baseline = self.roadmap(doc['baseline_revision_id'])
        forecast = self.roadmap(doc['forecast_revision_id']) if doc['forecast_revision_id'] else None
        row = self.ops.db.execute('SELECT * FROM pm_document_snapshots WHERE workspace_id=? AND snapshot_id=?',
                                  (self.wid, doc['snapshot_id'])).fetchone()
        if row is None:
            raise unavailable()
        snapshot = dict(row)
        self.charge(row['document'])
        snapshot['document'] = json.loads(row['document'])
        self.charge(snapshot)
        saved = snapshot['document']
        self.operations([row['operation_id'], *pm_snapshots._plan_source_operations(self.ops.db, saved)])
        evidence = []
        for entry in saved['evidence']:
            self.charge(entry)
            evidence.append(pm_evidence.get_evidence(self.ops, self.principal, self.wid, entry['evidence_id'])['evidence'])
        maintenance = saved.get('maintenance')
        if maintenance:
            from . import pm_maintenance_review_sources
            for reference in maintenance['access_refs']:
                self.charge(reference)
                pm_maintenance_review_sources.access(self.ops, self.principal, self.wid, [reference])
        plans, adoptions = {}, {}
        selected = [(m['plan_revision_id'], m['scope_revision']) for m in baseline['document']['milestones']]
        # The snapshot's candidate lineage is also in the conservative closure.
        for identity in {saved['baseline'].get('document_revision'),
                         (saved.get('candidate_document') or {}).get('revision_id')} - {None}:
            plans[identity] = self.plan(identity)
        for identity, scope in sorted(set(selected)):
            if identity not in plans:
                plans[identity] = self.plan(identity)
            # A scope may be reused on readoption. The first matching historical
            # decision is stable; a later adoption can never substitute for it.
            adoption = self.ops.db.execute('''SELECT * FROM pm_plan_adoptions
                WHERE workspace_id=? AND revision_id=? AND scope_revision=? ORDER BY rowid LIMIT 1''',
                (self.wid, identity, scope)).fetchone()
            if adoption is None:
                raise unavailable()
            value = dict(adoption)
            value['result'] = json.loads(value['result'])
            self.charge(value)
            self.operations([value['operation_id'], value['result']['commit_operation_id']])
            adoptions[identity, scope] = value
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
    if saved['template_version'] != 1:
        raise OperationError('PM_EXPORT_TEMPLATE_UNAVAILABLE', 'The snapshot template is unsupported', 409)


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
    except (OperationError, KeyError, TypeError, ValueError, RecursionError):
        # No source-specific corruption/size/coverage information before full access.
        raise unavailable() from None
    _verify(closure)
    return bounded(project(closure))
