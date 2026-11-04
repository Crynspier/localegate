<#
.SYNOPSIS
    Bootstraps LocaleGate Python (.venv via uv) and Web (npm) dependencies on Windows.
#>
$ErrorActionPreference = "Stop"

Write-Host "[LocaleGate] Syncing Python 3.12 virtual environment via uv..." -ForegroundColor Cyan
uv sync --dev

if (Test-Path "web/package.json") {
    Write-Host "[LocaleGate] Installing web dashboard dependencies..." -ForegroundColor Cyan
    npm --prefix web install
    Write-Host "[LocaleGate] Building web dashboard static bundle..." -ForegroundColor Cyan
    npm --prefix web run build
}

Write-Host "[LocaleGate] Running environment doctor..." -ForegroundColor Cyan
uv run locale-gate doctor
