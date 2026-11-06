# Prepare (and optionally deploy) the gallery to Cloudflare Pages. Run from anywhere.
#   .\pipeline\deploy_cloudflare.ps1              -> builds .\dist only (no network)
#   .\pipeline\deploy_cloudflare.ps1 -Deploy      -> also runs wrangler pages deploy (needs `wrangler login`)
# Never ships: face embeddings, raw detections, pipeline code, tag jobs, corrections history.
# After the first deploy, put the project behind Cloudflare Access (Zero Trust > Access > Applications,
# email one-time-PIN policy). The gallery names minors: do not leave it public.
param([switch]$Deploy, [string]$Project = "rkmv-gallery", [string]$Bucket = "")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$dist = Join-Path $root "dist"
if (Test-Path $dist) { Remove-Item $dist -Recurse -Force }
New-Item -ItemType Directory -Path $dist | Out-Null
Copy-Item "$root\index.html" $dist
Copy-Item "$root\css", "$root\js", "$root\img" $dist -Recurse
# Data JSON names students: it must NOT ship as a static file. It goes to R2 and is served by functions/api/data/[name].js behind login.
$mb = [math]::Round((Get-ChildItem $dist -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 1)
$n = (Get-ChildItem $dist -Recurse -File).Count
Write-Host "dist ready: $n files, $mb MB (Pages limits: 20000 files, 25 MB each)"
if ($Deploy) {
  if (-not $Bucket) { throw "Pass -Bucket <r2-bucket-name>: the data JSON is uploaded there, not to Pages." }
  foreach ($f in "photos", "people", "graph", "questions", "clusters") { npx wrangler r2 object put "$Bucket/data/$f.json" --file "$root\data\$f.json" --content-type application/json --remote }
  Push-Location $root; try { npx wrangler pages deploy $dist --project-name $Project } finally { Pop-Location }  # run from gallery/ so functions/ is picked up
}
