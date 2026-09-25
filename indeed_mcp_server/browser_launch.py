"""Persistent-profile browser launch.

Page-owning module: imports Playwright directly, nothing else in this
package. A persistent user-data-dir is the entire persistence mechanism
here (real login cookies survive process restarts) -- deliberately without
the reference project's fingerprint-keyed derived-profile machinery, since
Indeed's search/detail pages don't require an authenticated session at all.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright


async def launch_persistent_browser(
    user_data_dir: Path, headless: bool = False
) -> tuple[Any, Any]:
    """Launch (or attach to) a persistent Chromium profile.

    Returns (context, page). The profile directory at `user_data_dir` is
    what makes any manually-completed login survive across runs -- same
    principle as the reference project, minus its multi-profile complexity.
    """
    user_data_dir.mkdir(parents=True, exist_ok=True)

    playwright = await async_playwright().start()
    context = await playwright.chromium.launch_persistent_context(
        user_data_dir=str(user_data_dir),
        headless=headless,
        channel="chromium",
        viewport={"width": 1280, "height": 900},
    )
    page = context.pages[0] if context.pages else await context.new_page()
    return context, page
