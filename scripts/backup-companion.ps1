param([string]$Destination = '')
$ErrorActionPreference = 'Stop'
$companionRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$stamp = Get-Date -Format yyyyMMdd-HHmmss-fff
if (-not $Destination) { $Destination = Join-Path $companionRoot "build/backup-pre-deploy-$stamp" }
if (Test-Path -LiteralPath $Destination) { throw 'Backup destination must not already exist' }
$catalog = (& docker compose --project-directory $companionRoot -f (Join-Path $companionRoot 'compose.yaml') ps --all --quiet catalog).Trim()
$gateway = (& docker compose --project-directory $companionRoot -f (Join-Path $companionRoot 'compose.yaml') ps --all --quiet gateway).Trim()
if (-not $catalog -or -not $gateway) { throw 'Both existing companion containers are required for this backup' }
$catalogImage = (& docker inspect --format '{{.Image}}' $catalog).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve current catalog image ID' }
$gatewayImage = (& docker inspect --format '{{.Image}}' $gateway).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve current image IDs' }
$catalogTag = "osmedeus-catalog:rollback-$stamp"
$gatewayTag = "osmedeus-catalog-ui:rollback-$stamp"
$python = Join-Path $companionRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    $python = (Get-Command python3 -ErrorAction SilentlyContinue).Source
    if (-not $python) { $python = (Get-Command python -ErrorAction Stop).Source }
}
$backup = New-Item -ItemType Directory -Path $Destination
New-Item -ItemType Directory -Path (Join-Path $backup.FullName 'data') | Out-Null
@{ services = @{ catalog = @{ image = $catalogTag }; gateway = @{ image = $gatewayTag } } } |
    ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $backup.FullName 'rollback.compose.json') -Encoding UTF8
Copy-Item -LiteralPath (Join-Path $companionRoot 'upstream.json') -Destination (Join-Path $backup.FullName 'candidate-pin.json')
if (Test-Path -LiteralPath (Join-Path $companionRoot 'build/compatibility.json')) {
    Copy-Item -LiteralPath (Join-Path $companionRoot 'build/compatibility.json') -Destination (Join-Path $backup.FullName 'checked-release.json')
}
$wasRunning = (& docker inspect --format '{{.State.Running}}' $catalog).Trim() -eq 'true'
try {
    & docker stop --time 90 $catalog
    if ($LASTEXITCODE -ne 0) { throw 'Cannot stop catalog; no data copied' }
    $catalogRetention = & $python (Join-Path $PSScriptRoot 'retain_image.py') $catalog $catalogTag
    if ($LASTEXITCODE -ne 0) { throw 'Cannot retain current catalog image' }
    $gatewayRetention = & $python (Join-Path $PSScriptRoot 'retain_image.py') $gateway $gatewayTag
    if ($LASTEXITCODE -ne 0) { throw 'Cannot retain current gateway image' }
    @{ catalog = $catalogImage; gateway = $gatewayImage;
       retained_images = @{ catalog = ($catalogRetention | ConvertFrom-Json); gateway = ($gatewayRetention | ConvertFrom-Json) };
       created_at = (Get-Date).ToUniversalTime().ToString('o') } |
        ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $backup.FullName 'running-images.json') -Encoding UTF8
    & docker cp "${catalog}:/data/." (Join-Path $backup.FullName 'data')
    if ($LASTEXITCODE -ne 0) { throw 'Data backup failed; do not promote' }
} finally {
    # Start the exact previous container, never recreate it with candidate tags.
    if ($wasRunning) {
        & docker start $catalog | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Cannot restart previous catalog container' }
    }
}
Write-Host "Stopped-service backup complete: $($backup.FullName)"
Write-Host 'Rollback images and catalog data retained. No engine container was touched.'
