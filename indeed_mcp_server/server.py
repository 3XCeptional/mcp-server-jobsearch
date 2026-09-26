"""MCP entrypoint: registers Indeed job tools on a FastMCP server.

The `mcp` SDK package renamed `FastMCP` (`mcp.server.fastmcp`) to
`MCPServer` (`mcp.server.mcpserver`) in its 2.x line - `pyproject.toml` pins
`mcp>=1.2.0`, which spans both. Import whichever name is actually available
at runtime rather than hardcoding the pre-2.x path, aliased to `FastMCP`
either way since that's the name the rest of this module (and the
linkedin-mcp-server reference project this mirrors) uses.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

try:
    from mcp.server.fastmcp import FastMCP
except ModuleNotFoundError:
    from mcp.server.mcpserver import MCPServer as FastMCP  # mcp>=2.0

from indeed_mcp_server.contracts import ApplicantProfile
from indeed_mcp_server.extractor import IndeedExtractor, SeekExtractor
from indeed_mcp_server.identifiers import normalize_job_id
from indeed_mcp_server.seek_identifiers import normalize_seek_job_id
from indeed_mcp_server.session_state import SessionManager

# Constructed eagerly at import time, but cheap: `SessionManager.__init__` and
# `IndeedExtractor.__init__` only store configuration - the actual browser
# session (and any Playwright/Chromium process) is created lazily, inside
# `SessionManager.get_or_create_session()`, the first time a tool call
# actually needs one.
extractor = IndeedExtractor(SessionManager())

# Seek gets its own SessionManager, pointed at its own browser profile
# directory - Seek and Indeed are different sites and must not share
# cookies/session state, so this deliberately does not reuse the
# default `~/.indeed-mcp-server/browser-profile` directory Indeed's
# SessionManager() call above defaults to.
_SEEK_USER_DATA_DIR = Path.home() / ".indeed-mcp-server" / "seek-browser-profile"
seek_extractor = SeekExtractor(SessionManager(_SEEK_USER_DATA_DIR))

mcp = FastMCP("indeed-mcp-server")


def register_job_tools(mcp: FastMCP, extractor: IndeedExtractor) -> None:
    """Register the 4 Indeed job tools on `mcp`, delegating to `extractor`."""

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
        job_id = normalize_job_id(job_id)
        return await extractor.get_job_details(job_id)

    @mcp.tool(name="apply_to_job")
    async def apply_to_job(
        job_id: str,
        full_name: str,
        email: str,
        phone: str,
        resume_path: str,
        location: str = "Sydney, NSW",
        cover_letter_path: str = "",
        screening_answers_json: str = "{}",
    ) -> dict:
        """Apply to an Indeed job, filling only the fields given here.

        Mechanical only: this tool never decides whether the candidate
        should apply, never fact-checks, and never invents a field value -
        the calling agent owns that judgment and supplies every fact via
        these parameters. It never creates an account, never bypasses a
        CAPTCHA, and stops (reporting `blocked_reason`) rather than
        guessing at anything it isn't given.

        `screening_answers_json` is a JSON object string mapping a
        screening question's field label/name to its answer, since MCP
        tool parameters must be flat JSON-primitive types rather than a
        nested dict.
        """
        job_id = normalize_job_id(job_id)
        screening_answers = json.loads(screening_answers_json) if screening_answers_json else {}
        if not isinstance(screening_answers, dict):
            raise ValueError(
                "screening_answers_json must decode to a JSON object (dict), got "
                f"{type(screening_answers).__name__}"
            )
        profile = ApplicantProfile(
            full_name=full_name,
            email=email,
            phone=phone,
            resume_path=resume_path,
            location=location,
            cover_letter_path=cover_letter_path or None,
            screening_answers=screening_answers or None,
        )
        result = await extractor.apply_to_job(job_id, profile)
        return dataclasses.asdict(result)

    @mcp.tool(name="close_session")
    async def close_session() -> str:
        """Close the underlying browser session, releasing the Chromium process."""
        await extractor.close_session()
        return "Indeed session closed."


def register_seek_job_tools(mcp: FastMCP, seek_extractor: SeekExtractor) -> None:
    """Register the Seek job tools on `mcp`, delegating to `seek_extractor`.

    `seek_apply_to_job` is a later leaf's concern, not this one.
    """

    @mcp.tool(name="seek_search_jobs")
    async def seek_search_jobs(
        keywords: str, location: str = "", max_results: int = 20
    ) -> list[dict]:
        """Search Seek for jobs matching `keywords`, optionally near `location`."""
        results = await seek_extractor.search_jobs(
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

    @mcp.tool(name="seek_get_job_details")
    async def seek_get_job_details(job_id: str) -> dict:
        """Fetch full detail (description, salary, job type) for one Seek job id."""
        job_id = normalize_seek_job_id(job_id)
        return await seek_extractor.get_job_details(job_id)

    @mcp.tool(name="seek_close_session")
    async def seek_close_session() -> str:
        """Close Seek's underlying browser session, releasing its Chromium process."""
        await seek_extractor.close_session()
        return "Seek session closed."


register_job_tools(mcp, extractor)
register_seek_job_tools(mcp, seek_extractor)


def main() -> None:
    """Run the MCP server over stdio transport."""
    mcp.run()


if __name__ == "__main__":  # pragma: no cover - manual invocation path
    main()
