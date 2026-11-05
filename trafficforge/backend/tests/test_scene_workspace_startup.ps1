# Offline launcher regression: mock service/process calls; no real credentials or processes.
$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../..'))
$names = @('OPENAI_API_KEY','MODEL_API_KEY','AMAP_WEB_API_KEY','MAP_GEOCODER_PROVIDER','MAP_AMAP_CITY','WSLENV')
$saved = @{}
foreach ($name in $names) { $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
$capture = [pscustomobject]@{ HealthCalls=0; Forwarded=@(); MapKeyReachedChild=$false }
function Invoke-RestMethod {
    param($Uri, $TimeoutSec)
    if ($Uri -eq 'http://127.0.0.1:8001/health') {
        $capture.HealthCalls++
        if ($capture.HealthCalls -eq 1) { throw 'offline start required' }
    }
    return @{ status='ok' }
}
function Get-NetTCPConnection { param($LocalPort,$State,$ErrorAction) return @() }
function Start-Process {
    param($FilePath,$ArgumentList,$WindowStyle,$RedirectStandardOutput,$RedirectStandardError,[switch]$PassThru)
    if ($FilePath -ne 'wsl.exe') { throw 'Unexpected service launch' }
    $capture.Forwarded = $env:WSLENV -split ':'
    $capture.MapKeyReachedChild = $env:AMAP_WEB_API_KEY -eq 'offline-test-map-key'
    return [System.Diagnostics.Process]::GetCurrentProcess()
}
try {
    $env:OPENAI_API_KEY = 'offline-test-model-key'
    $env:AMAP_WEB_API_KEY = ' '
    $env:MAP_GEOCODER_PROVIDER = ' '
    $env:MAP_AMAP_CITY = ' '
    $env:WSLENV = 'AMAP_WEB_API_KEY/u:MAP_GEOCODER_PROVIDER/u:MAP_AMAP_CITY/u:EXISTING/u'
    $capture.HealthCalls = 0
    & (Join-Path $repo 'scripts/dev/start_scene_workspace.ps1') -NoUi | Out-Null
    if ($capture.Forwarded -match '^(AMAP_WEB_API_KEY|MAP_GEOCODER_PROVIDER|MAP_AMAP_CITY)(/|$)') {
        throw 'Absent map configuration was exported into WSL'
    }
    if ('MODEL_API_KEY/u' -notin $capture.Forwarded -or 'EXISTING/u' -notin $capture.Forwarded) {
        throw 'Required or unrelated forwarding was lost'
    }
    $env:AMAP_WEB_API_KEY = 'offline-test-map-key'
    $env:MAP_GEOCODER_PROVIDER = 'amap'
    $env:MAP_AMAP_CITY = '110105'
    $beforeWslEnv = $env:WSLENV
    $capture.HealthCalls = 0
    & (Join-Path $repo 'scripts/dev/start_scene_workspace.ps1') -NoUi | Out-Null
    foreach ($name in @('AMAP_WEB_API_KEY','MAP_GEOCODER_PROVIDER','MAP_AMAP_CITY')) {
        if (@($capture.Forwarded | Where-Object { $_ -eq ($name + '/u') }).Count -ne 1) {
            throw 'Configured map variable was not forwarded exactly once'
        }
    }
    if (-not $capture.MapKeyReachedChild -or $env:WSLENV -ne $beforeWslEnv) {
        throw 'Credential forwarding or environment restoration failed'
    }
    Write-Output 'PASS: absent/present map configuration, forwarding deduplication and restoration'
} finally {
    foreach ($name in $names) { [Environment]::SetEnvironmentVariable($name, $saved[$name], 'Process') }
}
