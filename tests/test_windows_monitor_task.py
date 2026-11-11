"""Task definitions and upgrade repair; never register a real scheduled task."""
import json
import os
from pathlib import Path

import pytest

from bat_agent_connector import hidden_process

ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / 'desktop/scripts/windows'


def test_upgrade_hook_is_packaged_for_manual_and_updater_installs():
    config = json.loads((ROOT / 'desktop/src-tauri/tauri.conf.json').read_text())
    hook = ROOT / 'desktop/src-tauri' / config['bundle']['windows']['nsis']['installerHooks']
    source = hook.read_text()
    assert '!macro NSIS_HOOK_POSTINSTALL' in source
    assert 'repair-fleet-monitor-task.ps1' in source
    assert '-WindowStyle Hidden' in source
    assert 'nsExec::ExecToStack' in source


def powershell(script, *arguments):
    executable = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    env = os.environ.copy()
    for index, value in enumerate(arguments):
        env[f'BATC_TASK_FIXTURE_{index}'] = str(value)
        script += f' $env:BATC_TASK_FIXTURE_{index}'
    return hidden_process.run(
        [str(executable), '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden',
         '-Command', script],
        check=True, capture_output=True, text=True, timeout=60, env=env,
    ).stdout


@pytest.mark.skipif(os.name != 'nt', reason='Windows Task Scheduler definition cmdlets')
def test_generated_task_definition_contains_hidden(tmp_path):
    exe = tmp_path / 'better-agent-dashboard.exe'
    config = tmp_path / 'fleet.json'
    exe.touch()
    config.write_text('{}')
    # DefinitionOnly uses the real Task Scheduler definition cmdlets without registration.
    script = "& { param($installer,$exe,$config) $d = & $installer -ExePath $exe -ConfigPath $config -DefinitionOnly; $d.Settings | Select-Object Hidden | ConvertTo-Json }"
    definition = json.loads(powershell(script, SCRIPTS / 'install-fleet-monitor-task.ps1', exe, config))
    assert definition['Hidden'] is True


@pytest.mark.skipif(os.name != 'nt', reason='Windows PowerShell upgrade fixture')
def test_upgrade_repairs_existing_task_without_changing_other_settings(tmp_path):
    script = r"""& { param($repair)
$ErrorActionPreference = 'Stop'
$sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$script:task = [pscustomobject]@{
    Principal = [pscustomobject]@{ UserId = $sid; LogonType = 'Interactive' }
    Actions = @([pscustomobject]@{ Execute = 'C:\Example\better-agent-dashboard.exe'; Arguments = '--fleet-supervisor --fleet-config "C:\Example\fleet.json"' })
    Settings = [pscustomobject]@{ Hidden = $false; RestartCount = 999 }
}
$script:writes = 0
function Get-ScheduledTask { param($TaskPath,$TaskName,$ErrorAction) return $script:task }
function Set-ScheduledTask { param($InputObject) $script:writes++; if (!$InputObject.Settings.Hidden) { throw 'not hidden' } }
$before = $task | ConvertTo-Json -Depth 5
& $repair -ExePath 'C:\Example\better-agent-dashboard.exe'
if ($writes -ne 1 -or !$task.Settings.Hidden) { throw 'upgrade did not repair' }
$task.Settings.Hidden = $false
if (($task | ConvertTo-Json -Depth 5) -ne $before) { throw 'unrelated settings changed' }
$task.Settings.Hidden = $true
& $repair -ExePath 'C:\Example\better-agent-dashboard.exe'
& $repair -ExePath 'C:\Other\better-agent-dashboard.exe'
$task.Principal.UserId = 'S-1-5-18'
& $repair -ExePath 'C:\Example\better-agent-dashboard.exe'
$script:task = $null
& $repair -ExePath 'C:\Example\better-agent-dashboard.exe'
if ($writes -ne 1) { throw 'unexpected task write' }
} """
    powershell(script, SCRIPTS / 'repair-fleet-monitor-task.ps1')
