"""Apply-flow automation: fills and submits a Seek (seek.com.au) job application.

Mirrors `apply.py`'s `JobApplier` structure and hard-stop safety discipline
exactly, adapted for Seek's own markup conventions - see that module's
docstring for the full safety contract this class also honors. The
genuinely site-agnostic pieces (locator helpers, attachment-path validation,
CAPTCHA detection, success-text detection, screening-question filling) are
not redefined here: they are imported from `apply_common.py`, which both
`apply.py` and this module depend on.

MECHANICAL ONLY. `SeekJobApplier` never decides whether a candidate should
apply, never fact-checks, never humanizes copy, and never invents a field
value - every fact it places into a form traces directly to the
caller-supplied `ApplicantProfile`. It never types a username/password
(never logs in as the candidate), never attempts to solve or click through
a CAPTCHA, and never creates an account: each of those conditions is
detected and reported back as a `blocked_reason`, not worked around.

[INFERENCE]: every Seek-specific selector below (the Quick Apply button, the
`data-automation` field attributes, the submit button) follows the
`data-automation` convention `seek_job_pages.py` and `seek_search_urls.py`
already document as inferred-but-unverified against a live Seek response.
Each selector's fallback exists precisely because of that uncertainty, same
discipline as those modules.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from jobsearch_mcp_server import authentication
from jobsearch_mcp_server.apply import _ACCOUNT_LOGIN_SELECTORS
from jobsearch_mcp_server.apply_common import (
    _CAPTCHA_SELECTORS,
    _answer_screening_questions,
    _any_selector_present,
    _fill_first,
    _first_present,
    _is_external_domain,
    _upload_first,
    _validate_attachment_path,
    _wait_for_submission_success,
)
from jobsearch_mcp_server.contracts import ApplicantProfile, ApplyResult, ExtractionError
from jobsearch_mcp_server.navigation import PageNavigator
from jobsearch_mcp_server.seek_identifiers import seek_job_view_url

logger = logging.getLogger(__name__)

# Mirrors apply.py's `_DEFAULT_SCREENSHOT_DIR` convention: a per-user
# directory under the home dir rather than the repo (screenshots are a local
# audit trail, not something to commit). Kept under the same top-level
# `.indeed-mcp-server` directory as Indeed's own screenshots/browser-profile
# dirs (see server.py's `_SEEK_USER_DATA_DIR` comment for why the on-disk
# directory name wasn't renamed package-wide), in its own `seek-screenshots`
# subdirectory so the two sites' audit trails never collide.
_DEFAULT_SCREENSHOT_DIR = Path.home() / ".indeed-mcp-server" / "seek-screenshots"

# [INFERENCE] Seek's native "Quick apply" control, following the same
# `data-automation` convention `seek_job_pages.py` already uses for Seek's
# job detail/search pages. A text-based fallback is tried too, mirroring
# apply.py's own `_NATIVE_APPLY_BUTTON_SELECTORS` fallback-chain pattern.
_NATIVE_APPLY_BUTTON_SELECTORS = (
    '[data-automation="job-detail-apply"]',
    'button:has-text("Quick apply")',
    'a:has-text("Quick apply")',
)

# [INFERENCE] A control that hands the flow off to a third-party employer
# site rather than Seek's own multi-step application form - out of scope
# for this tool (see module docstring), mirroring apply.py's
# `_EXTERNAL_APPLY_SELECTORS`.
_EXTERNAL_APPLY_SELECTORS = (
    'a:has-text("Apply on company site")',
    'button:has-text("Apply on company site")',
    'a:has-text("Apply on employer site")',
)

# [INFERENCE] Unlike Indeed Apply, Seek's own application form is not known
# to reliably render inside an iframe - it's more commonly a same-page
# multi-step form. `_wait_for_apply_form_root` still tries an iframe first
# (in case a specific job's Quick Apply does render one) and falls back to
# the bare page, exactly mirroring apply.py's fallback discipline.
_APPLY_IFRAME_SELECTOR = 'iframe[id^="seek-apply-modal-iframe"]'

# [INFERENCE] Seek's own `data-automation` convention, following the same
# naming pattern the task brief and `seek_job_pages.py` both establish.
_NAME_INPUT_SELECTORS = ('input[data-automation="applicantName"]',)
_EMAIL_INPUT_SELECTORS = ('input[data-automation="applicantEmail"]', 'input[type="email"]')
_PHONE_INPUT_SELECTORS = ('input[data-automation="applicantPhone"]', 'input[type="tel"]')
_RESUME_FILE_INPUT_SELECTORS = ('input[type="file"][data-automation*="resume" i]',)
_COVER_LETTER_FILE_INPUT_SELECTORS = ('input[type="file"][data-automation*="cover" i]',)
_SUBMIT_BUTTON_SELECTORS = (
    'button[type="submit"]',
    'button:has-text("Submit application")',
    'button:has-text("Review your application")',
)

# `data-automation` attribute values that identify the core fields handled
# directly from `ApplicantProfile` above, excluded from the screening-
# question scan so they are never double-filled or reported as an unanswered
# required field. Mirrors apply.py's `_STANDARD_FIELD_NAMES`, but keyed on
# Seek's own `data-automation` attribute rather than Indeed's `name`
# attribute: the call below passes `standard_field_attribute="data-automation"`
# so `_answer_screening_questions` compares against the right attribute for
# this site. (Fixed 2026-09-27: an earlier version of this comment claimed
# not colliding with a real `name` attribute was intentional and safe - it
# wasn't. `_answer_screening_questions` only ever compared against `name`
# at the time, so these `data-automation` values could never match anything,
# and every one of Seek's real, `required` name/email/phone inputs was
# misreported as an unanswered screening question, blocking every real
# apply with `unsupported_apply_flow`.)
_STANDARD_FIELD_NAMES = frozenset(
    {"applicantName", "applicantEmail", "applicantPhone"}
)

# Domain suffix `_is_external_domain` checks an apply-flow handoff against.
_SEEK_DOMAIN_SUFFIX = "seek.com.au"


def _resolve_page(navigator: PageNavigator, page_getter: Callable[[], Any] | None) -> Any:
    """Fetch the live Playwright Page a navigator is driving.

    Mirrors `apply._resolve_page` exactly - see that function's docstring
    for why the private `_session` attribute is tried alongside the
    (currently nonexistent) public `session` name.
    """
    for attr_path in ("session", "_session"):
        session = getattr(navigator, attr_path, None)
        if session is not None and hasattr(session, "page"):
            return session.page
    if hasattr(navigator, "page"):
        return navigator.page
    if page_getter is not None:
        return page_getter()
    raise AttributeError(
        "PageNavigator instance exposes no reachable Playwright Page via "
        "`.session.page`, `._session.page`, `.page`, or a supplied page_getter"
    )


async def _wait_for_apply_form_root(page: Any, timeout_ms: int = 10000) -> Any:
    """Return the apply form's root: the modal iframe if one appears, else the page.

    Mirrors `apply._wait_for_apply_form_root` exactly, using Seek's own
    (inferred) iframe id prefix instead of Indeed's.
    """
    try:
        await page.wait_for_selector(_APPLY_IFRAME_SELECTOR, timeout=timeout_ms)
        return page.frame_locator(_APPLY_IFRAME_SELECTOR)
    except Exception:
        return page


async def _click_reveals_external_domain(page: Any, locator: Any, wait_ms: int = 1500) -> bool:
    """Click an "apply on company site" control and check if it left seek.com.au.

    Mirrors `apply._click_reveals_external_domain` exactly, checking against
    Seek's own domain suffix instead of Indeed's.
    """
    context = getattr(page, "context", None)
    pages_before = set(context.pages) if context is not None else set()
    try:
        await locator.click(timeout=5000)
    except Exception:
        return True

    await page.wait_for_timeout(wait_ms)

    target = page
    if context is not None:
        new_pages = [p for p in context.pages if p not in pages_before]
        if new_pages:
            target = new_pages[-1]
            try:
                await target.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass

    is_external = _is_external_domain(getattr(target, "url", "") or "", _SEEK_DOMAIN_SUFFIX)

    if target is not page:
        try:
            await target.close()
        except Exception:  # pragma: no cover - best-effort cleanup
            pass

    return is_external


class SeekJobApplier:
    """Fills and submits a Seek job application. Mechanical only.

    Every fact placed into the form traces directly to the caller-supplied
    `ApplicantProfile` - this class decides nothing about whether the
    candidate *should* apply, and never fabricates a value for a field
    `profile` doesn't cover. Mirrors `apply.JobApplier`'s exact step
    sequence and hard-stop behaviour.
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
        """Navigate to `job_id`'s Seek job page and attempt to apply.

        Returns an `ApplyResult` with `submitted=True` only after a real
        confirmation signal is observed. Any hard-stop condition (CAPTCHA,
        account-creation wall, external ATS handoff, an unanswerable
        required field) is reported via `blocked_reason` rather than pushed
        through. Mirrors `JobApplier.apply_to_job`'s step sequence exactly:
        navigate -> `authentication.ensure_logged_in` -> detect
        native-vs-external apply -> detect CAPTCHA/account-wall -> fill core
        fields (failing closed on any missing one) -> validate/upload
        attachments -> answer screening questions -> pre-submit screenshot
        -> submit -> wait for confirmation.
        """
        url = seek_job_view_url(job_id)
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

        Scope note: same as `JobApplier._run_apply_flow` - v1 does not
        detect or advance through a multi-step apply wizard. If a required
        core field (name, email, phone, or resume) isn't found on the first
        form snapshot, this returns `blocked_reason="unsupported_apply_flow"`
        rather than guessing or reporting a false `submitted=True`.
        """
        native_locator = await _first_present(page, _NATIVE_APPLY_BUTTON_SELECTORS)

        if native_locator is None:
            external_locator = await _first_present(page, _EXTERNAL_APPLY_SELECTORS)
            if external_locator is None:
                return ApplyResult(
                    job_id=job_id, submitted=False, blocked_reason="unsupported_apply_flow"
                )
            if await _click_reveals_external_domain(page, external_locator):
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
                f"clicking the native Seek Quick Apply button failed: {exc}"
            ) from exc

        form_root = await _wait_for_apply_form_root(page)

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
        # tool doesn't advance through) must stop the flow here, not fall
        # through toward a submit click that would report a false
        # `submitted=True` with core fields missing from the form.
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
            form_root,
            profile,
            _STANDARD_FIELD_NAMES,
            standard_field_attribute="data-automation",
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
