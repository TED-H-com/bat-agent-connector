"""The deepest producer is the original maintenance repair required-CI matrix."""
from bat_agent_connector import pm_maintenance_review_sources as maintenance
from bat_agent_connector import pm_roadmap_report_schema as schema
from tests import test_pm_repair_required_ci_binding as shared

world, fixed, pm, ready = shared.world, shared.fixed, shared.pm, shared.ready
repair_ready, quality_repair = shared.repair_ready, shared.quality_repair
original_publication_authority = shared.original_publication_authority
publishing, published = shared.publishing, shared.published
configured_required_ci = shared.configured_required_ci


def deepest(value):
    pending, result = [(value, ())], ()
    while pending:
        value, path = pending.pop()
        if isinstance(value, (dict, list)):
            if len(path) >= len(result):
                result = path
            pending.extend((child, (*path, key)) for key, child in
                (value.items() if isinstance(value, dict) else enumerate(value)))
    return len(result) + 1, result


async def test_actual_repair_required_ci_preserves_depth_margin(published):
    w, ops = published, published['d'].ops
    await shared.select(w, w['ci_ref'])
    repair = shared.view(w, w['parent'])
    intent = repair['context']['check']['workload']
    period = {'start': repair['created_at'] - 1, 'end': repair['created_at'] + 1}
    report, refs = maintenance.build(ops, shared.OWNER, w['workspace_id'], intent, period, period['end'])
    # The existing snapshot carrier embeds this report at maintenance.report.
    snapshot = {'maintenance': {'report': report, 'access_refs': refs}}
    depth, path = deepest(snapshot)
    print('producer container depth', depth, path, 'margin', schema.MAX_CONTAINER_DEPTH - depth)
    assert depth <= schema.MAX_CONTAINER_DEPTH // 4
    proof = report['repairs'][0]['progress']['required_ci_receipts'][0]['proof']
    assert isinstance(proof['required'][0]['check_ids'], list)
    assert len(('maintenance', 'report', 'repairs', 0, 'progress', 'required_ci_receipts', 0,
                'proof', 'required', 0, 'check_ids')) + 1 <= depth
    schema.require_depth(snapshot)
