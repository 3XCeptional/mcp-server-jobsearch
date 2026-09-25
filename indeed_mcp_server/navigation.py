"""Page navigation with errors normalized into ExtractionError.

Page-owning module: imports only session.py from within this package.
"""

from __future__ import annotations

from enum import Enum

from indeed_mcp_server.contracts import ExtractionError
from indeed_mcp_server.session import ScrapingSession


class WaitUntil(str, Enum):
    LOAD = "load"
    DOMCONTENTLOADED = "domcontentloaded"
    NETWORKIDLE = "networkidle"


class PageNavigator:
    """Navigates the page owned by a ScrapingSession."""

    def __init__(self, session: ScrapingSession) -> None:
        self._session = session

    async def goto(
        self,
        url: str,
        wait_until: WaitUntil = WaitUntil.DOMCONTENTLOADED,
        timeout_ms: int = 30000,
    ) -> None:
        try:
            await self._session.page.goto(
                url, wait_until=wait_until.value, timeout=timeout_ms
            )
        except Exception as exc:  # playwright.async_api.Error, TimeoutError, etc.
            raise ExtractionError(
                f"Navigation to {url!r} failed after {timeout_ms}ms: {exc}"
            ) from exc
