"""Apply-flow tests for seek_apply.py against hand-built fixture pages.

Mirrors test_apply.py's structure and coverage exactly, adapted for
SeekJobApplier. Loads tests/fixtures/seek_apply_modal_sample.html
(hand-written markup, not scraped from a live page) into a real headless
Playwright Chromium page via `page.set_content()`, same pattern as
test_apply.py and test_job_pages_dom.py. No network access happens anywhere
in this file.

The account-login-wall case uses a small inline HTML string instead of the
shared fixture file, matching test_apply.py's own convention of keeping
edge-case markup inline and reserving the fixture file for the "happy path"
sample.

Several cases here (path validation, checkbox/radio matching) exercise
`apply_common`'s shared logic through `seek_apply.py`'s own call sites -
this is deliberate: the point is to confirm `SeekJobApplier` actually wires
into the shared, already-battle-tested `apply_common` functions rather than
silently reimplementing (or skipping) them, not to re-prove `apply_common`'s
own correctness a second time (that's `test_apply.py`'s job).

Honest limitation: same as test_apply.py - there is no live Seek backend to
confirm a real submission against. The "normal form" fixture's own submit
handler mutates its iframe's document with the same success-text marker
apply_common._SUCCESS_TEXT_MARKERS looks for, so SeekJobApplier's real,
unmodified `_wait_for_submission_success` logic runs unstubbed end to end
against this fixture. What this proves: the detection logic correctly
recognizes a real confirmation signal when one appears. What it does NOT
prove: that Seek's actual production markup/selectors match what
seek_apply.py looks for (all marked [INFERENCE] in that module) - that can
only be confirmed against a live job page, out of scope here by design.

No `pytest-asyncio` plugin is installed for this project, so each test
drives its own event loop with a plain `asyncio.run()` around an async
helper, via the shared `html_page_runner` fixture in conftest.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jobsearch_mcp_server import apply_common as apply_common_module
from jobsearch_mcp_server import seek_apply as seek_apply_module
from jobsearch_mcp_server.contracts import ExtractionError
from jobsearch_mcp_server.seek_apply import SeekJobApplier

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "seek_apply_modal_sample.html"

# Hand-built stand-in for a Seek Quick Apply modal that demands a fresh
# account login (email/password) before showing any application fields -
# the wall SeekJobApplier must detect and stop at, never push through.
# Reuses the same generic account-login-wall shape apply.py's own test uses
# (a password field), since that detection is genuinely cross-site.
_ACCOUNT_LOGIN_GATE_HTML = """
<!DOCTYPE html>
<html>
<body>
<button data-automation="job-detail-apply" onclick="window.__openModal()">Quick apply</button>
<div id="wrap" style="display:none"></div>
<script>
window.__openModal = function () {
  document.getElementById('wrap').style.display = 'block';
  var iframe = document.createElement('iframe');
  iframe.id = 'seek-apply-modal-iframe-1';
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

    Mirrors test_apply.py's `_FakeNavigator` exactly - see that class's
    docstring for why `.page` is exposed via the private `_session`
    attribute rather than a public shortcut.
    """

    def __init__(self, page: Any) -> None:
        self._session = _FakeSession(page)

    async def goto(self, *_args: object, **_kwargs: object) -> None:
        return None


async def _apply(page: Any, profile) -> Any:
    navigator = _FakeNavigator(page)
    applier = SeekJobApplier(navigator)
    return await applier.apply_to_job("93326286", profile)


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
            "How did you hear about us?": "Seek",
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
        screening_answers={"How did you hear about us?": "Seek"},
    )
    html = _FIXTURE_PATH.read_text(encoding="utf-8")

    result = _run_apply(html_page_runner, html, profile)

    assert result.submitted is False
    assert result.blocked_reason == "unsupported_apply_flow"
    assert "Notice period (weeks)" in result.unanswered_fields
    assert result.screenshot_path is None


# ---------------------------------------------------------------------------
# A core field selector miss must block, not false-submit (mirrors
# test_apply.py's FIX 3 coverage).
# ---------------------------------------------------------------------------

_MISSING_NAME_SELECTOR_HTML = """
<!DOCTYPE html>
<html>
<body>
  <button data-automation="job-detail-apply" onclick="window.__openSeekApplyModal()">Quick apply</button>
  <div id="wrap" style="display:none"></div>
  <script>
    window.__openSeekApplyModal = function () {
      document.getElementById('wrap').style.display = 'block';
      var iframe = document.createElement('iframe');
      iframe.id = 'seek-apply-modal-iframe-1';
      document.getElementById('wrap').appendChild(iframe);
      var doc = iframe.contentDocument;
      doc.open();
      doc.write(`
        <html>
          <body>
            <form id="seek-apply-form">
              <input type="text" data-automation="candidate-full-name" placeholder="Full name" />
              <input type="email" data-automation="applicantEmail" placeholder="Email" />
              <input type="tel" data-automation="applicantPhone" placeholder="Phone" />
              <input type="file" data-automation="resumeUpload" />
              <button type="submit" id="submit-application">Submit application</button>
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
    # `data-automation="candidate-full-name"`, simulating page-structure
    # drift / an [INFERENCE] selector guess that missed real Seek markup.
    profile = make_applicant_profile()

    result = _run_apply(html_page_runner, _MISSING_NAME_SELECTOR_HTML, profile)

    assert result.submitted is False
    assert result.blocked_reason == "unsupported_apply_flow"
    assert "full_name" in result.unanswered_fields


# ---------------------------------------------------------------------------
# Checkbox/radio answer matching, exercised through seek_apply.py's own
# `_STANDARD_FIELD_NAMES` and call into the shared `apply_common` logic.
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
            unanswered = await apply_common_module._answer_screening_questions(
                page, profile, seek_apply_module._STANDARD_FIELD_NAMES
            )
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
# Path-validated attachment rejection - exercises apply_common's shared
# `_validate_attachment_path` through seek_apply.py's own call site.
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


def test_resume_path_under_home_ssh_directory_is_rejected(html_page_runner, make_applicant_profile, monkeypatch, tmp_path):
    fake_home = tmp_path / "fake_home"
    ssh_dir = fake_home / ".ssh"
    ssh_dir.mkdir(parents=True)
    fake_key = ssh_dir / "id_rsa.pdf"  # allowed extension, on purpose: this
    # test proves the sensitive-directory check blocks it independent of
    # the extension check.
    fake_key.write_bytes(b"not actually a key")

    monkeypatch.setattr(apply_common_module.Path, "home", staticmethod(lambda: fake_home))

    profile = make_applicant_profile(resume_path=str(fake_key))
    html = _FIXTURE_PATH.read_text(encoding="utf-8")

    with pytest.raises(ExtractionError, match="sensitive directory"):
        _run_apply(html_page_runner, html, profile)


# ---------------------------------------------------------------------------
# External-domain handoff uses Seek's own domain suffix, not Indeed's.
# ---------------------------------------------------------------------------


def test_is_external_domain_uses_seek_domain_suffix():
    assert apply_common_module._is_external_domain(
        "https://notseek.com.au/job", "seek.com.au"
    ) is True
    assert apply_common_module._is_external_domain(
        "https://seek.com.au/job", "seek.com.au"
    ) is False
    assert apply_common_module._is_external_domain(
        "https://www.seek.com.au/job", "seek.com.au"
    ) is False
