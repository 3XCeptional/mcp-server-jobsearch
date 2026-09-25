"""Apply-flow tests for apply.py against hand-built fixture pages.

Loads tests/fixtures/indeed_apply_modal_sample.html (hand-written markup,
not scraped from a live page) into a real headless Playwright Chromium page
via `page.set_content()`, same pattern as test_job_pages_dom.py. No network
access happens anywhere in this file.

The account-login-wall case uses a small inline HTML string instead of the
shared fixture file, matching test_job_pages_dom.py's own convention of
keeping edge-case markup inline and reserving the fixture file for the
"happy path" sample.

Honest limitation: there is no live Indeed backend to confirm a real
submission against. The "normal form" fixture's own submit handler mutates
its iframe's document with the same success-text marker
apply._SUCCESS_TEXT_MARKERS looks for, so JobApplier's real, unmodified
`_wait_for_submission_success` / `_any_frame_contains_success_text` logic
runs unstubbed end to end in these tests. What this proves: the detection
logic correctly recognizes a real confirmation signal when one appears.
What it does NOT prove: that Indeed's actual production markup/selectors
match what apply.py looks for - that can only be confirmed against a live
job page, which is out of scope here by design (no live-site scraping in
tests).

No `pytest-asyncio` plugin is installed for this project, so each test
drives its own event loop with a plain `asyncio.run()` around an async
helper, rather than declaring `async def test_...` functions directly - via
the shared `html_page_runner` fixture in conftest.py, which owns the
browser launch/set_content/close boilerplate.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from indeed_mcp_server.apply import JobApplier

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "indeed_apply_modal_sample.html"

# Hand-built stand-in for an Indeed Apply modal that demands a fresh account
# login (email/password) before showing any application fields - the wall
# JobApplier must detect and stop at, never push through.
_ACCOUNT_LOGIN_GATE_HTML = """
<!DOCTYPE html>
<html>
<body>
<button data-testid="indeedApplyButton" onclick="window.__openModal()">Apply now</button>
<div id="wrap" style="display:none"></div>
<script>
window.__openModal = function () {
  document.getElementById('wrap').style.display = 'block';
  var iframe = document.createElement('iframe');
  iframe.id = 'indeedapply-modal-iframe-1';
  document.getElementById('wrap').appendChild(iframe);
  var doc = iframe.contentDocument;
  doc.open();
  doc.write('<html><body><h2>Sign in to continue</h2>' +
    '<input type="email" name="email"/>' +
    '<input type="password" name="password"/>' +
    '<button>Sign in</button></body></html>');
  doc.close();
};
</script>
</body>
</html>
"""


class _FakeSession:
    """Minimal stand-in exposing the `.page` property `ScrapingSession` has."""

    def __init__(self, page: Any) -> None:
        self.page = page


class _FakeNavigator:
    """Stands in for `PageNavigator`.

    `goto()` is a no-op because the fixture page's content is already loaded
    via `page.set_content()` - there is nothing to navigate to. The real
    `PageNavigator` only exposes its page via the private `_session`
    attribute (see `job_pages._navigator_page`'s docstring), so this fake
    mirrors that exactly rather than adding a public `.session` shortcut,
    to exercise the same attribute-resolution path `apply._resolve_page`
    uses against the real class.
    """

    def __init__(self, page: Any) -> None:
        self._session = _FakeSession(page)

    async def goto(self, *_args: object, **_kwargs: object) -> None:
        return None


async def _apply(page: Any, profile) -> Any:
    navigator = _FakeNavigator(page)
    applier = JobApplier(navigator)
    return await applier.apply_to_job("abc123", profile)


def _run_apply(html_page_runner, html: str, profile):
    return html_page_runner(html, lambda page: _apply(page, profile))


def test_account_login_wall_blocks_without_creating_account(html_page_runner, make_applicant_profile):
    profile = make_applicant_profile(
        # Never reached: the login wall must stop the flow before any file
        # upload is attempted, so this path does not need to exist.
        resume_path="/nonexistent/resume.pdf",
    )

    result = _run_apply(html_page_runner, _ACCOUNT_LOGIN_GATE_HTML, profile)

    assert result.submitted is False
    assert result.blocked_reason == "account_creation_required"
    assert result.screenshot_path is None
    assert result.unanswered_fields == ()


def test_normal_form_fills_and_submits(html_page_runner, make_applicant_profile):
    profile = make_applicant_profile(
        screening_answers={
            "How did you hear about us?": "Indeed",
            "Notice period (weeks)": "2",
        },
    )
    html = _FIXTURE_PATH.read_text(encoding="utf-8")

    result = _run_apply(html_page_runner, html, profile)

    assert result.blocked_reason is None
    assert result.unanswered_fields == ()
    assert result.submitted is True
    assert result.screenshot_path is not None
    assert Path(result.screenshot_path).exists()


def test_normal_form_reports_unanswered_required_field_without_submitting(html_page_runner, make_applicant_profile):
    profile = make_applicant_profile(
        # "Notice period (weeks)" is deliberately left unanswered - the tool
        # must flag it, never guess a plausible-sounding default.
        screening_answers={"How did you hear about us?": "Indeed"},
    )
    html = _FIXTURE_PATH.read_text(encoding="utf-8")

    result = _run_apply(html_page_runner, html, profile)

    assert result.submitted is False
    assert result.blocked_reason == "unsupported_apply_flow"
    assert "Notice period (weeks)" in result.unanswered_fields
    assert result.screenshot_path is None
