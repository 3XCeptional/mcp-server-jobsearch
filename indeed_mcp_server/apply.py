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

# Attachment-upload safety limits: `resume_path`/`cover_letter_path` are
# caller-supplied and go straight to Playwright's `set_input_files()`, which
# reads and uploads whatever bytes live at that path to a real third-party
# employer's form. These bound what can be uploaded to something that looks
# like an actual resume/cover letter.
_ALLOWED_ATTACHMENT_SUFFIXES = frozenset({".pdf", ".doc", ".docx", ".txt", ".rtf"})
_MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024  # 10 MB

# Truthy-shaped answer strings that mean "check this checkbox". Anything
# else (including an explicit "no") leaves it unchecked - the previous
# behavior of unconditionally calling `.check()` ignored the answer
# entirely.
_CHECKBOX_TRUTHY_ANSWERS = frozenset({"yes", "true", "1", "y", "on", "agree", "i agree"})

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


def _sensitive_attachment_directories() -> tuple[Path, ...]:
    """Directories a resume/cover-letter path must never resolve under.

    Defense-in-depth against exactly the credential-exfiltration scenario an
    injected/malicious `resume_path` enables: uploading the bytes of an SSH
    key, AWS credentials, a GPG keyring, or an OS credential store to a
    real third-party employer's form as a "resume".
    """
    home = Path.home()
    return (
        home / ".ssh",
        home / ".aws",
        home / ".gnupg",
        Path("/etc"),
        home / "Library" / "Keychains",
    )


def _validate_attachment_path(path: str, *, kind: str) -> str:
    """Resolve and validate a caller-supplied attachment path before upload.

    Raises `ExtractionError` (never uploads) unless the path resolves to an
    existing regular file, with a document-shaped extension, under the
    10 MB size cap, and outside every directory in
    `_sensitive_attachment_directories()`. Returns the resolved, absolute
    path string to actually hand to Playwright.
    """
    resolved = Path(path).expanduser().resolve()

    if not resolved.is_file():
        raise ExtractionError(
            f"{kind} path {path!r} does not resolve to an existing file "
            f"(resolved: {resolved})"
        )

    if resolved.suffix.lower() not in _ALLOWED_ATTACHMENT_SUFFIXES:
        raise ExtractionError(
            f"{kind} path {path!r} has an unsupported extension {resolved.suffix!r} "
            f"(allowed: {sorted(_ALLOWED_ATTACHMENT_SUFFIXES)})"
        )

    size = resolved.stat().st_size
    if size > _MAX_ATTACHMENT_BYTES:
        raise ExtractionError(
            f"{kind} path {path!r} is {size} bytes, over the "
            f"{_MAX_ATTACHMENT_BYTES}-byte cap"
        )

    for sensitive_dir in _sensitive_attachment_directories():
        if resolved.is_relative_to(sensitive_dir):
            raise ExtractionError(
                f"{kind} path {path!r} resolves under a sensitive directory "
                f"({sensitive_dir}) and was refused"
            )

    return str(resolved)


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


def _is_external_domain(url: str) -> bool:
    """True if `url`'s host is neither exactly indeed.com nor a subdomain of it.

    A plain `netloc.endswith("indeed.com")` check is fooled by a lookalike
    host - `"notindeed.com".endswith("indeed.com")` is `True` in Python -
    so this requires an exact match or a dot-separated subdomain instead.
    """
    netloc = urlparse(url).netloc.lower()
    return not (netloc == "indeed.com" or netloc.endswith(".indeed.com"))


async def _click_reveals_external_domain(page: Any, locator: Any, wait_ms: int = 1500) -> bool:
    """Click an "apply on company site" control and check if it left indeed.com.

    Checks both a same-tab navigation and a newly opened tab/page, per the
    two ways Indeed is known to hand off to an external ATS. If the click
    itself fails outright, the control's mere presence (labeled as an
    external apply link) is treated as sufficient evidence on its own.

    A newly opened off-domain tab is closed before returning: it was only
    ever needed to inspect its URL, and leaving it open leaks a Playwright
    Page for the lifetime of the shared browser context.
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

    is_external = _is_external_domain(getattr(target, "url", "") or "")

    if target is not page:
        try:
            await target.close()
        except Exception:  # pragma: no cover - best-effort cleanup
            pass

    return is_external


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

    # Tracks how many times each raw (aria-label/name/placeholder) label has
    # been seen so far in this call. Two genuinely distinct fields that
    # happen to share the same non-empty `name` (e.g. two differently
    # purposed inputs both named "phone") would otherwise silently collide
    # on one answer-lookup key - the `unlabeled_required_field_{index}`
    # fallback is already unique per field, but that fallback is only
    # reached when none of aria-label/name/placeholder is set.
    seen_label_counts: dict[str, int] = {}

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

        occurrence = seen_label_counts.get(label, 0)
        seen_label_counts[label] = occurrence + 1
        if occurrence:
            label = f"{label}_{occurrence + 1}"

        answer = answers.get(label)
        if answer is None:
            unanswered.append(label)
            continue

        try:
            if tag == "select":
                await field.select_option(label=answer)
            elif field_type.lower() == "checkbox":
                if answer.strip().lower() in _CHECKBOX_TRUTHY_ANSWERS:
                    await field.check()
                else:
                    await field.uncheck()
            elif field_type.lower() == "radio":
                await _maybe_check_matching_radio(form_root, field, answer)
            else:
                await field.fill(answer)
        except Exception as exc:
            raise ExtractionError(
                f"filling screening question field {label!r} failed: {exc}"
            ) from exc

    return unanswered


async def _maybe_check_matching_radio(form_root: Any, field: Any, answer: str) -> None:
    """Check `field` only if `answer` names this specific radio option.

    A radio input's `name` groups it with its siblings, but each option in
    the group is a distinct choice: unconditionally checking every radio
    the loop visits (the previous behavior) would check every option in
    every group regardless of what was actually answered. This compares
    `answer` against the option's own `value` attribute and its associated
    `<label for="...">` text, and only checks this one field on a match -
    a non-matching radio is left alone (not an error - a sibling option may
    match on a later loop iteration).
    """
    field_value = (await field.get_attribute("value")) or ""
    field_label_text = ""
    field_id = await field.get_attribute("id")
    if field_id:
        label_locator = form_root.locator(f'label[for="{field_id}"]')
        try:
            if await label_locator.count() > 0:
                field_label_text = (await label_locator.first.text_content()) or ""
        except Exception:  # pragma: no cover - defensive against a torn-down frame
            field_label_text = ""

    candidates = {field_value.strip().lower(), field_label_text.strip().lower()} - {""}
    if answer.strip().lower() in candidates:
        await field.check()


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
