; Both manual installs and the desktop updater run the same NSIS postinstall hook.
!define BAT_TASK_REPAIR_SCRIPT "${__FILEDIR__}\repair-fleet-monitor-task.ps1"
!macro NSIS_HOOK_POSTINSTALL
  InitPluginsDir
  File /oname=$PLUGINSDIR\repair-fleet-monitor-task.ps1 "${BAT_TASK_REPAIR_SCRIPT}"
  nsExec::ExecToStack '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "$PLUGINSDIR\repair-fleet-monitor-task.ps1" -ExePath "$INSTDIR\better-agent-dashboard.exe"'
  Pop $0
  Pop $1
  ${If} $0 != 0
    DetailPrint "Fleet monitor task update failed: $1"
    SetErrorLevel 1
    Abort
  ${EndIf}
!macroend
