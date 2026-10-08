param([string]$Revision = '', [string]$Source = '')
$ErrorActionPreference = 'Stop'
$companionRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
# The build uses the upstream Linux/Bun toolchain; keep Windows shell quoting out of it.
$linuxRoot = (& wsl -d Ubuntu --exec wslpath -a $companionRoot.Replace('\', '/')).Trim()
if ($LASTEXITCODE -ne 0) { throw 'WSL path conversion failed' }
$arguments = @('-d', 'Ubuntu', '--cd', $linuxRoot, '--', 'npx', '--yes', '--package=bun@1.3.11', '--', '.venv-linux/bin/python', 'scripts/check_upgrade.py')
if ($Revision) { $arguments += @('--revision', $Revision) }
if ($Source) {
    $linuxSource = (& wsl -d Ubuntu --exec wslpath -a (Resolve-Path $Source).Path.Replace('\', '/')).Trim()
    $arguments += @('--source', $linuxSource)
}
& wsl @arguments
if ($LASTEXITCODE -ne 0) { throw 'Compatibility gate failed; existing deployment was not changed' }
