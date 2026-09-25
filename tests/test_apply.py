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

import pytest

from indeed_mcp_server import apply as apply_module
from indeed_mcp_server.apply import JobApplier
from indeed_mcp_server.contracts import ExtractionError

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


# ---------------------------------------------------------------------------
# FIX 1 (security): resume_path/cover_letter_path validation.
# ---------------------------------------------------------------------------


def test_resume_path_with_disallowed_extension_is_rejected(html_page_runner, make_applicant_profile, tmp_path):
    bad_path = tmp_path / "resume.exe"
    bad_path.write_bytes(b"not a real resume")
    profile = make_applicant_profile(resume_path=str(bad_path))
    html = _FIXTURE_PATH.read_text(encoding="utf-8")

    with pytest.raises(ExtractionError, match="extension"):
        _run_apply(html_page_runner, html, profile)


def test_resume_path_that_does_not_exist_is_rejected(html_page_runner, make_applicant_profile):
    profile = make_applicant_profile(resume_path="/nonexistent/path/resume.pdf")
    html = _FIXTURE_PATH.read_text(encoding="utf-8")

    with pytest.raises(ExtractionError, match="does not resolve to an existing file"):
        _run_apply(html_page_runner, html, profile)


def test_resume_path_over_size_cap_is_rejected(html_page_runner, make_applicant_profile, tmp_path, monkeypatch):
    monkeypatch.setattr(apply_module, "_MAX_ATTACHMENT_BYTES", 10)
    big_path = tmp_path / "resume.pdf"
    big_path.write_bytes(b"x" * 100)
    profile = make_applicant_profile(resume_path=str(big_path))
    html = _FIXTURE_PATH.read_text(encoding="utf-8")

    with pytest.raises(ExtractionError, match="bytes"):
        _run_apply(html_page_runner, html, profile)


def test_resume_path_under_home_ssh_directory_is_rejected(html_page_runner, make_applicant_profile, monkeypatch, tmp_path):
    fake_home = tmp_path / "fake_home"
    ssh_dir = fake_home / ".ssh"
    ssh_dir.mkdir(parents=True)
    fake_key = ssh_dir / "id_rsa.pdf"  # allowed extension, on purpose: this
    # test proves the sensitive-directory check blocks it independent of
    # the extension check.
    fake_key.write_bytes(b"not actually a key")

    monkeypatch.setattr(apply_module.Path, "home", staticmethod(lambda: fake_home))

    profile = make_applicant_profile(resume_path=str(fake_key))
    html = _FIXTURE_PATH.read_text(encoding="utf-8")

    with pytest.raises(ExtractionError, match="sensitive directory"):
        _run_apply(html_page_runner, html, profile)


# ---------------------------------------------------------------------------
# FIX 2 (logical): checkbox/radio answers must actually be consulted.
# ---------------------------------------------------------------------------

_CHECKBOX_RADIO_HTML = """
<!DOCTYPE html>
<html>
<body>
<input type="checkbox" id="relocate" aria-label="Willing to relocate?" required />

<label for="shift-morning">Morning</label>
<input type="radio" id="shift-morning" name="shift" value="morning" aria-label="Shift preference" required />
<label for="shift-evening">Evening</label>
<input type="radio" id="shift-evening" name="shift" value="evening" aria-label="Shift preference" required />
</body>
</html>
"""


def _run_answer_screening(html_page_runner, html: str, profile):
    def _run(page):
        async def _inner():
            unanswered = await apply_module._answer_screening_questions(page, profile)
            relocate_checked = await page.locator("#relocate").is_checked()
            morning_checked = await page.locator("#shift-morning").is_checked()
            evening_checked = await page.locator("#shift-evening").is_checked()
            return unanswered, relocate_checked, morning_checked, evening_checked

        return _inner()

    return html_page_runner(html, _run)


def test_checkbox_no_shaped_answer_is_not_checked(html_page_runner, make_applicant_profile):
    profile = make_applicant_profile(
        screening_answers={
            "Willing to relocate?": "No",
            "Shift preference": "evening",
            "Shift preference_2": "evening",
        },
    )

    unanswered, relocate_checked, _morning, _evening = _run_answer_screening(
        html_page_runner, _CHECKBOX_RADIO_HTML, profile
    )

    assert unanswered == []
    assert relocate_checked is False


def test_checkbox_yes_shaped_answer_is_checked(html_page_runner, make_applicant_profile):
    profile = make_applicant_profile(
        screening_answers={
            "Willing to relocate?": "Yes",
            "Shift preference": "evening",
            "Shift preference_2": "evening",
        },
    )

    unanswered, relocate_checked, _morning, _evening = _run_answer_screening(
        html_page_runner, _CHECKBOX_RADIO_HTML, profile
    )

    assert unanswered == []
    assert relocate_checked is True


def test_radio_group_only_checks_the_matching_option(html_page_runner, make_applicant_profile):
    profile = make_applicant_profile(
        screening_answers={
            "Willing to relocate?": "no",
            # Both radios share the same aria-label ("Shift preference"),
            # so the second is disambiguated to "Shift preference_2" by the
            # FIX 6 label-collision fix; both map to the same desired
            # option text so only the matching radio ends up checked.
            "Shift preference": "evening",
            "Shift preference_2": "evening",
        },
    )

    unanswered, _relocate, morning_checked, evening_checked = _run_answer_screening(
        html_page_runner, _CHECKBOX_RADIO_HTML, profile
    )

    assert unanswered == []
    assert morning_checked is False
    assert evening_checked is True


# ---------------------------------------------------------------------------
# FIX 3 (logical): a core field selector miss must block, not false-submit.
# ---------------------------------------------------------------------------

_MISSING_NAME_SELECTOR_HTML = """
<!DOCTYPE html>
<html>
<body>
  <button data-testid="indeedApplyButton" onclick="window.__openIndeedApplyModal()">Apply now</button>
  <div id="wrap" style="display:none"></div>
  <script>
    window.__openIndeedApplyModal = function () {
      document.getElementById('wrap').style.display = 'block';
      var iframe = document.createElement('iframe');
      iframe.id = 'indeedapply-modal-iframe-1';
      document.getElementById('wrap').appendChild(iframe);
      var doc = iframe.contentDocument;
      doc.open();
      doc.write(`
        <html>
          <body>
            <form id="indeed-apply-form">
              <input type="text" name="candidate-full-name" placeholder="Full name" />
              <input type="email" name="applicant.email" placeholder="Email" />
              <input type="tel" name="applicant.phoneNumber" placeholder="Phone" />
              <input type="file" name="resume-upload" />
              <button type="submit" id="submit-application">Submit your application</button>
            </form>
          </body>
        </html>
      `);
      doc.close();
    };
  </script>
</body>
</html>
"""


def test_missing_name_selector_blocks_instead_of_false_submit(html_page_runner, make_applicant_profile):
    # No selector in `_NAME_INPUT_SELECTORS` matches
    # `name="candidate-full-name"`, simulating page-structure drift.
    profile = make_applicant_profile()

    result = _run_apply(html_page_runner, _MISSING_NAME_SELECTOR_HTML, profile)

    assert result.submitted is False
    assert result.blocked_reason == "unsupported_apply_flow"
    assert "full_name" in result.unanswered_fields


# ---------------------------------------------------------------------------
# FIX 4 (design): an orphaned off-domain tab must be closed, not leaked.
# ---------------------------------------------------------------------------

_EXTERNAL_APPLY_NEW_TAB_HTML = """
<!DOCTYPE html>
<html>
<body>
<a href="#" onclick="window.open('about:blank', '_blank'); return false;">Apply on company site</a>
</body>
</html>
"""


def test_external_apply_click_closes_the_orphaned_new_tab(html_page_runner):
    def _run(page):
        async def _inner():
            locator = page.locator('a:has-text("Apply on company site")')
            context = page.context
            pages_before = set(context.pages)
            is_external = await apply_module._click_reveals_external_domain(page, locator)
            leaked_pages = set(context.pages) - pages_before
            return is_external, leaked_pages

        return _inner()

    is_external, leaked_pages = html_page_runner(_EXTERNAL_APPLY_NEW_TAB_HTML, _run)

    assert is_external is True
    assert leaked_pages == set()


# ---------------------------------------------------------------------------
# FIX 5 (logical): domain-suffix check needs a dot boundary.
# ---------------------------------------------------------------------------


def test_is_external_domain_requires_a_dot_boundary():
    assert apply_module._is_external_domain("https://notindeed.com/job") is True
    assert apply_module._is_external_domain("https://evilindeed.com/job") is True
    assert apply_module._is_external_domain("https://indeed.com/job") is False
    assert apply_module._is_external_domain("https://au.indeed.com/job") is False
    assert apply_module._is_external_domain("") is True


# ---------------------------------------------------------------------------
# FIX 6 (design): two distinct required fields must not collide on one
# answer-lookup key just because they share the same `name` attribute.
# ---------------------------------------------------------------------------

_DUPLICATE_NAME_FIELDS_HTML = """
<!DOCTYPE html>
<html>
<body>
  <button data-testid="indeedApplyButton" onclick="window.__openIndeedApplyModal()">Apply now</button>
  <div id="wrap" style="display:none"></div>
  <script>
    window.__openIndeedApplyModal = function () {
      document.getElementById('wrap').style.display = 'block';
      var iframe = document.createElement('iframe');
      iframe.id = 'indeedapply-modal-iframe-1';
      document.getElementById('wrap').appendChild(iframe);
      var doc = iframe.contentDocument;
      doc.open();
      doc.write(`
        <html>
          <body>
            <form id="indeed-apply-form">
              <input type="text" name="applicant.name" />
              <input type="email" name="applicant.email" />
              <input type="tel" name="applicant.phoneNumber" />
              <input type="file" name="resume-upload" />

              <input name="dept" required />
              <input name="dept" required />

              <button type="submit" id="submit-application">Submit your application</button>
            </form>
          </body>
        </html>
      `);
      doc.close();
    };
  </script>
</body>
</html>
"""


def test_duplicate_name_fields_are_disambiguated_not_collided(html_page_runner, make_applicant_profile):
    # Only "dept" is answered. If the second field silently collided on the
    # same "dept" key (the pre-fix behavior), it would incorrectly be
    # treated as answered too and nothing would end up in unanswered_fields.
    # With the fix, the second field's key is "dept_2", which has no
    # matching answer, so it is correctly flagged.
    profile = make_applicant_profile(screening_answers={"dept": "Engineering"})

    result = _run_apply(html_page_runner, _DUPLICATE_NAME_FIELDS_HTML, profile)

    assert result.submitted is False
    assert result.blocked_reason == "unsupported_apply_flow"
    assert result.unanswered_fields == ("dept_2",)
