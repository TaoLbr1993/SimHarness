param([switch]$NoUi, [ValidateSet('en','zh')][string]$Language = 'en')

$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$python = Join-Path $repoRoot '.venv/Scripts/python.exe'
$sumoBin = Join-Path $repoRoot '.venv/Lib/site-packages/sumo/bin'
$logs = Join-Path $repoRoot 'artifacts/scene-workspace-logs'
New-Item -ItemType Directory -Force -Path $logs | Out-Null
if (-not (Test-Path -LiteralPath $python)) { throw 'Project .venv is missing.' }
if (-not (Test-Path -LiteralPath (Join-Path $sumoBin 'sumo.exe'))) { throw 'Windows SUMO is missing from .venv.' }

function Read-ServiceHealth([string]$Url) {
    try { return Invoke-RestMethod -Uri $Url -TimeoutSec 2 } catch { return $null }
}

function Wait-ServiceHealth([string]$Url, [Diagnostics.Process]$Process, [string]$LogFile) {
    for ($attempt=0; $attempt -lt 45; $attempt++) {
        if (Read-ServiceHealth $Url) { return }
        if ($Process.HasExited) { throw "Service exited. See $LogFile" }
        Start-Sleep -Milliseconds 500
    }
    throw "Service did not become ready. See $LogFile"
}

if (-not (Read-ServiceHealth 'http://127.0.0.1:8001/health')) {
    if (Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue) {
        throw 'Port 8001 is occupied by an unrecognized service.'
    }
    $apiKey = [Environment]::GetEnvironmentVariable('OPENAI_API_KEY', 'Process')
    if (-not $apiKey) { $apiKey = [Environment]::GetEnvironmentVariable('OPENAI_API_KEY', 'User') }
    if (-not $apiKey) { $apiKey = [Environment]::GetEnvironmentVariable('OPENAI_API_KEY', 'Machine') }
    if (-not $apiKey) { throw 'OPENAI_API_KEY is not configured.' }
    $previousModelKey = $env:MODEL_API_KEY
    $previousWslEnv = $env:WSLENV
    $mapEnvironmentNames = @('AMAP_WEB_API_KEY','MAP_GEOCODER_PROVIDER','MAP_AMAP_CITY')
    $previousMapEnvironment = @{}
    foreach ($mapEnvironmentName in $mapEnvironmentNames) {
        $previousMapEnvironment[$mapEnvironmentName] = [Environment]::GetEnvironmentVariable($mapEnvironmentName, 'Process')
    }
    try {
        $env:MODEL_API_KEY = $apiKey
        $forwardedMapNames = @()
        foreach ($mapEnvironmentName in $mapEnvironmentNames) {
            $mapEnvironmentValue = [Environment]::GetEnvironmentVariable($mapEnvironmentName, 'Process')
            if (-not $mapEnvironmentValue) { $mapEnvironmentValue = [Environment]::GetEnvironmentVariable($mapEnvironmentName, 'User') }
            if (-not $mapEnvironmentValue) { $mapEnvironmentValue = [Environment]::GetEnvironmentVariable($mapEnvironmentName, 'Machine') }
            if (-not [string]::IsNullOrWhiteSpace($mapEnvironmentValue)) {
                [Environment]::SetEnvironmentVariable($mapEnvironmentName, $mapEnvironmentValue, 'Process')
                $forwardedMapNames += $mapEnvironmentName
            }
        }
        $forwardedNames = @('MODEL_API_KEY') + $mapEnvironmentNames
        $forwardedPattern = '^(' + (($forwardedNames | ForEach-Object { [regex]::Escape($_) }) -join '|') + ')(/|$)'
        $env:WSLENV = (@($previousWslEnv -split ':' | Where-Object { $_ -and $_ -notmatch $forwardedPattern }) + @((@('MODEL_API_KEY') + $forwardedMapNames) | ForEach-Object { $_ + '/u' })) -join ':'
        $backend = Join-Path $repoRoot 'trafficforge/backend'
        $arguments = @('-d','SimHarness-Ubuntu','-u','nl2map','--cd',('"'+$backend+'"'),'--','/opt/nl2map-venv/bin/python','-m','uvicorn','main:app','--host','127.0.0.1','--port','8001')
        $generator = Start-Process -FilePath 'wsl.exe' -ArgumentList $arguments -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logs 'generator.stdout.log') -RedirectStandardError (Join-Path $logs 'generator.stderr.log') -PassThru
    } finally {
        $env:MODEL_API_KEY = $previousModelKey
        $env:WSLENV = $previousWslEnv
        foreach ($mapEnvironmentName in $mapEnvironmentNames) {
            [Environment]::SetEnvironmentVariable($mapEnvironmentName, $previousMapEnvironment[$mapEnvironmentName], 'Process')
        }
        $apiKey = $null
        $mapEnvironmentValue = $null
    }
    Wait-ServiceHealth 'http://127.0.0.1:8001/health' $generator (Join-Path $logs 'generator.stderr.log')
}

if (-not (Read-ServiceHealth 'http://127.0.0.1:8000/api/v1/health')) {
    if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) {
        throw 'Port 8000 is occupied by an unrecognized service.'
    }
    $previousPath = $env:PATH
    try {
        $env:PATH = $sumoBin + [IO.Path]::PathSeparator + $previousPath
        $core = Start-Process -FilePath $python -ArgumentList @('-m','trafficverse.cli','serve','--carla-mode','disabled','--port','8000') -WorkingDirectory $repoRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logs 'core.stdout.log') -RedirectStandardError (Join-Path $logs 'core.stderr.log') -PassThru
    } finally {
        $env:PATH = $previousPath
    }
    Wait-ServiceHealth 'http://127.0.0.1:8000/api/v1/health' $core (Join-Path $logs 'core.stderr.log')
}

if (-not $NoUi) {
    Start-Process -FilePath $python -ArgumentList @('scripts/dev/open_trafficforge.py','--language',$Language) -WorkingDirectory $repoRoot -RedirectStandardOutput (Join-Path $logs 'desktop.stdout.log') -RedirectStandardError (Join-Path $logs 'desktop.stderr.log') | Out-Null
}
Write-Host 'TrafficForge ready. Describe a scene, review the preview, and confirm to add it before starting a simulation.'
Write-Host "CLI: .venv/Scripts/python.exe trafficforge/scene_cli.py list"
