"""Prepare fixed SQL reads and writes against the complete daemon schema."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import bat_agent_connector
from bat_agent_connector import pm_access, pm_delivery_lineage, pm_source_lineage
from tests.fixed_sql import discover
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


def fixed_sql(root=None):
    # Read source without importing every module: discovery must not launch a
    # CLI or require an optional runtime. Include nested packages and __init__.
    root = root or Path(bat_agent_connector.__file__).parent
    result, dynamic = {}, {}
    for path in sorted(root.rglob("*.py")):
        statements, unresolved = discover(path.read_text(encoding="utf-8"))
        module = ".".join(path.relative_to(root).with_suffix("").parts)
        if statements:
            result[module] = statements
        if unresolved:
            dynamic[module] = unresolved
    return result, dynamic


def test_discovery_includes_writes_and_named_constants(tmp_path):
    source = '''
MODULE_SQL = ("SELECT " "1")
def reader(db):
    local_sql: str = ("UPDATE missing SET value=?")
    db.execute(MODULE_SQL)
    db.execute(local_sql, (None,))
    db.executemany("INSERT INTO missing VALUES(?)", [])
    db.execute("REPLACE INTO missing VALUES(?)", (None,))
    db.execute("DELETE FROM missing")
    db.execute("WITH value AS (SELECT 1) SELECT * FROM value")
'''
    (tmp_path / "reader.py").write_text(source)
    inventory, dynamic = fixed_sql(tmp_path)
    statements = {row.sql for row in inventory["reader"]}
    assert statements == {
        "SELECT 1", "UPDATE missing SET value=?", "INSERT INTO missing VALUES(?)",
        "REPLACE INTO missing VALUES(?)", "DELETE FROM missing", "WITH value AS (SELECT 1) SELECT * FROM value",
    }
    assert [row.origin for row in inventory["reader"]] == ["named", "named", "inline", "inline", "inline", "inline"]
    assert inventory["reader"][2].method == "executemany"
    assert not dynamic


def test_discovery_respects_scopes_and_counts_dynamic_calls():
    source = '''
QUERY = "SELECT 1"
def reader(db):
    query = "SELECT 2"
    db.execute(QUERY)
    db.execute(query)
    def nested():
        db.execute(query)
    def shadowed(query):
        db.execute(query)
    db.execute(f"SELECT {1}")
    db.execute("SELECT %s" % 1)
    db.execute("SELECT {}".format(1))
    db.execute("SELECT " + "1")
    query += " LIMIT 1"
def other(db):
    query = ("DELETE " "FROM missing")
    db.execute(query)
    db.execute("CREATE TABLE missing(value)")
    db.executescript("DELETE FROM missing;")
    db.executemany(f"INSERT INTO {table} VALUES(?)", [])
'''
    statements, dynamic = discover(source)
    assert [(row.sql, row.origin) for row in statements] == [("SELECT 1", "named"), ("DELETE FROM missing", "named")]
    assert len(dynamic) == 8
    assert dynamic[-1][1] == "executemany"


def test_discovery_rejects_global_and_nonlocal_reassignment():
    source = '''
QUERY = "SELECT 1"
def change():
    global QUERY
    QUERY = "SELECT 2"
def read(db):
    db.execute(QUERY)
    query = "SELECT 3"
    def nested():
        nonlocal query
        query = "SELECT 4"
    db.execute(query)
'''
    statements, dynamic = discover(source)
    assert not statements
    assert len(dynamic) == 2


def test_discovery_handles_declarations_and_sql_keyword_boundaries():
    source = '''
MODULE_SQL: str
MODULE_SQL = "SELECT 1"
def reader(db):
    local_sql: str
    local_sql = "UPDATE missing SET value=?"
    db.execute(MODULE_SQL)
    db.execute(local_sql)
    db.execute("/* fixed SQL */ SELECT(1)")
    db.execute("-- fixed SQL\\nWITH value AS (SELECT 1) SELECT * FROM value")
'''
    statements, dynamic = discover(source)
    assert [(row.kind, row.origin) for row in statements] == [
        ("SELECT", "named"), ("UPDATE", "named"), ("SELECT", "inline"), ("WITH", "inline"),
    ]
    assert not dynamic


FIXED_SQL, DYNAMIC_CALLS = fixed_sql()
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
        "DELETE FROM browser_handoffs WHERE expires_at<=?",
        "INSERT INTO browser_handoffs VALUES(?,?,?,?,?,?)",
        "DELETE FROM browser_handoffs WHERE digest=?",
    )
}


def test_fixed_sql_exclusions_match_real_statements():
    assert FIXED_SQL
    for (module_name, match), (_, reason) in EXCLUSIONS.items():
        assert reason.strip()
        assert sum(match in row.sql for row in FIXED_SQL.get(module_name, ())) == 1, (module_name, match)


@pytest.mark.parametrize("module_name", sorted(FIXED_SQL))
def test_fixed_sql_prepares_on_installed_schema(daemon, module_name):  # noqa: F811
    failures = []
    for row in FIXED_SQL[module_name]:
        line, statement = row.line, row.sql
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


def test_lazy_browser_sql_prepares_after_the_real_constructor(daemon, tmp_path):  # noqa: F811
    from bat_agent_connector.browser_sessions import BrowserSessions

    BrowserSessions(daemon, tmp_path, "fixture-installation")
    for module_name, statement in EXCLUSIONS:
        assert module_name == "browser_sessions"
        daemon.ops.db.execute("EXPLAIN " + statement, (None,) * statement.count("?")).fetchall()
