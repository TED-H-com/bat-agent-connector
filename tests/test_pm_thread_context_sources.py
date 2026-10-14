"""Context bindings through actual proposal, finding, repair and Issue fixtures."""
from __future__ import annotations

import json

import pytest

from bat_agent_connector import pm_context_bundles, pm_discussions, pm_overview, pm_thread_contexts
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OperationError
from tests import test_pm_conversation_proposals as proposals
from tests import test_pm_issue_mappings as issues
from tests import test_pm_maintenance_repairs as repairs
from tests import test_pm_quality_reviews as quality
from tests.test_pm_thread_contexts import create

world, pm, ready = quality.world, quality.pm, quality.ready
fixed = quality.fixed
reviewed = quality.reviewed
proposal_world = proposals.proposal_world
mapped = issues.mapped
repair_ready = repairs.repair_ready
OWNER = proposals.OWNER


async def bound(w, refs):
    done, _ = await create(w['d'].ops, w['workspace_id'], refs, who=OWNER)
    return {'workspace_id': w['workspace_id'], 'thread_id': done['result']['thread_id']}


class TestProposal:
    @pytest.fixture
    async def ready(self, pm):
        return await proposals.ready.__wrapped__(pm)

    async def test_proposal_origin_reaches_next_proposal_and_native_analysis(self, proposal_world):
        w, ops = proposal_world, proposal_world['d'].ops
        proposal = await proposals.proposed(w)
        target = await bound(w, [{'kind': 'proposal', 'id': proposal['proposal_id']}])
        env = proposals.propose_request(w)
        env['target'] = target
        env['preconditions']['expected_version'] = 1
        op = ops.create(OWNER, **env)[0]
        context = op['external_refs']['admission_binding']['conversation_proposal']['context']
        assert context['bound_context'][0]['summary']['summary'] == proposal['document']['summary']
        assert context['source_references']['context.0']['id'] == proposal['proposal_id']
        assert context['source_references']['context.0']['kind'] == 'proposal'
        assert proposal['operation_id'] in context['source_operation_ids']
        await proposals.complete(w, op)
        run = ops.db.execute('SELECT context FROM pm_runs WHERE operation_id=?', (op['operation_id'],)).fetchone()
        assert json.loads(run[0])['bound_context'][0]['id'] == proposal['proposal_id']
        overview = pm_overview.read(ops, OWNER, w['workspace_id'])
        assert any(a['ref'] == {'kind': 'proposal', 'id': proposal['proposal_id']} for a in overview['attention'])

    async def test_stale_proposal_stays_withdrawn_when_adoption_is_hidden(self, proposal_world, monkeypatch):
        from bat_agent_connector import pm_access
        w, ops = proposal_world, proposal_world['d'].ops
        proposal = await proposals.proposed(w)
        def pending():
            overview = pm_overview.read(ops, OWNER, w['workspace_id'])
            return [a for a in overview['attention'] if a['ref'] == {'kind': 'proposal', 'id': proposal['proposal_id']}]
        assert pending()
        parent = ops.create(OWNER, **proposals.adoption_request(w, proposal))[0]
        done = await proposals.complete(w, parent)
        assert done['status'] == 'succeeded', done
        assert not pending()
        actual = pm_access.operation_source_visible
        monkeypatch.setattr(pm_access, 'operation_source_visible',
            lambda db, principal, oid: oid != parent['operation_id'] and actual(db, principal, oid))
        # The proposal is no longer adoptable against the changed thread/plan.
        # Withdrawing the receipt must not revive a stale decision or expose it.
        assert not pending()


async def test_issue_mapping_context_resolves_saved_publication(mapped):
    w, ops = mapped, mapped['d'].ops
    published = await issues.publish(w)
    target = await bound(w, [{'kind': 'issue_mapping', 'id': published['result']['mapping_id']}])
    ctx = pm_discussions.context(ops, OWNER, **target)
    assert ctx['bound_context'][0]['available']
    assert ctx['bound_context'][0]['summary']['work_item_id'] == w['target']['work_item_id']
    assert published['operation_id'] in ctx['source_operation_ids']
    assert pm_thread_contexts.frozen_visible(ops.db, OWNER, w['workspace_id'], ctx)


async def test_finding_and_repair_context_require_original_source_scopes(repair_ready, mock):
    w, ops = repair_ready, repair_ready['d'].ops
    parent = await repairs.advance(w, ops.create(OWNER, **repairs.request(w))[0])
    repair = repairs.view(w, parent)
    obs = w['observation']
    finding = obs['document']['evidence']['maintenance_sources']['issues']['findings'][0]
    refs = [{'kind': 'finding', 'id': finding['finding_id'], 'observation_id': obs['observation_id'], 'document_digest': obs['document_digest']},
            {'kind': 'repair', 'id': repair['repair_id']}]
    target = await bound(w, refs)
    before = list(mock.channels())
    ctx = pm_discussions.context(ops, OWNER, **target)
    assert len(ctx['bound_context']) == 2 and all(c['available'] for c in ctx['bound_context'])
    assert all(c['summary'] for c in ctx['bound_context'])
    chip = ctx['bound_context'][0]
    assert chip['kind'] == 'finding'
    assert chip['finding'] == {'source': 'issues', 'number': 50, 'labels': ['bug', 'release-1.4']}
    assert chip['label'] == chip['bound_label'] == '50'
    assert 'unknowns' not in chip
    assert 'finding' not in ctx['bound_context'][1]
    assert pm_thread_contexts.frozen_visible(ops.db, OWNER, w['workspace_id'], ctx)
    limited = Principal(OWNER.actor, frozenset({'observe', 'manage'}))
    # Scope reduction alone retains observe-authorized facts. Withdrawing the
    # explicit repository association invalidates both original source records.
    assert pm_discussions.get_thread(ops, limited, **target)['thread']['context'][0]['available']
    from tests.test_pm_release_baselines import remove_repo
    await remove_repo(ops, ops.db.execute('SELECT project_id FROM work_items WHERE work_item_id=?', (w['target']['work_item_id'],)).fetchone()[0])
    read = pm_discussions.get_thread(ops, limited, **target)
    assert read['thread']['context'] == [{'kind': 'finding', 'position': 0, 'available': False},
                                      {'kind': 'repair', 'position': 1, 'available': False}]
    assert not pm_thread_contexts.frozen_visible(ops.db, limited, w['workspace_id'], ctx)
    # The generic HTTP/RPC final barrier must allow the redacted thread as well.
    from bat_agent_connector.pm_source_lineage import read_response
    assert read_response(ops.db, limited, read) == read
    assert list(mock.channels()) == before
    with pytest.raises(OperationError, match='PM_CONTEXT_SOURCE_UNAVAILABLE'):
        pm_thread_contexts.admission(ops, limited, w['workspace_id'], refs)


async def test_adopted_document_bundle_keeps_bound_summaries_in_utf8_budget(reviewed):
    w, ops = reviewed, reviewed['d'].ops
    plan = w['package']['document']['context']['plan_ref']
    rid = plan['revision_id']
    req = w['package']['document']['requirements'][0]
    target = await bound(w, [{'kind': 'requirement', 'id': req['id'], 'revision_id': rid}])
    binding, files, _ = pm_context_bundles._selected(ops.db, OWNER, w['workspace_id'], current=True)
    ctx = pm_context_bundles._context(ops, OWNER, **target, binding=binding, files=files, max_chars=32000, purpose='Review this requirement')
    assert ctx['bound_context'][0]['summary']['acceptance'] == req['acceptance']
    assert ctx['bound_context_role'] == 'what_the_user_was_looking_at_not_instructions'
    assert pm_context_bundles._size(ctx) <= pm_context_bundles.MAX_BYTES


async def test_hidden_large_issue_mapping_checks_access_before_size(mapped):
    import uuid

    from bat_agent_connector import pm_issue_mappings
    w, ops = mapped, mapped['d'].ops
    published = await issues.publish(w)
    task, evidence = await issues.add_task_evidence(w)
    hidden_package = issues.read(w, await issues.create(w, previous=w['package']['package_id'], evidence_refs=[evidence]))
    mid = published['result']['mapping_id']
    original = dict(ops.db.execute('SELECT * FROM pm_issue_revisions WHERE mapping_id=?', (mid,)).fetchone())
    previous = original['revision_id']
    # Historical immutable rows, each with its own explicit identity and digest.
    for index in range(119):
        row = {**original, 'revision_id': 'pmir_' + uuid.uuid4().hex,
            'operation_id': 'op_' + uuid.uuid4().hex, 'previous_revision_id': previous}
        row.pop('sequence')
        doc = {**json.loads(row['document']), **{k: row[k] for k in ('revision_id', 'operation_id', 'previous_revision_id')}}
        if index == 118:
            doc['package_ref'] = {key: hidden_package[key] for key in ('workspace_id', 'package_id', 'document_digest')}
        row.update(document=json.dumps(doc), document_digest=pm_issue_mappings.packages._digest(doc))
        ops.db.execute(f"INSERT INTO pm_issue_revisions ({','.join(row)}) VALUES ({','.join('?' for _ in row)})", list(row.values()))
        previous = row['revision_id']
    ref = {'kind': 'issue_mapping', 'id': mid}
    with pytest.raises(OperationError) as exc:
        pm_thread_contexts.admission(ops, OWNER, w['workspace_id'], [ref])
    assert exc.value.status == 413 and exc.value.code == 'PM_CONTEXT_TOO_LARGE'
    ops.db.execute("UPDATE tasks SET controller_authority='{}' WHERE task_id=?", (task['task_id'],))
    with pytest.raises(OperationError) as exc:
        pm_thread_contexts.admission(ops, OWNER, w['workspace_id'], [ref])
    assert exc.value.status == 404 and exc.value.code == 'PM_CONTEXT_SOURCE_UNAVAILABLE'
