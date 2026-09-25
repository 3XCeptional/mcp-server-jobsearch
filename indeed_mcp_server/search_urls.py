"""Indeed job search URL construction.

Browser-free. May import `identifiers` but nothing heavier (no browser or
session modules), so it stays usable by pure URL-building callers and tests
without pulling in Playwright.
"""

from __future__ import annotations

from urllib.parse import urlencode

# Indeed's real query param for job type is `jt`.
JOB_TYPE_MAP: dict[str, str] = {
    "internship": "internship",
    "full_time": "fulltime",
    "part_time": "parttime",
    "contract": "contract",
    "temporary": "temporary",
    "permanent": "permanent",
}

# Indeed's real query param for recency is `fromage`, expressed in days.
DATE_POSTED_MAP: dict[str, str] = {
    "today": "1",
    "past_3_days": "3",
    "past_week": "7",
    "past_month": "14",
}


def build_job_search_url(
    keywords: str,
    location: str | None = None,
    *,
    job_type: str | None = None,
    date_posted: str | None = None,
    radius_km: int | None = None,
    start: int = 0,
    domain: str = "au.indeed.com",
) -> str:
    """Build an Indeed job search URL.

    Produces a URL of the shape:
        https://au.indeed.com/jobs?q=<keywords>&l=<location>&jt=...&fromage=...&radius=...&start=...

    `q` is always present. `l` is included only when `location` is given.
    `jt`, `fromage`, and `radius` are omitted when their source argument is
    `None`. `start` is always included since it has a concrete default (0)
    rather than being optional.

    Raises:
        ValueError: if `keywords` is empty, or `job_type`/`date_posted` is
            given but not a recognized key in the corresponding map, or
            `domain` is empty.
    """
    if not keywords or not keywords.strip():
        raise ValueError("keywords must be a non-empty string")
    if not domain or not domain.strip():
        raise ValueError("domain must not be empty")

    params: dict[str, str] = {"q": keywords}

    if location:
        params["l"] = location

    if job_type is not None:
        jt = JOB_TYPE_MAP.get(job_type)
        if jt is None:
            raise ValueError(
                f"unknown job_type: {job_type!r}; expected one of {sorted(JOB_TYPE_MAP)}"
            )
        params["jt"] = jt

    if date_posted is not None:
        fromage = DATE_POSTED_MAP.get(date_posted)
        if fromage is None:
            raise ValueError(
                f"unknown date_posted: {date_posted!r}; expected one of {sorted(DATE_POSTED_MAP)}"
            )
        params["fromage"] = fromage

    if radius_km is not None:
        params["radius"] = str(radius_km)

    params["start"] = str(start)

    query = urlencode(params)
    return f"https://{domain}/jobs?{query}"
