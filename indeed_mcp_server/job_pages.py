"""Page-owning DOM extraction for Indeed job search and job detail pages.

This module directly drives a Playwright `Page` (via a `PageNavigator`), so
unlike `identifiers.py`/`search_urls.py`/`text.py`/`job_policy.py` it is not
browser-free. The actual DOM-parsing logic is factored into module-level
functions (`parse_job_detail_from_page`, `parse_search_results_from_page`)
that take a bare Playwright `Page` directly, so they can be unit tested
against a fixture loaded into a real headless browser without needing a
live `ScrapingSession`/`PageNavigator` at all.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from indeed_mcp_server.contracts import ExtractionError, JobDetail, JobSummary
from indeed_mcp_server.identifiers import job_view_url, normalize_job_id
from indeed_mcp_server.navigation import PageNavigator
from indeed_mcp_server.text import strip_indeed_noise

if TYPE_CHECKING:  # pragma: no cover - typing only
    from playwright.async_api import Locator, Page

logger = logging.getLogger(__name__)

# Indeed's real, known-stable-ish testids/selectors for a job detail
# ("viewjob") page. Used as the primary selector for each field, with a
# looser text-based fallback below for when Indeed A/B tests or reshuffles
# markup.
_TITLE_SELECTOR = 'h1[data-testid="jobsearch-JobInfoHeader-title"]'
_TITLE_FALLBACK_SELECTOR = "h1"

_COMPANY_SELECTOR = 'div[data-company-name="true"]'
_COMPANY_FALLBACK_SELECTOR = '[data-testid="inlineHeader-companyName"]'

_LOCATION_SELECTOR = 'div[data-testid="inlineHeader-companyLocation"]'
_LOCATION_FALLBACK_SELECTOR = '[data-testid="job-location"]'

_DESCRIPTION_SELECTOR = "#jobDescriptionText"

# Indeed doesn't expose one single stable testid for salary/job-type the way
# it does for title/company/location - these are best-effort, and a missing
# match is treated as "not posted" (None) rather than an extraction failure.
_SALARY_SELECTOR = '[data-testid="attribute_snippet_testid"]'
_JOB_TYPE_SELECTOR = '[data-testid="attribute_snippet_testid"]:has-text("time")'

# Search-results ("jobs") page: Indeed has used both of these as the result
# card container at different times, so both are tried.
_RESULT_CARD_SELECTORS = ("div.job_seen_beacon", "td.resultContent")
_CARD_TITLE_SELECTOR = 'h2.jobTitle a, a[data-jk]'
_CARD_COMPANY_SELECTOR = '[data-testid="company-name"], span.companyName'
_CARD_LOCATION_SELECTOR = '[data-testid="text-location"], div.companyLocation'
_CARD_SNIPPET_SELECTOR = '[data-testid="jobsnippet_footer"], div.job-snippet, table.jobCardShelfContainer'
_CARD_POSTED_SELECTOR = '[data-testid="myJobsStateDate"], span.date'


async def _text_or_none(locator: "Locator") -> str | None:
    """Return the stripped text of the first match, or None if there is none."""
    try:
        count = await locator.count()
    except Exception:  # pragma: no cover - defensive against a torn-down page
        return None
    if count == 0:
        return None
    try:
        text = await locator.first.text_content()
    except Exception:  # pragma: no cover - defensive against a stale handle
        return None
    if text is None:
        return None
    stripped = text.strip()
    return stripped or None


async def _first_matching_text(page: "Page", *selectors: str) -> str | None:
    """Try each selector in order, returning the first non-empty text found."""
    for selector in selectors:
        text = await _text_or_none(page.locator(selector))
        if text:
            return text
    return None


async def parse_job_detail_from_page(page: "Page", *, job_id: str, url: str) -> JobDetail:
    """Extract a JobDetail from an already-loaded Indeed job detail page.

    This is the pure DOM-parsing half of `JobPageReader.read_job_detail`,
    factored out so it can be exercised against a fixture page in tests
    without a real `ScrapingSession`/`PageNavigator`.

    Raises:
        ExtractionError: if the title or description can't be found at all.
            A `JobDetail` is never returned with a fabricated/empty required
            field - callers get a clear failure instead.
    """
    title = await _first_matching_text(page, _TITLE_SELECTOR, _TITLE_FALLBACK_SELECTOR)
    if not title:
        raise ExtractionError(f"could not find a job title on {url!r}")

    description_raw = await _text_or_none(page.locator(_DESCRIPTION_SELECTOR))
    if not description_raw:
        raise ExtractionError(f"could not find a job description on {url!r}")
    description = strip_indeed_noise(description_raw)

    company = await _first_matching_text(page, _COMPANY_SELECTOR, _COMPANY_FALLBACK_SELECTOR)
    location = await _first_matching_text(page, _LOCATION_SELECTOR, _LOCATION_FALLBACK_SELECTOR)
    salary = await _text_or_none(page.locator(_SALARY_SELECTOR))
    job_type = await _text_or_none(page.locator(_JOB_TYPE_SELECTOR))

    return JobDetail(
        job_id=job_id,
        title=title,
        company=company or "",
        location=location or "",
        description=description,
        url=url,
        salary=salary,
        job_type=job_type,
    )


async def _extract_job_id_from_card(card: "Locator") -> str | None:
    """Best-effort extraction of the Indeed job id (`jk`) from a result card."""
    link = card.locator("a[data-jk]")
    try:
        if await link.count() > 0:
            jk = await link.first.get_attribute("data-jk")
            if jk:
                try:
                    return normalize_job_id(jk)
                except ValueError:
                    pass  # fall through to the href-based fallback below
    except Exception:  # pragma: no cover - defensive
        pass

    link = card.locator("h2.jobTitle a, a[href*='jk=']")
    try:
        if await link.count() > 0:
            href = await link.first.get_attribute("href")
            if href:
                return normalize_job_id(href)
    except Exception:  # noqa: BLE001 - a malformed href just means "skip this card"
        pass
    return None


async def parse_search_results_from_page(page: "Page") -> list[JobSummary]:
    """Extract JobSummary rows from an already-loaded Indeed search page.

    Cards that don't parse (missing job id, missing title) are skipped
    rather than raising, since one malformed card should not sink an entire
    batch of otherwise-good results. The number skipped is logged.

    Raises:
        ExtractionError: if literally zero elements match ANY of
            `_RESULT_CARD_SELECTORS`. This is a total structural mismatch,
            not a legitimate empty search - `ensure_logged_in` treats a
            challenge/login wall as best-effort and never blocks session
            creation, so a Cloudflare interstitial or login page can be
            handed straight to this parser. Silently returning `[]` in
            that case would be indistinguishable from a real zero-results
            search to the caller. NOTE: this module has no independent
            signal (e.g. Indeed's own "no jobs match your search" markup)
            to positively confirm a genuine empty search, so a real
            zero-results page that happens to render none of
            `_RESULT_CARD_SELECTORS` either will also raise here rather
            than returning `[]`. That is a known, honest limitation of the
            current selector list, not an attempt to paper over it.
    """
    cards: "Locator | None" = None
    for selector in _RESULT_CARD_SELECTORS:
        candidate = page.locator(selector)
        if await candidate.count() > 0:
            cards = candidate
            break

    if cards is None:
        raise ExtractionError(
            "no search-result card containers found at all (tried "
            f"{_RESULT_CARD_SELECTORS!r}) - page may be a bot-check/login "
            "wall rather than a genuine empty search"
        )

    count = await cards.count()
    results: list[JobSummary] = []
    skipped = 0

    for index in range(count):
        card = cards.nth(index)
        job_id = await _extract_job_id_from_card(card)
        title = await _text_or_none(card.locator(_CARD_TITLE_SELECTOR))
        if not job_id or not title:
            skipped += 1
            continue

        company = await _text_or_none(card.locator(_CARD_COMPANY_SELECTOR)) or ""
        location = await _text_or_none(card.locator(_CARD_LOCATION_SELECTOR)) or ""
        snippet = await _text_or_none(card.locator(_CARD_SNIPPET_SELECTOR)) or ""
        posted = await _text_or_none(card.locator(_CARD_POSTED_SELECTOR))

        results.append(
            JobSummary(
                job_id=job_id,
                title=title,
                company=company,
                location=location,
                snippet=snippet,
                url=job_view_url(job_id),
                posted=posted,
            )
        )

    if skipped:
        logger.info("skipped %d unparsable search result card(s)", skipped)

    return results


def _navigator_page(navigator: Any) -> "Page":
    """Fetch the live Playwright Page a navigator is driving.

    The contract for `PageNavigator` (leaf 1.2.1) documents `goto()` but not
    a page accessor. `ScrapingSession.page` is the documented source of
    truth, and a navigator is constructed from a session
    (`PageNavigator(session)`); the actual implementation stores it as the
    private `navigator._session` rather than a public `navigator.session`.
    Tries the public name first (in case a future revision adds one), then
    the private attribute the current implementation actually uses, then a
    direct `navigator.page` as a last resort.
    """
    for attr_path in ("session", "_session"):
        session = getattr(navigator, attr_path, None)
        if session is not None and hasattr(session, "page"):
            return session.page
    if hasattr(navigator, "page"):
        return navigator.page
    raise AttributeError(
        "PageNavigator instance exposes no reachable Playwright Page via "
        "`.session.page`, `._session.page`, or `.page`"
    )


class JobPageReader:
    """Reads job data out of the DOM of already-navigated Indeed pages."""

    def __init__(self, navigator: PageNavigator) -> None:
        self._navigator = navigator

    async def read_job_detail(self, job_id: str) -> JobDetail:
        """Navigate to `job_id`'s viewjob page and extract its full detail."""
        url = job_view_url(job_id)
        await self._navigator.goto(url)
        page = _navigator_page(self._navigator)
        return await parse_job_detail_from_page(page, job_id=job_id, url=url)

    async def read_search_results(self) -> list[JobSummary]:
        """Read job result cards on the currently-loaded search page."""
        page = _navigator_page(self._navigator)
        return await parse_search_results_from_page(page)
