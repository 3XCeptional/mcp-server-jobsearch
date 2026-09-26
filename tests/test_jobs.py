"""Unit tests for JobScraper's pagination/dedupe orchestration in jobs.py.

Genuine coverage gap closed here: `JobScraper.search_jobs`'s dedupe-by-
`job_id` and multi-stop-condition pagination loop had no direct unit test
anywhere in the existing suite (only exercised indirectly, if at all,
through higher-level integration paths). These tests drive it against a
fake navigator/reader pair - `JobScraper` only calls `navigator.goto(url)`
and `reader.read_search_results()` / `reader.read_job_detail(job_id)`, so a
plain duck-typed fake is enough; no Playwright/browser involved anywhere in
this file, and no network access.
"""

from __future__ import annotations

import asyncio
from urllib.parse import parse_qs, urlparse

from jobsearch_mcp_server.contracts import JobDetail, JobSummary
from jobsearch_mcp_server.jobs import JobScraper


def _run(coro):
    return asyncio.run(coro)


def _job(job_id: str) -> JobSummary:
    return JobSummary(
        job_id=job_id,
        title=f"Title {job_id}",
        company="Acme Corp",
        location="Sydney NSW",
        snippet="snippet",
        url=f"https://au.indeed.com/viewjob?jk={job_id}",
    )


class _FakeNavigator:
    """Records every URL `goto()` was called with; never touches a browser."""

    def __init__(self) -> None:
        self.urls_visited: list[str] = []

    async def goto(self, url: str, *args: object, **kwargs: object) -> None:
        self.urls_visited.append(url)

    def start_values(self) -> list[int]:
        starts = []
        for url in self.urls_visited:
            query = parse_qs(urlparse(url).query)
            starts.append(int(query["start"][0]))
        return starts


class _FakeReader:
    """Returns one page of `JobSummary`s per call, from a pre-scripted list.

    Once `pages` is exhausted, further calls return an empty list (as a real
    "no more results" page would), unless `repeat_last` is set, which keeps
    returning the final page forever - used for the MAX_SEARCH_PAGES ceiling
    test, where the fake must never signal "no new results" on its own.
    """

    def __init__(self, pages: list[list[JobSummary]], *, repeat_last: bool = False) -> None:
        self._pages = pages
        self._repeat_last = repeat_last
        self.call_count = 0

    async def read_search_results(self) -> list[JobSummary]:
        index = self.call_count
        self.call_count += 1
        if index < len(self._pages):
            return self._pages[index]
        if self._repeat_last and self._pages:
            return self._pages[-1]
        return []

    async def read_job_detail(self, job_id: str) -> JobDetail:
        return JobDetail(
            job_id=job_id,
            title="Title",
            company="Acme Corp",
            location="Sydney NSW",
            description="description",
            url=f"https://au.indeed.com/viewjob?jk={job_id}",
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


def test_dedupes_overlapping_results_across_pages():
    # Page 1: A, B, C. Page 2: C, D, E (C repeats). Page 3: empty -> stop.
    reader = _FakeReader(
        [
            [_job("a"), _job("b"), _job("c")],
            [_job("c"), _job("d"), _job("e")],
            [],
        ]
    )
    navigator = _FakeNavigator()
    scraper = JobScraper(navigator, reader)

    results = _run(scraper.search_jobs("security analyst", max_results=100))

    assert {r.job_id for r in results} == {"a", "b", "c", "d", "e"}
    assert len(results) == 5  # "c" counted once despite appearing on both pages


def test_stops_as_soon_as_max_results_is_reached_without_extra_pages():
    reader = _FakeReader([[_job("a"), _job("b"), _job("c")], [_job("d"), _job("e")]])
    navigator = _FakeNavigator()
    scraper = JobScraper(navigator, reader)

    results = _run(scraper.search_jobs("security analyst", max_results=2))

    assert len(results) == 2
    assert reader.call_count == 1  # never fetched the second page
    assert len(navigator.urls_visited) == 1


def test_stops_when_a_page_returns_zero_new_results():
    # Page 2 is a complete repeat of page 1 -> zero *new* jobs -> must stop,
    # never reaching page 3 even though it has fresh jobs.
    reader = _FakeReader(
        [
            [_job("a"), _job("b")],
            [_job("a"), _job("b")],
            [_job("z")],
        ]
    )
    navigator = _FakeNavigator()
    scraper = JobScraper(navigator, reader)

    results = _run(scraper.search_jobs("security analyst", max_results=100))

    assert {r.job_id for r in results} == {"a", "b"}
    assert reader.call_count == 2
    assert "z" not in {r.job_id for r in results}


def test_max_results_zero_or_negative_short_circuits_without_any_page_fetch():
    reader = _FakeReader([[_job("a")]])
    navigator = _FakeNavigator()
    scraper = JobScraper(navigator, reader)

    assert _run(scraper.search_jobs("security analyst", max_results=0)) == []
    assert _run(scraper.search_jobs("security analyst", max_results=-5)) == []
    assert reader.call_count == 0
    assert navigator.urls_visited == []


def test_pagination_advances_the_start_offset_by_results_per_page(monkeypatch):
    monkeypatch.setattr("jobsearch_mcp_server.jobs.RESULTS_PER_PAGE", 15)
    reader = _FakeReader(
        [
            [_job("a")],
            [_job("b")],
            [],
        ]
    )
    navigator = _FakeNavigator()
    scraper = JobScraper(navigator, reader)

    _run(scraper.search_jobs("security analyst", max_results=100))

    assert navigator.start_values() == [0, 15, 30]


def test_respects_the_max_search_pages_ceiling(monkeypatch):
    # Reader never runs dry and max_results is unreachable, so only the
    # MAX_SEARCH_PAGES ceiling (patched down to 3 here) can stop the loop.
    monkeypatch.setattr("jobsearch_mcp_server.jobs.MAX_SEARCH_PAGES", 3)
    reader = _InfiniteUniqueReader()
    navigator = _FakeNavigator()
    scraper = JobScraper(navigator, reader)

    results = _run(scraper.search_jobs("security analyst", max_results=1_000_000))

    assert len(results) == 3
    assert reader.call_count == 3
    assert len(navigator.urls_visited) == 3


def test_get_job_details_delegates_straight_to_the_reader():
    reader = _FakeReader([])
    navigator = _FakeNavigator()
    scraper = JobScraper(navigator, reader)

    detail = _run(scraper.get_job_details("abc123"))

    assert detail.job_id == "abc123"
    assert isinstance(detail, JobDetail)
    # get_job_details never navigates via `goto()` itself - the reader owns
    # any navigation it needs.
    assert navigator.urls_visited == []
