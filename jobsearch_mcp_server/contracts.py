"""Shared data contracts for Indeed job data.

Browser-free, no imports from other jobsearch_mcp_server modules, so both the
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


@dataclass(frozen=True)
class ApplicantProfile:
    """Caller-supplied, fact-only applicant data for filling an apply form.

    The calling agent owns fact-checking and resume tailoring; this MCP
    server only performs mechanical form-fill/upload/submit. No field here
    is inferred or defaulted by the server itself.
    """

    full_name: str
    email: str
    phone: str
    resume_path: str
    location: str = "Sydney, NSW"
    cover_letter_path: str | None = None
    screening_answers: dict[str, str] | None = None


@dataclass(frozen=True)
class ApplyResult:
    """Outcome of an apply_to_job call. Exactly one of submitted/blocked is true."""

    job_id: str
    submitted: bool
    blocked_reason: str | None = None
    # one of: None (submitted), "captcha_wall", "account_creation_required",
    # "external_ats_login_required", "already_applied", "listing_closed",
    # "unsupported_apply_flow"
    screenshot_path: str | None = None
    unanswered_fields: tuple[str, ...] = ()
