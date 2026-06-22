#Requires -Version 5
<#
.SYNOPSIS
    Index this project and reference repos into codebase-memory-mcp for
    SOURCE-CODE intelligence (call chains, risk-sensitive execution paths).

.DESCRIPTION
    This is NOT for OHLCV/market data. See docs/CODEBASE_MEMORY_MCP.md.

    The binary is optional: if codebase-memory-mcp is not installed, this script
    prints install guidance and exits 0 so it is safe to run anywhere (including
    CI). Override the binary path with the CODEBASE_MEMORY_MCP_BIN env var.
#>
$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$Bin  = if ($env:CODEBASE_MEMORY_MCP_BIN) { $env:CODEBASE_MEMORY_MCP_BIN } else { 'codebase-memory-mcp' }

# Repos to index. "required" warns if missing; "optional" silently skips.
$Required = @(
    $Root,                                      # quant-ai-trader (this project)
    (Join-Path $Root 'external/quant-trading')  # strategy source
)
$Optional = @(
    (Join-Path $Root 'external/QuantDinger'),   # backtest/platform reference
    (Join-Path $Root 'external/Kronos')         # K-line prediction model
)

if (-not (Get-Command $Bin -ErrorAction SilentlyContinue)) {
    Write-Host "codebase-memory-mcp not found (looked for '$Bin')."
    Write-Host "It is optional. To install, see docs/CODEBASE_MEMORY_MCP.md, or set"
    Write-Host "CODEBASE_MEMORY_MCP_BIN to the binary path. Skipping indexing."
    exit 0
}

function Index-Repo {
    param([string]$Path, [string]$Kind)

    if (-not (Test-Path $Path)) {
        if ($Kind -eq 'required') { Write-Warning "required path missing, skipping: $Path" }
        else { Write-Host "skip (not present): $Path" }
        return
    }

    Write-Host "indexing ($Kind): $Path"
    # Use forward slashes so the JSON string is valid on Windows too.
    $jsonPath = ($Path -replace '\\', '/')
    $payload  = '{"repo_path": "' + $jsonPath + '"}'
    & $Bin cli index_repository $payload
}

foreach ($p in $Required) { Index-Repo -Path $p -Kind 'required' }
foreach ($p in $Optional) { Index-Repo -Path $p -Kind 'optional' }

Write-Host "codebase-memory indexing complete."
