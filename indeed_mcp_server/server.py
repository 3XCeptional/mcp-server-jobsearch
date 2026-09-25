"""MCP entrypoint: registers Indeed job tools on a FastMCP server.

The `mcp` SDK package renamed `FastMCP` (`mcp.server.fastmcp`) to
`MCPServer` (`mcp.server.mcpserver`) in its 2.x line - `pyproject.toml` pins
`mcp>=1.2.0`, which spans both. Import whichever name is actually available
at runtime rather than hardcoding the pre-2.x path, aliased to `FastMCP`
either way since that's the name the rest of this module (and the
linkedin-mcp-server reference project this mirrors) uses.
"""

from __future__ import annotations

try:
    from mcp.server.fastmcp import FastMCP
except ModuleNotFoundError:
    from mcp.server.mcpserver import MCPServer as FastMCP  # mcp>=2.0

from indeed_mcp_server.extractor import IndeedExtractor
from indeed_mcp_server.session_state import SessionManager

# Constructed eagerly at import time, but cheap: `SessionManager.__init__` and
# `IndeedExtractor.__init__` only store configuration - the actual browser
# session (and any Playwright/Chromium process) is created lazily, inside
# `SessionManager.get_or_create_session()`, the first time a tool call
# actually needs one.
extractor = IndeedExtractor(SessionManager())

mcp = FastMCP("indeed-mcp-server")


def register_job_tools(mcp: FastMCP, extractor: IndeedExtractor) -> None:
    """Register the 3 Indeed job tools on `mcp`, delegating to `extractor`."""

    @mcp.tool(name="search_jobs")
    async def search_jobs(
        keywords: str, location: str = "", max_results: int = 20
    ) -> list[dict]:
        """Search Indeed for jobs matching `keywords`, optionally near `location`."""
        results = await extractor.search_jobs(
            keywords,
            location or None,
            max_results,
        )
        return [
            {
                "job_id": r.job_id,
                "title": r.title,
                "company": r.company,
                "location": r.location,
                "snippet": r.snippet,
                "url": r.url,
                "posted": r.posted,
            }
            for r in results
        ]

    @mcp.tool(name="get_job_details")
    async def get_job_details(job_id: str) -> dict:
        """Fetch full detail (description, salary, job type) for one job id."""
        return await extractor.get_job_details(job_id)

    @mcp.tool(name="close_session")
    async def close_session() -> str:
        """Close the underlying browser session, releasing the Chromium process."""
        await extractor.close_session()
        return "Indeed session closed."


register_job_tools(mcp, extractor)


def main() -> None:
    """Run the MCP server over stdio transport."""
    mcp.run()


if __name__ == "__main__":  # pragma: no cover - manual invocation path
    main()
