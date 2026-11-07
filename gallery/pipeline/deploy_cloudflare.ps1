# Build (and optionally deploy) the gallery to Cloudflare Pages. Run from anywhere.
#   .\pipeline\deploy_cloudflare.ps1 -Target smriti            -> builds .\dist only (no network)
#   .\pipeline\deploy_cloudflare.ps1 -Target smriti  -Deploy   -> demo site, fabricated data from .\demo
#   .\pipeline\deploy_cloudflare.ps1 -Target mb27ndp -Deploy   -> the real batch site, .\data + .\img
# Photos are renamed to unguessable names first (pipeline\obfuscate.py). The data JSON (it names students)
# goes to Workers KV and is served by functions/api/data/[name].js behind login. It never ships as a static file.
# Never ships: face embeddings, raw detections, pipeline code, tag jobs, corrections history.
param([Parameter(Mandatory)][ValidateSet("smriti", "mb27ndp")][string]$Target, [switch]$Deploy)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$sites = @{
  smriti  = @{ Project = "smriti-gallery"; Src = "$root\demo"; Kv = "dbbb32af93aa4a24aaa6ad77fb2c896d" }
  mb27ndp = @{ Project = "mb27ndp";        Src = $root;       Kv = "80cc6d107d874218b937e6c4f81e4ead" }
}
$site = $sites[$Target]
$dist = Join-Path $root "dist"
$stage = Join-Path $root "dist-stage"
foreach ($d in $dist, $stage) { if (Test-Path $d) { Remove-Item $d -Recurse -Force } }
New-Item -ItemType Directory -Path $dist, $stage | Out-Null
python "$PSScriptRoot\obfuscate.py" --src $site.Src --out $stage
if ($LASTEXITCODE -ne 0) { throw "obfuscate.py failed" }
Copy-Item "$root\index.html" $dist
Copy-Item "$root\css", "$root\js", "$stage\img" $dist -Recurse
if ($Target -eq "smriti") {
  # Public demo: the data is fabricated, so it can be a plain static file and needs no sign-in.
  Copy-Item "$stage\data" $dist -Recurse
  Set-Content "$dist\js\config.js" "export const IMAGE_BASE = `"`";`nexport const API = `"/api`";`nexport const DEMO = true;`n"
}
$mb = [math]::Round((Get-ChildItem $dist -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 1)
$n = (Get-ChildItem $dist -Recurse -File).Count
Write-Host "dist ready for '$Target': $n files, $mb MB (Pages limits: 20000 files, 25 MB each)"
if ($Deploy) {
  $ErrorActionPreference = "Continue"  # PS 5.1 treats any native stderr line (wrangler warnings) as fatal under "Stop"; exit codes are checked instead
  $toml = Join-Path $root "wrangler.toml"
  $backup = Get-Content $toml -Raw
  try {
    Copy-Item "$root\deploy\$Target.toml" $toml -Force   # binds this site's KV namespace
    Push-Location $root   # run from gallery/ so functions/ is picked up
    foreach ($f in "photos", "people", "graph", "questions", "clusters") {
      npx wrangler kv key put "data/$f.json" --namespace-id $site.Kv --path "$stage\data\$f.json" --remote
      if ($LASTEXITCODE -ne 0) { throw "kv upload failed for $f" }
    }
    npx wrangler pages deploy $dist --project-name $site.Project --commit-dirty=true
    if ($LASTEXITCODE -ne 0) { throw "wrangler pages deploy failed" }
  } finally {
    Pop-Location
    Set-Content $toml $backup -NoNewline
    Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
  }
}
