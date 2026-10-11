"""The Windows central validates paths without importing the POSIX host script."""
import subprocess
import sys


def test_central_revision_validation_keeps_host_only_imports_out():
    result = subprocess.run([sys.executable, '-c', '''
import sys
from bat_agent_connector import pm_revision_git, pm_revision_paths
assert 'bat_agent_connector.pm_revision_host' not in sys.modules
assert 'bat_agent_connector.repository_sync_host' not in sys.modules
assert 'bat_agent_connector.pm_worktree_host' not in sys.modules
assert pm_revision_git.pm_revision_paths is pm_revision_paths
'''], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
