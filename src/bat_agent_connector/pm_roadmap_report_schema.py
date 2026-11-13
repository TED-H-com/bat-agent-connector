"""Consumed v1 shapes, shared by report validation and mutation-test inventory.

This is a read-boundary schema, not a new specification or write authority.
Unknown fields are preserved. JSON leaves are only serialized, never dereferenced.
Optional means absent is supported; nullable is an independent property.
"""
from __future__ import annotations

import json
import math

from .operations import OperationError


def node(kind, **kw):
    return {'kind': kind, **kw}


def obj(**fields):
    return node('object', fields=fields)


def array(item):
    return node('array', item=item)


def optional(value):
    return {**value, 'optional': True}


def nullable(value):
    return {**value, 'nullable': True}


JSON = node('json')
TEXT = node('text')
REF = node('ref')  # Connector-owned fixed identities and digests, at most 512 UTF-8 bytes.
EXTERNAL = node('external')  # Producer-owned scalar identity; bounded by the source budget.
BOOL = node('bool')
INT = node('int')
NUMBER = node('number')
REFS = array(REF)
MAP = obj()
COVERAGE = obj(**{key: optional(BOOL) for key in (
    'work_truncated', 'evidence_truncated', 'bytes_truncated', 'projects_truncated', 'inaccessible_sources')})
INPUT = obj(input_id=REF, operation_id=REF, value=JSON, status=TEXT, source_ref=JSON,
            confirmed_by=JSON, evidence_verification=optional(JSON))
SECTION = obj(source_input_ids=REFS)
PLAN = obj(workspace_id=REF, markdown=TEXT, title=TEXT, requirements=array(obj(id=REF)),
           selected_inputs=array(INPUT), sections=obj(governance=SECTION, scope=SECTION))
ADOPTION = obj(decision_id=REF, commit_operation_id=REF, rationale=JSON, adopted_by=JSON, created_at=NUMBER)
ACCEPTED = obj(document_revision=nullable(REF), scope_revision=nullable(REF), document=nullable(PLAN),
               adoption=nullable(ADOPTION), document_digest=optional(REF), structured_scope_digest=optional(REF))
CRITERION = obj(kind=TEXT, requirement_id=optional(REF))
MATRIX = obj(version=INT, criteria=array(obj(criterion=CRITERION, text=JSON)),
    assessment=obj(criteria=array(obj(criterion=CRITERION, status=TEXT)), gaps=JSON, rationale=JSON),
    plan_ref=JSON, work_ref=JSON, dod_ref=JSON, package_ref=JSON, execution_operation_id=JSON)
FACT_COMMON = {key: optional(nullable(TEXT)) for key in ('state', 'status', 'source_lane')}
FACT_AUTH = {key: optional(nullable(REF)) for key in ('task_id', 'command_id', 'execution_id')}
FACT_AUTH.update({key: optional(nullable(EXTERNAL)) for key in
    ('session_resource_id', 'resource_id', 'ref', 'kind', 'resource_type')})
FACT_AUTH['ref'] = optional(nullable(node('external', max_chars=300)))
FACTS = obj(**FACT_COMMON, **FACT_AUTH)
QUALITY = obj(**FACT_COMMON, **FACT_AUTH, work_item_id=REF, binding_id=REF, scope_revision=REF,
    plan_revision_id=REF, applicable_at_selection=BOOL, qualified=BOOL,
    requirements=array(obj(requirement_id=REF, status=TEXT)), dod=TEXT, acceptance=TEXT,
    reference=JSON, candidate_commit=JSON, dod_revision_id=JSON, applicability_reasons=JSON,
    assessed_by=JSON, assessed_at=JSON, criterion_assessments=optional(MATRIX))
FACT_VARIANTS = {
    'quality_assessment': QUALITY,
    'observed_verification': obj(**FACTS['fields'], verification=TEXT, candidate_commit=JSON),
    'pr_merge': obj(**FACTS['fields'], merged_sha=JSON),
    'deployment': obj(**FACTS['fields'], deployment=TEXT),
    'integration_delivery': obj(**FACTS['fields'], delivery=TEXT),
    'execution_state': obj(**{**FACTS['fields'], 'task_id': REF}),
    'execution_command': obj(**{**FACTS['fields'], 'task_id': REF, 'command_id': REF}),
}
# _fact_summary copies these optional scalars into version/assessment JSON.
# Identity is conditionally dereferenced as a mapping by the shared projection.
for _facts in (FACTS, QUALITY, *FACT_VARIANTS.values()):
    for _key in ('verification', 'deployment', 'delivery', 'merge_verified', 'candidate_commit',
                 'tree_hash', 'head_sha', 'base_sha', 'merged_sha', 'delivered_sha'):
        _facts['fields'].setdefault(_key, optional(JSON))
    _facts['fields']['identity'] = optional(nullable(obj(**{key: optional(JSON) for key in
        ('source_sha', 'artifact_id', 'artifact_digest', 'release_id')})))

EVIDENCE_KINDS = ('operation_status', 'pr_merge', 'resource_relation', 'execution_state',
    'execution_command', 'observed_verification', 'session_observation', 'integration_delivery',
    'pr_observation', 'deployment', 'work_claim', 'work_link', 'quality_assessment')
EVIDENCE = obj(evidence_id=REF, source_kind=node('enum', values=EVIDENCE_KINDS), fact_digest=REF, document=obj(facts=FACTS))
BINDING = obj(binding_id=REF, requirement_ids=REFS)
CAPTURE = obj(capture_id=REF, binding_id=REF, coverage=obj(
    truncated=optional(BOOL), unavailable_sources=optional(INT),
    unsupported_sources=optional(INT), inaccessible_sources=optional(BOOL)))
WORK = obj(work_item_id=REF, binding=BINDING, evidence_ids=REFS, capture=nullable(CAPTURE), coverage=COVERAGE)
MAINTENANCE_KINDS = ('policy', 'baseline', 'github', 'workload', 'maintenance_check',
                     'maintenance_repair', 'evidence', 'snapshot_identity')
ACCESS_REF = obj(kind=node('enum', values=MAINTENANCE_KINDS), id=REF, digest=REF,
                 basis=optional(node('enum', values=('admission_context', 'final_document'))))
SNAPSHOT = obj(snapshot_id=REF, workspace_id=REF, workspace_name=TEXT, as_of=NUMBER,
    journal_cursor=INT, template_version=INT, baseline=ACCEPTED,
    candidate_document=optional(nullable(obj(revision_id=REF, document_digest=REF))),
    evidence=array(EVIDENCE), work=array(WORK), coverage=obj(**{**COVERAGE['fields'],
        **{key: BOOL for key in ('work_truncated', 'evidence_truncated', 'bytes_truncated')}}),
    maintenance=optional(nullable(obj(access_refs=array(ACCESS_REF)))))
BASE_MILESTONE = obj(milestone_id=REF, title=TEXT, plan_revision_id=REF, plan_document_digest=REF,
    scope_revision=REF, requirement_ids=REFS, work_binding_ids=REFS, baseline_date=nullable(TEXT),
    original_baseline_date=nullable(TEXT), depends_on=REFS)
BASELINE = obj(title=TEXT, rationale=TEXT, milestones=array(BASE_MILESTONE), changes=JSON)
FORECAST = obj(baseline_revision_id=REF, milestones=array(obj(milestone_id=REF, forecast_date=nullable(TEXT))))
QUALITY_CONTEXT = obj(profile=TEXT, plan_revision_id=REF, plan_document_digest=REF,
                      scope_revision=REF, requirement_ids=REFS)
MATCH = obj(work_item_id=REF, match_status=TEXT, work_binding_id=REF, capture_id=nullable(REF), requirement_ids=REFS)
OBSERVATION = obj(baseline_revision_id=REF, forecast_revision_id=nullable(REF), snapshot_id=REF,
    snapshot_digest=REF, as_of=NUMBER, journal_cursor=INT, milestones=array(obj(milestone_id=REF, evidence_ids=REFS, coverage=obj(**{**COVERAGE['fields'], 'evidence_truncated': BOOL}),
    work=array(MATCH), quality_context=optional(QUALITY_CONTEXT))), coverage=COVERAGE)
ADOPTION_RESULT = obj(**{key: REF for key in ('workspace_id', 'revision_id', 'document_revision',
    'scope_revision', 'decision_id', 'document_digest', 'structured_scope_digest', 'commit_operation_id')},
    previous_revision_id=nullable(REF), adopted_by=JSON)

# Only fields the collector itself dereferences or delegates are required here.
# Version-specific shape requirements belong to V1, after whole-closure access.
AUTHORITY = {
    'observation': obj(baseline_revision_id=REF, forecast_revision_id=nullable(REF), snapshot_id=REF),
    'baseline': obj(milestones=array(obj(plan_revision_id=REF, scope_revision=REF))),
    'forecast': MAP,
    'snapshot': obj(baseline=obj(document_revision=optional(nullable(REF)), document=optional(nullable(MAP)),
        adoption=optional(nullable(obj(decision_id=REF, commit_operation_id=REF)))),
        candidate_document=optional(nullable(obj(revision_id=REF))),
        evidence=array(obj(evidence_id=REF, source_kind=node('enum', values=EVIDENCE_KINDS),
                           document=obj(facts=obj(**FACT_AUTH)))),
        maintenance=optional(nullable(obj(access_refs=array(ACCESS_REF))))),
    'plan': obj(selected_inputs=optional(array(obj(operation_id=REF)))),
    'adoption': obj(commit_operation_id=REF),
    'operation': REF,
    'maintenance_reference': ACCESS_REF,
    'evidence_facts': obj(**FACT_AUTH),
}
V1 = {'observation': OBSERVATION, 'baseline': BASELINE, 'forecast': FORECAST,
      'snapshot': SNAPSHOT, 'plan': PLAN, 'adoption': ADOPTION_RESULT,
      **{'facts.' + kind: value for kind, value in FACT_VARIANTS.items()}}


def valid(spec, value):
    """Total for JSON values: exact scalar types; no coercion or SQL binding."""
    if value is None and spec.get('nullable'):
        return True
    kind = spec['kind']
    if kind == 'json':
        return True  # Already canonical-encoded within the collection budget.
    if kind in ('text', 'ref', 'external', 'enum'):
        if type(value) is not str:
            return False
        if kind in ('ref', 'external'):
            if not value or any(0xD800 <= ord(c) <= 0xDFFF for c in value):
                return False
            return (len(value) <= spec.get('max_chars', len(value)) if kind == 'external'
                    else len(value) <= 512 and len(value.encode('utf-8')) <= 512)
        return kind != 'enum' or value in spec['values']
    if kind == 'bool':
        return type(value) is bool
    if kind == 'int':
        return type(value) is int and 0 <= value <= 2**63 - 1
    if kind == 'number':
        return type(value) in (int, float) and 0 <= value <= 253402300799 and math.isfinite(value)
    if kind == 'array':
        return type(value) is list and all(valid(spec['item'], item) for item in value)
    if kind == 'object':
        return type(value) is dict and all(
            (key not in value and child.get('optional', False)) or (key in value and valid(child, value[key]))
            for key, child in spec['fields'].items())
    raise AssertionError('Unknown internal report schema node')


def require(spec, value, *, authority=False):
    if not valid(spec, value):
        if authority:
            raise OperationError('PM_REPORT_SOURCE_UNAVAILABLE', 'Original report sources are unavailable', 404)
        raise OperationError('PM_REPORT_SOURCE_CHANGED', 'The fixed report source identities do not match', 409)


# Native JSON safety budget (serde_json / connection_sources): count dict/list
# containers including each complete document root, not journal row envelopes.
# Scalars do not consume depth. This is downstream safety margin, not a producer
# shape contract; the iterative walk also covers opaque and future members.
MAX_CONTAINER_DEPTH = 127


def source_changed():
    return OperationError('PM_REPORT_SOURCE_CHANGED', 'The fixed report source identities do not match', 409)


def closure_documents(closure):
    """Every complete document, including opaque leaves and unknown members."""
    observation, baseline, forecast, snapshot, evidence, plans, adoptions = closure
    for row in (observation, baseline, forecast, snapshot, *plans.values(), *evidence):
        if row is not None:
            yield row['document']
    for row in adoptions.values():
        yield row['result']


def require_depth(document):
    # Iterators keep auxiliary memory proportional to depth, not document size.
    stack = [iter((document,))]
    while stack:
        value = next(stack[-1], _END)
        if value is _END:
            stack.pop()
        elif type(value) in (dict, list):
            if len(stack) > MAX_CONTAINER_DEPTH:
                raise source_changed()
            stack.append(iter(value.values() if type(value) is dict else value))


_END = object()


def validate_v1(closure):
    """One structural gate, before v1 proofs or any shared projection."""
    for document in closure_documents(closure):
        require_depth(document)
    observation, baseline, forecast, snapshot, evidence, plans, adoptions = closure
    for name, row in (('observation', observation), ('baseline', baseline), ('forecast', forecast), ('snapshot', snapshot)):
        if row is not None:
            require(V1[name], row['document'])
    for plan in plans.values():
        require(PLAN, plan['document'])
    for row in adoptions.values():
        require(ADOPTION_RESULT, row['result'])
    saved = snapshot['document']
    # Include the embedded accepted plan, and both fixed and authorized evidence.
    for entry in [*saved['evidence'], *evidence]:
        require(EVIDENCE, entry)
        require(FACT_VARIANTS.get(entry['source_kind'], FACTS), entry['document']['facts'])
    # These cross-reference shapes otherwise index maps in the shared Brief.
    accepted = saved['baseline']
    if accepted['document_revision'] is not None:
        require(obj(document_digest=REF, structured_scope_digest=REF, scope_revision=REF,
                    document=PLAN, adoption=ADOPTION), accepted)
    if accepted['document']:
        require(ADOPTION, accepted['adoption'])
        plan = accepted['document']
        inputs = {item['input_id'] for item in plan['selected_inputs']}
        for domain in ('governance', 'scope'):
            if not set(plan['sections'][domain]['source_input_ids']) <= inputs:
                require(BOOL, None)
    for entry in saved['evidence']:
        if entry['source_kind'] != 'quality_assessment':
            continue
        matrix = entry['document']['facts'].get('criterion_assessments')
        if matrix and matrix['version'] == 1:
            originals = {json.dumps(row['criterion'], sort_keys=True) for row in matrix['criteria']}
            for row in matrix['assessment']['criteria']:
                criterion = row['criterion']
                if criterion['kind'] == 'acceptance' and 'requirement_id' not in criterion:
                    require(BOOL, None)
                if row['status'] == 'rejected' and json.dumps(criterion, sort_keys=True) not in originals:
                    require(BOOL, None)
