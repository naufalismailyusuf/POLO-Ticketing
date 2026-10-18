# Revisi 30: safely make a NEW, separate MP4 copy with Fast Start.
# Does not overwrite the source video, database, or QNAP files.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$InputFile,
    [Parameter(Mandatory=$true)][string]$OutputFile
)
$ErrorActionPreference = 'Stop'
$source = (Resolve-Path -LiteralPath $InputFile -ErrorAction Stop).ProviderPath
if ([IO.Path]::GetExtension($source).ToLowerInvariant() -ne '.mp4') {
    throw 'Input must be an MP4 file.'
}
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    throw 'ffmpeg.exe not found in PATH. Install FFmpeg first or run on a PC with FFmpeg.'
}
$out = [IO.Path]::GetFullPath($OutputFile)
if ($source -ieq $out) { throw 'Output must not overwrite input.' }
if (Test-Path -LiteralPath $out) { throw 'Output already exists. Select a new name.' }
$dir = Split-Path -Parent $out
if (-not (Test-Path -LiteralPath $dir -PathType Container)) { throw 'Output folder does not exist.' }
& ffmpeg -hide_banner -loglevel error -n -i $source -c copy -movflags +faststart $out
if ($LASTEXITCODE -ne 0) {
    throw "FFmpeg failed (exit $LASTEXITCODE). Original was not changed."
}
if (-not (Test-Path -LiteralPath $out -PathType Leaf) -or (Get-Item -LiteralPath $out).Length -eq 0) {
    throw 'Output file was not created or is empty.'
}
Write-Host "Fast Start copy created: $out"
Write-Host 'Play and verify this file before uploading it to Guide as a replacement.'
