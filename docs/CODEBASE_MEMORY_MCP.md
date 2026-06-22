# Codebase-Memory MCP — Setup & Usage

`codebase-memory-mcp` ([DeusData/codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp))
gives coding agents (Codex, Claude, others) **source-code intelligence** for
this project: it indexes source files so an agent can navigate structure, trace
call chains, and locate risk-sensitive execution paths quickly.

## What it is for (and what it is NOT for)

- ✅ **Source-code intelligence.** Understanding project structure, call chains,
  where trades can be placed, where risk checks live, where MT5 execution
  happens, which files relate to Kronos predictions, etc.
- ❌ **Not for market data.** It does **not** store, compress, or serve OHLCV /
  historical price data. Market data lives under `data/` and is handled by the
  data service (later milestone). Never point this tool at price datasets.

Think of it as "code search and memory for agents", not a data store.

## Install

This tool is **optional** and **not required** to run the project, its tests,
or CI. Install it only on a developer/agent machine that needs code navigation.

macOS / Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/install.sh | bash
```

Windows (PowerShell):

```powershell
Invoke-WebRequest -Uri https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/install.ps1 -OutFile install.ps1
.\install.ps1
```

Other options: Homebrew, Scoop, Winget, Chocolatey, AUR
(`yay -S codebase-memory-mcp-bin`), `go install`, or a pre-built binary from the
[latest release](https://github.com/DeusData/codebase-memory-mcp/releases/latest).

The result is a single static binary named `codebase-memory-mcp`. Indexes are
stored in `~/.cache/codebase-memory-mcp/` (SQLite) — outside this repo.

## Register as an MCP server

Add to `~/.claude/.mcp.json` (or a project-local `.mcp.json`):

```json
{
  "mcpServers": {
    "codebase-memory-mcp": {
      "command": "/path/to/codebase-memory-mcp",
      "args": []
    }
  }
}
```

After registration the agent gets the tool's indexing and query tools
(e.g. `index_repository`).

## What to index

Index source-code repositories only:

| Repository                 | Index?    | Why                                       |
| -------------------------- | --------- | ----------------------------------------- |
| `quant-ai-trader` (this)   | required  | our own structure, risk & execution paths |
| `external/quant-trading`   | required  | strategy source ideas                     |
| `external/QuantDinger`     | optional  | backtest/trading-platform reference       |
| `external/Kronos`          | optional  | K-line prediction model code              |

> The `external/*` repos are cloned by
> [`scripts/clone_external_repos.py`](../scripts/clone_external_repos.py) (see
> `config/external_repos.yaml`). Clone them first; optional repos are skipped if
> not present. Do **not** index `external/OpenBB` for this purpose — it is large
> and used as a data-platform reference, not for code navigation.

## How to index

Use the helper scripts (they index this repo + `external/quant-trading`, and
the optional repos when present). They no-op with a clear message if the binary
is not installed, so they are safe to run anywhere:

```bash
# macOS / Linux
./scripts/index_codebase_memory.sh

# Windows (PowerShell)
./scripts/index_codebase_memory.ps1
```

Override the binary location with the `CODEBASE_MEMORY_MCP_BIN` environment
variable if it is not on `PATH`.

Equivalent manual command (per repo, absolute path):

```bash
codebase-memory-mcp cli index_repository '{"repo_path": "/abs/path/to/repo"}'
```

## Useful queries

Once indexed, ask the agent to run queries like these against the codebase
memory. They are most valuable for the project's safety-critical paths:

1. **Find every function that can place a trade.**
2. **Find the call chain from the AI agent to execution.**
3. **Find every strategy adapter.**
4. **Find all risk checks performed before execution.**
5. **Find all code that references `order_send`** (direct MT5 execution must
   only ever live in the single locked MT5 gateway — see `SAFETY.md`).
6. **Find files related to Kronos prediction.**

These map directly to the architecture in
[`ARCHITECTURE.md`](../ARCHITECTURE.md): the agent proposes, the `RiskManager`
gates, and only the locked MT5 gateway may call `order_send`. Use query #1, #4,
and #5 as a quick audit that no execution path bypasses `RiskManager`.

## Notes

- Re-run the indexing script after significant code changes to refresh memory.
- Indexes live in `~/.cache/codebase-memory-mcp/`, never committed to this repo.
- No secrets are read or stored by indexing; it operates on source files only.
