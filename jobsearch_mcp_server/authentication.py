"""Best-effort login/challenge detection -- never types credentials.

Page-owning module. Imports navigation.py to sit at the correct layer in
the dependency graph, even though detection here only reads page state.

Indeed's job search and job-detail pages work fully logged out, so this
module's only real job is to notice when the *current* page has been
diverted to a Cloudflare challenge or an Indeed login wall, and give a
human at the visible browser window time to clear it manually. It never
fills in a username/password itself.
"""

from __future__ import annotations

import asyncio
from typing import Any

from jobsearch_mcp_server import navigation  # noqa: F401  (dependency-graph layering)

_CHALLENGE_SELECTORS = (
    "#challenge-running",
    "#cf-challenge-running",
    ".cf-turnstile",
)
_CHALLENGE_TITLE_MARKERS = (
    "just a moment",
    "attention required",
)
_LOGIN_WALL_URL_MARKERS = (
    "/account/login",
    "secure.indeed.com/auth",
)

_POLL_INTERVAL_S = 2


async def _looks_blocked(page: Any) -> bool:
    try:
        title = (await page.title()) or ""
    except Exception:
        title = ""
    if any(marker in title.lower() for marker in _CHALLENGE_TITLE_MARKERS):
        return True

    url = getattr(page, "url", "") or ""
    if any(marker in url for marker in _LOGIN_WALL_URL_MARKERS):
        return True

    for selector in _CHALLENGE_SELECTORS:
        try:
            element = await page.query_selector(selector)
        except Exception:
            element = None
        if element is not None:
            return True

    return False


async def ensure_logged_in(page: Any, timeout_s: int = 300) -> bool:
    """Return True once the page is clear of a challenge/login wall.

    Indeed doesn't require this for search/detail pages, so if no
    challenge or login wall is detected at all, this returns True
    immediately. If one is detected, it polls (never types credentials)
    for up to `timeout_s`, giving a human at the visible window a chance
    to clear it, and returns False if it's still blocked when time runs
    out.
    """
    if not await _looks_blocked(page):
        return True

    elapsed = 0
    while elapsed < timeout_s:
        await asyncio.sleep(_POLL_INTERVAL_S)
        elapsed += _POLL_INTERVAL_S
        if not await _looks_blocked(page):
            return True

    return False
