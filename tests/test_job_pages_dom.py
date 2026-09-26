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
from indeed_mcp_server.job_pages import parse_job_detail_from_page, parse_search_results_from_page

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


# --- parse_search_results_from_page -----------------------------------------
#
# No fixture file existed for the search-results page before this change, so
# these use small hand-built inline HTML snippets, matching the convention
# already used above for the missing-title/description edge cases.

_SEARCH_RESULT_CARD_HTML = """
<html><body>
  <div class="job_seen_beacon">
    <h2 class="jobTitle"><a data-jk="xyz789" href="/rc/clk?jk=xyz789">Security Engineer</a></h2>
    <span data-testid="company-name">Acme Corp</span>
    <div data-testid="text-location">Sydney NSW</div>
    <div data-testid="jobsnippet_footer">Great opportunity for a security engineer.</div>
    <span data-testid="myJobsStateDate">Posted 3 days ago</span>
  </div>
</body></html>
"""

_SEARCH_RESULT_ADVERSARIAL_JK_WITH_VALID_HREF_HTML = """
<html><body>
  <div class="job_seen_beacon">
    <h2 class="jobTitle"><a data-jk="xyz789&amp;evil=<script>" href="https://au.indeed.com/rc/clk?jk=validhref123">Security Engineer</a></h2>
    <span data-testid="company-name">Acme Corp</span>
    <div data-testid="text-location">Sydney NSW</div>
  </div>
</body></html>
"""

_SEARCH_RESULT_ADVERSARIAL_JK_NO_VALID_HREF_HTML = """
<html><body>
  <div class="job_seen_beacon">
    <h2 class="jobTitle"><a data-jk="../../etc/passwd" href="/rc/clk?other=1">Security Engineer</a></h2>
    <span data-testid="company-name">Acme Corp</span>
    <div data-testid="text-location">Sydney NSW</div>
  </div>
</body></html>
"""

_SEARCH_RESULT_UNPARSEABLE_CARD_HTML = """
<html><body>
  <div class="job_seen_beacon">
    <span>A card container with none of the expected title/link markup.</span>
  </div>
</body></html>
"""

_SEARCH_RESULT_BOT_CHECK_HTML = """
<html><body>
  <p>Please verify you are a human before continuing.</p>
</body></html>
"""


def test_parses_a_normal_search_result_card(html_page_runner):
    results = html_page_runner(
        _SEARCH_RESULT_CARD_HTML, lambda page: parse_search_results_from_page(page)
    )
    assert len(results) == 1
    summary = results[0]
    assert summary.job_id == "xyz789"
    assert summary.title == "Security Engineer"
    assert summary.company == "Acme Corp"
    assert summary.location == "Sydney NSW"


def test_adversarial_data_jk_falls_through_to_valid_href(html_page_runner):
    """A crafted `data-jk` attribute (containing `&`, `<script>`, etc.) must

    not be returned as-is - it has to fail `normalize_job_id` validation and
    fall through to the href-based `jk=` fallback, which yields the real,
    safe job id here.
    """
    results = html_page_runner(
        _SEARCH_RESULT_ADVERSARIAL_JK_WITH_VALID_HREF_HTML,
        lambda page: parse_search_results_from_page(page),
    )
    assert len(results) == 1
    assert results[0].job_id == "validhref123"


def test_adversarial_data_jk_with_no_valid_fallback_skips_card(html_page_runner):
    """When both the `data-jk` attribute and the href are unusable, the card

    is skipped entirely (job id can't be resolved by either path) rather
    than leaking the raw adversarial attribute value.
    """
    results = html_page_runner(
        _SEARCH_RESULT_ADVERSARIAL_JK_NO_VALID_HREF_HTML,
        lambda page: parse_search_results_from_page(page),
    )
    assert results == []


def test_containers_found_but_zero_parseable_cards_returns_empty_list(html_page_runner):
    """A real card container that fails to yield a job id/title is skipped,

    not raised - this is the "some cards malformed" case, distinct from
    "no card containers matched at all" below. It legitimately returns []
    when every found container fails to parse.
    """
    results = html_page_runner(
        _SEARCH_RESULT_UNPARSEABLE_CARD_HTML,
        lambda page: parse_search_results_from_page(page),
    )
    assert results == []


def test_no_result_containers_at_all_raises_extraction_error(html_page_runner):
    """A page where NONE of `_RESULT_CARD_SELECTORS` match anything (e.g. a

    bot-check/login wall handed to this parser by best-effort auth) must
    surface as a failure, not silently look like a genuine zero-results
    search.
    """
    with pytest.raises(ExtractionError):
        html_page_runner(
            _SEARCH_RESULT_BOT_CHECK_HTML,
            lambda page: parse_search_results_from_page(page),
        )
