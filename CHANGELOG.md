# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [0.2.0] - 2026-09-27

### Added

- Seek (seek.com.au) support as a second job site, alongside Indeed:
  - `seek_search_jobs` and `seek_get_job_details`, mirroring the Indeed module
    split (`seek_identifiers`/`seek_search_urls` for browser-free URL and ID
    handling, `seek_job_pages`/`seek_jobs` for page orchestration, a
    `SeekExtractor` facade).
  - `seek_apply_to_job`, reusing the site-agnostic apply safety logic
    (selector-locator helpers, attachment path validation, CAPTCHA
    detection, checkbox/radio answer matching, success-text detection,
    domain-suffix checking) extracted from Indeed's apply flow into a new
    shared `apply_common` module.
  - `seek_close_session`, backed by a second `SessionManager` with its own
    browser profile directory so Seek's cookies never mix with Indeed's.
  - The server now exposes 8 MCP tools in total (4 Indeed, 4 Seek).
- GitHub Actions CI workflow running the test suite (pytest, Python 3.11,
  Playwright Chromium) on every push and pull request.

### Changed

- Renamed the package and repository from the Indeed-only naming
  (`indeed_mcp_server`, `mcp-server-indeed`) to `jobsearch_mcp_server` /
  `mcp-server-jobsearch`, reflecting support for both Indeed and Seek.
  Updated `pyproject.toml` (package name, script entry point, project
  URLs), `.mcp.json`, `README.md`, and all internal imports across the
  package and test suite. The on-disk browser-profile and screenshot
  directory (`~/.indeed-mcp-server/`) was left unchanged, since renaming
  it would relocate existing saved login sessions and screenshots rather
  than just rename code.
- Hoisted duplicated apply-flow helpers (`_resolve_page`,
  `_ACCOUNT_LOGIN_SELECTORS`, `_wait_for_apply_form_root`,
  `_click_reveals_external_domain`) out of Indeed's and Seek's apply
  modules and into the shared `apply_common` module. Generalized the
  helpers that had a site-specific value baked in (iframe selector,
  domain suffix) to take it as a parameter. Widened the account-login-wall
  detection regex to also match Seek's own wording ("sign in to your seek
  account") alongside Indeed's.
- Removed dead code (`JOB_SEARCH_PATH`, `pages_needed_for`) confirmed
  unused anywhere in the package, and removed the corresponding stale
  reference from `architecture.md`.

### Fixed

Three rounds of security and correctness fixes were made against the
Indeed and Seek apply and search flows, each round found by a different
independent review pass (pre-publish source audit, then execution-based
fuzzing, then an adversarial reviewer loop):

- **Round 1 (pre-publish audit):**
  - Validated `resume_path`/`cover_letter_path` (file extension, size cap,
    sensitive-directory denylist) before upload.
  - Normalized job IDs at both MCP tool boundaries before they reach a URL
    or filesystem path, and added a domain allowlist to
    `job_view_url`/`build_job_search_url` as defense in depth.
  - Fixed checkbox/radio screening answers being unconditionally checked
    instead of matching the caller-supplied answer.
  - Fill or upload failures on name, email, phone, or resume fields now
    block with `unsupported_apply_flow` instead of silently continuing
    toward a false `submitted=True`.
  - Closed the browser tab left open after an external-ATS handoff, fixed
    a missing dot-boundary in the `indeed.com` domain suffix check,
    disambiguated duplicate screening-question labels, stopped a leaked
    Playwright driver process on `close()`, and raised `ExtractionError`
    instead of returning an empty list when zero search-result containers
    were found (previously indistinguishable from a bot-check or login
    wall).
  - Follow-up fix: `normalize_job_id` shape-checked the bare-token branch
    (`[A-Za-z0-9]+`) but not the URL and query-fragment branches, which
    returned the raw `jk` parameter value unchecked. A crafted `jk`
    containing path separators (e.g. `jk=abc/../../etc/passwd`) could
    reach a filesystem path built for the apply-flow screenshot filename.
    All branches now validate consistently.
  - Follow-up fix: a newly opened tab was only closed when it was
    external, so a tab that landed on an internal `indeed.com` URL leaked
    a Playwright page instead of being closed, the exact leak the
    function's own docstring said it prevented.

- **Round 2 (adversarial execution-based fuzzing pass):** found by running
  crafted inputs against the real functions rather than reading source.
  - `job_pages.py`: the `data-jk` card attribute bypassed
    `normalize_job_id` validation entirely (only the `href` fallback was
    checked), letting an untrusted DOM attribute reach `job_view_url`'s
    unescaped f-string interpolation, a query-parameter injection path
    into the URL returned to the MCP client. Now validated the same way
    as the `href` path, falling through instead of returning the raw
    value on a mismatch.
  - `search_urls.py`: `build_job_search_url` raised an undocumented
    `UnicodeEncodeError` instead of the `ValueError` promised by its
    docstring, on a lone UTF-16 surrogate character in keywords or
    location (reachable via a JSON escape over the MCP transport). Now
    caught and re-raised as `ValueError`.
  - `text.py`: `strip_indeed_noise`'s substring-based noise matching
    deleted entire legitimate sentences that happened to mention a noise
    phrase mid-clause (for example, a job description discussing cookies
    in a recipe). It now requires the non-matched remainder of the line to
    be short, distinguishing an actual banner line from a sentence that
    merely contains the phrase.
  - `screening_answers_json` is now type-checked as a dict before use,
    instead of letting a non-dict value crash inside the screening-question
    fill loop with a confusing `AttributeError`.

- **Round 3 (adversarial-review-team loop, independent auditor pass):**
  - Fixed a bug where Seek's required contact fields (name, email, phone)
    were incorrectly treated as unanswered screening questions.
    `apply_common._answer_screening_questions` excluded a field from the
    screening-question pass by matching its `name` HTML attribute against
    a set of standard field names. Indeed's fields use real `name`
    attributes, so this worked for Indeed, but Seek identifies the same
    fields with `data-automation` attributes, which can never match on
    `name`. Every real Seek application with a required name, email, or
    phone field failed closed as `unsupported_apply_flow`, even though
    those fields had already been filled correctly earlier in the flow.
    Generalized the exclusion check to a `standard_field_attribute`
    parameter (`name` for Indeed, unchanged; `data-automation` for Seek).

## [0.1.0] - 2026-09-25

Initial release: Indeed-only job search and application automation via the
user's own browser session.

### Added

- Browser-free utility layer for building and parsing Indeed search URLs
  and job IDs.
- Session, navigation, browser-launch, and authentication layer: a
  persistent-profile Chromium session with no-login-required detection and
  single-process session reuse.
- Job search and scraping layer (`job_policy.py`, `job_pages.py`,
  `jobs.py`, `extractor.py`) backing three MCP tools: `search_jobs`,
  `get_job_details`, and `close_session`.
- `apply_to_job` MCP tool (`JobApplier`): mechanical apply-flow automation
  that detects CAPTCHA, account-login, and external-ATS walls and stops
  rather than bypassing them, fills only caller-supplied `ApplicantProfile`
  fields, never guesses screening answers, and takes a pre-submit audit
  screenshot.
- `ApplicantProfile`/`ApplyResult` contracts shared across the tool
  surface.
- Test suite covering the session, job-policy, job-scraping, and server
  registration surfaces, plus shared fixtures (`html_page_runner`,
  `make_applicant_profile`).
- README and `architecture.md` documentation.

### Security

- Pre-publish audit fixes folded into this release before it was ever
  tagged: attachment path validation (extension, size cap, sensitive-
  directory denylist), job ID normalization at MCP tool boundaries, a
  domain allowlist on job/search URL builders, corrected screening-answer
  matching, fail-closed behavior on field-fill failures instead of a false
  `submitted=True`, orphaned browser tab cleanup, a corrected `indeed.com`
  domain suffix check, disambiguated duplicate screening-question labels,
  a fix for a leaked Playwright driver process on `close()`, and raising
  `ExtractionError` instead of returning an empty list when zero
  search-result containers were found. See the 0.2.0 entry above for the
  two follow-up fixes to this same round.
