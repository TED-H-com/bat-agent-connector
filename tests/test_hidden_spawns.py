"""Policy regressions use synthetic source; never launch live providers."""
import importlib.util
from pathlib import Path

import pytest

from bat_agent_connector import hidden_process

SPEC = importlib.util.spec_from_file_location('hidden_guard', Path(__file__).parents[1] / 'scripts/check-hidden-spawns.py')
GUARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GUARD)


@pytest.mark.parametrize(('path', 'source'), [
    ('main.rs', 'let child = std::process::Command::new("git").spawn();'),
    ('main.rs', 'command.creation_flags(0);'),
    ('main.ts', 'import {spawn as launch} from "node:child_process";'),
    ('main.js', 'const cp = require("child_process");'),
    ('main.ps1', 'Start-Process -FilePath node.exe'),
    ('main.ps1', '$s = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew'),
    ('task.xml', '<Task><Settings/></Task>'),
    ('task.xml', '<Task><Settings><Hidden>true</Hidden></Settings><Command>node.exe</Command></Task>'),
    ('main.py', 'subprocess.run(["git"])'),
])
def test_detects_bypass(path, source):
    assert list(GUARD.violations(path, source))


@pytest.mark.parametrize(('path', 'source'), [
    ('main.rs', 'hidden_command("git").spawn();'),
    ('main.rs', 'command.creation_flags(hidden_flags(existing));'),
    ('main.ts', 'import {spawn} from "./hidden-child-process.mjs";'),
    ('main.ps1', 'Start-Process -WindowStyle Hidden -FilePath node.exe'),
    ('task.xml', '<Task><Settings><Hidden>true</Hidden></Settings></Task>'),
])
def test_accepts_hidden_launch(path, source):
    assert not list(GUARD.violations(path, source))


def test_allowlist_requires_path_pattern_and_justification(tmp_path):
    (tmp_path / 'main.rs').write_text('Command::new("git");')
    entry = {'path': 'main.rs', 'pattern': r'Command::new', 'justification': 'reviewed helper'}
    assert not GUARD.scan(tmp_path, ['main.rs'], [entry])
    for key, value in [('path', 'other.rs'), ('pattern', 'different'), ('justification', '')]:
        assert GUARD.scan(tmp_path, ['main.rs'], [{**entry, key: value}])


def test_python_preserves_flags_and_linux_options(monkeypatch):
    options = {'creationflags': 0x01000400, 'stdin': -1}
    monkeypatch.setattr(hidden_process.os, 'name', 'nt')
    assert hidden_process.hidden_options(options) == {**options, 'creationflags': 0x09000400}
    assert options['creationflags'] == 0x01000400
    monkeypatch.setattr(hidden_process.os, 'name', 'posix')
    assert hidden_process.hidden_options(options) == options
    assert hidden_process.hidden_options({}) == {}
