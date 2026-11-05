# Compatibility entry for existing Scene Studio shortcuts.
param([switch]$NoUi, [ValidateSet('en','zh')][string]$Language = 'en')
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'start_trafficforge.ps1') @PSBoundParameters
