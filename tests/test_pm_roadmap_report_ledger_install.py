"""Daemon upgrades install the report ledger and action without changing stored facts."""
from bat_agent_connector import pm_plans
from bat_agent_connector import pm_roadmap_report_materialization as reports
from bat_agent_connector.task_daemon import TaskDaemon
from tests.conftest import make_config
from tests.test_pm_plan_operations import OWNER


async def test_daemon_upgrades_pre_report_journal_twice_without_rewriting_facts(mock, tmp_path, monkeypatch):
    path = tmp_path / 'tasks.db'
    config = make_config(mock, writes=True, orchestrate=True, managed_roots=['/srv'])
    # Reproduce the prior daemon's complete schema: its API never called this
    # schema installer. Do not depend on Git history in shallow CI checkouts.
    with monkeypatch.context() as patch:
        patch.setattr(reports, 'install', lambda ops: None)
        old = TaskDaemon(config, path)
        db = old.ops.db  # The daemon initializes lazily, while the old installer is active.
    try:
        assert not db.execute('SELECT 1 FROM sqlite_master WHERE name=?', (reports.TABLE,)).fetchone()
        task = old.journal.submit(project='p', host='h1', workspace='w', original_words='Synthetic upgrade sentinel',
                                  idempotency_key='report-schema-upgrade')
        saved = dict(db.execute('SELECT * FROM tasks WHERE task_id=?', (task['task_id'],)).fetchone())
        version = db.execute('PRAGMA user_version').fetchone()[0]
        schema = dict(db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
    finally:
        await old.fleet.close()
        await old.inventory.close()
        old.journal.close()

    for _ in range(2):
        daemon = TaskDaemon(config, path)
        try:
            db = daemon.ops.db
            assert db.execute('PRAGMA user_version').fetchone()[0] == version
            for name, ddl in schema.items():
                assert db.execute('SELECT sql FROM sqlite_master WHERE name=?', (name,)).fetchone()[0] == ddl
            assert dict(db.execute('SELECT * FROM tasks WHERE task_id=?', (task['task_id'],)).fetchone()) == saved
            assert db.execute('SELECT COUNT(*) FROM pm_roadmap_report_materializations').fetchone()[0] == 0
            objects = dict(db.execute("SELECT name,type FROM sqlite_master WHERE name LIKE 'pm_roadmap_report_materializations%'"))
            assert objects == {
                reports.TABLE: 'table',
                reports.TABLE + '_workspace_sequence': 'index',
                reports.TABLE + '_update_immutable': 'trigger',
                reports.TABLE + '_delete_immutable': 'trigger',
            }
            assert reports.ACTION in daemon.ops.actions
            status, capabilities = await daemon.api.capabilities(OWNER)
            assert status == 200
            features = capabilities['features']['pm_workspaces']
            assert features['roadmap_report']['materialization'] is True
            assert features['roadmap_report_materialization'] == reports.capabilities()
            assert pm_plans.latest_git_receipt(db, 'ws_' + '0' * 32) is None
        finally:
            await daemon.fleet.close()
            await daemon.inventory.close()
            daemon.journal.close()
