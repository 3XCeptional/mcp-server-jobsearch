"""Browser-free job search orchestration for Seek.

`SeekJobScraper` coordinates the page-owning `SeekJobPageReader` and the
pure `seek_search_urls` helper to run a paginated Seek job search. Mirrors
`jobs.JobScraper` exactly, except pagination increments a page NUMBER
(`build_seek_search_url`'s `page` parameter, default 1) rather than
Indeed's `start=` result offset - `seek_search_urls.py` (leaf 3.1) confirms
Seek's search URL scheme uses `page`, not an offset. This module never
touches a Playwright `Page` directly - all DOM access is delegated to the
`SeekJobPageReader`/`PageNavigator` it is given.
"""

from __future__ import annotations

import logging

from jobsearch_mcp_server.contracts import JobDetail, JobSummary
from jobsearch_mcp_server.job_policy import MAX_SEARCH_PAGES
from jobsearch_mcp_server.navigation import PageNavigator
from jobsearch_mcp_server.seek_job_pages import SeekJobPageReader
from jobsearch_mcp_server.seek_search_urls import build_seek_search_url

logger = logging.getLogger(__name__)


class SeekJobScraper:
    """Runs Seek job searches and fetches individual job details."""

    def __init__(self, navigator: PageNavigator, reader: SeekJobPageReader) -> None:
        self._navigator = navigator
        self._reader = reader

    async def search_jobs(
        self,
        keywords: str,
        location: str | None = None,
        max_results: int = 20,
        **filters: object,
    ) -> list[JobSummary]:
        """Search Seek for `keywords`, paginating until `max_results` is met.

        Pagination walks Seek's `page=` number one page at a time (starting
        at page 1, `build_seek_search_url`'s own default), stopping as soon
        as one of the following holds:
          - `max_results` unique jobs have been collected,
          - a page returns zero new (not-already-seen) results, or
          - `job_policy.MAX_SEARCH_PAGES` pages have been walked (the same
            safety ceiling `jobs.JobScraper` uses, reused here rather than
            duplicated since it isn't Indeed-specific - it's just "stop
            walking pages eventually" independent of the pagination scheme).

        Results are deduped by `job_id` and the returned list is capped at
        `max_results`.
        """
        if max_results <= 0:
            return []

        collected: dict[str, JobSummary] = {}
        page_number = 1

        for _page_index in range(MAX_SEARCH_PAGES):
            url = build_seek_search_url(
                keywords,
                location,
                page=page_number,
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
                logger.info("search page=%d returned no new results, stopping", page_number)
                break

            if len(collected) >= max_results:
                break

            page_number += 1

        return list(collected.values())[:max_results]

    async def get_job_details(self, job_id: str) -> JobDetail:
        """Fetch full detail for a single job by id."""
        return await self._reader.read_job_detail(job_id)
