<#
.SYNOPSIS
    Validates the curated LocaleGate benchmark-v1 dataset offline.
#>
$ErrorActionPreference = "Stop"

Write-Host "[LocaleGate] Validating benchmark-v1 dataset..." -ForegroundColor Cyan
uv run locale-gate eval validate-dataset
