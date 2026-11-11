# Read-only oracle copied from reviewed Fleet Kit client/fleet-desktop-core.ps1.
# Only synthetic fixture paths are supplied through this child process environment.
$ErrorActionPreference='Stop'
function Get-FleetDesktopBinding([string]$inventoryPath,[string]$profileIndexPath,[string]$kitRoot=$PSScriptRoot) {
  [Console]::Error.Write("BATC_PHASE hash-create-before`n");[Console]::Error.Flush()
  $hash=[Security.Cryptography.SHA256]::Create()
  [Console]::Error.Write("BATC_PHASE hash-create-after`n");[Console]::Error.Flush()
  try {
    $text="desktop-configuration-v1`n"
    foreach ($path in @($inventoryPath,$profileIndexPath,(Join-Path $kitRoot 'ssh-config'),(Join-Path (Join-Path $env:USERPROFILE '.ssh') 'config'))) {
      $full=[IO.Path]::GetFullPath($path)
      $text+=[string]$full.Length+':'+$full+':'
      if ([IO.File]::Exists($path)) {
        [Console]::Error.Write("BATC_PHASE file-read-before`n");[Console]::Error.Flush()
        $fixtureBytes=[IO.File]::ReadAllBytes($path)
        [Console]::Error.Write("BATC_PHASE file-read-after`n");[Console]::Error.Flush()
        [Console]::Error.Write("BATC_PHASE file-hash-before`n");[Console]::Error.Flush()
        $digest=([BitConverter]::ToString($hash.ComputeHash($fixtureBytes))).Replace('-','').ToLowerInvariant()
        [Console]::Error.Write("BATC_PHASE file-hash-after`n");[Console]::Error.Flush()
        $text+='present:'+ $digest+"`n"
      } else { $text+="absent`n" }
    }
    $script:fixtureBindingPreimage=$text
    [Console]::Error.Write("BATC_PHASE binding-hash-before`n");[Console]::Error.Flush()
    $fixtureDigest=([BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($text)))).Replace('-','').ToLowerInvariant()
    [Console]::Error.Write("BATC_PHASE binding-hash-after`n");[Console]::Error.Flush()
    return $fixtureDigest
  } finally { $hash.Dispose() }
}

[Console]::Error.Write("BATC_PHASE first-cmdlet-Join-Path-before`n");[Console]::Error.Flush()
$kitRoot=Join-Path $env:BAT_FLEET_BINDING_FIXTURE 'kit'
[Console]::Error.Write("BATC_PHASE first-cmdlet-Join-Path-after`n");[Console]::Error.Flush()
$env:USERPROFILE=Join-Path $env:BAT_FLEET_BINDING_FIXTURE ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('5L2/55So6ICF8J+mgA==')))
[Console]::Error.Write("BATC_PHASE binding-before`n");[Console]::Error.Flush()
$binding=Get-FleetDesktopBinding (Join-Path $kitRoot 'fleet-inventory.json') (Join-Path $kitRoot 'bat-profiles/index.json') $kitRoot
[Console]::Error.Write("BATC_PHASE binding-after`n");[Console]::Error.Flush()
# Base64 keeps Windows PowerShell's console code page out of the Unicode oracle.
[Console]::Error.Write("BATC_PHASE json-before`n");[Console]::Error.Flush()
$fixtureJson=@{binding=$binding;preimage_base64=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($script:fixtureBindingPreimage))} | ConvertTo-Json -Compress
[Console]::Error.Write("BATC_PHASE json-after`n");[Console]::Error.Flush()
[Console]::Error.Write("BATC_PHASE output-before`n");[Console]::Error.Flush()
$fixtureJson
[Console]::Error.Write("BATC_PHASE output-after`n");[Console]::Error.Flush()
[Console]::Error.Write("BATC_PHASE script-end`n");[Console]::Error.Flush()
