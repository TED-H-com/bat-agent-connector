"""Weekly reports retain configured original findings without new provider reads."""
import copy
import uuid
from types import SimpleNamespace

import pytest

from bat_agent_connector import pm_maintenance_policy as policy
from bat_agent_connector import pm_maintenance_review as review
from bat_agent_connector import pm_snapshots
from tests import test_pm_maintenance_findings as findingtests
from tests import test_pm_maintenance_review as reviewtests
from tests.test_pm_evidence import OWNER
from tests.test_pm_release_baselines import execute

base_ops = findingtests.base_ops
release_ops = findingtests.release_ops
workload_ops = findingtests.workload_ops
github_ops = findingtests.github_ops
finding_ops = findingtests.ops
NOW = findingtests.NOW


@pytest.fixture
def ops(finding_ops, monkeypatch):
    review.install(finding_ops)
    clock = SimpleNamespace(time=lambda: NOW + 10)
    monkeypatch.setattr(review, 'time', clock)
    monkeypatch.setattr(pm_snapshots, 'time', clock)
    return finding_ops


async def test_fixed_configured_samples_and_disappearance_are_not_resolution(ops, monkeypatch):
    data = await findingtests.prepared(ops, monkeypatch)
    provider = ops.context['github']
    findingtests.populate(provider)
    await findingtests.githubtests.observe(ops, data)
    reads = list(provider.reads)
    _, first = await reviewtests.prepare(ops, data)
    report = first['snapshot']['maintenance']['report']
    assert provider.reads == reads
    assert report['coverage']['issues']['status'] == report['coverage']['dependencies']['status'] == 'observed'
    assert report['coverage']['dependencies']['manifest_scan_completeness'] == 'unknown'
    finding = report['observations'][0]['maintenance_sources']['issues']['findings'][0]
    assert finding['native']['id'] == 501 and finding['release_applicability'] == 'unknown'
    assert report['finding_changes']['issues']['status'] == 'comparison_unavailable'
    original = copy.deepcopy(first['snapshot'])
    provider.issues = []
    provider.alerts[0]['security_vulnerability']['severity'] = 'critical'
    await findingtests.githubtests.observe(ops, data)
    _, second = await reviewtests.prepare(ops, data, previous_review_id=first['review']['review_id'])
    changes = second['snapshot']['maintenance']['report']['finding_changes']
    assert changes['issues']['no_longer_observed'] == [finding['finding_id']]
    assert changes['issues']['resolution_assessed'] is False
    assert changes['dependabot_alerts']['changed']
    assert changes['dependabot_alerts']['whole_period_no_events_proven'] is False
    assert review.get_review(ops, OWNER, data['workspace_id'], first['review']['review_id'])['snapshot'] == original


async def test_newest_failed_dependency_read_never_uses_older_complete_sample(ops, monkeypatch):
    data = await findingtests.prepared(ops, monkeypatch)
    findingtests.populate(ops.context['github'])
    await findingtests.githubtests.observe(ops, data)
    ops.context['github'].failure = 403
    await findingtests.githubtests.observe(ops, data)
    _, saved = await reviewtests.prepare(ops, data)
    report = saved['snapshot']['maintenance']['report']
    assert report['coverage']['dependencies']['status'] == 'incomplete'
    assert report['finding_changes']['dependabot_alerts']['status'] == 'comparison_unavailable'
    assert report['report_status'] == 'data_gaps'


async def test_same_repository_different_config_does_not_inherit_observed_coverage(ops, monkeypatch):
    data = await findingtests.prepared(ops, monkeypatch)
    await findingtests.githubtests.observe(ops, data)
    _, first = await reviewtests.prepare(ops, data)
    current = policy.active_record(ops, OWNER, data['workspace_id'])
    changed = copy.deepcopy(current['policy'])
    changed['observation']['maintenance_sources']['issues']['labels_all'] = ['security']
    await execute(ops, {'action': policy.ACTION, 'target': {'workspace_id': data['workspace_id']},
        'params': {'policy': changed, 'rationale': 'Observe the new exact label selection'},
        'preconditions': {'expected_policy_revision': current['version'], 'expected_previous_policy_id': current['policy_id'],
            'expected_baseline_id': current['baseline_id'], 'expected_lifecycle_revision': 0},
        'idempotency_key': uuid.uuid4().hex})
    _, second = await reviewtests.prepare(ops, data, previous_review_id=first['review']['review_id'])
    report = second['snapshot']['maintenance']['report']
    assert report['coverage']['issues']['status'] == 'incomplete'
    assert report['coverage']['issues']['samples'] == 0
    assert report['coverage']['issues']['other_context_records']
    assert report['finding_changes']['issues']['status'] == 'comparison_unavailable'
    await findingtests.githubtests.observe(ops, data)
    _, third = await reviewtests.prepare(ops, data, previous_review_id=first['review']['review_id'])
    report = third['snapshot']['maintenance']['report']
    assert report['coverage']['issues']['status'] == 'observed'
    assert report['finding_changes']['issues']['status'] == 'comparison_unavailable'


async def test_configured_stale_findings_and_disabled_kinds_remain_distinct(ops, monkeypatch):
    data = await findingtests.prepared(ops, monkeypatch, {'issues': {'labels_all': []}, 'dependencies': None})
    await findingtests.githubtests.observe(ops, data)
    monkeypatch.setattr(review, 'time', SimpleNamespace(time=lambda: NOW + 1000))
    _, saved = await reviewtests.prepare(ops, data)
    report = saved['snapshot']['maintenance']['report']
    assert report['coverage']['issues']['status'] == 'stale'
    assert report['coverage']['dependencies']['status'] == 'disabled'
    assert report['report_status'] == 'data_gaps'
