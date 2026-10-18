"""The PM ranks fixed evidence; it cannot supply facts, numbers or decisions."""
from __future__ import annotations

import copy
import json

import pytest

from bat_agent_connector import pm_overview_assessment_format as fmt


def context():
    return {'goal_ref': 'goal', 'required_limitations': [], 'eligible_attention': True,
        'source_references': {
            'projection': {'category': 'projection'}, 'goal': {'category': 'goal'},
            'req': {'category': 'requirement'},
            'blocker': {'category': 'blocker', 'goal_ref': 'goal', 'requirement_refs': ['req'],
                'eligible_templates': ['goal_blocker', 'clarify_dependency']},
            'decision': {'category': 'decision', 'eligible_templates': ['decision_first', 'confirm_direction']},
            'risk': {'category': 'risk', 'eligible_templates': ['watch_exposure', 'watch_recorded_risk']},
            'arrangement': {'category': 'arrangement', 'eligible_templates': ['follow_recorded_action']},
        }}


def document():
    return {'version': 1,
        'judgment': {'key': 'j', 'template': 'goal_blocker', 'subject_ref': 'blocker', 'basis_refs': ['blocker', 'goal', 'req']},
        'recommendations': [], 'needs_decision': [], 'can_be_handled': [], 'watch': [], 'limitations': []}


def validate(value, ctx=None):
    return fmt.validate(json.dumps(value, ensure_ascii=False), ctx or context())


def test_accepts_ranked_original_refs_without_mutating_evidence():
    ctx, value = context(), document()
    original = copy.deepcopy(ctx)
    value['needs_decision'] = [{'key': 'd.a', 'template': 'confirm_direction', 'subject_ref': 'decision', 'basis_refs': ['decision']}]
    value['can_be_handled'] = [{'key': 'h.a', 'template': 'follow_recorded_action', 'subject_ref': 'arrangement', 'basis_refs': ['arrangement']}]
    value['watch'] = [{'key': 'w.a', 'template': 'watch_recorded_risk', 'subject_ref': 'risk', 'basis_refs': ['risk']}]
    saved, status = validate(value, ctx)
    assert saved == value and status['status'] == 'format_valid'
    assert status['conclusions_verified'] is False and ctx == original


@pytest.mark.parametrize('extra', [{'text': 'three issues remain'}, {'count': 3}, {'confirmed_by': 'owner'},
    {'percentage': '一半'}, {'url': 'https://example.org'}, {'deadline': 'tomorrow'}])
def test_rejects_every_prose_quantity_and_confirmation_slot(extra):
    value = document()
    value['judgment'].update(extra)
    assert validate(value)[0] is None


@pytest.mark.parametrize('change', ['unknown_ref', 'missing_goal', 'missing_requirement', 'duplicate_basis',
    'wrong_group_template', 'wrong_slot', 'invented_subject', 'wrong_version', 'too_many_decisions'])
def test_rejects_unsupported_claims(change):
    value = document()
    if change == 'unknown_ref':
        value['judgment']['basis_refs'].append('unselected')
    elif change == 'missing_goal':
        value['judgment']['basis_refs'].remove('goal')
    elif change == 'missing_requirement':
        value['judgment']['basis_refs'].remove('req')
    elif change == 'duplicate_basis':
        value['judgment']['basis_refs'].append('req')
    elif change == 'wrong_group_template':
        value['judgment']['template'] = 'confirm_direction'
    elif change == 'wrong_slot':
        value['judgment']['key'] = 'three'
    elif change == 'invented_subject':
        value['judgment']['subject_ref'] = 'risk'
    elif change == 'wrong_version':
        value['version'] = True
    else:
        value['needs_decision'] = [dict(value['judgment'])] * 3
    saved, status = validate(value)
    assert saved is None and status['status'] == 'needs_review'


def test_requires_exact_coverage_and_conservative_judgment_without_goal():
    ctx, value = context(), document()
    ctx['goal_ref'] = None
    ctx['required_limitations'] = ['coverage.goal']
    ctx['source_references']['coverage.goal'] = {'category': 'coverage'}
    assert validate(value, ctx)[0] is None
    value['judgment'] = {'key': 'j', 'template': 'insufficient_basis', 'subject_ref': 'projection', 'basis_refs': ['projection']}
    assert validate(value, ctx)[0] is None
    value['limitations'] = ['coverage.goal']
    assert validate(value, ctx)[0] == value
    value['judgment']['template'] = 'no_priority_identified'
    assert validate(value, ctx)[0] is None


def test_no_priority_requires_complete_coverage_and_no_eligible_attention():
    ctx, value = context(), document()
    value['judgment'] = {'key': 'j', 'template': 'no_priority_identified', 'subject_ref': 'projection', 'basis_refs': ['projection']}
    assert validate(value, ctx)[0] is None
    ctx['eligible_attention'] = False
    assert validate(value, ctx)[0] == value


def test_disjoint_classification_and_recorded_arrangement_required():
    ctx, value = context(), document()
    decision = {'key': 'd.a', 'template': 'confirm_direction', 'subject_ref': 'decision', 'basis_refs': ['decision']}
    value['needs_decision'] = [decision]
    ctx['source_references']['decision']['eligible_templates'].append('follow_recorded_action')
    value['can_be_handled'] = [{**decision, 'key': 'h.a', 'template': 'follow_recorded_action'}]
    assert validate(value, ctx)[0] is None
    value['needs_decision'] = []
    ctx['source_references']['decision']['eligible_templates'].remove('follow_recorded_action')
    assert validate(value, ctx)[0] is None


@pytest.mark.parametrize('raw', ['{"version":1,"version":1}', '{"version":NaN}', '[]', '"text"', '\ud800', ' '*17000])
def test_malformed_or_unbounded_reply_remains_needs_review(raw):
    saved, status = fmt.validate(raw, context())
    assert saved is None and status['status'] == 'needs_review'
