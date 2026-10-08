param([switch]$Watch, [switch]$Force, [switch]$NoOpen, [string]$Source = '')
$ErrorActionPreference = 'Stop'
$companionRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$arguments = @((Join-Path $PSScriptRoot 'deploy.py'))
if ($Watch) { $arguments += '--watch' }
if ($Force) { $arguments += '--force' }
if ($NoOpen) { $arguments += '--no-open' }
if ($Source) { $arguments += @('--source', (Resolve-Path -LiteralPath $Source).Path) }
$python = Join-Path $companionRoot '.venv/Scripts/python.exe'
if (Test-Path -LiteralPath $python) { & $python @arguments }
elseif (Get-Command python3 -ErrorAction SilentlyContinue) { & python3 @arguments }
elseif (Get-Command py -ErrorAction SilentlyContinue) { & py -3 @arguments }
else { throw 'Python 3 is required. Complete README deployment setup first.' }
if ($LASTEXITCODE -ne 0) { throw 'Deployment failed. See the error above.' }
