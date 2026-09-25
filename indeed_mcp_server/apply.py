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
from urllib.parse import urlparse

from indeed_mcp_server import authentication
from indeed_mcp_server.contracts import ApplicantProfile, ApplyResult, ExtractionError
from indeed_mcp_server.identifiers import job_view_url
from indeed_mcp_server.navigation import PageNavigator

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

# Any of these inside the apply form root means Indeed is demanding a fresh
# account login before showing the application - a wall this tool must not
# push through.
_ACCOUNT_LOGIN_SELECTORS = (
    'input[type="password"]',
    'button:has-text("Sign in with Google")',
    'button:has-text("Continue with Google")',
    'text=/sign in to (your indeed account|continue)/i',
)

_CAPTCHA_SELECTORS = (
    "iframe[src*='recaptcha']",
    "iframe[title*='recaptcha' i]",
    ".g-recaptcha",
    "iframe[src*='hcaptcha']",
    ".h-captcha",
    ".cf-turnstile",
    "#challenge-running",
)

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
_STANDARD_FIELD_NAMES = {"applicant.name", "applicant.email", "applicant.phoneNumber"}

_SUCCESS_TEXT_MARKERS = (
    "application submitted",
    "your application has been submitted",
    "you applied",
    "application sent",
)


def _resolve_page(navigator: PageNavigator, page_getter: Callable[[], Any] | None) -> Any:
    """Fetch the live Playwright Page a navigator is driving.

    Same reasoning as `job_pages._navigator_page`: `PageNavigator` documents
    `goto()` but not a page accessor, so the public name is tried first (in
    case a future revision adds one), then the private `_session` attribute
    the current implementation actually uses, then a direct `.page`, then
    finally the caller-supplied `page_getter` fallback.
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


async def _first_present(root: Any, selectors: tuple[str, ...]) -> Any | None:
    """Return the first Locator (as `.first`) matching any selector, or None."""
    for selector in selectors:
        locator = root.locator(selector)
        try:
            if await locator.count() > 0:
                return locator.first
        except Exception:  # pragma: no cover - defensive against a torn-down frame
            continue
    return None


async def _any_selector_present(root: Any, selectors: tuple[str, ...]) -> bool:
    return await _first_present(root, selectors) is not None


async def _fill_first(root: Any, selectors: tuple[str, ...], value: str) -> bool:
    locator = await _first_present(root, selectors)
    if locator is None:
        return False
    await locator.fill(value)
    return True


async def _upload_first(root: Any, selectors: tuple[str, ...], path: str) -> bool:
    locator = await _first_present(root, selectors)
    if locator is None:
        return False
    await locator.set_input_files(path)
    return True


async def _wait_for_apply_form_root(page: Any, timeout_ms: int = 10000) -> Any:
    """Return the apply form's root: the modal iframe if one appears, else the page.

    Indeed Apply commonly renders inside an iframe, but some flows render
    inline on the main page - both are treated identically by every
    selector-based helper above, since `Page` and Playwright's
    `FrameLocator` both expose `.locator()`.
    """
    try:
        await page.wait_for_selector(_APPLY_IFRAME_SELECTOR, timeout=timeout_ms)
        return page.frame_locator(_APPLY_IFRAME_SELECTOR)
    except Exception:
        return page


async def _click_reveals_external_domain(page: Any, locator: Any, wait_ms: int = 1500) -> bool:
    """Click an "apply on company site" control and check if it left indeed.com.

    Checks both a same-tab navigation and a newly opened tab/page, per the
    two ways Indeed is known to hand off to an external ATS. If the click
    itself fails outright, the control's mere presence (labeled as an
    external apply link) is treated as sufficient evidence on its own.
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

    netloc = urlparse(getattr(target, "url", "") or "").netloc.lower()
    return not netloc.endswith("indeed.com")


async def _any_frame_contains_success_text(page: Any) -> bool:
    for frame in page.frames:
        try:
            content = (await frame.content()).lower()
        except Exception:  # pragma: no cover - a torn-down/cross-origin frame
            continue
        if any(marker in content for marker in _SUCCESS_TEXT_MARKERS):
            return True
    return False


async def _wait_for_submission_success(
    page: Any, url_before: str, timeout_ms: int = 15000, poll_ms: int = 300
) -> bool:
    """Poll for a real confirmation signal: a URL change or success text.

    Checks every frame's content (not just the main page's), since a
    successful Indeed Apply submission commonly mutates the modal iframe's
    own document rather than navigating the outer page.
    """
    elapsed = 0
    while elapsed <= timeout_ms:
        if page.url != url_before:
            return True
        if await _any_frame_contains_success_text(page):
            return True
        await page.wait_for_timeout(poll_ms)
        elapsed += poll_ms
    return False


async def _answer_screening_questions(
    form_root: Any, profile: ApplicantProfile
) -> list[str]:
    """Fill every required field answerable from `profile.screening_answers`.

    Never guesses: a required field with no matching entry is collected and
    returned, never filled with a plausible-sounding default. Fields already
    handled directly from `profile` (name/email/phone/file uploads) are
    skipped so they are neither double-filled nor misreported as
    unanswered.
    """
    unanswered: list[str] = []
    required = form_root.locator("[required], [aria-required='true']")

    try:
        count = await required.count()
    except Exception:  # pragma: no cover - defensive against a torn-down frame
        return unanswered

    answers = profile.screening_answers or {}

    for index in range(count):
        field = required.nth(index)
        try:
            tag = (await field.evaluate("el => el.tagName.toLowerCase()")) or ""
            field_type = (await field.get_attribute("type")) or ""
            name_attr = await field.get_attribute("name")
        except Exception:  # pragma: no cover - defensive against a stale handle
            continue

        if tag == "input" and field_type.lower() == "file":
            continue  # resume/cover-letter uploads are handled separately
        if name_attr in _STANDARD_FIELD_NAMES:
            continue

        label = (
            await field.get_attribute("aria-label")
            or name_attr
            or await field.get_attribute("placeholder")
            or f"unlabeled_required_field_{index}"
        )

        answer = answers.get(label)
        if answer is None:
            unanswered.append(label)
            continue

        try:
            if tag == "select":
                await field.select_option(label=answer)
            elif field_type.lower() in ("checkbox", "radio"):
                await field.check()
            else:
                await field.fill(answer)
        except Exception as exc:
            raise ExtractionError(
                f"filling screening question field {label!r} failed: {exc}"
            ) from exc

    return unanswered


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
                f"clicking the native Indeed Apply button failed: {exc}"
            ) from exc

        form_root = await _wait_for_apply_form_root(page)

        if await _any_selector_present(form_root, _CAPTCHA_SELECTORS):
            return ApplyResult(job_id=job_id, submitted=False, blocked_reason="captcha_wall")

        if await _any_selector_present(form_root, _ACCOUNT_LOGIN_SELECTORS):
            return ApplyResult(
                job_id=job_id, submitted=False, blocked_reason="account_creation_required"
            )

        await _fill_first(form_root, _NAME_INPUT_SELECTORS, profile.full_name)
        await _fill_first(form_root, _EMAIL_INPUT_SELECTORS, profile.email)
        await _fill_first(form_root, _PHONE_INPUT_SELECTORS, profile.phone)

        try:
            await _upload_first(form_root, _RESUME_FILE_INPUT_SELECTORS, profile.resume_path)
        except Exception as exc:
            raise ExtractionError(
                f"uploading resume from {profile.resume_path!r} failed: {exc}"
            ) from exc

        if profile.cover_letter_path:
            try:
                await _upload_first(
                    form_root, _COVER_LETTER_FILE_INPUT_SELECTORS, profile.cover_letter_path
                )
            except Exception as exc:
                raise ExtractionError(
                    f"uploading cover letter from {profile.cover_letter_path!r} failed: {exc}"
                ) from exc

        unanswered = await _answer_screening_questions(form_root, profile)
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
