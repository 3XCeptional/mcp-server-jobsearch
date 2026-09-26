"""Seek (seek.com.au) job search URL construction.

Browser-free. May import `seek_identifiers` but nothing heavier (no browser
or session modules), so it stays usable by pure URL-building callers and
tests without pulling in Playwright. Mirrors search_urls.py's structure and
validation discipline for Indeed, adapted for Seek's own query params.

[INFERENCE - not verified against a live Seek response, unlike Indeed's
scheme which was grounded against real source/URLs. This is based on public
third-party Seek-scraper API documentation, not a confirmed Seek source. See
seek_identifiers.py's normalize_seek_job_id docstring for the one part of
the Seek scheme (the /job/<id> path) that IS grounded against real URLs.]
"""

from __future__ import annotations

from urllib.parse import urlencode

from jobsearch_mcp_server.seek_identifiers import _validate_seek_domain

# [INFERENCE] Seek's presumed query param for work type is `worktype`.
WORK_TYPE_MAP: dict[str, str] = {
    "full_time": "full-time",
    "part_time": "part-time",
    "contract": "contract",
    "casual": "casual",
}

# [INFERENCE] Seek's presumed query param for recency is `dateposted`,
# expressed in days.
DATE_POSTED_MAP: dict[str, str] = {
    "today": "1",
    "past_3_days": "3",
    "past_week": "7",
    "past_month": "30",
}


def build_seek_search_url(
    keywords: str,
    location: str | None = None,
    *,
    work_type: str | None = None,
    date_posted: str | None = None,
    classification: str | None = None,
    page: int = 1,
    domain: str = "www.seek.com.au",
) -> str:
    """Build a Seek job-search URL.

    Produces a URL of the shape:
        https://www.seek.com.au/jobs?keywords=<keywords>&where=<location>&worktype=...&dateposted=...&classification=...&page=...

    `keywords` is always present. `where` is included only when `location`
    is given. `worktype`, `dateposted`, and `classification` are omitted
    when their source argument is `None`. `page` is always included since
    it has a concrete default (1) rather than being optional.

    Raises:
        ValueError: if `keywords` is empty, or `work_type`/`date_posted` is
            given but not a recognized key in the corresponding map, or
            `domain` is empty or not an allowed Seek domain.
    """
    if not keywords or not keywords.strip():
        raise ValueError("keywords must be a non-empty string")
    if not domain or not domain.strip():
        raise ValueError("domain must not be empty")
    _validate_seek_domain(domain)

    params: dict[str, str] = {"keywords": keywords}

    if location:
        params["where"] = location

    if work_type is not None:
        worktype = WORK_TYPE_MAP.get(work_type)
        if worktype is None:
            raise ValueError(
                f"unknown work_type: {work_type!r}; expected one of {sorted(WORK_TYPE_MAP)}"
            )
        params["worktype"] = worktype

    if date_posted is not None:
        dateposted = DATE_POSTED_MAP.get(date_posted)
        if dateposted is None:
            raise ValueError(
                f"unknown date_posted: {date_posted!r}; expected one of {sorted(DATE_POSTED_MAP)}"
            )
        params["dateposted"] = dateposted

    if classification is not None:
        params["classification"] = classification

    params["page"] = str(page)

    query = urlencode(params)
    return f"https://{domain}/jobs?{query}"
