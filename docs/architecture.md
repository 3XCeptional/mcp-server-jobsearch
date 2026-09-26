# Architecture

This document describes the module layering, the session/browser
persistence model, and the safety model behind `apply_to_job`, as the code
actually behaves today (not as originally planned). It mirrors the pattern
used in `linkedin-mcp-server`'s `docs/scraping-architecture.md`, adapted to
this project's simpler, no-required-login shape.

## Module ownership

"Page-owning" means the module directly imports Playwright or holds a
reference to a live `Page`/`Locator`. "Browser-free" means the module does
neither and only orchestrates page-owning collaborators or does pure data
work (URL building, id parsing, text cleanup).

| Module | Public surface | Classification |
| --- | --- | --- |
| `__init__.py` | package marker | browser-free |
| `__main__.py` | `main()` (delegates to `cli_main`) | browser-free |
| `cli_main.py` | `build_parser()`, `main()` | browser-free |
| `contracts.py` | `JobSummary`, `JobDetail`, `ApplicantProfile`, `ApplyResult`, `ExtractionError` | browser-free |
| `identifiers.py` | `normalize_job_id()`, `job_view_url()` | browser-free |
| `seek_identifiers.py` | `normalize_seek_job_id()`, `seek_job_view_url()` | browser-free |
| `search_urls.py` | `build_job_search_url()`, `JOB_TYPE_MAP`, `DATE_POSTED_MAP` | browser-free |
| `seek_search_urls.py` | `build_seek_search_url()`, `WORK_TYPE_MAP`, `DATE_POSTED_MAP` | browser-free |
| `job_policy.py` | `RESULTS_PER_PAGE`, `MAX_SEARCH_PAGES`, `next_start_offset()` | browser-free |
| `text.py` | `strip_indeed_noise()`, `filter_indeed_noise_lines()` | browser-free |
| `jobs.py` | `JobScraper` | browser-free (orchestrates `JobPageReader`, never touches a `Page` directly) |
| `seek_jobs.py` | `SeekJobScraper` | browser-free (orchestrates `SeekJobPageReader`, never touches a `Page` directly) |
| `session.py` | `ScrapingSession` | page-owning (thin wrapper around a live `Page`) |
| `browser_launch.py` | `launch_persistent_browser()` | page-owning (only module that imports `playwright.async_api` directly) |
| `navigation.py` | `PageNavigator`, `WaitUntil` | page-owning |
| `authentication.py` | `ensure_logged_in()` | page-owning (reads page state only, never fills credentials) |
| `session_state.py` | `SessionManager` | page-owning (owns the process-lifetime session cache) |
| `job_pages.py` | `JobPageReader`, `parse_job_detail_from_page()`, `parse_search_results_from_page()` | page-owning |
| `seek_job_pages.py` | `SeekJobPageReader`, `parse_seek_job_detail_from_page()`, `parse_seek_search_results_from_page()` | page-owning |
| `apply_common.py` | `_answer_screening_questions()`, `_validate_attachment_path()`, `_wait_for_submission_success()`, `_is_external_domain()`, and other locator/CAPTCHA/attachment helpers shared by `apply.py` and `seek_apply.py` | page-owning (its helpers take and call methods directly on a live `Page`/`Locator`/`FrameLocator`, even though the module itself never imports `playwright`) |
| `apply.py` | `JobApplier` | page-owning |
| `seek_apply.py` | `SeekJobApplier` | page-owning |
| `extractor.py` | `IndeedExtractor`, `SeekExtractor` | page-owning (facade; delegates all page access to its collaborators) |
| `server.py` | `mcp`, `register_job_tools()`, `register_seek_job_tools()`, `main()` | page-owning transitively (constructs `IndeedExtractor` and `SeekExtractor`, exposes them as 8 MCP tools total) |

## Internal import graph

```
contracts        -> (none)
identifiers       -> (none)
seek_identifiers     -> (none)
search_urls        -> identifiers
seek_search_urls     -> seek_identifiers
job_policy        -> (none)
text          -> (none)
session         -> (none)
browser_launch      -> (none, imports playwright directly)
navigation        -> contracts, session
authentication      -> navigation
session_state      -> authentication, browser_launch, session
job_pages         -> contracts, identifiers, navigation, text
seek_job_pages      -> contracts, navigation, seek_identifiers
jobs           -> contracts, job_pages, job_policy, navigation, search_urls
seek_jobs         -> contracts, job_policy, navigation, seek_job_pages, seek_search_urls
apply_common       -> contracts
apply          -> apply_common, authentication, contracts, identifiers, navigation
seek_apply        -> apply, apply_common, authentication, contracts, navigation, seek_identifiers
extractor         -> apply, contracts, job_pages, jobs, navigation, seek_apply, seek_job_pages, seek_jobs, session_state
server          -> contracts, extractor, identifiers, seek_identifiers, session_state
cli_main         -> server (deferred import, inside main())
__main__         -> cli_main
```

The dependency direction is one-way: browser-free utility modules
(`contracts`, `identifiers`, `search_urls`, `job_policy`, `text`) sit at the
bottom and are imported by every layer above them but import nothing from
this package themselves (other than `identifiers` being imported by
`search_urls`). `session.py` is the only thing `navigation.py` depends on
from within the package. Everything that needs a live page goes through
`PageNavigator`, never around it.

## Session and browser persistence model

Single-process, single long-lived Playwright context, deliberately simpler
than the reference `linkedin-mcp-server` project:

- **`browser_launch.launch_persistent_browser()`** launches (or attaches
  to) a Chromium instance via
  `playwright.chromium.launch_persistent_context()`, pointed at a fixed
  on-disk profile directory (`~/.indeed-mcp-server/browser-profile` by
  default). The persistent profile directory is the entire persistence
  mechanism: any cookies from a manual login survive process restarts
  because they live in that Chromium profile, not because this code
  serializes or re-injects them.

- **`session_state.SessionManager`** is a process-lifetime cache of exactly
  one `ScrapingSession`. `get_or_create_session()` launches the browser only
  on the first call that actually needs one (constructing `SessionManager`
  itself is cheap and does nothing with Playwright); every subsequent call
  in the process returns the same session. There is no multi-process
  daemon, no election, and no profile fingerprinting, unlike the reference
  project, because Indeed's search and job-detail pages do not require an
  authenticated session at all.

- **No login is required for search or detail.** `authentication.ensure_logged_in()`
  is best-effort: it checks the current page for a Cloudflare
  challenge (`#challenge-running`, a "Just a moment" title) or an Indeed
  login-wall URL, and if neither is present, returns `True` immediately
  without touching a form. If one is present, it polls for up to 5 minutes
  (5-minute default, 2-second poll interval) so a human at the visible,
  non-headless browser window can clear it by hand; it never types a
  username or password itself. `SessionManager.get_or_create_session()`
  calls this once after launch but treats its return value as
  informational, not fatal: a blocked result does not stop the session from
  being handed back, since search/detail pages usually still work.

- **`close()`** closes the Playwright browser context and then stops the
  Playwright driver-manager connection itself (`await playwright.stop()`).
  The second step matters: without it, every `close()` followed by another
  `get_or_create_session()` call leaks one orphaned driver
  process/connection over the life of a long-running MCP server.

## Safety model for `apply_to_job`

`apply.JobApplier` has been through three rounds of security fixes. What it
actually does today:

- **Mechanical only.** Every value written into the form comes directly
  from the caller-supplied `ApplicantProfile`. The applier does not decide
  whether the candidate should apply, does not fact-check anything, and
  does not fabricate a plausible-sounding answer for any field.

- **Hard stops on CAPTCHA.** `_CAPTCHA_SELECTORS` covers reCAPTCHA,
  hCaptcha, Cloudflare Turnstile, and a generic challenge marker. This is
  checked both right after the apply form loads and again immediately
  before the final submit click (a CAPTCHA can appear mid-flow, not only at
  the top), and either check returns `blocked_reason="captcha_wall"`
  without attempting to solve or click through it.

- **Hard stop on account-creation/login walls.** `_ACCOUNT_LOGIN_SELECTORS`
  detects a password input, a "Sign in with Google"/"Continue with Google"
  button, or "sign in to your indeed account" text inside the apply form
  root, and returns `blocked_reason="account_creation_required"` rather
  than typing credentials or creating an account.

- **Refuses external ATS handoffs.** An "Apply on company site" control is
  clicked only to observe where it leads (`_click_reveals_external_domain()`);
  if the resulting page or a newly opened tab resolves to a domain other
  than `indeed.com` or a subdomain of it (checked with an exact/suffix
  match, not a naive `.endswith()` that a lookalike host like
  `notindeed.com` would pass), the applier reports
  `blocked_reason="external_ats_login_required"` and does not attempt to
  fill the third-party form. Any newly opened off-domain tab is closed
  immediately after the check so it doesn't leak a live Playwright `Page`
  for the rest of the browser session.

- **Hard stop on any unanswerable required field.** `_answer_screening_questions()`
  walks every `[required]`/`[aria-required="true"]` field in the form,
  skips the ones already handled directly from `profile` (name, email,
  phone, file uploads), and for every other required field looks up an
  answer by its label/name/placeholder in `profile.screening_answers`. A
  required field with no matching entry is collected, never guessed, and
  its presence causes `apply_to_job` to return
  `blocked_reason="unsupported_apply_flow"` with `unanswered_fields` naming
  every field that was missing an answer. The same field-name collision
  edge case (two distinct fields sharing one label) is handled by
  appending an occurrence suffix rather than letting the second field
  silently overwrite the first's answer key.

- **No multi-step wizard support (documented scope limit, not a bug).**
  `_run_apply_flow()` only fills the form snapshot present immediately
  after clicking the native "Apply now" control. If any of the four core
  fields (name, email, phone, resume) fails to fill on that snapshot, the
  call returns `blocked_reason="unsupported_apply_flow"` naming which
  fields were missing, rather than guessing that they live on a later page
  it never navigates to, and rather than reporting a false
  `submitted=True`.

- **Path-validated attachments.** `_validate_attachment_path()` resolves
  every `resume_path`/`cover_letter_path` to an absolute path and rejects
  it (raising `ExtractionError`, never uploading) unless it is an existing
  regular file, has a document-shaped extension
  (`.pdf`, `.doc`, `.docx`, `.txt`, `.rtf`), is under a 10 MB cap, and does
  not resolve under a sensitive directory (`~/.ssh`, `~/.aws`, `~/.gnupg`,
  `/etc`, `~/Library/Keychains`). This is defense-in-depth against a
  malicious or mistaken caller pointing the applier at credential material
  that would otherwise be uploaded, byte for byte, to a real third-party
  employer's form as though it were a resume.

- **Normalized job ids.** Every `job_id` reaching `JobApplier` (via
  `server.py`'s `apply_to_job` tool) is passed through
  `identifiers.normalize_job_id()` first, which accepts a full `viewjob`
  URL, a bare `jk=...` query fragment, or an already-bare id, and raises
  `ValueError` on anything that doesn't shape-check as a plausible
  alphanumeric Indeed job id. This closes off a job id containing
  unexpected characters flowing straight into `job_view_url()`'s URL
  construction.

- **Real confirmation before reporting success.** After the submit click,
  `_wait_for_submission_success()` polls for up to 15 seconds for either
  the page URL to change or a known success-text marker to appear in any
  frame's content (Indeed Apply commonly completes inside its own modal
  iframe rather than navigating the outer page, so every frame is checked,
  not just the main one). If neither signal appears in time, the call
  raises `ExtractionError` instead of returning `submitted=True` on a
  click that may not have actually gone through.

- **Audit-trail screenshot.** Immediately before the submit click,
  `_capture_pre_submit_screenshot()` saves a PNG to
  `~/.indeed-mcp-server/screenshots/<job_id>_<utc-timestamp>.png`, giving a
  local, timestamped record of exactly what was about to be submitted.
