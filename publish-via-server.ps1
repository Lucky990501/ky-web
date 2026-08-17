param([switch]$Execute)

# Backward-compatible project entry point. The reusable engine lives in deployment/.
& (Join-Path $PSScriptRoot 'deployment/release.ps1') -ProjectRoot $PSScriptRoot -Execute:$Execute
