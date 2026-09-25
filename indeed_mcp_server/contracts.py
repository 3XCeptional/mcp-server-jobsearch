"""Shared data contracts for Indeed job data.

Browser-free, no imports from other indeed_mcp_server modules, so both the
pure URL/text utilities and the browser/session/scraping layers can depend
on these shapes without a cyclic or heavy import.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class JobSummary:
    """A single result row from an Indeed job search listing page."""

    job_id: str
    title: str
    company: str
    location: str
    snippet: str
    url: str
    posted: str | None = None


@dataclass(frozen=True)
class JobDetail:
    """The full detail extracted from an Indeed job's viewjob page."""

    job_id: str
    title: str
    company: str
    location: str
    description: str
    url: str
    salary: str | None = None
    job_type: str | None = None


class ExtractionError(Exception):
    """Raised when a page's expected structure can't be found/parsed."""
