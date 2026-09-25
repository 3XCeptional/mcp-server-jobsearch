"""DOM-parsing tests for job_pages.py against a hand-built fixture page.

Loads tests/fixtures/indeed_job_detail_sample.html (hand-written markup, not
scraped from a live page) into a real headless Playwright Chromium page via
`page.set_content()` and asserts the pure `parse_job_detail_from_page`
function extracts the fields correctly. No network access happens anywhere
in this file - `set_content` renders a string directly, and the fixture
domain used in assertions below is a placeholder, not a real Indeed host.

No `pytest-asyncio` plugin is installed for this project, so each test
drives its own event loop with a plain `asyncio.run()` around an async
helper, rather than declaring `async def test_...` functions directly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from indeed_mcp_server.contracts import ExtractionError, JobDetail
from indeed_mcp_server.job_pages import parse_job_detail_from_page

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "indeed_job_detail_sample.html"
_FIXTURE_URL = "https://example.test/viewjob?jk=abc123"


def _parse(html_page_runner, html: str, *, job_id: str = "abc123", url: str = _FIXTURE_URL) -> JobDetail:
    return html_page_runner(html, lambda page: parse_job_detail_from_page(page, job_id=job_id, url=url))


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
    # Chrome noise present in the fixture must be stripped by
    # text.strip_indeed_noise().
    assert "Sponsored" not in detail.description
    assert "Save this job" not in detail.description
    assert "Report this job" not in detail.description


def test_extracts_salary_and_job_type(html_page_runner):
    detail = _parse_fixture(html_page_runner)
    assert detail.salary == "$90,000 - $110,000 a year"
    assert detail.job_type == "Full-time"


def test_job_id_and_url_passthrough(html_page_runner):
    detail = _parse_fixture(html_page_runner)
    assert detail.job_id == "abc123"
    assert detail.url == _FIXTURE_URL


def test_missing_title_raises_extraction_error(html_page_runner):
    html = "<html><body><div id='jobDescriptionText'>Some description text.</div></body></html>"
    with pytest.raises(ExtractionError):
        _parse(html_page_runner, html)


def test_missing_description_raises_extraction_error(html_page_runner):
    html = "<html><body><h1>Security Analyst</h1></body></html>"
    with pytest.raises(ExtractionError):
        _parse(html_page_runner, html)
