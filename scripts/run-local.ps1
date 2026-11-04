<#
.SYNOPSIS
    Starts the local loopback-only LocaleGate API on 127.0.0.1:8765.
    Optionally starts local llama-server when -WithModel is passed.
.PARAMETER HostAddress
    Local IP to bind (default: 127.0.0.1). Non-loopback addresses are rejected.
.PARAMETER Port
    Local TCP port to bind (default: 8765).
.PARAMETER WithModel
    If set, launches local llama-server in a background job before starting API.
#>
param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8765,
    [switch]$WithModel
)
$ErrorActionPreference = "Stop"

if ($HostAddress -ne "127.0.0.1" -and $HostAddress -ne "localhost" -and $HostAddress -ne "::1") {
    Write-Error "[LocaleGate Security] Refusing to bind to non-loopback address: $HostAddress"
    exit 1
}

$modelJob = $null
if ($WithModel) {
    Write-Host "[LocaleGate] Starting local model server on 127.0.0.1:8080 ..." -ForegroundColor Cyan
    $modelJob = Start-Job -ScriptBlock {
        uv run locale-gate model serve --model qwen35-q4km --port 8080
    }
}

try {
    Write-Host "[LocaleGate] Starting local API service on http://${HostAddress}:$Port ..." -ForegroundColor Cyan
    uv run locale-gate api serve --host $HostAddress --port $Port
} finally {
    if ($modelJob) {
        Write-Host "[LocaleGate] Stopping local model server job..." -ForegroundColor Yellow
        Stop-Job $modelJob -ErrorAction SilentlyContinue
        Remove-Job $modelJob -ErrorAction SilentlyContinue
    }
}
