"""Facade tying session management to the job-scraping orchestrator.

`IndeedExtractor` is the single entry point the MCP tool layer (`server.py`)
talks to. It owns no browser state itself - it asks the `SessionManager` for
a session, builds the page-owning/browser-free collaborators around it, and
delegates.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from indeed_mcp_server.contracts import JobSummary
from indeed_mcp_server.job_pages import JobPageReader
from indeed_mcp_server.jobs import JobScraper
from indeed_mcp_server.navigation import PageNavigator
from indeed_mcp_server.session_state import SessionManager


class IndeedExtractor:
    """High-level, session-managed facade over Indeed job search/detail."""

    def __init__(self, session_manager: SessionManager) -> None:
        self._session_manager = session_manager

    async def _build_scraper(self) -> JobScraper:
        session = await self._session_manager.get_or_create_session()
        navigator = PageNavigator(session)
        reader = JobPageReader(navigator)
        return JobScraper(navigator, reader)

    async def search_jobs(
        self,
        keywords: str,
        location: str | None = None,
        max_results: int = 20,
        **filters: object,
    ) -> list[JobSummary]:
        """Search Indeed for jobs, returning JobSummary dataclass instances."""
        scraper = await self._build_scraper()
        return await scraper.search_jobs(keywords, location, max_results, **filters)

    async def get_job_details(self, job_id: str) -> dict[str, Any]:
        """Fetch full detail for a job id, returned as a plain dict.

        A plain dict (rather than the `JobDetail` dataclass) is returned
        because this method is the boundary an MCP tool call crosses, and
        MCP tool results need to be JSON-serializable.
        """
        scraper = await self._build_scraper()
        detail = await scraper.get_job_details(job_id)
        return asdict(detail)

    async def close_session(self) -> None:
        """Close the underlying browser session, if one is open."""
        await self._session_manager.close()
