# Prepare (and optionally deploy) the gallery to Cloudflare Pages. Run from anywhere.
#   .\pipeline\deploy_cloudflare.ps1              -> builds .\dist only (no network)
#   .\pipeline\deploy_cloudflare.ps1 -Deploy      -> also runs wrangler pages deploy (needs `wrangler login`)
# Never ships: face embeddings, raw detections, pipeline code, tag jobs, corrections history.
# After the first deploy, put the project behind Cloudflare Access (Zero Trust > Access > Applications,
# email one-time-PIN policy). The gallery names minors: do not leave it public.
param([switch]$Deploy, [string]$Project = "rkmv-gallery")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$dist = Join-Path $root "dist"
if (Test-Path $dist) { Remove-Item $dist -Recurse -Force }
New-Item -ItemType Directory -Path "$dist\data" | Out-Null
Copy-Item "$root\index.html" $dist
Copy-Item "$root\css", "$root\js", "$root\img" $dist -Recurse
foreach ($f in "photos", "people", "graph", "questions", "clusters") { Copy-Item "$root\data\$f.json" "$dist\data\" }
"/data/*`n  Cache-Control: no-cache" | Set-Content "$dist\_headers" -Encoding ascii
$mb = [math]::Round((Get-ChildItem $dist -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 1)
$n = (Get-ChildItem $dist -Recurse -File).Count
Write-Host "dist ready: $n files, $mb MB (Pages limits: 20000 files, 25 MB each)"
if ($Deploy) { npx wrangler pages deploy $dist --project-name $Project }
