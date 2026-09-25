"""Browser-free job search orchestration.

`JobScraper` coordinates the page-owning `JobPageReader` and the pure
`search_urls`/`job_policy` helpers to run a paginated Indeed job search.
This module never touches a Playwright `Page` directly - all DOM access is
delegated to the `JobPageReader`/`PageNavigator` it is given.
"""

from __future__ import annotations

import logging

from indeed_mcp_server.contracts import JobDetail, JobSummary
from indeed_mcp_server.job_pages import JobPageReader
from indeed_mcp_server.job_policy import MAX_SEARCH_PAGES, RESULTS_PER_PAGE, next_start_offset
from indeed_mcp_server.navigation import PageNavigator
from indeed_mcp_server.search_urls import build_job_search_url

logger = logging.getLogger(__name__)


class JobScraper:
    """Runs Indeed job searches and fetches individual job details."""

    def __init__(self, navigator: PageNavigator, reader: JobPageReader) -> None:
        self._navigator = navigator
        self._reader = reader

    async def search_jobs(
        self,
        keywords: str,
        location: str | None = None,
        max_results: int = 20,
        **filters: object,
    ) -> list[JobSummary]:
        """Search Indeed for `keywords`, paginating until `max_results` is met.

        Pagination walks Indeed's `start=` offset one page
        (`job_policy.RESULTS_PER_PAGE` results) at a time, stopping as soon
        as one of the following holds:
          - `max_results` unique jobs have been collected,
          - a page returns zero new (not-already-seen) results, or
          - `job_policy.MAX_SEARCH_PAGES` pages have been walked (a safety
            ceiling against an infinite loop if Indeed's pagination
            behaviour changes).

        Results are deduped by `job_id` and the returned list is capped at
        `max_results`.
        """
        if max_results <= 0:
            return []

        collected: dict[str, JobSummary] = {}
        start = 0

        for _page_number in range(MAX_SEARCH_PAGES):
            url = build_job_search_url(
                keywords,
                location,
                start=start,
                **filters,  # type: ignore[arg-type]
            )
            await self._navigator.goto(url)
            page_results = await self._reader.read_search_results()

            new_count = 0
            for summary in page_results:
                if summary.job_id not in collected:
                    collected[summary.job_id] = summary
                    new_count += 1

            if new_count == 0:
                logger.info("search page at start=%d returned no new results, stopping", start)
                break

            if len(collected) >= max_results:
                break

            start = next_start_offset(start, RESULTS_PER_PAGE)

        return list(collected.values())[:max_results]

    async def get_job_details(self, job_id: str) -> JobDetail:
        """Fetch full detail for a single job by id."""
        return await self._reader.read_job_detail(job_id)
