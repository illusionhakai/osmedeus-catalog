param([string]$Report = '', [switch]$ValidateOnly)
$ErrorActionPreference = 'Stop'
$companionRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $Report) { $Report = Join-Path $companionRoot 'build/compatibility.json' }
$checked = Get-Content -LiteralPath $Report -Raw | ConvertFrom-Json
$pin = Get-Content -LiteralPath (Join-Path $companionRoot 'upstream.json') -Raw | ConvertFrom-Json
if ($checked.status -ne 'passed' -or $checked.revision -ne $pin.osmedeus_revision -or $checked.companion_version -ne $pin.companion_version) {
    throw 'A passing compatibility report matching upstream.json is required. No deployment changed.'
}
$requiredChecks = @('catalog', 'types', 'overlay-lint', 'static-build', 'browser', 'offline-input-smoke', 'real-engine-auth-gateway-outage')
if ([version]$checked.companion_version -ge [version]'0.2.0') { $requiredChecks += 'delivered-input-smoke' }
if ([version]$checked.companion_version -ge [version]'0.3.0') { $requiredChecks += 'explicit-workflow-handoff' }
if ([version]$checked.companion_version -ge [version]'0.4.0') { $requiredChecks += 'scan-batch-submit-cancel' }
foreach ($check in $requiredChecks) {
    if ($checked.checks -notcontains $check) { throw "Missing compatibility check: $check" }
}
foreach ($entry in $checked.images.PSObject.Properties) {
    $actual = & docker image inspect --format '{{.Id}}' $entry.Name
    if ($LASTEXITCODE -ne 0 -or $actual.Trim() -ne $entry.Value) { throw "Tested image is missing or changed: $($entry.Name)" }
}
foreach ($image in @("osmedeus-catalog:$($checked.release)", "osmedeus-catalog-ui:$($checked.release)")) {
    if ($checked.images.PSObject.Properties.Name -notcontains $image) { throw "Report does not contain $image" }
}
if ($ValidateOnly) {
    Write-Host 'Passing report, pin and tested image IDs verified. No services were started.'
    exit 0
}
$previousRelease = $env:CATALOG_RELEASE
try {
    $env:CATALOG_RELEASE = $checked.release
    & docker compose --project-directory $companionRoot -f (Join-Path $companionRoot 'compose.yaml') up -d --no-build
    if ($LASTEXITCODE -ne 0) { throw 'Companion deployment failed. Inspect docker compose logs.' }
} finally {
    $env:CATALOG_RELEASE = $previousRelease
}
Write-Host 'Companion started. Open http://localhost:8080 (or your configured CATALOG_PORT).'
Write-Host 'The existing Osmedeus engine was not changed; it must match the checked upstream revision.'
