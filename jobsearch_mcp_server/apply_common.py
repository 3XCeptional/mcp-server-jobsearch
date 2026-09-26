"""Site-agnostic apply-flow helpers shared by Indeed's and Seek's appliers.

Extracted out of `apply.py` (originally written Indeed-only) once Seek's own
apply flow needed the exact same safety-critical logic: pure Playwright
locator helpers, file-path validation, CAPTCHA detection, and
success-text/confirmation detection. None of this module's code knows or
cares which site it's driving - a site-specific `JobApplier`/`SeekJobApplier`
supplies its own selectors/domain/field-name set and calls into these.

MECHANICAL ONLY, same discipline as apply.py's module docstring: nothing
here decides whether a candidate should apply, fact-checks, or invents a
field value; nothing here types a username/password or solves/bypasses a
CAPTCHA - those conditions are only ever detected and reported upward.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from jobsearch_mcp_server.contracts import ApplicantProfile, ExtractionError

# CAPTCHA/bot-check providers are cross-site by nature (reCAPTCHA, hCaptcha,
# and Cloudflare Turnstile all get embedded verbatim regardless of which
# site is hosting them), so this selector list is not Indeed- or
# Seek-specific.
_CAPTCHA_SELECTORS = (
    "iframe[src*='recaptcha']",
    "iframe[title*='recaptcha' i]",
    ".g-recaptcha",
    "iframe[src*='hcaptcha']",
    ".h-captcha",
    ".cf-turnstile",
    "#challenge-running",
)

# Attachment-upload safety limits: `resume_path`/`cover_letter_path` are
# caller-supplied and go straight to Playwright's `set_input_files()`, which
# reads and uploads whatever bytes live at that path to a real third-party
# employer's form. These bound what can be uploaded to something that looks
# like an actual resume/cover letter. Site-agnostic: the same limits apply
# regardless of which job board the upload is headed to.
_ALLOWED_ATTACHMENT_SUFFIXES = frozenset({".pdf", ".doc", ".docx", ".txt", ".rtf"})
_MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024  # 10 MB

# Truthy-shaped answer strings that mean "check this checkbox". Anything
# else (including an explicit "no") leaves it unchecked - unconditionally
# calling `.check()` would ignore the answer entirely.
_CHECKBOX_TRUTHY_ANSWERS = frozenset({"yes", "true", "1", "y", "on", "agree", "i agree"})

_SUCCESS_TEXT_MARKERS = (
    "application submitted",
    "your application has been submitted",
    "you applied",
    "application sent",
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


def _is_external_domain(url: str, allowed_suffix: str) -> bool:
    """True if `url`'s host is neither exactly `allowed_suffix` nor a subdomain of it.

    A plain `netloc.endswith(allowed_suffix)` check is fooled by a lookalike
    host - `"not" + allowed_suffix` would satisfy `.endswith()` even though
    it's a different domain entirely - so this requires an exact match or a
    dot-separated subdomain instead. `allowed_suffix` is supplied by the
    caller (e.g. `"indeed.com"` or `"seek.com.au"`) so this one function
    serves every site's applier.
    """
    netloc = urlparse(url).netloc.lower()
    return not (netloc == allowed_suffix or netloc.endswith(f".{allowed_suffix}"))


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
    successful apply submission commonly mutates a modal iframe's own
    document rather than navigating the outer page.
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


async def _maybe_check_matching_radio(form_root: Any, field: Any, answer: str) -> None:
    """Check `field` only if `answer` names this specific radio option.

    A radio input's `name` groups it with its siblings, but each option in
    the group is a distinct choice: unconditionally checking every radio
    the loop visits would check every option in every group regardless of
    what was actually answered. This compares `answer` against the option's
    own `value` attribute and its associated `<label for="...">` text, and
    only checks this one field on a match - a non-matching radio is left
    alone (not an error - a sibling option may match on a later loop
    iteration).
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


async def _answer_screening_questions(
    form_root: Any,
    profile: ApplicantProfile,
    standard_field_names: frozenset[str],
    *,
    standard_field_attribute: str = "name",
) -> list[str]:
    """Fill every required field answerable from `profile.screening_answers`.

    Never guesses: a required field with no matching entry is collected and
    returned, never filled with a plausible-sounding default. Fields already
    handled directly from `profile` (name/email/phone/file uploads) are
    identified by `standard_field_names` - the site-specific set of values a
    core field carries on the HTML attribute named by
    `standard_field_attribute` - and skipped here so they are neither
    double-filled nor misreported as unanswered.

    `standard_field_attribute` defaults to `"name"` (Indeed's core fields are
    identified by their `name` attribute), but a caller whose site instead
    identifies its core fields by a different attribute (e.g. Seek's
    `data-automation`) must pass that attribute name explicitly - comparing
    `standard_field_names` against the wrong attribute means the exclusion
    can never match, so every core field gets treated as an unanswerable
    screening question.
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

        # The exclusion check compares against whichever attribute this
        # site's core fields are actually identified by - reusing `name_attr`
        # when that attribute is "name" avoids a redundant second
        # `get_attribute()` call for Indeed's (default) call site.
        excluded_value = (
            name_attr
            if standard_field_attribute == "name"
            else await field.get_attribute(standard_field_attribute)
        )
        if excluded_value in standard_field_names:
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
