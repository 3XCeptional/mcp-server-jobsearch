"""Tests for SessionManager caching/reuse -- no real browser is launched.

The browser-launch and authentication layers are monkeypatched with fakes,
per the leaf-1.2.1 gate: verify reuse (not relaunch) and safe/real close(),
without any network or Playwright process involved.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

from indeed_mcp_server.session import ScrapingSession
from indeed_mcp_server.session_state import SessionManager


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


def _run(coro):
    return asyncio.run(coro)


def test_get_or_create_session_reuses_same_session(tmp_path: Path) -> None:
    page = _FakePage()
    context = _FakeContext(page)
    fake_launch = AsyncMock(return_value=(context, page))

    with (
        patch(
            "indeed_mcp_server.session_state.launch_persistent_browser",
            fake_launch,
        ),
        patch(
            "indeed_mcp_server.session_state.ensure_logged_in",
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


def test_close_is_safe_when_no_session_created(tmp_path: Path) -> None:
    manager = SessionManager(user_data_dir=tmp_path)

    # Should not raise even though get_or_create_session was never called.
    _run(manager.close())


def test_close_calls_underlying_context_close(tmp_path: Path) -> None:
    page = _FakePage()
    context = _FakeContext(page)
    fake_launch = AsyncMock(return_value=(context, page))

    with (
        patch(
            "indeed_mcp_server.session_state.launch_persistent_browser",
            fake_launch,
        ),
        patch(
            "indeed_mcp_server.session_state.ensure_logged_in",
            AsyncMock(return_value=True),
        ),
    ):
        manager = SessionManager(user_data_dir=tmp_path)
        _run(manager.get_or_create_session())
        _run(manager.close())

    context.close.assert_awaited_once()

    # A second close() after the context is already gone must still be safe.
    _run(manager.close())
