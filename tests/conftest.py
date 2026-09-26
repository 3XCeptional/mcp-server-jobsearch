"""Shared pytest fixtures for the jobsearch_mcp_server test suite.

Kept intentionally small: only fixtures that collapse *real*, verified
duplication across multiple existing test files are added here. Fixtures
that would only ever be used by one file belong in that file, not here.

Duplication found and factored:

- `html_page_runner`: `test_job_pages_dom.py` and `test_apply.py` each
  hand-rolled the identical "launch a headless Chromium browser, load an
  HTML string via `page.set_content()`, run one async callback against the
  page, then close the browser" boilerplate (~10 lines apiece). Both files
  also had no `pytest-asyncio` plugin to lean on, so each drove its own
  `asyncio.run()`. This fixture centralizes that launch/run/close pattern
  behind one call.
- `resume_file` / `make_applicant_profile`: `test_apply.py` constructed a
  temp resume file and an `ApplicantProfile` with the same
  full_name/email/phone three times, varying only `resume_path` and
  `screening_answers`. Factored into a fixture + factory.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Awaitable, Callable, TypeVar

import pytest
from playwright.async_api import Page, async_playwright

from jobsearch_mcp_server.contracts import ApplicantProfile

_T = TypeVar("_T")


@pytest.fixture
def html_page_runner() -> Callable[[str, Callable[[Page], Awaitable[_T]]], _T]:
    """Return a callable that runs `coro_fn(page)` against a fresh headless page.

    `page` is a real Playwright Chromium page with `html` already loaded via
    `set_content()`. The browser is launched and closed around the single
    call, so the returned callable is synchronous and safe to invoke directly
    from a plain (non-async) test function - matching this project's
    convention of driving Playwright through `asyncio.run()` rather than
    `pytest-asyncio` (not installed for this project).
    """

    def _run(html: str, coro_fn: Callable[[Page], Awaitable[_T]]) -> _T:
        async def _inner() -> _T:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch()
                try:
                    page = await browser.new_page()
                    await page.set_content(html)
                    return await coro_fn(page)
                finally:
                    await browser.close()

        return asyncio.run(_inner())

    return _run


@pytest.fixture
def resume_file(tmp_path: Path) -> Path:
    """A throwaway resume file on disk, for tests that need a real path."""
    path = tmp_path / "resume.txt"
    path.write_text("Sample resume content for a fixture-only test.")
    return path


@pytest.fixture
def make_applicant_profile(
    resume_file: Path,
) -> Callable[..., ApplicantProfile]:
    """Factory for `ApplicantProfile` with sane test defaults, override-able.

    Defaults to the same "Jamie Rivers" identity every apply-flow test
    already used, pointed at the `resume_file` fixture's path, so call sites
    only need to specify what actually varies for that test (e.g.
    `screening_answers`, or a nonexistent `resume_path` for a wall test that
    never reaches the upload step).
    """

    def _make(**overrides: object) -> ApplicantProfile:
        defaults: dict[str, object] = {
            "full_name": "Jamie Rivers",
            "email": "jamie@example.test",
            "phone": "0400000000",
            "resume_path": str(resume_file),
        }
        defaults.update(overrides)
        return ApplicantProfile(**defaults)  # type: ignore[arg-type]

    return _make
