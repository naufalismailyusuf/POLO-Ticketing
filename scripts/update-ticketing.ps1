# POLO Ticketing: update static files safely, without restarting services.
$ErrorActionPreference = 'Stop'
$root = 'C:\Ticketing'
$git = @("$env:ProgramFiles\Git\cmd\git.exe", "$env:LOCALAPPDATA\Programs\Git\cmd\git.exe") |
  Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $git) { throw 'Git not found.' }
Set-Location $root
if (@(& $git status --porcelain).Count -gt 0) { throw 'STOP: Local changes present.' }
& $git fetch origin main
if ($LASTEXITCODE -ne 0) { throw 'STOP: Fetch failed.' }
& $git merge-base --is-ancestor HEAD origin/main
if ($LASTEXITCODE -ne 0) { throw 'STOP: Local branch diverged.' }
$changed = @(& $git diff --name-only HEAD origin/main)
if ($LASTEXITCODE -ne 0) { throw 'STOP: Diff failed.' }
if ($changed.Count -eq 0) { Write-Host 'Already up to date.'; exit 0 }
$unsafe = @($changed | Where-Object { $_ -notmatch '^(static|templates)/.+\.(css|js|html|svg)$' })
if ($unsafe.Count -gt 0) {
  $unsafe | ForEach-Object { Write-Host "Manual deployment required: $_" }
  throw 'STOP: Only static file changes can be deployed automatically.'
}
$backup = "C:\Ticketing-Backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
foreach ($file in $changed) {
  $from = Join-Path $root ($file -replace '/', '\')
  if (Test-Path -LiteralPath $from -PathType Leaf) {
    $to = Join-Path $backup ($file -replace '/', '\')
    New-Item -ItemType Directory -Force -Path (Split-Path $to) | Out-Null
    Copy-Item -LiteralPath $from -Destination $to
  }
}
Write-Host "Backup: $backup"
& $git merge --ff-only origin/main
if ($LASTEXITCODE -ne 0) { throw 'STOP: Merge failed; backup kept.' }
Write-Host 'Update complete. Refresh browser with Ctrl+F5.'
