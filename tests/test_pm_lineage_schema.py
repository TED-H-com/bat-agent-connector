"""Prepare every module's fixed SELECTs against the complete daemon schema."""
from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import pytest

import bat_agent_connector
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


def fixed_selects():
    # Read source without importing every module: discovery must not launch a
    # CLI or require an optional runtime. Include nested packages and __init__.
    root = Path(bat_agent_connector.__file__).parent
    result = {}
    for path in sorted(root.rglob("*.py")):
        statements = []
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "execute" and node.args and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str) and node.args[0].value.lstrip().upper().startswith("SELECT")):
                statements.append((node.lineno, node.args[0].value))
        if statements:
            result[".".join(path.relative_to(root).with_suffix("").parts)] = sorted(statements)
    return result


FIXED_SELECTS = fixed_selects()
# (module, unique SQL substring): (expected prepare error, reason).
# Only legitimate non-daemon schemas belong here; stale matches must fail.
EXCLUSIONS = {
    ("browser_sessions", statement): (
        "no such table: browser_handoffs",
        "BrowserSessions.__init__ creates browser_handoffs lazily when the managed browser adapter starts.",
    )
    for statement in (
        "SELECT filename FROM browser_handoffs WHERE expires_at<=?",
        "SELECT COUNT(*) FROM browser_handoffs",
        "SELECT * FROM browser_handoffs WHERE digest=?",
    )
}


def test_fixed_select_exclusions_match_real_statements():
    assert FIXED_SELECTS
    for (module_name, match), (_, reason) in EXCLUSIONS.items():
        assert reason.strip()
        assert sum(match in statement for _, statement in FIXED_SELECTS.get(module_name, ())) == 1, (module_name, match)


@pytest.mark.parametrize("module_name", sorted(FIXED_SELECTS))
def test_fixed_selects_prepare_on_installed_schema(daemon, module_name):  # noqa: F811
    failures = []
    for line, statement in FIXED_SELECTS[module_name]:
        exceptions = [value for (module, match), value in EXCLUSIONS.items()
                      if module == module_name and match in statement]
        if len(exceptions) > 1:
            failures.append(f"{module_name}:{line}: overlapping exclusions: {statement}")
        try:
            daemon.ops.db.execute("EXPLAIN " + statement, (None,) * statement.count("?")).fetchall()
        except sqlite3.Error as error:
            if not (len(exceptions) == 1 and isinstance(error, sqlite3.OperationalError)
                    and str(error) == exceptions[0][0]):
                failures.append(f"{module_name}:{line}: {statement}: {error}")
        else:
            if exceptions:
                failures.append(f"{module_name}:{line}: stale exclusion; statement now compiles: {statement}")
    assert not failures, "\n".join(failures)


def test_lazy_browser_selects_prepare_after_the_real_constructor(daemon, tmp_path):  # noqa: F811
    from bat_agent_connector.browser_sessions import BrowserSessions

    BrowserSessions(daemon, tmp_path, "fixture-installation")
    for module_name, statement in EXCLUSIONS:
        assert module_name == "browser_sessions"
        daemon.ops.db.execute("EXPLAIN " + statement, (None,) * statement.count("?")).fetchall()
