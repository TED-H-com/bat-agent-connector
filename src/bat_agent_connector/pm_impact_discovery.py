"""Indexed private locators for retained impact evidence; never source authority."""

PREDICATE = """(kind IN ('pm.work_package.created','work.start.succeeded','pm.issue.recorded')
    OR (kind='operation.accepted' AND resource_type='operation'
        AND json_extract(body,'$.action') IN ('pm.work_package.create','work.start','pm.issue.publish','pm.issue.cancel')))"""
WORKSPACE = """CASE WHEN kind='operation.accepted' THEN json_extract(body,'$.target.workspace_id')
    ELSE json_extract(body,'$.workspace_id') END"""
RELATION = """CASE WHEN kind='operation.accepted' THEN COALESCE(json_extract(body,'$.target.work_item_id'),
    json_extract(body,'$.target.mapping_id')) ELSE json_extract(body,'$.work_item_id') END"""
INDEXES = {
    'pm_impact_event_relation': f"""CREATE INDEX IF NOT EXISTS pm_impact_event_relation
        ON api_events(({WORKSPACE}),({RELATION}),seq) WHERE {PREDICATE}""",
    'pm_impact_issue_work': """CREATE INDEX IF NOT EXISTS pm_impact_issue_work
        ON pm_issue_revisions(workspace_id,work_item_id,mapping_id)""",
}
EVENTS = f"""SELECT * FROM api_events /* pm_impact:events */ WHERE {PREDICATE}
    AND ({WORKSPACE})=? AND ({RELATION})=? AND seq<=? ORDER BY seq"""  # noqa: S608 -- fixed schema expressions, bound selectors
MAPPINGS = """SELECT DISTINCT mapping_id FROM pm_issue_revisions /* pm_impact:mappings */
    WHERE workspace_id=? AND work_item_id=?"""
PACKAGES = """SELECT package_id FROM pm_work_packages /* pm_impact:packages */
    WHERE workspace_id=? AND work_item_id=?"""
HANDOFFS = """SELECT operation_id FROM operations /* pm_impact:handoffs */ WHERE
    json_extract(external_refs,'$.admission_binding.pm_handoff.contract.package_ref.workspace_id')=?
    AND json_extract(external_refs,'$.admission_binding.pm_handoff.contract.package_ref.package_id')=?
    AND json_extract(external_refs,'$.admission_binding.pm_handoff.contract.package_ref.package_id') IS NOT NULL"""
ADMISSIONS = """SELECT * FROM api_events /* pm_impact:admissions */ WHERE resource_type='operation'
    AND resource_id=? AND seq<=? AND kind='operation.accepted' ORDER BY seq"""


def schema(journal):
    # Called only by the existing issue/package startup schema path.
    for statement in INDEXES.values():
        journal.db.execute(statement)


def rows(db, workspace_id, work_ids, cursor):
    for work_id in work_ids:
        yield from db.execute(EVENTS, (workspace_id, work_id, cursor))
        # Cancellation targets a mapping, not a Work. Immutable mapping identities
        # locate its admission; only the cursor-ranged event proves the boundary.
        for mapping in db.execute(MAPPINGS, (workspace_id, work_id)):
            yield from db.execute(EVENTS, (workspace_id, mapping[0], cursor))
        # Handoff admissions contain no PM identifiers. Reuse the package-keyed
        # original-envelope index, then the resource + cursor event range.
        for package in db.execute(PACKAGES, (workspace_id, work_id)):
            for handoff in db.execute(HANDOFFS, (workspace_id, package[0])):
                yield from db.execute(ADMISSIONS, (handoff[0], cursor))
