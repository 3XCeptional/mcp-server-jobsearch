"""Enables `python -m indeed_mcp_server` as an alternate entry point.

Delegates entirely to `cli_main.main()`, which owns argument parsing
(including `--help`) and starting the stdio MCP server.
"""

from __future__ import annotations

from indeed_mcp_server.cli_main import main

if __name__ == "__main__":
    main()
