"""Process-lifetime cache of a single ScrapingSession.

Page-owning module. Deliberately simple: one browser context reused for
the life of the process, no daemon election, no multi-process locking, no
profile fingerprinting -- that machinery exists in the reference
(linkedin-mcp-server) project because LinkedIn mandates an authenticated
session for everything. Indeed's search/detail pages don't, so
authentication here is best-effort only and never blocks session creation.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from jobsearch_mcp_server.authentication import ensure_logged_in
from jobsearch_mcp_server.browser_launch import launch_persistent_browser
from jobsearch_mcp_server.session import ScrapingSession

_DEFAULT_USER_DATA_DIR = Path.home() / ".indeed-mcp-server" / "browser-profile"


class SessionManager:
    """Lazily creates one ScrapingSession and hands back the same one."""

    def __init__(self, user_data_dir: Path | None = None) -> None:
        self._user_data_dir = user_data_dir or _DEFAULT_USER_DATA_DIR
        self._playwright: Any = None
        self._context: Any = None
        self._session: ScrapingSession | None = None
        # Guards the check-then-launch/close critical sections below. Two
        # concurrent MCP tool invocations share one SessionManager instance
        # (both the Indeed and Seek managers in server.py), so without this
        # lock two overlapping awaits of get_or_create_session() can both
        # observe self._session is None and both launch a browser -- the
        # second assignment silently orphans the first context/driver
        # process. The same lock also serialises close() against
        # get_or_create_session() so a close() that runs while a launch is
        # still in flight can't no-op past a session that finishes seconds
        # later, and a get_or_create_session() can't hand back a session
        # that close() is concurrently tearing down.
        self._lock = asyncio.Lock()

    async def get_or_create_session(self) -> ScrapingSession:
        if self._session is not None:
            return self._session

        async with self._lock:
            # Re-check: another coroutine may have finished creating the
            # session while this one was waiting for the lock.
            if self._session is not None:
                return self._session

            playwright, context, page = await launch_persistent_browser(self._user_data_dir)
            self._playwright = playwright
            self._context = context

            # Best-effort: Indeed's search/detail pages work logged out, so a
            # blocked challenge/login wall here is surfaced (return value is
            # informational, not fatal) rather than raised.
            await ensure_logged_in(page)

            self._session = ScrapingSession(page)
            return self._session

    async def close(self) -> None:
        async with self._lock:
            if self._context is not None:
                await self._context.close()
                self._context = None
            if self._playwright is not None:
                # Stops the Playwright driver-manager connection itself, not
                # just the browser context -- without this, every
                # close()+get_or_create_session() cycle leaks one orphaned
                # driver process/connection over a long-running process.
                await self._playwright.stop()
                self._playwright = None
            self._session = None
