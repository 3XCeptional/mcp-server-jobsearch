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

import asyncio
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from indeed_mcp_server.contracts import ExtractionError, JobDetail
from indeed_mcp_server.job_pages import parse_job_detail_from_page

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "indeed_job_detail_sample.html"
_FIXTURE_URL = "https://example.test/viewjob?jk=abc123"


async def _parse_html(html: str, *, job_id: str = "abc123", url: str = _FIXTURE_URL) -> JobDetail:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page()
            await page.set_content(html)
            return await parse_job_detail_from_page(page, job_id=job_id, url=url)
        finally:
            await browser.close()


def _parse_fixture() -> JobDetail:
    html = _FIXTURE_PATH.read_text(encoding="utf-8")
    return asyncio.run(_parse_html(html))


def test_extracts_title_company_location():
    detail = _parse_fixture()
    assert detail.title == "Security Analyst"
    assert detail.company == "Acme Corp"
    assert detail.location == "Sydney NSW"


def test_extracts_and_cleans_description():
    detail = _parse_fixture()
    assert "looking for a Security Analyst to join our growing team" in detail.description
    assert "SIEM tooling" in detail.description
    # Chrome noise present in the fixture must be stripped by
    # text.strip_indeed_noise().
    assert "Sponsored" not in detail.description
    assert "Save this job" not in detail.description
    assert "Report this job" not in detail.description


def test_extracts_salary_and_job_type():
    detail = _parse_fixture()
    assert detail.salary == "$90,000 - $110,000 a year"
    assert detail.job_type == "Full-time"


def test_job_id_and_url_passthrough():
    detail = _parse_fixture()
    assert detail.job_id == "abc123"
    assert detail.url == _FIXTURE_URL


def test_missing_title_raises_extraction_error():
    html = "<html><body><div id='jobDescriptionText'>Some description text.</div></body></html>"
    with pytest.raises(ExtractionError):
        asyncio.run(_parse_html(html))


def test_missing_description_raises_extraction_error():
    html = "<html><body><h1>Security Analyst</h1></body></html>"
    with pytest.raises(ExtractionError):
        asyncio.run(_parse_html(html))
