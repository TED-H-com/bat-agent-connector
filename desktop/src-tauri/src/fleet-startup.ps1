# Native-owned bootstrap for the read-only contract handshake, in the adapter process.
# The path is trusted configuration passed as data; no request has been sent yet.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$env:PSModulePath = $PSHOME + '\Modules'
Import-Module -Name ($PSHOME + '\Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop
Import-Module -Name ($PSHOME + '\Modules\Microsoft.PowerShell.Management\Microsoft.PowerShell.Management.psd1') -ErrorAction Stop
# Exercise first-use JSON and file cmdlets before announcing readiness. Outputs stay
# private; stdout contains only the fixed marker followed by the facade's JSON reply.
$null = '{"ready":true}' | ConvertFrom-Json | ConvertTo-Json -Compress
$null = Get-Content -LiteralPath $env:BATC_FLEET_ADAPTER -TotalCount 1
$ready = [Text.Encoding]::ASCII.GetBytes("BATC_FLEET_READY_V1`n")
$stdout = [Console]::OpenStandardOutput()
$stdout.Write($ready, 0, $ready.Length)
$stdout.Flush()
& $env:BATC_FLEET_ADAPTER
exit $LASTEXITCODE
