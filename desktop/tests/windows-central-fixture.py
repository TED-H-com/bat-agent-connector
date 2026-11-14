"""Windows equivalent of pytest-disktmp, with an explicit disk basetemp and cleanup."""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path.home() / "agent-work" / "tmp" / "pytest"
root.mkdir(parents=True, exist_ok=True)
temporary = Path(tempfile.mkdtemp(prefix="windows-central-", dir=root))
try:
    result = subprocess.run(  # noqa: S603 - fixed repository fixture selectors
        [sys.executable, "-m", "pytest", "-q", "--basetemp", str(temporary),
         "tests/test_platform_files.py", "tests/test_windows_central.py",
         "tests/test_windows_monitor_task.py", "tests/test_e2e_windows_named_pipe.py"],
        cwd=Path(__file__).resolve().parents[2],
    )
    raise SystemExit(result.returncode)
finally:
    def remove_sealed_fixture(function, path, exception):
        owned = Path(path)
        if not isinstance(exception, PermissionError) or not owned.is_relative_to(temporary) or owned.is_symlink() or not owned.is_file():
            raise exception
        from bat_agent_connector import platform_files
        # Use the same verified-handle deletion exercised by the cleanup fixture;
        # Win32 path deletion can still refuse sealed artifacts after chmod.
        parent = platform_files.native.open_dir(owned.parent)
        try:
            platform_files.native._remove_child(parent.handle, owned.parent, owned.name, directory=False)
        finally:
            parent.close()
    shutil.rmtree(temporary, onexc=remove_sealed_fixture)
