#!/usr/bin/env bash
#
# index_codebase_memory.sh — index this project and reference repos into
# codebase-memory-mcp for SOURCE-CODE intelligence (call chains, risk paths).
#
# This is NOT for OHLCV/market data. See docs/CODEBASE_MEMORY_MCP.md.
#
# The binary is optional: if codebase-memory-mcp is not installed, this script
# prints install guidance and exits without error so it is safe to run anywhere
# (including CI). Override the binary path with CODEBASE_MEMORY_MCP_BIN.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="${CODEBASE_MEMORY_MCP_BIN:-codebase-memory-mcp}"

# Repos to index. "required" warns if missing; "optional" silently skips.
declare -a REQUIRED=(
  "$ROOT"                          # quant-ai-trader (this project)
  "$ROOT/external/quant-trading"   # strategy source
)
declare -a OPTIONAL=(
  "$ROOT/external/QuantDinger"     # backtest/platform reference
  "$ROOT/external/Kronos"          # K-line prediction model
)

if ! command -v "$BIN" >/dev/null 2>&1; then
  echo "codebase-memory-mcp not found (looked for '$BIN')."
  echo "It is optional. To install, see docs/CODEBASE_MEMORY_MCP.md, or set"
  echo "CODEBASE_MEMORY_MCP_BIN to the binary path. Skipping indexing."
  exit 0
fi

index_repo() {
  local path="$1" kind="$2"
  if [ ! -d "$path" ]; then
    if [ "$kind" = "required" ]; then
      echo "WARN: required path missing, skipping: $path" >&2
    else
      echo "skip (not present): $path"
    fi
    return 0
  fi
  echo "indexing ($kind): $path"
  "$BIN" cli index_repository "{\"repo_path\": \"$path\"}"
}

for p in "${REQUIRED[@]}"; do index_repo "$p" required; done
for p in "${OPTIONAL[@]}"; do index_repo "$p" optional; done

echo "codebase-memory indexing complete."
