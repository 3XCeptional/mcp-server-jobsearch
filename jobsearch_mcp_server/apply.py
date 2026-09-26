"""Apply-flow automation: fills and submits an Indeed job application.

Page-owning module: imports navigation.py, contracts.py, identifiers.py, and
authentication.py, the same layering the rest of this package uses.

MECHANICAL ONLY. `JobApplier` never decides whether a candidate should
apply, never fact-checks, never humanizes copy, and never invents a field
value - every fact it places into a form traces directly to the
caller-supplied `ApplicantProfile`. It never types a username/password
(never logs in as the candidate), never attempts to solve or click through
a CAPTCHA, and never creates an account: each of those conditions is
detected and reported back as a `blocked_reason`, not worked around.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from jobsearch_mcp_server import authentication
from jobsearch_mcp_server.apply_common import (
    _ACCOUNT_LOGIN_SELECTORS,
    _CAPTCHA_SELECTORS,
    _answer_screening_questions,
    _any_selector_present,
    _click_reveals_external_domain,
    _fill_first,
    _first_present,
    _is_external_domain,
    _resolve_page,
    _upload_first,
    _validate_attachment_path,
    _wait_for_apply_form_root,
    _wait_for_submission_success,
)
from jobsearch_mcp_server.contracts import ApplicantProfile, ApplyResult, ExtractionError
from jobsearch_mcp_server.identifiers import job_view_url
from jobsearch_mcp_server.navigation import PageNavigator

logger = logging.getLogger(__name__)

# Mirrors session_state.py's `_DEFAULT_USER_DATA_DIR` convention: a per-user
# directory under the home dir rather than the repo (screenshots are a local
# audit trail, not something to commit).
_DEFAULT_SCREENSHOT_DIR = Path.home() / ".indeed-mcp-server" / "screenshots"

# Indeed's native "Indeed Apply" control. Real markup shifts between an
# in-page button and a link; both are tried.
_NATIVE_APPLY_BUTTON_SELECTORS = (
    'button[data-testid="indeedApplyButton"]',
    'button:has-text("Apply now")',
    'a:has-text("Apply now")',
)

# An "apply on company site" control means the flow hands off to a
# third-party ATS - out of scope for this tool (see module docstring).
_EXTERNAL_APPLY_SELECTORS = (
    'a:has-text("Apply on company site")',
    'button:has-text("Apply on company site")',
)

# Indeed Apply commonly renders its form in an iframe whose id starts with
# this prefix. If it never appears, the flow is assumed to render inline on
# the main page instead.
_APPLY_IFRAME_SELECTOR = 'iframe[id^="indeedapply-modal-iframe"]'

_NAME_INPUT_SELECTORS = ('input[name="applicant.name"]',)
_EMAIL_INPUT_SELECTORS = ('input[name="applicant.email"]', 'input[type="email"]')
_PHONE_INPUT_SELECTORS = ('input[name="applicant.phoneNumber"]', 'input[type="tel"]')
_RESUME_FILE_INPUT_SELECTORS = ('input[type="file"][name*="resume" i]',)
_COVER_LETTER_FILE_INPUT_SELECTORS = ('input[type="file"][name*="cover" i]',)
_SUBMIT_BUTTON_SELECTORS = (
    'button[type="submit"]',
    'button:has-text("Submit your application")',
    'button:has-text("Submit application")',
)

# `name` attributes handled directly from `ApplicantProfile` fields above,
# excluded from the screening-question scan so they are never double-filled
# or reported as an unanswered required field.
_STANDARD_FIELD_NAMES = frozenset({"applicant.name", "applicant.email", "applicant.phoneNumber"})


class JobApplier:
    """Fills and submits an Indeed job application. Mechanical only.

    Every fact placed into the form traces directly to the caller-supplied
    `ApplicantProfile` - this class decides nothing about whether the
    candidate *should* apply, and never fabricates a value for a field
    `profile` doesn't cover.
    """

    def __init__(
        self,
        navigator: PageNavigator,
        page_getter: Callable[[], Any] | None = None,
        screenshot_dir: Path | None = None,
    ) -> None:
        self._navigator = navigator
        self._page_getter = page_getter
        self._screenshot_dir = screenshot_dir or _DEFAULT_SCREENSHOT_DIR

    async def apply_to_job(self, job_id: str, profile: ApplicantProfile) -> ApplyResult:
        """Navigate to `job_id`'s viewjob page and attempt to apply.

        Returns an `ApplyResult` with `submitted=True` only after a real
        confirmation signal is observed. Any hard-stop condition (CAPTCHA,
        account-creation wall, external ATS handoff, an unanswerable
        required field) is reported via `blocked_reason` rather than pushed
        through.
        """
        url = job_view_url(job_id)
        await self._navigator.goto(url)
        page = _resolve_page(self._navigator, self._page_getter)

        if not await authentication.ensure_logged_in(page):
            return ApplyResult(job_id=job_id, submitted=False, blocked_reason="captcha_wall")

        try:
            return await self._run_apply_flow(page, job_id, profile)
        except ExtractionError:
            raise
        except Exception as exc:  # pragma: no cover - defensive catch-all
            raise ExtractionError(
                f"apply_to_job(job_id={job_id!r}) failed unexpectedly during the "
                f"apply flow: {exc}"
            ) from exc

    async def _run_apply_flow(
        self, page: Any, job_id: str, profile: ApplicantProfile
    ) -> ApplyResult:
        """Fill and submit the apply form on the current form snapshot.

        Scope note: v1 does not detect or advance through a multi-step
        apply wizard (Indeed's real Apply flow can be multiple pages). If a
        required core field (name, email, phone, or resume) isn't found on
        the first form snapshot - whether from page-structure drift or
        because the field actually lives on a later, un-navigated-to step -
        this returns `blocked_reason="unsupported_apply_flow"` rather than
        guessing or reporting a false `submitted=True`.
        """
        native_locator = await _first_present(page, _NATIVE_APPLY_BUTTON_SELECTORS)

        if native_locator is None:
            external_locator = await _first_present(page, _EXTERNAL_APPLY_SELECTORS)
            if external_locator is None:
                return ApplyResult(
                    job_id=job_id, submitted=False, blocked_reason="unsupported_apply_flow"
                )
            if await _click_reveals_external_domain(page, external_locator, "indeed.com"):
                return ApplyResult(
                    job_id=job_id,
                    submitted=False,
                    blocked_reason="external_ats_login_required",
                )
            return ApplyResult(
                job_id=job_id, submitted=False, blocked_reason="unsupported_apply_flow"
            )

        try:
            await native_locator.click(timeout=10000)
        except Exception as exc:
            raise ExtractionError(
                f"clicking the native Indeed Apply button failed: {exc}"
            ) from exc

        form_root = await _wait_for_apply_form_root(page, _APPLY_IFRAME_SELECTOR)

        if await _any_selector_present(form_root, _CAPTCHA_SELECTORS):
            return ApplyResult(job_id=job_id, submitted=False, blocked_reason="captcha_wall")

        if await _any_selector_present(form_root, _ACCOUNT_LOGIN_SELECTORS):
            return ApplyResult(
                job_id=job_id, submitted=False, blocked_reason="account_creation_required"
            )

        name_filled = await _fill_first(form_root, _NAME_INPUT_SELECTORS, profile.full_name)
        email_filled = await _fill_first(form_root, _EMAIL_INPUT_SELECTORS, profile.email)
        phone_filled = await _fill_first(form_root, _PHONE_INPUT_SELECTORS, profile.phone)

        resume_path = _validate_attachment_path(profile.resume_path, kind="resume")
        try:
            resume_uploaded = await _upload_first(
                form_root, _RESUME_FILE_INPUT_SELECTORS, resume_path
            )
        except Exception as exc:
            raise ExtractionError(
                f"uploading resume from {profile.resume_path!r} failed: {exc}"
            ) from exc

        # A selector failing to match at all (page-structure drift, or the
        # field genuinely lives on a later step of a multi-step wizard this
        # tool doesn't advance through - see `_run_apply_flow`'s docstring)
        # must stop the flow here, not fall through toward a submit click
        # that would report a false `submitted=True` with core fields
        # missing from the form.
        missing_core_fields = [
            name
            for name, filled in (
                ("full_name", name_filled),
                ("email", email_filled),
                ("phone", phone_filled),
                ("resume", resume_uploaded),
            )
            if not filled
        ]
        if missing_core_fields:
            return ApplyResult(
                job_id=job_id,
                submitted=False,
                blocked_reason="unsupported_apply_flow",
                unanswered_fields=tuple(missing_core_fields),
            )

        if profile.cover_letter_path:
            cover_letter_path = _validate_attachment_path(
                profile.cover_letter_path, kind="cover letter"
            )
            try:
                cover_letter_uploaded = await _upload_first(
                    form_root, _COVER_LETTER_FILE_INPUT_SELECTORS, cover_letter_path
                )
            except Exception as exc:
                raise ExtractionError(
                    f"uploading cover letter from {profile.cover_letter_path!r} failed: {exc}"
                ) from exc
            if not cover_letter_uploaded:
                return ApplyResult(
                    job_id=job_id,
                    submitted=False,
                    blocked_reason="unsupported_apply_flow",
                    unanswered_fields=("cover_letter",),
                )

        unanswered = await _answer_screening_questions(
            form_root, profile, _STANDARD_FIELD_NAMES
        )
        if unanswered:
            return ApplyResult(
                job_id=job_id,
                submitted=False,
                blocked_reason="unsupported_apply_flow",
                unanswered_fields=tuple(unanswered),
            )

        # A CAPTCHA can appear anywhere in the flow, not just at the top -
        # re-check right before committing to a submit click.
        if await _any_selector_present(form_root, _CAPTCHA_SELECTORS):
            return ApplyResult(job_id=job_id, submitted=False, blocked_reason="captcha_wall")

        screenshot_path = await self._capture_pre_submit_screenshot(page, job_id)

        submit_locator = await _first_present(form_root, _SUBMIT_BUTTON_SELECTORS)
        if submit_locator is None:
            raise ExtractionError(
                "apply form had no element matching any known submit-button selector "
                f"({_SUBMIT_BUTTON_SELECTORS!r})"
            )

        url_before = page.url
        try:
            await submit_locator.click(timeout=10000)
        except Exception as exc:
            raise ExtractionError(f"clicking the final submit button failed: {exc}") from exc

        submitted = await _wait_for_submission_success(page, url_before)
        if not submitted:
            raise ExtractionError(
                "submit button was clicked but no confirmation signal (a URL change or "
                "success text in any frame) was observed within the timeout"
            )

        return ApplyResult(job_id=job_id, submitted=True, screenshot_path=screenshot_path)

    async def _capture_pre_submit_screenshot(self, page: Any, job_id: str) -> str:
        """Audit-trail screenshot taken immediately before the submit click."""
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        path = self._screenshot_dir / f"{job_id}_{timestamp}.png"
        try:
            await page.screenshot(path=str(path))
        except Exception as exc:
            raise ExtractionError(f"pre-submit audit screenshot failed: {exc}") from exc
        return str(path)
