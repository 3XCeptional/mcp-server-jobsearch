"""Page-owning DOM extraction for Seek (seek.com.au) job search and detail pages.

Mirrors `job_pages.py`'s structure and discipline exactly, adapted for
Seek's own markup conventions. Like `job_pages.py`, this module directly
drives a Playwright `Page` (via a `PageNavigator`), so it is not
browser-free. The pure DOM-parsing logic is factored into module-level
functions (`parse_seek_job_detail_from_page`, `parse_seek_search_results_from_page`)
that take a bare Playwright `Page`, so they can be unit tested against a
fixture loaded into a real headless browser without a live
`ScrapingSession`/`PageNavigator`.

[INFERENCE] The `data-automation` selectors below are Seek's known, stable
convention (more reliable in general than Indeed's `data-testid` churn) but
are not verified against a live Seek response captured in this codebase -
same inference caveat `seek_search_urls.py` already documents for the
search URL scheme. Each selector's fallback exists precisely because of
that uncertainty.

Noise stripping: Seek's own page chrome uses different boilerplate labels
than Indeed's ("Quick apply", "SEEK Promoted", "Save" without "this job",
etc.). `text.strip_indeed_noise`'s noise-label set is Indeed-specific
(exact-match phrases like "save this job", "employer active") and doesn't
cover Seek's phrasing, so extending it there would pollute an
Indeed-named, Indeed-owned module with Seek-specific knowledge. Instead
this module defines its own small `_strip_seek_noise` using the same
line-filtering approach as `text.py`, kept local to this file rather than
touching `text.py`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from indeed_mcp_server.contracts import ExtractionError, JobDetail, JobSummary
from indeed_mcp_server.navigation import PageNavigator
from indeed_mcp_server.seek_identifiers import normalize_seek_job_id, seek_job_view_url

if TYPE_CHECKING:  # pragma: no cover - typing only
    from playwright.async_api import Locator, Page

logger = logging.getLogger(__name__)

# --- Seek page chrome noise stripping ---------------------------------------
#
# Seek's own boilerplate labels, distinct from Indeed's (see module
# docstring for why this isn't in text.py). Exact-match against a
# stripped/lowercased line, same discipline as text._is_noise_line, to
# avoid stripping legitimate description text that happens to contain one
# of these words mid-sentence.
_SEEK_EXACT_NOISE_LINES: frozenset[str] = frozenset(
    {
        "quick apply",
        "save",
        "saved",
        "seek promoted",
        "promoted",
        "report this job ad",
        "report this job",
    }
)


def _is_seek_noise_line(line: str) -> bool:
    stripped = line.strip().lower()
    if not stripped:
        return False
    return stripped in _SEEK_EXACT_NOISE_LINES


def _strip_seek_noise(text: str) -> str:
    """Strip Seek page chrome noise from extracted description text, line by line."""
    lines = text.splitlines()
    filtered = [line for line in lines if not _is_seek_noise_line(line)]
    return "\n".join(filtered)


# --- Job detail page selectors ----------------------------------------------
#
# Seek's data-automation convention for a job detail ("job") page.
_TITLE_SELECTOR = '[data-automation="job-detail-title"]'
_TITLE_FALLBACK_SELECTOR = "h1"

_COMPANY_SELECTOR = '[data-automation="advertiser-name"]'
_COMPANY_FALLBACK_SELECTOR = '[data-automation="job-detail-company"]'

_LOCATION_SELECTOR = '[data-automation="job-detail-location"]'
_LOCATION_FALLBACK_SELECTOR = '[data-automation="job-detail-locations"]'

_DESCRIPTION_SELECTOR = '[data-automation="jobAdDetails"]'

# No single stable data-automation attribute is documented for salary/work
# type the way title/company/location are - best-effort, missing match is
# "not posted" (None), not an extraction failure, mirroring job_pages.py.
_SALARY_SELECTOR = '[data-automation="job-detail-salary"]'
_JOB_TYPE_SELECTOR = '[data-automation="job-detail-work-type"]'

# --- Search-results page selectors ------------------------------------------
#
# [INFERENCE] Seek has used both of these shapes for its result-card
# container, so both are tried, mirroring job_pages.py's
# _RESULT_CARD_SELECTORS fallback-chain pattern.
_RESULT_CARD_SELECTORS = ('[data-automation="normalJob"]', '[data-automation="job-card"]')
_CARD_TITLE_SELECTOR = '[data-automation="jobTitle"]'
_CARD_COMPANY_SELECTOR = '[data-automation="jobCompany"]'
_CARD_LOCATION_SELECTOR = '[data-automation="jobLocation"]'
_CARD_SNIPPET_SELECTOR = '[data-automation="jobShortDescription"]'
_CARD_POSTED_SELECTOR = '[data-automation="jobListingDate"]'


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


async def parse_seek_job_detail_from_page(page: "Page", *, job_id: str, url: str) -> JobDetail:
    """Extract a JobDetail from an already-loaded Seek job detail page.

    This is the pure DOM-parsing half of `SeekJobPageReader.read_job_detail`,
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
    description = _strip_seek_noise(description_raw)

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
    """Best-effort extraction of the Seek numeric job id from a result card.

    Tries a `data-job-id` attribute on the card container first ([INFERENCE]
    - a plausible, unverified real Seek markup attribute, same inference
    caveat as the rest of this module's selectors). Falls back to parsing
    the title link's href, taking only the URL's path component via
    `urlparse` before handing it to `normalize_seek_job_id`: a real anchor
    href commonly carries tracking query parameters after the job id (e.g.
    "/job/93326286?type=standout"), which would otherwise make
    `normalize_seek_job_id`'s strict fragment-fullmatch reject an
    otherwise-valid id.
    """
    try:
        raw_id = await card.get_attribute("data-job-id")
        if raw_id:
            try:
                return normalize_seek_job_id(raw_id)
            except ValueError:
                pass
    except Exception:  # pragma: no cover - defensive against a torn-down page
        pass

    link = card.locator(f'{_CARD_TITLE_SELECTOR} a, a{_CARD_TITLE_SELECTOR}')
    try:
        if await link.count() > 0:
            href = await link.first.get_attribute("href")
            if href:
                path_only = urlparse(href).path
                try:
                    return normalize_seek_job_id(path_only)
                except ValueError:
                    return None
    except Exception:  # noqa: BLE001 - a malformed href just means "skip this card"
        pass
    return None


async def parse_seek_search_results_from_page(page: "Page") -> list[JobSummary]:
    """Extract JobSummary rows from an already-loaded Seek search page.

    Cards that don't parse (missing job id, missing title) are skipped
    rather than raising, since one malformed card should not sink an entire
    batch of otherwise-good results. The number skipped is logged.

    Raises:
        ExtractionError: if literally zero elements match ANY of
            `_RESULT_CARD_SELECTORS`. This is a total structural mismatch,
            not a legitimate empty search - the same false-safe lesson
            already applied on the Indeed side in
            `job_pages.parse_search_results_from_page`: silently returning
            `[]` here would be indistinguishable from a real zero-results
            search to the caller if the page handed to this parser is
            actually a bot-check/login wall. NOTE: this module has no
            independent signal (e.g. Seek's own "no matching jobs" markup)
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
                url=seek_job_view_url(job_id),
                posted=posted,
            )
        )

    if skipped:
        logger.info("skipped %d unparsable Seek search result card(s)", skipped)

    return results


def _navigator_page(navigator: Any) -> "Page":
    """Fetch the live Playwright Page a navigator is driving.

    Mirrors job_pages._navigator_page exactly - see that function's
    docstring for why the private `_session` attribute is tried alongside
    the (currently nonexistent) public `session` name.
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


class SeekJobPageReader:
    """Reads job data out of the DOM of already-navigated Seek pages."""

    def __init__(self, navigator: PageNavigator) -> None:
        self._navigator = navigator

    async def read_job_detail(self, job_id: str) -> JobDetail:
        """Navigate to `job_id`'s Seek job page and extract its full detail."""
        url = seek_job_view_url(job_id)
        await self._navigator.goto(url)
        page = _navigator_page(self._navigator)
        return await parse_seek_job_detail_from_page(page, job_id=job_id, url=url)

    async def read_search_results(self) -> list[JobSummary]:
        """Read job result cards on the currently-loaded Seek search page."""
        page = _navigator_page(self._navigator)
        return await parse_seek_search_results_from_page(page)
