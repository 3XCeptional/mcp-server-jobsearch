"""Facade tying session management to the job-scraping orchestrator.

`IndeedExtractor` is the single entry point the MCP tool layer (`server.py`)
talks to. It owns no browser state itself - it asks the `SessionManager` for
a session, builds the page-owning/browser-free collaborators around it, and
delegates.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from jobsearch_mcp_server.apply import JobApplier
from jobsearch_mcp_server.contracts import ApplicantProfile, ApplyResult, JobSummary
from jobsearch_mcp_server.job_pages import JobPageReader
from jobsearch_mcp_server.jobs import JobScraper
from jobsearch_mcp_server.navigation import PageNavigator
from jobsearch_mcp_server.seek_job_pages import SeekJobPageReader
from jobsearch_mcp_server.seek_jobs import SeekJobScraper
from jobsearch_mcp_server.session_state import SessionManager


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

    async def apply_to_job(self, job_id: str, profile: ApplicantProfile) -> ApplyResult:
        """Apply to a job through Indeed, filling only what `profile` supplies.

        Mechanical only: never decides whether the candidate should apply,
        never fact-checks, never invents a field value. See
        `apply.JobApplier` for the full walk-through and its hard-stop
        behaviour (CAPTCHA walls, account-creation walls, external ATS
        handoffs, unanswerable required fields).
        """
        session = await self._session_manager.get_or_create_session()
        navigator = PageNavigator(session)
        applier = JobApplier(navigator)
        return await applier.apply_to_job(job_id, profile)

    async def close_session(self) -> None:
        """Close the underlying browser session, if one is open."""
        await self._session_manager.close()


class SeekExtractor:
    """High-level, session-managed facade over Seek job search/detail.

    Mirrors `IndeedExtractor`'s exact structure. Deliberately has no
    `apply_to_job`/`close_session` split from Indeed's own - the
    `SessionManager` instance handed in at construction is Seek's own
    (a separate browser profile from Indeed's, wired up in `server.py`), so
    this class owns no browser state itself here either.
    """

    def __init__(self, session_manager: SessionManager) -> None:
        self._session_manager = session_manager

    async def _build_scraper(self) -> SeekJobScraper:
        session = await self._session_manager.get_or_create_session()
        navigator = PageNavigator(session)
        reader = SeekJobPageReader(navigator)
        return SeekJobScraper(navigator, reader)

    async def search_jobs(
        self,
        keywords: str,
        location: str | None = None,
        max_results: int = 20,
        **filters: object,
    ) -> list[JobSummary]:
        """Search Seek for jobs, returning JobSummary dataclass instances."""
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
