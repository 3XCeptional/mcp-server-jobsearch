"""Indeed-specific search/pagination constants.

Browser-free. Must not import from any other indeed_mcp_server module (other
than the fact that nothing here needs to), so it stays trivially unit
testable and safe for both the URL-building layer and the orchestration
layer to depend on.

Where a constant is a verified fact about Indeed's real site (its query
param names, for instance) that is documented at the call site in
`search_urls.py`/`identifiers.py`. The constants below are reasonable,
commonly-cited estimates rather than values scraped and confirmed live -
each is commented to say so.
"""

from __future__ import annotations

# Indeed's search results page is widely reported to return 15 job cards per
# page of results (its `start=` offset increments in steps of this size).
# This is a reasonable estimate based on common observation of Indeed's
# public search pages, not a value verified against a live response in this
# codebase - Indeed has changed this before and may change it again.
RESULTS_PER_PAGE = 15

# The path segment for Indeed's job search page, used when building/
# recognizing search URLs (paired with the `/jobs` host route that
# `search_urls.build_job_search_url` targets).
JOB_SEARCH_PATH = "/jobs"

# A hard ceiling on how many pages a single `search_jobs` call will walk,
# independent of `max_results`. This exists purely to stop a pathological
# `max_results` value (or a site change that stops returning 0 to signal
# "no more results") from paginating forever.
MAX_SEARCH_PAGES = 50


def next_start_offset(current_start: int, results_per_page: int = RESULTS_PER_PAGE) -> int:
    """Compute the `start=` offset for the page after `current_start`.

    Indeed paginates by a numeric `start` offset rather than a page number,
    so "next page" is just "current offset plus one page's worth of
    results".
    """
    if current_start < 0:
        raise ValueError("current_start must not be negative")
    if results_per_page <= 0:
        raise ValueError("results_per_page must be positive")
    return current_start + results_per_page


def pages_needed_for(max_results: int, results_per_page: int = RESULTS_PER_PAGE) -> int:
    """Estimate how many pages of `results_per_page` are needed for `max_results`.

    This is an upper bound used for planning/looping, not a guarantee - a
    real page can return fewer results than `results_per_page` (end of
    results, filtered-out listings), which callers must still detect and
    stop on independently rather than trusting this count alone.
    """
    if max_results <= 0:
        raise ValueError("max_results must be positive")
    if results_per_page <= 0:
        raise ValueError("results_per_page must be positive")
    full_pages, remainder = divmod(max_results, results_per_page)
    return full_pages + (1 if remainder else 0)
