"""Real disk Git report commits retain the original carrier's create-new rules."""
from pathlib import Path

import pytest

from bat_agent_connector import pm_roadmap_report_files as files
from bat_agent_connector import pm_worktree
from bat_agent_connector.operations import AmbiguousOutcome
from tests.test_checkpoints import git, snapshot
from tests.test_pm_roadmap_report_files import rendered
from tests.test_pm_worktree import pm as pm_fixture
from tests.test_pm_worktree import resume, submit
from tests.test_repository_sync import world as world_fixture

world = world_fixture
pm = pm_fixture


@pytest.fixture
def report_writer(pm, monkeypatch):
    original = pm_worktree.commit_revision
    async def commit(ctx, plan, revision_files, **kwargs):
        report = rendered(format=ctx.params.get('format', 'markdown'), locale=ctx.params.get('locale', 'en'))
        report['workspace_id'] = pm['workspace_id']
        revision = files.files(report, [], ctx.operation_id)
        return await original(ctx, plan, revision, **kwargs)
    monkeypatch.setattr(pm_worktree, 'commit_revision', commit)
    return pm


@pytest.mark.parametrize('format', ['markdown', 'html', 'svg'])
async def test_report_commit_and_lost_reply_recover_exact_files(report_writer, format):
    w = report_writer
    before = snapshot(w['human'])
    def lose(_):
        raise AmbiguousOutcome('Lost original report reply')
    w['runner'].after['pm-revision'] = lose
    first = await submit(w, revision={}, format=format)
    assert first['status'] == 'uncertain', first
    done = await resume(w, first)
    assert done['status'] == 'succeeded', done
    receipt = done['result']['revision']
    assert receipt['artifact_kind'] == 'pm-roadmap-report'
    assert receipt['revision_id'] == 'pmrpt_' + first['operation_id'][3:]
    assert len(receipt['file_digests']) == 2
    for name in receipt['file_digests']:
        assert Path(w['plan']['worktree_path'], name).is_file()
    assert git(w['plan']['worktree_path'], 'rev-list', '--count', w['sha'] + '..HEAD') == '1'
    assert w['runner'].phases.count('pm-revision') == 1
    assert snapshot(w['human']) == before
