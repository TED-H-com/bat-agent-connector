# Synthetic native transport fixture. It cannot inspect configuration or change hosts.
$ErrorActionPreference='Stop'
# Match the facade's byte-oriented framing; Console.In is PowerShell-host controlled.
# This marker contains only fixed fixture phases, never request/configuration data.
$phasePath=[IO.Path]::Combine($PSScriptRoot,'phase.txt')
[IO.File]::WriteAllText($phasePath,'started')
$inputStream=[Console]::OpenStandardInput()
$body=New-Object IO.MemoryStream
$buffer=New-Object byte[] 4096
try {
  while (($count=$inputStream.Read($buffer,0,$buffer.Length)) -gt 0) {
    if ($body.Length+$count -gt 65536) { exit 1 }
    $body.Write($buffer,0,$count)
    [IO.File]::WriteAllText($phasePath,'input-received')
  }
  [IO.File]::WriteAllText($phasePath,'input-closed')
  $request=(New-Object Text.UTF8Encoding($false,$true)).GetString($body.ToArray()) | ConvertFrom-Json
  [IO.File]::WriteAllText($phasePath,'request-decoded')
} finally { $body.Dispose() }
if ($request.action -ne 'contract' -or $request.schema_version -ne 1) { exit 1 }
$reply=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'contract.json') -Raw | ConvertFrom-Json
[IO.File]::WriteAllText($phasePath,'reply-loaded')
$reply.request_id=$request.request_id
$bytes=[Text.Encoding]::UTF8.GetBytes(($reply | ConvertTo-Json -Depth 8 -Compress))
[IO.File]::WriteAllText($phasePath,'reply-encoded')
$stdout=[Console]::OpenStandardOutput()
$stdout.Write($bytes,0,$bytes.Length)
$stdout.Flush()
[IO.File]::WriteAllText($phasePath,'output-written')
exit 0
