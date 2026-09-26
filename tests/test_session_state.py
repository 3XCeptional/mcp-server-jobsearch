"""Tests for SessionManager caching/reuse -- no real browser is launched.

The browser-launch and authentication layers are monkeypatched with fakes,
per the leaf-1.2.1 gate: verify reuse (not relaunch) and safe/real close(),
without any network or Playwright process involved.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

from jobsearch_mcp_server.session import ScrapingSession
from jobsearch_mcp_server.session_state import SessionManager


class _FakePage:
    """Stand-in for a playwright Page; only what authentication touches."""

    def __init__(self) -> None:
        self.url = "https://www.indeed.com/jobs?q=test"

    async def title(self) -> str:
        return "Indeed Job Search"

    async def query_selector(self, selector: str):
        return None


class _FakeContext:
    def __init__(self, page: _FakePage) -> None:
        self.pages = [page]
        self.close = AsyncMock()


class _FakePlaywright:
    """Stand-in for the `playwright` driver-manager handle."""

    def __init__(self) -> None:
        self.stop = AsyncMock()


def _run(coro):
    return asyncio.run(coro)


def test_get_or_create_session_reuses_same_session(tmp_path: Path) -> None:
    page = _FakePage()
    context = _FakeContext(page)
    playwright = _FakePlaywright()
    fake_launch = AsyncMock(return_value=(playwright, context, page))

    with (
        patch(
            "jobsearch_mcp_server.session_state.launch_persistent_browser",
            fake_launch,
        ),
        patch(
            "jobsearch_mcp_server.session_state.ensure_logged_in",
            AsyncMock(return_value=True),
        ),
    ):
        manager = SessionManager(user_data_dir=tmp_path)

        session_one = _run(manager.get_or_create_session())
        session_two = _run(manager.get_or_create_session())

    assert isinstance(session_one, ScrapingSession)
    assert session_one is session_two
    assert session_one.page is page
    fake_launch.assert_awaited_once()


def test_get_or_create_session_concurrent_calls_launch_once(tmp_path: Path) -> None:
    """Two overlapping get_or_create_session() awaits must not race.

    Without the lock, both coroutines can observe self._session is None
    before either finishes launch_persistent_browser, so both launch a
    browser and the second assignment orphans the first context/driver.
    The artificial delay widens the race window so this test would fail
    reliably (fake_launch called twice, or two different session objects
    returned) on the unlocked implementation.
    """

    page = _FakePage()
    context = _FakeContext(page)
    playwright = _FakePlaywright()

    async def _delayed_launch(_user_data_dir: Path):
        await asyncio.sleep(0.05)
        return playwright, context, page

    fake_launch = AsyncMock(side_effect=_delayed_launch)

    async def _scenario():
        manager = SessionManager(user_data_dir=tmp_path)
        with (
            patch(
                "jobsearch_mcp_server.session_state.launch_persistent_browser",
                fake_launch,
            ),
            patch(
                "jobsearch_mcp_server.session_state.ensure_logged_in",
                AsyncMock(return_value=True),
            ),
        ):
            return await asyncio.gather(
                manager.get_or_create_session(),
                manager.get_or_create_session(),
            )

    session_one, session_two = _run(_scenario())

    fake_launch.assert_awaited_once()
    assert session_one is session_two
    assert isinstance(session_one, ScrapingSession)


def test_close_is_safe_when_no_session_created(tmp_path: Path) -> None:
    manager = SessionManager(user_data_dir=tmp_path)

    # Should not raise even though get_or_create_session was never called.
    _run(manager.close())


def test_close_does_not_stop_playwright_when_no_session_created(tmp_path: Path) -> None:
    """close() must be a no-op on `playwright` too if nothing was ever launched."""
    playwright = _FakePlaywright()
    fake_launch = AsyncMock(return_value=(playwright, _FakeContext(_FakePage()), _FakePage()))

    with patch(
        "jobsearch_mcp_server.session_state.launch_persistent_browser",
        fake_launch,
    ):
        manager = SessionManager(user_data_dir=tmp_path)
        _run(manager.close())

    fake_launch.assert_not_awaited()
    playwright.stop.assert_not_awaited()


def test_close_calls_underlying_context_close(tmp_path: Path) -> None:
    page = _FakePage()
    context = _FakeContext(page)
    playwright = _FakePlaywright()
    fake_launch = AsyncMock(return_value=(playwright, context, page))

    with (
        patch(
            "jobsearch_mcp_server.session_state.launch_persistent_browser",
            fake_launch,
        ),
        patch(
            "jobsearch_mcp_server.session_state.ensure_logged_in",
            AsyncMock(return_value=True),
        ),
    ):
        manager = SessionManager(user_data_dir=tmp_path)
        _run(manager.get_or_create_session())
        _run(manager.close())

    context.close.assert_awaited_once()
    playwright.stop.assert_awaited_once()

    # A second close() after the context/playwright are already gone must
    # still be safe, and must not call stop() a second time.
    _run(manager.close())
    playwright.stop.assert_awaited_once()
