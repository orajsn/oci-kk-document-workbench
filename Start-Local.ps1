param(
    [ValidateRange(1024, 65535)][int]$Port = 8765,
    [string]$PythonExecutable = 'python'
)

$ErrorActionPreference = 'Stop'
$pilotLauncher = Join-Path $PSScriptRoot 'run_local.py'
Write-Host "KK local workflow demo: http://127.0.0.1:$Port"
Write-Host 'Synthetic Demo does not read the document with AI. Ctrl+C stops the server.'
& $PythonExecutable $pilotLauncher --port $Port
exit $LASTEXITCODE
