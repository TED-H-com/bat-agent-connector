"""Prepare lineage readers against the daemon's complete production schema."""
from __future__ import annotations

import ast
import importlib
import inspect
import sqlite3

import pytest

from bat_agent_connector import pm_access, pm_delivery_lineage, pm_source_lineage
from tests.test_api_v1 import daemon  # noqa: F401
from tests.test_work_items import VIEWER


@pytest.mark.parametrize("kind", sorted(pm_source_lineage._RESOURCE_KINDS | {"github_pr", "git_revision"}))
def test_every_resource_kind_prepares_on_absent_identity(daemon, kind):  # noqa: F811
    db = daemon.ops.db
    # Assert optional branch tables exist: an absent table must not turn this
    # into a passing test that never prepares the reader's SQL.
    for table in ("pm_source_lineage", "operations", "integration_previews", "pr_merge_previews",
                  "deployments", "artifact_revisions", "session_worktree_bindings", "checkpoints", "tasks"):
        assert pm_source_lineage._table(db, table), table
    assert pm_source_lineage.roots(db, kind, "absent") == set()
    if kind in {"integration_preview", "delivery_preview"}:
        assert pm_source_lineage.roots(db, kind, "ipv_absent") == set()


def test_access_and_delivery_readers_on_absent_identities(daemon):  # noqa: F811
    ops = daemon.ops
    assert pm_delivery_lineage.known_pr_roots(ops, {"repository": "example/absent", "pull_number": 1}) == set()
    assert pm_delivery_lineage.admitted_roots(ops.db, "op_absent") == set()
    assert pm_access.operation_source_visible(ops.db, VIEWER, "op_absent")
    for kind in ("operation", "pm_snapshot"):
        assert pm_access.event_visible(ops.db, VIEWER, {"resource_type": kind, "resource_id": "absent",
            "kind": "fixture.saved", "body": {"snapshot_id": "absent"}}) == (kind == "operation")


# Include fixed SELECTs behind row-dependent branches as well as the direct
# journal readers found by tracing source/lineage imports. EXPLAIN compiles the
# real statement without executing it, needing neither fake rows nor providers.
READERS = (
    "pm_source_lineage", "pm_delivery_lineage", "pm_access", "pm_read_cache", "pm_source_reads",
    "execution_sources", "pm_quality_sources", "pm_review_candidate_sources", "pm_operation_loop",
    "pm_execution_runtime", "pm_maintenance_repair_runtime", "pm_register", "pm_schedule",
    "artifact_managed", "work_item_results", "pm_thread_contexts", "cleanup",
)


@pytest.mark.parametrize("module_name", READERS)
def test_fixed_lineage_selects_prepare_on_installed_schema(daemon, module_name):  # noqa: F811
    module = importlib.import_module("bat_agent_connector." + module_name)
    statements = []
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "execute" and node.args and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str) and node.args[0].value.lstrip().upper().startswith("SELECT")):
            statements.append((node.lineno, node.args[0].value))
    assert statements, module_name
    for line, statement in statements:
        try:
            daemon.ops.db.execute("EXPLAIN " + statement, (None,) * statement.count("?")).fetchall()
        except sqlite3.Error as error:
            pytest.fail(f"{module_name}:{line}: {statement}: {error}")
