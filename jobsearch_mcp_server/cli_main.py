"""Thin CLI entry point, referenced by pyproject.toml's [project.scripts].

Argument parsing is minimal for v1: there are no real flags yet beyond the
`-h`/`--help` that `argparse` provides for free. Parsing (rather than
skipping straight to `server.main()`) is what makes `--help` exit 0 without
starting the stdio server loop, which would otherwise block waiting on
stdin.
"""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog="jobsearch-mcp-server",
        description=(
            "MCP server that gives AI assistants access to Indeed and Seek "
            "job search, postings, and listings through the user's own "
            "browser session, over the stdio MCP transport."
        ),
    )


def main() -> None:
    build_parser().parse_args()

    # Imported here, not at module scope, so `--help`/`-h` (which exits
    # inside `parse_args()` above) never pays the cost of importing the
    # server module (and, transitively, Playwright/mcp) just to print usage.
    from jobsearch_mcp_server.server import main as run_server

    run_server()


if __name__ == "__main__":  # pragma: no cover - manual invocation path
    main()
