$ErrorActionPreference = 'Stop'

$baseDir = Join-Path $PSScriptRoot '.pytest_runtime'
$tmpRoot = Join-Path $baseDir 'tmp'
$cacheDir = Join-Path $baseDir 'cache'

$runId = "run_{0}_{1}" -f (Get-Date -Format 'yyyyMMdd_HHmmss'), $PID
$tmpDir = Join-Path $tmpRoot $runId

New-Item -ItemType Directory -Path $baseDir -Force | Out-Null
New-Item -ItemType Directory -Path $tmpRoot -Force | Out-Null
New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null
New-Item -ItemType Directory -Path $cacheDir -Force | Out-Null

python -m pytest unit_tests API_tests --basetemp $tmpDir @args
$exitCode = $LASTEXITCODE
exit $exitCode
