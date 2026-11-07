<#
.SYNOPSIS
  Download photos from the school's public Facebook page with gallery-dl.

.DESCRIPTION
  Cookies come from a Netscape-format file you export from your own browser.
  Images only (videos off). A download archive makes reruns fetch only new photos.
  Cookie contents are never read or printed; only the path is checked.

.EXAMPLE
  .\ingest_facebook.ps1 -Simulate      # count what would be fetched, write nothing
  .\ingest_facebook.ps1 -Limit 20      # fetch at most the first 20 items
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Url,   # the page's /photos URL, e.g. https://www.facebook.com/<page>/photos
    [string]$Cookies = (Join-Path $PSScriptRoot '.secrets\facebook-cookies.txt'),
    [switch]$Simulate,
    [Nullable[datetime]]$Since,   # stop at the first photo older than this (the page lists newest first)
    [ValidateRange(0, [int]::MaxValue)]
    [int]$Limit = 0   # 0 = no limit
)

$ErrorActionPreference = 'Stop'

$ingestDir = Join-Path $PSScriptRoot 'ingest'
$destDir   = Join-Path $ingestDir 'facebook'
$archive   = Join-Path $ingestDir 'archive.sqlite3'

# pip's user Scripts folder is often not on PATH, so fall back to `python -m gallery_dl`.
$gdl = Get-Command gallery-dl -ErrorAction SilentlyContinue
if ($gdl) { $gdlExe = $gdl.Source; $gdlPre = @() }
elseif ((Get-Command python -ErrorAction SilentlyContinue) -and ((& python -c 'import gallery_dl' 2>&1 | Out-String) -eq '')) { $gdlExe = (Get-Command python).Source; $gdlPre = @('-m', 'gallery_dl') }
else { throw 'gallery-dl is not installed. Install it with: pip install gallery-dl' }
if (-not (Test-Path -LiteralPath $Cookies -PathType Leaf)) {
    throw "Cookie file not found at '$Cookies'. Export your Facebook cookies (Netscape format) from your browser to that path."
}

New-Item -ItemType Directory -Force -Path $destDir | Out-Null

$gdlArgs = @(
    '--cookies', $Cookies,
    '--download-archive', $archive,
    '--sleep-request', '2-5',
    '-o', 'videos=false',
    '-d', $destDir
)
if ($Limit -gt 0) { $gdlArgs += @('--range', "1-$Limit") }
if ($Since)       { $gdlArgs += @('--filter', ('date >= datetime({0},{1},{2}) or abort()' -f $Since.Year, $Since.Month, $Since.Day)) }
if ($Simulate)    { $gdlArgs += '--simulate' }
$gdlArgs += $Url

if ($Simulate) {
    # Output lists the would-be files; keep only the count so no names or paths are printed.
    $lines = @(& $gdlExe @gdlPre @gdlArgs)
    $count = @($lines | Where-Object { $_ -and $_.Trim() }).Count
    Write-Host "Simulate: $count item(s) would be considered. Nothing downloaded."
} else {
    $ErrorActionPreference = 'Continue'   # PowerShell 5.1 turns gallery-dl's stderr warnings into errors under 'Stop'
    & $gdlExe @gdlPre @gdlArgs
    if ($LASTEXITCODE -ne 0) { throw "gallery-dl exited with code $LASTEXITCODE" }
    Write-Host "Done. Files are in $destDir (archive: $archive)."
}
