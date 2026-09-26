"""Unit tests for SeekJobScraper's pagination-delay behaviour in seek_jobs.py.

Mirrors the equivalent `jobs.py`/`test_jobs.py` coverage: `SeekJobScraper`
paginates by an incrementing `page=` number rather than Indeed's `start=`
offset, but the throttling contract is identical (no delay before the first
page, no delay after the page that ends the loop). Uses the same plain
duck-typed fakes as `test_jobs.py` - no Playwright/browser involved, no
network access.
"""

from __future__ import annotations

import asyncio

from jobsearch_mcp_server.contracts import JobDetail, JobSummary
from jobsearch_mcp_server.job_policy import PAGINATION_DELAY_SECONDS
from jobsearch_mcp_server.seek_jobs import SeekJobScraper


def _run(coro):
    return asyncio.run(coro)


def _job(job_id: str) -> JobSummary:
    return JobSummary(
        job_id=job_id,
        title=f"Title {job_id}",
        company="Acme Corp",
        location="Sydney NSW",
        snippet="snippet",
        url=f"https://www.seek.com.au/job/{job_id}",
    )


class _FakeNavigator:
    """Records every URL `goto()` was called with; never touches a browser."""

    def __init__(self) -> None:
        self.urls_visited: list[str] = []

    async def goto(self, url: str, *args: object, **kwargs: object) -> None:
        self.urls_visited.append(url)


class _FakeReader:
    """Returns one page of `JobSummary`s per call, from a pre-scripted list."""

    def __init__(self, pages: list[list[JobSummary]]) -> None:
        self._pages = pages
        self.call_count = 0

    async def read_search_results(self) -> list[JobSummary]:
        index = self.call_count
        self.call_count += 1
        if index < len(self._pages):
            return self._pages[index]
        return []

    async def read_job_detail(self, job_id: str) -> JobDetail:
        return JobDetail(
            job_id=job_id,
            title="Title",
            company="Acme Corp",
            location="Sydney NSW",
            description="description",
            url=f"https://www.seek.com.au/job/{job_id}",
        )


class _InfiniteUniqueReader:
    """Always returns exactly one brand-new job per call - never dedupes,
    never runs dry - so only the MAX_SEARCH_PAGES ceiling can stop it.
    """

    def __init__(self) -> None:
        self.call_count = 0

    async def read_search_results(self) -> list[JobSummary]:
        job = _job(f"job{self.call_count}")
        self.call_count += 1
        return [job]


def test_single_page_result_incurs_no_pagination_delay(monkeypatch):
    sleep_calls: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr("jobsearch_mcp_server.seek_jobs.asyncio.sleep", _fake_sleep)

    reader = _FakeReader([[_job("a"), _job("b"), _job("c")]])
    navigator = _FakeNavigator()
    scraper = SeekJobScraper(navigator, reader)

    results = _run(scraper.search_jobs("security analyst", max_results=2))

    assert len(results) == 2
    assert reader.call_count == 1
    assert sleep_calls == []


def test_three_page_search_sleeps_exactly_twice_between_pages(monkeypatch):
    sleep_calls: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr("jobsearch_mcp_server.seek_jobs.asyncio.sleep", _fake_sleep)

    reader = _FakeReader(
        [
            [_job("a")],
            [_job("b")],
            [_job("c")],
        ]
    )
    navigator = _FakeNavigator()
    scraper = SeekJobScraper(navigator, reader)

    results = _run(scraper.search_jobs("security analyst", max_results=3))

    assert len(results) == 3
    assert reader.call_count == 3
    assert sleep_calls == [PAGINATION_DELAY_SECONDS, PAGINATION_DELAY_SECONDS]


def test_max_search_pages_ceiling_does_not_sleep_after_the_final_page(monkeypatch):
    monkeypatch.setattr("jobsearch_mcp_server.seek_jobs.MAX_SEARCH_PAGES", 3)
    sleep_calls: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr("jobsearch_mcp_server.seek_jobs.asyncio.sleep", _fake_sleep)

    reader = _InfiniteUniqueReader()
    navigator = _FakeNavigator()
    scraper = SeekJobScraper(navigator, reader)

    _run(scraper.search_jobs("security analyst", max_results=1_000_000))

    assert sleep_calls == [PAGINATION_DELAY_SECONDS, PAGINATION_DELAY_SECONDS]
