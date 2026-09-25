"""Thin wrapper around a single Playwright page.

Page-owning module: holds no knowledge of navigation, auth, or browser
launch mechanics. Zero imports from other indeed_mcp_server modules so it
can sit at the bottom of the dependency graph.
"""

from __future__ import annotations

from typing import Any


class ScrapingSession:
    """Owns a single live Playwright page for the lifetime of the session."""

    def __init__(self, page: Any) -> None:
        self._page = page

    @property
    def page(self) -> Any:
        return self._page
