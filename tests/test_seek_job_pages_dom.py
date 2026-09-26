"""DOM-parsing tests for seek_job_pages.py against a hand-built fixture page.

Mirrors test_job_pages_dom.py's pattern exactly: loads
tests/fixtures/seek_job_detail_sample.html (hand-written markup, not scraped
from a live page) into a real headless Playwright Chromium page via
`page.set_content()` and asserts the pure `parse_seek_job_detail_from_page`
function extracts the fields correctly. No network access happens anywhere
in this file - `set_content` renders a string directly, and the fixture
domain used in assertions below is a placeholder, not a real Seek host.

No `pytest-asyncio` plugin is installed for this project, so each test uses
the shared `html_page_runner` fixture (tests/conftest.py) rather than
declaring `async def test_...` functions directly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jobsearch_mcp_server.contracts import ExtractionError, JobDetail
from jobsearch_mcp_server.seek_job_pages import (
    parse_seek_job_detail_from_page,
    parse_seek_search_results_from_page,
)

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "seek_job_detail_sample.html"
_FIXTURE_URL = "https://example.test/job/93326286"


def _parse(html_page_runner, html: str, *, job_id: str = "93326286", url: str = _FIXTURE_URL) -> JobDetail:
    return html_page_runner(html, lambda page: parse_seek_job_detail_from_page(page, job_id=job_id, url=url))


def _parse_fixture(html_page_runner) -> JobDetail:
    html = _FIXTURE_PATH.read_text(encoding="utf-8")
    return _parse(html_page_runner, html)


def test_extracts_title_company_location(html_page_runner):
    detail = _parse_fixture(html_page_runner)
    assert detail.title == "Security Analyst"
    assert detail.company == "Acme Corp"
    assert detail.location == "Sydney NSW"


def test_extracts_and_cleans_description(html_page_runner):
    detail = _parse_fixture(html_page_runner)
    assert "looking for a Security Analyst to join our growing team" in detail.description
    assert "SIEM tooling" in detail.description
    # Seek chrome noise present in the fixture must be stripped by the
    # local _strip_seek_noise helper (Seek's own labels, not Indeed's).
    assert "Quick apply" not in detail.description
    assert "SEEK Promoted" not in detail.description


def test_extracts_salary_and_job_type(html_page_runner):
    detail = _parse_fixture(html_page_runner)
    assert detail.salary == "$90,000 - $110,000 a year"
    assert detail.job_type == "Full-time"


def test_job_id_and_url_passthrough(html_page_runner):
    detail = _parse_fixture(html_page_runner)
    assert detail.job_id == "93326286"
    assert detail.url == _FIXTURE_URL


def test_missing_title_raises_extraction_error(html_page_runner):
    html = '<html><body><div data-automation="jobAdDetails">Some description text.</div></body></html>'
    with pytest.raises(ExtractionError):
        _parse(html_page_runner, html)


def test_missing_description_raises_extraction_error(html_page_runner):
    html = '<html><body><h1 data-automation="job-detail-title">Security Analyst</h1></body></html>'
    with pytest.raises(ExtractionError):
        _parse(html_page_runner, html)


# --- parse_seek_search_results_from_page ------------------------------------

_SEARCH_RESULT_CARD_HTML = """
<html><body>
  <article data-automation="normalJob">
    <a data-automation="jobTitle" href="/job/93326286">Security Engineer</a>
    <span data-automation="jobCompany">Acme Corp</span>
    <span data-automation="jobLocation">Sydney NSW</span>
    <span data-automation="jobShortDescription">Great opportunity for a security engineer.</span>
    <span data-automation="jobListingDate">Posted 3d ago</span>
  </article>
</body></html>
"""

_SEARCH_RESULT_UNPARSEABLE_CARD_HTML = """
<html><body>
  <article data-automation="normalJob">
    <span>A card container with none of the expected title/link markup.</span>
  </article>
</body></html>
"""

_SEARCH_RESULT_BOT_CHECK_HTML = """
<html><body>
  <p>Please verify you are a human before continuing.</p>
</body></html>
"""


def test_parses_a_normal_search_result_card(html_page_runner):
    results = html_page_runner(
        _SEARCH_RESULT_CARD_HTML, lambda page: parse_seek_search_results_from_page(page)
    )
    assert len(results) == 1
    summary = results[0]
    assert summary.job_id == "93326286"
    assert summary.title == "Security Engineer"
    assert summary.company == "Acme Corp"
    assert summary.location == "Sydney NSW"


def test_containers_found_but_zero_parseable_cards_returns_empty_list(html_page_runner):
    """A real card container that fails to yield a job id/title is skipped,

    not raised - this is the "some cards malformed" case, distinct from
    "no card containers matched at all" below. It legitimately returns []
    when every found container fails to parse.
    """
    results = html_page_runner(
        _SEARCH_RESULT_UNPARSEABLE_CARD_HTML,
        lambda page: parse_seek_search_results_from_page(page),
    )
    assert results == []


def test_no_result_containers_at_all_raises_extraction_error(html_page_runner):
    """A page where NONE of `_RESULT_CARD_SELECTORS` match anything (e.g. a

    bot-check/login wall) must surface as a failure, not silently look like
    a genuine zero-results search. This is the false-safe lesson from
    Indeed's own audit history (job_pages.parse_search_results_from_page),
    applied here from day one.
    """
    with pytest.raises(ExtractionError):
        html_page_runner(
            _SEARCH_RESULT_BOT_CHECK_HTML,
            lambda page: parse_seek_search_results_from_page(page),
        )
